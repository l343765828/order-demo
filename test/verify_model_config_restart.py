"""在隔离库和真实 Qwen 上核对 Host 启动配置及重启生效。"""

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import load_config
from order_mysql_fixture import isolated_order_database, order_mcp_process
from verify_policy_catalog import host


@contextmanager
def qwen_proxy(settings, label):
    path = f'/{label}/v1'
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != f'{path}/chat/completions':
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((payload['model'], payload['max_completion_tokens'], 'Authorization' in self.headers,
                          'response_format' in payload, [tool['function']['name'] for tool in payload.get('tools', [])]))
            upstream = httpx.post(f'{settings.base_url.rstrip("/")}/chat/completions', json=payload,
                                  timeout=settings.timeout, trust_env=False)
            self.send_response(upstream.status_code)
            self.send_header('Content-Type', upstream.headers.get('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(upstream.content)))
            self.end_headers()
            self.wfile.write(upstream.content)

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}{path}', calls
        finally:
            server.shutdown()
            worker.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    settings = load_config(args.config).qwen
    with (
        isolated_order_database(args.config) as (path, _),
        qwen_proxy(settings, 'before') as (before, before_calls),
        qwen_proxy(settings, 'after') as (after, after_calls),
    ):
        data = json.loads(path.read_text(encoding='utf-8'))
        assert set(data) == {'mysql', 'mcp', 'qwen'}
        data['qwen']['base_url'] = before
        path.write_text(json.dumps(data), encoding='utf-8')
        with order_mcp_process(path), host(path) as (old_pid, turn, _):
            data['qwen']['base_url'] = after
            path.write_text(json.dumps(data), encoding='utf-8')
            turn('查一下 DEMO-1001', '实付 19900 分')
        assert not after_calls
        before_count = len(before_calls)
        with order_mcp_process(path), host(path) as (new_pid, turn, _):
            turn('查一下 DEMO-1001', '实付 19900 分')
        assert old_pid != new_pid and len(before_calls) == before_count
        for calls in (before_calls, after_calls):
            assert any(structured for _, _, _, structured, _ in calls)
            assert any('get_order' in tools for _, _, _, _, tools in calls)
            assert all((model, tokens, auth) == (settings.model, settings.max_completion_tokens, False)
                       for model, tokens, auth, _, _ in calls)
        print(f'通过：精简配置查单，Host PID {old_pid} 重用旧地址（{before_count} 请求），'
              f'重启 PID {new_pid} 使用新地址（{len(after_calls)} 请求），均覆盖主模型与调查子 Agent。', flush=True)


if __name__ == '__main__':
    main()
