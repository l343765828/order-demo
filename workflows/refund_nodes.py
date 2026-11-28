"""协调退款调查节点对订单、退款意图和政策能力的调用。"""

from collections.abc import Callable

from config.limits import RefundLimits
from implementations.orders.reply import ORDER_QUERY_INCOMPLETE, SERVICE_ERROR, order_reply
from implementations.policy.answer import NO_EVIDENCE, POLICY_UNAVAILABLE, answer_policy
from implementations.policy.eligibility import answer_eligibility, eligibility_query, eligibility_reply
from implementations.refund.intent import ORDER_NUMBER, resolve_intent
from implementations.refund.execution import RefundExecution
from implementations.refund.qualification import qualify_refund
from implementations.refund.reply import refund_reply
from interfaces.refund.decision import Decide
from interfaces.refund.preference import ManagePreferences
from model.orders.query import OrderQueryResult
from model.orders.status import OrderQueryStatus
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus, PolicySearchMode, PolicySearchStatus
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
    ConversationIntent, HostStepId, InvestigationEvent, InvestigationKind, ObservationStatus, OrderGoal, PolicyRequest,
    ReplanAction, RefundEvent, RefundOutcome, RefundQualification, RefundReason,
)
from model.refund.preference import ListPreferences, PreferenceAction, PreferenceEvent, PreferenceResult, PreferenceStatus
from model.refund.submission import AuthorizedRefundIntent, PreparedRefund, RefundResult

InvestigateOrder = Callable[[InvestigationStep, str], OrderToolEvent | None]
SearchPolicy = Callable[[PolicyStep, str, PolicySearchMode], PolicyToolEvent | None]
InvestigationNodeHandler = Callable[[OrderState], OrderStateUpdate]


