"""将退款调查步骤接入 LangGraph。"""

from collections.abc import Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from openai import OpenAIError

from interfaces.refund.decision import Decide
from model.refund.flow import OrderFlowNode, OrderState, OrderTurn
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
) -> RefundGraph:
    # region 创建退款调查节点和流程图
    nodes = create_investigation_nodes(decide, investigate, emit, search)
    graph = StateGraph(OrderState, input_schema=OrderTurn)
    # endregion

    # region 定义已有回复时结束的条件路由
    # 连接条件边，已有回复时结束，否则进入指定节点。
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
    connect_or_finish(OrderFlowNode.INTENT, OrderFlowNode.PLAN)
    connect_or_finish(OrderFlowNode.PLAN, OrderFlowNode.EXECUTE)
    graph.add_edge(OrderFlowNode.EXECUTE.value, OrderFlowNode.OBSERVE.value)
    connect_or_finish(OrderFlowNode.OBSERVE, OrderFlowNode.REPLAN)
    connect_or_finish(OrderFlowNode.REPLAN, OrderFlowNode.EXECUTE)
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
    result = graph.invoke(turn.model_dump(mode="json"), thread_config(session_id))
    return OrderState.model_validate(result)
    # endregion
