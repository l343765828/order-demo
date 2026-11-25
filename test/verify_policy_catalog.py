"""在目标后端的随机命名空间验收政策发布、恢复和 Host 读取。"""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, nullcontext
import json
from pathlib import Path
from queue import Queue
import subprocess
import sys
import tempfile
from threading import Barrier, Thread
import time
from unittest.mock import patch
from uuid import uuid4

import pymysql
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_config, use_config
from config.demo import DemoPolicy
from config.limits import PolicyCatalogTables
from implementations.policy.backend import PolicyBackend
from implementations.policy.catalog import MySQLPolicyCatalog, PolicyPublicationSession, PublicationConflict
from implementations.policy.evidence import verify_policy_evidence
from implementations.policy.publish import publish_policy
from implementations.policy.source import SOURCE_PATH, read_policy_document
from mcp_service.policy.search import search_policy
from utils.mysql import connection_config
from model.policy.catalog import (
    PolicyPublicationApproval, PolicyVersion, PolicyWriteState, PublicationStatus, QualificationConfirmation,
)
from model.policy.evidence import PolicyEvidence
from model.policy.search import PolicySearchResult, SearchPolicyRequest
from model.policy.status import PolicyEntryType, PolicySearchMode, PolicySearchStatus
from utils.database import create_mysql_engine
from utils.mcp_client import bind_mcp_tool, open_mcp_client
from launch_with_config import application_command
from order_mysql_fixture import isolated_order_database
from verify_conversation_mysql import assert_transaction_ended
from verify_order_cli import read_events
from verify_policy_cli import server

DOC = 'POLICY-RECEIPT-PROBE'
QUESTION = '模拟退款回执可作为商家报销凭证吗？'
FAQ_ANSWER_V1 = '模拟退款回执只可用于课堂记录，不能用作商家报销凭证。'
FAQ_ANSWER_V2 = '模拟退款回执仅供教学核对，不能用作商家报销凭证。'
FORMAL_QUERY = '订单属于当前消费者、已支付、未发货且尚未成功退款时，可以办理整单原路退款吗？'
REMOVED = '仅教学使用的模拟退款回执印有蓝色教学印章。'
OLD_SUPPLEMENT = '模拟退款回执上的蓝色教学印章是什么意思？'


def source(version, answer=FAQ_ANSWER_V2, *, old_clause=False):
    removal = f'\n## RF-015 — 蓝色教学印章\n\n{REMOVED}\n' if old_clause else ''
    baseline = read_policy_document(SOURCE_PATH.read_text(encoding='utf-8'), version=DemoPolicy.BASELINE_VERSION)
    heading, body = next(item.text.split('\n', 1) for item in baseline.entries if item.clause_id == 'RF-003')
    return (
        f'# 模拟退款回执补充政策\n\n文档编号：{DOC}\n版本：{version}\n\n'
        f'## RF-003 — {heading}\n\n{body}\n'
        f'{removal}\n## FAQ-001 — {QUESTION}\n\n{answer}\n'
    )


def release(catalog, index, document, **kwargs):
    return publish_policy(document, publisher='issue16-verification', catalog=catalog, open_backend=lambda: nullcontext(index),
                          qualification=kwargs.pop('qualification', QualificationConfirmation.UNCHANGED), **kwargs)


def cli_publish(config, path, version, *, qualification='unchanged', expected=0):
    result = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'publish_policy.py'), '--config', str(config), '--source',
                             str(path), '--version', version, '--publisher', 'issue16-verification',
                             '--qualification', qualification], capture_output=True, encoding='utf-8', timeout=180)
    assert result.returncode == expected, (result.returncode, result.stderr)
    return json.loads(result.stdout) if expected == 0 else result.stderr.strip()


