"""初始化、装载和重置退款演示数据。"""

from pathlib import Path

from sqlalchemy import Connection, Engine

from config import MySQLConfig
from config.demo import CONSUMER_ID, DemoSeed
from config.limits import ConnectionLimits
from model.orders.record import DemoSeedOrderId, OrderFacts
from model.orders.status import PaymentStatus, ShippingStatus
from model.refund.status import RefundSource
from model.refund.submission import RefundReceipt
from repositories.demo_data_repository import clear_refund_demo
from repositories.order_repository import insert_order
from repositories.refund_repository import insert_refund
from utils.mysql import connection_config, mysql_connection


# 使用独立管理连接创建数据库和当前版本的应用表。
def initialize_demo_database(mysql: MySQLConfig) -> None:

    # region 创建并选中配置指定的演示库
    with mysql_connection(None, **connection_config(mysql)) as connection, connection.cursor() as cursor:
        database = mysql.database.replace("`", "``")
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET {ConnectionLimits.MYSQL_CHARSET} "
            f"COLLATE {ConnectionLimits.MYSQL_DATABASE_COLLATION}"
        )
        connection.select_db(mysql.database)

        schema = Path(__file__).resolve().parents[2] / "model" / "schema.sql"
        for statement in schema.read_text(encoding="utf-8-sig").split(";"):
            if statement.strip():

                # region 执行包含表定义的语句
                cursor.execute(statement)
                # endregion

    # endregion


# 使用入口提供的 Engine 管理演示数据装载和重置事务。
class MySQLSeedService:
    # 保存与演示数据库绑定的 Engine。
    def __init__(self, engine: Engine) -> None:

        # region 保存入口注入的 Engine
        self._engine = engine
        # endregion

    # 装载固定样例，仅在配套订单首次插入时同时创建种子退款。
    def seed(self) -> None:

        # region 在一个事务内补齐演示种子
        with self._engine.begin() as connection:
            self._seed(connection)
        # endregion

    # 清空本退款演示的业务表与图状态，在同一事务中装载新版种子。
    def reset(self) -> None:

        # region 同一事务内清空演示业务表和已存在的检查点，再重建种子数据
        with self._engine.begin() as connection:
            clear_refund_demo(connection)
            self._seed(connection)
        # endregion

    # 使用调用方提供的事务连接写入种子订单及其示例退款。
    def _seed(self, connection: Connection) -> None:

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

        # region 插入订单与配套退款，异常交由调用方回滚
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
