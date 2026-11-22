"""Explicit schema initialization and non-destructive demo seed commands."""
import argparse
from pathlib import Path

import pymysql
from config import use_config
from model import connection_config
from model.orders.status import PaymentStatus, ShippingStatus


# 解析管理命令，显式初始化演示数据库结构或装载种子数据。
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="JSON 配置文件路径")
    parser.add_argument("command", choices=("init", "seed"))
    args = parser.parse_args()
    use_config(args.config)
    # region 为显式管理命令建立独立数据库连接
    connection = pymysql.connect(**connection_config(), autocommit=True)
    try:
        with connection.cursor() as cursor:
            # region init 补齐缺失的演示库、应用表和退款外键
            if args.command == "init":
                cursor.execute("CREATE DATABASE IF NOT EXISTS order_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
                cursor.execute("USE order_demo")
                for statement in Path(__file__).with_name("model").joinpath("schema.sql").read_text(encoding="utf-8-sig").split(";"):
                    if statement.strip():
                        cursor.execute(statement)
                cursor.execute("SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='fk_dispatch_refund'")
                if not cursor.fetchone()[0]:
                    cursor.execute("ALTER TABLE refund_dispatches ADD CONSTRAINT fk_dispatch_refund FOREIGN KEY (refund_id, order_id) REFERENCES refunds(refund_id, order_id)")
            # endregion

            # region seed 保留已有订单，仅为本次新插入的指定订单写入种子退款
            else:
                cursor.execute("USE order_demo")
                # 已有业务记录优先；重装种子不会覆盖订单或退款。
                rows = [("DEMO-1001", "DEMO-USER-01", PaymentStatus.PAID, ShippingStatus.UNSHIPPED, 19900),
                        ("DEMO-1002", "DEMO-USER-01", PaymentStatus.UNPAID, ShippingStatus.UNSHIPPED, 0),
                        ("DEMO-1003", "DEMO-USER-01", PaymentStatus.PAID, ShippingStatus.UNSHIPPED, 8800),
                        ("DEMO-1004", "DEMO-USER-01", PaymentStatus.PAID, ShippingStatus.SHIPPED, 29900),
                        ("DEMO-2001", "DEMO-USER-02", PaymentStatus.PAID, ShippingStatus.UNSHIPPED, 5900)]
                connection.autocommit(False)
                try:
                    for order_id, consumer_id, payment, shipping, amount in rows:
                        cursor.execute("INSERT INTO orders (order_id,consumer_id,payment_status,shipping_status,paid_amount_fen,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE order_id=orders.order_id",
                                       (order_id, consumer_id, payment.value, shipping.value, amount))
                        if order_id == "DEMO-1003" and cursor.rowcount == 1:
                            cursor.execute("INSERT INTO refunds (refund_id,order_id,amount_fen,source,created_at) VALUES ('MOCK-RF-1003','DEMO-1003',8800,'seed',UTC_TIMESTAMP(6))")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            # endregion
    finally:
        connection.close()
    # endregion
    print(args.command + " completed")


if __name__ == "__main__":
    main()
