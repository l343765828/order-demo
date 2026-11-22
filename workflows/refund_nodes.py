"""协调退款调查节点对订单、退款意图和政策能力的调用。"""

from collections.abc import Callable

from config.limits import RefundLimits
from implementations.orders.reply import ORDER_QUERY_INCOMPLETE, SERVICE_ERROR, order_reply
from implementations.policy.answer import NO_EVIDENCE, POLICY_UNAVAILABLE, answer_policy, eligibility
from implementations.refund.intent import resolve_intent
from interfaces.refund.decision import Decide
from model.orders.status import OrderQueryStatus
from model.policy.status import PolicySearchMode, PolicySearchStatus
from model.refund.flow import (
    InvestigationPlan,
    InvestigationStep,
    OrderFlowNode,
    OrderState,
    OrderStateUpdate,
    OrderToolEvent,
    PolicyStep,
    PolicyToolEvent,
    ReplanDecision,
    TaskObservation,
)
from model.refund.status import (
    ConversationIntent, HostStepId, InvestigationKind, ObservationStatus, OrderGoal, ReplanAction,
)

InvestigateOrder = Callable[[InvestigationStep, str], OrderToolEvent | None]
SearchPolicy = Callable[[PolicyStep, str, PolicySearchMode], PolicyToolEvent | None]
InvestigationNodeHandler = Callable[[OrderState], OrderStateUpdate]


