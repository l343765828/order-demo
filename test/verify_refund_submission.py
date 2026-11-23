"""在真实后端的隔离库和集合验收工单 07。"""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from importlib.metadata import version
import json
from pathlib import Path
import runpy
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
import pymysql
from pymilvus import MilvusClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_config, use_config
from config.demo import CONSUMER_ID, DemoPolicy
from implementations.policy.backend import PolicyBackend
from implementations.policy.source import SOURCE_PATH
from model import connection_config
from model.orders.query import OrderQueryResult
from model.policy.search import PolicySearchResult, SearchPolicyRequest
from model.refund.flow import ConversationDecision, InvestigationPlan, InvestigationStep, OrderToolEvent, PolicyToolEvent
from model.refund.status import ConversationIntent, InvestigationKind, OrderGoal
from model.refund.status import RefundOutcome, RefundReason
from model.refund.submission import RefundResult, SubmitMockRefundRequest
from utils.events import log_event
from utils.mcp_client import bind_mcp_tool, mcp_correlation_meta, open_mcp_client
from launch_with_config import application_command
from verify_order_cli import read_events
from verify_policy_catalog import cli_publish


def application(module, config, *args, fault=None):
    command = application_command(module, config, *args)
    use_probe = module == 'cli' and '--message' in args and (config.parent / 'investigation-probe').exists()
    if fault or use_probe:
        command = [sys.executable, '-X', 'utf8', __file__, '--probe', module, str(config), fault or 'none', *args]
    result = subprocess.run(command, cwd=ROOT, text=True, encoding='utf-8', errors='replace',
                            capture_output=True, timeout=300)
    if result.returncode:
        print(result.stdout, result.stderr, file=sys.stderr)
        raise AssertionError(f'{module} exited with {result.returncode}')
    return result


@contextmanager
def mcp_process(config, port):
    log_path = config.parent / 'mcp.log'
    with log_path.open('wb') as log, subprocess.Popen(
        [sys.executable, '-X', 'utf8', __file__, '--probe', 'mcp_service.server', str(config), 'mcp'],
        cwd=ROOT, stderr=log,
    ) as process:
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('MCP did not start')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            yield log_path
        finally:
            process.terminate()
            process.wait(timeout=15)


def mcp_events(log):
    return read_events(log.read_text(encoding='utf-8'))


def cli_turn(config, prompt, expected, *, fault=None):
    run = application('cli', config, '--message', prompt, fault=fault)
    events = read_events(run.stderr)
    assert expected in run.stdout, (run.stdout, events, run.stderr[-2000:])
    assert events and events[-1]['event'] == 'turn_completed'
    return {'prompt': prompt, 'reply': run.stdout.splitlines()[-1], 'events': events,
            'trace_id': events[-1]['trace_id'], 'session_id': events[-1]['session_id']}


def refund_request(order):
    return f'如果能退，就帮我退 {order}'


def seed_order(cursor, order):
    cursor.execute(
        "INSERT INTO orders (order_id,consumer_id,payment_status,shipping_status,paid_amount_fen,created_at,updated_at) "
        "VALUES (%s,%s,'paid','unshipped',19900,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (order, CONSUMER_ID),
    )


