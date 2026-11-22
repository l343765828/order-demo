"""通过独立 MCP 进程验证真实政策检索和认证。"""
import asyncio
import argparse
import json
import socket
import subprocess
import sys
import time
import tempfile
from pathlib import Path
import httpx2
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model.policy.search import SearchPolicyRequest, PolicySearchResult
from model.policy.status import PolicySearchStatus
from config import DEFAULT_PATH
from utils.mcp_client import bind_mcp_tool, open_mcp_client
from utils.policy_tool import make_policy_tool


async def verify(url, token, expect_failure):
    async with httpx2.AsyncClient(trust_env=False) as http:
        response = await http.post(url, json={})
        assert response.status_code == 401
    async with open_mcp_client(url, token=token, read_timeout_seconds=90) as client:
        tool = await bind_mcp_tool(client, 'search_policy', SearchPolicyRequest, PolicySearchResult)
        if expect_failure:
            result = await tool.call({'query':'退款', 'mode':'formal'}, timeout=60)
            assert result.status is PolicySearchStatus.FAILED and not result.candidates
            print('milvus unavailable: failed, empty candidates, event=', result.event_id)
            return
        for mode, query in (('consultation', '已支付但没发货可以退款吗'),
                            ('formal', '已支付但没发货可以退款吗'),
                            ('consultation', '会员积分如何兑换航空里程')):
            result = PolicySearchResult.model_validate(await make_policy_tool(tool, mode).ainvoke({'query':query}))
            assert result.status is PolicySearchStatus.OK and result.candidates
            assert all(c.version == '1.0' for c in result.candidates)
            if mode == 'formal': assert all(c.entry_type == 'policy' for c in result.candidates)
            print(mode, query.encode('unicode_escape').decode(), result.model_dump_json(ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(DEFAULT_PATH))
    parser.add_argument('--expect-failure', action='store_true')
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as folder:
        settings = json.loads(Path(args.config).read_text(encoding='utf-8'))
        settings['mcp']['read_token'] = 'x' * 40
        if args.expect_failure:
            settings['policy']['milvus_uri'] = 'http://127.0.0.1:1'
        path = Path(folder) / 'settings.json'
        path.write_text(json.dumps(settings), encoding='utf-8')
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('policy_probe_server.py')), str(port), str(path)])
        try:
            deadline = time.monotonic() + 20
            while True:
                if process.poll() is not None: raise RuntimeError('MCP process exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.2): break
                except OSError:
                    if time.monotonic() > deadline: raise TimeoutError('MCP process failed to start')
                    time.sleep(0.1)
            asyncio.run(verify(f'http://127.0.0.1:{port}/mcp', 'x' * 40, args.expect_failure))
        finally:
            process.terminate(); process.wait(timeout=10)

if __name__ == '__main__': main()
