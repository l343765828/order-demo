"""订单表保存的业务事实。"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from config.limits import RefundLimits
from model.base import StrictModel
from model.orders.query import OrderQueryId
from model.orders.status import PaymentStatus, ShippingStatus


# 标识种子装载的五笔固定演示订单。
class DemoSeedOrderId(StrEnum):
    # 本人已支付且未发货的订单。
    PAID = "DEMO-1001"
    # 本人尚未支付的订单。
    UNPAID = "DEMO-1002"
    # 本人已支付且在首次装载时写入配套模拟退款的订单。
    REFUNDED = "DEMO-1003"
    # 本人已支付且已发货的订单。
    SHIPPED = "DEMO-1004"
    # 用于验证归属隔离的其他消费者订单。
    OTHER_CONSUMER = "DEMO-2001"


# 表示订单的归属、支付、发货和金额事实，用于写入种子和读取持久化订单。
class OrderFacts(StrictModel):
    # 演示订单编号，数据库主键。
    order_id: OrderQueryId
    # 订单所属的演示消费者编号。
    consumer_id: str = Field(min_length=1, max_length=RefundLimits.MAX_REFUND_IDENTIFIER_CHARS)
    # 支付完成时才有可退款的实付款项。
    payment_status: Annotated[PaymentStatus, BeforeValidator(PaymentStatus)]
    # 发货后超出演示退款范围。
    shipping_status: Annotated[ShippingStatus, BeforeValidator(ShippingStatus)]
    # 实付金额，单位人民币分。
    paid_amount_fen: int = Field(ge=0)
    # 演示订单的人民币币种。
    currency: Literal["CNY"]


# 表示 orders 中已持久化的一行，包含数据库记录的时间。
class OrderRecord(OrderFacts):
    # 订单建立时间，按 UTC 保存。
    created_at: datetime
    # 最近一次更新订单事实的时间，按 UTC 保存。
    updated_at: datetime
