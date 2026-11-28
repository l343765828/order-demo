"""在真实后端的隔离库和集合验收模拟退款及政策解释。"""

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
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import httpx
import pymysql
from pymilvus import MilvusClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_config, use_config
from config.demo import CONSUMER_ID, DemoPolicy
from implementations.policy.backend import PolicyBackend
from implementations.policy.catalog import MySQLPolicyCatalog
from implementations.policy.source import SOURCE_PATH, parse_policy, read_policy_document
from utils.database import create_mysql_engine
from utils.mysql import connection_config
from model.orders.query import OrderQueryResult
from model.orders.status import PaymentStatus, ShippingStatus
from model.policy.search import PolicySearchResult, SearchPolicyRequest
from model.policy.status import PolicyEligibilityOutcome, PolicyEvent, PolicyOrderFact, PolicySearchStatus
from model.refund.flow import InvestigationPlan, InvestigationStep, OrderToolEvent, PolicyToolEvent
from model.refund.intent import IntentDecision
from model.refund.status import ConversationIntent, InvestigationKind, OrderGoal
from model.refund.status import DispatchStatus, RefundEvent, RefundOutcome, RefundReason
from model.refund.submission import GetRefundResultRequest, RefundResult, SubmitMockRefundRequest
from utils.events import log_event
from utils.mcp_client import bind_mcp_tool, mcp_correlation_meta, open_mcp_client
from launch_with_config import application_command
from policy_condition_checks import extracted_policy_rules
from verify_order_cli import read_events
from verify_policy_catalog import cli_publish

RENAMED_CLAUSE = 'RF-083'
RENAMED_TEXT = '仅限已付款且尚未发货的本人订单按原支付渠道退回整笔实付款，已有成功模拟退款记录的订单除外。'
POLICY_CONDITIONS_GROUP = 'policy-conditions'
EXPLICIT_POLICY_GROUP = 'explicit-policy'
OMITTED_SHIPPING = 'omitted_shipping'
CUTOVER_VERSION = '1.1'
CUTOVER_SOURCE = SOURCE_PATH.with_name(f'refund-policy-{CUTOVER_VERSION}.md')


def application(module, config, *args, fault=None):
    command = application_command(module, config, *args)
    use_probe = module == 'cli' and '--message' in args and any(
        (config.parent / flag).exists() for flag in ('investigation-probe', 'db-faults')
    )
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


def cli_turn(config, prompt, expected, *, fault=None, session=None):
    session_args = ('--session', session) if session else ()
    run = application('cli', config, *session_args, '--message', prompt, fault=fault)
    events = read_events(run.stderr)
    assert expected in run.stdout, (run.stdout, events, run.stderr[-2000:])
    assert events and events[-1]['event'] == 'turn_completed'
    return {'prompt': prompt, 'reply': '\n'.join(run.stdout.splitlines()[1:]), 'events': events,
            'trace_id': events[-1]['trace_id'], 'session_id': events[-1]['session_id']}


def refund_request(order):
    return f'如果能退，就帮我退 {order}'


def seed_order(cursor, order, consumer=CONSUMER_ID):
    cursor.execute(
        "INSERT INTO orders (order_id,consumer_id,payment_status,shipping_status,paid_amount_fen,created_at,updated_at) "
        "VALUES (%s,%s,'paid','unshipped',19900,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (order, consumer),
    )


def seed_source(cursor, order, consumer=CONSUMER_ID):
    session = str(uuid4())
    cursor.execute("INSERT INTO conversation_sessions VALUES (%s,%s,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
                   (session, consumer))
    cursor.execute("INSERT INTO conversation_messages VALUES (%s,1,'user',%s,UTC_TIMESTAMP(6))",
                   (session, refund_request(order)))
    return session


def seed_dispatch(cursor, order, consumer=CONSUMER_ID, revoked=False):
    key, trace = (str(uuid4()) for _ in range(2))
    session = seed_source(cursor, order, consumer)
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
    for name in ('graph_node', 'plan', 'execute', 'observe', 'replan'):
        assert name in names, name
    position = [names.index(name) for name in ('refund_dispatch_saved', 'refund_submit', 'refund_result')]
    assert position == sorted(position)
    assert not {'refund_policy_verified', 'policy_selection', 'policy_eligibility_extraction'}.intersection(names)
    stored, refunds = records(cursor, 'DEMO-1001')
    (key, status, refund_id, trace, error), = stored
    assert (status, trace, error) == ('succeeded', turn['trace_id'], None)
    assert refunds == ((refund_id, 19900, key),)
    assert refund_id in turn['reply']
    assert not list(events_dir.iterdir())
    [authorization] = [event['authorization'] for event in events if event['event'] == 'refund_dispatch_saved']
    assert authorization['session_id'] == turn['session_id'] and authorization['order_id'] == 'DEMO-1001'
    cursor.execute("SELECT m.role,m.content FROM refund_dispatches d JOIN conversation_messages m "
                   "ON m.session_id=d.session_id AND m.sequence=d.source_message_sequence WHERE d.idempotency_key=%s",
                   (key,))
    assert cursor.fetchone() == ('user', turn['prompt'])
    assert not any(event['event'] == 'mcp_search_policy' and event['trace_id'] == trace for event in mcp_events(log))
    write, = submit_calls(log, trace)
    assert write['idempotency_key'] == key and write['pid'] != events[0]['pid']
    return {'case': 'success', **turn, 'policy_calls': 0, 'mcp': [write],
            'database': {'dispatch': stored, 'refund': refunds}}


def assert_exact_policy_citation(evidence, retrieved, version):
    [citation] = evidence['citations']
    [search] = [event for event in retrieved if event['policy_event_id'] == citation['event_id']]
    assert search['catalog_revision'] == evidence['catalog_revision']
    assert citation['version'] == version and citation['entry_type'] == 'policy'
    source = next(item for item in search['ranked'] if all(
        item[key] == citation[key] for key in ('document_id', 'clause_id', 'version', 'entry_type')
    ))
    assert citation['excerpt'] and citation['excerpt'] in source['text']
    return citation


