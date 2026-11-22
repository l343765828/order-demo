"""定义当前退款政策及其文档解析方式。"""

import re
from pathlib import Path

from model.policy.search import PolicyEntry, policy_chunk_id
from model.policy.status import PolicyEntryType

DOCUMENT_ID = 'DEMO-REFUND-POLICY'
VERSION = '1.0'
TITLE = '演示退款政策与常见问题'
SOURCE_PATH = Path(__file__).resolve().parents[2] / 'policy' / 'refund-policy.md'
FORMAL_CLAUSE_IDS = frozenset(f'RF-{number:03d}' for number in range(1, 15))
FAQ_IDS = frozenset(f'FAQ-{number:03d}' for number in range(1, 8))


# 当前发布入口只支持固定文档和版本，拒绝错误标注来源。
def validate_policy_source(source: str) -> None:
    # region 标题、文档编号或版本与发布目标不匹配时拒绝发布
    if not (source.startswith(f'# {TITLE}\n')
            and re.search(rf'^文档编号：{re.escape(DOCUMENT_ID)}\s*$', source, re.MULTILINE)
            and re.search(rf'^版本：{re.escape(VERSION)}\s*$', source, re.MULTILINE)):
        raise ValueError('政策文档标头不匹配当前发布版本')
    # endregion


# 将文档条款和 FAQ 分块，保留标题与正文；标识由固定文档、版本和编号确定。
def parse_policy(source: str) -> list[PolicyEntry]:
    # region 按条款或常见问题编号切分原文，拒绝空正文并生成稳定的条目标识
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
    # endregion

    # region 同一文档版本内出现重复条目编号时拒绝发布
    if len({e.clause_id for e in entries}) != len(entries):
        raise ValueError('政策条目编号重复')
    # endregion
    return entries
