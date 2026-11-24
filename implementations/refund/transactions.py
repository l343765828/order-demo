"""供独立 MCP 服务查询原退款及执行受控退款事务。"""

from uuid import uuid4

from pymysql import MySQLError
from pymysql.cursors import DictCursor

from config.demo import MOCK_REFUND_ID_PREFIX
from config.limits import RefundTables
from implementations.refund.qualification import reject_refund_for_order_state
from model.orders.record import OrderRecord
from model.refund.status import ConversationRole, DispatchStatus, RefundLookupStatus, RefundOutcome, RefundReason, RefundSource
from model.refund.submission import (
    GetRefundResultRequest, RefundDispatch, RefundLookupResult, RefundReceipt, RefundRecord,
    RefundResult, SubmitMockRefundRequest,
)
from utils.mysql import pooled_mysql_connection


# 从持久记录投影消费者核对所需的订单、退款单号和实付金额。
def _receipt(row) -> RefundReceipt:

    # region 校验退款库记录并提取对外字段
    record = RefundRecord.model_validate(row)
    return RefundReceipt(order_id=record.order_id, refund_id=record.refund_id,
                         amount_fen=record.amount_fen, currency=record.currency)
    # endregion


# 只读核对本人订单原退款，未知提交键时仍可按订单查询。
def lookup_refund(pool, request: GetRefundResultRequest, consumer_id: str) -> RefundLookupResult:

    # region 以经认证的消费者限定查询并读取真实退款
    try:
        with pooled_mysql_connection(pool) as connection, connection.cursor(DictCursor) as cursor:
            cursor.execute(
                f"SELECT r.* FROM {RefundTables.REFUNDS} r JOIN {RefundTables.ORDERS} o USING (order_id) "
                "WHERE r.order_id=%s AND o.consumer_id=%s",
                (request.order_id, consumer_id),
            )
            row = cursor.fetchone()
            if row is None:

                # region 没有本人原退款时返回未找到
                return RefundLookupResult(status=RefundLookupStatus.NOT_FOUND)
                # endregion

            return RefundLookupResult(status=RefundLookupStatus.FOUND, refund=_receipt(row))
    except (MySQLError, ValueError):

        # region 库不可读或原记录损坏时返回查询不可用
        return RefundLookupResult(status=RefundLookupStatus.UNAVAILABLE)
        # endregion

    # endregion


# 读取已锁定提交对应的会话授权和订单，在当前事务内决定是否插入退款。
def _execute(cursor, request: SubmitMockRefundRequest, consumer_id: str) -> RefundResult:

    # region 先锁提交，校验请求匹配及授权来源的消费者归属
    cursor.execute(f"SELECT * FROM {RefundTables.DISPATCHES} WHERE idempotency_key=%s FOR UPDATE",
                   (request.idempotency_key,))
    row = cursor.fetchone()
    invalid = RefundResult(status=RefundOutcome.DENIED, reason=RefundReason.INVALID_DISPATCH)
    if row is None:
        return invalid
    dispatch = RefundDispatch.model_validate(row)
    if dispatch.order_id != request.order_id:
        return invalid
    cursor.execute(
        f"SELECT 1 FROM {RefundTables.SESSIONS} s JOIN {RefundTables.MESSAGES} m USING (session_id) "
        "WHERE s.session_id=%s AND s.consumer_id=%s AND m.sequence=%s AND m.role=%s",
        (dispatch.session_id, consumer_id, dispatch.source_message_sequence, ConversationRole.USER.value),
    )
    if cursor.fetchone() is None:

        # region 授权原文不存在或会话归属不符时拒绝
        return invalid
        # endregion

    # endregion

    # region 再锁本人订单，优先回传已有退款
    cursor.execute(
        f"SELECT * FROM {RefundTables.ORDERS} WHERE order_id=%s AND consumer_id=%s FOR UPDATE",
        (request.order_id, consumer_id),
    )
    order_row = cursor.fetchone()
    if order_row is None:
        return RefundResult(status=RefundOutcome.DENIED, reason=RefundReason.UNAVAILABLE_ORDER)
    cursor.execute(f"SELECT * FROM {RefundTables.REFUNDS} WHERE order_id=%s FOR UPDATE", (request.order_id,))
    previous = cursor.fetchone()
    if previous is not None:
        return RefundResult(status=RefundOutcome.ALREADY_REFUNDED, refund=_receipt(previous))
    # endregion

    # region 首次写入前校验授权尚有效，再按锁内订单事实确定退款资格和金额
    if (
        # 提交已完成、关闭或正在等待核对。
        dispatch.status is not DispatchStatus.PENDING
        # Host 已撤销本次首次写入的授权。
        or dispatch.revoked_at is not None
    ):
        return invalid
    order = OrderRecord.model_validate(order_row)
    rejection = reject_refund_for_order_state(order.payment_status, order.shipping_status)
    if rejection is not None:
        return RefundResult(status=RefundOutcome.DENIED, reason=rejection)
    receipt = RefundReceipt(order_id=order.order_id, refund_id=f"{MOCK_REFUND_ID_PREFIX}{uuid4().hex}",
                            amount_fen=order.paid_amount_fen, currency=order.currency)
    cursor.execute(
        f"INSERT INTO {RefundTables.REFUNDS} "
        "(refund_id, order_id, idempotency_key, amount_fen, currency, source, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))",
        (receipt.refund_id, receipt.order_id, request.idempotency_key, receipt.amount_fen,
         receipt.currency, RefundSource.EXECUTION.value),
    )
    return RefundResult(status=RefundOutcome.SIMULATED_SUCCEEDED, refund=receipt)
    # endregion


# 执行一次退款事务，区分提交确认丢失与明确回滚。
def submit_refund(pool, request: SubmitMockRefundRequest, consumer_id: str) -> RefundResult:

    # region 在独占连接上执行事务，只有提交返回后才确认本次成功
    committing = False
    try:
        with pooled_mysql_connection(pool, shareable=False) as connection:
            connection.begin()
            try:
                with connection.cursor(DictCursor) as cursor:
                    result = _execute(cursor, request, consumer_id)
                if result.status is RefundOutcome.SIMULATED_SUCCEEDED:
                    committing = True
                    connection.commit()
                else:
                    connection.rollback()
                return result
            except (MySQLError, ValueError):

                # region 写入确认前异常时释放事务，遇提交错误由 Host 核对
                connection.rollback()
                raise
                # endregion

    except (MySQLError, ValueError):

        # region COMMIT 可能已生效时返回未确认，其他错误无成功提交
        if committing:
            return RefundResult(status=RefundOutcome.UNCONFIRMED, reason=RefundReason.RECEIPT_UNKNOWN)
        return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.DATABASE_FAILURE)
        # endregion

    # endregion
