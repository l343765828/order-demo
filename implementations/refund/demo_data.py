"""初始化退款演示数据库和装载演示种子。"""
from pathlib import Path

import pymysql

from config import load_config
from config.demo import CONSUMER_ID
from model import connection_config
from model.orders.status import PaymentStatus, ShippingStatus
from model.refund.status import DemoDataCommand


# 根据管理命令初始化应用表或装载演示种子。
def manage_demo_data(command: DemoDataCommand) -> None:
    # region 使用独立数据库连接初始化演示库或装载种子数据
    mysql = load_config().mysql
    connection = pymysql.connect(**connection_config(mysql), autocommit=True)
    try:
        with connection.cursor() as cursor:
            match command:
                # region init 补齐缺失的演示库、应用表和退款外键
                case DemoDataCommand.INIT:
                    database = mysql.database.replace("`", "``")
                    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
                    connection.select_db(mysql.database)
                    for statement in (Path(__file__).resolve().parents[2] / "model" / "schema.sql").read_text(encoding="utf-8-sig").split(";"):
                        if statement.strip():
                            cursor.execute(statement)
                    cursor.execute("SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='fk_dispatch_refund'")
                    if not cursor.fetchone()[0]:
                        cursor.execute("ALTER TABLE refund_dispatches ADD CONSTRAINT fk_dispatch_refund FOREIGN KEY (refund_id, order_id) REFERENCES refunds(refund_id, order_id)")
                # endregion

                # region seed 保留已有订单，仅为本次新插入的指定订单写入种子退款
                case DemoDataCommand.SEED:
                    connection.select_db(mysql.database)
                    # 已有业务记录优先；重装种子不会覆盖订单或退款。
                    rows = [("DEMO-1001", CONSUMER_ID, PaymentStatus.PAID, ShippingStatus.UNSHIPPED, 19900),
                            ("DEMO-1002", CONSUMER_ID, PaymentStatus.UNPAID, ShippingStatus.UNSHIPPED, 0),
                            ("DEMO-1003", CONSUMER_ID, PaymentStatus.PAID, ShippingStatus.UNSHIPPED, 8800),
                            ("DEMO-1004", CONSUMER_ID, PaymentStatus.PAID, ShippingStatus.SHIPPED, 29900),
                            ("DEMO-2001", "DEMO-USER-02", PaymentStatus.PAID, ShippingStatus.UNSHIPPED, 5900)]
                    connection.autocommit(False)
                    try:
                        for order_id, consumer_id, payment, shipping, amount in rows:
                            cursor.execute("INSERT INTO orders (order_id,consumer_id,payment_status,shipping_status,paid_amount_fen,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE order_id=orders.order_id",
                                           (order_id, consumer_id, payment.value, shipping.value, amount))
                            if (
                                # 当前订单是预置为已退款的演示样例。
                                order_id == "DEMO-1003"
                                # 本次初始化刚插入该订单，需要同时补入模拟退款记录。
                                and cursor.rowcount == 1
                            ):
                                cursor.execute("INSERT INTO refunds (refund_id,order_id,amount_fen,source,created_at) VALUES ('MOCK-RF-1003','DEMO-1003',8800,'seed',UTC_TIMESTAMP(6))")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                # endregion
    finally:
        connection.close()
    # endregion
