import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymysql import OperationalError
from model.order_query import GetOrderRequest, OrderQueryResult
from model.status import OrderQueryStatus, PaymentStatus, RefundStatus, ShippingStatus
from mcp_service.orders.query import query_order


def test_request_rejects_identity_and_invalid_order():
    with pytest.raises(ValidationError):
        GetOrderRequest.model_validate({'order_id': 'DEMO-1001', 'consumer_id': 'DEMO-USER-02'})
    with pytest.raises(ValidationError):
        GetOrderRequest.model_validate({'order_id': ''})


def test_result_cannot_leak_order_on_unavailable():
    with pytest.raises(ValidationError):
        OrderQueryResult.model_validate({'status': 'unavailable', 'order': {
            'order_id': 'DEMO-2001', 'payment_status': 'paid',
            'shipping_status': 'unshipped', 'refund_status': 'not_refunded',
            'paid_amount_fen': 19900, 'currency': 'CNY',
        }})


@pytest.mark.parametrize('payload', [
    {'status': 'found'},
    {'status': 'failed'},
    {'status': 'failed', 'error': '错误', 'order': {
        'order_id': 'DEMO-1001', 'payment_status': 'paid',
        'shipping_status': 'unshipped', 'refund_status': 'not_refunded',
        'paid_amount_fen': 19900, 'currency': 'CNY',
    }},
])
def test_result_rejects_inconsistent_status(payload):
    with pytest.raises(ValidationError):
        OrderQueryResult.model_validate(payload)


def test_database_failure_is_distinct_and_hides_details():
    class BrokenPool:
        def connection(self):
            raise OperationalError(2003, 'sensitive connection details')

    result = query_order(BrokenPool(), GetOrderRequest(order_id='DEMO-1001'), 'DEMO-USER-01')
    assert result.status is OrderQueryStatus.FAILED
    assert result.order is None
    assert 'sensitive' not in str(result)


def test_order_statuses_round_trip_the_existing_payload():
    payload = {'status': 'found', 'error': None, 'order': {
        'order_id': 'DEMO-1001', 'payment_status': 'paid',
        'shipping_status': 'unshipped', 'refund_status': 'not_refunded',
        'paid_amount_fen': 19900, 'currency': 'CNY',
    }}
    result = OrderQueryResult.model_validate(payload)
    assert result.order is not None
    assert result.status is OrderQueryStatus.FOUND
    assert result.order.payment_status is PaymentStatus.PAID
    assert result.order.shipping_status is ShippingStatus.UNSHIPPED
    assert result.order.refund_status is RefundStatus.NOT_REFUNDED
    serialized = result.model_dump(mode='json')
    assert type(serialized['status']) is str
    assert serialized == payload
    assert OrderQueryResult.model_json_schema()['properties']['status'] == {
        'type': 'string', 'enum': ['found', 'unavailable', 'failed'], 'title': 'Status',
    }
    with pytest.raises(ValidationError):
        OrderQueryResult.model_validate({'status': b'unavailable'})
