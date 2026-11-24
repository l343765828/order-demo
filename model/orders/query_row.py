"""本人查单读取的持久化结果。"""

from model.orders.record import OrderRecord
from model.refund.submission import RefundIdentifier


# 表示完整订单及其关联的唯一模拟退款记录，用于校验 JOIN 结果。
class OrderQueryRow(OrderRecord):
    # 关联的模拟退款单号，未创建任何模拟退款记录时为 None。
    refund_id: RefundIdentifier | None