def check_omitted_shipping(config, cursor, log, events_dir):
    order = 'DEMO-1004'
    assert records(cursor, order) == ((), ()) and not list(events_dir.iterdir())
    turn = cli_turn(config, f'请帮我退 {order}', '退款办理已拒绝', fault=OMITTED_SHIPPING)
    assert '已发货' in turn['reply'] and '未提交退款' in turn['reply']
    [intent] = [e['decision'] for e in turn['events'] if e['event'] == 'intent']
    assert intent['intent'] == ConversationIntent.REFUND_REQUEST and intent['order_id'] == order
    [order_facts] = [e['result']['order'] for e in turn['events']
                     if e['event'] == 'mcp_observation' and 'order' in e['result']]
    assert (order_facts['order_id'], order_facts['payment_status'], order_facts['shipping_status']) == (
        order, PaymentStatus.PAID, ShippingStatus.SHIPPED,
    )
    [denial] = [e for e in turn['events'] if e['event'] == RefundEvent.RESULT]
    assert denial['result']['status'] == RefundOutcome.DENIED and denial['result']['reason'] == RefundReason.SHIPPED
    names = [e['event'] for e in turn['events']]
    assert names.index(RefundEvent.RESULT) < names.index(PolicyEvent.ELIGIBILITY_EXTRACTION)
    rules = list(extracted_policy_rules(turn['events']))
    assert rules and all(
        item['clause_id'] == RENAMED_CLAUSE and item['version'] == '2.0'
        and rule['excerpt'] == RENAMED_TEXT and rule['excerpt'] in item['text']
        and set(rule['conditions']) == {PolicyOrderFact.OWNED, PolicyOrderFact.PAID}
        and rule['exceptions'] == [{'conditions': [PolicyOrderFact.REFUNDED]}]
        and rule['outcome'] == PolicyEligibilityOutcome.FULL_ORIGINAL
        for _, rule, item in rules
    ), rules
    searches = [e for e in mcp_events(log) if e['event'] == 'mcp_search_policy' and e['trace_id'] == turn['trace_id']]
    assert {receipt['event_id'] for receipt, _, _ in rules} <= {e['policy_event_id'] for e in searches}
    assert all(e['pid'] != turn['events'][0]['pid'] for e in searches)
    assert not set(names).intersection({RefundEvent.POLICY_VERIFIED, RefundEvent.DISPATCH_SAVED, RefundEvent.SUBMIT})
    assert records(cursor, order) == ((), ()) and not list(events_dir.iterdir())
    assert not submit_calls(log, turn['trace_id'])
    return {'case': OMITTED_SHIPPING, **turn, 'mcp': searches, 'final_policy_events': 0,
            'refund_dispatches': 0, 'refund_records': 0, 'refund_write_calls': 0}


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
                request = httpx.Request('POST', self.reranker.url)
                log_event('probe_rerank_failure', http=503)
                raise httpx.HTTPStatusError('reranker fault', request=request,
                                            response=httpx.Response(503, request=request))
            results = rerank(self, query, candidates)
            if mode == 'empty':
                return []
            if mode in ('contradiction', 'denial_contradiction'):
                text = ('已支付且未发货的本人订单不办理退款。' if mode == 'contradiction'
                        else '已发货的已付款本人订单可以办理整单原路退款。')
                return [results[0].model_copy(update={'text': text})]
            return results

        MilvusClient.search = inspect
        PolicyBackend.rerank = policy_fault
        if (config.parent / 'db-faults').exists():
            install_mcp_db_faults()
    else:
        if (config.parent / 'investigation-probe').exists():
            install_investigation_probe()
        if fault != 'none':
            install_cli_fault(fault, settings)
        if (config.parent / 'db-faults').exists():
            observe_cli_db_boundary(settings)
    sys.argv = [module, *args]
    runpy.run_module(module, run_name='__main__', alter_sys=True)


