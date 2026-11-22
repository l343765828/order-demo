"""咨询政策证据及其检索来源校验。"""
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from model.base import StrictModel
from model.policy.search import PolicySearchResult
from model.base import status_schema
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus, PolicySearchStatus


# 模型选择实际候选的序号，缺少依据时可建议一次正式条款补查。
class PolicySelection(StrictModel):
    # 模型从 Host 提供的候选序号中选出的条目，未选中时返回 None。
    source_index: int | None
    # 模型建议的正式条款补查文本，没有补查建议时返回 None。
    retry_query: str | None


# 保存回答所引用的单条正式政策及本轮检索事件。
class PolicyCitation(StrictModel):
    # 召回被引用条款的政策检索事件编号，用于追溯到本轮实际证据。
    event_id: str = Field(min_length=1)
    # 被引用条款所属的政策文档编号。
    document_id: str = Field(min_length=1)
    # 被引用条款所属的文档版本，用于识别过期依据。
    version: str = Field(min_length=1)
    # 被引用的正式条款编号，用于定位文档中的具体规则。
    clause_id: str = Field(min_length=1)
    # 被引用的政策原文，核验时必须能在实际检索到的条款中找到。
    excerpt: str = Field(min_length=1)


# 保存原始问题、检索查询及拟采用的正式政策依据。
class PolicyEvidence(StrictModel):
    # 本轮等待回答的消费者问题，补充订单号时沿用原资格咨询问题。
    question: str = Field(min_length=1)
    # 当前步骤实际使用的检索文本，可能根据订单事实或补查需要改写。
    query: str = Field(min_length=1)
    # 本轮是否已找到支持答复的政策依据，声明充分时必须提供引用。
    status: Annotated[PolicyEvidenceStatus, BeforeValidator(PolicyEvidenceStatus), status_schema(PolicyEvidenceStatus)]
    # 拟用于本轮答复的正式条款引用，逐条关联文档版本和本轮检索事件。
    citations: list[PolicyCitation]

    # 充分证据必须明确包含至少一条引用。
    @model_validator(mode='after')
    def require_citations(self):
        # region 声明依据充分时至少提供一条正式政策引用
        if self.status is PolicyEvidenceStatus.SUFFICIENT and not self.citations:
            raise ValueError('充分证据必须包含正式条款')
        # endregion
        return self


# 仅核验引用来源和原文；语义相关性及订单事实由调用方另行核验。
def verify_policy_evidence(evidence: PolicyEvidence, results: list[PolicySearchResult]) -> bool:
    # region 仅在每条引用都匹配本轮成功检索中的当前版本正式条款和非空原文时通过
    if evidence.status is not PolicyEvidenceStatus.SUFFICIENT:
        return False
    return all(citation.excerpt.strip() and any(
        result.status is PolicySearchStatus.OK and result.event_id == citation.event_id
        and any(candidate.document_id == citation.document_id == 'DEMO-REFUND-POLICY'
                and candidate.title == '演示退款政策与常见问题'
                and candidate.version == citation.version == '1.0'
                and candidate.entry_type is PolicyEntryType.POLICY
                and candidate.clause_id in {f'RF-{number:03d}' for number in range(1, 15)}
                and candidate.clause_id == citation.clause_id
                and citation.excerpt in candidate.text
                for candidate in result.candidates)
        for result in results) for citation in evidence.citations)
    # endregion
