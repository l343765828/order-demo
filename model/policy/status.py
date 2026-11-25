"""退款政策检索及政策依据模型使用的枚举。"""

from enum import StrEnum


# 区分咨询检索和仅检索正式条款。
class PolicySearchMode(StrEnum):
    CONSULTATION = "consultation"  # 可以检索 FAQ 和正式政策条款。
    FORMAL = "formal"  # 只检索正式政策条款。


# 区分正式政策条款和常见问题。
class PolicyEntryType(StrEnum):
    POLICY = "policy"  # 可用于核验退款规则或回复依据的正式条款。
    FAQ = "faq"  # 用于解释常见问题，采用答案前仍须核验正式出处。


# 区分政策搜索成功和检索失败。
class PolicySearchStatus(StrEnum):
    OK = "ok"  # 检索流程正常结束，候选仍需核验是否适用。
    FAILED = "failed"  # 向量化、召回或重排失败，本轮不提供候选。


# 表示本轮政策依据能否支持消费者回复。
class PolicyEvidenceStatus(StrEnum):
    SUFFICIENT = "sufficient"  # 本轮有可支持回答的正式条款。
    INSUFFICIENT = "insufficient"  # 本轮正式依据不足，需补查或说明当前政策未说明。
    CONFLICT = "conflict"  # 本轮适用的正式条款与可信订单资格结论冲突。


# 标识资格咨询留存的结构化提取事件。
class PolicyEvent(StrEnum):
    ELIGIBILITY_EXTRACTION = "policy_eligibility_extraction"  # 已从本轮候选原文提取资格规则。


# 为政策原文中的条件标注可用订单事实和暂无法核实的取值。
class PolicyOrderFact(StrEnum):
    OWNED = "owned"  # 订单属于当前消费者。
    OTHER_OWNER = "other_owner"  # 订单属于其他消费者，不能用本人订单证实。
    PAID = "paid"  # 已收到这笔订单的支付款项。
    UNPAID = "unpaid"  # 这笔订单尚未支付。
    SHIPPED = "shipped"  # 订单商品已发货。
    UNSHIPPED = "unshipped"  # 订单商品尚未发货。
    REFUNDED = "refunded"  # 已有这笔订单的成功模拟退款记录。
    NOT_REFUNDED = "not_refunded"  # 尚无这笔订单的成功模拟退款记录。
    UNKNOWN = "unknown"  # 条件超出本轮可信事实，例如会员等级或签收日期。


# 区分政策原文对当前退款申请描述的结论。
class PolicyEligibilityOutcome(StrEnum):
    FULL_ORIGINAL = "full_original"  # 明确允许按原支付渠道退回整笔实付款。
    DENIED = "denied"  # 明确不办理本次退款，包括已有退款后的重复请求。
    OTHER_REFUND = "other_refund"  # 明确允许与整单原路不同的退款方式。
    ALLOWED = "method_unspecified"  # 明确允许办理，但退款金额或渠道不足以核实整单原路。
    UNSPECIFIED = "no_explicit_decision"  # 原文没有明确说明能否办理退款。