def install_mcp_db_faults():
    from sqlalchemy import event
    from sqlalchemy.dialects.mysql.pymysql import MySQLDialect_pymysql
    from sqlalchemy.engine import Engine

    commit = MySQLDialect_pymysql.do_commit
    pending_faults = {}
    faults = {
        'DEMO-1027': 'after_insert',
        'DEMO-1028': 'commit_ack',
        'DEMO-1029': 'commit_disconnect',
    }

    @event.listens_for(Engine, 'after_cursor_execute')
    def after_insert(connection, _cursor, statement, _parameters, context, _executemany):
        if not statement.startswith('INSERT INTO refunds'):
            return
        order = context.compiled_parameters[0]['order_id']
        fault = faults.get(order)
        if fault is None:
            return
        log_event('probe_refund_inserted', order_id=order, fault=fault)
        if fault == 'after_insert':
            raise ValueError('injected failure after real insert, before commit')
        pending_faults[connection.connection.dbapi_connection] = (order, fault)

    def commit_with_disconnect(dialect, connection):
        fault = pending_faults.pop(connection.dbapi_connection, None)
        if fault is None:
            return commit(dialect, connection)
        order, mode = fault
        if mode == 'commit_ack':
            commit(dialect, connection)
        connection.dbapi_connection.close()
        log_event('probe_refund_commit_disconnect', order_id=order, committed=mode == 'commit_ack')
        raise pymysql.OperationalError(2013, 'injected disconnect in commit')

    @event.listens_for(Engine, 'connect')
    def shorten_probe_lock_wait(dbapi_connection, _connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute('SET SESSION innodb_lock_wait_timeout=2')

    MySQLDialect_pymysql.do_commit = commit_with_disconnect


def track_open_transactions(engine):
    from sqlalchemy import event

    active = set()
    event.listen(engine, 'begin', active.add)
    event.listen(engine, 'commit', active.discard)
    event.listen(engine, 'rollback', active.discard)
    return active


def observe_cli_db_boundary(settings):
    from implementations.refund import controlled_tools
    from utils import database

    create_engine = database.create_mysql_engine
    active = set()
    send, lookup = controlled_tools.send_refund, controlled_tools.read_refund

    def track_engine(*args, **kwargs):
        nonlocal active
        engine = create_engine(*args, **kwargs)
        active = track_open_transactions(engine)
        return engine

    def check_send(mcp, request, trace, **kwargs):
        assert not active
        with pymysql.connect(database=settings.mysql.database, autocommit=True,
                             **connection_config(settings.mysql)) as db, db.cursor() as cursor:
            stored, refunds = records(cursor, request.order_id)
            assert stored == ((request.idempotency_key, 'pending', None, trace, None),)
            assert not refunds
        log_event('probe_mcp_outside_transaction', trace_id=trace, operation='send')
        return send(mcp, request, trace, **kwargs)

    def check_lookup(mcp, request, trace):
        assert not active
        log_event('probe_mcp_outside_transaction', trace_id=trace, operation='lookup')
        return lookup(mcp, request, trace)

    database.create_mysql_engine = track_engine
    controlled_tools.send_refund = check_send
    controlled_tools.read_refund = check_lookup


def install_investigation_probe():
    from implementations.refund import host
    from implementations.refund.controlled_tools import read_order

    decide = host.QwenDecider.__call__

    def observe(step, trace, emit, *, settings, mode=None):
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
        if schema is IntentDecision:
            message = json.loads(prompt.splitlines()[-1])[-1]['content']
            order = next((word.rstrip('？') for word in message.split() if word.startswith('DEMO-')), None)
            if message.startswith(('查一下', '能否退款', '请帮我退刚才')):
                if message.startswith('请帮我退'):
                    return IntentDecision(intent=ConversationIntent.REFUND_REQUEST, order_id=None, order_goal=None,
                                          order_id_status='missing', order_id_evidence=None,
                                          continues_eligibility_question=False)
                return IntentDecision(intent=ConversationIntent.ORDER_QUESTION, order_id=order,
                                      order_goal=OrderGoal.STATUS if message.startswith('查一下') else OrderGoal.ELIGIBILITY,
                                      order_id_status='extracted', order_id_evidence=order,
                                      continues_eligibility_question=False)
        if schema is InvestigationPlan:
            order = prompt.split()[1]
            assert order.startswith('DEMO-')
            return InvestigationPlan(steps=[InvestigationStep(
                step_id='order', kind=InvestigationKind.INSPECT_ORDER, order_id=order,
                depends_on=[], completion='读取原退款订单状态',
            )])
        return decide(self, schema, prompt)

    host.execute_order = observe
    host.QwenDecider.__call__ = read_only_probe


def install_cli_fault(fault, settings):
    from implementations.refund import controlled_tools, dispatches, reply
    from sqlalchemy.exc import OperationalError

    def ship(order):
        with pymysql.connect(database=settings.mysql.database, autocommit=True,
                             **connection_config(settings.mysql)) as db, db.cursor() as cursor:
            cursor.execute("UPDATE orders SET shipping_status='shipped' WHERE order_id=%s", (order,))
            assert cursor.rowcount == 1

    if fault in ('dispatch_before', 'dispatch_ack'):
        reserve = dispatches.MySQLRefundDispatchService.reserve_dispatch

        def lose_dispatch(*args, **kwargs):
            if fault == 'dispatch_ack':
                assert reserve(*args, **kwargs)
            raise OperationalError(None, None, pymysql.OperationalError(2013, 'injected submission acknowledgement fault'))

        dispatches.MySQLRefundDispatchService.reserve_dispatch = lose_dispatch
    elif fault == 'refetch_shipped':
        read = controlled_tools.read_order

        def read_changed(settings, order, trace):
            ship(order)
            return read(settings, order, trace)

        controlled_tools.read_order = read_changed
    elif fault == 'transaction_shipped':
        send = controlled_tools.send_refund

        def send_after_shipping(settings, request, trace, **kwargs):
            ship(request.order_id)
            return send(settings, request, trace, **kwargs)

        controlled_tools.send_refund = send_after_shipping
    elif fault == OMITTED_SHIPPING:
        from langchain_core.runnables import RunnableLambda

        from model.policy.evidence import EligibilityExtraction
        from utils.qwen_client import QwenChatOpenAI

        structured_output = QwenChatOpenAI.with_structured_output

        def extract_without_shipping(prompt):
            _, _, candidate_json = prompt.rsplit('\n', 1)[-1].partition('：')
            assert candidate_json.startswith('{'), '政策提取提示未按预期提供候选 JSON'
            candidates = json.loads(candidate_json)
            return EligibilityExtraction.model_validate({'rules': [
                {'source_index': int(index), 'excerpt': RENAMED_TEXT,
                 'conditions': [PolicyOrderFact.OWNED, PolicyOrderFact.PAID],
                 'exceptions': [{'conditions': [PolicyOrderFact.REFUNDED]}],
                 'outcome': PolicyEligibilityOutcome.FULL_ORIGINAL}
                for index, lines in candidates.items() if any(RENAMED_TEXT in line for line in lines)
            ]})

        def model_output(self, schema, *args, **kwargs):
            if schema is EligibilityExtraction:
                return RunnableLambda(extract_without_shipping)
            return structured_output(self, schema, *args, **kwargs)

        QwenChatOpenAI.with_structured_output = model_output
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


def check_business(config, cursor, log, record, *, version=DemoPolicy.BASELINE_VERSION):
    session = None
    for order, expected in (
        ('DEMO-1002', '未支付'), ('DEMO-1004', '已发货'), ('DEMO-2001', '未找到可操作的订单'),
        ('DEMO-1003', '¥88.00'),
    ):
        before = records(cursor, order)
        turn = cli_turn(config, refund_request(order), expected, session=session)
        session = turn['session_id']
        assert not submit_calls(log, turn['trace_id'])
        server = mcp_events(log)
        searches = [e for e in server if e['event'] == 'mcp_search_policy' and e['trace_id'] == turn['trace_id']]
        retrieved = [e for e in server if e['event'] == 'policy_retrieval'
                     and e['policy_event_id'] in {search['policy_event_id'] for search in searches}]
        if order in ('DEMO-1002', 'DEMO-1004'):
            assert '退款办理已拒绝' in turn['reply'] and '未提交退款' in turn['reply']
            assert searches and all(e['mode'] == 'formal' and e['pid'] != turn['events'][0]['pid'] for e in searches)
            [evidence] = [e['evidence'] for e in turn['events']
                          if e['event'] == 'policy_evidence' and e['evidence']['status'] == 'sufficient']
            citation = assert_exact_policy_citation(evidence, retrieved, version)
            assert expected in citation['excerpt'] and citation['excerpt'] in turn['reply']
            assert f"{version} 版 {citation['clause_id']}" in turn['reply']
        else:
            assert not searches
        assert records(cursor, order) == before
        if order == 'DEMO-1003':
            assert 'MOCK-RF-1003' in turn['reply']
        record({'case': 'order_guard', **turn, 'mcp': searches, 'policy_retrieval': retrieved,
                'refund_write_calls': 0, 'database': before})


def check_transaction_denial(config, cursor, log, record, *, policy_version=None):
    order = 'DEMO-1015'
    seed_order(cursor, order)
    turn = cli_turn(config, refund_request(order), '已发货', fault='transaction_shipped')
    related = [e for e in mcp_events(log) if e.get('trace_id') == turn['trace_id']]
    [write] = [e for e in related if e['event'] == 'mcp_submit_mock_refund']
    searches = [e for e in related if e['event'] == 'mcp_search_policy']
    assert write['status'] == RefundOutcome.DENIED
    stored, refunds = records(cursor, order)
    assert len(stored) == 1 and stored[0][1:3] == (DispatchStatus.CLOSED_NO_REFUND, None)
    assert stored[0][4] == RefundReason.SHIPPED
    assert not refunds
    [result] = [e['result'] for e in turn['events'] if e['event'] == 'refund_result']
    assert result['status'] == RefundOutcome.DENIED and result['reason'] == RefundReason.SHIPPED
    assert '退款办理已拒绝' in turn['reply'] and '未发货' not in turn['reply'] and '未提交退款' not in turn['reply']
    assert 1 <= len(searches) <= 2
    assert all(related.index(search) > related.index(write) and search['mode'] == 'formal' for search in searches)
    if policy_version is not None:
        retrieved = [e for e in mcp_events(log) if e['event'] == 'policy_retrieval'
                     and e['policy_event_id'] in {search['policy_event_id'] for search in searches}]
        [evidence] = [e['evidence'] for e in turn['events'] if e['event'] == 'policy_evidence'
                      and e['evidence']['status'] == 'sufficient']
        citation = assert_exact_policy_citation(evidence, retrieved, policy_version)
        assert '已发货' in citation['excerpt'] and citation['excerpt'] in turn['reply']
        case = 'transaction_shipped_with_policy'
    else:
        assert [e['status'] for e in searches] == [PolicySearchStatus.FAILED] and '暂时无法核实退款政策' in turn['reply']
        case = 'locked_latest_order'
    record({'case': case, **turn, 'mcp': related, 'database': [stored, refunds],
            'refund_write_calls': 1, 'refund_rows': 0})


def check_amount(config, cursor, log, record):
    seed_order(cursor, 'DEMO-1005')
    turn = cli_turn(config, '请帮我办理 DEMO-1005 的退款，我希望只退 1 元到其他账户，使用其他渠道。', '模拟退款成功')
    assert '¥199.00' in turn['reply'] and len(submit_calls(log, turn['trace_id'])) == 1
    assert records(cursor, 'DEMO-1005')[1][0][1] == 19900
    record({'case': 'amount_and_channel', **turn})


def check_policy_and_storage_faults(config, cursor, log, record):
    marker = config.parent / 'policy-fault'
    dir_events = Path(load_config(config).refund_policy_events_dir)
    seed_order(cursor, 'DEMO-1007')
    seed_dispatch(cursor, 'DEMO-1007', revoked=True)
    for fault, reply, orders in (
        ('rerank', '暂时无法核实退款政策', ('DEMO-1002',)),
        ('empty', '暂时无法核实退款政策', ('DEMO-1004',)),
        ('denial_contradiction', '与办理规则冲突', ('DEMO-1004',)),
    ):
        marker.write_text(fault, encoding='ascii')
        before = set(dir_events.iterdir())
        for sample in orders:
            turn = cli_turn(config, refund_request(sample), reply)
            assert not submit_calls(log, turn['trace_id']) and records(cursor, sample) == ((), ())
            assert set(dir_events.iterdir()) == before
            assert '未提交退款' in turn['reply'] and '依据：《' not in turn['reply']
            assert '退款办理已拒绝' in turn['reply'] and ('未支付' if sample == 'DEMO-1002' else '已发货') in turn['reply']
            searches = [e for e in mcp_events(log) if e.get('trace_id') == turn['trace_id'] and e['event'] == 'mcp_search_policy']
            assert len(searches) == (2 if fault == 'empty' else 1)
            assert all(e['status'] == ('failed' if fault == 'rerank' else 'ok') for e in searches)
            if fault == 'empty':
                assert searches[0]['catalog_revision'] == searches[1]['requested_catalog_revision']
                queries = [e['evidence']['query'] for e in turn['events'] if e['event'] == 'policy_evidence']
                assert len(queries) == 2 and all(sample not in query and '已发货' in query for query in queries)
            if fault == 'denial_contradiction':
                assert any(e['event'] == 'policy_evidence' and e['evidence']['status'] == 'conflict'
                           for e in turn['events'])
            record({'case': fault, **turn, 'mcp': searches, 'database': [[], []], 'refund_write_calls': 0})
    marker.unlink()
    storage_faults = (
        ('dispatch_before', '无法确认本次提交已保存', None),
        ('dispatch_ack', '无法确认本次提交已保存', 'pending'),
    )
    orders = [f'DEMO-103{index}' for index in range(len(storage_faults))]
    for order in orders:
        seed_order(cursor, order)
    for order, (fault, reply, submit_state) in zip(orders, storage_faults):
        turn = cli_turn(config, refund_request(order), reply, fault=fault)
        stored, refunds = records(cursor, order)
        assert not submit_calls(log, turn['trace_id']) and not refunds
        assert [row[1] for row in stored] == ([submit_state] if submit_state else [])
        assert not list(dir_events.iterdir())
        record({'case': fault, **turn, 'dispatch_status': submit_state, 'event_files_added': 0,
                'refund_write_calls': 0, 'refund_rows': len(refunds)})
    marker.write_text('rerank', encoding='ascii')
    try:
        existing = cli_turn(config, refund_request('DEMO-1003'), 'MOCK-RF-1003')
        pending = cli_turn(config, refund_request('DEMO-1007'), '结果未确认')
        read = cli_turn(config, '查一下 DEMO-1003', '有模拟退款记录')
        traces = {existing['trace_id'], pending['trace_id'], read['trace_id']}
        events = [e for e in mcp_events(log) if e.get('trace_id') in traces]
        assert [e['status'] for e in events if e['event'] == 'mcp_get_refund_result'] == ['found', 'not_found']
        assert not any(e['event'] in ('mcp_submit_mock_refund', 'mcp_search_policy') for e in events)
        assert records(cursor, 'DEMO-1007')[0][0][1] == 'pending'
        assert '¥88.00' in existing['reply']
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
        ('DEMO-1027', None, '执行失败', 'failed', 'closed_no_refund', 1),
        ('DEMO-1028', None, '已完成模拟退款', 'already_refunded', 'succeeded', 1),
        ('DEMO-1029', None, '结果未确认', 'unconfirmed', 'unconfirmed', 1),
    )
    cursor.execute("ALTER TABLE refunds ADD CONSTRAINT check_probe_failure CHECK (order_id <> 'DEMO-1025')")
    for order, *_ in cases:
        seed_order(cursor, order)
    for order, fault, reply, outcome, dispatch, count in cases:
        turn = cli_turn(config, refund_request(order), reply, fault=fault)
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
            assert 'mcp_search_policy' not in names
        db_fault_cases = {'DEMO-1027': 'rollback_after_insert', 'DEMO-1028': 'commit_ack_lost',
                          'DEMO-1029': 'commit_disconnect'}
        if order in db_fault_cases:
            insert_event, = [e for e in mcp_events(log)
                             if e['event'] == 'probe_refund_inserted' and e['order_id'] == order]
            assert insert_event['fault'] in ('after_insert', 'commit_ack', 'commit_disconnect')
            if order in ('DEMO-1028', 'DEMO-1029'):
                disconnect, = [e for e in mcp_events(log)
                               if e['event'] == 'probe_refund_commit_disconnect' and e['order_id'] == order]
                assert disconnect['committed'] == (order == 'DEMO-1028')
                assert [(e['event'], e['status']) for e in mcp if e['event'] in (
                    'mcp_submit_mock_refund', 'mcp_get_refund_result',
                )] == [('mcp_submit_mock_refund', 'unconfirmed'),
                       ('mcp_get_refund_result', 'found' if refunds else 'not_found')]
                assert [e['operation'] for e in turn['events'] if e['event'] == 'probe_mcp_outside_transaction'] == [
                    'send', 'lookup',
                ]
                mcp += [disconnect]
            mcp += [insert_event]
        record({'case': db_fault_cases.get(order, fault or 'explicit_failure'), **turn,
                'mcp': mcp, 'database': [stored, refunds]})
        if fault in ('lost_denial', 'template'):
            again = cli_turn(config, refund_request(order), '结果未确认' if fault == 'lost_denial' else refunds[0][0])
            assert not submit_calls(log, again['trace_id']) and records(cursor, order) == (stored, refunds)
            record({'case': f'{fault}_followup', **again})
        if order in ('DEMO-1028', 'DEMO-1029'):
            history = application('cli', config, '--session', turn['session_id'], '--history')
            assert turn['prompt'] in history.stdout and turn['reply'] in history.stdout
            for session in (turn['session_id'], None):
                again = cli_turn(config, refund_request(order), refunds[0][0] if refunds else '结果未确认',
                                 session=session)
                assert (again['session_id'] == turn['session_id']) == (session is not None)
                assert not submit_calls(log, again['trace_id']) and records(cursor, order) == (stored, refunds)
                lookup, = [e for e in mcp_events(log) if e.get('trace_id') == again['trace_id']
                           and e['event'] == 'mcp_get_refund_result']
                assert lookup['status'] == ('found' if refunds else 'not_found')
                suffix = '_reopen' if session else '_new_session'
                record({'case': db_fault_cases[order] + suffix, 'history_restored': bool(session),
                        'mcp': lookup, **again})


