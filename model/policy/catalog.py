"""政策发布源、目录快照和发布历史契约。"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from config.limits import PolicyLimits
from model.base import StrictModel
from model.policy.search import PolicyCatalogRevision, PolicyEntry

_IDENTIFIER_PATTERN = r'^[A-Za-z0-9][A-Za-z0-9._-]*$'
PublisherId = Annotated[str, Field(min_length=1, max_length=PolicyLimits.PUBLISHER_MAX_CHARS, pattern=r'\S')]
SourceDigest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]


# 发布者对本版本是否影响代码计算的退款资格作出的确认。
class QualificationConfirmation(StrEnum):
    # 文档版本沿用当前资格规则，可以独立切换。
    UNCHANGED = 'unchanged'
    # 文档版本改变资格，必须等待同版本结构化规则随之生效。
    CHANGED = 'changed'


# 记录维护入口是否已经对单个政策版本发起 Milvus 操作。
class PolicyWriteState(StrEnum):
    # 当前发布目录从未对该文档版本启动索引写入。
    NOT_STARTED = 'not_started'
    # 已授权修改索引，上一进程或后端写入是否结束待确认。
    PENDING = 'pending'
    # 前次修改索引的发布进程及写入均已结束，可以在未生效时重试。
    FINISHED = 'finished'


# 标识一份政策文档的具体版本，目录和向量存储共用此身份。
class PolicyVersion(StrictModel):
    # 文档的稳定标识，在不同文档间区分同编号条款。
    document_id: Annotated[str, Field(max_length=PolicyLimits.FIELD_BYTES['document_id'], pattern=_IDENTIFIER_PATTERN)]
    # 文档内部的显式版本，独立于其他文档版本编号。
    version: Annotated[str, Field(max_length=PolicyLimits.FIELD_BYTES['version'], pattern=_IDENTIFIER_PATTERN)]


# 一次退款调查读取政策时采用的不可变生效版本组合。
class PolicySnapshot(StrictModel):
    # 目录为这组生效版本分配的正整数标识。
    revision: PolicyCatalogRevision
    # 该修订所包含的每份文档的生效版本。
    versions: list[PolicyVersion] = Field(min_length=1)


# 已从权威源解析并校验的完整文档。
class PolicyDocument(StrictModel):
    # 从源文档和命令的显式版本确认的发布身份。
    target: PolicyVersion
    # 当前权威原件的 SHA-256，用于核对已生效版本的重复发布。
    source_digest: SourceDigest
    # 独立的 FAQ 和正式条款，包含原文及引用字段。
    entries: list[PolicyEntry] = Field(min_length=1)


# 发布者提交的原件摘要及资格确认。
class PolicyPublicationApproval(StrictModel):
    # 当前发布尝试采用的权威原件摘要。
    source_digest: SourceDigest
    # 为最近一次发布确认资格行为的发布者。
    publisher: PublisherId
    # 记录发布者的资格确认，待规则配套的版本不可切换。
    qualification: Annotated[QualificationConfirmation, BeforeValidator(QualificationConfirmation)]


# MySQL 保存的单份文档版本发布记录。
class PolicyRelease(PolicyVersion, PolicyPublicationApproval):
    # 索引写入进度，历史迁移和同版本恢复使用此字段确认能否修改既有条目。
    write_state: Annotated[PolicyWriteState, BeforeValidator(PolicyWriteState)]
    # 首次生效的目录修订，空值表示此版本从未生效。
    activated_revision: PolicyCatalogRevision | None
    # 最近一次提交发布或确认写入进度的时间，数据库使用 UTC。
    updated_at: datetime


# MySQL 保存目前对新调查开放的目录修订。
class PolicyCatalogHead(StrictModel):
    # 唯一目录行的主键，防止同时存在两个当前修订。
    singleton: Literal[1]
    # 最新生效的目录修订，0 表示尚无文档生效。
    revision: Annotated[int, Field(ge=0)]


# 发布命令可报告的业务结果。
class PublicationStatus(StrEnum):
    # 当前调用已确认新修订生效。
    ACTIVATED = 'activated'
    # 原件一致，发布历史已经记录该版本首次生效的修订。
    ALREADY_ACTIVATED = 'already_activated'


# 发布命令返回的版本及其首次生效修订。
class PolicyPublicationResult(StrictModel):
    # 表示本次切换成功或核对到此前已经切换成功。
    status: PublicationStatus
    # 本次发布的文档及版本。
    target: PolicyVersion
    # 该版本首次切换生效产生的目录修订。
    catalog_revision: PolicyCatalogRevision
    # 从本次权威原件读取的条目数。
    entry_count: Annotated[int, Field(gt=0)]
