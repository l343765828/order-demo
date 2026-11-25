"""用已确认的业务结果生成确定性模拟退款回复。"""

from collections.abc import Callable

from config.limits import RefundLimits
from implementations.orders.reply import UNAVAILABLE
from model.refund.status import RefundEvent, RefundOutcome, RefundReason
from model.refund.submission import RefundReceipt, RefundResult

_REASON_REPLIES = {
    RefundReason.UNAVAILABLE_ORDER: UNAVAILABLE,
    RefundReason.UNPAID: "退款办理已拒绝。该订单未支付，没有可退的款项。",
    RefundReason.SHIPPED: "退款办理已拒绝。该订单已发货，超出演示退款范围。",
    RefundReason.INVALID_DISPATCH: "退款办理已拒绝。本次授权或提交无效。",
    RefundReason.POLICY_UNAVAILABLE: "政策依据不足或与订单事实不符，未提交退款。",
    RefundReason.POLICY_NOT_SAVED: "暂时无法确认政策核验事件已保存，未发送退款请求。",
    RefundReason.DISPATCH_NOT_SAVED: "暂时无法确认本次提交已保存，未发送退款请求。",
    RefundReason.EXECUTION_UNAVAILABLE: "退款服务暂时不可用，未提交退款。",
    RefundReason.DATABASE_FAILURE: "本次模拟退款执行失败，未产生退款记录。",
    RefundReason.RECEIPT_UNKNOWN: "本次模拟退款结果未确认。该订单需要核对原结果后才能再次办理。",
}


# 根据结构化原单格式化实付整数分及模拟退款单号。
def _format_receipt(title: str, receipt: RefundReceipt) -> str:

    # region 将人民币整数分转换为准确的两位小数并列出原单字段
    yuan, fen = divmod(receipt.amount_fen, RefundLimits.FEN_PER_YUAN)
    return f"{title}。订单 {receipt.order_id}，退款金额 ¥{yuan}.{fen:02d}，模拟退款单号 {receipt.refund_id}。"
    # endregion


# 生成用户回复，模板故障时仍保留已确认的模拟退款结果。
def refund_reply(result: RefundResult, emit: Callable[..., None]) -> str:

    # region 未取得退款记录时按原因区分拒绝、失败和待核对
    receipt = result.refund
    if receipt is None:

        # region 未取得原退款时按原因提示办理状态
        assert result.reason is not None
        return _REASON_REPLIES[result.reason]
        # endregion

    # endregion

    # region 展示成功或既有记录，模板异常时回传真实订单、整数分和原单号
    title = "模拟退款成功" if result.status is RefundOutcome.SIMULATED_SUCCEEDED else "该订单已完成模拟退款"
    try:
        return _format_receipt(title, receipt)
    except Exception as exc:

        # region 模板异常时仍展示真实结果及整数分原值
        emit(RefundEvent.REPLY_FAILED.value, error_type=type(exc).__name__)
        return f"{title}。订单 {receipt.order_id}，退款金额 {receipt.amount_fen} 分，模拟退款单号 {receipt.refund_id}。"
        # endregion

    # endregion