def check_lock_contention(config, cursor, record):
    settings = load_config(config)
    order = 'DEMO-1040'
    seed_order(cursor, order)
    key = seed_dispatch(cursor, order)
    with pymysql.connect(database=settings.mysql.database, **connection_config(settings.mysql)) as db, db.cursor() as lock:
        lock.execute('SELECT idempotency_key FROM refund_dispatches WHERE idempotency_key=%s FOR UPDATE', (key,))
        assert lock.fetchone() == (key,)
        blocked = asyncio.run(direct_submission(config, order, key))
        assert (blocked['status'], blocked['reason']) == ('failed', 'database_failure')
        assert records(cursor, order)[0][0][1] == 'pending' and not records(cursor, order)[1]
        db.rollback()
    retried = asyncio.run(direct_submission(config, order, key))
    assert retried['status'] == 'simulated_succeeded'
    assert len(records(cursor, order)[1]) == 1
    record({'case': 'dispatch_row_lock_timeout_and_release', 'blocked': blocked, 'retried': retried})


def check_lookup_scope(config, cursor, record):
    from implementations.refund.controlled_tools import read_refund

    other = 'DEMO-1042'
    seed_order(cursor, other, 'DEMO-USER-02')
    cursor.execute(
        "INSERT INTO refunds (refund_id,order_id,idempotency_key,amount_fen,source,created_at) "
        "VALUES (%s,%s,NULL,19900,'seed',UTC_TIMESTAMP(6))",
        ('MOCK-RF-1042', other),
    )
    settings = load_config(config).mcp
    results = {}
    for order, expected in (('DEMO-1003', 'found'), (other, 'not_found'), ('DEMO-1099', 'not_found')):
        result = read_refund(settings, GetRefundResultRequest(order_id=order, idempotency_key=str(uuid4())),
                             str(uuid4()))
        assert result.status.value == expected
        assert (result.refund.refund_id if result.refund else None) == ('MOCK-RF-1003' if order == 'DEMO-1003' else None)
        results[order] = result.model_dump(mode='json')
    record({'case': 'read_refund_consumer_scope', 'results': results})


