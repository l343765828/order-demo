"""Exercise real MySQL constraints in a disposable database."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pymysql

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from model import connection_config


def main():
    name = 'order_demo_test_' + uuid.uuid4().hex[:12]
    conn = pymysql.connect(**connection_config(), autocommit=True)
    try:
        with conn.cursor() as c:
            c.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
            try:
                c.execute(f'USE `{name}`')
                for sql in PROJECT_DIR.joinpath('model', 'schema.sql').read_text(encoding='utf-8-sig').split(';'):
                    if sql.strip(): c.execute(sql)
                c.execute('ALTER TABLE refund_dispatches ADD CONSTRAINT fk_dispatch_refund FOREIGN KEY (refund_id, order_id) REFERENCES refunds(refund_id, order_id)')
                c.execute("INSERT INTO orders VALUES ('o1','u','paid','unshipped',19900,'CNY',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6)),('o2','u','paid','unshipped',5900,'CNY',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")
                c.execute("INSERT INTO conversation_sessions VALUES ('s','u',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")
                c.execute("INSERT INTO conversation_messages VALUES ('s',1,'user','授权',UTC_TIMESTAMP(6))")
                insert = "INSERT INTO refund_dispatches (idempotency_key,order_id,session_id,source_message_sequence,status,trace_id,created_at,updated_at) VALUES (%s,%s,'s',%s,%s,'trace',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))"
                c.execute(insert, ('d1','o1',1,'pending'))
                def reject(sql, params, code):
                    try: c.execute(sql, params)
                    except pymysql.MySQLError as e:
                        assert e.args[0] == code, e.args[0]
                    else: raise AssertionError('expected MySQL rejection')
                reject(insert, ('d1','o2',1,'pending'), 1062)
                reject(insert, ('bad','o2',2,'pending'), 1452)
                reject(insert, ('d2','o1',1,'unconfirmed'), 1062)
                c.execute(insert, ('other','o2',1,'pending'))
                c.execute("UPDATE refund_dispatches SET status='closed_no_refund' WHERE idempotency_key='d1'")
                c.execute(insert, ('d2','o1',1,'unconfirmed'))
                c.execute("INSERT INTO refunds VALUES ('r1','o1','d2',19900,'CNY','execution',UTC_TIMESTAMP(6))")
                reject("INSERT INTO refunds VALUES ('r2','o1',NULL,19900,'CNY','seed',UTC_TIMESTAMP(6))", (), 1062)
                reject("INSERT INTO refunds VALUES ('r2','o2','d2',5900,'CNY','execution',UTC_TIMESTAMP(6))", (), 1062)
                print('Isolated foreign keys, duplicate keys, blocking transitions and independent orders: PASS')
                for table in ('orders','refunds','refund_dispatches','conversation_sessions','conversation_messages','conversation_summaries','consumer_preferences'):
                    c.execute(f'SHOW CREATE TABLE `{table}`')
                    print(c.fetchone()[1] + ';')
            finally:
                c.execute(f'DROP DATABASE `{name}`')
    finally:
        conn.close()
    # 每个子进程创建自己的连接池，一方关闭后另一方继续读取。
    worker = "from model import create_pool; p=create_pool(); c=p.connection(); q=c.cursor(); q.execute('SELECT COUNT(*) FROM orders'); print(q.fetchone()[0],flush=True); input(); q.execute('SELECT COUNT(*) FROM orders'); print(q.fetchone()[0],flush=True); c.close(); p.close()"
    processes = [subprocess.Popen([sys.executable,'-c',worker], cwd=PROJECT_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for p in processes:
            assert p.stdin is not None and p.stdout is not None
            assert int(p.stdout.readline()) >= 5
        for p in processes:
            assert p.stdin is not None and p.stdout is not None
            p.stdin.write('\n'); p.stdin.flush()
            assert int(p.stdout.readline()) >= 5
            assert p.wait(timeout=10) == 0
        print('Two processes with independent pools: PASS')
    finally:
        for p in processes:
            if p.poll() is None: p.kill(); p.wait()


if __name__ == '__main__': main()
