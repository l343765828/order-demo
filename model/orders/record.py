"""订单表保存的业务事实。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from config.limits import RefundLimits
from model.base import StrictModel
from model.orders.query import OrderQueryId
from model.orders.status import PaymentStatus, ShippingStatus


# 表示 orders 的一行，用于持久化边界核对归属、状态和金额。
class OrderRecord(StrictModel):
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
    # 订单建立时间，按 UTC 保存。
    created_at: datetime
    # 最近一次更新订单事实的时间，按 UTC 保存。
    updated_at: datetime
