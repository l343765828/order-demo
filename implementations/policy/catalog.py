"""从 MySQL 读取政策目录并原子提交版本切换。"""

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from config import MySQLConfig
from config.limits import PolicyCatalogTables, PolicyLimits
from interfaces.policy.catalog import PolicyCatalogReader
from model import connection_config
from model.policy.catalog import (
    PolicyCatalogHead, PolicyPublicationApproval, PolicyRelease, PolicySnapshot, PolicyVersion, PolicyWriteState,
    QualificationConfirmation,
)
from utils.mysql import mysql_connection

_SCHEMA = Path(__file__).resolve().parents[2] / 'model' / 'policy' / 'schema.sql'


# 标识目标文档在本次发布验证期间已被其他版本切换。
class PublicationConflict(ValueError):
    pass


# 提供独立连接上的 MySQL 发布目录读写。
class MySQLPolicyCatalog(PolicyCatalogReader):
    # 指定保存目录和发布历史的数据库。
    def __init__(self, database: str, mysql: MySQLConfig) -> None:
        # region 保存目录所在数据库
        self.database = database
        self.options = connection_config(mysql)
        # endregion

    # 显式补齐目录表，保留已有发布历史。
    def initialize(self) -> None:
        # region 执行可重复的政策目录建表和初始化脚本
        schema = _SCHEMA.read_text(encoding='utf-8').format(head=PolicyCatalogTables.HEAD,
                                                           releases=PolicyCatalogTables.RELEASES)
        with mysql_connection(self.database, **self.options) as connection, connection.cursor() as cursor:
            for statement in schema.split(';'):
                if statement.strip():
                    cursor.execute(statement)
        # endregion

    # 由首次激活修订重建指定时点每份文档的版本；空目录和未知修订均报错。
    def snapshot(self, revision: int | None = None) -> PolicySnapshot:
        # region 在同一次查询中解析修订并选取各文档当时最新生效的版本
        if (
            # 这次读取明确指定了历史修订。
            revision is not None
            # 指定值超出目录修订的有效整数范围。
            and not 1 <= revision <= PolicyLimits.CATALOG_REVISION_MAX
        ):
            raise ValueError('政策目录修订不可用')
        sql = f'''
            WITH requested AS (
                SELECT COALESCE(CAST(%s AS UNSIGNED), revision) AS revision
                FROM {PolicyCatalogTables.HEAD} WHERE singleton = TRUE
            ), versions AS (
                SELECT document_id, version, activated_revision,
                       ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY activated_revision DESC) AS position
                FROM {PolicyCatalogTables.RELEASES} JOIN requested ON activated_revision <= requested.revision
            )
            SELECT document_id, version, requested.revision FROM versions CROSS JOIN requested
            WHERE position = 1 AND EXISTS (
                SELECT 1 FROM {PolicyCatalogTables.RELEASES} WHERE activated_revision = requested.revision
            ) ORDER BY document_id
        '''
        with mysql_connection(self.database, **self.options) as connection, connection.cursor() as cursor:
            cursor.execute(sql, (revision,))
            rows = cursor.fetchall()
        # endregion

        # region 缺少目录或修订时返回故障，其余情况构造不可变历史引用
        if not rows:
            raise ValueError('政策目录修订不可用')
        return PolicySnapshot(revision=rows[0][2], versions=[
            PolicyVersion(document_id=document_id, version=version) for document_id, version, _ in rows
        ])
        # endregion

    # 用同一 MySQL 会话锁住同一文档版本，连接结束时自动释放锁。
    @contextmanager
    def lock_version(self, target: PolicyVersion) -> Iterator['PolicyPublicationSession']:
        # region 获取版本互斥锁，在释放连接前串行处理该版本
        key = sha256(f'policy:{self.database}:{target.document_id}:{target.version}'.encode()).hexdigest()
        with mysql_connection(self.database, **self.options) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s, 0)', (key,))
            if cursor.fetchone()[0] != 1:
                raise PublicationConflict('此政策版本已有发布操作在执行')
            yield PolicyPublicationSession(connection, target)
        # endregion