# 构建退款调查节点，由节点调用业务能力并提交图状态更新。
def create_investigation_nodes(
    decide: Decide,
    investigate: InvestigateOrder,
    emit: Callable[..., None],
    search: SearchPolicy,
    refund: RefundExecution | None = None,
    is_live: Callable[[str], bool] | None = None,
    preferences: ManagePreferences | None = None,
) -> dict[OrderFlowNode, InvestigationNodeHandler]:

    # region 定义意图识别节点
    # 调用退款意图判断并将结果写入图状态，入口节点负责重置上一轮字段。
    def intent(state: OrderState) -> OrderStateUpdate:

        # region 识别消费者意图并生成本轮状态更新
        result = resolve_intent(state.history, state.eligibility_question, decide, emit)
        authorization = None
        if (
            # 目前没有需要先澄清的事项。
            result.reply is None
            # 本轮意图识别明确请求办理。
            and result.decision is not None
            # 资格咨询不会或不能生成办理授权。
            and result.decision.intent is ConversationIntent.REFUND_REQUEST
        ):

            # region 仅关联当前已保存的真实消费者消息和唯一订单
            source = state.refund_source
            order_id = result.decision.order_id
            if (
                # 调用来自具备本轮可信输入的会话 Host。
                source is not None
                # 已核验本轮唯一指定的办理订单。
                and order_id is not None
                # 图已经配置固定退款能力。
                and refund is not None
            ):
                authorization = AuthorizedRefundIntent(
                    consumer_id=source.consumer_id, session_id=source.session_id,
                    source_message_sequence=source.source_message_sequence, order_id=order_id,
                )
            else:
                result.reply = "暂时无法办理退款。"
            # endregion

        return OrderStateUpdate(
            question=result.question,
            decision=result.decision,
            eligibility_question=result.eligibility_question,
            reply=result.reply,
            refund_authorization=authorization,
        )
        # endregion

    # endregion

    # region 定义处理长期偏好和临时详略的固定节点
    # 执行固定偏好读写，并把临时详略优先应用于本轮答复。
    def manage_preferences(state: OrderState) -> OrderStateUpdate:

        # region 仅用当前活跃轮次的命令调用绑定消费者的存储
        assert state.decision is not None
        decision = state.decision
        command = decision.memory_request
        detail = decision.temporary_detail
        if (
            # 已配置可信消费者的偏好管理接口。
            preferences is not None
            # 有来自应用入口的活跃轮次校验。
            and is_live is not None
            # 当前输入仍由该 Host 调用处理，非历史执行恢复。
            and is_live(state.trace_id)
        ):
            read = ListPreferences(action=PreferenceAction.LIST)
            result = preferences(command or read)
            if (
                # 实际长期偏好写入结果无法确认。
                result.status is PreferenceStatus.FAILED
                # 失败来自显式变更，不能把它当作查看失败。
                and command is not None
                and command.action is not PreferenceAction.LIST
                # 同一消息还要查订单状态。
                and decision.intent is ConversationIntent.ORDER_QUESTION
                and decision.order_goal is not OrderGoal.ELIGIBILITY
                # 消费者没有指定优先的临时详略。
                and detail is None
            ):

                # region 固定写入失败时仍独立读取订单答复适用的当前偏好
                detail = preferences(read).detail
                # endregion

        else:
            result = PreferenceResult(status=PreferenceStatus.FAILED)
        # endregion

        # region 单独记录显式命令的实际结果，隐式读取只用于本轮排版
        if command is not None:
            emit(PreferenceEvent.RESULT.value, action=command.action.value, result=result.model_dump(mode="json"))
        return OrderStateUpdate(
            preference_result=result if command is not None else None,
            response_detail=detail or result.detail,
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
            # 已识别出指定订单。
            decision.order_id is not None
            # 消费者要核对退款资格或办理退款。
            and (
                # 当前路径仅解释资格。
                decision.order_goal is OrderGoal.ELIGIBILITY
                # 当前路径已取得明确的办理授权。
                or state.refund_authorization is not None
            )
        ):

            # region 指定订单的资格咨询和办理先取得本人订单事实
            investigation = InvestigationPlan(steps=[
                InvestigationStep(step_id=HostStepId.ORDER.value, kind=InvestigationKind.INSPECT_ORDER,
                                  order_id=decision.order_id, depends_on=[], completion="取得本人订单事实"),
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
    # 读取当前步骤，政策补查沿用首次回执的目录版本。
    def current_step(state: OrderState) -> InvestigationStep | PolicyStep:

        # region 读取正在执行的计划步骤，政策补查替换查询并沿用目录版本
        if state.plan is None:
            raise ValueError("缺少调查计划")
        step = state.plan.steps[state.step_index]
        if isinstance(step, PolicyStep):

            # region 政策步用当前查询和本轮首个目录版本执行
            step = step.model_copy()
            step.query = state.retry_query or step.query
            step.catalog_revision = state.policy_results[0].catalog_revision if state.policy_results else None
            # endregion

        return step
        # endregion

    # 执行已验证的当前步骤，每个步骤最多发送一次工具调用。
    def execute(state: OrderState) -> OrderStateUpdate:

        # region 按当前步骤调用政策或查单工具并提交回执
        step = current_step(state)
        emit("execute", step_id=step.step_id, tool_budget=1)
        event: OrderToolEvent | PolicyToolEvent | None
        if isinstance(step, PolicyStep):
            event = search(step, state.trace_id, policy_mode(state))
        else:
            event = investigate(step, state.trace_id)
        return OrderStateUpdate(event=event)
        # endregion

    # endregion

    # region 固定政策调查使用范围
    # 按订单解释、先看正式条款和补查均限正式条款，通用咨询可含 FAQ。
    def policy_mode(state: OrderState) -> PolicySearchMode:

        # region 从召回前选择政策范围
        formal = (
            state.denial_qualification is not None
            or state.policy_qualification is not None
            or state.retry_query is not None
            or (state.decision is not None and state.decision.policy_request is PolicyRequest.BEFORE_REFUND)
        )
        return PolicySearchMode.FORMAL if formal else PolicySearchMode.CONSULTATION
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
                return OrderStateUpdate(reply=policy_reply(state, emit))
            if event.result.status is PolicySearchStatus.OK:

                # region 成功回执缺少目录修订或补查结果超出本轮范围时停止咨询
                if (
                    # 成功回执没有可固定的目录修订号。
                    event.result.catalog_revision is None
                    # 补查结果未沿用首次成功回执的修订。
                    or (
                        # 本轮步骤明确要求沿用首轮修订进行正式条款补查。
                        step.catalog_revision is not None
                        # 目录修订改变会混用不同版本快照。
                        and event.result.catalog_revision != step.catalog_revision
                    )
                    # 正式条款范围不允许工具返回 FAQ。
                    or (
                        # 本步仅允许召回正式条款。
                        policy_mode(state) is PolicySearchMode.FORMAL
                        # 工具回执混入其他条目类型。
                        and any(item.entry_type is not PolicyEntryType.POLICY for item in event.result.candidates)
                    )
                ):
                    emit("observation_rejected", step_id=step.step_id)
                    return OrderStateUpdate(reply=policy_reply(state, emit))
                # endregion

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
    # 取得消费者主动索要的政策问题，供检索和解释使用，省去订单编号。
    def explicit_policy_question(state: OrderState) -> str | None:

        # region 普通业务拒绝只用已确认的业务原因查询政策
        if (
            # 本轮还未识别出业务意图。
            state.decision is None
            # 本轮没有主动索要政策依据。
            or state.decision.policy_request is None
        ):
            return None
        # endregion

        # region 查找主动请求的内容时删除订单编号并限制文本长度
        return ORDER_NUMBER.sub("", state.question)[:RefundLimits.MAX_POLICY_QUERY_CHARS]
        # endregion

    # 按已确认的资格追加唯一的只读政策步骤，保留解释需要的事实和业务回执。
    def investigate_order_policy(
        state: OrderState, qualification: RefundQualification, *, order_result: OrderQueryResult | None = None,
        refund_result: RefundResult | None = None, denial: bool = False,
    ) -> OrderStateUpdate:

        # region 在本人订单调查后安排唯一的条款解释步骤
        assert state.plan is not None
        plan = state.plan
        if len(plan.steps) == 1:

            # region 尚未安排政策检索时按确认的资格结果追加
            policy_step = PolicyStep(
                step_id=HostStepId.POLICY.value, kind=InvestigationKind.RETRIEVE_POLICY,
                query=eligibility_query(qualification, policy_question=None if denial else explicit_policy_question(state)),
                depends_on=[HostStepId.ORDER.value],
                completion="按已确认的退款资格查找适用条款",
            )
            plan = InvestigationPlan(steps=[*plan.steps, policy_step])
            # endregion

        # endregion

        # region 记录已确认的业务结果并推进到可恢复的政策步骤
        if refund_result is not None:

            # region 在政策调查之前记录实际办理回执
            emit(RefundEvent.RESULT.value, result=refund_result.model_dump(mode="json"))
            # endregion

        emit(InvestigationEvent.REPLAN.value, decision=ReplanDecision(
            action=ReplanAction.CONTINUE, reason="按已确认的退款资格调查适用条款",
        ).model_dump(mode="json"))
        return OrderStateUpdate(plan=plan, step_index=len(plan.steps) - 1,
                                denial_qualification=qualification if denial else None,
                                policy_qualification=None if denial else qualification, order_result=order_result,
                                refund_result=refund_result)
        # endregion

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
                emit(InvestigationEvent.REPLAN.value, decision=ReplanDecision(
                    action=ReplanAction.FAIL, reason="政策检索失败",
                ).model_dump(mode="json"))
                return OrderStateUpdate(reply=policy_reply(state, emit))
            # endregion

            # region 核验政策候选并记录本轮证据
            step = current_step(state)
            assert isinstance(step, PolicyStep)
            results = [*state.policy_results, observation.policy_result]
            qualification = state.denial_qualification or state.policy_qualification
            if qualification is not None:

                # region 从正式候选提取规则，核对订单事实和程序资格结论
                answer = answer_eligibility(
                    decide, state.question, step.query, results, state.order_result,
                    qualification=qualification, handling=state.refund_authorization is not None,
                    policy_question=explicit_policy_question(state),
                )
                reply = policy_reply(state, emit, answer.policy_message) if answer.policy_message is not None else None
                evidence = answer.evidence
                retry = answer.retry_query
                # endregion

            else:

                # region 普通政策咨询保留按问题选择依据的流程
                reply, evidence, retry = answer_policy(decide, state.question, step.query, results)
                if reply is not None:
                    reply = policy_reply(state, emit, reply)
                # endregion

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
                emit(InvestigationEvent.REPLAN.value, decision=ReplanDecision(
                    action=ReplanAction.CONTINUE, reason="仅补查一次正式条款",
                ).model_dump(mode="json"))
                return OrderStateUpdate(retry_query=retry, policy_results=results, observation=observation)
            # endregion

            # region 结束政策调查并返回答复或依据不足提示
            emit(InvestigationEvent.REPLAN.value, decision=ReplanDecision(
                action=ReplanAction.FAIL if evidence.status is PolicyEvidenceStatus.CONFLICT or not reply
                else ReplanAction.FINISH, reason=evidence.status.value,
            ).model_dump(mode="json"))
            explicit = state.decision is not None and state.decision.policy_request is not None
            fallback = POLICY_UNAVAILABLE if qualification is not None or explicit else NO_EVIDENCE
            return OrderStateUpdate(reply=reply or policy_reply(state, emit, fallback), observation=observation,
                                    policy_results=results)
            # endregion

        # endregion

        # region 已授权时先检查本人订单、原退款及未决提交
        result = observation.order_result
        assert result is not None
        if state.refund_authorization is not None:

            # region 办理优先核对原退款及未决提交，资格拒绝继续只读检索
            assert refund is not None
            refund_result = refund.preflight(state.refund_authorization, result, state.trace_id)
            if refund_result is not None:
                denial = refund_result.order_state_denial
                if denial is not None:

                    # region 订单因支付或发货状态被拒时保留结果并调查正式条款
                    return investigate_order_policy(state, denial, order_result=result, refund_result=refund_result,
                                                    denial=True)
                    # endregion

                # region 已有退款结果或无法操作时直接回复该业务结果
                return OrderStateUpdate(refund_result=refund_result)
                # endregion

            # endregion

            # region 初查允许办理时进入独立的业务复核
            decision = ReplanDecision(action=ReplanAction.CONTINUE, reason="初查允许办理，复核本人订单")
            emit(InvestigationEvent.REPLAN.value, decision=decision.model_dump(mode="json"))
            return OrderStateUpdate(replan_decision=decision)
            # endregion

        # endregion

        # region 对取得可信订单事实的资格咨询直接判断资格
        order = result.order
        if (
            # 本轮是只读资格咨询。
            state.decision is not None
            and state.decision.order_goal is OrderGoal.ELIGIBILITY
            # 本轮已经从订单服务取得本人订单事实。
            and result.status is OrderQueryStatus.FOUND
            and order is not None
        ):
            qualification = qualify_refund(payment_status=order.payment_status, shipping_status=order.shipping_status,
                                           refund_status=order.refund_status)
            if (
                # 订单符合退款资格。
                qualification is RefundQualification.ELIGIBLE
                # 普通资格咨询没有请求政策依据。
                and state.decision.policy_request is None
            ):

                # region 合格咨询按业务事实答复，省去政策调查
                return OrderStateUpdate(reply=eligibility_reply(result, qualification))
                # endregion

            # region 有明确资格拒绝或消费者要求依据时调查正式条款
            return investigate_order_policy(state, qualification, order_result=result,
                                            denial=qualification is not RefundQualification.ELIGIBLE)
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

        emit(InvestigationEvent.REPLAN.value, decision=decision.model_dump(mode="json"))
        return OrderStateUpdate(replan_decision=decision, reply=order_reply(result, detail=state.response_detail))
        # endregion

    # endregion

    # region 定义复核、受控发送及确定性回复节点
    # 重查本人订单，按最新事实复核授权对应的办理资格。
    def review_refund(state: OrderState) -> OrderStateUpdate:

        # region 将已核验的本轮输入交给固定业务能力进行最终复核
        assert refund is not None
        assert state.refund_authorization is not None
        current = refund.read_current_order(state.refund_authorization, state.trace_id)
        result = refund.review(state.refund_authorization, state.trace_id, current)
        if isinstance(result, PreparedRefund):

            # region 最新状态仍允许办理时准备受控发送
            return OrderStateUpdate(prepared_refund=result, order_result=current)
            # endregion

        denial = result.order_state_denial
        if denial is not None:

            # region 复核发现资格变化时按最新订单事实调查拒绝依据
            assert current is not None
            return investigate_order_policy(state, denial, order_result=current, refund_result=result, denial=True)
            # endregion

        return OrderStateUpdate(refund_result=result, order_result=current)
        # endregion

    # 仅对活跃会话轮次完成复核的提交尝试发送一次，拒绝 checkpoint 自动重放。
    def send_refund(state: OrderState) -> OrderStateUpdate:

        # region 验证当前正在处理原授权轮次及其已复核的提交信息
        prepared = state.prepared_refund
        if (
            # 图正在恢复而没有本轮消费者输入。
            is_live is None
            # 该轮不是当前 CLI 正在处理的输入。
            or not is_live(state.trace_id)
            # 本轮没有已复核的提交信息。
            or prepared is None
            # 提交来自其他轮次的追踪标识。
            or prepared.trace_id != state.trace_id
            # 提交关联的办理授权与当前授权不一致。
            or prepared.authorization != state.refund_authorization
        ):

            # region 轮次或授权不匹配时返回待核对结果
            return OrderStateUpdate(refund_result=RefundResult(
                status=RefundOutcome.UNCONFIRMED, reason=RefundReason.RECEIPT_UNKNOWN,
            ))
            # endregion

        # endregion

        # region 使用当前有效提交继续单次发送
        assert refund is not None
        result = refund.send(prepared)
        denial = result.order_state_denial
        if denial is not None:

            # region 事务已结束且确认资格拒绝时按该次回执调查政策
            return investigate_order_policy(state, denial, refund_result=result, denial=True)
            # endregion

        return OrderStateUpdate(refund_result=result)
        # endregion

    # 根据真实业务回执输出消费者回复，不再调用模型。
    def render_refund(state: OrderState) -> OrderStateUpdate:

        # region 记录业务结果并按确定性模板生成回复
        result = state.refund_result
        assert result is not None
        if (
            # 本轮已经处理办理请求。
            state.decision is not None
            # 消费者同时要求解释退款依据。
            and state.decision.policy_request is PolicyRequest.BASIS
        ):

            # region 取得真实退款单后，按可核实的办理前事实调查政策
            order_result = state.order_result
            order = order_result.order if order_result is not None else None
            if (
                # 办理已经确认退款单及其目标。
                result.refund is not None
                # 最新一次已核验的本人订单与退款单一致。
                and order is not None
                # 真实回执属于该订单。
                and result.refund.order_id == order.order_id
            ):
                qualification = qualify_refund(payment_status=order.payment_status,
                                               shipping_status=order.shipping_status,
                                               refund_status=order.refund_status)
                if (
                    # 退款成功以提交前已核验的合格状态解释。
                    result.status is RefundOutcome.SIMULATED_SUCCEEDED
                    # 回复原退款时，已有退款状态也经过可信查单核实。
                    or qualification is RefundQualification.ALREADY_REFUNDED
                ):

                    # region 按真实退款单和已核验的资格追加只读调查
                    return investigate_order_policy(state, qualification, order_result=order_result,
                                                    refund_result=result)
                    # endregion
            # endregion

            # region 缺少可解释的资格事实时保留业务回执并说明依据不可核实
            emit(RefundEvent.RESULT.value, result=result.model_dump(mode="json"))
            return OrderStateUpdate(reply=policy_reply(state, emit))
            # endregion

        emit(RefundEvent.RESULT.value, result=result.model_dump(mode="json"))
        return OrderStateUpdate(reply=refund_reply(result, emit))
        # endregion

    # endregion

    # region 按流程节点标识返回各处理函数
    return {
        OrderFlowNode.INTENT: intent,
        OrderFlowNode.PREFERENCES: manage_preferences,
        OrderFlowNode.PLAN: plan,
        OrderFlowNode.EXECUTE: execute,
        OrderFlowNode.OBSERVE: observe,
        OrderFlowNode.REPLAN: replan,
        OrderFlowNode.REVIEW_REFUND: review_refund,
        OrderFlowNode.SEND_REFUND: send_refund,
        OrderFlowNode.REFUND_REPLY: render_refund,
    }
    # endregion


# 在已确认的业务结果后附上核验的依据或政策降级说明。
def policy_reply(state: OrderState, emit: Callable[..., None], message: str = POLICY_UNAVAILABLE) -> str:

    # region 提交前拒绝或只读资格咨询使用可信的查单事实
    qualification = state.denial_qualification or state.policy_qualification
    if (
        # 当前有经过核验的订单回执。
        state.order_result is not None
        # 回执包含可信本人订单详情。
        and state.order_result.order is not None
        # 本次政策调查采用了已核验的资格。
        and qualification is not None
        # 需要展示本人订单事实和资格结论。
        and (
            # 本轮只读，未进入办理。
            state.refund_result is None
            # 本轮办理在提交前已按该资格被拒。
            or state.denial_qualification is not None
        )
    ):

        # region 以查单事实解释退款资格并附政策说明
        message = eligibility_reply(
            state.order_result, qualification, message, handling=state.refund_authorization is not None,
        )
        # endregion

    elif state.refund_result is not None:

        # region 已有办理回执时先回复真实结果，再解释政策
        message = f"{refund_reply(state.refund_result, emit)}\n{message}"
        # endregion

    # endregion

    # region 等待条款时告知本轮未提交及后续明确办理所需的输入
    if (
        # 本轮已识别到政策调查意图。
        state.decision is not None
        # 消费者要先看条款或把正式条款设为前提。
        and state.decision.policy_request is PolicyRequest.BEFORE_REFUND
    ):

        # region 等待条款的消费者可在后续消息重新明确授权
        return f"{message}\n本轮未提交退款。如要办理，请在同一条新消息中明确要求退款，并写出唯一的演示订单编号。"
        # endregion

    return message
    # endregion


# 按失败的调查步骤和消费者意图生成本轮降级回复。
def investigation_failure(node: OrderFlowNode, state: OrderState, emit: Callable[..., None]) -> OrderStateUpdate:

    # region 意图识别失败时保留待补号问题并提示本轮未能查单
    if node is OrderFlowNode.INTENT:
        return OrderStateUpdate(question=state.question, eligibility_question=state.eligibility_question,
                                reply=ORDER_QUERY_INCOMPLETE)
    # endregion

    # region 政策调查失败时保留已确认的资格或办理结果
    if (
        # 执行失败前已确认需要解释的拒绝原因。
        state.denial_qualification is not None
        # 本轮正在执行已安排的只读政策步骤。
        or (
            # 本轮已生成调查计划。
            state.plan is not None
            # 当前执行的是政策检索或解释。
            and isinstance(state.plan.steps[state.step_index], PolicyStep)
        )
        # 当前消费者直接询问通用政策。
        or (
            # 本轮已有明确的意图。
            state.decision is not None
            # 该意图是通用政策咨询。
            and state.decision.intent is ConversationIntent.POLICY_QUESTION
        )
    ):
        return OrderStateUpdate(reply=policy_reply(state, emit))
    # endregion

    # region 其他调查失败时提示本轮未能查单
    return OrderStateUpdate(reply=ORDER_QUERY_INCOMPLETE)
    # endregion
