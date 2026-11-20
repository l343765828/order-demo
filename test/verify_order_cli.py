"""用真实 Qwen、MySQL 和独立进程验证 CLI，只清理本脚本的会话。"""

import asyncio
import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pymysql
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from conversations import create_conversation
from utils.mcp_client import controlled_mcp_http
from model import connection_config, create_pool
from config import load_config, use_config
from order_flow import SERVICE_ERROR, UNAVAILABLE
from order_runtime import CONSUMER_ID


def read_events(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


async def check_mcp_permissions(url, token, session_id):
    async with httpx.AsyncClient(trust_env=False) as http:
        result = await http.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"clientInfo": {"name": "order-demo", "version": "1"}, "capabilities": {},
                       "protocolVersion": "2025-11-25"},
        }, headers={"X-Thread-ID": session_id})
        assert result.status_code == 401

    async with controlled_mcp_http() as http_mcp:
        http_mcp.headers.update({"Authorization": f"Bearer {token}", "X-Thread-ID": session_id})
        client = Client(streamable_http_client(url, http_client=http_mcp), read_timeout_seconds=8)
        async with client:
            assert {tool.name for tool in (await client.list_tools()).tools} == {"get_order", "search_policy"}
            for args in (
                {"order_id": "", "thread_id": session_id},
                {"order_id": "DEMO-2001", "consumer_id": "DEMO-USER-02"},
                {"order_id": 2001},
            ):
                invalid = await client.session.call_tool("get_order", args)
                assert invalid.is_error
            missing = await client.session.call_tool("get_order", {"order_id": "DEMO-2001"})
            assert not missing.is_error and missing.structured_content["status"] == "unavailable"
            assert missing.structured_content["order"] is None
    return 3


