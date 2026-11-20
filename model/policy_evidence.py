"""咨询政策证据及其检索来源校验。"""
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from model.order_query import StrictModel
from model.policy import PolicySearchResult
from model.status import PolicyEntryType, PolicyEvidenceStatus, PolicySearchStatus, status_schema


# 模型选择实际候选的序号，缺少依据时可建议一次正式条款补查。
class PolicySelection(StrictModel):
    source_index: int | None
    retry_query: str | None


# 保存回答所引用的单条正式政策及本轮检索事件。
class PolicyCitation(StrictModel):
    event_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    clause_id: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


# 保存原始问题、检索查询及拟采用的正式政策依据。
class PolicyEvidence(StrictModel):
    question: str = Field(min_length=1)
    query: str = Field(min_length=1)
    status: Annotated[PolicyEvidenceStatus, BeforeValidator(PolicyEvidenceStatus), status_schema(PolicyEvidenceStatus)]
    citations: list[PolicyCitation]

    # 充分证据必须明确包含至少一条引用。
    @model_validator(mode='after')
    def require_citations(self):
        if self.status is PolicyEvidenceStatus.SUFFICIENT and not self.citations:
            raise ValueError('充分证据必须包含正式条款')
        return self


# 仅核验引用来源和原文；语义相关性及订单事实由调用方另行核验。
def verify_policy_evidence(evidence: PolicyEvidence, results: list[PolicySearchResult]) -> bool:
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
