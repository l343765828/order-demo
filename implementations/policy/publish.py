"""校验政策发布源并向专用集合发布当前条目。"""

from implementations.policy.source import FAQ_IDS, FORMAL_CLAUSE_IDS, SOURCE_PATH, parse_policy, validate_policy_source
from mcp_service.policy.backend import PolicyBackend


# 发布条目完整且版本匹配的退款政策，返回实际发布条数。
def publish_policy() -> int:
    # region 当前文档版本和条目集合完整时允许发布
    source = SOURCE_PATH.read_text(encoding='utf-8')
    validate_policy_source(source)
    entries = parse_policy(source)
    if {e.clause_id for e in entries} != (FORMAL_CLAUSE_IDS | FAQ_IDS):
        raise ValueError('政策源条目不完整')
    # endregion

    # region 发布已校验条目，释放后端连接并返回条数
    backend = PolicyBackend()
    try:
        backend.publish(entries)
    finally:
        backend.close()
    return len(entries)
    # endregion