def check_legacy_order_facts(config, cursor, record):
    results = {}
    cursor.execute('ALTER TABLE orders ALTER CHECK chk_order_shipping NOT ENFORCED')
    try:
        for order, revoked, existing, status, reason in (
            ('DEMO-1043', True, True, 'already_refunded', None),
            ('DEMO-1044', True, False, 'denied', 'invalid_dispatch'),
            ('DEMO-1045', False, False, 'failed', 'database_failure'),
        ):
            seed_order(cursor, order)
            key = seed_dispatch(cursor, order, revoked=revoked)
            cursor.execute("UPDATE orders SET shipping_status='invalid' WHERE order_id=%s", (order,))
            if existing:
                cursor.execute(
                    "INSERT INTO refunds (refund_id,order_id,idempotency_key,amount_fen,source,created_at) "
                    "VALUES ('MOCK-RF-1043',%s,NULL,19900,'seed',UTC_TIMESTAMP(6))",
                    (order,),
                )
            result = asyncio.run(direct_submission(config, order, key))
            assert (result['status'], result['reason']) == (status, reason), result
            assert (result['refund']['refund_id'] if result['refund'] else None) == ('MOCK-RF-1043' if existing else None)
            assert len(records(cursor, order)[1]) == int(existing)
            results[order] = result
    finally:
        cursor.execute("UPDATE orders SET shipping_status='unshipped' WHERE order_id IN (%s,%s,%s)",
                       ('DEMO-1043', 'DEMO-1044', 'DEMO-1045'))
        cursor.execute('ALTER TABLE orders ALTER CHECK chk_order_shipping ENFORCED')
    record({'case': 'legacy_order_fact_validation_precedence', 'results': results})


