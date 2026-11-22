"""只读核验订单查询；使用既有演示种子，不修改数据库。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import create_pool
from model.orders.query import GetOrderRequest
from model.orders.status import OrderQueryStatus, RefundStatus
from mcp_service.orders.query import query_order


def main():
    pool = create_pool()
    try:
        def find(order_id):
            return query_order(pool, GetOrderRequest(order_id=order_id), 'DEMO-USER-01')

        own = find('DEMO-1001')
        assert own.status is OrderQueryStatus.FOUND and own.order is not None
        assert own.order.paid_amount_fen == 19900
        assert own.order.refund_status is RefundStatus.NOT_REFUNDED
        other, missing = find('DEMO-2001'), find('DEMO-NOT-FOUND')
        assert other == missing and other.status is OrderQueryStatus.UNAVAILABLE and other.order is None
        refunded = find('DEMO-1003')
        assert refunded.status is OrderQueryStatus.FOUND and refunded.order is not None
        assert refunded.order.refund_status is RefundStatus.REFUNDED
        print('own order: 19900 fen; other and missing: identical unavailable; seed refund: found')
    finally:
        pool.close()


if __name__ == '__main__':
    main()
