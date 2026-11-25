"""从本轮成功的政策检索回执核验引用来源。"""

from model.policy.evidence import PolicyEvidence
from model.policy.search import PolicySearchResult
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus, PolicySearchStatus


# 按同一目录修订下的实际政策条目核对引用；资格和办理仅接受正式条款。
def verify_policy_evidence(
    evidence: PolicyEvidence, results: list[PolicySearchResult], *, formal_only: bool = True,
) -> bool:

    # region 缺少充分依据或本轮目录修订时拒绝核验
    first_success = next((result for result in results if result.status is PolicySearchStatus.OK), None)
    if (
        # 证据未声明足以支持本轮答案。
        evidence.status is not PolicyEvidenceStatus.SUFFICIENT
        # 证据未关联本轮的政策版本快照。
        or evidence.catalog_revision is None
        # 本轮没有可为证据建立快照的成功回执。
        or first_success is None
        # 证据标注的目录修订与本轮首次成功回执不一致。
        or first_success.catalog_revision != evidence.catalog_revision
    ):
        return False
    # endregion

    # region 按同修订的成功回执索引条目，核对引用的原文及证据用途
    sources = {
        (result.event_id, item.document_id, item.version, item.clause_id, item.entry_type): item.text
        for result in results
        if result.status is PolicySearchStatus.OK and result.catalog_revision == evidence.catalog_revision
        for item in result.candidates
    }
    for citation in evidence.citations:
        entry_type = citation.entry_type
        if (
            # 旧会话中的引用未记录条目类型，无法进行完整的出处核对。
            entry_type is None
            # 当前用途需要正式条款，却引用了其他类型。
            or (
                # 当前正在核验订单资格或退款办理的政策依据。
                formal_only
                # 本条引用属于 FAQ。
                and entry_type is not PolicyEntryType.POLICY
            )
        ):
            return False
        key = (citation.event_id, citation.document_id, citation.version, citation.clause_id, entry_type)
        text = sources.get(key, "")
        if (
            # 引用摘录为空白，无法作为政策依据。
            not citation.excerpt.strip()
            # 本轮成功回执没有该条目或其原文不包含引用摘录。
            or citation.excerpt not in text
        ):
            return False
    return bool(evidence.citations)
    # endregion
