"""为本人查单验收创建并清理独立 MySQL 库、配置和服务进程。"""

from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from uuid import uuid4

import pymysql

from config import load_config
from launch_with_config import application_command
from utils.mysql import connection_config

@contextmanager
def isolated_order_database(source, *, allow_write=False, seed=True, initialize=True):
    settings = load_config(source)
    database = f'order_demo_core01_{uuid4().hex[:12]}'
    with tempfile.TemporaryDirectory(prefix='order-core01-') as folder, pymysql.connect(
        autocommit=True, **connection_config(settings.mysql),
    ) as db, db.cursor() as cursor:
        cursor.execute('SELECT VERSION()')
        print('SELECT VERSION():', cursor.fetchone()[0], flush=True)

        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        path = Path(folder) / 'settings.json'
        payload = {
            'mysql': {**asdict(settings.mysql), 'database': database},
            'qwen': settings.qwen.model_dump(),
            'mcp': {'url': f'http://127.0.0.1:{port}/mcp', 'port': port, 'read_token': secrets.token_urlsafe(32)},
        }
        if allow_write:
            payload['mcp']['write_token'] = secrets.token_urlsafe(32)
        path.write_text(json.dumps(payload), encoding='utf-8')
        cursor.execute('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (database,))
        assert cursor.fetchone() == (0,), 'isolated database name is already in use'
        print('isolated database:', database, flush=True)
        try:
            if initialize:
                for command in ('init', 'seed') if seed else ('init',):
                    result = subprocess.run(application_command('manage', path, command), capture_output=True, timeout=30)
                    assert result.returncode == 0, f'manage {command} failed (exit {result.returncode})'
                db.select_db(database)
            yield path, cursor
        finally:
            with pymysql.connect(autocommit=True, **connection_config(settings.mysql)) as cleanup, cleanup.cursor() as drop:
                drop.execute(f'DROP DATABASE IF EXISTS `{database}`')
                drop.execute('SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (database,))
                assert drop.fetchone() == (0,)
            print('isolated database removed:', database, flush=True)


@contextmanager
def order_mcp_process(path, *, env=None):
    port = load_config(path).mcp.port
    with tempfile.TemporaryFile() as log:
        command = application_command('mcp_service.server', path)
        server = subprocess.Popen(command, env=env, stderr=log)
        try:
            deadline = time.monotonic() + 15
            while True:
                assert server.poll() is None, 'MCP server exited before listening'
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('MCP server did not start') from None
                    time.sleep(0.1)
            yield log
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                        pass
                except OSError:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('MCP server still accepts connections after termination')
                time.sleep(0.05)
