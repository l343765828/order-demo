"""订单数据的只读查询。"""
from pymysql import MySQLError
from model.orders.query import GetOrderRequest, OrderDetails, OrderQueryResult
from model.orders.status import OrderQueryStatus, RefundStatus

# 按认证消费者与订单号查询订单及退款记录，将数据库错误转为失败结果。
def query_order(pool, request: GetOrderRequest, authenticated_consumer_id: str) -> OrderQueryResult:
    """仅接受认证层提供的消费者身份；数据库错误不包含连接信息。"""
    try:
        # region 按可信身份限定订单并读取真实退款状态
        connection = pool.connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT o.order_id, o.payment_status, o.shipping_status, "
                    "o.paid_amount_fen, o.currency, r.refund_id "
                    "FROM orders o LEFT JOIN refunds r ON r.order_id=o.order_id "
                    "WHERE o.order_id=%s AND o.consumer_id=%s",
                    (request.order_id, authenticated_consumer_id),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        # endregion
    # region 数据库异常时报告服务失败，找不到归属当前消费者的订单时统一隐藏详情
    except MySQLError:
        return OrderQueryResult(status=OrderQueryStatus.FAILED, error="订单服务暂时不可用")
    if row is None:
        return OrderQueryResult(status=OrderQueryStatus.UNAVAILABLE)
    # endregion

    # region 查到本人订单时，根据关联退款记录返回模拟退款状态
    return OrderQueryResult(status=OrderQueryStatus.FOUND, order=OrderDetails(
        order_id=row[0], payment_status=row[1], shipping_status=row[2],
        paid_amount_fen=row[3], currency=row[4],
        refund_status=RefundStatus.REFUNDED if row[5] is not None else RefundStatus.NOT_REFUNDED,
    ))
    # endregion
