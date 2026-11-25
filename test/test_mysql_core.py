import json
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import MySQLConfig


def test_engine_passes_special_password_to_driver_as_data():
    from utils.database import create_mysql_engine

    mysql = MySQLConfig('127.0.0.1', 3306, 'test', "a@b/c:%#?'\\")
    engine = create_mysql_engine(mysql, database='order_test')
    try:
        _, options = engine.dialect.create_connect_args(engine.url)
        assert options['password'] == mysql.password
        assert mysql.password not in str(engine.url)
        assert options['database'] == 'order_test'
        assert options['charset'] == 'utf8mb4'
        assert engine.pool.size() == 5
        assert engine.hide_parameters
    finally:
        engine.dispose()


def test_sql_loader_resolves_from_repository_root_and_reuses_statement(tmp_path, monkeypatch):
    from utils.sql_loader import load_sql

    escaped = tmp_path / 'outside.sql'
    escaped.write_text('SELECT 1', encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    statement = load_sql('orders/get_order.sql', orders='orders', refunds='refunds')
    assert statement is load_sql('orders/get_order.sql', orders='orders', refunds='refunds')
    assert statement is load_sql('orders/./get_order.sql', refunds='refunds', orders='orders')
    assert set(statement.compile().params) == {'consumer_id', 'order_id'}
    assert '订单' in statement.text
    with pytest.raises(ValueError):
        load_sql(str(escaped))
    with pytest.raises(ValueError):
        load_sql('../../model/schema.sql')


def test_infrastructure_imports_without_application_or_business_orchestration():
    subprocess.run([sys.executable, '-c', '''
import sys
import config, config.demo, config.limits
import utils.database, utils.mysql, utils.mysql_lock, utils.sql_loader
import repositories.conversation_repository, repositories.dispatch_repository, repositories.order_repository
import repositories.policy_catalog_repository, repositories.preference_repository, repositories.refund_repository
for name in sys.modules:
    assert name.split('.')[0] not in {
        'implementations', 'workflows', 'mcp_service', 'cli', 'manage', 'publish_policy', 'dbutils',
    }, name
'''], cwd=ROOT, check=True)


@pytest.mark.parametrize('raises', [False, True])
@pytest.mark.parametrize('catalog_database', [None, 'order_test', 'policy_test'])
def test_mcp_entry_disposes_process_engine_on_exit(tmp_path, monkeypatch, raises, catalog_database):
    import config
    from config.limits import PolicyLimits
    from mcp_service import server
    from utils.database import create_mysql_engine

    path = tmp_path / 'settings.json'
    settings = {
        'mysql': {'host': '127.0.0.1', 'port': 3306, 'user': 'test', 'password': 'sample', 'database': 'order_test'},
        'mcp': {'url': 'http://127.0.0.1:8765/mcp', 'port': 8765, 'read_token': 'r' * 32},
        'qwen': {'base_url': 'http://127.0.0.1:8766/v1', 'model': 'qwen-test',
                 'max_completion_tokens': 32, 'timeout': 10, 'api_key': 'sample'},
    }
    if catalog_database is not None:
        settings['policy'] = {'catalog_database': catalog_database, 'milvus_uri': 'http://example.invalid',
                              'milvus_token': 'sample'}
        settings['embedding'] = {'url': 'http://embed.test/encode', 'model': 'embed-test', 'timeout': 7}
        settings['reranker'] = {'url': 'http://rank.test/rank', 'model': 'rank-test', 'timeout': 9}
    path.write_text(json.dumps(settings), encoding='utf-8')
    monkeypatch.setattr(config, '_config_path', path)
    monkeypatch.setattr(sys, 'argv', ['mcp-service'])
    lifecycle = []

    def observe_engine(mysql, *, database=None):
        engine = create_mysql_engine(mysql, database=database)
        lifecycle.append(('created', engine.url.database))
        event.listen(engine, 'engine_disposed', lambda _: lifecycle.append(('disposed', engine.url.database)))
        return engine

    def run_server(app, **options):
        lifecycle.append('running')
        if raises:
            raise RuntimeError('startup failure')

    monkeypatch.setattr(server, 'create_mysql_engine', observe_engine)
    monkeypatch.setattr(server.uvicorn, 'run', run_server)
    if raises:
        with pytest.raises(RuntimeError, match='startup failure'):
            server.main()
    else:
        server.main()
    databases = ['order_test']
    expected_catalog = catalog_database or PolicyLimits.CATALOG_DATABASE
    if expected_catalog != 'order_test':
        databases.append(expected_catalog)
    assert lifecycle == ([('created', database) for database in databases] + ['running']
                         + [('disposed', database) for database in reversed(databases)])
