"""订单、政策与调查使用的状态枚举。"""

from enum import StrEnum

from pydantic import WithJsonSchema


# 表示消费者本轮的业务意图。
class ConversationIntent(StrEnum):
    ORDER_QUESTION = "order_question"  # 查询本人订单状态或咨询该订单能否退款。
    POLICY_QUESTION = "policy_question"  # 咨询通用退款规则，例如退款条件或到账时间。
    REFUND_REQUEST = "refund_request"  # 明确请求办理退款，包括满足条件时办理的请求。
    CANCEL = "cancel"  # 请求取消尚未执行的退款办理。
    CHANGE_ORDER = "change_order"  # 请求更换本次退款办理的目标订单。
    MEMORY_REQUEST = "memory_request"  # 请求查看、保存、更正或删除长期偏好。
    OUT_OF_SCOPE = "out_of_scope"  # 请求超出退款演示支持的业务范围。
    UNCLEAR = "unclear"  # 当前消息不足以确定意图，例如无法区分咨询和办理，需要先澄清。


# 区分订单状态查询和退款资格咨询。
class OrderGoal(StrEnum):
    STATUS = "status"  # 查询订单的支付、发货或模拟退款状态。
    ELIGIBILITY = "eligibility"  # 结合订单事实和政策解释能否退款，本轮只做咨询。


# 表示调查后继续、结束或失败。
class ReplanAction(StrEnum):
    CONTINUE = "continue"  # 根据已有观察继续调查或补查政策。
    FINISH = "finish"  # 调查已得出结果，可以结束本轮。
    FAIL = "fail"  # 查询失败或证据不足，结束本轮并说明原因。


# 区分订单与政策调查步骤。
class InvestigationKind(StrEnum):
    INSPECT_ORDER = "inspect_order"  # 按当前消费者身份读取一笔订单的业务事实。
    RETRIEVE_POLICY = "retrieve_policy"  # 检索并重排当前版本的退款政策候选。


# 区分咨询检索和仅检索正式条款。
class PolicySearchMode(StrEnum):
    CONSULTATION = "consultation"  # 允许同时召回常见问题和正式政策条款。
    FORMAL = "formal"  # 从召回阶段起只检索正式政策条款。


# 区分正式政策条款和常见问题。
class PolicyEntryType(StrEnum):
    POLICY = "policy"  # 可用于核验退款规则或回复依据的正式条款。
    FAQ = "faq"  # 用于解释常见问题，采用答案前仍须核验正式出处。


# 区分本人订单查找成功、不可操作及服务失败。
class OrderQueryStatus(StrEnum):
    FOUND = "found"  # 已找到归属当前消费者的订单，可以返回订单详情。
    UNAVAILABLE = "unavailable"  # 订单不存在或不属于当前消费者，统一隐藏订单详情。
    FAILED = "failed"  # 订单服务查询失败，无法确认订单状态。


# 表示订单是否已支付。
class PaymentStatus(StrEnum):
    PAID = "paid"  # 订单已支付，实付金额以订单记录为准。
    UNPAID = "unpaid"  # 订单未支付，没有可退的实付款项。


# 表示订单是否已发货。
class ShippingStatus(StrEnum):
    SHIPPED = "shipped"  # 订单已发货，超出演示支持的退款范围。
    UNSHIPPED = "unshipped"  # 订单尚未发货，满足退款资格的一项条件。


# 表示订单是否已有模拟退款记录。
class RefundStatus(StrEnum):
    REFUNDED = "refunded"  # 已有成功的模拟退款记录。
    NOT_REFUNDED = "not_refunded"  # 没有成功的模拟退款记录，仍需判断退款资格。


# 区分政策搜索成功和检索失败。
class PolicySearchStatus(StrEnum):
    OK = "ok"  # 检索流程正常结束，候选仍需核验是否适用。
    FAILED = "failed"  # 向量化、召回或重排失败，本轮不提供候选。


# 表示本轮政策依据能否支持消费者回复。
class PolicyEvidenceStatus(StrEnum):
    SUFFICIENT = "sufficient"  # 本轮有可支持回答的正式条款。
    INSUFFICIENT = "insufficient"  # 本轮正式依据不足，需补查或说明当前政策未说明。


# 表示调查步骤是否完成、缺少依据或失败。
class ObservationStatus(StrEnum):
    COMPLETED = "completed"  # 步骤正常取得订单结果或政策检索结果。
    NO_EVIDENCE = "no_evidence"  # 步骤正常返回，但未找到可继续调查的本人订单。
    FAILED = "failed"  # 工具查询失败，无法取得本步骤的业务结果。


# 保留原有内联字符串枚举 Schema，供按完整 Schema 绑定工具的 MCP 客户端使用。
def status_schema(status: type[StrEnum], title: str = "Status") -> WithJsonSchema:
    return WithJsonSchema({"type": "string", "enum": [member.value for member in status], "title": title})
