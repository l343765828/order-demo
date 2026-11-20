"""只读订单查询契约；调用者必须先认证消费者身份。"""
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from model.status import OrderQueryStatus, PaymentStatus, RefundStatus, ShippingStatus, status_schema


# 为订单相关数据模型统一启用严格类型校验并禁止额外字段。
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# 定义查单工具可接收的订单编号，不包含消费者身份。
class GetOrderRequest(StrictModel):
    order_id: str = Field(min_length=1, max_length=64)


# 描述本人订单的支付、发货、退款状态及实付金额。
class OrderDetails(StrictModel):
    order_id: str
    payment_status: Annotated[PaymentStatus, BeforeValidator(PaymentStatus), status_schema(PaymentStatus, "Payment Status")]
    shipping_status: Annotated[ShippingStatus, BeforeValidator(ShippingStatus), status_schema(ShippingStatus, "Shipping Status")]
    refund_status: Annotated[RefundStatus, BeforeValidator(RefundStatus), status_schema(RefundStatus, "Refund Status")]
    paid_amount_fen: int = Field(ge=0)
    currency: Literal["CNY"]


# 区分查到订单、订单不可操作和查询失败三种结果。
class OrderQueryResult(StrictModel):
    status: Annotated[OrderQueryStatus, BeforeValidator(OrderQueryStatus), status_schema(OrderQueryStatus)]
    order: OrderDetails | None = None
    error: str | None = None

    # 校验查询状态、订单详情与错误信息之间的对应关系。
    @model_validator(mode="after")
    def check_result(self):
        if self.status is OrderQueryStatus.FOUND and self.order is not None and self.error is None:
            return self
        if self.status is OrderQueryStatus.UNAVAILABLE and self.order is None and self.error is None:
            return self
        if self.status is OrderQueryStatus.FAILED and self.order is None and self.error:
            return self
        raise ValueError("订单结果与状态不一致")
