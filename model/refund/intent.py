"""定义模型的本轮意图契约和应用采用的业务结果。"""

from typing import Annotated, Self

from pydantic import BeforeValidator, model_validator

from model.base import StrictModel, status_schema
from model.refund.flow import BusinessConversationDecision, ConversationDecision, PolicyRequestValue
from model.refund.status import ConversationIntent, OrderGoal, OrderIdExtractionStatus


# 扩展模型侧意图契约，提取状态和依据仅用于本轮处理及观察。
class IntentDecision(BusinessConversationDecision):
    # 从本轮原文识别的独立政策需求，未提出时为 None。
    policy_request: PolicyRequestValue = None
    # 模型是否在本轮明确提取唯一目标，区分缺号与目标不明。
    order_id_status: Annotated[
        OrderIdExtractionStatus, BeforeValidator(OrderIdExtractionStatus), status_schema(OrderIdExtractionStatus),
    ]
    # 支持模型选单的连续消费者原文，缺号或歧义时为 None。
    order_id_evidence: str | None
    # 本轮是否在补充原资格咨询所需的订单号，提出新需求时为 False。
    continues_eligibility_question: bool

    # 核对提取字段与状态的一致性，以及接续标记是否用于资格咨询。
    @model_validator(mode="after")
    def check_extraction(self) -> Self:

        # region 按提取状态要求原样编号和依据同时存在或同时为空
        match self.order_id_status:
            case OrderIdExtractionStatus.EXTRACTED:

                # region 明确提取必须同时提供非空编号和依据
                if (
                    # 缺少本轮原样编号或编号为空白。
                    not (self.order_id or "").strip()
                    # 缺少支持选单的原文依据或依据为空白。
                    or not (self.order_id_evidence or "").strip()
                ):

                    # region 拒绝缺少有效编号或有效依据的明确提取
                    raise ValueError("明确提取时必须同时提供非空订单号和原文依据")
                    # endregion

                # endregion

            case OrderIdExtractionStatus.MISSING | OrderIdExtractionStatus.AMBIGUOUS:

                # region 未选定订单时两个字段均为空
                if (
                    # 缺号或目标不明时模型仍提供了订单号。
                    self.order_id is not None
                    # 缺号或目标不明时模型仍提供了选单依据。
                    or self.order_id_evidence is not None
                ):

                    # region 拒绝在缺号或歧义状态下附带选单信息
                    raise ValueError("缺号或歧义时订单号和原文依据必须为 null")
                    # endregion

                # endregion

        # endregion

        # region 只有原资格咨询的补号续接可以声明沿用原问题
        if (
            # 模型判断本轮是在回应资格咨询的补号追问。
            self.continues_eligibility_question
            # 模型给出的本轮意图或查询目标无法接续原资格咨询。
            and (
                # 本轮意图并非指定订单咨询。
                self.intent is not ConversationIntent.ORDER_QUESTION
                # 本轮要查订单状态或没有指定资格目标。
                or self.order_goal is not OrderGoal.ELIGIBILITY
            )
        ):

            # region 拒绝把新的业务意图或查单目标当作补号
            raise ValueError("补号接续必须是退款资格咨询")
            # endregion

        return self
        # endregion


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