def check_host_competition(config, cursor, log, record):
    from implementations.refund import controlled_tools
    from implementations.refund.dispatches import MySQLRefundDispatchService
    from implementations.refund.execution import RefundExecution
    from model.refund.submission import AuthorizedRefundIntent, PreparedRefund
    from utils.database import create_mysql_engine

    settings = load_config(config)
    order = 'DEMO-1041'
    seed_order(cursor, order)
    engine = create_mysql_engine(settings.mysql)
    active = track_open_transactions(engine)
    matching = Event()
    looked_up = Event()
    send, lookup = controlled_tools.send_refund, controlled_tools.read_refund

    def before_send(mcp, request, trace, **kwargs):
        assert matching.wait(15)
        return send(mcp, request, trace, **kwargs)

    def before_lookup(mcp, request, trace):
        result = lookup(mcp, request, trace)
        assert result.status.value == 'not_found'
        looked_up.set()
        matching.set()
        return result

    events = [
        PreparedRefund(
            trace_id=str(uuid4()), idempotency_key=str(uuid4()),
            authorization=AuthorizedRefundIntent(order_id=order, consumer_id=CONSUMER_ID,
                                                 session_id=seed_source(cursor, order), source_message_sequence=1),
        )
        for _ in range(2)
    ]
    try:
        service = MySQLRefundDispatchService(engine)
        executor = RefundExecution(service, settings, lambda *args, **kwargs: None)
        with (patch.object(controlled_tools, 'send_refund', before_send),
              patch.object(controlled_tools, 'read_refund', before_lookup),
              ThreadPoolExecutor(max_workers=2) as pool):
            results = list(pool.map(executor.send, events))
        assert looked_up.is_set()
        assert {result.status for result in results} == {RefundOutcome.SIMULATED_SUCCEEDED, RefundOutcome.UNCONFIRMED}
        stored, refunds = records(cursor, order)
        assert len(stored) == len(refunds) == 1 and stored[0][1] == 'succeeded'
        calls = [e for item in events for e in submit_calls(log, item.trace_id)]
        assert len(calls) == 1 and calls[0]['idempotency_key'] == stored[0][0]
        assert not active
        record({'case': 'host_competing_reservations', 'statuses': sorted(r.status.value for r in results),
                'write_calls': len(calls), 'database': [stored, refunds]})
    finally:
        engine.dispose()


def check_qualification(config, cursor, log, record, events):
    def related_calls(turn):
        return [e for e in mcp_events(log) if e.get('trace_id') == turn['trace_id']
                and e['event'] in ('mcp_search_policy', 'mcp_submit_mock_refund')]

    for case, prompt, expected in (
        ('order_status', '查一下 DEMO-1001', '已支付，未发货'),
        ('eligible_inquiry', '能否退款 DEMO-1001', '符合整单原路退款条件'),
    ):
        turn = cli_turn(config, prompt, expected)
        names = [e['event'] for e in turn['events']]
        assert names.count('order_id_extraction') == 1 and 'policy_eligibility_extraction' not in names
        assert not related_calls(turn) and records(cursor, 'DEMO-1001') == ((), ())
        record({'case': case, **turn, 'policy_calls': 0, 'refund_write_calls': 0, 'refund_rows': 0})

    missing = cli_turn(config, '这笔订单可以退款吗？', '提供要咨询资格')
    completed = cli_turn(config, 'DEMO-1001', '本轮仅解释资格', session=missing['session_id'])
    assert '符合整单原路退款条件' in completed['reply']
    assert [sum(e['event'] == 'order_id_extraction' for e in turn['events']) for turn in (missing, completed)] == [1, 0]
    assert not related_calls(missing) and not related_calls(completed) and records(cursor, 'DEMO-1001') == ((), ())
    record({'case': 'cold_read_only_number_followup', 'missing': missing, 'completed': completed,
            'policy_calls': 0, 'refund_write_calls': 0, 'refund_rows': 0})

    success = check_success(config, cursor, log, events)
    record(success)
    stored, refunds = records(cursor, 'DEMO-1001')
    for case, prompt, expected, order in (
        ('repeat_original_receipt', refund_request('DEMO-1001'), refunds[0][0], 'DEMO-1001'),
        ('missing_current_authorization', '请帮我退刚才那单', '演示订单编号', 'DEMO-1001'),
        ('foreign_order', refund_request('DEMO-2001'), '未找到可操作的订单', 'DEMO-2001'),
    ):
        before = records(cursor, order)
        turn = cli_turn(config, prompt, expected, session=success['session_id'])
        assert not related_calls(turn) and records(cursor, order) == before
        record({'case': case, **turn, 'policy_calls': 0, 'refund_write_calls': 0, 'database': before})
    assert records(cursor, 'DEMO-1001') == (stored, refunds)

    for order, reason, expected in (
        ('DEMO-1002', RefundReason.UNPAID, '未支付'), ('DEMO-1004', RefundReason.SHIPPED, '已发货'),
    ):
        inquiry = cli_turn(config, f'能否退款 {order}', expected)
        preflight = cli_turn(config, refund_request(order), expected)
        assert '按办理规则' in inquiry['reply'] and '退款办理已拒绝' in preflight['reply']
        for turn in (inquiry, preflight):
            assert '暂时无法核实退款政策' in turn['reply'] and '依据：《' not in turn['reply']
            assert [e['status'] for e in related_calls(turn) if e['event'] == 'mcp_search_policy'] == [PolicySearchStatus.FAILED]
        [result] = [e['result'] for e in preflight['events'] if e['event'] == 'refund_result']
        assert result['status'] == RefundOutcome.DENIED and result['reason'] == reason
        assert not any(submit_calls(log, turn['trace_id']) for turn in (inquiry, preflight))
        assert records(cursor, order) == ((), ())
        direct = asyncio.run(direct_submission(config, order, seed_dispatch(cursor, order)))
        assert direct['status'] == RefundOutcome.DENIED and direct['reason'] == reason and not records(cursor, order)[1]
        record({'case': 'same_facts', 'inquiry': inquiry, 'preflight': preflight, 'transaction': direct,
                'order': order, 'refund_rows': 0})

    consultation = cli_turn(config, '退款多久能到账？', '暂时无法核实退款政策')
    assert [e['status'] for e in related_calls(consultation)] == [PolicySearchStatus.FAILED]
    assert '依据：《' not in consultation['reply']
    record({'case': 'cold_policy_unavailable', **consultation, 'mcp': related_calls(consultation)})

    check_transaction_denial(config, cursor, log, record)
    record(check_server(config, cursor))


