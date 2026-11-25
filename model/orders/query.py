"""只读订单查询契约；调用者必须先认证消费者身份。"""
from typing import Annotated, Any, Self

from pydantic import BeforeValidator, Field, model_validator

from config.limits import RefundLimits
from model.base import StrictModel
from model.base import status_schema
from model.orders.status import OrderQueryStatus, PaymentStatus, RefundStatus, ShippingStatus


# 查单允许的订单号类型和字符数范围，由工具签名和请求模型共用。
OrderQueryId = Annotated[str, Field(strict=True, min_length=1, max_length=RefundLimits.MAX_ORDER_ID_CHARS)]


# 定义查单工具可接收的订单编号，不包含消费者身份。
class GetOrderRequest(StrictModel):
    # 消费者要查询的订单号，服务端会结合认证身份核对订单归属。
    order_id: OrderQueryId


# 描述本人订单的支付、发货、退款状态及实付金额。
class OrderDetails(StrictModel):
    # 已通过归属校验的订单编号，用于核对查单结果与请求一致。
    order_id: str
    # 订单是否完成支付，未支付时没有可退的实付款项。
    payment_status: Annotated[PaymentStatus, BeforeValidator(PaymentStatus), status_schema(PaymentStatus, "Payment Status")]
    # 订单是否已经发货，用于判断是否符合演示退款条件。
    shipping_status: Annotated[ShippingStatus, BeforeValidator(ShippingStatus), status_schema(ShippingStatus, "Shipping Status")]
    # 订单是否已有模拟退款记录，用于识别重复退款。
    refund_status: Annotated[RefundStatus, BeforeValidator(RefundStatus), status_schema(RefundStatus, "Refund Status")]
    # 消费者实际支付的金额，单位为人民币分。
    paid_amount_fen: int = Field(ge=0)


# 区分查到订单、订单不可操作和查询失败三种结果。
class OrderQueryResult(StrictModel):
    # 查单结果状态，用于区分查到本人订单、订单不可操作和服务失败。
    status: Annotated[OrderQueryStatus, BeforeValidator(OrderQueryStatus), status_schema(OrderQueryStatus)]
    # 仅查到本人订单时携带订单详情，其余状态为 None。
    order: OrderDetails | None = None
    # 查询失败时供调用方使用的错误说明，其他状态为 None。
    error: str | None = None

    # 将 SDK 接收的查单结果恢复为具名模型，供流程按枚举和属性读取。
    @classmethod
    def from_mcp(cls, data: dict[str, Any]) -> Self:

        # region 返回包含订单详情时恢复嵌套模型和业务枚举
        details = data.get("order")
        order = None
        if details is not None:
            order = OrderDetails.model_construct(
                order_id=details["order_id"], payment_status=PaymentStatus(details["payment_status"]),
                shipping_status=ShippingStatus(details["shipping_status"]), refund_status=RefundStatus(details["refund_status"]),
                paid_amount_fen=details["paid_amount_fen"],
            )
        # endregion

        # region 还原查单状态及可选订单详情和错误信息
        return cls.model_construct(status=OrderQueryStatus(data["status"]), order=order, error=data.get("error"))
        # endregion

    # 校验查询状态、订单详情与错误信息之间的对应关系。
    @model_validator(mode="after")
    def check_result(self):

        # region 按查询状态限制对外结果，只有查到本人订单时返回详情
        match self.status:
            case OrderQueryStatus.FOUND if self.order is not None and self.error is None:
                return self
            case OrderQueryStatus.UNAVAILABLE if self.order is None and self.error is None:
                return self
            case OrderQueryStatus.FAILED if self.order is None and self.error:
                return self
        raise ValueError("订单结果与状态不一致")
        # endregion
