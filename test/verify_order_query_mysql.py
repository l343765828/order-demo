"""通过真实 MySQL 和独立 MCP 进程验证本人查单。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import load_config
from config.demo import CONSUMER_ID
from implementations.orders.query import MySQLOrderQueryService
from model.orders.query import GetOrderRequest, OrderQueryResult
from model.orders.status import OrderQueryStatus, RefundStatus
from order_mysql_fixture import isolated_order_database, order_mcp_process
from utils.database import create_mysql_engine
from utils.mcp_client import controlled_mcp_http


SPECIAL_ORDER = "隔离'单\\%𠮷"
SPECIAL_CONSUMER = "归属'人\\%𠮷"
FAILURE = {'status': 'failed', 'order': None, 'error': '订单服务暂时不可用'}


def verify_engine_and_binding(mysql, cursor):
    cursor.executemany(
        'INSERT INTO orders (order_id, consumer_id, payment_status, shipping_status, paid_amount_fen, '
        'created_at, updated_at) VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))',
        [(SPECIAL_ORDER, CONSUMER_ID, 'paid', 'unshipped', 12345),
         ('SPECIAL-CONSUMER', SPECIAL_CONSUMER, 'paid', 'unshipped', 23456)],
    )
    engine = create_mysql_engine(mysql)
    try:
        with engine.connect() as connection:
            connection_id, timezone, charset = connection.execute(text(
                'SELECT CONNECTION_ID(), @@session.time_zone, @@session.character_set_connection',
            )).one()
        assert (timezone, charset) == ('+00:00', 'utf8mb4')
        service = MySQLOrderQueryService(engine)
        result = service.query_order(GetOrderRequest(order_id='SPECIAL-CONSUMER'),
                                     authenticated_consumer_id=SPECIAL_CONSUMER)
        assert result.order is not None and result.order.paid_amount_fen == 23456
        assert engine.pool.checkedout() == 0
        with engine.connect() as connection:
            assert connection.execute(text('SELECT CONNECTION_ID()')).scalar_one() == connection_id
        cursor.execute('KILL CONNECTION %s', (connection_id,))
        with engine.connect() as connection:
            assert connection.execute(text('SELECT CONNECTION_ID()')).scalar_one() != connection_id
    finally:
        engine.dispose()
    print('UTC/utf8mb4; consumer value bound; connection returned, reused and replaced after disconnect')


async def verify_mcp(mcp, cursor):
    trace = str(uuid4())
    tool_event = str(uuid4())
    async with httpx.AsyncClient(trust_env=False) as http:
        assert (await http.post(mcp.url, json={})).status_code == 401

    async with controlled_mcp_http() as http:
        http.headers['Authorization'] = f'Bearer {mcp.read_token}'
        async with Client(streamable_http_client(mcp.url, http_client=http), read_timeout_seconds=10) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {'get_order', 'get_refund_result', 'submit_mock_refund', 'search_policy'}
            order_tool = tools['get_order']
            assert order_tool.input_schema['required'] == ['order_id']
            assert set(order_tool.input_schema['properties']) == {'order_id'}
            assert order_tool.output_schema is None and order_tool.annotations.read_only_hint is True

            async def get_order(order_id, **extra):
                result = await client.session.call_tool('get_order', {'order_id': order_id, **extra},
                                                        meta={'trace_id': trace, 'tool_event_id': tool_event})
                assert not result.is_error and result.content == []
                assert result.structured_content is not None
                return OrderQueryResult.model_validate(result.structured_content)

            own = await get_order('DEMO-1001')
            assert own.status is OrderQueryStatus.FOUND and own.order is not None
            assert own.order.paid_amount_fen == 19900 and own.order.refund_status is RefundStatus.NOT_REFUNDED
            refunded = await get_order('DEMO-1003')
            assert refunded.order is not None and refunded.order.refund_status is RefundStatus.REFUNDED
            other = await get_order('DEMO-2001', consumer_id='DEMO-USER-02')
            missing = await get_order('DEMO-NOT-FOUND')
            assert other == missing and other.status is OrderQueryStatus.UNAVAILABLE and other.order is None
            special = await get_order(SPECIAL_ORDER)
            assert special.order is not None and special.order.order_id == SPECIAL_ORDER
            invalid = await client.session.call_tool('get_order', {'order_id': 1001})
            assert invalid.is_error
            assert (await client.session.call_tool('submit_mock_refund', {
                'order_id': 'DEMO-1001', 'idempotency_key': 'absent-dispatch',
            })).is_error
            cursor.execute('UPDATE orders SET currency=%s WHERE order_id=%s', ('USD', SPECIAL_ORDER))
            assert (await get_order(SPECIAL_ORDER)).model_dump(mode='json') == FAILURE
            cursor.execute('RENAME TABLE refunds TO refunds_core01_fault')
            try:
                assert (await get_order('DEMO-1001')).model_dump(mode='json') == FAILURE
            finally:
                cursor.execute('RENAME TABLE refunds_core01_fault TO refunds')
            assert (await get_order('DEMO-1001')) == own

    async with controlled_mcp_http() as http:
        http.headers['Authorization'] = f'Bearer {mcp.write_token}'
        async with Client(streamable_http_client(mcp.url, http_client=http), read_timeout_seconds=10) as client:
            result = await client.session.call_tool('submit_mock_refund', {
                'order_id': 'DEMO-1001', 'idempotency_key': 'absent-dispatch',
            })
            assert not result.is_error and result.structured_content['status'] == 'denied'
    cursor.execute('SELECT (SELECT COUNT(*) FROM refunds), (SELECT COUNT(*) FROM refund_dispatches)')
    assert cursor.fetchone() == (1, 0)
    print('MCP: found/unavailable/failed, exact special order, discovery, auth and isolated write permission passed')
    return trace, tool_event


def main(source):
    with isolated_order_database(source, allow_write=True) as (path, cursor):
        settings = load_config(path)
        verify_engine_and_binding(settings.mysql, cursor)
        with order_mcp_process(path, env={**os.environ, 'PYTHONUTF8': '1'}) as log:
            trace, tool_event = asyncio.run(verify_mcp(settings.mcp, cursor))
            log.seek(0)
            events = [json.loads(line) for line in log.read().decode('utf-8').splitlines() if line.startswith('{')]
        orders = [event for event in events if event['event'] == 'mcp_get_order']
        assert [event['status'] for event in orders] == [
            'found', 'found', 'unavailable', 'unavailable', 'found', 'failed', 'failed', 'found',
        ]
        assert all(event['trace_id'] == trace and event['tool_event_id'] == tool_event
                   and event['mcp_request_id'] is not None and event['pid'] != os.getpid() for event in orders)
        assert sum(event['event'] == 'mcp_auth_rejected' for event in events) == 1
        assert [event['reason'] for event in events if event['event'] == 'mcp_tool_rejected'] == [
            'bad_arguments', 'write_forbidden',
        ]
        assert [event['status'] for event in events if event['event'] == 'mcp_submit_mock_refund'] == ['denied']
        print(f'MCP server PID {orders[0]["pid"]}, trace {trace}; request correlation and sanitized failures passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, help='现有验收 JSON 配置路径')
    main(parser.parse_args().config)