def verify():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    token = secrets.token_urlsafe(32)
    env = {**os.environ, "PYTHONUTF8": "1"}
    source = load_config()
    config_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8", delete=False)
    config_path = Path(config_file.name)
    with config_file:
        from dataclasses import asdict
        settings = asdict(source)
        settings["mcp"] = {"read_token": token, "port": port, "url": url}
        json.dump(settings, config_file)

    def cli(*args, user_input=None, code=0):
        result = subprocess.run([sys.executable, str(ROOT / "cli.py"), "--config", str(config_path), *args], env=env,
                                input=user_input, capture_output=True, encoding="utf-8", timeout=180)
        events = read_events(result.stderr)
        assert result.returncode == code, (result.stdout, result.stderr)
        return result.stdout, events, result.stderr

    pool = create_pool()
    sessions: list[str] = []
    try:
        with pymysql.connect(database="order_demo", autocommit=True, **connection_config()) as db, db.cursor() as cur:
            cur.execute("SELECT VERSION()")
            print("SELECT VERSION():", cur.fetchone()[0], flush=True)
            cur.execute("SELECT COUNT(*) FROM refunds")
            refund_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM refund_dispatches")
            dispatch_count = cur.fetchone()[0]
            cli("--init-checkpoints")
            for _ in range(2):
                sessions.append(create_conversation(pool, CONSUMER_ID))
            sid, empty_sid = sessions
            with tempfile.TemporaryFile() as log:
                with subprocess.Popen([sys.executable, "-m", "mcp_service.server", "--config", str(config_path)],
                                      cwd=ROOT, env=env, stderr=log) as server:
                    try:
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            assert server.poll() is None, "MCP server exited before listening"
                            try:
                                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                                    break
                            except OSError:
                                time.sleep(0.1)
                        else:
                            raise TimeoutError("MCP server did not start")

                        original = "查一下 DEMO-1001"
                        own, own_events, _ = cli("--session", sid, user_input=original + "\n")
                        assert "实付 19900 分" in own and "已支付，未发货" in own, own_events
                        assert [x["node"] for x in own_events if x["event"] == "graph_node"] == [
                            "intent", "plan", "execute", "observe", "replan"
                        ]
                        first = {event["event"]: event for event in own_events}
                        seen = first["react_call"]
                        assert seen["model_tool_call_id"] == first["react_observation"]["model_tool_call_id"]
                        assert seen["tool_event_id"] == first["observe"]["observation"]["tool_event_id"]
                        assert first["deepagent_delegate"]["agent"] == "order-agent"
                        assert first["replan"]["decision"]["action"] == "finish"

                        resumed, resume_events, _ = cli("--session", sid, "--history")
                        loaded, = resume_events
                        assert loaded["pid"] != seen["pid"] and loaded["trace_id"] == seen["trace_id"]
                        assert loaded["checkpoint"] and loaded["messages"] == 2 and loaded["pending_nodes"] == []
                        assert original in resumed and "实付 19900 分" in resumed
                        cp = PyMySQLSaver(db).get_tuple({"configurable": {"thread_id": sid}})
                        assert cp is not None
                        channels = cp.checkpoint["channel_values"]
                        assert channels["trace_id"] == seen["trace_id"]
                        assert "19900" in channels["reply"]

                        foreign, _, _ = cli("--session", empty_sid, "--history")
                        assert original not in foreign
                        other, other_events, _ = cli("--session", sid, "--message", "查一下 DEMO-2001")
                        missing, missing_events, _ = cli("--session", sid, "--message", "查一下 DEMO-9999")
                        assert other == missing and other.splitlines()[-1] == UNAVAILABLE, (other_events, missing_events)
                        rejected = asyncio.run(check_mcp_permissions(url, token, sid))
                    finally:
                        server.terminate()
                        server.wait(timeout=15)
                log.seek(0)
                server_events = read_events(log.read().decode("utf-8"))

            receipt, = [x for x in server_events if x.get("tool_event_id") == seen["tool_event_id"]]
            assert receipt["trace_id"] == seen["trace_id"] and receipt["pid"] != seen["pid"]
            assert receipt["mcp_request_id"] is not None and receipt["status"] == "found"
            assert len([x for x in server_events if x["event"] == "mcp_get_order"]) == 4
            assert len([x for x in server_events if x["event"] == "mcp_tool_rejected"]) == rejected
            assert sum(x["event"] == "mcp_auth_rejected" for x in server_events) == 1
            assert not any(x["event"] == "mcp_submit_mock_refund" for x in server_events)

            failed, failed_events, _ = cli("--session", sid, "--message", original)
            assert failed.splitlines()[-1] == SERVICE_ERROR, failed_events
            assert not any(event["event"] == "react_call" for event in failed_events)
            cur.execute("SELECT sequence, role, content FROM conversation_messages WHERE session_id=%s ORDER BY sequence", (sid,))
            rows = cur.fetchall()
            assert [row[0] for row in rows] == list(range(1, 9))
            assert [row[1] for row in rows] == ["user", "assistant"] * 4
            assert rows[0][2] == original and "实付 19900 分" in rows[1][2]
            assert rows[-1][2] == SERVICE_ERROR
            cur.execute("UPDATE conversation_sessions SET consumer_id=%s WHERE session_id=%s", ("DEMO-USER-02", sid))
            denied, denied_events, denied_error = cli("--session", sid, "--history", code=2)
            assert denied == "" and denied_events == [] and "会话不可用" in denied_error
            denied, denied_events, _ = cli("--session", sid, "--message", original, code=2)
            assert denied == "" and denied_events == []
            cur.execute("SELECT COUNT(*) FROM refunds")
            assert cur.fetchone()[0] == refund_count
            cur.execute("SELECT COUNT(*) FROM refund_dispatches")
            assert cur.fetchone()[0] == dispatch_count
            print(json.dumps({
                "session_id": sid, "trace_id": seen["trace_id"], "host_pid": seen["pid"],
                "server_pid": receipt["pid"], "reopened_host_pid": loaded["pid"],
                "nodes": ["intent", "plan", "execute", "observe", "replan"],
                "plan": first["plan"]["plan"], "delegation_id": first["deepagent_delegate"]["delegation_id"],
                "model_tool_call_id": seen["model_tool_call_id"], "mcp_request_id": receipt["mcp_request_id"],
                "tool_event_id": seen["tool_event_id"], "order_result": first["mcp_observation"]["result"],
                "replan": first["replan"]["decision"],
                "other_and_missing": UNAVAILABLE, "disconnected": SERVICE_ERROR,
                "ordered_messages": len(rows), "bad_arguments_rejected": rejected,
                "unauthenticated_rejected": 1, "refund_write_calls": 0,
            }, ensure_ascii=False), flush=True)
    finally:
        pool.close()
        config_path.unlink(missing_ok=True)
        if sessions:
            with pymysql.connect(database="order_demo", autocommit=True, **connection_config()) as db, db.cursor() as cur:
                for sid in sessions:
                    PyMySQLSaver(db).delete_thread(sid)
                    cur.execute("DELETE FROM conversation_messages WHERE session_id=%s", (sid,))
                    cur.execute("DELETE FROM conversation_sessions WHERE session_id=%s", (sid,))
                cur.execute("SELECT COUNT(*) FROM conversation_sessions WHERE session_id IN %s", (tuple(sessions),))
                assert cur.fetchone()[0] == 0
                print("isolated sessions and checkpoints cleaned", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="JSON 配置文件路径")
    use_config(parser.parse_args().config)
    verify()
