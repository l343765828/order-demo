"""Host 保存和核对未决退款提交。"""

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from interfaces.refund.storage import RefundDispatchService
from model.refund.status import DispatchStatus, RefundOutcome
from model.refund.submission import AuthorizedRefundIntent, RefundPolicyVerification, RefundResult
from repositories import dispatch_repository


# 为 Host 管理退款提交记录的连接和短事务。
class MySQLRefundDispatchService(RefundDispatchService):
    # 使用 Host 进程复用的业务 Engine。
    def __init__(self, engine: Engine):

        # region 保存入口注入的 Engine
        self._engine = engine
        # endregion

    # 查找同一消费者订单的阻塞提交键，供只读核对使用。
    def blocking_dispatch(self, authorization: AuthorizedRefundIntent) -> str | None:

        # region 读取仍拦截本订单的提交，归还连接后交给 Host 核对
        with self._engine.connect() as connection:
            return dispatch_repository.get_blocking_dispatch(connection, authorization=authorization)
        # endregion

    # 为此次授权保存新提交。只有确认了本次 INSERT 和 COMMIT 才允许发送。
    def reserve_dispatch(self, event: RefundPolicyVerification) -> bool:

        # region 验证可信会话和用户原文并持久化单次提交
        try:
            with self._engine.begin() as connection:
                inserted = dispatch_repository.insert_dispatch(connection, event=event, status=DispatchStatus.PENDING)
            return inserted
        except IntegrityError:

            # region 同订单已有拦截或同键已保存时回滚并交给 Host 核对
            return False
            # endregion

        # endregion

    # 仅更新现有未决提交。已确认的终态不会被后续回执覆盖。
    def record_dispatch_result(self, key: str, result: RefundResult) -> None:

        # region 根据回执类型选择提交状态和允许的前态
        match result.status:
            case RefundOutcome.SIMULATED_SUCCEEDED | RefundOutcome.ALREADY_REFUNDED:

                # region 已确认退款时从未决状态转为成功
                status = DispatchStatus.SUCCEEDED
                expected = (DispatchStatus.PENDING, DispatchStatus.UNCONFIRMED)
                # endregion

            case RefundOutcome.UNCONFIRMED:

                # region 暂时无法核实时保留同订单拦截
                status = DispatchStatus.UNCONFIRMED
                expected = (DispatchStatus.PENDING, DispatchStatus.UNCONFIRMED)
                # endregion

            case _:

                # region 明确拒绝或失败时仅关闭尚待执行的提交
                status = DispatchStatus.CLOSED_NO_REFUND
                expected = (DispatchStatus.PENDING, DispatchStatus.PENDING)
                # endregion

        # endregion

        # region 在短事务中回写仍符合期望前态的提交
        with self._engine.begin() as connection:
            dispatch_repository.update_dispatch_result(
                connection, key=key, status=status, expected=expected,
                refund_id=result.refund.refund_id if result.refund else None, reason=result.reason,
            )
        # endregion
