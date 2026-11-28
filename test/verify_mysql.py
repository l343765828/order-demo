"""在隔离 MySQL 中验收初始化、种子事务、约束及进程连接池。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pymysql
from sqlalchemy import event, text

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import manage
from config import load_config
from config.limits import PolicyCatalogTables
from implementations.policy.catalog import initialize_policy_catalog
from model.refund.status import DispatchStatus
from order_mysql_fixture import isolated_order_database
from utils.database import create_mysql_engine
from verify_checkpoint_mysql import config as checkpoint_config, graph as checkpoint_graph
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver


def run_manage(config, command):
    result = subprocess.run([sys.executable, str(PROJECT_DIR / 'manage.py'), '--config', str(config), command],
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, f'manage {command} exited {result.returncode}'
    assert result.stdout.strip() == f'{command} completed'


def rows(cursor, table):
    cursor.execute(f'SELECT * FROM {table} ORDER BY 1')
    return cursor.fetchall()


def check_seed(config, cursor):
    assert rows(cursor, 'orders') == rows(cursor, 'refunds') == ()
    cursor.execute("""
        CREATE TRIGGER fail_seed_refund BEFORE INSERT ON refunds FOR EACH ROW
        SIGNAL SQLSTATE '45000' SET MYSQL_ERRNO=1644, MESSAGE_TEXT='isolated seed refund failure'
    """)
    engine = create_mysql_engine(load_config(config).mysql)
    writes = []
    errors = []

    @event.listens_for(engine, 'before_cursor_execute')
    def observe(connection, _cursor, statement, parameters, _context, _many):
        if statement.startswith(('INSERT INTO orders', 'INSERT INTO refunds')):
            assert connection.in_transaction()
            writes.append((connection, statement.split()[2], parameters['order_id']))

    @event.listens_for(engine, 'handle_error')
    def observe_failure(context):
        errors.append(context.original_exception.args[0])

    try:
        with patch.object(manage, 'load_config', return_value=load_config(config)), \
                patch.object(manage, 'create_mysql_engine', return_value=engine), \
                patch.object(sys, 'argv', ['manage.py', 'seed']):
            try:
                manage.main()
            except SystemExit as exc:
                assert exc.code == 1
            else:
                raise AssertionError('expected an injected refund write failure')
        assert errors == [1644]
        assert [(table, order_id) for _, table, order_id in writes] == [
            ('orders', 'DEMO-1001'), ('orders', 'DEMO-1002'), ('orders', 'DEMO-1003'), ('refunds', 'DEMO-1003'),
        ]
        assert all(connection is writes[0][0] for connection, _, _ in writes)
        assert rows(cursor, 'orders') == rows(cursor, 'refunds') == ()
    finally:
        engine.dispose()
        cursor.execute('DROP TRIGGER fail_seed_refund')
    print('真实退款写入错误 1644，独立连接确认三条订单全部回滚', flush=True)

    with ThreadPoolExecutor(max_workers=2) as workers:
        seeds = [workers.submit(run_manage, config, 'seed') for _ in range(2)]
        for seed in seeds:
            seed.result()
    orders, refunds = rows(cursor, 'orders'), rows(cursor, 'refunds')
    assert [row[:5] for row in orders] == [
        ('DEMO-1001', 'DEMO-USER-01', 'paid', 'unshipped', 19900),
        ('DEMO-1002', 'DEMO-USER-01', 'unpaid', 'unshipped', 0),
        ('DEMO-1003', 'DEMO-USER-01', 'paid', 'unshipped', 8800),
        ('DEMO-1004', 'DEMO-USER-01', 'paid', 'shipped', 29900),
        ('DEMO-2001', 'DEMO-USER-02', 'paid', 'unshipped', 5900),
    ]
    assert [row[:5] for row in refunds] == [('MOCK-RF-1003', 'DEMO-1003', None, 8800, 'seed')]
    run_manage(config, 'seed')
    assert rows(cursor, 'orders') == orders and rows(cursor, 'refunds') == refunds
    print('两进程并发装载后仅持久化五笔订单与一笔配套退款，重跑保留全部字段', flush=True)

    cursor.execute("UPDATE orders SET consumer_id='existing-owner', shipping_status='shipped', "
                   "paid_amount_fen=123, updated_at=UTC_TIMESTAMP(6) WHERE order_id='DEMO-1003'")
    cursor.execute("UPDATE refunds SET refund_id='existing-refund', amount_fen=123 WHERE order_id='DEMO-1003'")
    existing_orders, existing_refunds = rows(cursor, 'orders'), rows(cursor, 'refunds')
    run_manage(config, 'seed')
    assert rows(cursor, 'orders') == existing_orders and rows(cursor, 'refunds') == existing_refunds
    cursor.execute("DELETE FROM refunds WHERE order_id='DEMO-1003'")
    engine = create_mysql_engine(load_config(config).mysql)
    try:
        with engine.begin() as connection:
            result = connection.execute(text(
                "INSERT INTO orders (order_id,consumer_id,payment_status,shipping_status,paid_amount_fen,created_at,updated_at) "
                "VALUES ('DEMO-1003','DEMO-USER-01','paid','unshipped',8800,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6)) "
                "ON DUPLICATE KEY UPDATE order_id=orders.order_id"
            ))
            assert result.rowcount == 1
    finally:
        engine.dispose()
    run_manage(config, 'seed')
    assert rows(cursor, 'orders') == existing_orders and rows(cursor, 'refunds') == ()
    print('FOUND_ROWS 对重复键空更新返回 1；种子重跑保留已有业务事实且不补写退款', flush=True)


def check_initialization(config, cursor):
    orders = rows(cursor, 'orders')
    for _ in range(2):
        run_manage(config, 'init')
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='fk_dispatch_refund'"
        )
        assert cursor.fetchone() == (1,)
        assert rows(cursor, 'orders') == orders
    mysql = load_config(config).mysql
    initialize_policy_catalog(mysql.database, mysql)
    cursor.execute(f'UPDATE {PolicyCatalogTables.HEAD} SET revision=2')
    initialize_policy_catalog(mysql.database, mysql)
    cursor.execute(f'SELECT revision FROM {PolicyCatalogTables.HEAD}')
    assert cursor.fetchone() == (2,)
    print('管理入口可重复初始化，退款外键和政策目录头仍有效', flush=True)


def check_constraints(cursor):
    cursor.execute("INSERT INTO orders VALUES ('o1','u','paid','unshipped',19900,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6)),"
                   "('o2','u','paid','unshipped',5900,UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")
    cursor.execute("INSERT INTO conversation_sessions VALUES ('s','u',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")
    cursor.execute("INSERT INTO conversation_messages VALUES ('s',1,'user','授权',UTC_TIMESTAMP(6))")
    insert = ("INSERT INTO refund_dispatches (idempotency_key,order_id,session_id,source_message_sequence,status,trace_id,"
              "created_at,updated_at) VALUES (%s,%s,'s',%s,%s,'trace',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")

    def reject(sql, params, code):
        try:
            cursor.execute(sql, params)
        except pymysql.MySQLError as exc:
            assert exc.args[0] == code
        else:
            raise AssertionError('expected MySQL rejection')

    cursor.execute(insert, ('d1', 'o1', 1, 'pending'))
    reject(insert, ('d1', 'o2', 1, 'pending'), 1062)
    reject(insert, ('bad', 'o2', 2, 'pending'), 1452)
    reject(insert, ('d2', 'o1', 1, 'unconfirmed'), 1062)
    cursor.execute(insert, ('other', 'o2', 1, 'pending'))
    cursor.execute("UPDATE refund_dispatches SET status='closed_no_refund' WHERE idempotency_key='d1'")
    cursor.execute(insert, ('d2', 'o1', 1, 'unconfirmed'))
    cursor.execute("INSERT INTO refunds VALUES ('r1','o1','d2',19900,'execution',UTC_TIMESTAMP(6))")
    reject("INSERT INTO refunds VALUES ('r2','o1',NULL,19900,'seed',UTC_TIMESTAMP(6))", (), 1062)
    reject("INSERT INTO refunds VALUES ('r2','o2','d2',5900,'execution',UTC_TIMESTAMP(6))", (), 1062)
    reject("UPDATE refund_dispatches SET refund_id='r1' WHERE idempotency_key='other'", (), 1452)
    print('外键、重复键、未决限制和独立订单约束通过', flush=True)


def check_worker_pools(config):
    worker = """
