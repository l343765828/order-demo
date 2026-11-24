"""MySQL 中的会话归属和原文记录。"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import ResourceClosedError

from config.limits import ConnectionLimits, ConversationStorage
from model.refund.flow import ExchangeMessage
from repositories import conversation_repository
from utils.mysql_lock import mysql_named_lock


# 表示会话不存在、消费者归属不匹配或会话正被占用。
class SessionUnavailable(Exception):
    """会话不存在、归属不匹配或正在处理。"""


# 持有已验证的会话与数据库连接，提供原文读取和追加操作。
class Conversation:
    # 绑定当前会话编号与已获取的数据库连接。
    def __init__(self, connection: Connection, session_id: str):

        # region 绑定当前会话编号与已获取的数据库连接
        self._connection = connection
        self.session_id = session_id
        # endregion

    # 在仍持有会话锁的连接上开启短事务，连接丢失时终止本轮访问。
    @contextmanager
    def _transaction(self) -> Iterator[Connection]:

        # region 确认持锁连接仍可用并开启短事务
        if self._connection.invalidated:

            # region 原连接失效时拒绝在自动重连的新连接上操作会话
            raise ResourceClosedError("会话持锁连接已断开")
            # endregion

        with self._connection.begin():
            yield self._connection
        # endregion

    # 按消息序号读取当前会话的消费者和助手原文。
    def history(self) -> list[ExchangeMessage]:

        # region 按会话编号限定读取范围并按原始序号恢复消息
        with self._transaction() as connection:
            rows = conversation_repository.get_history(connection, session_id=self.session_id)
        return [ExchangeMessage.model_construct(role=row.role, content=row.content) for row in rows]
        # endregion

    # 为当前会话分配下一消息序号，写入原文并提交事务。
    def append(self, message: ExchangeMessage) -> int:

        # region 当前会话独占期间提交原文，保证序号和落库顺序
        with self._transaction() as connection:
            sequence = conversation_repository.get_last_sequence(connection, session_id=self.session_id) + 1
            conversation_repository.insert_message(connection, session_id=self.session_id, sequence=sequence,
                                                   message=message)
            conversation_repository.touch_session(connection, session_id=self.session_id)
        return sequence
        # endregion


# 创建退款会话，并在每次访问期间独占会话、核对归属。
class MySQLConversationService:
    # 接收由应用入口管理生命周期的业务 Engine。
    def __init__(self, engine: Engine):

        # region 保存用于处理退款会话的 Engine
        self._engine = engine
        # endregion

    # 为指定消费者创建会话，提交后返回会话编号。
    def create(self, consumer_id: str) -> str:

        # region 生成会话编号并在独立事务中保存归属
        session_id = str(uuid4())
        with self._engine.begin() as connection:
            conversation_repository.create_session(connection, session_id=session_id, consumer_id=consumer_id)
        return session_id
        # endregion

    # 在同一物理连接持有锁期间交付已核验的会话，退出时释放连接和锁。
    @contextmanager
    def open(self, session_id: str, consumer_id: str) -> Iterator[Conversation]:

        # region 将会话编号规范化并据此生成原有命名锁
        try:
            session_id = str(UUID(session_id))
        except ValueError:

            # region 会话编号不是有效的 UUID 时拒绝访问
            raise SessionUnavailable from None
            # endregion

        lock_name = f"{ConversationStorage.LOCK_PREFIX}{session_id}"
        # endregion

        # region 独占会话并核验消费者，收束查询事务后交付会话
        with self._engine.connect() as connection, mysql_named_lock(
            connection, name=lock_name, wait_seconds=ConnectionLimits.MYSQL_CONVERSATION_LOCK_WAIT_SECONDS,
        ) as acquired:
            if not acquired:

                # region 未取得锁时沿用会话不可用结果
                raise SessionUnavailable
                # endregion

            with connection.begin():
                stored = conversation_repository.get_session(connection, session_id=session_id)
            if (
                # 指定编号对应的会话尚未建立。
                stored is None
                # 会话不属于当前可信消费者。
                or stored.consumer_id != consumer_id
            ):

                # region 会话未知或归属不匹配时拒绝交付并释放锁
                raise SessionUnavailable
                # endregion

            yield Conversation(connection, session_id)
        # endregion
