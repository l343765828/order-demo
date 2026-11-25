"""初始化退款演示数据库并在单一事务内装载演示种子。"""

from pathlib import Path

from pymysql.cursors import Cursor
from sqlalchemy import Engine

from config import MySQLConfig
from config.demo import CONSUMER_ID, DemoSeed
from config.limits import ConnectionLimits, LegacyCurrency, RefundTables
from model.orders.record import DemoSeedOrderId, OrderFacts
from model.orders.status import PaymentStatus, ShippingStatus
from model.refund.status import RefundSource
from model.refund.submission import RefundReceipt
from repositories.order_repository import insert_order
from repositories.refund_repository import insert_refund
from utils.mysql import connection_config, mysql_connection


# 表示历史数据中存在无法按人民币分解释的金额，消息仅列出受影响的表。
class LegacyCurrencyError(ValueError):
    pass


# 在管理连接上验证所有旧币种列，再移除已确认仅包含人民币的列。
def _migrate_legacy_currency(cursor: Cursor) -> None:

    # region 检查是否存在需迁移的历史币种列
    orders, refunds = RefundTables.ORDERS, RefundTables.REFUNDS
    field = LegacyCurrency.FIELD
    cursor.execute(
        "SELECT TABLE_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s, %s) AND COLUMN_NAME=%s",
        (orders, refunds, field),
    )
    if not cursor.fetchone():

        # region 已是新版数据库时直接结束
        return
        # endregion

    # endregion

    # region 锁定两张表，先完成全部校验，再执行删列
    cursor.execute(f"LOCK TABLES {orders} WRITE, {refunds} WRITE")
    try:
        legacy_tables = []
        for table in (orders, refunds):
            cursor.execute(f"SHOW COLUMNS FROM {table} WHERE Field=%s", (field,))
            if cursor.fetchone():

                # region 仅将尚有历史币种列的表纳入校验与删列
                legacy_tables.append(table)
                # endregion

        for table in legacy_tables:
            cursor.execute(
                f"SELECT 1 FROM {table} WHERE {field} IS NULL "
                f"OR CAST({field} AS BINARY) <> CAST(%s AS BINARY) LIMIT 1",
                (LegacyCurrency.CNY,),
            )
            if cursor.fetchone():

                # region 发现非人民币时在任何删列之前中止并报告表名
                raise LegacyCurrencyError(f"{table} 存在非 {LegacyCurrency.CNY} 的历史币种。请核实后再运行初始化。")
                # endregion

        for table in legacy_tables:
            cursor.execute(f"ALTER TABLE {table} DROP COLUMN {field}")
    finally:
        cursor.execute("UNLOCK TABLES")
    # endregion


# 使用独立管理连接补齐演示数据库、应用表及退款外键，并升级历史币种列。
def initialize_demo_database(mysql: MySQLConfig) -> None:

    # region 创建并选中配置指定的演示库
    with mysql_connection(None, **connection_config(mysql)) as connection, connection.cursor() as cursor:
        database = mysql.database.replace("`", "``")
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET {ConnectionLimits.MYSQL_CHARSET} "
            f"COLLATE {ConnectionLimits.MYSQL_DATABASE_COLLATION}"
        )
        connection.select_db(mysql.database)

        # region 按原有顺序补齐应用表和提交记录到退款的外键
        schema = Path(__file__).resolve().parents[2] / "model" / "schema.sql"
        for statement in schema.read_text(encoding="utf-8-sig").split(";"):
            if statement.strip():
                cursor.execute(statement)
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s",
            (RefundTables.DISPATCH_REFUND_FOREIGN_KEY,),
        )
        if cursor.fetchone()[0] == 0:

            # region 外键缺失时补齐退款引用
            cursor.execute(
                f"ALTER TABLE {RefundTables.DISPATCHES} ADD CONSTRAINT {RefundTables.DISPATCH_REFUND_FOREIGN_KEY} "
                f"FOREIGN KEY (refund_id, order_id) REFERENCES {RefundTables.REFUNDS}(refund_id, order_id)"
            )
            # endregion

        # endregion

        # region 将历史金额列升级为人民币分契约
        _migrate_legacy_currency(cursor)
        # endregion

    # endregion


# 使用入口提供的 Engine 管理演示种子装载事务。
class MySQLSeedService:
    # 保存与演示数据库绑定的 Engine。
    def __init__(self, engine: Engine) -> None:

        # region 保存入口注入的 Engine
        self._engine = engine
        # endregion

    # 装载固定样例，仅在配套订单首次插入时同时创建种子退款。
    def seed(self) -> None:

        # region 构造演示订单事实，保留既有样例的顺序、归属和状态
        orders = (
            OrderFacts(order_id=DemoSeedOrderId.PAID.value, consumer_id=CONSUMER_ID,
                       payment_status=PaymentStatus.PAID, shipping_status=ShippingStatus.UNSHIPPED,
                       paid_amount_fen=DemoSeed.PAID_AMOUNT_FEN),
            OrderFacts(order_id=DemoSeedOrderId.UNPAID.value, consumer_id=CONSUMER_ID,
                       payment_status=PaymentStatus.UNPAID, shipping_status=ShippingStatus.UNSHIPPED,
                       paid_amount_fen=DemoSeed.UNPAID_AMOUNT_FEN),
            OrderFacts(order_id=DemoSeedOrderId.REFUNDED.value, consumer_id=CONSUMER_ID,
                       payment_status=PaymentStatus.PAID, shipping_status=ShippingStatus.UNSHIPPED,
                       paid_amount_fen=DemoSeed.REFUNDED_AMOUNT_FEN),
            OrderFacts(order_id=DemoSeedOrderId.SHIPPED.value, consumer_id=CONSUMER_ID,
                       payment_status=PaymentStatus.PAID, shipping_status=ShippingStatus.SHIPPED,
                       paid_amount_fen=DemoSeed.SHIPPED_AMOUNT_FEN),
            OrderFacts(order_id=DemoSeedOrderId.OTHER_CONSUMER.value, consumer_id=DemoSeed.OTHER_CONSUMER_ID,
                       payment_status=PaymentStatus.PAID, shipping_status=ShippingStatus.UNSHIPPED,
                       paid_amount_fen=DemoSeed.OTHER_AMOUNT_FEN),
        )
        # endregion

        # region 在同一事务装载新订单与配套退款，任一写入失败则回滚整批
        with self._engine.begin() as connection:
            for order in orders:
                inserted = insert_order(connection, order=order)
                if (
                    # 当前订单由本次种子装载首次插入。
                    inserted
                    # 当前订单是配有模拟退款的样例。
                    and order.order_id == DemoSeedOrderId.REFUNDED
                ):

                    # region 指定订单首次插入后写入配套种子退款
                    receipt = RefundReceipt(order_id=order.order_id, refund_id=DemoSeed.REFUND_ID,
                                            amount_fen=order.paid_amount_fen)
                    insert_refund(connection, receipt=receipt, key=None, source=RefundSource.SEED)
                    # endregion

        # endregion
