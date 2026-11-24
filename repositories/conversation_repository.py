"""在会话 Service 提供的连接上读写会话与原文。"""

from typing import Final

from sqlalchemy import Connection

from config.limits import RefundTables
from model.refund.conversation import ConversationMessageRecord, ConversationSequenceRow, ConversationSessionRecord
from model.refund.flow import ExchangeMessage
from utils.sql_loader import load_sql

_CREATE_SESSION_SQL: Final = "conversations/create_session.sql"
_GET_SESSION_SQL: Final = "conversations/get_session.sql"
_GET_HISTORY_SQL: Final = "conversations/get_history.sql"
_GET_LAST_SEQUENCE_SQL: Final = "conversations/get_last_sequence.sql"
_INSERT_MESSAGE_SQL: Final = "conversations/insert_message.sql"
_TOUCH_SESSION_SQL: Final = "conversations/touch_session.sql"


# 用指定的会话编号和消费者编号创建会话记录。
def create_session(connection: Connection, *, session_id: str, consumer_id: str) -> None:

    # region 绑定会话及消费者并写入会话记录
    connection.execute(load_sql(_CREATE_SESSION_SQL, sessions=RefundTables.SESSIONS),
                       {"session_id": session_id, "consumer_id": consumer_id})
    # endregion


# 读取并校验指定会话，数据库没有该记录时返回 None。
def get_session(connection: Connection, *, session_id: str) -> ConversationSessionRecord | None:

    # region 读取完整会话记录并校验数据库字段
    row = connection.execute(load_sql(_GET_SESSION_SQL, sessions=RefundTables.SESSIONS),
                             {"session_id": session_id}).mappings().one_or_none()
    return ConversationSessionRecord.model_validate(dict(row)) if row is not None else None
    # endregion


# 按原文序号读取完整会话历史，返回已验证的数据库记录。
def get_history(connection: Connection, *, session_id: str) -> list[ConversationMessageRecord]:

    # region 按会话查询有序原文并校验每条记录
    rows = connection.execute(load_sql(_GET_HISTORY_SQL, messages=RefundTables.MESSAGES),
                              {"session_id": session_id}).mappings()
    return [ConversationMessageRecord.model_validate(dict(row)) for row in rows]
    # endregion


# 读取末条消息的序号，空会话返回 0。
def get_last_sequence(connection: Connection, *, session_id: str) -> int:

    # region 读取并校验会话内的最大序号
    row = connection.execute(load_sql(_GET_LAST_SEQUENCE_SQL, messages=RefundTables.MESSAGES),
                             {"session_id": session_id}).mappings().one()
    return ConversationSequenceRow.model_validate(dict(row)).last_sequence
    # endregion


# 使用 Service 分配的序号插入一条会话原文。
def insert_message(connection: Connection, *, session_id: str, sequence: int, message: ExchangeMessage) -> None:

    # region 使用既有字符串角色和原文格式写入数据库
    fields = message.model_dump(mode="json", include={"role", "content"})
    connection.execute(load_sql(_INSERT_MESSAGE_SQL, messages=RefundTables.MESSAGES),
                       {"session_id": session_id, "sequence": sequence, **fields})
    # endregion


# 将指定会话的更新时间设为数据库当前 UTC 时间。
def touch_session(connection: Connection, *, session_id: str) -> None:

    # region 更新本次追加原文所属会话的时间
    connection.execute(load_sql(_TOUCH_SESSION_SQL, sessions=RefundTables.SESSIONS), {"session_id": session_id})
    # endregion