def check_explicit_policy(config, cursor, log, record, policy_version, *, smoke=False):
    reference_order, mixed_order, fault_order, empty_order = ('DEMO-1041', 'DEMO-1042', 'DEMO-1043', 'DEMO-1044')
    for order in (reference_order, mixed_order, fault_order, empty_order):
        seed_order(cursor, order)

    def check(case, prompt, order, expected, *, session=None, mode=None, sends=0, cite=False, intent_calls=1):
        old_dispatches, old_refunds = records(cursor, order)
        turn = cli_turn(config, prompt, expected, session=session)
        trace = turn['trace_id']
        server = mcp_events(log)
        related = [e for e in server if e.get('trace_id') == trace]
        searches = [e for e in related if e['event'] == 'mcp_search_policy']
        writes = submit_calls(log, trace)
        assert len(writes) == sends
        dispatches, refunds = records(cursor, order)
        assert len(dispatches) - len(old_dispatches) == len(refunds) - len(old_refunds) == sends
        assert sum(e['event'] == 'order_id_extraction' for e in turn['events']) == intent_calls
        assert sum(e['event'] == 'intent' for e in turn['events']) == 1
        if mode is None:
            assert not searches
        else:
            assert 1 <= len(searches) <= 2 and searches[0]['mode'] == mode
            if len(searches) == 2:
                assert searches[1]['mode'] == 'formal'
                assert searches[1]['requested_catalog_revision'] == searches[0]['catalog_revision']
        retrievals = [e for e in server if e['event'] == 'policy_retrieval'
                      and e['policy_event_id'] in {search['policy_event_id'] for search in searches}]
        if sends:
            refund_id, amount, key = refunds[-1]
            assert amount == 19900 and refund_id in turn['reply'] and '¥199.00' in turn['reply']
            assert key == writes[0]['idempotency_key'] and writes[0]['pid'] != turn['events'][0]['pid']
            assert all(related.index(writes[0]) < related.index(search) for search in searches)
        if cite:
            [evidence] = [e['evidence'] for e in turn['events']
                          if e['event'] == 'policy_evidence' and e['evidence']['status'] == 'sufficient']
            if mode == 'formal':
                citation = assert_exact_policy_citation(evidence, retrievals, policy_version)
                assert citation['excerpt'] in turn['reply'] and order not in evidence['query']
            else:
                [citation] = evidence['citations']
                [retrieval] = [e for e in retrievals if e['policy_event_id'] == citation['event_id']]
                assert evidence['catalog_revision'] == retrieval['catalog_revision']
                assert any(all(item[key] == citation[key] for key in ('document_id', 'clause_id', 'version', 'entry_type'))
                           and item['text'] == citation['excerpt'] for item in retrieval['ranked'])
            assert f"{policy_version} 版 {citation['clause_id']}" in turn['reply']
        record({'case': case, **turn, 'mcp': related, 'policy_retrieval': retrievals,
                'refund_writes': len(writes), 'new_refunds': sends})
        return turn['session_id']

    if not smoke:
        check('general_policy', '退款多久能到账？', reference_order, 'FAQ-004', mode='consultation', cite=True)
        check('order_policy', f'{reference_order} 适用哪条退款政策？', reference_order, '符合整单原路退款条件',
              mode='formal', cite=True)
        pending = check('eligibility_missing_order', '这笔订单可以退款吗？', reference_order, '提供要咨询资格')
        check('eligibility_number_only', reference_order, reference_order, '本轮仅解释资格', session=pending, intent_calls=0)
        check('submit_and_policy', f'帮我退 {mixed_order}，顺便告诉我依据。', mixed_order, '模拟退款成功',
              mode='formal', sends=1, cite=True, intent_calls=0)
    session = check('wait_for_policy', f'我想看完条款再决定是否退 {reference_order}。', reference_order, '本轮未提交退款',
                    mode='formal', cite=True)
    check('specific_policy_term', f'先给我看 {reference_order} 模拟退款到账时限的正式条款，我看完再决定是否退款。',
          reference_order, 'RF-008', mode='formal', cite=True)
    if not smoke:
        check('requires_policy', f'如果查得到适用的正式条款就帮我退 {reference_order}。', reference_order, '本轮未提交退款',
              mode='formal', session=session, cite=True)
        marker = config.parent / 'policy-fault'
        try:
            marker.write_text('empty', encoding='ascii')
            check('required_policy_missing', f'如果查得到适用的正式条款就帮我退 {empty_order}。', empty_order,
                  '暂时无法核实退款政策', mode='formal')
            marker.write_text('rerank', encoding='ascii')
            check('submit_and_policy_unavailable', f'帮我退 {fault_order}。另请问适用哪条政策？', fault_order,
                  '暂时无法核实退款政策', mode='formal', sends=1, intent_calls=0)
        finally:
            marker.unlink(missing_ok=True)
    for case, prompt in (('missing_authorization_order', '帮我退'), ('historical_order', '退刚才那单')):
        check(case, prompt, reference_order, '演示订单编号', session=session, intent_calls=0)
    check('later_explicit_authorization', refund_request(reference_order), reference_order, '模拟退款成功',
          session=session, sends=1, intent_calls=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--group', choices=(
        'all', 'business', 'amount', 'safety', 'outcomes', 'qualification', 'policy-handling', POLICY_CONDITIONS_GROUP,
        EXPLICIT_POLICY_GROUP,
    ), default='all')
    parser.add_argument('--investigation-probe', action='store_true')
    args = parser.parse_args()
    if args.investigation_probe and args.group not in ('safety', 'outcomes', 'qualification'):
        parser.error('--investigation-probe requires the safety, outcomes or qualification group')
    business_only = args.group in ('qualification', 'outcomes')
    use_config(args.config)
    settings = json.loads(args.config.read_text(encoding='utf-8'))
    namespace = 'order_demo_r07_' + uuid4().hex[:12]
    mysql_options = connection_config()
    utc_sql = mysql_options.pop('init_command')
    with tempfile.TemporaryDirectory(prefix='issue07-test-') as folder, pymysql.connect(
        autocommit=True, **mysql_options,
    ) as db, db.cursor() as cursor, args.evidence.open('w', encoding='utf-8') as evidence:
        def record(result):
            evidence.write(json.dumps(result, ensure_ascii=False) + '\n')
            evidence.flush()
            print(result['case'], flush=True)

        cursor.execute('SELECT VERSION()')
        mysql_version = cursor.fetchone()[0]
        print(f'SELECT VERSION(): {mysql_version}', flush=True)
        cursor.execute(utc_sql)
        path = Path(folder)
        events = path / 'policy-events'
        events.mkdir()
        if args.investigation_probe:
            (path / 'investigation-probe').touch()
        if not args.smoke and args.group in ('all', 'outcomes'):
            (path / 'db-faults').touch()
        config = path / 'settings.json'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        settings['mysql']['database'] = namespace
        if business_only:
            settings.pop('policy', None)
        else:
            settings['policy'].update(catalog_database=namespace, milvus_collection=namespace)
        settings['mcp'] = {'url': f'http://127.0.0.1:{port}/mcp', 'port': port,
                           'read_token': secrets.token_urlsafe(40), 'write_token': secrets.token_urlsafe(40)}
        settings['refund_policy_events_dir'] = str(events)
        config.write_text(json.dumps(settings), encoding='utf-8')
        isolated_settings = load_config(config)
        backend = None if business_only else PolicyBackend(isolated_settings)
        try:
            application('manage', config, 'init')
            application('manage', config, 'seed')
            application('cli', config, '--init-checkpoints')
            db.select_db(namespace)
            record({'case': 'versions', 'mysql': mysql_version,
                    'milvus': backend.client.get_server_version() if backend else None,
                    'python': sys.version.split()[0], 'mcp': version('mcp'), 'deepagents': version('deepagents'),
                    'langgraph': version('langgraph'), 'isolated_namespace': namespace,
                    'model': isolated_settings.qwen.model, 'investigation_probe': args.investigation_probe})
            renumbered = args.group in ('policy-handling', POLICY_CONDITIONS_GROUP)
            source, policy_version = CUTOVER_SOURCE, CUTOVER_VERSION
            if renumbered:
                original = SOURCE_PATH.read_text(encoding='utf-8')
                baseline = next(item.text.split('\n', 1)[1] for item in parse_policy(original) if item.clause_id == 'RF-003')
                source, policy_version = path / 'renumbered-policy.md', '2.0'
                source.write_text(original.replace('版本：1.0', '版本：2.0').replace('RF-003', RENAMED_CLAUSE)
                                  .replace(baseline.split('。', 1)[0] + '。', RENAMED_TEXT), encoding='utf-8')
            if backend is not None:
                if args.group == 'all':
                    baseline = cli_publish(config, SOURCE_PATH, DemoPolicy.BASELINE_VERSION)
                    assert baseline['catalog_revision'] == 1
                    record({'case': 'baseline_policy_published', 'receipt': baseline})
                published = cli_publish(config, source, policy_version)
                assert published['catalog_revision'] == (2 if args.group == 'all' else 1)
                record({'case': 'policy_published', 'receipt': published})
                if args.group == 'all':
                    engine = create_mysql_engine(isolated_settings.mysql)
                    try:
                        catalog = MySQLPolicyCatalog(engine)
                        for revision, original, expected_version in (
                            (1, SOURCE_PATH, DemoPolicy.BASELINE_VERSION), (2, source, policy_version),
                        ):
                            document = read_policy_document(original.read_text(encoding='utf-8'), version=expected_version)
                            snapshot = catalog.snapshot(None if revision == 2 else revision)
                            assert snapshot.revision == revision and snapshot.versions == [document.target]
                            assert sorted(backend.read_version(document.target), key=lambda e: e.clause_id) == sorted(
                                document.entries, key=lambda e: e.clause_id,
                            )
                            record({'case': 'policy_snapshot', 'snapshot': snapshot.model_dump(),
                                    'source_digest': document.source_digest, 'entries': len(document.entries),
                                    'rf_013': next(e.text for e in document.entries if e.clause_id == 'RF-013')})
                    finally:
                        engine.dispose()
            with mcp_process(config, port) as log:
                if args.group == EXPLICIT_POLICY_GROUP:
                    check_explicit_policy(config, cursor, log, record, policy_version, smoke=args.smoke)
                if args.group == POLICY_CONDITIONS_GROUP:
                    record(check_omitted_shipping(config, cursor, log, events))
                if args.group == 'qualification':
                    check_qualification(config, cursor, log, record, events)
                if args.group in ('all', 'business', 'policy-handling'):
                    record(check_success(config, cursor, log, events))
                    if not renumbered and not args.smoke:
                        record(check_server(config, cursor))
                    if renumbered or not args.smoke:
                        check_business(config, cursor, log, record, version=policy_version)
                    if args.group == 'policy-handling' or (args.group == 'all' and not args.smoke):
                        check_transaction_denial(config, cursor, log, record, policy_version=policy_version)
                if not args.smoke and args.group in ('all', 'business', 'amount'):
                    check_amount(config, cursor, log, record)
                if not args.smoke and args.group in ('all', 'safety', 'policy-handling'):
                    check_policy_and_storage_faults(config, cursor, log, record)
                if not args.smoke and args.group in ('all', 'outcomes'):
                    check_outcomes(config, cursor, log, record)
                    check_lock_contention(config, cursor, record)
                    check_lookup_scope(config, cursor, record)
                    check_legacy_order_facts(config, cursor, record)
                    check_host_competition(config, cursor, log, record)
        finally:
            try:
                if backend is not None:
                    if backend.client.has_collection(namespace):
                        backend.client.drop_collection(namespace)
                    assert not backend.client.has_collection(namespace)
            finally:
                if backend is not None:
                    backend.close()
                cursor.execute(f'DROP DATABASE IF EXISTS `{namespace}`')
                cursor.execute('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (namespace,))
                assert cursor.fetchone() == (0,)
                record({'case': 'cleanup', 'mysql_database_removed': True,
                        'milvus_collection_removed': backend is not None})


if __name__ == '__main__':
    if sys.argv[1:2] == ['--probe']:
        _, _, module, config, fault, *args = sys.argv
        probe(module, Path(config), fault, args)
    else:
        main()
