"""在调用方提供的连接上读取订单及关联退款，并写入订单。"""

from typing import Final

from pymysql.constants import ER
from pymysql.err import IntegrityError as MySQLIntegrityError
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from config.limits import RefundTables
from model.orders.query_row import OrderQueryRow
from model.orders.record import OrderFacts, OrderRecord
from utils.sql_loader import load_sql

_GET_ORDER_SQL: Final = "orders/get_order.sql"
_LOCK_ORDER_SQL: Final = "orders/lock_order.sql"
_GET_LOCKED_ORDER_SQL: Final = "orders/get_locked_order.sql"
_INSERT_ORDER_SQL: Final = "orders/insert_order.sql"


# 按订单号和认证消费者绑定参数，返回已验证的持久化记录或 None。
def get_order(connection: Connection, *, order_id: str, consumer_id: str) -> OrderQueryRow | None:

    # region 查询指定消费者的订单与退款记录并校验数据库字段
    row = connection.execute(
        load_sql(_GET_ORDER_SQL, orders=RefundTables.ORDERS, refunds=RefundTables.REFUNDS),
        {"order_id": order_id, "consumer_id": consumer_id},
    ).mappings().one_or_none()
    return OrderQueryRow.model_validate(dict(row)) if row is not None else None
    # endregion


# 在退款事务内锁定本人订单，返回该订单是否可供当前消费者操作。
def lock_order(connection: Connection, *, order_id: str, consumer_id: str) -> bool:

    # region 先锁定本人订单，供后续核对原退款或读取首次写入需要的订单事实
    result = connection.execute(
        load_sql(_LOCK_ORDER_SQL, orders=RefundTables.ORDERS),
        {"order_id": order_id, "consumer_id": consumer_id},
    )
    return result.scalar_one_or_none() is not None
    # endregion


# 在同一事务中读取已锁定的本人订单，以已校验的事实办理首次退款。
def get_locked_order(connection: Connection, *, order_id: str, consumer_id: str) -> OrderRecord:

    # region 用当前读取得并校验加锁时的订单事实
    row = connection.execute(
        load_sql(_GET_LOCKED_ORDER_SQL, orders=RefundTables.ORDERS),
        {"order_id": order_id, "consumer_id": consumer_id},
    ).mappings().one()
    return OrderRecord.model_validate(dict(row))
    # endregion


# 插入订单事实，仅本次插入成功时返回 True，已有主键时保留原订单。
def insert_order(connection: Connection, *, order: OrderFacts) -> bool:

    # region 执行普通插入，通过订单主键的重复错误识别既有订单
    try:
        connection.execute(
            load_sql(_INSERT_ORDER_SQL, orders=RefundTables.ORDERS),
            order.model_dump(mode="json"),
        )
    except IntegrityError as exc:

        # region 订单表仅有订单主键唯一约束，重复键表示已有订单
        if (
            # 完整性异常来自当前使用的 MySQL 驱动。
            isinstance(exc.orig, MySQLIntegrityError)
            # MySQL 报告订单主键已经存在。
            and exc.orig.args[0] == ER.DUP_ENTRY
        ):
            return False
        # endregion

        # region 其他完整性错误交由 Service 回滚整批种子
        raise
        # endregion

    return True
    # endregion
