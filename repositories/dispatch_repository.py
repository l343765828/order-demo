"""在调用方的连接上读写退款提交及其授权来源。"""

from typing import Final

from sqlalchemy import Connection

from config.limits import RefundTables
from model.refund.status import ConversationRole, DispatchStatus, RefundReason
from model.refund.submission import AuthorizedRefundIntent, RefundDispatch, RefundDispatchKeyRow, RefundPolicyVerification
from utils.sql_loader import load_sql

_GET_BLOCKING_DISPATCH_SQL: Final = "dispatches/get_blocking_dispatch.sql"
_INSERT_DISPATCH_SQL: Final = "dispatches/insert_dispatch.sql"
_UPDATE_DISPATCH_RESULT_SQL: Final = "dispatches/update_dispatch_result.sql"
_LOCK_DISPATCH_SQL: Final = "dispatches/lock_dispatch.sql"
_GET_AUTHORIZATION_SOURCE_SQL: Final = "dispatches/get_authorization_source.sql"


# 读取指定消费者订单当前未决的提交键。
def get_blocking_dispatch(connection: Connection, *, authorization: AuthorizedRefundIntent) -> str | None:

    # region 按归属筛选并校验提交键投影
    row = connection.execute(
        load_sql(_GET_BLOCKING_DISPATCH_SQL, dispatches=RefundTables.DISPATCHES, sessions=RefundTables.SESSIONS),
        {"order_id": authorization.order_id, "consumer_id": authorization.consumer_id},
    ).mappings().one_or_none()
    return RefundDispatchKeyRow.model_validate(dict(row)).idempotency_key if row is not None else None
    # endregion


# 使用已保存的用户原文插入本次提交，返回是否有一行写入。
def insert_dispatch(connection: Connection, *, event: RefundPolicyVerification, status: DispatchStatus) -> bool:

    # region 绑定本次政策事件、授权来源及初始提交状态并执行插入
    authorization = event.authorization
    result = connection.execute(
        load_sql(_INSERT_DISPATCH_SQL, dispatches=RefundTables.DISPATCHES,
                 sessions=RefundTables.SESSIONS, messages=RefundTables.MESSAGES),
        {**authorization.model_dump(), "idempotency_key": event.idempotency_key, "role": ConversationRole.USER.value,
         "status": status.value, "trace_id": event.trace_id},
    )
    return result.rowcount == 1
    # endregion


# 为符合期望前态的提交记录写入退款单号或结果原因。
def update_dispatch_result(
    connection: Connection, *, key: str, status: DispatchStatus, expected: tuple[DispatchStatus, DispatchStatus],
    refund_id: str | None, reason: RefundReason | None,
) -> None:

    # region 按原幂等键和期望前态更新提交
    connection.execute(
        load_sql(_UPDATE_DISPATCH_RESULT_SQL, dispatches=RefundTables.DISPATCHES),
        {"idempotency_key": key, "status": status.value, "refund_id": refund_id,
         "error_code": reason.value if reason is not None else None,
         "first_expected_status": expected[0].value, "second_expected_status": expected[1].value},
    )
    # endregion


# 以幂等键锁定对应提交并校验持久化字段。
def lock_dispatch(connection: Connection, *, key: str) -> RefundDispatch | None:

    # region 在退款事务内读取并锁定提交
    row = connection.execute(load_sql(_LOCK_DISPATCH_SQL, dispatches=RefundTables.DISPATCHES),
                             {"idempotency_key": key}).mappings().one_or_none()
    return RefundDispatch.model_validate(dict(row)) if row is not None else None
    # endregion


# 核实锁定提交引用的原文由认证消费者在其会话中发送。
def has_authorization_source(connection: Connection, *, dispatch: RefundDispatch, consumer_id: str) -> bool:

    # region 核对原文角色和认证消费者归属
    result = connection.execute(
        load_sql(_GET_AUTHORIZATION_SOURCE_SQL, sessions=RefundTables.SESSIONS, messages=RefundTables.MESSAGES),
        {"session_id": dispatch.session_id, "consumer_id": consumer_id,
         "source_message_sequence": dispatch.source_message_sequence, "role": ConversationRole.USER.value},
    )
    return result.scalar_one_or_none() is not None
    # endregion
