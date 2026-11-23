"""Host 保存和核对未决退款提交。"""

from pymysql import IntegrityError

from config.limits import RefundTables
from model.refund.status import ConversationRole, DispatchStatus, RefundOutcome
from model.refund.submission import AuthorizedRefundIntent, RefundPolicyVerification, RefundResult
from utils.mysql import pooled_mysql_connection


# 查找同一消费者订单的阻塞提交键，供只读核对使用。
def blocking_dispatch(pool, authorization: AuthorizedRefundIntent) -> str | None:
    # region 在新连接上读取仍拦截本订单的提交
    with pooled_mysql_connection(pool, shareable=False) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT d.idempotency_key FROM {RefundTables.DISPATCHES} d "
            f"JOIN {RefundTables.SESSIONS} s ON s.session_id=d.session_id "
            "WHERE d.blocking_order_id=%s AND s.consumer_id=%s",
            (authorization.order_id, authorization.consumer_id),
        )
        row = cursor.fetchone()
        return row[0] if row is not None else None
    # endregion


# 为此次授权保存新提交。只有确认了本次 INSERT 和 COMMIT 才允许发送。
def reserve_dispatch(pool, event: RefundPolicyVerification) -> bool:
    # region 验证可信会话和用户原文并持久化单次提交
    authorization = event.authorization
    with pooled_mysql_connection(pool, shareable=False) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {RefundTables.DISPATCHES} "
                    "(idempotency_key, order_id, session_id, source_message_sequence, status, trace_id, created_at, updated_at) "
                    "SELECT %s,%s,s.session_id,m.sequence,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6) "
                    f"FROM {RefundTables.SESSIONS} s JOIN {RefundTables.MESSAGES} m USING (session_id) "
                    "WHERE s.session_id=%s AND s.consumer_id=%s AND m.sequence=%s AND m.role=%s",
                    (event.idempotency_key, authorization.order_id, DispatchStatus.PENDING.value, event.trace_id,
                     authorization.session_id, authorization.consumer_id, authorization.source_message_sequence,
                     ConversationRole.USER.value),
                )
                inserted = cursor.rowcount == 1
            connection.commit()
            return inserted
        except IntegrityError:
            # region 同订单已有拦截或同键已经持久化时停止，交给只读核对
            connection.rollback()
            return False
            # endregion
    # endregion


# 仅更新现有未决提交。可确认成功的结果不会被后续回执覆盖。
def record_dispatch_result(pool, key: str, result: RefundResult) -> None:
    # region 映射已确认结果和未决状态并提交期望前态更新
    match result.status:
        case RefundOutcome.SIMULATED_SUCCEEDED | RefundOutcome.ALREADY_REFUNDED:
            status = DispatchStatus.SUCCEEDED
            expected = (DispatchStatus.PENDING, DispatchStatus.UNCONFIRMED)
        case RefundOutcome.UNCONFIRMED:
            status = DispatchStatus.UNCONFIRMED
            expected = (DispatchStatus.PENDING, DispatchStatus.UNCONFIRMED)
        case _:
            status = DispatchStatus.CLOSED_NO_REFUND
            expected = (DispatchStatus.PENDING, DispatchStatus.PENDING)
    with pooled_mysql_connection(pool, shareable=False) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {RefundTables.DISPATCHES} SET status=%s, refund_id=%s, error_code=%s, "
            "updated_at=UTC_TIMESTAMP(6) WHERE idempotency_key=%s AND status IN (%s,%s)",
            (status.value, result.refund.refund_id if result.refund else None,
             result.reason.value if result.reason else None, key, *expected),
        )
        connection.commit()
    # endregion
