"""识别消费者的退款相关意图并核对本轮提供的订单号。"""

import re
from collections.abc import Callable

from config.limits import RefundLimits
from implementations.orders.reply import ORDER_QUERY_INCOMPLETE
from interfaces.refund.decision import Decide
from model.refund.flow import ConversationDecision, ExchangeMessage
from model.refund.intent import IntentResolution
from model.refund.status import ConversationIntent, OrderGoal

ORDER_NUMBER = re.compile(r"(?<![A-Za-z0-9-])DEMO-[A-Za-z0-9-]+", re.IGNORECASE)


# 识别当前消息意图，返回已核对的查单目标、补号问题或直接回复。
def resolve_intent(
    history: list[ExchangeMessage],
    pending: str | None,
    decide: Decide,
    emit: Callable[..., None],
) -> IntentResolution:
    # region 读取最新消费者消息并初始化意图结果
    current = history[-1].content
    resolution = IntentResolution(question=current)
    # endregion

    # region 会话累计正文超限时请消费者开启新会话
    if sum(len(m.content) for m in history) > RefundLimits.MAX_CONVERSATION_HISTORY_CHARS:
        resolution.reply = "当前会话内容过长，请开启新会话后再试。"
        return resolution
    # endregion

    # region 提取订单号并判断沿用资格咨询或识别新意图
    candidates = {match.upper() for match in ORDER_NUMBER.findall(current)}
    if (pending and ORDER_NUMBER.fullmatch(current.strip())
            and len(current.strip()) <= RefundLimits.MAX_ORDER_ID_CHARS):
        # region 只收到合规订单号时接续待补号的资格咨询
        decision = ConversationDecision(intent=ConversationIntent.ORDER_QUESTION, order_id=current.strip().upper(),
                                        order_goal=OrderGoal.ELIGIBILITY)
        resolution.question = pending
        # endregion

    else:
        # region 其他消息由模型识别本轮意图和订单号
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
        # endregion
    # endregion

    # region 记录本轮意图并更新识别结果
    emit("intent", decision=decision.model_dump())
    resolution.decision = decision
    # endregion
    # region 按意图校验查单目标，必要时生成直接回复或保存待补单问题
    match decision.intent:
        case ConversationIntent.POLICY_QUESTION:
            # region 通用政策咨询直接返回识别结果
            return resolution
            # endregion
        case ConversationIntent.REFUND_REQUEST:
            # region 明确请求办理退款时说明本轮处理结果
            resolution.reply = "已识别为退款办理请求。当前演示支持咨询和查单，本轮没有提交退款。"
            # endregion
        case ConversationIntent.UNCLEAR:
            # region 意图不清时询问是咨询资格还是请求办理
            resolution.reply = "请问你想咨询这笔订单能否退款，还是要办理退款？"
            # endregion
        case ConversationIntent.ORDER_QUESTION:
            # region 查单时核对本轮编号，按结果追问、结束或规范化编号
            if len(candidates) != 1 or decision.order_id is None:
                # region 未确定唯一编号时追问，缺少资格咨询编号时保留待补问题
                if not candidates and decision.order_goal is OrderGoal.ELIGIBILITY:
                    resolution.eligibility_question = current
                    resolution.reply = "请提供要咨询资格的演示订单编号。"
                else:
                    resolution.reply = "请提供一个要查询的演示订单编号。"
                # endregion
            elif (decision.order_id.upper() not in candidates
                    or len(decision.order_id) > RefundLimits.MAX_ORDER_ID_CHARS):
                # region 编号与原文不符或超长时停止查单
                resolution.reply = ORDER_QUERY_INCOMPLETE
                # endregion
            else:
                # region 编号核验通过后统一为大写
                decision.order_id = decision.order_id.upper()
                # endregion
            # endregion
        case _:
            # region 其他意图提示可咨询政策或查询订单
            resolution.reply = "请告诉我要查询的订单或退款政策问题。"
            # endregion
    return resolution
    # endregion
