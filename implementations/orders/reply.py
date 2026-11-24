"""将核验过的订单事实转换为消费者回复。"""

from model.orders.query import OrderQueryResult
from model.orders.status import OrderQueryStatus, PaymentStatus, RefundStatus, ShippingStatus
from model.refund.preference import ResponseDetail

UNAVAILABLE = "未找到可操作的订单。"
SERVICE_ERROR = "订单服务暂时不可用，请稍后再试。"
ORDER_QUERY_INCOMPLETE = "本轮未能完成订单查询，请稍后再试。"


# 根据已校验的订单查询结果生成面向消费者的中文回复。
def order_reply(result: OrderQueryResult, *, detail: ResponseDetail | None = None) -> str:

    # region 订单不可操作时隐藏详情，查询失败时提示服务不可用
    if result.status is OrderQueryStatus.UNAVAILABLE:
        return UNAVAILABLE
    if result.order is None:
        return SERVICE_ERROR
    # endregion

    # region 取得本人订单详情时说明支付、发货和模拟退款状态
    order = result.order
    payment = "已支付" if order.payment_status is PaymentStatus.PAID else "未支付"
    shipping = "已发货" if order.shipping_status is ShippingStatus.SHIPPED else "未发货"
    refund = "有模拟退款记录" if order.refund_status is RefundStatus.REFUNDED else "无模拟退款记录"
    match detail:
        case ResponseDetail.BRIEF:

            # region 简短答复保留全部事实并采用紧凑格式
            return f"{order.order_id}｜{payment}｜{shipping}｜{refund}｜实付 {order.paid_amount_fen} 分。"
            # endregion

        case ResponseDetail.DETAILED:

            # region 详细答复为同一组事实提供独立标签
            return (f"订单 {order.order_id}\n支付状态：{payment}\n发货状态：{shipping}\n"
                    f"模拟退款状态：{refund}\n实付金额：{order.paid_amount_fen} 分")
            # endregion

    return f"订单 {order.order_id}：{payment}，{shipping}，{refund}，实付 {order.paid_amount_fen} 分。"
    # endregion
