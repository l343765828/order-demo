"""将权威 Markdown 原件切分为正式条款和独立 FAQ。"""

import re
from hashlib import sha256
from pathlib import Path

from config.demo import DemoPolicy
from model.policy.catalog import PolicyDocument, PolicyVersion
from model.policy.search import PolicyEntry, policy_chunk_id
from model.policy.status import PolicyEntryType

SOURCE_PATH = Path(__file__).resolve().parents[2] / 'policy' / 'refund-policy.md'
_ENTRY_HEADING = re.compile(r'^## ((?:RF|FAQ)-[0-9]{3})[ \t]*—[ \t]*(\S[^\n]*?)[ \t]*$', re.MULTILINE)
_ENTRY_PREFIX = re.compile(r'^##\s+(?:RF|FAQ)-', re.MULTILINE)


# 从权威原件读取标题、文档编号和条目，核对维护者指定的版本。
def read_policy_document(source: str, *, version: str) -> PolicyDocument:
    # region 校验文档标题、唯一文档编号和显式版本
    header = _ENTRY_PREFIX.split(source, maxsplit=1)[0]
    title = re.match(r'^# (\S[^\n]*)', header)
    document_ids = re.findall(r'^文档编号：[ \t]*(\S+)[ \t]*$', header, re.MULTILINE)
    versions = re.findall(r'^版本：[ \t]*(\S+)[ \t]*$', header, re.MULTILINE)
    if (
        # 文档未提供可供引用的标题。
        title is None
        # 文档没有唯一身份。
        or len(document_ids) != 1
        # 权威原件中的版本缺失、重复或与发布命令不一致。
        or versions != [version]
    ):
        raise ValueError('政策文档标头不匹配发布目标')
    target = PolicyVersion(document_id=document_ids[0], version=version)
    # endregion

    # region 解析完整条目并保存原件摘要以便核对重发
    entries = parse_policy(source, target=target, title=title[1].strip())
    return PolicyDocument(target=target, source_digest=sha256(source.encode('utf-8')).hexdigest(), entries=entries)
    # endregion


# 将文档分为带独立正文的条款和 FAQ；默认身份供演示源的既有验证脚本使用。
def parse_policy(source: str, *, target: PolicyVersion | None = None,
                 title: str = DemoPolicy.BASELINE_TITLE) -> list[PolicyEntry]:
    # region 确认每个条目标题均可识别
    if target is None:
        target = PolicyVersion(document_id=DemoPolicy.DOCUMENT_ID, version=DemoPolicy.BASELINE_VERSION)
    matches = list(_ENTRY_HEADING.finditer(source))
    if len(matches) != len(_ENTRY_PREFIX.findall(source)):
        raise ValueError('政策条目标题格式无效')
    # endregion

    # region 保留各条条款及 FAQ 的独立正文，按完整条款身份生成主键
    entries = []
    for i, match in enumerate(matches):
        clause, heading = match.group(1, 2)
        body = source[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(source)].strip()
        text = f'{heading}\n{body}'
        if not body:
            raise ValueError('政策条目为空')
        entry = PolicyEntry(chunk_id=policy_chunk_id(target.document_id, target.version, clause),
                     document_id=target.document_id, title=title, version=target.version, clause_id=clause,
                     entry_type=PolicyEntryType.POLICY if clause.startswith('RF-') else PolicyEntryType.FAQ, text=text)
        entries.append(entry)
    # endregion

    # region 拒绝重复条目编号并返回已解析条目
    if len({e.clause_id for e in entries}) != len(entries):
        raise ValueError('政策条目编号重复')
    return entries
    # endregion
