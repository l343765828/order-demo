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
