"""描述当前消费者消息的意图判断结果。"""

from model.base import StrictModel
from model.refund.flow import ConversationDecision


# 保存本轮意图判断、可直接发送的回复及待补充的资格咨询。
class IntentResolution(StrictModel):
    # 本轮待回答的问题，补充订单号时恢复原资格咨询问题。
    question: str
    # 实际识别的意图，因会话长度提前结束时为 None。
    decision: ConversationDecision | None = None
    # 需要消费者补充订单号的资格咨询问题，无待补充问题时为 None。
    eligibility_question: str | None = None
    # 无需继续调查时直接回复消费者，仍需调查时为 None。
    reply: str | None = None
