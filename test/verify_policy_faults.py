"""通过故障代理和独立 MCP 进程检查失败不回退。"""
import asyncio
import argparse
from contextlib import contextmanager
import json
import subprocess
import sys
import threading
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model.policy.search import SearchPolicyRequest, PolicySearchResult
from model.policy.status import PolicySearchMode, PolicySearchStatus
from config import DEFAULT_PATH, load_config
from config.demo import DemoPolicy
from implementations.policy.backend import open_policy_backend
from implementations.policy.source import SOURCE_PATH
from utils.mcp_client import bind_mcp_tool, open_mcp_client
from order_mysql_fixture import isolated_order_database, order_mcp_process

mode = 'invalid_vector'
counts = {'embedding': 0, 'rerank': 0}
MODEL_ROUTES = {'/embedding': 'embedding', '/rerank': 'reranker'}

class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    @property
    def model_section(self):
        return MODEL_ROUTES[self.path]

    def forward(self, **payload):
        upstream = self.server.upstreams[self.model_section]
        return httpx.post(upstream['url'], timeout=upstream['timeout'], trust_env=False, **payload)

    def reply(self, status, body):
        try:
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            pass

    def do_POST(self):
        size = int(self.headers['content-length'])
        body = self.rfile.read(size)
        kind = 'rerank' if self.model_section == 'reranker' else 'embedding'
        counts[kind] += 1
        if kind == 'embedding' and mode == 'invalid_vector':
            response = json.dumps({'data':[{'embedding':[0.0]*1024}]}).encode(); status = 200
        elif kind == 'rerank':
            if mode == 'rerank_timeout':
                time.sleep(self.server.reranker_timeout + 2)
            response = b'{}'; status = 503
        else:
            upstream = self.forward(content=body, headers={'content-type':'application/json'})
            status = upstream.status_code; response = upstream.content
        self.reply(status, response)


@contextmanager
def model_proxy(settings, handler=Proxy):
    with ThreadingHTTPServer(('127.0.0.1', 0), handler) as proxy:
        proxy.upstreams = {section: settings[section] for section in MODEL_ROUTES.values()}
        # 在 MCP 工具的等待时间内触发模型连接的读取超时。
        proxy.reranker_timeout = min(settings['reranker']['timeout'], 30)
        routed = {**settings, **{
            section: {**settings[section], 'url': f'http://127.0.0.1:{proxy.server_port}{route}'}
            for route, section in MODEL_ROUTES.items()
        }}
        routed['reranker']['timeout'] = proxy.reranker_timeout
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            yield routed
        finally:
            proxy.shutdown()


async def verify(mcp):
    global mode
    async with open_mcp_client(mcp.url, token=mcp.read_token, read_timeout_seconds=60) as client:
        tool = await bind_mcp_tool(client, 'search_policy', PolicySearchResult.from_mcp)
        for mode in ('invalid_vector','rerank_failure','rerank_timeout'):
            before = counts.copy()
            result = await tool.call(SearchPolicyRequest(query='未发货能退款吗', mode=PolicySearchMode.FORMAL))
            assert result.status is PolicySearchStatus.FAILED and not result.candidates
            delta = {k:counts[k]-before[k] for k in counts}
            assert delta == ({'embedding':1,'rerank':0} if mode == 'invalid_vector' else {'embedding':1,'rerank':1})
            print(mode, 'event=',result.event_id, 'calls=',delta, flush=True)


async def verify_restarted(mcp):
    before = counts.copy()
    async with open_mcp_client(mcp.url, token=mcp.read_token, read_timeout_seconds=60) as client:
        tool = await bind_mcp_tool(client, 'search_policy', PolicySearchResult.from_mcp)
        result = await tool.call(SearchPolicyRequest(query='未发货能退款吗', mode=PolicySearchMode.FORMAL))
        assert result.status is PolicySearchStatus.OK and result.candidates
    assert counts == before
    print('通过：MCP 重启后使用更新的两个模型地址，政策检索返回已重排候选。', flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding='utf-8'))
    if load_config(args.config).policy is None:
        parser.error('验收配置必须包含 policy 后端设置')
    with isolated_order_database(args.config) as (order_path, _), tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'settings.json'
        order = json.loads(order_path.read_text(encoding='utf-8'))
        namespace = order['mysql']['database']
        settings.update(mysql=order['mysql'], mcp=order['mcp'])
        settings['policy'].update(catalog_database=namespace, milvus_collection=namespace)
        path.write_text(json.dumps(settings), encoding='utf-8')
        loaded = load_config(path)
        with open_policy_backend(loaded) as backend:
            assert not backend.client.has_collection(namespace), 'isolated collection name already in use'
            try:
                published = subprocess.run(
                    [sys.executable, str(SOURCE_PATH.parents[1] / 'publish_policy.py'), '--config', str(path),
                     '--source', str(SOURCE_PATH), '--version', DemoPolicy.BASELINE_VERSION,
                     '--publisher', 'policy-fault-verification', '--qualification', 'unchanged'],
                    capture_output=True, timeout=300,
                )
                assert published.returncode == 0, f'isolated policy publication exited {published.returncode}'
                with model_proxy(settings) as routed:
                    path.write_text(json.dumps(routed), encoding='utf-8')
                    with order_mcp_process(path):
                        path.write_text(json.dumps(settings), encoding='utf-8')
                        asyncio.run(verify(loaded.mcp))
                    with order_mcp_process(path):
                        asyncio.run(verify_restarted(loaded.mcp))
            finally:
                if backend.client.has_collection(namespace):
                    backend.client.drop_collection(namespace)
                assert not backend.client.has_collection(namespace)
                print('isolated Milvus collection removed', flush=True)

if __name__ == '__main__': main()
