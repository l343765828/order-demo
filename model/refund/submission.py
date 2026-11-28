"""模拟退款执行、结果核对与跨服务提交契约。"""

from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BeforeValidator, Field, model_validator

from config.limits import RefundLimits
from model.base import StrictModel, status_schema
from model.orders.query import GetOrderRequest, OrderQueryId
from model.refund.status import (
    DispatchStatus, RefundLookupStatus, RefundOutcome, RefundQualification, RefundReason, RefundSource,
)

RefundIdentifier = Annotated[str, Field(strict=True, min_length=1, max_length=RefundLimits.MAX_REFUND_IDENTIFIER_CHARS)]


# 保存当前会话中已提交的消费者原始消息定位。
class RefundTurnSource(StrictModel):
    # 可信运行上下文确定的模拟消费者编号。
    consumer_id: RefundIdentifier
    # 归属已核验且正被当前 Host 独占的会话编号。
    session_id: RefundIdentifier
    # 已持久保存的本轮用户原文序号。
    source_message_sequence: int = Field(gt=0)


# 记录由当前消费者对本次会话指定订单给出的明确办理授权。
class AuthorizedRefundIntent(RefundTurnSource):
    # 从本轮原文提取且已经核对的唯一办理订单。
    order_id: OrderQueryId


# 保存独立复核通过后为当前轮次准备的单次退款提交信息。
class PreparedRefund(StrictModel):
    # 本次调查从 CLI 到 MCP 的追踪标识。
    trace_id: RefundIdentifier
    # 本次提交唯一键，后续与提交记录和实际退款对账。
    idempotency_key: RefundIdentifier
    # 经复核的目标订单和本轮原始授权消息。
    authorization: AuthorizedRefundIntent


# 退款执行请求仅包含订单号和已持久化提交的幂等键。
class SubmitMockRefundRequest(GetOrderRequest):
    # Host 为本次明确办理生成的幂等键。
    idempotency_key: RefundIdentifier


# 查询本人订单的原退款，可附已知提交编号用于追踪。
class GetRefundResultRequest(GetOrderRequest):
    # 本次要核对的提交，未知时仍可仅按已认证的本人订单查询。
    idempotency_key: RefundIdentifier | None = None


# 退款业务结果仅暴露消费者核对需要的字段。
class RefundReceipt(StrictModel):
    # 成功退款所属的演示订单编号。
    order_id: OrderQueryId
    # 模拟退款单号，可用于查询现有记录。
    refund_id: RefundIdentifier
    # 本次模拟退款金额，单位为人民币分。
    amount_fen: int = Field(gt=0)


# 表示只读核对的结果，只有找到时附带原退款记录。
class RefundLookupResult(StrictModel):
    # 查询是否找到原退款、当前未找到或无法提供可信结果。
    status: Annotated[RefundLookupStatus, BeforeValidator(RefundLookupStatus), status_schema(RefundLookupStatus)]
    # 找到时返回真实原单，其余状态为空。
    refund: RefundReceipt | None = None

    # 将 MCP 已校验的只读回执还原为业务模型和枚举。
    @classmethod
    def from_mcp(cls, data: dict[str, Any]) -> Self:

        # region 按共享回执契约恢复原退款及查询状态
        refund = data.get("refund")
        return cls.model_construct(status=RefundLookupStatus(data["status"]),
                                   refund=RefundReceipt.model_construct(**refund) if refund is not None else None)
        # endregion

    # 核对退款单与查询是否找到之间的对应关系。
    @model_validator(mode="after")
    def check_refund(self):

        # region 确保只有找到原退款时提供原单
        if (self.status is RefundLookupStatus.FOUND) != (self.refund is not None):
            raise ValueError("核对状态与原退款记录不一致")
        return self
        # endregion


# 表示单次模拟退款结果或原因。
class RefundResult(StrictModel):
    # 区分成功、原单、拒绝、明确失败和结果未知。
    status: Annotated[RefundOutcome, BeforeValidator(RefundOutcome), status_schema(RefundOutcome)]
    # 本次产生或再次核对到的真实原单。
    refund: RefundReceipt | None = None
    # 未确认成功时的业务原因。
    reason: Annotated[RefundReason, BeforeValidator(RefundReason)] | None = None

    # 区分本人订单因支付或发货状态被拒的结果，此类拒绝可继续只读检索政策。
    @property
    def order_state_denial(self) -> RefundQualification | None:

        # region 排除非拒绝结果
        if self.status is not RefundOutcome.DENIED:
            return None
        # endregion

        # region 将需要解释的拒绝原因转换为已确认的资格结论
        match self.reason:
            case RefundReason.UNPAID:
                return RefundQualification.UNPAID
            case RefundReason.SHIPPED:
                return RefundQualification.SHIPPED
            case _:

                # region 授权或订单不可操作等拒绝沿用原业务路径
                return None
                # endregion
        # endregion

    # 将 MCP 已校验的执行回执还原为业务模型和枚举。
    @classmethod
    def from_mcp(cls, data: dict[str, Any]) -> Self:

        # region 按共享回执契约恢复实际退款、业务状态及原因
        refund = data.get("refund")
        reason = data.get("reason")
        return cls.model_construct(status=RefundOutcome(data["status"]),
                                   refund=RefundReceipt.model_construct(**refund) if refund is not None else None,
                                   reason=RefundReason(reason) if reason is not None else None)
        # endregion

    # 限制成功结果附原单，其余结果附原因。
    @model_validator(mode="after")
    def check_result(self):

        # region 检查状态与原单及原因的组合
        succeeded = self.status in (RefundOutcome.SIMULATED_SUCCEEDED, RefundOutcome.ALREADY_REFUNDED)
        if (self.refund is not None, self.reason is None) != (succeeded, succeeded):
            raise ValueError("退款结果与状态不一致")
        return self
        # endregion


# 表示 refunds 中已持久化的原退款记录。
class RefundRecord(RefundReceipt):
    # 实际执行使用的提交键。种子退款无提交键。
    idempotency_key: RefundIdentifier | None
    # 原退款来源，与是否具有提交键对应。
    source: Annotated[RefundSource, BeforeValidator(RefundSource)]
    # 模拟退款已经落库的 UTC 时间。
    created_at: datetime


# 表示 refund_dispatches 中的一次持久提交及其授权证据。
class RefundDispatch(SubmitMockRefundRequest):
    # 保存本次办理原文的消费者会话。
    session_id: RefundIdentifier
    # 授权原文在该会话内的序号。
    source_message_sequence: int = Field(gt=0)
    # 授权被取消的 UTC 时间，未取消时为空。
    revoked_at: datetime | None
    # 该提交是否拦截后续尝试及当前完成状态。
    status: Annotated[DispatchStatus, BeforeValidator(DispatchStatus)]
    # 已确认原退款单号，未确认时为空。
    refund_id: RefundIdentifier | None
    # 用于关联已保存政策依据与业务调用的追踪标识。
    trace_id: RefundIdentifier
    # 关闭或未确认的原因，尚未产生原因时为空。
    error_code: RefundIdentifier | None
    # 建立提交记录时的 UTC 时间。
    created_at: datetime
    # 最近更改提交状态时的 UTC 时间。
    updated_at: datetime
    # MySQL 生成列，仅未决状态下有值，由唯一约束拦截新提交。
    blocking_order_id: OrderQueryId | None


# 未决提交查询用的数据库投影。
class RefundDispatchKeyRow(StrictModel):
    # 当前仍拦截该订单办理的提交键。
    idempotency_key: RefundIdentifier
