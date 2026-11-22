"""订单查询及订单状态模型使用的枚举。"""

from enum import StrEnum


# 区分本人订单查找成功、不可操作及服务失败。
class OrderQueryStatus(StrEnum):
    FOUND = "found"  # 已找到归属当前消费者的订单，可以返回订单详情。
    UNAVAILABLE = "unavailable"  # 订单不存在或不属于当前消费者，统一隐藏订单详情。
    FAILED = "failed"  # 订单服务查询失败，无法确认订单状态。


# 表示订单是否已支付。
class PaymentStatus(StrEnum):
    PAID = "paid"  # 订单已支付，实付金额以订单记录为准。
    UNPAID = "unpaid"  # 订单未支付，没有可退的实付款项。


# 表示订单是否已发货。
class ShippingStatus(StrEnum):
    SHIPPED = "shipped"  # 订单已发货，超出演示支持的退款范围。
    UNSHIPPED = "unshipped"  # 订单尚未发货，满足退款资格的一项条件。


# 表示订单是否已有模拟退款记录。
class RefundStatus(StrEnum):
    REFUNDED = "refunded"  # 已有成功的模拟退款记录。
    NOT_REFUNDED = "not_refunded"  # 没有成功的模拟退款记录，仍需判断退款资格。
