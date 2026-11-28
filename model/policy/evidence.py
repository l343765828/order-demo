"""咨询政策证据与引用的数据契约。"""
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from model.base import StrictModel
from model.base import status_schema
from model.policy.search import PolicyCatalogRevision
from model.policy.status import PolicyEligibilityOutcome, PolicyEvidenceStatus, PolicyEntryType, PolicyOrderFact


# 模型选择实际候选的序号，缺少依据时可建议一次正式条款补查。
class PolicySelection(StrictModel):
    # 模型从 Host 提供的候选序号中选出的条目，未选中时返回 None。
    source_index: int | None
    # 模型建议的正式条款补查文本，没有补查建议时返回 None。
    retry_query: str | None


# 表示例外触发时必须同时满足的一组事实。
class EligibilityException(StrictModel):
    # 同组条件均满足才触发该例外，不同组例外相互独立。
    conditions: list[Annotated[PolicyOrderFact, BeforeValidator(PolicyOrderFact)]]


# 记录从一个实际政策候选中提取的一条独立适用规则。
class EligibilityRule(StrictModel):
    # 本次回执候选的原始数组序号。
    source_index: int
    # 包含适用条件、例外和结论的连续原文，可按本次回执核对。
    excerpt: str
    # 规则要求同时成立的条件；原文明示无附加条件时可为空。
    conditions: list[Annotated[PolicyOrderFact, BeforeValidator(PolicyOrderFact)]]
    # 原文中的例外，任意一组成立即排除本规则。
    exceptions: list[EligibilityException]
    # 原文给出的结论，退款方式不明确时不得视作整单原路。
    outcome: Annotated[PolicyEligibilityOutcome, BeforeValidator(PolicyEligibilityOutcome)]
    # 原文是否直接回答主动索要的政策问题。未指定主题或不能确认时为 False。
    answers_policy_question: bool = False


# 模型从本轮候选原文提取的规则集合，无法提取时为空。
class EligibilityExtraction(StrictModel):
    # 并列适用范围拆为独立规则，保留各自的条件、例外和结论。
    rules: list[EligibilityRule]


# 保存回答所引用的单条政策及本轮检索事件。
class PolicyCitation(StrictModel):
    # 召回被引用条款的政策检索事件编号，用于追溯到本轮实际证据。
    event_id: str = Field(min_length=1)
    # 被引用条款所属的政策文档编号。
    document_id: str = Field(min_length=1)
    # 被引用条款所属的文档版本，用于识别过期依据。
    version: str = Field(min_length=1)
    # 被引用的条目编号，可以是正式条款或常见问题。
    clause_id: str = Field(min_length=1)
    # 被引用条目的类型，用于区分正式条款与常见问题。
    entry_type: Annotated[PolicyEntryType, BeforeValidator(PolicyEntryType)]
    # 被引用的政策原文，核验时必须能在实际检索到的条款中找到。
    excerpt: str = Field(min_length=1)


# 保存原始问题、检索查询及拟采用的政策依据。
class PolicyEvidence(StrictModel):
    # 本轮等待回答的消费者问题，补充订单号时沿用原资格咨询问题。
    question: str = Field(min_length=1)
    # 当前步骤实际使用的检索文本，可能根据订单事实或补查需要改写。
    query: str = Field(min_length=1)
    # 本轮是否已找到支持答复的政策依据，声明充分时必须提供引用。
    status: Annotated[PolicyEvidenceStatus, BeforeValidator(PolicyEvidenceStatus), status_schema(PolicyEvidenceStatus)]
    # 本轮选定的目录修订号，尚未成功读取目录时为 None。
    catalog_revision: PolicyCatalogRevision | None
    # 拟用于本轮答复的政策引用，逐条关联文档版本和本轮检索事件。
    citations: list[PolicyCitation]

    # 充分证据必须明确包含至少一条引用。
    @model_validator(mode='after')
    def require_citations(self):

        # region 声明依据充分时至少提供一条政策引用
        if (
            # 该结果声明退款政策依据充分。
            self.status is PolicyEvidenceStatus.SUFFICIENT
            # 结果没有附上可支持答复的正式条款引用。
            and not self.citations
        ):
            raise ValueError('充分证据必须包含政策引用')
        return self
        # endregion


# 保存资格政策核验结果，供调查节点组织答复或补查。
class EligibilityAnswer(StrictModel):
    # 核验后的政策引文与出处或冲突说明，None 表示可继续补查。
    policy_message: str | None
    # 本轮资格答复使用或无法确认的政策依据。
    evidence: PolicyEvidence
    # 缺少充分依据时，建议沿用本轮快照补查的文本。
    retry_query: str | None