# 在持有版本互斥锁的同一数据库会话上跟踪发布与切换。
class PolicyPublicationSession:
    # 绑定持锁连接及本次发布的文档版本。
    def __init__(self, connection: Connection, target: PolicyVersion) -> None:
        # region 保存本次发布的连接和版本
        self.connection = connection
        self.target = target
        # endregion

    # 读取当前激活的同文档版本，首次发布返回空值。
    def active_version(self) -> str | None:
        # region 取该文档最新生效的版本作为并发比较基准
        with self.connection.cursor() as cursor:
            cursor.execute(f'''SELECT version FROM {PolicyCatalogTables.RELEASES}
                              WHERE document_id = %s AND activated_revision IS NOT NULL
                              ORDER BY activated_revision DESC LIMIT 1''', (self.target.document_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        # endregion

    # 核对版本是否曾生效或前一次 Milvus 写入是否还待确认。
    def release(self) -> PolicyRelease | None:
        # region 读取已提交的版本发布历史和资格确认
        with self.connection.cursor(DictCursor) as cursor:
            cursor.execute(f'''SELECT document_id, version, source_digest, publisher, qualification,
                                      write_state, activated_revision, updated_at
                               FROM {PolicyCatalogTables.RELEASES} WHERE document_id = %s AND version = %s''',
                           (self.target.document_id, self.target.version))
            row = cursor.fetchone()
        return None if row is None else PolicyRelease.model_validate(row)
        # endregion

    # 记录发布者确认和原件；写入前将未完成标记先提交到 MySQL。
    def record_attempt(self, approval: PolicyPublicationApproval, *, write_state: PolicyWriteState) -> None:
        # region 保存可追溯的发布意图和 Milvus 写入状态
        with self.connection.cursor() as cursor:
            cursor.execute(f'''INSERT INTO {PolicyCatalogTables.RELEASES}
                              (document_id, version, source_digest, publisher, qualification, write_state, updated_at)
                              VALUES (%s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(6)) AS attempt
                              ON DUPLICATE KEY UPDATE source_digest = attempt.source_digest,
                                  publisher = attempt.publisher, qualification = attempt.qualification,
                                  write_state = attempt.write_state, updated_at = attempt.updated_at''',
                           (self.target.document_id, self.target.version, approval.source_digest, approval.publisher,
                            approval.qualification.value, write_state.value))
        # endregion

    # 在本次 Milvus 删除和写入均已确认结束后，允许后续校验失败时安全重试。
    def finish_write(self) -> None:
        # region 持久记录 Milvus 写入已结束
        with self.connection.cursor() as cursor:
            cursor.execute(f'''UPDATE {PolicyCatalogTables.RELEASES} SET write_state = %s, updated_at = UTC_TIMESTAMP(6)
                              WHERE document_id = %s AND version = %s''',
                           (PolicyWriteState.FINISHED.value, self.target.document_id, self.target.version))
        # endregion

    # 以目录行锁串行化修订分配，比较同文档的生效版本并原子切换。
    def activate(self, expected_version: str | None, *, adoption: PolicyPublicationApproval | None = None) -> int:
        # region 获取目录行锁并核对验证期间同文档的生效版本
        self.connection.begin()
        with self.connection.cursor(DictCursor) as cursor:
            cursor.execute(f'SELECT singleton, revision FROM {PolicyCatalogTables.HEAD} WHERE singleton = TRUE FOR UPDATE')
            head = PolicyCatalogHead.model_validate(cursor.fetchone())
            if self.active_version() != expected_version:
                raise PublicationConflict('该文档的生效版本已被并发发布改变')
            # endregion

            # region 保存版本首次生效修订并推进目录，退出事务时同时生效
            revision = head.revision + 1
            if adoption is not None:
                self.record_attempt(adoption, write_state=PolicyWriteState.NOT_STARTED)
            cursor.execute(f'''UPDATE {PolicyCatalogTables.RELEASES} SET activated_revision = %s, updated_at = UTC_TIMESTAMP(6)
                              WHERE document_id = %s AND version = %s AND activated_revision IS NULL
                                AND write_state <> %s AND qualification = %s''',
                           (revision, self.target.document_id, self.target.version,
                            PolicyWriteState.PENDING.value, QualificationConfirmation.UNCHANGED.value))
            if cursor.rowcount != 1:
                raise ValueError('发布记录未达到切换条件')
            cursor.execute(f'UPDATE {PolicyCatalogTables.HEAD} SET revision = %s WHERE singleton = TRUE', (revision,))
            self.connection.commit()
            return revision
            # endregion
