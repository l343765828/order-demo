"""退款办理依据的确定性支付和发货规则。"""

from model.orders.status import PaymentStatus, ShippingStatus
from model.refund.status import RefundReason


# 根据可信支付及发货状态返回拒绝原因，None 表示订单状态允许退款。
def reject_refund_for_order_state(payment_status: PaymentStatus, shipping_status: ShippingStatus) -> RefundReason | None:
    # region 按优先级拒绝未支付和已发货订单
    if payment_status is PaymentStatus.UNPAID:
        return RefundReason.UNPAID
    if shipping_status is ShippingStatus.SHIPPED:
        return RefundReason.SHIPPED
    return None
    # endregion
