"""退款政策向量规范化、召回与重排。"""
import math
from config.limits import PolicyLimits
from model.policy.status import PolicySearchMode, PolicySearchStatus
from utils.events import log_event

COLLECTION = 'order_demo_refund_policy'


# 验证 1024 维有限非零向量并执行 L2 归一化。
def normalize_vector(values: list[float]) -> list[float]:
    # region 仅对维度正确且模长有限非零的向量做归一化
    if len(values) != PolicyLimits.VECTOR_DIMENSIONS or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        raise ValueError('无效向量')
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('无效向量模长')
    return [v / norm for v in values]
    # endregion

# 完成编码、带范围过滤的召回和重排；任一阶段失败均不返回未重排内容。
def search_policy(query, mode: PolicySearchMode, backend):
    from uuid import uuid4
    from model.policy.search import PolicyCandidate, PolicySearchResult
    event_id = str(uuid4())
    try:
        # region 按本次检索范围召回候选，有候选时完成重排并核对数量上限
        vector = normalize_vector(backend.embed(query))
        candidates = backend.recall(vector, mode)
        if len(candidates) > PolicyLimits.MAX_RECALLED_ENTRIES:
            raise ValueError('召回结果超过限制')
        ranked = backend.rerank(query, candidates) if candidates else []
        if len(ranked) > PolicyLimits.MAX_RANKED_ENTRIES:
            raise ValueError('重排结果超过限制')
        # endregion

        # region 记录本轮召回和重排证据，返回通过契约校验的候选
        recalled = [item.model_dump(exclude={'chunk_id'}) for item in candidates]
        selected = [item.model_dump(exclude={'chunk_id'}) for item in ranked]
        log_event('policy_retrieval', policy_event_id=event_id, mode=mode.value,
                  recalled=recalled, ranked=selected)
        return PolicySearchResult(status=PolicySearchStatus.OK, event_id=event_id,
                                  candidates=[PolicyCandidate.model_validate(item) for item in selected])
        # endregion
    # region 检索任一环节失败时返回失败状态和空候选
    except Exception:
        return PolicySearchResult(status=PolicySearchStatus.FAILED, event_id=event_id, candidates=[])
    # endregion