from sys import argv
from sqlalchemy import text
from config import load_config
from utils.database import create_mysql_engine

engine = create_mysql_engine(load_config(argv[1]).mysql)
try:
    for index in range(2):
        with engine.connect() as connection:
            print(connection.execute(text("SELECT COUNT(*) FROM orders")).scalar_one(), flush=True)
        if index == 0:
            input()
finally:
    engine.dispose()
"""
    workers = []
    try:
        for _ in range(2):
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', worker, str(config)],
                                       cwd=PROJECT_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            workers.append(process)
        for process in workers:
            assert process.stdout.readline().strip() == '7'
        for process in workers:
            output, _ = process.communicate(input='\n', timeout=30)
            assert process.returncode == 0 and output.strip() == '7'
    finally:
        for process in workers:
            if process.poll() is None:
                process.kill()
                process.wait()
    print('两个独立进程的 Core 连接池通过顺序释放和继续读取验证', flush=True)


def check_reset(config, cursor):
    saver = PyMySQLSaver(cursor.connection)
    saver.setup()
    app = checkpoint_graph(saver)
    app.invoke({'value': 'old', 'observation': ''}, checkpoint_config('old-thread'))
    cursor.execute("INSERT INTO conversation_summaries VALUES ('s',1,1,'摘要','old-event',UTC_TIMESTAMP(6))")
    cursor.execute("INSERT INTO consumer_preferences VALUES ('u','response_detail','brief',UTC_TIMESTAMP(6))")
    cursor.execute("UPDATE refund_dispatches SET status='succeeded', refund_id='r1' WHERE idempotency_key='d2'")
    cursor.execute("UPDATE refund_dispatches SET status=%s WHERE idempotency_key='other'",
                   (DispatchStatus.UNCONFIRMED.value,))
    cursor.execute(
        "INSERT INTO refund_dispatches (idempotency_key,order_id,session_id,source_message_sequence,status,trace_id,"
        "created_at,updated_at) VALUES ('old-pending','DEMO-1002','s',1,%s,'old',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))",
        (DispatchStatus.PENDING.value,),
    )
    cursor.execute("CREATE TABLE other_demo_sentinel (item VARCHAR(32) PRIMARY KEY)")
    cursor.execute("INSERT INTO other_demo_sentinel VALUES ('keep')")
    cursor.execute(
        f"INSERT INTO {PolicyCatalogTables.RELEASES} VALUES "
        "('reset-test-policy','1.0',%s,'test','unchanged','finished',2,UTC_TIMESTAMP(6))",
        ('0' * 64,),
    )

    cleared = ('conversation_sessions', 'conversation_messages', 'conversation_summaries',
               'consumer_preferences', 'refund_dispatches', 'checkpoints', 'checkpoint_blobs', 'checkpoint_writes')
    retained = (PolicyCatalogTables.HEAD, PolicyCatalogTables.RELEASES, 'checkpoint_migrations', 'other_demo_sentinel')
    before = {table: rows(cursor, table) for table in retained}
    assert all(rows(cursor, table) for table in cleared)
    cursor.execute("SELECT DISTINCT status FROM refund_dispatches")
    assert {DispatchStatus(status) for status, in cursor.fetchall()} == set(DispatchStatus)
    for _ in range(2):
        run_manage(config, 'reset')
        assert all(not rows(cursor, table) for table in cleared)
        assert [row[0] for row in rows(cursor, 'orders')] == [
            'DEMO-1001', 'DEMO-1002', 'DEMO-1003', 'DEMO-1004', 'DEMO-2001',
        ]
        assert [row[:5] for row in rows(cursor, 'refunds')] == [('MOCK-RF-1003', 'DEMO-1003', None, 8800, 'seed')]
        assert {table: rows(cursor, table) for table in retained} == before
    print('重置清空会话、偏好、已决及未决提交和检查点，恢复种子并保留政策目录与其他演示哨兵', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, help='授权的验收配置 JSON')
    with isolated_order_database(parser.parse_args().config, seed=False) as (config, cursor):
        check_seed(config, cursor)
        check_initialization(config, cursor)
        check_constraints(cursor)
        check_worker_pools(config)
        check_reset(config, cursor)
