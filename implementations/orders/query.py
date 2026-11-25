"""查询认证消费者的订单并收束只读数据库事务。"""

from typing import Final

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from interfaces.orders.query import OrderQueryService
from model.orders.query import GetOrderRequest, OrderDetails, OrderQueryResult
from model.orders.status import OrderQueryStatus, RefundStatus
from repositories.order_repository import get_order

_QUERY_FAILURE: Final = "订单服务暂时不可用"


# 复用入口传入的 Engine 读取本人订单，按照既有契约返回查询结果。
class MySQLOrderQueryService(OrderQueryService):
    # 保存进程复用的 Engine，由应用入口负责释放。
    def __init__(self, engine: Engine) -> None:

        # region 保存查单连接来源
        self._engine = engine
        # endregion

    # 仅使用认证层提供的消费者编号限定查询范围。
    def query_order(self, request: GetOrderRequest, *, authenticated_consumer_id: str) -> OrderQueryResult:

        # region 借出连接查询本人订单，退出时结束只读事务并归还连接
        try:
            with self._engine.connect() as connection:
                row = get_order(connection, order_id=request.order_id, consumer_id=authenticated_consumer_id)
        except (SQLAlchemyError, ValidationError):

            # region 数据库查询或数据校验失败时返回统一错误
            return OrderQueryResult(status=OrderQueryStatus.FAILED, error=_QUERY_FAILURE)
            # endregion

        # endregion

        # region 无本人订单时返回不可操作状态
        if row is None:
            return OrderQueryResult(status=OrderQueryStatus.UNAVAILABLE)
        # endregion

        # region 根据真实退款关联组装允许对外返回的订单详情
        refund_status = RefundStatus.REFUNDED if row.refund_id is not None else RefundStatus.NOT_REFUNDED
        return OrderQueryResult(status=OrderQueryStatus.FOUND, order=OrderDetails(
            order_id=row.order_id, payment_status=row.payment_status, shipping_status=row.shipping_status,
            paid_amount_fen=row.paid_amount_fen, refund_status=refund_status,
        ))
        # endregion
