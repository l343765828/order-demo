"""供独立 MCP 服务查询原退款及执行受控退款事务。"""

from uuid import uuid4

from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from config.demo import MOCK_REFUND_ID_PREFIX
from implementations.refund.qualification import reject_refund_for_order_state
from interfaces.refund.storage import RefundTransactionService
from model.refund.status import DispatchStatus, RefundLookupStatus, RefundOutcome, RefundReason, RefundSource
from model.refund.submission import (
    GetRefundResultRequest, RefundLookupResult, RefundReceipt, RefundRecord, RefundResult, SubmitMockRefundRequest,
)
from repositories import dispatch_repository, order_repository, refund_repository


# 从退款实体提取消费者核对所需的订单、退款单号和实付金额。
def _receipt(record: RefundRecord) -> RefundReceipt:

    # region 提取对外回执字段
    return RefundReceipt(order_id=record.order_id, refund_id=record.refund_id,
                         amount_fen=record.amount_fen)
    # endregion


# 读取已锁定提交对应的授权和订单，在当前事务内决定是否插入退款。
def _execute(connection: Connection, *, request: SubmitMockRefundRequest, consumer_id: str) -> RefundResult:

    # region 先锁提交，校验请求匹配及授权来源的消费者归属
    dispatch = dispatch_repository.lock_dispatch(connection, key=request.idempotency_key)
    if (
        # 幂等键没有对应的持久提交。
        dispatch is None
        # 已锁定提交属于另一笔订单。
        or dispatch.order_id != request.order_id
        # 授权原文不存在或会话不属于认证消费者。
        or not dispatch_repository.has_authorization_source(connection, dispatch=dispatch, consumer_id=consumer_id)
    ):

        # region 提交或授权来源不匹配时拒绝执行
        return RefundResult(status=RefundOutcome.DENIED, reason=RefundReason.INVALID_DISPATCH)
        # endregion

    # endregion

    # region 再锁本人订单和退款记录，优先回传已有退款
    if not order_repository.lock_order(connection, order_id=request.order_id, consumer_id=consumer_id):

        # region 查不到本人订单时返回不可操作
        return RefundResult(status=RefundOutcome.DENIED, reason=RefundReason.UNAVAILABLE_ORDER)
        # endregion

    previous = refund_repository.lock_refund(connection, order_id=request.order_id)
    if previous is not None:

        # region 已有退款时返回原回执
        return RefundResult(status=RefundOutcome.ALREADY_REFUNDED, refund=_receipt(previous))
        # endregion

    # endregion

    # region 校验首次写入的授权与资格并按真实实付金额插入退款
    if (
        # 提交已完成、关闭或正在等待核对。
        dispatch.status is not DispatchStatus.PENDING
        # Host 已撤销本次首次写入的授权。
        or dispatch.revoked_at is not None
    ):

        # region 提交不再允许首次写入时拒绝执行
        return RefundResult(status=RefundOutcome.DENIED, reason=RefundReason.INVALID_DISPATCH)
        # endregion

    order = order_repository.get_locked_order(connection, order_id=request.order_id, consumer_id=consumer_id)
    rejection = reject_refund_for_order_state(order.payment_status, order.shipping_status)
    if rejection is not None:

        # region 订单不符合当前退款资格时返回拒绝原因
        return RefundResult(status=RefundOutcome.DENIED, reason=rejection)
        # endregion

    receipt = RefundReceipt(order_id=order.order_id, refund_id=f"{MOCK_REFUND_ID_PREFIX}{uuid4().hex}",
                            amount_fen=order.paid_amount_fen)
    refund_repository.insert_refund(connection, receipt=receipt, key=request.idempotency_key,
                                    source=RefundSource.EXECUTION)
    return RefundResult(status=RefundOutcome.SIMULATED_SUCCEEDED, refund=receipt)
    # endregion


# 管理 MCP 退款核对借用的连接及单次模拟退款事务。
class MySQLRefundTransactionService(RefundTransactionService):
    # 使用 MCP 进程复用的业务 Engine。
    def __init__(self, engine: Engine):

        # region 保存入口注入的 Engine
        self._engine = engine
        # endregion

    # 按认证消费者和订单只读核对原退款，提交键仅用于追踪。
    def lookup_refund(self, request: GetRefundResultRequest, *,
                      authenticated_consumer_id: str) -> RefundLookupResult:

        # region 查询本人订单原退款，归还连接后返回核对结果
        try:
            with self._engine.connect() as connection:
                record = refund_repository.get_refund(connection, order_id=request.order_id,
                                                      consumer_id=authenticated_consumer_id)
            if record is None:

                # region 查不到本人原退款时返回未找到
                return RefundLookupResult(status=RefundLookupStatus.NOT_FOUND)
                # endregion

            return RefundLookupResult(status=RefundLookupStatus.FOUND, refund=_receipt(record))
        except (SQLAlchemyError, ValueError):

            # region 数据库或持久记录不可用时返回核对不可用
            return RefundLookupResult(status=RefundLookupStatus.UNAVAILABLE)
            # endregion

        # endregion

    # 执行单次退款事务，保留明确回滚与提交回执不明的区分。
    def submit_refund(self, request: SubmitMockRefundRequest, *, authenticated_consumer_id: str) -> RefundResult:

        # region 在同一连接与事务内锁定和写入，提交返回后才确认本次成功
        committing = False
        try:
            with self._engine.connect() as connection, connection.begin() as transaction:
                result = _execute(connection, request=request, consumer_id=authenticated_consumer_id)
                if result.status is not RefundOutcome.SIMULATED_SUCCEEDED:

                    # region 已有原退款或拒绝时回滚只读事务并释放行锁
                    transaction.rollback()
                    return result
                    # endregion

                committing = True
                transaction.commit()
                return result
        except (SQLAlchemyError, ValueError):

            # region 提交回执无法确认时转入原退款核对
            if committing:
                return RefundResult(status=RefundOutcome.UNCONFIRMED, reason=RefundReason.RECEIPT_UNKNOWN)
            # endregion

            # region 提交前发生故障时返回明确失败
            return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.DATABASE_FAILURE)
            # endregion

        # endregion
