"""在隔离库通过应用入口验证人民币分契约和历史数据迁移。"""

import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from mcp.client.session import ClientSession

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import load_config, use_config
from config.demo import DemoPolicy
from implementations.policy.backend import PolicyBackend
from implementations.policy.source import SOURCE_PATH
from order_mysql_fixture import isolated_order_database, order_mcp_process
from launch_with_config import application_command
from utils.checkpoint import thread_config
from utils.events import log_event
from verify_order_cli import read_events
from verify_policy_catalog import cli_publish
from verify_refund_submission import records, seed_dispatch, seed_order, submit_calls


def run_manage(config, command, *, legacy_root=None, expected=0):
    if legacy_root is None:
        args = application_command('manage', config, command)
        args[1:1] = ['-X', 'utf8']
    else:
        args = [sys.executable, '-X', 'utf8', str(legacy_root / 'test' / 'launch_with_config.py'),
                'manage', str(config), command]
    run = subprocess.run(args, cwd=legacy_root or ROOT, capture_output=True, encoding='utf-8', timeout=30)
    assert run.returncode == expected, f'manage {command}: exit {run.returncode}, expected {expected}'
    return run.stderr.strip()


def columns(cursor, table):
    cursor.execute(f'SHOW COLUMNS FROM {table}')
    return [row[0] for row in cursor.fetchall()]


def business_rows(cursor):
    cursor.execute('SELECT order_id, consumer_id, payment_status, shipping_status, paid_amount_fen, '
                   'created_at, updated_at FROM orders ORDER BY order_id')
    orders = cursor.fetchall()
    cursor.execute('SELECT refund_id, order_id, idempotency_key, amount_fen, source, created_at '
                   'FROM refunds ORDER BY refund_id')
    refunds = cursor.fetchall()
    cursor.execute('SELECT * FROM refund_dispatches ORDER BY idempotency_key')
    return orders, refunds, cursor.fetchall()


def check_migration(config, cursor, record):
    assert all('currency' in columns(cursor, table) for table in ('orders', 'refunds'))
    before = business_rows(cursor)
    assert before[0][0][:5] == ('DEMO-1001', 'DEMO-USER-01', 'paid', 'unshipped', 19900)
    assert before[1][0][:5] == ('MOCK-RF-1003', 'DEMO-1003', None, 8800, 'seed')
    assert len(before[1]) == 2 and len(before[2]) == 1
    for table, key, row_id in (('refunds', 'refund_id', 'MOCK-RF-1003'), ('orders', 'order_id', 'DEMO-1001')):
        cursor.execute(f"UPDATE {table} SET currency='USD' WHERE {key}=%s", (row_id,))
        cursor.execute(f'SELECT {key}, currency FROM {table} ORDER BY {key}')
        currencies = cursor.fetchall()
        message = run_manage(config, 'init', expected=1)
        assert table in message and 'CNY' in message
        assert all('currency' in columns(cursor, name) for name in ('orders', 'refunds'))
        cursor.execute(f'SELECT {key}, currency FROM {table} ORDER BY {key}')
        assert cursor.fetchall() == currencies
        assert business_rows(cursor) == before
        record({'case': 'migration_rejected', 'table': table, 'message': message,
                'columns_and_business_rows_preserved': True})
        cursor.execute(f"UPDATE {table} SET currency='CNY' WHERE {key}=%s", (row_id,))

    run_manage(config, 'init')
    run_manage(config, 'init')
    assert business_rows(cursor) == before
    schema = {table: columns(cursor, table) for table in ('orders', 'refunds')}
    assert all('currency' not in names for names in schema.values())
    record({'case': 'migrated', 'schema': schema, 'rows_preserved': [len(rows) for rows in before],
            'paid_amount_fen': 19900, 'amount_fen': 8800, 'executed_refund_and_dispatch_preserved': True,
            'repeat_init_passed': True})
    return schema


def count_currency(value):
    if isinstance(value, dict):
        return int('currency' in value) + sum(map(count_currency, value.values()))
    if isinstance(value, list):
        return sum(map(count_currency, value))
    return 0


def run_cli(config, *args):
    run = subprocess.run([sys.executable, '-X', 'utf8', __file__, '--observe-cli', str(config), *args],
                         cwd=ROOT, capture_output=True, encoding='utf-8', timeout=240)
    events = read_events(run.stderr)
    assert run.returncode == 0, (run.returncode, [event['event'] for event in events])
    return run.stdout, events


def observe_cli(config, args):
    use_config(config)
    call_tool = ClientSession.call_tool

    async def observe(self, name, *args, **kwargs):
        response = await call_tool(self, name, *args, **kwargs)
        if name in ('get_order', 'get_refund_result', 'submit_mock_refund') and not response.is_error:
            log_event('probe_currency_mcp_response', tool=name, result=response.structured_content)
        return response

    ClientSession.call_tool = observe
    sys.argv = ['cli', *args]
    runpy.run_module('cli', run_name='__main__', alter_sys=True)


