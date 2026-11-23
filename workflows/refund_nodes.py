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
    # region 定义意图识别节点
    # 调用退款意图判断并将结果写入图状态，入口节点负责重置上一轮字段。
    def intent(state: OrderState) -> OrderStateUpdate:
        # region 识别消费者意图并生成本轮状态更新
        result = resolve_intent(state.history, state.eligibility_question, decide, emit)
        return OrderStateUpdate(
            question=result.question,
            decision=result.decision,
            eligibility_question=result.eligibility_question,
            reply=result.reply,
        )
        # endregion
    # endregion

    # region 定义调查计划节点
    # 生成政策计划或严格核对模型给出的单步查单计划。
    def plan(state: OrderState) -> OrderStateUpdate:
        # region 缺少意图判断时停止规划，政策查询文本按检索契约截断
        decision = state.decision
        if decision is None:
            raise ValueError("缺少意图判断")
        question = state.question[:RefundLimits.MAX_POLICY_QUERY_CHARS]
        # endregion

        # region 按咨询类型生成政策、资格或查单计划
        if decision.intent is ConversationIntent.POLICY_QUESTION:
            # region 通用退款政策咨询只安排政策检索
            investigation = InvestigationPlan(steps=[PolicyStep(
                step_id=HostStepId.POLICY.value, kind=InvestigationKind.RETRIEVE_POLICY, query=question, depends_on=[],
                completion="取得本轮当前政策候选并核验依据",
            )])
            # endregion

        elif (
            # 消费者在咨询指定订单的退款资格。
            decision.order_goal is OrderGoal.ELIGIBILITY
            # 已识别出要核对的订单号。
            and decision.order_id
        ):
            # region 指定订单的资格咨询先查订单，再检索适用政策
            investigation = InvestigationPlan(steps=[
                InvestigationStep(step_id=HostStepId.ORDER.value, kind=InvestigationKind.INSPECT_ORDER,
                                  order_id=decision.order_id, depends_on=[], completion="取得本人订单事实"),
                PolicyStep(step_id=HostStepId.POLICY.value, kind=InvestigationKind.RETRIEVE_POLICY, query=question,
                           depends_on=[HostStepId.ORDER.value], completion="按实际订单状态查找适用条款"),
            ])
            # endregion

        else:
            # region 其他订单查询只接受目标一致、无依赖的单步计划
            investigation = decide(
                InvestigationPlan, f"为 {decision.order_id} 生成唯一的 inspect_order 步骤。"
                "步骤没有依赖（depends_on 为空数组），完成条件是取得该订单的查询结果。",
            )
            step = investigation.steps[0]
            if (
                # 普通查单计划没有恰好一个步骤。
                len(investigation.steps) != 1
                # 计划安排的首步不是订单调查步骤。
                or not isinstance(step, InvestigationStep)
                # 计划中的订单号与当前意图确认的订单号不一致。
                or step.order_id != decision.order_id
                # 唯一的查单步骤声明了前置依赖。
                or step.depends_on
            ):
                emit("plan_rejected", plan=investigation.model_dump())
                return OrderStateUpdate(reply=ORDER_QUERY_INCOMPLETE)
            # endregion
        # endregion
        # region 记录已接受的计划并写入流程状态
        emit("plan", plan=investigation.model_dump())
        return OrderStateUpdate(plan=investigation)
        # endregion

    # endregion
    # region 定义步骤准备和工具执行
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
        # region 按当前步骤调用政策或查单工具并提交回执
        step = current_step(state)
        emit("execute", step_id=step.step_id, tool_budget=1)
        event: OrderToolEvent | PolicyToolEvent | None
        if isinstance(step, PolicyStep):
            mode = PolicySearchMode.FORMAL if state.retry_query else PolicySearchMode.CONSULTATION
            event = search(step, state.trace_id, mode)
        else:
            event = investigate(step, state.trace_id)
        return OrderStateUpdate(event=event)
        # endregion
    # endregion

    # region 定义工具回执核验节点
    # 核对事件属于当前请求及步骤，保留真实订单结果或政策候选。
    def observe(state: OrderState) -> OrderStateUpdate:
        # region 读取当前步骤和工具调用回执
        step = current_step(state)
        event = state.event
        # endregion

        # region 政策步骤核对本轮查询和回执，区分检索失败与已收到候选结果
        if isinstance(step, PolicyStep):
            if (
                # 当前政策检索步骤没有收到政策工具回执。
                not isinstance(event, PolicyToolEvent)
                # 回执不属于当前会话轮次。
                or event.trace_id != state.trace_id
                # 工具实际查询的文本与当前步骤指定的文本不一致。
                or event.query != step.query
            ):
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
        if (
            # 当前查单步骤没有收到查单工具回执。
            not isinstance(event, OrderToolEvent)
            # 查单回执不属于当前会话轮次。
            or event.trace_id != state.trace_id
            # 工具实际查询的订单号与当前步骤指定的订单号不一致。
            or event.order_id != step.order_id
            # 回执中的订单详情与当前步骤指定的订单不一致。
            or (
                # 查单回执带回了一笔订单的详情。
                event.result.order is not None
                # 详情里的订单号与当前步骤指定的订单号不同。
                and event.result.order.order_id != step.order_id
            )
        ):
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
    # endregion

    # region 定义调查重规划和结束节点
    # 按真实观察继续、限次补查或结束，消费者回复只采用核验后的结果。
    def replan(state: OrderState) -> OrderStateUpdate:
        # region 缺少观察或计划时停止重规划
        if (
            # 当前调查步骤缺少已核验的工具观察。
            state.observation is None
            # 当前会话轮次缺少退款调查计划。
            or state.plan is None
        ):
            raise ValueError("缺少观察或调查计划")
        observation = state.observation
        # endregion

        # region 根据政策观察决定补查、答复或降级
        if observation.policy_result is not None:

            # region 检索失败时记录失败决定并返回政策不可用提示
            if observation.status is ObservationStatus.FAILED:
                emit("replan", decision=ReplanDecision(action=ReplanAction.FAIL, reason="政策检索失败").model_dump(mode="json"))
                return OrderStateUpdate(reply=POLICY_UNAVAILABLE)
            # endregion

            # region 核验政策候选并记录本轮证据
            step = current_step(state)
            assert isinstance(step, PolicyStep)
            results = [*state.policy_results, observation.policy_result]
            order = state.order_result.order if state.order_result else None
            reply, evidence, retry = answer_policy(decide, state.question, step.query, results, order)
            observation = observation.model_copy()
            observation.policy_evidence = evidence
            emit("policy_evidence", evidence=evidence.model_dump(mode="json"))
            # endregion

            # region 尚无答复且本轮未补查时仅补查一次正式条款
            if (
                # 本轮已检索的政策依据还不足以给出答复。
                reply is None
                # 政策答复模块提供了补查正式条款的查询文本。
                and retry
                # 本轮调查尚未安排政策补查。
                and not state.retry_query
            ):
                emit("replan", decision=ReplanDecision(action=ReplanAction.CONTINUE, reason="仅补查一次正式条款").model_dump(mode="json"))
                return OrderStateUpdate(retry_query=retry, policy_results=results, observation=observation)
            # endregion

            # region 结束政策调查并返回答复或依据不足提示
            emit("replan", decision=ReplanDecision(
                action=ReplanAction.FINISH if reply else ReplanAction.FAIL, reason=evidence.status.value,
            ).model_dump(mode="json"))
            return OrderStateUpdate(reply=reply or NO_EVIDENCE, observation=observation)
            # endregion
        # endregion

        # region 资格咨询仅在取得本人订单事实时继续检索，否则按查单结果结束
        result = observation.order_result
        assert result is not None
        if len(state.plan.steps) == 2:
            if result.order is not None:
                # region 已取得本人订单事实时进入政策检索
                emit("replan", decision=ReplanDecision(
                    action=ReplanAction.CONTINUE, reason="结合本人订单事实检索政策",
                ).model_dump(mode="json"))
                return OrderStateUpdate(step_index=1)
                # endregion

            # region 缺少本人订单事实时按查单结果结束
            return OrderStateUpdate(reply=order_reply(result))
            # endregion
        # endregion

        # region 根据普通查单的实际观察记录决定，并按订单事实回复
        if observation.status is ObservationStatus.FAILED:
            # region 订单服务查询失败时记录失败决定
            decision = ReplanDecision(action=ReplanAction.FAIL, reason="订单查询失败")
            # endregion
        else:
            # region 订单服务正常返回查询结果时记录完成决定
            decision = ReplanDecision(action=ReplanAction.FINISH, reason="唯一的查单步骤已结束")
            # endregion
        emit("replan", decision=decision.model_dump(mode="json"))
        return OrderStateUpdate(replan_decision=decision, reply=order_reply(result))
        # endregion
    # endregion

    # region 按流程节点标识返回各处理函数
    return {
        OrderFlowNode.INTENT: intent,
        OrderFlowNode.PLAN: plan,
        OrderFlowNode.EXECUTE: execute,
        OrderFlowNode.OBSERVE: observe,
        OrderFlowNode.REPLAN: replan,
    }
    # endregion


# 按失败的调查步骤和消费者意图生成本轮降级回复。
def investigation_failure(node: OrderFlowNode, state: OrderState) -> OrderStateUpdate:
    # region 意图识别失败时保留问题并提示本轮未能查单
    if node is OrderFlowNode.INTENT:
        return OrderStateUpdate(question=state.question, reply=ORDER_QUERY_INCOMPLETE)
    # endregion

    # region 政策或订单资格咨询失败时提示政策暂不可用
    if (
        # 本轮已经识别出消费者的咨询意图。
        state.decision
        # 本轮问题需要退款政策依据。
        and (
            # 消费者直接询问退款政策。
            state.decision.intent is ConversationIntent.POLICY_QUESTION
            # 消费者询问某笔订单的退款资格。
            or state.decision.order_goal is OrderGoal.ELIGIBILITY
        )
    ):
        return OrderStateUpdate(reply=POLICY_UNAVAILABLE)
    # endregion

    # region 其他调查失败时提示本轮未能查单
    return OrderStateUpdate(reply=ORDER_QUERY_INCOMPLETE)
    # endregion
