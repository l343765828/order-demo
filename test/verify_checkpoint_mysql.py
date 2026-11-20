"""Verify synchronous checkpoint persistence in a disposable MySQL database."""
import importlib.metadata as metadata
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TypedDict

import pymysql
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from langgraph.graph import END, START, StateGraph

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from model import connection_config

TABLES = ('orders', 'refunds', 'refund_dispatches', 'conversation_sessions',
          'conversation_messages', 'conversation_summaries', 'consumer_preferences')

class State(TypedDict):
    value: str
    observation: str


def graph(saver):
    builder = StateGraph(State)
    builder.add_node('observe', lambda state: {'observation': state['value'] + '-observed'})
    builder.add_node('finish', lambda state: {'value': state['value'] + '-continued'})
    builder.add_edge(START, 'observe')
    builder.add_edge('observe', 'finish')
    builder.add_edge('finish', END)
    return builder.compile(checkpointer=saver, interrupt_before=['finish'])


def config(thread):
    return {'configurable': {'thread_id': thread}}


def worker(stage, database):
    conn = pymysql.connect(**connection_config(), database=database, autocommit=True)
    try:
        saver = PyMySQLSaver(conn)
        app = graph(saver)
        if stage == 'write':
            saver.setup()
            for thread in ('a', 'b'):
                app.invoke({'value': thread, 'observation': ''}, config(thread))
                snapshot = app.get_state(config(thread))
                assert snapshot.values == {'value': thread, 'observation': thread + '-observed'}
                assert snapshot.next == ('finish',)
            print('write: both threads saved and paused before finish', flush=True)
        elif stage == 'resume':
            for thread in ('a', 'b'):
                snapshot = app.get_state(config(thread))
                assert snapshot.values == {'value': thread, 'observation': thread + '-observed'}
                assert snapshot.next == ('finish',)
                app.invoke(None, config(thread))
                snapshot = app.get_state(config(thread))
                assert snapshot.values == {'value': thread + '-continued', 'observation': thread + '-observed'}
                assert snapshot.next == ()
            print('resume: independent process recovered both observations and positions, then saved', flush=True)
        else:
            with conn.cursor() as cur:
                before = {}
                for table in TABLES:
                    cur.execute(f'SELECT * FROM `{table}`')
                    before[table] = cur.fetchall()
                    assert before[table], table
                cur.execute('SHOW TABLES')
                print('tables:', ', '.join(sorted(row[0] for row in cur.fetchall())))
                for table in ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes'):
                    cur.execute(f'SELECT COUNT(*) FROM `{table}` WHERE thread_id=%s', ('a',))
                    assert cur.fetchone()[0] > 0, table
                saver.delete_thread('a')
                saver.delete_thread('a')
                assert app.get_state(config('a')).values == {}
                for table in ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes'):
                    cur.execute(f'SELECT COUNT(*) FROM `{table}` WHERE thread_id=%s', ('a',))
                    assert cur.fetchone()[0] == 0, table
                assert app.get_state(config('b')).values['value'] == 'b-continued'
                app.invoke({'value': 'b-again', 'observation': ''}, config('b'))
                assert app.get_state(config('b')).values['observation'] == 'b-again-observed'
                for table in TABLES:
                    cur.execute(f'SELECT * FROM `{table}`')
                    assert cur.fetchall() == before[table], table
            print('cleanup: repeated deletion removed thread a and auxiliary rows; thread b read/write and seven business tables unchanged', flush=True)
    finally:
        conn.close()


def main():
    if len(sys.argv) == 3:
        worker(sys.argv[1], sys.argv[2])
        return
    name = 'order_demo_checkpoint_test_' + uuid.uuid4().hex[:12]
    conn = pymysql.connect(**connection_config(), autocommit=True)
    created = False
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT VERSION()')
            print('SELECT VERSION():', cur.fetchone()[0], flush=True)
            print('Python:', sys.version.split()[0], flush=True)
            for package in ('PyMySQL', 'langgraph', 'langgraph-checkpoint', 'langgraph-checkpoint-mysql', 'deepagents'):
                print(package + ':', metadata.version(package), flush=True)
            cur.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')
            created = True
            cur.execute(f'USE `{name}`')
            for sql in (ROOT / 'model' / 'schema.sql').read_text(encoding='utf-8-sig').split(';'):
                if sql.strip():
                    cur.execute(sql)
            cur.execute("INSERT INTO orders VALUES ('sentinel','consumer','paid','unshipped',19900,'CNY',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")
            cur.execute("INSERT INTO conversation_sessions VALUES ('session','consumer',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")
            cur.execute("INSERT INTO conversation_messages VALUES ('session',1,'user','sentinel',UTC_TIMESTAMP(6))")
            cur.execute("INSERT INTO conversation_summaries VALUES ('session',1,1,'sentinel','event',UTC_TIMESTAMP(6))")
            cur.execute("INSERT INTO consumer_preferences VALUES ('consumer','language','sentinel',UTC_TIMESTAMP(6))")
            cur.execute("INSERT INTO refund_dispatches (idempotency_key,order_id,session_id,source_message_sequence,status,trace_id,created_at,updated_at) VALUES ('dispatch','sentinel','session',1,'closed_no_refund','trace',UTC_TIMESTAMP(6),UTC_TIMESTAMP(6))")
            cur.execute("INSERT INTO refunds VALUES ('refund','sentinel',NULL,19900,'CNY','seed',UTC_TIMESTAMP(6))")
        for stage in ('write', 'resume', 'cleanup'):
            subprocess.run([sys.executable, __file__, stage, name], check=True)
    finally:
        if created:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE `{name}`')
                cur.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (name,))
                assert cur.fetchone() is None
                print('disposable database removed and verified', flush=True)
        conn.close()


if __name__ == '__main__':
    main()
