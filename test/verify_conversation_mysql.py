"""在隔离 MySQL 中验证会话事务、命名锁以及 Host 保存原文的时机。"""

import argparse
from argparse import Namespace
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

import httpx
from pymysql.constants.SERVER_STATUS import SERVER_STATUS_IN_TRANS
import pytest
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import load_config, use_config
from config.demo import CONSUMER_ID
from implementations.refund import conversations, host
from implementations.refund.dispatches import MySQLRefundDispatchService
from implementations.refund.preferences import MySQLPreferences
from model.refund.flow import ExchangeMessage
from model.refund.status import ConversationRole
from order_mysql_fixture import isolated_order_database
from utils.checkpoint import open_mysql_checkpointer
from utils.database import create_mysql_engine
from utils.mysql import connection_config


def lock_owner(cursor, session_id):
    cursor.execute('SELECT IS_USED_LOCK(%s)', (f'order_demo:conversation:{session_id}',))
    return cursor.fetchone()[0]


def assert_can_lock(cursor, session_id, wait_seconds=0):
    name = f'order_demo:conversation:{session_id}'
    cursor.execute('SELECT GET_LOCK(%s, %s)', (name, wait_seconds))
    assert cursor.fetchone() == (1,)
    cursor.execute('SELECT RELEASE_LOCK(%s)', (name,))
    assert cursor.fetchone() == (1,)


def messages(cursor, session_id):
    cursor.execute('SELECT sequence, role, content FROM conversation_messages WHERE session_id=%s ORDER BY sequence',
                   (session_id,))
    return cursor.fetchall()


def session(cursor, session_id):
    cursor.execute('SELECT consumer_id, created_at, updated_at FROM conversation_sessions WHERE session_id=%s',
                   (session_id,))
    return cursor.fetchone()


def assert_transaction_ended(connection):
    assert not connection.in_transaction()
    assert not connection.connection.driver_connection.server_status & SERVER_STATUS_IN_TRANS


