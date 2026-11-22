"""MySQL 中的会话归属和原文记录。"""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID, uuid4

from model.refund.flow import ExchangeMessage


# 表示会话不存在、消费者归属不匹配或会话正被占用。
class SessionUnavailable(Exception):
    """会话不存在、归属不匹配或正在处理。"""


# 持有已验证的会话与数据库连接，提供原文读取和追加操作。
class Conversation:
    # 绑定当前会话编号与已获取的数据库连接。
    def __init__(self, connection, session_id: str):
        self.connection = connection
        self.session_id = session_id

    # 按消息序号读取当前会话的消费者和助手原文。
    def history(self) -> list[ExchangeMessage]:
        # region 按会话编号限定读取范围并按原始序号恢复消息
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT role, content FROM conversation_messages WHERE session_id=%s ORDER BY sequence",
                (self.session_id,),
            )
            return [ExchangeMessage(role=role, content=content) for role, content in cursor.fetchall()]
        # endregion

    # 为当前会话分配下一消息序号，写入原文并提交事务。
    def append(self, message: ExchangeMessage) -> None:
        # region 当前会话独占期间提交原文，保证序号和落库顺序
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO conversation_messages (session_id, sequence, role, content, created_at) "
                "SELECT %s, COALESCE(MAX(sequence), 0)+1, %s, %s, UTC_TIMESTAMP(6) "
                "FROM conversation_messages WHERE session_id=%s",
                (self.session_id, message.role, message.content, self.session_id),
            )
            cursor.execute(
                "UPDATE conversation_sessions SET updated_at=UTC_TIMESTAMP(6) WHERE session_id=%s",
                (self.session_id,),
            )
        self.connection.commit()
        # endregion


# 为指定消费者创建会话并返回新的会话编号。
def create_conversation(pool, consumer_id: str) -> str:
    # region 生成独立会话编号，绑定消费者并提交后供本轮使用
    session_id = str(uuid4())
    connection = pool.connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO conversation_sessions (session_id, consumer_id, created_at, updated_at) "
                "VALUES (%s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                (session_id, consumer_id),
            )
        connection.commit()
    finally:
        connection.close()
    return session_id
    # endregion


# 核验会话归属并取得会话锁，在上下文退出时释放连接与锁。
@contextmanager
def open_conversation(pool, session_id: str, consumer_id: str) -> Iterator[Conversation]:
    # region 验证会话编号并取得独占连接，再锁定会话和校验消费者归属
    try:
        session_id = str(UUID(session_id))
    except ValueError:
        raise SessionUnavailable from None
    connection = pool.connection(shareable=False)
    lock = f"order_demo:conversation:{session_id}"
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (lock,))
            locked = cursor.fetchone()[0] == 1
            if not locked:
                raise SessionUnavailable
            cursor.execute("SELECT consumer_id FROM conversation_sessions WHERE session_id=%s", (session_id,))
            row = cursor.fetchone()
            if row is None or row[0] != consumer_id:
                raise SessionUnavailable
        yield Conversation(connection, session_id)
    # endregion

    # region 会话使用结束或中途失败时回滚未提交事务并释放已持有的锁
    finally:
        try:
            connection.rollback()
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", (lock,))
        finally:
            connection.close()
    # endregion