@contextmanager
def host(config):
    output = Queue()
    setup = subprocess.run(application_command('cli', config, '--init-checkpoints'), cwd=ROOT,
                           capture_output=True, timeout=30)
    assert setup.returncode == 0, 'failed to initialize isolated checkpoints'
    with tempfile.TemporaryFile() as log, subprocess.Popen(
        application_command('cli', config), cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=log, encoding='utf-8', bufsize=1,
    ) as process:
        def pump():
            for line in process.stdout:
                output.put(line.strip())
            output.put(None)

        Thread(target=pump, daemon=True).start()
        try:
            session_line = output.get(timeout=45)
            assert session_line and session_line.startswith('会话：'), session_line

            def turn(question, expected):
                process.stdin.write(question + '\n')
                process.stdin.flush()
                lines = []
                # 一轮调查包含多次真实模型请求，等待覆盖整轮回复。
                while line := output.get(timeout=300):
                    lines.append(line.removeprefix('你：'))
                    if expected in line:
                        return '\n'.join(lines)
                    if '依据：《' in line or '当前政策文档未说明' in line or '暂时无法核实退款政策' in line:
                        break
                raise AssertionError((question, expected, lines))

            yield process.pid, turn, log
            process.stdin.close()
            assert process.wait(timeout=15) == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)


async def check_receipts(settings, catalog, index, update, turn, evidence, probe):
    cfg = load_config(settings).mcp
    async with open_mcp_client(cfg.url, token=cfg.read_token, read_timeout_seconds=90) as client:
        tool = await bind_mcp_tool(client, 'search_policy', PolicySearchResult.from_mcp)

        async def call(query, mode=PolicySearchMode.CONSULTATION, revision=None):
            return await tool.call(SearchPolicyRequest(query=query, mode=mode, catalog_revision=revision))

        before = await call('退款回执上蓝色印章的含义是什么？')
        assert before.catalog_revision == 2 and before.status is PolicySearchStatus.OK
        both = await call(FORMAL_QUERY, PolicySearchMode.FORMAL, before.catalog_revision)
        assert {(item.document_id, item.version) for item in both.candidates if item.clause_id == 'RF-003'} == {
            (DOC, '1.0'), (DemoPolicy.DOCUMENT_ID, '1.0')}, both.model_dump(mode='json')
        first_reply = await asyncio.to_thread(turn, QUESTION, '《模拟退款回执补充政策》1.0 版 FAQ-001')
        assert FAQ_ANSWER_V1 in first_reply
        eligibility = await asyncio.to_thread(turn, 'DEMO-1001 能退吗？', '1.0 版 RF-003')
        assert '已支付、未发货且尚未退款' in eligibility
        clause = next(item for item in both.candidates if item.clause_id == 'RF-003')
        assert clause.text.split('\n', 1)[1].split('。', 1)[0] in eligibility
        pending = asyncio.create_task(asyncio.to_thread(turn, OLD_SUPPLEMENT, '《模拟退款回执补充政策》1.0 版 RF-015'))
        try:
            deadline = time.monotonic() + 300
            while not (probe / 'before-supplement').exists():
                if pending.done():
                    await pending
                    raise AssertionError('Host returned without reaching the formal supplement')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Host did not reach the formal supplement')
                await asyncio.sleep(.05)
            assert (probe / 'before-supplement').read_text(encoding='ascii') == '2'
            assert (await asyncio.to_thread(release, catalog, index, update)).catalog_revision == 3
        finally:
            (probe / 'resume').touch()
        pinned_reply = await pending
        assert REMOVED in pinned_reply
        old = await call(OLD_SUPPLEMENT, PolicySearchMode.FORMAL, before.catalog_revision)
        new = await call(OLD_SUPPLEMENT, PolicySearchMode.FORMAL)
        unknown = await call(OLD_SUPPLEMENT, PolicySearchMode.FORMAL, 99)
        assert old.catalog_revision == 2 and any(item.text.endswith(REMOVED) for item in old.candidates)
        assert new.catalog_revision == 3 and all(item.clause_id != 'RF-015' for item in new.candidates)
        assert unknown.status is PolicySearchStatus.FAILED and unknown.catalog_revision is None
        other_versions = [[item.version for item in catalog.snapshot(rev).versions if item.document_id != DOC]
                          for rev in (2, 3)]
        assert other_versions == [['1.0'], ['1.0']]
        second_reply = await asyncio.to_thread(turn, QUESTION, '《模拟退款回执补充政策》2.0 版 FAQ-001')
        assert FAQ_ANSWER_V2 in second_reply
        insufficient = await asyncio.to_thread(turn, '退款政策支持把积分兑换航空里程吗？', '当前政策文档未说明')
        evidence.append({'case': 'snapshot_and_host',
                         'replies': [first_reply, eligibility, pinned_reply, second_reply, insufficient],
                         'receipts': [r.model_dump(mode='json') for r in (before, both, old, new, unknown)]})


