"""按可信本人订单的当前状态判断退款资格。"""

from model.orders.status import PaymentStatus, RefundStatus, ShippingStatus
from model.refund.status import RefundQualification


# 根据已核实的支付、发货及退款状态返回本次资格结论。
def qualify_refund(
    *, payment_status: PaymentStatus, shipping_status: ShippingStatus, refund_status: RefundStatus,
) -> RefundQualification:

    # region 优先识别原退款，再按支付和发货状态判断首次办理资格
    if refund_status is RefundStatus.REFUNDED:

        # region 存在原退款时返回既有结果
        return RefundQualification.ALREADY_REFUNDED
        # endregion

    if payment_status is PaymentStatus.UNPAID:

        # region 未支付时没有可退款项
        return RefundQualification.UNPAID
        # endregion

    if shipping_status is ShippingStatus.SHIPPED:

        # region 已支付但已发货时拒绝首次办理
        return RefundQualification.SHIPPED
        # endregion

    # region 已付、未发货且无原退款时允许办理
    return RefundQualification.ELIGIBLE
    # endregion

    # endregion
