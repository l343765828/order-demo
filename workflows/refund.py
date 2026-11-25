"""将退款调查步骤接入 LangGraph。"""

from collections.abc import Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from openai import OpenAIError

from implementations.refund.execution import RefundExecution
from interfaces.refund.decision import Decide
from interfaces.refund.preference import ManagePreferences
from model.refund.flow import OrderFlowNode, OrderState, OrderTurn
from model.refund.status import ConversationIntent, OrderGoal
from utils.checkpoint import thread_config
from workflows.refund_nodes import InvestigateOrder, SearchPolicy, create_investigation_nodes, investigation_failure

RefundGraph = CompiledStateGraph[OrderState, None, OrderTurn, OrderState]


# 按固定节点名称和条件边装配退款调查图，可由调用方传入 checkpoint 存储。
def build_graph(
    decide: Decide,
    investigate: InvestigateOrder,
    emit: Callable[..., None],
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    search: SearchPolicy,
    refund: RefundExecution | None = None,
    is_live: Callable[[str], bool] | None = None,
    preferences: ManagePreferences | None = None,
) -> RefundGraph:

    # region 创建退款调查节点和流程图
    nodes = create_investigation_nodes(decide, investigate, emit, search, refund, is_live, preferences)
    graph = StateGraph(OrderState, input_schema=OrderTurn)
    # endregion

    # region 定义路由，已有回复时结束，否则创建条件边，进入指定节点
    def connect_or_finish(source: OrderFlowNode, target: OrderFlowNode) -> None:

        # region 将来源节点连到下一节点或流程终点
        # 从本轮结果判断图是否应继续。
        def route(state: OrderState) -> str:

            # region 已有回复时结束流程，否则进入下一个节点
            return END if state.reply is not None else target.value
            # endregion

        graph.add_conditional_edges(source.value, route, [target.value, END])
        # endregion

    # endregion

    # region 注册调查节点并统一处理执行和失败
    for name, node in nodes.items():
        # 记录节点执行并统一处理可预期的模型或数据校验错误。
        def run_node(state: OrderState, node=node, name=name) -> dict:

            # region 节点执行失败时记录错误并按当前调查路径生成降级回复
            emit("graph_node", node=name.value)
            try:
                update = node(state)
            except (ValueError, OpenAIError) as exc:
                emit("node_failed", node=name.value, error_type=type(exc).__name__)
                update = investigation_failure(name, state)
            # endregion

            # region 序列化节点更新，意图节点重置整轮状态
            # 意图节点重置整轮状态，其余节点只提交显式更新的字段，并保留嵌套模型的序列化格式。
            fields = None if name is OrderFlowNode.INTENT else update.model_fields_set
            return update.model_dump(mode="json", include=fields)
            # endregion

        graph.add_node(name.value, run_node)
    # endregion

    # region 连接意图识别、调查执行和重规划节点
    graph.add_edge(START, OrderFlowNode.INTENT.value)
    # 按显式命令和订单答复需要，进入偏好节点或直接继续调查。
    def after_intent(state: OrderState) -> str:

        # region 显式偏好命令独立于业务答复执行
        decision = state.decision
        if decision is None:
            return END
        if decision.memory_request is not None:
            return OrderFlowNode.PREFERENCES.value
        # endregion

        # region 无显式命令时，仅在准备订单状态答复时获取详略
        if state.reply is not None:
            return END
        if (
            # 本轮查询订单状态。
            decision.intent is ConversationIntent.ORDER_QUESTION
            # 退款资格咨询不使用订单状态排版。
            and decision.order_goal is not OrderGoal.ELIGIBILITY
            # 当前订单答复需要准备表达方式。
            and (
                # 已配置长期偏好持久化。
                preferences is not None
                # 或已识别当前消费者的临时要求。
                or decision.temporary_detail is not None
            )
        ):
            return OrderFlowNode.PREFERENCES.value
        return OrderFlowNode.PLAN.value
        # endregion

    # 偏好命令可单独完成，也可与任何业务答复在同轮返回。
    def after_preferences(state: OrderState) -> str:

        # region 纯偏好命令和已有答复的路径到达终点，其他路径继续调查
        assert state.decision is not None
        if (
            # 意图已产生直接业务答复。
            state.reply is not None
            # 本轮只管理长期偏好。
            or state.decision.intent is ConversationIntent.MEMORY_REQUEST
        ):
            return END
        return OrderFlowNode.PLAN.value
        # endregion

    graph.add_conditional_edges(OrderFlowNode.INTENT.value, after_intent, [
        END, OrderFlowNode.PREFERENCES.value, OrderFlowNode.PLAN.value,
    ])
    graph.add_conditional_edges(OrderFlowNode.PREFERENCES.value, after_preferences, [END, OrderFlowNode.PLAN.value])
    connect_or_finish(OrderFlowNode.PLAN, OrderFlowNode.EXECUTE)
    graph.add_edge(OrderFlowNode.EXECUTE.value, OrderFlowNode.OBSERVE.value)
    connect_or_finish(OrderFlowNode.OBSERVE, OrderFlowNode.REPLAN)
    # 按本轮是否已有答复、正式依据或真实退款结果决定调查后去向。
    def after_investigation(state: OrderState) -> str:

        # region 优先结束已答复或已有退款结果的本轮
        if state.reply is not None:
            return END
        if state.refund_result is not None:

            # region 已确认资格拒绝时仅继续政策解释，其他结果直接回复
            return (OrderFlowNode.EXECUTE.value if state.refund_result.is_order_state_denial
                    else OrderFlowNode.REFUND_REPLY.value)
            # endregion
        # endregion

        # region 已取得本轮正式依据的办理进入固定复核，其余继续调查
        if (
            # 当前是明确办理路径。
            state.refund_authorization is not None
            # 当前步骤已经核验过实际工具结果。
            and state.observation is not None
            # 已结束正式政策检索并取得可用依据。
            and state.observation.policy_evidence is not None
            # 政策依据足以继续业务复核。
            and state.observation.policy_evidence.citations
        ):
            return OrderFlowNode.REVIEW_REFUND.value
        return OrderFlowNode.EXECUTE.value
        # endregion

    # 固定复核后按最新事实继续解释拒绝、受控发送或回复业务结果。
    def after_review(state: OrderState) -> str:

        # region 只允许已确认政策事件进入固定发送
        if state.reply is not None:
            return END
        if (
            # 复核取得明确的退款业务结果。
            state.refund_result is not None
            # 复核时支付或发货状态不再满足资格。
            and state.refund_result.is_order_state_denial
        ):

            # region 最新订单资格被拒后仅执行只读政策检索
            return OrderFlowNode.EXECUTE.value
            # endregion

        return OrderFlowNode.SEND_REFUND.value if state.refund_verification else OrderFlowNode.REFUND_REPLY.value
        # endregion

    graph.add_conditional_edges(OrderFlowNode.REPLAN.value, after_investigation, [
        END, OrderFlowNode.EXECUTE.value, OrderFlowNode.REVIEW_REFUND.value, OrderFlowNode.REFUND_REPLY.value,
    ])
    graph.add_conditional_edges(OrderFlowNode.REVIEW_REFUND.value, after_review, [
        END, OrderFlowNode.EXECUTE.value, OrderFlowNode.SEND_REFUND.value, OrderFlowNode.REFUND_REPLY.value,
    ])
    graph.add_edge(OrderFlowNode.SEND_REFUND.value, OrderFlowNode.REFUND_REPLY.value)
    graph.add_edge(OrderFlowNode.REFUND_REPLY.value, END)
    # endregion

    # region 使用指定的检查点存储编译流程图
    return graph.compile(checkpointer=checkpointer)
    # endregion


# 读取指定会话的图快照，返回已校验的退款状态及待执行的节点名称。
def load_refund_state(graph: RefundGraph, session_id: str) -> tuple[OrderState | None, tuple[str, ...]]:

    # region 读取指定会话的图快照，返回已校验的退款状态及待执行的节点名称
    snapshot = graph.get_state(thread_config(session_id))
    state = OrderState.model_validate(snapshot.values) if snapshot.values else None
    return state, snapshot.next
    # endregion


# 在指定会话的图线程执行本轮输入，返回已校验的完整状态。
def run_refund_turn(graph: RefundGraph, session_id: str, turn: OrderTurn) -> OrderState:

    # region 在指定会话的图线程执行本轮输入，返回已校验的完整状态
    result = graph.invoke(turn, thread_config(session_id))
    return OrderState.model_validate(result)
    # endregion
