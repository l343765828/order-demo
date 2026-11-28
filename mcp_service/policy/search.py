"""协调退款政策查询编码、召回与重排。"""
from uuid import uuid4

from config.limits import PolicyLimits
from interfaces.policy.catalog import PolicyCatalogReader
from interfaces.policy.search import PolicySearchBackend
from model.policy.search import PolicyCandidate, PolicySearchResult
from model.policy.status import PolicySearchMode, PolicySearchStatus
from utils.events import log_event


# 完成编码、带范围过滤的召回和重排；任一阶段失败均不返回未重排内容。
def search_policy(
    query: str, mode: PolicySearchMode, backend: PolicySearchBackend, *,
    catalog: PolicyCatalogReader, catalog_revision: int | None = None,
) -> PolicySearchResult:

    # region 分配本轮检索事件编号
    event_id = str(uuid4())
    # endregion

    # region 执行政策召回与重排，记录并返回检索结果
    try:

        # region 从发布目录解析本轮使用的全部文档版本
        snapshot = catalog.snapshot(catalog_revision)
        # endregion

        # region 使用后端已归一化的向量从milvus召回候选，限制召回数量并在有候选时完成重排
        vector = backend.embed(query)
        candidates = backend.recall(vector, mode, snapshot.versions)
        if len(candidates) > PolicyLimits.MAX_RECALLED_ENTRIES:
            raise ValueError('召回结果超过限制')
        ranked = backend.rerank(query, candidates) if candidates else []
        # endregion

        # region 记录本轮召回和重排证据，返回候选的公开字段
        recalled = [item.model_dump(exclude={'chunk_id'}) for item in candidates]
        selected = [item.model_dump(exclude={'chunk_id'}) for item in ranked]
        log_event('policy_retrieval', policy_event_id=event_id, mode=mode.value,
                  catalog_revision=snapshot.revision, recalled=recalled, ranked=selected)
        return PolicySearchResult(status=PolicySearchStatus.OK, event_id=event_id,
                                  candidates=[PolicyCandidate.model_construct(**item) for item in selected],
                                  catalog_revision=snapshot.revision)
        # endregion

    except Exception:

        # region 检索发生异常时返回失败状态和空候选
        return PolicySearchResult(status=PolicySearchStatus.FAILED, event_id=event_id,
                                  candidates=[], catalog_revision=None)
        # endregion

    # endregion