def check_faults(catalog, index, config, evidence, engine, cursor):
    partial = read_policy_document(source('3.0', old_clause=True), version='3.0')
    corrected = read_policy_document(source('3.0'), version='3.0')
    live_reads = []
    connections = []

    @event.listens_for(engine, 'begin')
    def observe_transaction(connection):
        connections.append(connection)

    def check_write_boundary(target, expected):
        connection = connections[-1]
        assert_transaction_ended(connection)
        cursor.execute(f'SELECT write_state FROM {PolicyCatalogTables.RELEASES} WHERE document_id=%s AND version=%s',
                       (target.document_id, target.version))
        assert cursor.fetchone() == (expected.value,)

    def check_live(expected_revision, expected_version):
        result = search_policy(QUESTION, PolicySearchMode.CONSULTATION, index, catalog=catalog)
        assert result.status is PolicySearchStatus.OK and result.catalog_revision == expected_revision
        active = [item for item in result.candidates if item.document_id == DOC]
        assert active and all(item.version == expected_version for item in active)
        live_reads.append(result.model_dump(mode='json'))

    def partial_write(entries):
        check_write_boundary(partial.target, PolicyWriteState.PENDING)
        original_write(entries[:2])
        raise ConnectionError('injected after a partial write was acknowledged by Milvus')

    def clear_without_transaction(target):
        check_write_boundary(target, PolicyWriteState.PENDING)
        return original_clear(target)

    original_clear = index.clear_version
    original_write = index.publish
    with patch.object(index, 'clear_version', clear_without_transaction), patch.object(index, 'publish', partial_write):
        try:
            release(catalog, index, partial)
        except ConnectionError:
            assert catalog.snapshot().revision == 3 and len(index.read_version(partial.target)) == 2
            check_live(3, '2.0')
        else:
            raise AssertionError('partial write did not fail')
    with catalog.lock_version(partial.target) as attempt:
        record = attempt.release()
        assert record.write_state is PolicyWriteState.PENDING and record.activated_revision is None
    try:
        release(catalog, index, corrected)
    except ValueError as error:
        assert '--confirm-write-ended' in str(error)
    else:
        raise AssertionError('unconfirmed write was retried')
    def rerank_without_transaction(*args, **kwargs):
        check_write_boundary(corrected.target, PolicyWriteState.FINISHED)
        return original_rerank(*args, **kwargs)

    original_rerank = index.rerank
    with patch.object(index, 'rerank', rerank_without_transaction):
        result = release(catalog, index, corrected, confirm_write_ended=True)
    assert result.catalog_revision == 4 and set(item.clause_id for item in index.read_version(corrected.target)) == {'RF-003', 'FAQ-001'}

    invalid = read_policy_document(source('4.0', old_clause=True), version='4.0')
    valid = read_policy_document(source('4.0'), version='4.0')
    def fail_readback(target):
        check_write_boundary(target, PolicyWriteState.FINISHED)
        return []

    with patch.object(index, 'read_version', fail_readback):
        try:
            release(catalog, index, invalid)
        except ValueError as error:
            assert '回读' in str(error) and catalog.snapshot().revision == 4
            check_live(4, '3.0')
        else:
            raise AssertionError('verification failure activated a version')
    assert len(index.read_version(invalid.target)) == 3
    with catalog.lock_version(invalid.target) as attempt:
        record = attempt.release()
        assert record.write_state is PolicyWriteState.FINISHED and record.activated_revision is None

    original_activate = PolicyPublicationSession.activate

    def lose_receipt(self, *args, **kwargs):
        assert original_activate(self, *args, **kwargs) == 5
        raise ConnectionError('injected after actual commit')

    with patch.object(PolicyPublicationSession, 'activate', lose_receipt):
        try:
            release(catalog, index, valid)
        except ValueError as error:
            assert '回执未确认' in str(error) and catalog.snapshot().revision == 5
        else:
            raise AssertionError('missing receipt was not reported')
    with patch.object(index, 'clear_version', side_effect=AssertionError('would clear published history')):
        for document, version in ((valid, 5), (corrected, 4)):
            retry = release(catalog, index, document)
            assert retry.status is PublicationStatus.ALREADY_ACTIVATED and retry.catalog_revision == version
        assert len(index.read_version(valid.target)) == 2
        try:
            release(catalog, index, invalid)
        except ValueError as error:
            assert '曾经生效' in str(error)
        else:
            raise AssertionError('activated version was changed')

    barrier = Barrier(2, timeout=90)

    class RacingBackend(PolicyBackend):
        def read_version(self, target):
            rows = super().read_version(target)
            barrier.wait()
            return rows

    def race(version):
        racing = RacingBackend(config)
        try:
            doc = read_policy_document(source(version), version=version)
            return release(catalog, racing, doc).model_dump(mode='json')
        except PublicationConflict as error:
            return {'status': 'conflict', 'version': version, 'detail': str(error)}
        finally:
            racing.close()

    with catalog.lock_version(valid.target):
        try:
            release(catalog, index, valid)
        except PublicationConflict as error:
            assert '已有发布操作' in str(error)
        else:
            raise AssertionError('same-version writer was not excluded')
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(race, ['5.0', '6.0']))
    assert {item['status'] for item in outcomes} == {'activated', 'conflict'} and catalog.snapshot().revision == 6
    evidence.append({'case': 'retry_and_conflict', 'old_revision_on_partial_write': 3, 'partial_rows': 2,
                     'retry_revision': 4, 'old_revision_on_readback_failure': 4, 'committed_without_receipt': 5,
                     'same_version_locked': True, 'conflict': outcomes, 'active_revision': 6,
                     'queries_before_switch': live_reads,
                     'transaction_ended_at': ['clear_version', 'publish', 'read_version', 'rerank'],
                     'externally_visible_states': ['pending', 'finished']})
    event.remove(engine, 'begin', observe_transaction)


