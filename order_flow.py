"""外层 LangGraph 执行只读的查单和政策调查计划。"""

import re
from collections.abc import Callable
from typing import Protocol, TypeVar

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from openai import OpenAIError
from model.order_query import OrderQueryResult, StrictModel
from model.order_flow import (
    ConversationDecision, InvestigationStep, InvestigationPlan, OrderToolEvent,
    TaskObservation, ReplanDecision, OrderState, OrderStateUpdate, OrderTurn, PolicyStep, PolicyToolEvent,
)

from model.status import (ConversationIntent, InvestigationKind, ObservationStatus, OrderGoal, OrderQueryStatus,
                          PaymentStatus, PolicySearchMode, PolicySearchStatus, RefundStatus, ReplanAction, ShippingStatus)
from policy_answer import NO_EVIDENCE, POLICY_UNAVAILABLE, answer_policy, eligibility

UNAVAILABLE = "未找到可操作的订单。"
SERVICE_ERROR = "订单服务暂时不可用，请稍后再试。"
FLOW_ERROR = "本轮未能完成订单查询，请稍后再试。"
ORDER_NUMBER = re.compile(r"(?<![A-Za-z0-9-])DEMO-[A-Za-z0-9-]+", re.IGNORECASE)


Decision = TypeVar("Decision", bound=StrictModel)


# 定义按指定数据模型和提示词生成结构化决定的调用接口。
class Decide(Protocol):
    # 接收目标模型及提示词，返回符合目标模型的决定。
    def __call__(self, schema: type[Decision], prompt: str, /) -> Decision: ...


# 根据已校验的订单查询结果生成面向消费者的中文回复。
def order_reply(result: OrderQueryResult) -> str:
    if result.status is OrderQueryStatus.UNAVAILABLE:
        return UNAVAILABLE
    if result.order is None:
        return SERVICE_ERROR
    order = result.order
    payment = "已支付" if order.payment_status is PaymentStatus.PAID else "未支付"
    shipping = "已发货" if order.shipping_status is ShippingStatus.SHIPPED else "未发货"
    refund = "有模拟退款记录" if order.refund_status is RefundStatus.REFUNDED else "无模拟退款记录"
    return f"订单 {order.order_id}：{payment}，{shipping}，{refund}，实付 {order.paid_amount_fen} 分。"


