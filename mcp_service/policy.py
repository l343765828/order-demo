"""退款政策来源解析和向量规范化。"""
import math
import re
from model.policy import PolicyEntry, policy_chunk_id
from model.status import PolicyEntryType, PolicySearchMode, PolicySearchStatus

DOCUMENT_ID = 'DEMO-REFUND-POLICY'
VERSION = '1.0'
TITLE = '演示退款政策与常见问题'
COLLECTION = 'order_demo_refund_policy'


# 当前发布入口只支持固定文档和版本，拒绝错误标注来源。
def validate_policy_source(source: str) -> None:
    if not (source.startswith(f'# {TITLE}\n')
            and re.search(rf'^文档编号：{re.escape(DOCUMENT_ID)}\s*$', source, re.MULTILINE)
            and re.search(rf'^版本：{re.escape(VERSION)}\s*$', source, re.MULTILINE)):
        raise ValueError('政策文档标头不匹配当前发布版本')


# 将文档条款和 FAQ 分块，保留标题与正文；标识由固定文档、版本和编号确定。
def parse_policy(source: str) -> list[PolicyEntry]:
    pattern = r'^## ((?:RF|FAQ)-\d{3})\s*—\s*(.+?)\s*$'
    matches = list(re.finditer(pattern, source, re.MULTILINE))
    entries = []
    for i, match in enumerate(matches):
        clause, heading = match.group(1, 2)
        body = source[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(source)].strip()
        text = f'{heading}\n{body}'
        if not body:
            raise ValueError('政策条目为空')
        entry = PolicyEntry(chunk_id=policy_chunk_id(DOCUMENT_ID, VERSION, clause),
                     document_id=DOCUMENT_ID, title=TITLE, version=VERSION, clause_id=clause,
                     entry_type=PolicyEntryType.POLICY if clause.startswith('RF-') else PolicyEntryType.FAQ, text=text)
        entries.append(entry)
    if len({e.clause_id for e in entries}) != len(entries):
        raise ValueError('政策条目编号重复')
    return entries


# 验证 1024 维有限非零向量并执行 L2 归一化。
def normalize_vector(values: list[float]) -> list[float]:
    if len(values) != 1024 or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in values):
        raise ValueError('无效向量')
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('无效向量模长')
    return [v / norm for v in values]

# 完成编码、带范围过滤的召回和重排；任一阶段失败均不返回未重排内容。
def search_policy(query, mode: PolicySearchMode, backend):
    from uuid import uuid4
    from model.policy import PolicyCandidate, PolicySearchResult
    from order_runtime import log_event
    event_id = str(uuid4())
    try:
        vector = normalize_vector(backend.embed(query))
        candidates = backend.recall(vector, mode)
        if len(candidates) > 8:
            raise ValueError('召回结果超过限制')
        ranked = backend.rerank(query, candidates) if candidates else []
        if len(ranked) > 3:
            raise ValueError('重排结果超过限制')
        recalled = [item.model_dump(exclude={'chunk_id'}) for item in candidates]
        selected = [item.model_dump(exclude={'chunk_id'}) for item in ranked]
        log_event('policy_retrieval', policy_event_id=event_id, mode=mode.value,
                  recalled=recalled, ranked=selected)
        return PolicySearchResult(status=PolicySearchStatus.OK, event_id=event_id,
                                  candidates=[PolicyCandidate.model_validate(item) for item in selected])
    except Exception:
        return PolicySearchResult(status=PolicySearchStatus.FAILED, event_id=event_id, candidates=[])