def mcp_results(events, tool):
    return [event['result'] for event in events if event['event'] == 'probe_currency_mcp_response'
            and event['tool'] == tool]


def check_new_checkpoints(saver, session):
    snapshots = list(saver.list(thread_config(session['id'])))
    old, = (item for item in snapshots if item.checkpoint['id'] == session['checkpoint_id'])
    assert count_currency(old.checkpoint['channel_values']) == (4 if not session['next'] else 3)
    current = [item for item in snapshots if item.checkpoint['id'] > old.checkpoint['id']]
    assert current
    assert all(count_currency(item.checkpoint['channel_values']) == 0 for item in current), [
        (item.metadata['step'], count_currency(item.checkpoint['channel_values'])) for item in current
    ]
    return len(current)


def check_live(config, cursor, sessions, record):
    saver = PyMySQLSaver(cursor.connection)
    for kind, session in sessions.items():
        config_thread = thread_config(session['id'])
        snapshot = saver.get_tuple(config_thread)
        assert snapshot.checkpoint['id'] == session['checkpoint_id']
        legacy_count = count_currency(snapshot.checkpoint['channel_values'])
        assert legacy_count == (4 if kind == 'original' else 3)
        history, events = run_cli(config, '--session', session['id'], '--history')
        loaded, = events
        assert loaded['event'] == 'session_loaded' and loaded['checkpoint'] and loaded['trace_id'] == session['trace']
        assert loaded['pending_nodes'] == session['next']
        assert saver.get_tuple(config_thread).checkpoint['id'] == snapshot.checkpoint['id']
        if kind == 'original':
            assert '¥88.00' in history and 'MOCK-RF-1003' in history
        record({'case': 'legacy_checkpoint_read', 'kind': kind, 'pending_nodes': session['next'],
                'legacy_currency_fields': legacy_count, 'history': history.splitlines()[1:]})
    assert records(cursor, 'DEMO-1001') == ((), ())

    # 本人查单和退款原单核对均由 CLI 通过独立 MCP 进程调用。
    reply, events = run_cli(config, '--message', '查一下 DEMO-1001')
    assert '实付 19900 分' in reply
    order, = mcp_results(events, 'get_order')
    assert order['order'] == {'order_id': 'DEMO-1001', 'payment_status': 'paid', 'shipping_status': 'unshipped',
                              'refund_status': 'not_refunded', 'paid_amount_fen': 19900}
    assert type(order['order']['paid_amount_fen']) is int
    record({'case': 'order_cli', 'reply': reply.splitlines()[-1], 'mcp': order})

    original = sessions['original']['id']
    original_rows = records(cursor, 'DEMO-1003')
    reply, events = run_cli(config, '--session', original, '--message', '继续，请帮我退 DEMO-1003')
    assert '¥88.00' in reply and 'MOCK-RF-1003' in reply and '已完成模拟退款' in reply
    receipt = {'order_id': 'DEMO-1003', 'refund_id': 'MOCK-RF-1003', 'amount_fen': 8800}
    lookup, = mcp_results(events, 'get_refund_result')
    assert lookup == {'status': 'found', 'refund': receipt}
    assert type(lookup['refund']['amount_fen']) is int and not mcp_results(events, 'submit_mock_refund')
    assert records(cursor, 'DEMO-1003') == original_rows
    checked = check_new_checkpoints(saver, sessions['original'])
    record({'case': 'original_refund_cli', 'reply': reply.splitlines()[-1], 'mcp': lookup,
            'original_record_preserved': True, 'new_checkpoints_checked': checked, 'new_checkpoint_currency_fields': 0})

    pending = sessions['pending']['id']
    prompt = '继续，请帮我退 DEMO-1001'
    reply, events = run_cli(config, '--session', pending, '--message', prompt)
    assert '模拟退款成功' in reply and '¥199.00' in reply
    result, = mcp_results(events, 'submit_mock_refund')
    assert result['status'] == 'simulated_succeeded'
    dispatches, refunds = records(cursor, 'DEMO-1001')
    (key, status, refund_id, trace, error), = dispatches
    assert status == 'succeeded' and error is None and trace != sessions['pending']['trace']
    assert refunds == ((refund_id, 19900, key),)
    assert result['refund'] == {'order_id': 'DEMO-1001', 'refund_id': refund_id, 'amount_fen': 19900}
    assert type(result['refund']['amount_fen']) is int and refund_id in reply
    assert count_currency(result) == 0
    cursor.execute('SELECT m.sequence,m.content FROM refund_dispatches d JOIN conversation_messages m '
                   'ON m.session_id=d.session_id AND m.sequence=d.source_message_sequence WHERE d.idempotency_key=%s',
                   (key,))
    assert cursor.fetchone() == (2, prompt)
    assert (config.parent / f'{key}.jsonl').is_file()
    current = saver.get_tuple(thread_config(pending)).checkpoint
    assert current['channel_values']['refund_result'] == result
    checked = check_new_checkpoints(saver, sessions['pending'])
    record({'case': 'resumed_refund_cli', 'reply': reply.splitlines()[-1], 'mcp': result,
            'new_authorization_message': prompt, 'refunds_created': len(refunds),
            'new_checkpoints_checked': checked, 'new_checkpoint_currency_fields': 0})
    return events[0]['pid'], trace, key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--legacy-root', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.config.read_text(encoding='utf-8'))
    with args.evidence.open('w', encoding='utf-8') as output:
        def record(result):
            output.write(json.dumps(result, ensure_ascii=False) + '\n')
            output.flush()

        with isolated_order_database(args.config, allow_write=True, initialize=False) as (isolated, cursor):
            isolation = json.loads(isolated.read_text(encoding='utf-8'))
            namespace = isolation['mysql']['database']
            source.update(mysql=isolation['mysql'], mcp=isolation['mcp'], refund_policy_events_dir=str(isolated.parent))
            source['policy'].update(catalog_database=namespace, milvus_collection=namespace)
            isolated.write_text(json.dumps(source), encoding='utf-8')
            run_manage(isolated, 'init', legacy_root=args.legacy_root)
            run_manage(isolated, 'seed', legacy_root=args.legacy_root)
            cursor.connection.select_db(namespace)
            cursor.execute('SELECT VERSION()')
            record({'case': 'mysql_version', 'version': cursor.fetchone()[0]})
            seed_order(cursor, 'DEMO-1090')
            key = seed_dispatch(cursor, 'DEMO-1090')
            cursor.execute("INSERT INTO refunds (refund_id,order_id,idempotency_key,amount_fen,source,created_at) "
                           "VALUES ('OLD-RF-1090','DEMO-1090',%s,19900,'execution',UTC_TIMESTAMP(6))", (key,))
            cursor.execute("UPDATE refund_dispatches SET status='succeeded',refund_id='OLD-RF-1090' "
                           'WHERE idempotency_key=%s', (key,))
            old = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'test' / 'seed_currency_checkpoint.py'),
                                  str(args.legacy_root), str(isolated)],
                                 cwd=args.legacy_root, capture_output=True, encoding='utf-8', timeout=50)
            assert old.returncode == 0, old.stderr
            sessions = json.loads(old.stdout)
            migrated = check_migration(isolated, cursor, record)

            settings = load_config(isolated)
            backend = PolicyBackend(settings)
            try:
                assert not backend.client.has_collection(namespace)
                published = cli_publish(isolated, SOURCE_PATH, DemoPolicy.BASELINE_VERSION)
                record({'case': 'policy_published', 'catalog_revision': published['catalog_revision'],
                        'milvus': backend.client.get_server_version(), 'model': settings.qwen.model})
                with order_mcp_process(isolated, env={**os.environ, 'PYTHONUTF8': '1'}) as log:
                    agent_pid, trace, key = check_live(isolated, cursor, sessions, record)
                    log.seek(0)
                    events = read_events(log.read().decode('utf-8'))
                    calls = [event for event in events if event['event'] == 'mcp_submit_mock_refund']
                    write, = calls
                    assert write['idempotency_key'] == key and write['trace_id'] == trace and write['pid'] != agent_pid
                    assert any(event['event'] == 'mcp_search_policy' and event['trace_id'] == trace for event in events)
                    record({'case': 'independent_mcp', 'agent_pid': agent_pid, 'mcp_pid': write['pid'],
                            'writes': len(calls), 'real_policy_search': True})
            finally:
                try:
                    if backend.client.has_collection(namespace):
                        backend.client.drop_collection(namespace)
                    assert not backend.client.has_collection(namespace)
                finally:
                    backend.close()

        with isolated_order_database(args.config) as (_, cursor):
            fresh = {table: columns(cursor, table) for table in migrated}
            assert fresh == migrated
            assert business_rows(cursor)[1][0][:5] == ('MOCK-RF-1003', 'DEMO-1003', None, 8800, 'seed')
            record({'case': 'fresh_schema', 'schema': fresh, 'seed_amount_fen': 8800})
        record({'case': 'cleanup', 'mysql_databases_removed': True, 'milvus_collection_removed': True})


if __name__ == '__main__':
    if sys.argv[1:2] == ['--observe-cli']:
        observe_cli(sys.argv[2], sys.argv[3:])
    else:
        main()