def check_mysql_boundaries(catalog, engine, cursor, namespace, evidence):
    from hashlib import sha256

    target = PolicyVersion(document_id='MYSQL-BOUNDARY-PROBE', version='1.0')
    approval = PolicyPublicationApproval(source_digest='0' * 64, publisher='mysql-boundary-probe',
                                         qualification=QualificationConfirmation.UNCHANGED)
    key = sha256(f'policy:{namespace}:{target.document_id}:{target.version}'.encode()).hexdigest()

    def lock_owner():
        cursor.execute('SELECT IS_USED_LOCK(%s)', (key,))
        return cursor.fetchone()[0]

    with catalog.lock_version(target) as attempt:
        connection = attempt.connection
        owner = connection.connection.driver_connection.thread_id()
        assert lock_owner() == owner
        attempt.record_attempt(approval, write_state=PolicyWriteState.PENDING)
        assert_transaction_ended(connection)
        assert lock_owner() == owner
        attempt.finish_write()
        assert_transaction_ended(connection)
        assert lock_owner() == owner
        cursor.execute(f"CREATE TRIGGER probe_head_failure BEFORE UPDATE ON {PolicyCatalogTables.HEAD} "
                       "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='injected directory head failure'")
        try:
            try:
                attempt.activate(None)
            except DBAPIError as error:
                assert error.orig.args[0] == 1644
            else:
                raise AssertionError('switch failed to roll back')
        finally:
            cursor.execute('DROP TRIGGER probe_head_failure')
        assert_transaction_ended(connection)
        assert attempt.release().activated_revision is None and catalog.snapshot().revision == 2
    assert lock_owner() is None

    def lose_unlock_receipt(_connection, _cursor, statement, parameters, _context, _many):
        return ('SELECT 0', ()) if 'RELEASE_LOCK' in statement else (statement, parameters)

    event.listen(engine, 'before_cursor_execute', lose_unlock_receipt, retval=True)
    try:
        with catalog.lock_version(target) as attempt:
            discarded = lock_owner()
            assert discarded == attempt.connection.connection.driver_connection.thread_id()
    finally:
        event.remove(engine, 'before_cursor_execute', lose_unlock_receipt)
    cursor.execute('SELECT GET_LOCK(%s, 2)', (key,))
    assert cursor.fetchone() == (1,)
    cursor.execute('SELECT RELEASE_LOCK(%s)', (key,))
    assert cursor.fetchone() == (1,)
    with catalog.lock_version(target) as attempt:
        assert lock_owner() != discarded
        assert attempt.release().write_state is PolicyWriteState.FINISHED
    assert lock_owner() is None
    evidence.append({'case': 'mysql_boundaries', 'same_connection_across_transactions': owner,
                     'atomic_switch_rolled_back': True, 'mysql_error': 1644, 'discarded_connection': discarded,
                     'lock_acquired_after_discard': True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding='utf-8'))
    use_config(args.config)
    namespace = 'order_demo_policy05_' + uuid4().hex[:12]
    evidence = []
    with isolated_order_database(args.config) as (order_config, _), tempfile.TemporaryDirectory() as folder, pymysql.connect(
        autocommit=True, **connection_config(),
    ) as db, db.cursor() as cursor:
        cursor.execute('SELECT VERSION()')
        version = cursor.fetchone()[0]
        print('SELECT VERSION():', version, flush=True)
        assert version == '8.0.46'
        cursor.execute(f'CREATE DATABASE `{namespace}` CHARACTER SET utf8mb4')
        try:
            db.select_db(namespace)
            settings['mysql']['database'] = load_config(order_config).mysql.database
            settings['policy'].update(catalog_database=namespace, milvus_collection=namespace)
            root_config = Path(folder) / 'publisher.json'
            root_config.write_text(json.dumps(settings), encoding='utf-8')
            with ExitStack() as resources:
                config = load_config(root_config)
                engine = create_mysql_engine(config.mysql, database=namespace)
                resources.callback(engine.dispose)
                catalog = MySQLPolicyCatalog(engine)
                index = PolicyBackend(config)
                resources.callback(index.close)

                def remove_collection():
                    if index.client.has_collection(namespace):
                        index.client.drop_collection(namespace)
                    assert not index.client.has_collection(namespace)

                resources.callback(remove_collection)
                info = {'case': 'environment', 'mysql': version, 'milvus': index.client.get_server_version(),
                        'isolated_namespace': namespace, 'isolated_order_database': settings['mysql']['database']}
                evidence.append(info)
                print(info, flush=True)
                first = cli_publish(root_config, SOURCE_PATH, DemoPolicy.BASELINE_VERSION)
                supplemental = Path(folder) / 'supplemental.md'
                supplemental.write_text(source('1.0', FAQ_ANSWER_V1, old_clause=True), encoding='utf-8')
                second = cli_publish(root_config, supplemental, '1.0')
                assert (first['catalog_revision'], second['catalog_revision']) == (1, 2)
                evidence.append({'case': 'initial_publications', 'results': [first, second]})
                print('first two documents activated', flush=True)
                check_mysql_boundaries(catalog, engine, cursor, namespace, evidence)
                for options in ([], ['--expect-failure']):
                    result = subprocess.run(
                        [sys.executable, '-X', 'utf8', str(ROOT / 'test/verify_policy_mcp.py'), '--config',
                         str(root_config), *options], cwd=ROOT, capture_output=True, encoding='utf-8', timeout=140,
                    )
                    assert result.returncode == 0, result.stderr
                    evidence.append({'case': 'standalone_mcp', 'options': options, 'output': result.stdout.splitlines()})
                print('standalone MCP success and unavailable Milvus verified', flush=True)
                update = read_policy_document(source('2.0'), version='2.0')
                probe = Path(folder) / 'probe'
                probe.mkdir()
                with server(settings, folder, supplement_probe=probe) as (path, server_log), host(path) as (host_pid, turn, host_log):
                    asyncio.run(check_receipts(path, catalog, index, update, turn, evidence, probe))
                    host_log.seek(0)
                    events = read_events(host_log.read().decode('utf-8'))
                    observations = [e for e in events if e['event'] == 'mcp_observation' and 'candidates' in e['result']]
                    citations = [e['evidence'] for e in events if e['event'] == 'policy_evidence'
                                 and e['evidence']['status'] == 'sufficient']
                    assert [e['catalog_revision'] for e in citations] == [2, 2, 2, 3]
                    for citation in citations:
                        assert verify_policy_evidence(PolicyEvidence.model_validate(citation), [
                            PolicySearchResult.from_mcp(item['result']) for item in observations
                            if item['result']['event_id'] == citation['citations'][0]['event_id']
                        ], formal_only=citation['citations'][0]['entry_type'] == 'policy')
                    server_log.seek(0)
                    server_events = read_events(server_log.read().decode('utf-8'))
                    event_ids = {e['result']['event_id'] for e in observations}
                    receipts = [e for e in server_events if e['event'] == 'mcp_search_policy'
                                and e['policy_event_id'] in event_ids]
                    assert all(e['pid'] != host_pid for e in receipts) and len(receipts) == len(observations)
                    pinned_event = citations[2]['citations'][0]['event_id']
                    pinned_position = next(i for i, receipt in enumerate(receipts) if receipt['policy_event_id'] == pinned_event)
                    assert receipts[pinned_position]['requested_catalog_revision'] == receipts[pinned_position - 1]['catalog_revision'] == 2
                    assert receipts[pinned_position]['mode'] == 'formal'
                    assert receipts[-2]['requested_catalog_revision'] is None
                    assert receipts[-1]['requested_catalog_revision'] == receipts[-2]['catalog_revision'] == 3
                    assert receipts[-1]['mode'] == 'formal'
                    assert not any('submit' in e['event'] for e in server_events)
                    evidence.append({'case': 'host_tool_contract', 'pid': host_pid, 'mcp_events': receipts,
                                     'verified_citations': citations})
                print('same Host instance, MCP receipts and pinned supplement verified', flush=True)
                check_faults(catalog, index, config, evidence, engine, cursor)
                blocked = read_policy_document(source('7.0'), version='7.0')
                supplemental.write_text(source('7.0'), encoding='utf-8')
                failure = cli_publish(root_config, supplemental, '7.0', qualification='changed', expected=1)
                assert '结构化规则' in failure and not index.has_entries(blocked.target)
                with catalog.lock_version(blocked.target) as attempt:
                    record = attempt.release()
                    assert record.publisher == 'issue16-verification' and record.qualification is QualificationConfirmation.CHANGED
                    assert record.activated_revision is None and catalog.snapshot().revision == 6
                reclassified = cli_publish(root_config, supplemental, '7.0', expected=1)
                assert '原件已声明改变资格' in reclassified and not index.has_entries(blocked.target)
                evidence.append({'case': 'qualification_guard', 'error': failure, 'reclassification': reclassified,
                                 'record': record.model_dump(mode='json')})
                print('retry, concurrent conflict and qualification guard verified', flush=True)
        finally:
            cursor.execute(f'DROP DATABASE `{namespace}`')
            cursor.execute('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (namespace,))
            assert cursor.fetchone() == (0,)
            evidence.append({'case': 'catalog_database_removed', 'namespace': namespace})
            args.evidence.write_text(''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in evidence), encoding='utf-8')


if __name__ == '__main__':
    main()