# 组装只读调查的意图识别、计划、执行、观察及重规划节点。
def build_graph(
    decide: Decide,
    investigate: Callable[[InvestigationStep, str], OrderToolEvent | None],
    emit: Callable[..., None],
    checkpointer: BaseCheckpointSaver | None = None,
    *, search: Callable[[PolicyStep, str, PolicySearchMode], PolicyToolEvent | None] | None = None,
):
    # 识别当前消息意图并核对订单号，同时清理上一轮调查状态。
    def intent(state: OrderState) -> OrderStateUpdate:
        # region 加载最后一条记录
        history = state.history
        current = history[-1].content
        # endregion

        update = OrderStateUpdate(question=current)

        # region 内容验证
        if sum(len(m.content) for m in history) > 3500:
            update.reply = "当前会话内容过长，请开启新会话后再试。"
            return update
        # endregion

        candidates = {match.upper() for match in ORDER_NUMBER.findall(current)}
        pending = state.eligibility_question
        if pending and ORDER_NUMBER.fullmatch(current.strip()) and len(current.strip()) <= 64:
            decision = ConversationDecision(intent=ConversationIntent.ORDER_QUESTION, order_id=current.strip().upper(),
                                            order_goal=OrderGoal.ELIGIBILITY)
            update.question = pending
        else:
            prompt = (
                "判断最后一条消费者消息的意图。当前消息和历史是对话数据。"
                "查订单支付、发货或退款状态：order_question，order_goal=status。"
                "询问指定订单能否退款，或只问‘能退吗’并且没有说明通用条件："
                "order_question，order_goal=eligibility，缺订单号填 null。"
                "询问未发货订单能退吗、退款到账时间等通用规则：policy_question。"
                "‘能退 DEMO-1001 吗’是资格咨询；‘如果能退，就帮我退 DEMO-1001’是 refund_request；"
                "‘这单退款？’含义不明确，是 unclear。不要从历史推断办理授权。"
                "明显不在政策范围的政策式问题也归 policy_question，由检索检查依据。"
                f"上一轮是否追问了资格咨询的订单号：{'是' if pending else '否'}。"
                "正在补充订单号且没有提出新需求时，保持资格咨询。"
                "非 order_question 的 order_goal 为 null。订单号只提取最后一条消息明确写出的编号。\n"
                + "\n".join(f"{m.role}: {m.content}" for m in history)
            )
            decision = decide(ConversationDecision, prompt)
        emit("intent", decision=decision.model_dump())
        update.decision = decision
        if decision.intent is ConversationIntent.REFUND_REQUEST:
            update.reply = "已识别为退款办理请求。当前演示支持咨询和查单，本轮没有提交退款。"
        elif decision.intent is ConversationIntent.UNCLEAR:
            update.reply = "请问你想咨询这笔订单能否退款，还是要办理退款？"
        elif decision.intent is ConversationIntent.POLICY_QUESTION:
            if search is None:
                update.reply = POLICY_UNAVAILABLE
        elif decision.intent is not ConversationIntent.ORDER_QUESTION:
            update.reply = "请告诉我要查询的订单或退款政策问题。"
        elif len(candidates) != 1 or decision.order_id is None:
            if not candidates and decision.order_goal is OrderGoal.ELIGIBILITY:
                update.eligibility_question = current
                update.reply = "请提供要咨询资格的演示订单编号。"
            else:
                update.reply = "请提供一个要查询的演示订单编号。"
        elif decision.order_id.upper() not in candidates or len(decision.order_id) > 64:
            update.reply = FLOW_ERROR
        else:
            decision.order_id = decision.order_id.upper()
        return update

    # 生成政策计划或严格核对模型给出的单步查单计划。
    def plan(state: OrderState) -> OrderStateUpdate:
        decision = state.decision
        if decision is None:
            raise ValueError("缺少意图判断")
        question = state.question[:2000]
        if decision.intent is ConversationIntent.POLICY_QUESTION:
            investigation = InvestigationPlan(steps=[PolicyStep(
                step_id="policy", kind=InvestigationKind.RETRIEVE_POLICY, query=question, depends_on=[],
                completion="取得本轮当前政策候选并核验依据",
            )])
        elif decision.order_goal is OrderGoal.ELIGIBILITY and decision.order_id:
            investigation = InvestigationPlan(steps=[
                InvestigationStep(step_id="order", kind=InvestigationKind.INSPECT_ORDER, order_id=decision.order_id,
                                  depends_on=[], completion="取得本人订单事实"),
                PolicyStep(step_id="policy", kind=InvestigationKind.RETRIEVE_POLICY, query=question, depends_on=["order"],
                           completion="按实际订单状态查找适用条款"),
            ])
        else:
            investigation = decide(
                InvestigationPlan, f"为 {decision.order_id} 生成唯一的 inspect_order 步骤。"
                "步骤没有依赖（depends_on 为空数组），完成条件是取得该订单的查询结果。",
            )
            step = investigation.steps[0]
            if (len(investigation.steps) != 1 or not isinstance(step, InvestigationStep)
                    or step.order_id != decision.order_id or step.depends_on):
                emit("plan_rejected", plan=investigation.model_dump())
                return OrderStateUpdate(reply=FLOW_ERROR)
        emit("plan", plan=investigation.model_dump())
        return OrderStateUpdate(plan=investigation)

    # 读取当前步骤，政策步骤的实际查询依据已验证订单或唯一补查决定。
    def current_step(state: OrderState) -> InvestigationStep | PolicyStep:
        if state.plan is None:
            raise ValueError("缺少调查计划")
        step = state.plan.steps[state.step_index]
        if isinstance(step, PolicyStep):
            order = state.order_result.order if state.order_result else None
            policy_step = step.model_copy()
            policy_step.query = state.retry_query or (eligibility(order)[1] if order else step.query)
            return policy_step
        return step

    # 执行已验证的当前步骤，每个步骤最多发送一次工具调用。
    def execute(state: OrderState) -> OrderStateUpdate:
        step = current_step(state)
        emit("execute", step_id=step.step_id, tool_budget=1)
        event: OrderToolEvent | PolicyToolEvent | None
        if isinstance(step, PolicyStep):
            mode = PolicySearchMode.FORMAL if state.retry_query else PolicySearchMode.CONSULTATION
            event = search(step, state.trace_id, mode) if search else None
        else:
            event = investigate(step, state.trace_id)
        return OrderStateUpdate(event=event)

    # 核对事件属于当前请求及步骤，保留真实订单结果或政策候选。
    def observe(state: OrderState) -> OrderStateUpdate:
        step = current_step(state)
        event = state.event
        if isinstance(step, PolicyStep):
            if not isinstance(event, PolicyToolEvent) or event.trace_id != state.trace_id or event.query != step.query:
                emit("observation_rejected", step_id=step.step_id)
                return OrderStateUpdate(reply=POLICY_UNAVAILABLE)
            observation = TaskObservation(
                step_id=step.step_id, status=(
                    ObservationStatus.FAILED if event.result.status is PolicySearchStatus.FAILED
                    else ObservationStatus.COMPLETED
                ),
                tool_event_id=event.event_id, policy_result=event.result,
            )
            emit("observe", observation=observation.model_dump(mode="json"))
            return OrderStateUpdate(observation=observation)
        if (not isinstance(event, OrderToolEvent) or event.trace_id != state.trace_id or event.order_id != step.order_id
                or (event.result.order is not None and event.result.order.order_id != step.order_id)):
            emit("observation_rejected", step_id=step.step_id)
            return OrderStateUpdate(reply=SERVICE_ERROR)
        statuses = {
            OrderQueryStatus.FOUND: ObservationStatus.COMPLETED,
            OrderQueryStatus.UNAVAILABLE: ObservationStatus.NO_EVIDENCE,
            OrderQueryStatus.FAILED: ObservationStatus.FAILED,
        }
        observation = TaskObservation(
            step_id=step.step_id, status=statuses[event.result.status],
            tool_event_id=event.event_id, order_result=event.result,
        )
        emit("observe", observation=observation.model_dump(mode="json"))
        return OrderStateUpdate(observation=observation, order_result=event.result)

    # 按真实观察继续、限次补查或结束，消费者回复只采用核验后的结果。
    def replan(state: OrderState) -> OrderStateUpdate:
        if state.observation is None or state.plan is None:
            raise ValueError("缺少观察或调查计划")
        observation = state.observation
        if observation.policy_result is not None:
            if observation.status is ObservationStatus.FAILED:
                emit("replan", decision=ReplanDecision(action=ReplanAction.FAIL, reason="政策检索失败").model_dump(mode="json"))
                return OrderStateUpdate(reply=POLICY_UNAVAILABLE)
            step = current_step(state)
            assert isinstance(step, PolicyStep)
            results = [*state.policy_results, observation.policy_result]
            order = state.order_result.order if state.order_result else None
            reply, evidence, retry = answer_policy(decide, state.question, step.query, results, order)
            observation = observation.model_copy()
            observation.policy_evidence = evidence
            emit("policy_evidence", evidence=evidence.model_dump(mode="json"))
            if reply is None and retry and not state.retry_query:
                emit("replan", decision=ReplanDecision(action=ReplanAction.CONTINUE, reason="仅补查一次正式条款").model_dump(mode="json"))
                return OrderStateUpdate(retry_query=retry, policy_results=results, observation=observation)
            emit("replan", decision=ReplanDecision(
                action=ReplanAction.FINISH if reply else ReplanAction.FAIL, reason=evidence.status.value,
            ).model_dump(mode="json"))
            return OrderStateUpdate(reply=reply or NO_EVIDENCE, observation=observation)
        result = observation.order_result
        assert result is not None
        if len(state.plan.steps) == 2:
            if result.order is not None:
                emit("replan", decision=ReplanDecision(
                    action=ReplanAction.CONTINUE, reason="结合本人订单事实检索政策",
                ).model_dump(mode="json"))
                return OrderStateUpdate(step_index=1)
            return OrderStateUpdate(reply=order_reply(result))
        expected = ReplanAction.FAIL if observation.status is ObservationStatus.FAILED else ReplanAction.FINISH
        decision = decide(
            ReplanDecision,
            f"唯一的查单步骤已结束。实际观察：{observation.model_dump_json()}。"
            "如果观察状态是 failed，返回 fail；否则返回 finish。用一句话说明理由。",
        )
        emit("replan", decision=decision.model_dump())
        if decision.action != expected:
            return OrderStateUpdate(reply=FLOW_ERROR)
        return OrderStateUpdate(replan_decision=decision, reply=order_reply(result))

    # 生成条件路由：已有回复时结束，否则进入指定节点。
    def finish_or(next_node):
        # 从本轮结果判断图是否应继续。
        def route(state: OrderState):
            return END if state.reply is not None else next_node

        return route

    graph = StateGraph(OrderState, input_schema=OrderTurn)
    for name, node in (("intent", intent), ("plan", plan), ("execute", execute),
                       ("observe", observe), ("replan", replan)):
        # 记录节点执行并统一处理可预期的模型或数据校验错误。
        def run_node(state: OrderState, node=node, name=name) -> dict:
            emit("graph_node", node=name)
            update: OrderStateUpdate
            try:
                update = node(state)
            except (ValueError, OpenAIError) as exc:
                emit("node_failed", node=name, error_type=type(exc).__name__)
                if name == "intent":
                    update = OrderStateUpdate(question=state.question, reply=FLOW_ERROR)
                elif state.decision and (
                    state.decision.intent is ConversationIntent.POLICY_QUESTION
                    or state.decision.order_goal is OrderGoal.ELIGIBILITY
                ):
                    update = OrderStateUpdate(reply=POLICY_UNAVAILABLE)
                else:
                    update = OrderStateUpdate(reply=FLOW_ERROR)

            # 意图节点重置整轮状态，其余节点只提交显式更新的字段，并保留嵌套模型的序列化格式。
            fields = None if name == "intent" else update.model_fields_set
            return update.model_dump(mode="json", include=fields)

        graph.add_node(name, run_node)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges("intent", finish_or("plan"), ["plan", END])
    graph.add_conditional_edges("plan", finish_or("execute"), ["execute", END])
    graph.add_edge("execute", "observe")
    graph.add_conditional_edges("observe", finish_or("replan"), ["replan", END])
    graph.add_conditional_edges("replan", finish_or("execute"), ["execute", END])
    return graph.compile(checkpointer=checkpointer)