# 构建退款调查节点，由节点调用业务能力并提交图状态更新。
def create_investigation_nodes(
    decide: Decide,
    investigate: InvestigateOrder,
    emit: Callable[..., None],
    search: SearchPolicy,
) -> dict[OrderFlowNode, InvestigationNodeHandler]:
    # 调用退款意图判断并将结果写入图状态，入口节点负责重置上一轮字段。
    def intent(state: OrderState) -> OrderStateUpdate:
        result = resolve_intent(state.history, state.eligibility_question, decide, emit)
        return OrderStateUpdate(
            question=result.question,
            decision=result.decision,
            eligibility_question=result.eligibility_question,
            reply=result.reply,
        )

    # 生成政策计划或严格核对模型给出的单步查单计划。
    def plan(state: OrderState) -> OrderStateUpdate:
        # region 缺少意图判断时停止规划，政策查询文本按检索契约截断
        decision = state.decision
        if decision is None:
            raise ValueError("缺少意图判断")
        question = state.question[:RefundLimits.MAX_POLICY_QUERY_CHARS]
        # endregion

        # region 意图为通用退款政策咨询时，只安排政策检索
        if decision.intent is ConversationIntent.POLICY_QUESTION:
            investigation = InvestigationPlan(steps=[PolicyStep(
                step_id=HostStepId.POLICY.value, kind=InvestigationKind.RETRIEVE_POLICY, query=question, depends_on=[],
                completion="取得本轮当前政策候选并核验依据",
            )])
        # endregion

        # region 咨询指定订单的退款资格且已有订单号时，先查订单事实再检索适用政策
        elif decision.order_goal is OrderGoal.ELIGIBILITY and decision.order_id:
            investigation = InvestigationPlan(steps=[
                InvestigationStep(step_id=HostStepId.ORDER.value, kind=InvestigationKind.INSPECT_ORDER,
                                  order_id=decision.order_id, depends_on=[], completion="取得本人订单事实"),
                PolicyStep(step_id=HostStepId.POLICY.value, kind=InvestigationKind.RETRIEVE_POLICY, query=question,
                           depends_on=[HostStepId.ORDER.value], completion="按实际订单状态查找适用条款"),
            ])
        # endregion

        # region 其余已放行的订单查询只接受目标订单一致且无依赖的单步计划
        else:
            investigation = decide(
                InvestigationPlan, f"为 {decision.order_id} 生成唯一的 inspect_order 步骤。"
                "步骤没有依赖（depends_on 为空数组），完成条件是取得该订单的查询结果。",
            )
            step = investigation.steps[0]
            if (len(investigation.steps) != 1 or not isinstance(step, InvestigationStep)
                    or step.order_id != decision.order_id or step.depends_on):
                emit("plan_rejected", plan=investigation.model_dump())
                return OrderStateUpdate(reply=ORDER_QUERY_INCOMPLETE)
        # endregion
        emit("plan", plan=investigation.model_dump())
        return OrderStateUpdate(plan=investigation)

    # 读取当前步骤，政策步骤的实际查询依据已验证订单或唯一补查决定。
    def current_step(state: OrderState) -> InvestigationStep | PolicyStep:
        # region 读取正在执行的计划步骤，政策查询优先使用补查文本，其次使用订单事实
        if state.plan is None:
            raise ValueError("缺少调查计划")
        step = state.plan.steps[state.step_index]
        if isinstance(step, PolicyStep):
            order = state.order_result.order if state.order_result else None
            policy_step = step.model_copy()
            policy_step.query = state.retry_query or (eligibility(order)[1] if order else step.query)
            return policy_step
        return step
        # endregion

    # 执行已验证的当前步骤，每个步骤最多发送一次工具调用。
    def execute(state: OrderState) -> OrderStateUpdate:
        # region 政策初查用咨询范围，补查仅检索正式条款，查单走订单工具
        step = current_step(state)
        emit("execute", step_id=step.step_id, tool_budget=1)
        event: OrderToolEvent | PolicyToolEvent | None
        if isinstance(step, PolicyStep):
            mode = PolicySearchMode.FORMAL if state.retry_query else PolicySearchMode.CONSULTATION
            event = search(step, state.trace_id, mode)
        else:
            event = investigate(step, state.trace_id)
        # endregion
        return OrderStateUpdate(event=event)

    # 核对事件属于当前请求及步骤，保留真实订单结果或政策候选。
    def observe(state: OrderState) -> OrderStateUpdate:
        step = current_step(state)
        event = state.event
        # region 政策步骤核对本轮查询和回执，区分检索失败与已收到候选结果
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
        # endregion

        # region 查单步骤核对订单和回执归属，区分查到、无可操作订单及服务失败
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
        # endregion

    # 按真实观察继续、限次补查或结束，消费者回复只采用核验后的结果。
    def replan(state: OrderState) -> OrderStateUpdate:
        # region 收到政策观察时核验证据，有补查建议且本轮尚未补查时只补查一次
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
        # endregion

        # region 资格咨询仅在取得本人订单事实时继续检索，否则按查单结果结束
        result = observation.order_result
        assert result is not None
        if len(state.plan.steps) == 2:
            if result.order is not None:
                emit("replan", decision=ReplanDecision(
                    action=ReplanAction.CONTINUE, reason="结合本人订单事实检索政策",
                ).model_dump(mode="json"))
                return OrderStateUpdate(step_index=1)
            return OrderStateUpdate(reply=order_reply(result))
        # endregion

        # region 普通查单要求模型决定与真实观察一致，再按订单事实回复
        expected = ReplanAction.FAIL if observation.status is ObservationStatus.FAILED else ReplanAction.FINISH
        decision = decide(
            ReplanDecision,
            f"唯一的查单步骤已结束。实际观察：{observation.model_dump_json()}。"
            "如果观察状态是 failed，返回 fail；否则返回 finish。用一句话说明理由。",
        )
        emit("replan", decision=decision.model_dump())
        if decision.action != expected:
            return OrderStateUpdate(reply=ORDER_QUERY_INCOMPLETE)
        return OrderStateUpdate(replan_decision=decision, reply=order_reply(result))
        # endregion

    return {
        OrderFlowNode.INTENT: intent,
        OrderFlowNode.PLAN: plan,
        OrderFlowNode.EXECUTE: execute,
        OrderFlowNode.OBSERVE: observe,
        OrderFlowNode.REPLAN: replan,
    }


# 按失败的调查步骤和消费者意图生成本轮降级回复。
def investigation_failure(node: OrderFlowNode, state: OrderState) -> OrderStateUpdate:
    # region 意图识别失败时保留问题，其他步骤按是否属于咨询选择提示
    if node is OrderFlowNode.INTENT:
        return OrderStateUpdate(question=state.question, reply=ORDER_QUERY_INCOMPLETE)
    if state.decision and (
        state.decision.intent is ConversationIntent.POLICY_QUESTION
        or state.decision.order_goal is OrderGoal.ELIGIBILITY
    ):
        return OrderStateUpdate(reply=POLICY_UNAVAILABLE)
    return OrderStateUpdate(reply=ORDER_QUERY_INCOMPLETE)
    # endregion
