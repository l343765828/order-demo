"""通过故障代理和独立 MCP 进程检查失败不回退。"""
import asyncio
import argparse
import json
import socket
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
from model.policy.status import PolicySearchStatus
from config import DEFAULT_PATH
from utils.mcp_client import bind_mcp_tool, open_mcp_client

mode = 'invalid_vector'
counts = {'embedding': 0, 'rerank': 0}
model_base = ''

class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        global mode
        size = int(self.headers['content-length'])
        body = self.rfile.read(size)
        kind = 'rerank' if 'rerank' in self.path else 'embedding'
        counts[kind] += 1
        if kind == 'embedding' and mode == 'invalid_vector':
            response = json.dumps({'data':[{'embedding':[0.0]*1024}]}).encode(); status = 200
        elif kind == 'rerank':
            if mode == 'rerank_timeout': time.sleep(32)
            response = b'{}'; status = 503
        else:
            upstream = httpx.post(model_base + self.path, content=body,
                                  headers={'content-type':'application/json'}, timeout=30, trust_env=False)
            status = upstream.status_code; response = upstream.content
        try:
            self.send_response(status); self.send_header('Content-Length', str(len(response))); self.end_headers(); self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError): pass

async def verify(url):
    global mode
    async with open_mcp_client(url, token='x'*40, read_timeout_seconds=60) as client:
        tool = await bind_mcp_tool(client, 'search_policy', SearchPolicyRequest, PolicySearchResult)
        for mode in ('invalid_vector','rerank_failure','rerank_timeout'):
            before = counts.copy()
            result = await tool.call({'query':'未发货能退款吗','mode':'formal'})
            assert result.status is PolicySearchStatus.FAILED and not result.candidates
            delta = {k:counts[k]-before[k] for k in counts}
            assert delta == ({'embedding':1,'rerank':0} if mode == 'invalid_vector' else {'embedding':1,'rerank':1})
            print(mode, 'event=',result.event_id, 'calls=',delta, flush=True)

def main():
    global model_base
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(DEFAULT_PATH))
    args = parser.parse_args()
    settings = json.loads(Path(args.config).read_text(encoding='utf-8'))
    model_base = settings['policy']['model_base'].rstrip('/')
    proxy = ThreadingHTTPServer(('127.0.0.1',0), Proxy)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
    settings['mcp']['read_token'] = 'x' * 40
    settings['policy']['model_base'] = f'http://127.0.0.1:{proxy.server_port}'
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'settings.json'
        path.write_text(json.dumps(settings), encoding='utf-8')
        process = subprocess.Popen([sys.executable,str(Path(__file__).with_name('policy_probe_server.py')),str(port),str(path)])
        try:
            deadline = time.monotonic()+20
            while True:
                if process.poll() is not None: raise RuntimeError('MCP server exited')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2): break
                except OSError:
                    if time.monotonic()>deadline: raise TimeoutError('MCP startup')
                    time.sleep(.1)
            asyncio.run(verify(f'http://127.0.0.1:{port}/mcp'))
        finally:
            process.terminate(); process.wait(timeout=10); proxy.shutdown(); proxy.server_close()

if __name__ == '__main__': main()