def verify(config_path, cursor):
    mysql = load_config(config_path).mysql
    engine = create_mysql_engine(mysql)
    competitor_engine = create_mysql_engine(mysql)
    use_config(config_path)
    try:
        storage = conversations.MySQLConversationService(engine)
        competitor = conversations.MySQLConversationService(competitor_engine)
        connections = []

        @event.listens_for(engine, 'begin')
        def observe_connection(connection):
            connections.append(connection)

        sid = storage.create(CONSUMER_ID)
        owner, created, updated = session(cursor, sid)
        assert owner == CONSUMER_ID and created == updated
        for rejected in (('invalid-uuid', CONSUMER_ID), (str(uuid4()), CONSUMER_ID), (sid, CONSUMER_ID.lower())):
            with pytest.raises(conversations.SessionUnavailable), storage.open(*rejected):
                pytest.fail('会话未被拒绝')
            assert_can_lock(cursor, rejected[0])

        empty_owner = str(uuid4())
        cursor.execute("INSERT INTO conversation_sessions VALUES (%s, '', UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                       (empty_owner,))
        with pytest.raises(conversations.SessionUnavailable), storage.open(empty_owner, CONSUMER_ID):
            pytest.fail('遗留空归属会话未被拒绝')
        assert_can_lock(cursor, empty_owner)

        user = ExchangeMessage(role=ConversationRole.USER, content="原文 'quoted' % \\ 🐾\n第二行")
        assistant = ExchangeMessage(role=ConversationRole.ASSISTANT, content='完整回复。')
        with pytest.raises(RuntimeError, match='处理中断'):
            with storage.open(sid.upper(), CONSUMER_ID) as conversation:
                assert conversation.session_id == sid and conversation.history() == []
                owner_id = lock_owner(cursor, sid)
                assert owner_id is not None
                assert_transaction_ended(connections[-1])
                with pytest.raises(conversations.SessionUnavailable), competitor.open(sid, CONSUMER_ID):
                    pytest.fail('另一个连接进入了同一会话')
                assert conversation.append(user) == 1
                assert session(cursor, sid)[2] > updated
                assert messages(cursor, sid) == ((1, 'user', user.content),)
                assert lock_owner(cursor, sid) == owner_id
                assert_transaction_ended(connections[-1])

                insert_observed = []

                def observe_insert(_conn, _cursor, statement, _params, _context, _many):
                    if statement.startswith('INSERT INTO conversation_messages'):
                        insert_observed.append(True)

                cursor.execute("CREATE TRIGGER force_session_update_failure BEFORE UPDATE ON conversation_sessions "
                               "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='ticket 02 fault'")
                before = session(cursor, sid)
                event.listen(engine, 'after_cursor_execute', observe_insert)
                try:
                    with pytest.raises(DBAPIError) as error:
                        conversation.append(assistant)
                    assert error.value.orig.args[0] == 1644 and insert_observed == [True]
                finally:
                    event.remove(engine, 'after_cursor_execute', observe_insert)
                    cursor.execute('DROP TRIGGER force_session_update_failure')
                assert session(cursor, sid) == before
                assert messages(cursor, sid) == ((1, 'user', user.content),)
                assert lock_owner(cursor, sid) == owner_id
                assert_transaction_ended(connections[-1])
                assert conversation.append(assistant) == 2
                assert session(cursor, sid)[2] > before[2]
                assert conversation.history() == [user, assistant]
                raise RuntimeError('处理中断')
        assert_can_lock(cursor, sid)

        with storage.open(sid, CONSUMER_ID) as conversation:
            assert conversation.history() == [user, assistant]
        assert_can_lock(cursor, sid)

        def lose_release_receipt(_conn, _cursor, statement, parameters, _context, _many):
            if 'RELEASE_LOCK' in statement:
                return 'SELECT 0', ()
            return statement, parameters

        discarded = []

        @event.listens_for(engine, 'invalidate')
        def observe_invalidation(dbapi_connection, _record, _error):
            discarded.append(dbapi_connection.thread_id())

        event.listen(engine, 'before_cursor_execute', lose_release_receipt, retval=True)
        try:
            with storage.open(sid, CONSUMER_ID):
                discarded_id = lock_owner(cursor, sid)
                assert discarded_id is not None
        finally:
            event.remove(engine, 'before_cursor_execute', lose_release_receipt)
        assert connections[-1].closed and discarded == [discarded_id]
        # MySQL 在处理客户端断开后释放该连接持有的命名锁。
        assert_can_lock(cursor, sid, wait_seconds=2)
        with storage.open(sid, CONSUMER_ID):
            assert lock_owner(cursor, sid) != discarded_id
        assert_can_lock(cursor, sid)

        sid = storage.create(CONSUMER_ID)

        def fail_at_workflow(_graph, session_id, turn):
            assert session_id == sid and turn.refund_source.source_message_sequence == 1
            assert messages(cursor, sid) == ((1, 'user', user.content),)
            assert_transaction_ended(connections[-1])
            assert lock_owner(cursor, sid) is not None
            raise RuntimeError('流程中断')

        with (
            open_mysql_checkpointer(mysql.database, **connection_config(mysql)) as saver,
            httpx.Client(trust_env=False) as http,
            patch.object(host, 'run_refund_turn', fail_at_workflow),
        ):
            saver.setup()
            args = Namespace(session=sid, message=user.content, history=False)
            with pytest.raises(RuntimeError, match='流程中断'):
                host.run_cli(args, saver, http, settings=load_config(config_path), conversations=storage,
                             dispatches=MySQLRefundDispatchService(engine),
                             preferences=MySQLPreferences(engine, consumer_id=CONSUMER_ID))
        assert messages(cursor, sid) == ((1, 'user', user.content),)
        assert_can_lock(cursor, sid)
        print('通过：归属、锁竞争与释放、物理连接丢弃、原文原子回滚、Host 流程失败后保留用户原文。')
    finally:
        use_config(None)
        competitor_engine.dispose()
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    with isolated_order_database(parser.parse_args().config) as (isolated_config, observers):
        verify(isolated_config, observers)
