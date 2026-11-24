"""在政策目录 Service 提供的连接上查询快照及写入发布记录。"""

from enum import StrEnum

from sqlalchemy import Connection

from config.limits import PolicyCatalogTables
from model.policy.catalog import (
    PolicyCatalogHead, PolicyPublicationApproval, PolicyRelease, PolicySnapshotRow, PolicyVersion,
    PolicyWriteState, QualificationConfirmation,
)
from utils.sql_loader import load_sql


# 固定的政策目录语句路径由 Repository 选择。
class _CatalogSQL(StrEnum):
    # 同时解析当前或指定修订及当时生效的各份文档。
    SNAPSHOT = "policy/snapshot.sql"
    # 读取指定文档当前生效版本。
    ACTIVE_VERSION = "policy/active_version.sql"
    # 读取一个版本的发布记录。
    GET_RELEASE = "policy/get_release.sql"
    # 写入发布者确认和写入状态。
    RECORD_ATTEMPT = "policy/record_attempt.sql"
    # 记录 Milvus 写入已经结束。
    FINISH_WRITE = "policy/finish_write.sql"
    # 锁定目录头，串行分配修订。
    LOCK_HEAD = "policy/lock_head.sql"
    # 登记版本首次生效的修订。
    ACTIVATE_RELEASE = "policy/activate_release.sql"
    # 推进目录头指向的新修订。
    ADVANCE_HEAD = "policy/advance_head.sql"


# 查询当前或指定目录修订，返回已校验的生效文档版本行。
def get_snapshot(connection: Connection, *, revision: int | None) -> list[PolicySnapshotRow]:

    # region 读取同一目录修订的各份文档并校验数据库结果
    rows = connection.execute(
        load_sql(_CatalogSQL.SNAPSHOT.value, head=PolicyCatalogTables.HEAD, releases=PolicyCatalogTables.RELEASES),
        {"revision": revision},
    ).mappings()
    return [PolicySnapshotRow.model_validate(dict(row)) for row in rows]
    # endregion


# 读取指定文档当前生效版本，首次发布时返回 None。
def get_active_version(connection: Connection, *, document_id: str) -> PolicyVersion | None:

    # region 查询该文档最近生效的版本并校验数据库结果
    row = connection.execute(
        load_sql(_CatalogSQL.ACTIVE_VERSION.value, releases=PolicyCatalogTables.RELEASES),
        {"document_id": document_id},
    ).mappings().one_or_none()
    return PolicyVersion.model_validate(dict(row)) if row is not None else None
    # endregion


# 查询目标版本的发布记录，首次尝试前返回 None。
def get_release(connection: Connection, *, target: PolicyVersion) -> PolicyRelease | None:

    # region 按文档及版本读取并校验发布记录
    row = connection.execute(
        load_sql(_CatalogSQL.GET_RELEASE.value, releases=PolicyCatalogTables.RELEASES), target.model_dump(),
    ).mappings().one_or_none()
    return PolicyRelease.model_validate(dict(row)) if row is not None else None
    # endregion


# 保存版本本次采用的原件、资格确认和写入进度。
def record_attempt(
    connection: Connection, *, target: PolicyVersion, approval: PolicyPublicationApproval, write_state: PolicyWriteState,
) -> None:

    # region 插入发布记录或覆盖同版本的尝试字段
    connection.execute(
        load_sql(_CatalogSQL.RECORD_ATTEMPT.value, releases=PolicyCatalogTables.RELEASES),
        {**target.model_dump(), **approval.model_dump(mode="json"), "write_state": write_state.value},
    )
    # endregion


# 保存目标版本的 Milvus 写入完成状态。
def finish_write(connection: Connection, *, target: PolicyVersion) -> None:

    # region 将版本的写入状态更新为已结束
    connection.execute(
        load_sql(_CatalogSQL.FINISH_WRITE.value, releases=PolicyCatalogTables.RELEASES),
        {**target.model_dump(), "write_state": PolicyWriteState.FINISHED.value},
    )
    # endregion


# 锁住唯一目录头，返回当前修订供 Service 分配新修订。
def lock_head(connection: Connection) -> PolicyCatalogHead:

    # region 获取目录头的行锁并校验当前修订
    row = connection.execute(load_sql(_CatalogSQL.LOCK_HEAD.value, head=PolicyCatalogTables.HEAD)).mappings().one()
    return PolicyCatalogHead.model_validate(dict(row))
    # endregion


# 为符合条件的版本登记首次生效修订，返回是否更新了一条记录。
def activate_release(connection: Connection, *, target: PolicyVersion, revision: int) -> bool:

    # region 只更新从未生效、写入无待确认且资格确认允许切换的版本
    result = connection.execute(
        load_sql(_CatalogSQL.ACTIVATE_RELEASE.value, releases=PolicyCatalogTables.RELEASES),
        {**target.model_dump(), "revision": revision, "pending_state": PolicyWriteState.PENDING.value,
         "qualification": QualificationConfirmation.UNCHANGED.value},
    )
    return result.rowcount == 1
    # endregion


# 将目录头推进到 Service 分配的新修订。
def advance_head(connection: Connection, *, revision: int) -> None:

    # region 更新已在同一事务锁定的目录头
    connection.execute(
        load_sql(_CatalogSQL.ADVANCE_HEAD.value, head=PolicyCatalogTables.HEAD), {"revision": revision},
    )
    # endregion
