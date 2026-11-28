"""复核本次退款授权与事实，按确认顺序保存和单次执行。"""

from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from config import AppConfig
from implementations.refund import controlled_tools
from implementations.refund.qualification import qualify_refund
from interfaces.refund.storage import RefundDispatchService
from model.orders.query import OrderQueryResult
from model.orders.status import OrderQueryStatus
from model.refund.status import RefundEvent, RefundOutcome, RefundQualification, RefundReason
from model.refund.submission import (
    AuthorizedRefundIntent, GetRefundResultRequest, PreparedRefund, RefundResult, SubmitMockRefundRequest,
)


# 固定节点共享的业务能力，绑定数据库、独立 MCP 配置和当前会话事件。
class RefundExecution:
    # 注入当前 Host 的存储、配置和事件输出。
    def __init__(self, dispatches: RefundDispatchService, settings: AppConfig, emit: Callable[..., None]):

        # region 保存可信运行配置及依赖
        self.dispatches = dispatches
        self.settings = settings
        self.emit = emit
        # endregion

    # 确认已知提交的业务状态；回写故障时保留退款库的真实结果。
    def _record(self, key: str, result: RefundResult) -> None:

        # region 尝试更新未决提交，原回执不受回写故障影响
        try:
            self.dispatches.record_dispatch_result(key, result)
        except SQLAlchemyError as exc:
            self.emit(RefundEvent.STORE_FAILED.value, idempotency_key=key, error_type=type(exc).__name__)
        # endregion

    # 仅以只读工具核对原结果，找不到时保留旧提交拦截。
    def _lookup(self, order_id: str, trace_id: str, key: str | None = None, *, attempted: bool = False) -> RefundResult:

        # region 调用只读核对并校验订单，传输故障按未知处理
        self.emit(RefundEvent.LOOKUP.value, order_id=order_id, idempotency_key=key)
        request = GetRefundResultRequest(order_id=order_id, idempotency_key=key)
        try:
            lookup = controlled_tools.read_refund(self.settings.mcp, request, trace_id)
            receipt = lookup.refund
        except Exception:
            receipt = None
        # endregion

        # region 已查到可信原单时记录确认结果，否则保留待核对
        if (
            # 查询返回确切的原退款。
            receipt is not None
            # 原退款属于本次核对订单。
            and receipt.order_id == order_id
        ):
            result = RefundResult(status=RefundOutcome.ALREADY_REFUNDED, refund=receipt)
        else:
            result = RefundResult(status=RefundOutcome.UNCONFIRMED, reason=RefundReason.RECEIPT_UNKNOWN)
        if (
            # 知道要核对的是哪条提交。
            key is not None
            # 当前 Host 刚尝试发送，或已经查到了该订单原退款。
            and (attempted or result.refund is not None)
        ):
            self._record(key, result)
        return result
        # endregion

    # 根据可信订单和未决记录决定是否可继续调查或执行，None 表示可以继续。
    def preflight(self, authorization: AuthorizedRefundIntent, order_result: OrderQueryResult,
                  trace_id: str) -> RefundResult | None:

        # region 缺少可操作的本人订单时立即停止
        order = order_result.order
        if order_result.status is OrderQueryStatus.UNAVAILABLE:
            return RefundResult(status=RefundOutcome.DENIED, reason=RefundReason.UNAVAILABLE_ORDER)
        if (
            # 查单服务没有确认取得本人订单。
            order_result.status is not OrderQueryStatus.FOUND
            # 订单服务未返回本人订单事实。
            or order is None
            # 返回的订单与本次明确授权不符。
            or order.order_id != authorization.order_id
        ):
            return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.EXECUTION_UNAVAILABLE)
        # endregion

        # region 优先直接核对原退款或未决提交，无需再次检索政策
        qualification = qualify_refund(payment_status=order.payment_status, shipping_status=order.shipping_status,
                                      refund_status=order.refund_status)
        try:
            key = self.dispatches.blocking_dispatch(authorization)
        except (SQLAlchemyError, ValueError):
            if qualification is RefundQualification.ALREADY_REFUNDED:
                return self._lookup(order.order_id, trace_id)
            return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.EXECUTION_UNAVAILABLE)
        if (
            # 该订单已有退款记录。
            qualification is RefundQualification.ALREADY_REFUNDED
            # 已有未决提交可能产生退款。
            or key is not None
        ):
            return self._lookup(order.order_id, trace_id, key)
        # endregion

        # region 用真实订单支付和发货状态确定资格
        rejection = qualification.denial_reason
        if rejection is not None:
            return RefundResult(status=RefundOutcome.DENIED, reason=rejection)
        return None
        # endregion

    # 固定复核前独立重查本人订单，无法取得回执时返回 None。
    def read_current_order(self, authorization: AuthorizedRefundIntent, trace_id: str) -> OrderQueryResult | None:

        # region 用独立只读工具取得最新的订单事实
        try:
            return controlled_tools.read_order(self.settings.mcp, authorization.order_id, trace_id)
        except Exception:

            # region 独立查单失败时交给复核节点结束办理
            return None
            # endregion

        # endregion

    # 复核最新订单，通过时为本轮授权生成唯一提交键。
    def review(self, authorization: AuthorizedRefundIntent, trace_id: str,
               current: OrderQueryResult | None) -> PreparedRefund | RefundResult:

        # region 按最新订单事实复核资格并优先处理原退款及未决提交
        if current is None:

            # region 最新订单查询未返回回执时停止办理
            return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.EXECUTION_UNAVAILABLE)
            # endregion

        outcome = self.preflight(authorization, current, trace_id)
        if outcome is not None:

            # region 最新订单不符合新办条件时返回已确认的业务结果
            return outcome
            # endregion

        # endregion

        # region 执行通道可用时生成单次提交信息
        if self.settings.mcp.write_token is None:

            # region 缺少执行凭据时结束本次新办理
            return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.EXECUTION_UNAVAILABLE)
            # endregion

        return PreparedRefund(trace_id=trace_id, idempotency_key=str(uuid4()), authorization=authorization)
        # endregion

    # 保存当轮提交并最多发送一次，未确认结果仅使用只读核对。
    def send(self, prepared: PreparedRefund) -> RefundResult:

        # region 确认本次提交已保存，遇竞争或旧提交时只读核对
        key = prepared.idempotency_key
        authorization = prepared.authorization
        try:
            if not self.dispatches.reserve_dispatch(prepared):
                blocker = self.dispatches.blocking_dispatch(authorization)
                if blocker is not None:
                    return self._lookup(authorization.order_id, prepared.trace_id, blocker)
                return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.DISPATCH_NOT_SAVED)
        except (SQLAlchemyError, ValueError) as exc:
            self.emit(RefundEvent.STORE_FAILED.value, idempotency_key=key, stage=RefundEvent.DISPATCH_SAVED.value,
                      error_type=type(exc).__name__)
            return RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.DISPATCH_NOT_SAVED)
        self.emit(RefundEvent.DISPATCH_SAVED.value, idempotency_key=key,
                  authorization=authorization.model_dump(mode="json"))
        # endregion

        # region 按受控业务请求发送一次，错误或响应无法确认时转入只读核对
        request = SubmitMockRefundRequest(order_id=authorization.order_id, idempotency_key=key)

        # 在会话初始化及发现工具成功后记录实际写入尝试。
        def report_send() -> None:

            # region 关联单次写调用与已经保存的提交
            self.emit(RefundEvent.SUBMIT.value, **request.model_dump(mode="json"))
            # endregion

        try:
            result = controlled_tools.send_refund(self.settings.mcp, request, prepared.trace_id, before_send=report_send)
            if (
                # 回执报告成功且携带退款记录。
                result.refund is not None
                # 回执中的业务订单与已提交订单不同。
                and result.refund.order_id != request.order_id
            ):
                raise ValueError("退款回执订单不匹配")
        except controlled_tools.RefundToolUnavailable:

            # region 认证或发现失败且未发送时关闭本次提交
            result = RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.EXECUTION_UNAVAILABLE)
            # endregion

        except Exception:

            # region 已尝试发送且无可信回执时转入核对
            result = RefundResult(status=RefundOutcome.UNCONFIRMED, reason=RefundReason.RECEIPT_UNKNOWN)
            # endregion

        if result.status is RefundOutcome.UNCONFIRMED:
            return self._lookup(request.order_id, prepared.trace_id, key, attempted=True)
        self._record(key, result)
        return result
        # endregion
