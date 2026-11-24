"""在持有版本命名锁的连接上用短事务管理政策发布目录。"""

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Final

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import ResourceClosedError

from config import MySQLConfig
from config.limits import ConnectionLimits, PolicyCatalogTables, PolicyLimits
from interfaces.policy.catalog import PolicyCatalogReader
from model.policy.catalog import PolicyPublicationApproval, PolicyRelease, PolicySnapshot, PolicyVersion, PolicyWriteState
from repositories import policy_catalog_repository
from utils.mysql import connection_config, mysql_connection
from utils.mysql_lock import mysql_named_lock

_SCHEMA: Final = Path(__file__).resolve().parents[2] / 'model' / 'policy' / 'schema.sql'


# 标识目标文档在本次发布验证期间已被其他版本切换，或同一版本正被占用。
class PublicationConflict(ValueError):
    pass


# 独立初始化政策目录表和唯一目录头，重复执行时保留已有发布历史。
def initialize_policy_catalog(database: str, mysql: MySQLConfig) -> None:

    # region 读取目录初始化脚本并通过独立管理连接执行
    schema = _SCHEMA.read_text(encoding='utf-8').format(head=PolicyCatalogTables.HEAD,
                                                       releases=PolicyCatalogTables.RELEASES)
    with mysql_connection(database, **connection_config(mysql)) as connection, connection.cursor() as cursor:
        for statement in schema.split(';'):
            if statement.strip():

                # region 执行脚本中的有效语句
                cursor.execute(statement)
                # endregion

    # endregion


# 使用应用入口配置的目录 Engine 提供快照及持锁发布访问。
class MySQLPolicyCatalog(PolicyCatalogReader):
    # 保存目录 Engine，连接池由入口创建和释放。
    def __init__(self, engine: Engine) -> None:

        # region 绑定实际存储目录的 Engine
        self._engine = engine
        # endregion

    # 由首次激活修订重建指定时点每份文档的版本；空目录和未知修订均报错。
    def snapshot(self, revision: int | None = None) -> PolicySnapshot:

        # region 校验指定修订并在单次读取中取得各文档的生效版本
        if (
            # 这次读取明确指定了历史修订。
            revision is not None
            # 指定值超出目录修订的有效整数范围。
            and not 1 <= revision <= PolicyLimits.CATALOG_REVISION_MAX
        ):

            # region 请求无效修订时拒绝读取
            raise ValueError('政策目录修订不可用')
            # endregion

        with self._engine.connect() as connection:
            rows = policy_catalog_repository.get_snapshot(connection, revision=revision)
        # endregion

        # region 将读取结果组装为本轮的政策版本快照
        if not rows:

            # region 空目录或未知修订时沿用政策读取故障
            raise ValueError('政策目录修订不可用')
            # endregion

        return PolicySnapshot(revision=rows[0].revision, versions=[
            PolicyVersion.model_construct(document_id=row.document_id, version=row.version) for row in rows
        ])
        # endregion

    # 在同一物理连接上持有原版本命名锁，跨短事务和 Milvus 操作。
    @contextmanager
    def lock_version(self, target: PolicyVersion) -> Iterator['PolicyPublicationSession']:

        # region 按目录库和文档版本生成原发布入口使用的锁名
        identity = f'{PolicyCatalogTables.VERSION_LOCK_NAMESPACE}:{self._engine.url.database}:{target.document_id}:{target.version}'
        key = sha256(identity.encode()).hexdigest()
        # endregion

        # region 独占目标版本并交付发布会话，退出时释放锁和连接
        with self._engine.connect() as connection, mysql_named_lock(
            connection, name=key, wait_seconds=ConnectionLimits.MYSQL_POLICY_LOCK_WAIT_SECONDS,
        ) as acquired:
            if not acquired:

                # region 同版本另有发布在执行时拒绝开始
                raise PublicationConflict('此政策版本已有发布操作在执行')
                # endregion

            yield PolicyPublicationSession(connection, target)
        # endregion


# 在持有版本锁的同一物理连接上读取并分段提交发布记录。
class PolicyPublicationSession:
    # 绑定已持锁的连接以及本次发布的文档版本。
    def __init__(self, connection: Connection, target: PolicyVersion) -> None:

        # region 保存连接和本次发布目标
        self.connection = connection
        self.target = target
        # endregion

    # 在原持锁连接上开启短事务，连接失效时终止本次发布。
    @contextmanager
    def _transaction(self) -> Iterator[Connection]:

        # region 确认命名锁仍属于当前连接后开启短事务
        if self.connection.invalidated:

            # region 原连接断开时拒绝通过自动重连继续发布
            raise ResourceClosedError('政策发布持锁连接已断开')
            # endregion

        with self.connection.begin():
            yield self.connection
        # endregion

    # 读取当前激活的同文档版本，首次发布返回空值。
    def active_version(self) -> str | None:

        # region 读取同文档生效版本作为切换时的并发比较基准
        with self._transaction() as connection:
            version = policy_catalog_repository.get_active_version(connection, document_id=self.target.document_id)
        return version.version if version is not None else None
        # endregion

    # 核对版本是否曾生效或前一次 Milvus 写入是否还待确认。
    def release(self) -> PolicyRelease | None:

        # region 读取已提交的版本发布历史和资格确认
        with self._transaction() as connection:
            return policy_catalog_repository.get_release(connection, target=self.target)
        # endregion

    # 保存发布者和原件确认，在 Milvus 写入前提交待确认状态。
    def record_attempt(self, approval: PolicyPublicationApproval, *, write_state: PolicyWriteState) -> None:

        # region 独立提交发布尝试及其写入状态
        with self._transaction() as connection:
            policy_catalog_repository.record_attempt(
                connection, target=self.target, approval=approval, write_state=write_state,
            )
        # endregion

    # 在清理和写入均结束后，提交写入完成状态供后续校验或重试。
    def finish_write(self) -> None:

        # region 独立提交 Milvus 写入已结束的记录
        with self._transaction() as connection:
            policy_catalog_repository.finish_write(connection, target=self.target)
        # endregion

    # 在校验通过后串行分配修订，原子登记版本首次生效及目录头推进。
    def activate(self, expected_version: str | None, *, adoption: PolicyPublicationApproval | None = None) -> int:

        # region 锁住目录头并核对验证期间该文档的生效版本
        with self._transaction() as connection:
            head = policy_catalog_repository.lock_head(connection)
            active = policy_catalog_repository.get_active_version(connection, document_id=self.target.document_id)
            if (active.version if active is not None else None) != expected_version:

                # region 同文档被并发切换时回滚并报告冲突
                raise PublicationConflict('该文档的生效版本已被并发发布改变')
                # endregion

            # region 登记可直接沿用的演示基线并原子切换目录
            revision = head.revision + 1
            if adoption is not None:

                # region 已验证的历史基线随切换一同登记原件和资格确认
                policy_catalog_repository.record_attempt(
                    connection, target=self.target, approval=adoption, write_state=PolicyWriteState.NOT_STARTED,
                )
                # endregion

            if not policy_catalog_repository.activate_release(connection, target=self.target, revision=revision):

                # region 发布记录未满足生效条件时回滚切换
                raise ValueError('发布记录未达到切换条件')
                # endregion

            policy_catalog_repository.advance_head(connection, revision=revision)
            # endregion

        return revision
        # endregion