def seed_dispatch(cursor, order, consumer=CONSUMER_ID, revoked=False):
    key, session, trace = (str(uuid4()) for _ in range(3))
    cursor.execute("INSERT INTO conversation_sessions VALUES (%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                   (session, consumer))
    cursor.execute("INSERT INTO conversation_messages VALUES (%s,1,'user',%s,UTC_TIMESTAMP(6))",
                   (session, refund_request(order)))
    cursor.execute(
        "INSERT INTO refund_dispatches "
        "(idempotency_key,order_id,session_id,source_message_sequence,status,trace_id,revoked_at,created_at,updated_at) "
        "VALUES (%s,%s,%s,1,'pending',%s,IF(%s,UTC_TIMESTAMP(6),NULL),UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (key, order, session, trace, revoked),
    )
    return key


def records(cursor, order):
    cursor.execute("SELECT idempotency_key,status,refund_id,trace_id,error_code FROM refund_dispatches WHERE order_id=%s "
                   "ORDER BY created_at", (order,))
    dispatches = cursor.fetchall()
    cursor.execute("SELECT refund_id,amount_fen,idempotency_key FROM refunds WHERE order_id=%s", (order,))
    return dispatches, cursor.fetchall()


def submit_calls(log, trace_id):
    return [e for e in mcp_events(log)
            if e['event'] == 'mcp_submit_mock_refund' and e['trace_id'] == trace_id]


def check_success(config, cursor, log, events_dir):
    turn = cli_turn(config, refund_request('DEMO-1001'), '模拟退款成功')
    assert 'DEMO-1001' in turn['reply'] and '¥199.00' in turn['reply']
    events = turn['events']
    names = [event['event'] for event in events]
    for name in ('graph_node', 'plan', 'execute', 'deepagent_delegate', 'react_call', 'mcp_observation', 'replan'):
        assert name in names, name
    position = [names.index(name) for name in ('refund_policy_verified', 'refund_dispatch_saved',
                                               'refund_submit', 'refund_result')]
    assert position == sorted(position)
    assert not any(name in names[position[2] + 1:] for name in ('intent', 'deepagent_delegate', 'react_call', 'policy_selection'))
    stored, refunds = records(cursor, 'DEMO-1001')
    (key, status, refund_id, trace, error), = stored
    assert (status, trace, error) == ('succeeded', turn['trace_id'], None)
    assert refunds == ((refund_id, 19900, key),)
    assert refund_id in turn['reply']
    policy_event = json.loads((events_dir / f'{key}.jsonl').read_text(encoding='utf-8'))
    assert policy_event['trace_id'] == trace and policy_event['idempotency_key'] == key
    authorization = policy_event['authorization']
    assert authorization['session_id'] == turn['session_id'] and authorization['order_id'] == 'DEMO-1001'
    cursor.execute("SELECT m.role,m.content FROM refund_dispatches d JOIN conversation_messages m "
                   "ON m.session_id=d.session_id AND m.sequence=d.source_message_sequence WHERE d.idempotency_key=%s",
                   (key,))
    assert cursor.fetchone() == ('user', turn['prompt'])
    server_events = mcp_events(log)
    recalls = [event for event in server_events if event['event'] == 'probe_vector_recall']
    assert recalls and all('entry_type == "policy"' in event['filter'] for event in recalls)
    policy_calls = [event for event in server_events
                    if event['event'] == 'mcp_search_policy' and event['trace_id'] == trace]
    assert policy_calls and all(e['mode'] == 'formal' and e['pid'] != events[0]['pid'] for e in policy_calls)
    assert {citation['event_id'] for citation in policy_event['evidence']['citations']} <= {
        event['policy_event_id'] for event in policy_calls
    }
    write, = submit_calls(log, trace)
    assert write['idempotency_key'] == key and write['pid'] != events[0]['pid']
    return {'case': 'success', **turn, 'policy_event': policy_event, 'recalls': recalls,
            'mcp': [*policy_calls, write], 'database': {'dispatch': stored, 'refund': refunds}}


async def direct_submission(config, order, key, *, writer=True, arguments=None):
    settings = load_config(config).mcp
    token = settings.write_token if writer else settings.read_token
    async with open_mcp_client(settings.url, token=token, read_timeout_seconds=15) as client:
        if not writer or arguments is not None:
            await client.list_tools()
            result = await client.session.call_tool('submit_mock_refund', arguments or {
                'order_id': order, 'idempotency_key': key,
            })
            assert result.is_error
            return 'tool_error'
        tool = await bind_mcp_tool(client, 'submit_mock_refund', RefundResult.model_validate)
        result = await tool.call(SubmitMockRefundRequest(order_id=order, idempotency_key=key))
        return result.model_dump(mode='json')


def check_server(config, cursor):
    for order in ('DEMO-1010', 'DEMO-1011', 'DEMO-1012', 'DEMO-1013', 'DEMO-1014'):
        seed_order(cursor, order)
    valid = seed_dispatch(cursor, 'DEMO-1010')
    wrong_owner = seed_dispatch(cursor, 'DEMO-1011', 'DEMO-USER-02')
    revoked = seed_dispatch(cursor, 'DEMO-1012', revoked=True)
    results = {'investigator': asyncio.run(direct_submission(config, 'DEMO-1010', valid, writer=False))}
    for name, order, key in (
        ('missing', 'DEMO-1013', str(uuid4())), ('session_owner', 'DEMO-1011', wrong_owner),
        ('different_order', 'DEMO-1013', valid), ('revoked', 'DEMO-1012', revoked),
    ):
        result = asyncio.run(direct_submission(config, order, key))
        assert result['status'] == 'denied' and result['reason'] == 'invalid_dispatch', (name, result)
        results[name] = result
        assert not records(cursor, order)[1]

    async def race():
        return await asyncio.gather(*[direct_submission(config, 'DEMO-1010', valid) for _ in range(2)])

    first, second = asyncio.run(race())
    assert {first['status'], second['status']} == {'simulated_succeeded', 'already_refunded'}
    assert first['refund'] == second['refund']
    cursor.execute("UPDATE refund_dispatches SET status='succeeded', refund_id=%s WHERE idempotency_key=%s",
                   (first['refund']['refund_id'], valid))
    repeated = asyncio.run(direct_submission(config, 'DEMO-1010', valid))
    assert repeated['status'] == 'already_refunded' and repeated['refund'] == first['refund']
    assert len(records(cursor, 'DEMO-1010')[1]) == 1
    results['same_dispatch_concurrent'] = [first, second]
    results['completed_dispatch_receipt'] = repeated
    return {'case': 'direct_mcp', 'results': results}


def probe(module, config, fault, args):
    use_config(config)
    settings = load_config(config)
    assert settings.mysql.database.startswith('order_demo_r07_')
    if module == 'mcp_service.server':
        original = MilvusClient.search
        rerank = PolicyBackend.rerank

        def inspect(self, *args, **kwargs):
            log_event('probe_vector_recall', filter=kwargs['filter'])
            return original(self, *args, **kwargs)

        def policy_fault(self, query, candidates):
            flag = config.parent / 'policy-fault'
            mode = flag.read_text(encoding='ascii') if flag.exists() else ''
            if mode == 'rerank':
                request = httpx.Request('POST', self.base + '/reranker/v1/rerank')
                log_event('probe_rerank_failure', http=503)
                raise httpx.HTTPStatusError('reranker fault', request=request,
                                            response=httpx.Response(503, request=request))
            results = rerank(self, query, candidates)
            if mode == 'empty':
                return []
            if mode == 'contradiction':
                return [row.model_copy(update={'text': '可退款条件\n只有已发货订单才可退款。'})
                        if row.clause_id == 'RF-003' else row for row in results]
            return results

        MilvusClient.search = inspect
        PolicyBackend.rerank = policy_fault
    else:
        if (config.parent / 'investigation-probe').exists():
            install_investigation_probe(settings)
        if fault != 'none':
            install_cli_fault(fault, settings)
    sys.argv = [module, *args]
    runpy.run_module(module, run_name='__main__', alter_sys=True)


def install_investigation_probe(settings):
    from implementations.refund import host
    from implementations.refund.controlled_tools import read_order

    decide = host.QwenDecider.__call__

    def observe(step, trace, emit, *, mode=None):
        event = str(uuid4())
        tool_call = f'probe-{event}'
        if isinstance(step, InvestigationStep):
            emit('probe_readonly_investigation', tool='get_order')
            result = read_order(settings.mcp, step.order_id, trace)
            return OrderToolEvent(event_id=event, trace_id=trace, model_tool_call_id=tool_call,
                                  order_id=step.order_id, result=result)

        async def fetch():
            async with open_mcp_client(settings.mcp.url, token=settings.mcp.read_token,
                                       read_timeout_seconds=90) as client:
                tool = await bind_mcp_tool(client, 'search_policy', PolicySearchResult.from_mcp)
                return await tool.call(SearchPolicyRequest(query=step.query, mode=mode,
                                                           catalog_revision=step.catalog_revision),
                                       meta=mcp_correlation_meta(trace, event))

        emit('probe_readonly_investigation', tool='search_policy')
        return PolicyToolEvent(event_id=event, trace_id=trace, model_tool_call_id=tool_call,
                               query=step.query, result=asyncio.run(fetch()))

    def read_only_probe(self, schema, prompt, /):
        if schema is ConversationDecision:
            assert prompt.strip().endswith('查一下 DEMO-1003')
            return ConversationDecision(intent=ConversationIntent.ORDER_QUESTION, order_id='DEMO-1003',
                                        order_goal=OrderGoal.STATUS)
        if schema is InvestigationPlan:
            assert 'DEMO-1003' in prompt
            return InvestigationPlan(steps=[InvestigationStep(
                step_id='order', kind=InvestigationKind.INSPECT_ORDER, order_id='DEMO-1003',
                depends_on=[], completion='读取原退款订单状态',
            )])
        return decide(self, schema, prompt)

    host.execute_order = observe
    host.QwenDecider.__call__ = read_only_probe


def install_cli_fault(fault, settings):
    from implementations.refund import controlled_tools, dispatches, execution, reply

    def ship(order):
        with pymysql.connect(database=settings.mysql.database, autocommit=True,
                             **connection_config(settings.mysql)) as db, db.cursor() as cursor:
            cursor.execute("UPDATE orders SET shipping_status='shipped' WHERE order_id=%s", (order,))
            assert cursor.rowcount == 1

    if fault in ('event_before', 'event_ack'):
        persist = execution.persist_event

        def lose_event(*args, **kwargs):
            if fault == 'event_ack':
                persist(*args, **kwargs)
            raise OSError('injected event acknowledgement fault')

        execution.persist_event = lose_event
    elif fault in ('dispatch_before', 'dispatch_ack'):
        reserve = dispatches.reserve_dispatch

        def lose_dispatch(*args, **kwargs):
            if fault == 'dispatch_ack':
                assert reserve(*args, **kwargs)
            raise pymysql.OperationalError(2013, 'injected submission acknowledgement fault')

        dispatches.reserve_dispatch = lose_dispatch
    elif fault == 'refetch_shipped':
        read = controlled_tools.read_order

        def read_changed(settings, order, trace):
            ship(order)
            return read(settings, order, trace)

        controlled_tools.read_order = read_changed
    elif fault == 'bad_citation':
        review = execution.RefundExecution.review

        def review_invalid(self, authorization, evidence, *args):
            evidence.citations[0].event_id = str(uuid4())
            return review(self, authorization, evidence, *args)

        execution.RefundExecution.review = review_invalid
    elif fault in ('lost_success', 'lost_denial'):
        send = controlled_tools.send_refund

        def lose_receipt(settings, request, trace, **kwargs):
            if fault == 'lost_denial':
                ship(request.order_id)
            result = send(settings, request, trace, **kwargs)
            assert result.status is (RefundOutcome.SIMULATED_SUCCEEDED if fault == 'lost_success' else RefundOutcome.DENIED)
            log_event('probe_dropped_receipt', trace_id=trace, status=result.status.value)
            raise TimeoutError('injected lost receipt after actual MCP call')

        controlled_tools.send_refund = lose_receipt
    elif fault == 'template':
        def template_failure(*_):
            raise ValueError('injected presentation failure')

        reply._format_receipt = template_failure
    elif fault == 'writer_setup':
        send = controlled_tools.send_refund

        def invalid_credential(settings, request, trace, **kwargs):
            return send(replace(settings, write_token='wrong-' * 7), request, trace, **kwargs)

        controlled_tools.send_refund = invalid_credential
    else:
        raise ValueError(f'Unknown CLI fault: {fault}')


def check_business(config, cursor, log, record):
    for order, expected in (
        ('DEMO-1002', '未支付'), ('DEMO-1004', '已发货'), ('DEMO-2001', '未找到可操作的订单'),
        ('DEMO-1003', '¥88.00'),
    ):
        before = records(cursor, order)
        turn = cli_turn(config, refund_request(order), expected)
        assert not submit_calls(log, turn['trace_id'])
        assert not any(e['event'] == 'mcp_search_policy' and e['trace_id'] == turn['trace_id'] for e in mcp_events(log))
        assert records(cursor, order) == before
        if order == 'DEMO-1003':
            assert 'MOCK-RF-1003' in turn['reply']
        record({'case': 'order_guard', **turn})


def check_amount(config, cursor, log, record):
    seed_order(cursor, 'DEMO-1005')
    turn = cli_turn(config, '请帮我办理 DEMO-1005 的退款，我希望只退 1 元到其他账户，使用其他渠道。', '模拟退款成功')
    assert '¥199.00' in turn['reply'] and len(submit_calls(log, turn['trace_id'])) == 1
    assert records(cursor, 'DEMO-1005')[1][0][1] == 19900
    record({'case': 'amount_and_channel', **turn})


def check_policy_and_storage_faults(config, cursor, log, record):
    marker = config.parent / 'policy-fault'
    dir_events = Path(load_config(config).refund_policy_events_dir)
    order = 'DEMO-1006'
    seed_order(cursor, order)
    seed_order(cursor, 'DEMO-1007')
    seed_dispatch(cursor, 'DEMO-1007', revoked=True)
    for fault, reply in (('rerank', '暂时无法核实退款政策'), ('empty', '当前政策文档未说明'),
                         ('contradiction', '政策依据不足或与订单事实不符')):
        marker.write_text(fault, encoding='ascii')
        before = set(dir_events.iterdir())
        turn = cli_turn(config, refund_request(order), reply)
        assert not submit_calls(log, turn['trace_id']) and records(cursor, order) == ((), ())
        assert set(dir_events.iterdir()) == before
        record({'case': fault, **turn})
    marker.unlink()
    storage_faults = (
        ('bad_citation', '政策依据不足或与订单事实不符', 0, None),
        ('event_before', '无法确认政策核验事件已保存', 0, None),
        ('event_ack', '无法确认政策核验事件已保存', 1, None),
        ('dispatch_before', '无法确认本次提交已保存', 1, None),
        ('dispatch_ack', '无法确认本次提交已保存', 1, 'pending'),
    )
    orders = [f'DEMO-103{index}' for index in range(len(storage_faults))]
    for order in orders:
        seed_order(cursor, order)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(cli_turn, config, refund_request(order), reply, fault=fault)
                   for order, (fault, reply, _, _) in zip(orders, storage_faults)]
    events = [json.loads(path.read_text(encoding='utf-8')) for path in dir_events.glob('*.jsonl')]
    for order, (fault, _, event_count, submit_state), future in zip(orders, storage_faults, futures):
        turn = future.result()
        stored, refunds = records(cursor, order)
        assert not submit_calls(log, turn['trace_id']) and not refunds
        assert [row[1] for row in stored] == ([submit_state] if submit_state else [])
        assert sum(event['trace_id'] == turn['trace_id'] for event in events) == event_count
        record({'case': fault, **turn, 'dispatch_status': submit_state, 'event_files_added': event_count})
        if fault in ('event_before', 'event_ack'):
            # 同一种事件故障依旧不阻止查单及已存在的原退款。
            with ThreadPoolExecutor(max_workers=3) as executor:
                order_view = executor.submit(cli_turn, config, '查一下 DEMO-1003', '有模拟退款记录', fault=fault)
                refund_view = executor.submit(cli_turn, config, refund_request('DEMO-1003'), 'MOCK-RF-1003', fault=fault)
                pending_view = executor.submit(cli_turn, config, refund_request('DEMO-1007'), '结果未确认', fault=fault)
            independent, existing, pending = order_view.result(), refund_view.result(), pending_view.result()
            traces = {item['trace_id'] for item in (independent, existing, pending)}
            direct = [e for e in mcp_events(log) if e.get('trace_id') in traces]
            assert sorted(e['status'] for e in direct if e['event'] == 'mcp_get_refund_result') == ['found', 'not_found']
            assert not any(e['event'] in ('mcp_submit_mock_refund', 'mcp_search_policy') for e in direct)
            record({'case': f'{fault}_read_paths', 'order': independent, 'existing': existing, 'pending': pending,
                    'mcp': direct})
    marker.write_text('rerank', encoding='ascii')
    try:
        existing = cli_turn(config, refund_request('DEMO-1003'), 'MOCK-RF-1003')
        pending = cli_turn(config, refund_request(order), '结果未确认')
        read = cli_turn(config, '查一下 DEMO-1003', '有模拟退款记录')
        traces = {existing['trace_id'], pending['trace_id'], read['trace_id']}
        events = [e for e in mcp_events(log) if e.get('trace_id') in traces]
        assert [e['status'] for e in events if e['event'] == 'mcp_get_refund_result'] == ['found', 'not_found']
        assert not any(e['event'] in ('mcp_submit_mock_refund', 'mcp_search_policy') for e in events)
        assert records(cursor, order)[0][0][1] == 'pending'
        record({'case': 'rerank_failed_read_paths', 'mcp': events, 'existing': existing, 'pending': pending, 'read': read})
    finally:
        marker.unlink()


