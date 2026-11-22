"""按本轮政策检索结果核验证据。"""

from implementations.policy.source import DOCUMENT_ID, FORMAL_CLAUSE_IDS, TITLE, VERSION
from model.policy.evidence import PolicyEvidence
from model.policy.search import PolicySearchResult
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus, PolicySearchStatus


# 仅核验引用来源和原文；语义相关性及订单事实由调用方另行核验。
def verify_policy_evidence(evidence: PolicyEvidence, results: list[PolicySearchResult]) -> bool:
    # region 仅在每条引用都匹配本轮成功检索中的当前版本正式条款和非空原文时通过
    if evidence.status is not PolicyEvidenceStatus.SUFFICIENT:
        return False
    return all(citation.excerpt.strip() and any(
        result.status is PolicySearchStatus.OK and result.event_id == citation.event_id
        and any(candidate.document_id == citation.document_id == DOCUMENT_ID
                and candidate.title == TITLE
                and candidate.version == citation.version == VERSION
                and candidate.entry_type is PolicyEntryType.POLICY
                and candidate.clause_id in FORMAL_CLAUSE_IDS
                and candidate.clause_id == citation.clause_id
                and citation.excerpt in candidate.text
                for candidate in result.candidates)
        for result in results) for citation in evidence.citations)
    # endregion
