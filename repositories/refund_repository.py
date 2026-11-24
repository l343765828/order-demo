"""在退款 Service 提供的连接上读取和写入模拟退款记录。"""

from typing import Final

from sqlalchemy import Connection

from config.limits import RefundTables
from model.refund.status import RefundSource
from model.refund.submission import RefundReceipt, RefundRecord
from utils.sql_loader import load_sql

_GET_REFUND_SQL: Final = "refunds/get_refund.sql"
_LOCK_REFUND_SQL: Final = "refunds/lock_refund.sql"
_INSERT_REFUND_SQL: Final = "refunds/insert_refund.sql"


# 只读查询认证消费者及订单对应的退款记录。
def get_refund(connection: Connection, *, order_id: str, consumer_id: str) -> RefundRecord | None:

    # region 根据订单及归属读取原退款并校验持久化字段
    row = connection.execute(
        load_sql(_GET_REFUND_SQL, refunds=RefundTables.REFUNDS, orders=RefundTables.ORDERS),
        {"order_id": order_id, "consumer_id": consumer_id},
    ).mappings().one_or_none()
    return RefundRecord.model_validate(dict(row)) if row is not None else None
    # endregion


# 在已锁定本人订单的事务中锁定并读取该订单的退款记录。
def lock_refund(connection: Connection, *, order_id: str) -> RefundRecord | None:

    # region 在同一事务中锁定原退款并校验持久化字段
    row = connection.execute(load_sql(_LOCK_REFUND_SQL, refunds=RefundTables.REFUNDS),
                             {"order_id": order_id}).mappings().one_or_none()
    return RefundRecord.model_validate(dict(row)) if row is not None else None
    # endregion


# 使用回执金额、来源和可选提交键插入模拟退款，种子退款没有提交键。
def insert_refund(
    connection: Connection, *, receipt: RefundReceipt, key: str | None, source: RefundSource,
) -> None:

    # region 绑定退款回执字段、提交键和来源并执行插入
    connection.execute(
        load_sql(_INSERT_REFUND_SQL, refunds=RefundTables.REFUNDS),
        {**receipt.model_dump(), "idempotency_key": key, "source": source.value},
    )
    # endregion