def check_outcomes(config, cursor, log, record):
    cases = (
        ('DEMO-1021', 'refetch_shipped', '已发货', 'denied', None, 0),
        ('DEMO-1022', 'lost_success', '已完成模拟退款', 'already_refunded', 'succeeded', 1),
        ('DEMO-1023', 'lost_denial', '结果未确认', 'unconfirmed', 'unconfirmed', 1),
        ('DEMO-1024', 'template', '19900 分', 'simulated_succeeded', 'succeeded', 1),
        ('DEMO-1025', None, '执行失败', 'failed', 'closed_no_refund', 1),
        ('DEMO-1026', 'writer_setup', '退款服务暂时不可用', 'failed', 'closed_no_refund', 0),
    )
    cursor.execute("ALTER TABLE refunds ADD CONSTRAINT check_probe_failure CHECK (order_id <> 'DEMO-1025')")
    for order, *_ in cases:
        seed_order(cursor, order)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(cli_turn, config, refund_request(order), reply, fault=fault)
                   for order, fault, reply, *_ in cases]
    for (order, fault, _, outcome, dispatch, count), future in zip(cases, futures):
        turn = future.result()
        result, = [e['result'] for e in turn['events'] if e['event'] == 'refund_result']
        mcp = [e for e in mcp_events(log) if e.get('trace_id') == turn['trace_id']]
        assert result['status'] == outcome
        stored, refunds = records(cursor, order)
        assert [row[1] for row in stored] == ([dispatch] if dispatch else [])
        assert len(submit_calls(log, turn['trace_id'])) == count
        assert bool(refunds) == (dispatch == 'succeeded')
        if fault in ('lost_success', 'lost_denial'):
            names = [e['event'] for e in mcp]
            assert names.index('mcp_submit_mock_refund') < names.index('mcp_get_refund_result')
        record({'case': fault or 'explicit_failure', **turn, 'mcp': mcp, 'database': [stored, refunds]})
        if fault in ('lost_denial', 'template'):
            again = cli_turn(config, refund_request(order), '结果未确认' if fault == 'lost_denial' else refunds[0][0])
            assert not submit_calls(log, again['trace_id']) and records(cursor, order) == (stored, refunds)
            record({'case': f'{fault}_followup', **again})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--group', choices=('all', 'business', 'amount', 'safety', 'outcomes'), default='all')
    parser.add_argument('--investigation-probe', action='store_true')
    args = parser.parse_args()
    if args.investigation_probe and args.group not in ('safety', 'outcomes'):
        parser.error('--investigation-probe requires the safety or outcomes group')
    use_config(args.config)
    settings = json.loads(args.config.read_text(encoding='utf-8'))
    namespace = 'order_demo_r07_' + uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='issue07-test-') as folder, pymysql.connect(
        autocommit=True, **connection_config(),
    ) as db, db.cursor() as cursor, args.evidence.open('w', encoding='utf-8') as evidence:
        def record(result):
            evidence.write(json.dumps(result, ensure_ascii=False) + '\n')
            evidence.flush()
            print(result['case'], flush=True)

        cursor.execute('SELECT VERSION()')
        mysql_version = cursor.fetchone()[0]
        print(f'SELECT VERSION(): {mysql_version}', flush=True)
        assert mysql_version == '8.0.46'
        path = Path(folder)
        events = path / 'policy-events'
        events.mkdir()
        if args.investigation_probe:
            (path / 'investigation-probe').touch()
        config = path / 'settings.json'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        settings['mysql']['database'] = namespace
        settings['policy'].update(catalog_database=namespace, milvus_collection=namespace)
        settings['mcp'] = {'url': f'http://127.0.0.1:{port}/mcp', 'port': port,
                           'read_token': secrets.token_urlsafe(40), 'write_token': secrets.token_urlsafe(40)}
        settings['refund_policy_events_dir'] = str(events)
        config.write_text(json.dumps(settings), encoding='utf-8')
        use_config(config)
        backend = PolicyBackend()
        try:
            application('manage', config, 'init')
            application('manage', config, 'seed')
            application('cli', config, '--init-checkpoints')
            db.select_db(namespace)
            record({'case': 'versions', 'mysql': mysql_version, 'milvus': backend.client.get_server_version(),
                    'python': sys.version.split()[0], 'mcp': version('mcp'), 'deepagents': version('deepagents'),
                    'langgraph': version('langgraph'), 'isolated_namespace': namespace,
                    'investigation_probe': args.investigation_probe})
            published = cli_publish(config, SOURCE_PATH, DemoPolicy.BASELINE_VERSION)
            assert published['catalog_revision'] == 1
            record({'case': 'policy_published', 'receipt': published})
            with mcp_process(config, port) as log:
                if args.group in ('all', 'business'):
                    record(check_success(config, cursor, log, events))
                    if not args.smoke:
                        record(check_server(config, cursor))
                        check_business(config, cursor, log, record)
                if not args.smoke and args.group in ('all', 'business', 'amount'):
                    check_amount(config, cursor, log, record)
                if not args.smoke and args.group in ('all', 'safety'):
                    check_policy_and_storage_faults(config, cursor, log, record)
                if not args.smoke and args.group in ('all', 'outcomes'):
                    check_outcomes(config, cursor, log, record)
        finally:
            try:
                if backend.client.has_collection(namespace):
                    backend.client.drop_collection(namespace)
                assert not backend.client.has_collection(namespace)
            finally:
                backend.close()
                cursor.execute(f'DROP DATABASE IF EXISTS `{namespace}`')
                cursor.execute('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (namespace,))
                assert cursor.fetchone() == (0,)
                record({'case': 'cleanup', 'mysql_database_removed': True, 'milvus_collection_removed': True})


if __name__ == '__main__':
    if sys.argv[1:2] == ['--probe']:
        _, _, module, config, fault, *args = sys.argv
        probe(module, Path(config), fault, args)
    else:
        main()
