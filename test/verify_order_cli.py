"""用真实 Qwen、MySQL 和独立进程验证 CLI，使用独立验收库。"""

import asyncio
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from implementations.refund.conversations import MySQLConversationService
from utils.database import create_mysql_engine
from utils.mcp_client import controlled_mcp_http
from config import load_config
from implementations.orders.reply import SERVICE_ERROR, UNAVAILABLE
from config.demo import CONSUMER_ID
from order_mysql_fixture import isolated_order_database, order_mcp_process


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
            assert {tool.name for tool in (await client.list_tools()).tools} == {
                "get_order", "search_policy", "get_refund_result", "submit_mock_refund",
            }
            for args in (
                {"order_id": "", "thread_id": session_id},
                {"order_id": 2001},
            ):
                invalid = await client.session.call_tool("get_order", args)
                assert invalid.is_error
            missing = await client.session.call_tool("get_order", {
                "order_id": "DEMO-2001", "consumer_id": "DEMO-USER-02",
            })
            assert not missing.is_error and missing.structured_content["status"] == "unavailable"
            assert missing.structured_content["order"] is None
    return 2


def verify(source):
    with isolated_order_database(source) as (config_path, cur):
        verify_cli(config_path, cur)


def verify_cli(config_path, cur):
    settings = load_config(config_path)
    env = {**os.environ, "PYTHONUTF8": "1"}

    def cli(*args, user_input=None, code=0):
        command = [sys.executable, str(ROOT / "test" / "launch_with_config.py"), "cli", str(config_path), *args]
        result = subprocess.run(
            command, cwd=ROOT, env=env,
            input=user_input, capture_output=True, encoding="utf-8", timeout=180,
        )
        events = read_events(result.stderr)
        assert result.returncode == code, (result.stdout, result.stderr)
        return result.stdout, events, result.stderr

    engine = create_mysql_engine(settings.mysql)
    try:
        cur.execute("SELECT COUNT(*) FROM refunds")
        refund_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM refund_dispatches")
        dispatch_count = cur.fetchone()[0]
        cli("--init-checkpoints")
        empty_sid = MySQLConversationService(engine).create(CONSUMER_ID)

        with order_mcp_process(config_path, env=env) as log:
            original = "查一下 DEMO-1001"
            own, own_events, _ = cli(user_input=original + "\n")
            assert "实付 19900 分" in own and "已支付，未发货" in own, own_events
            assert own.startswith("会话：")
            sid = own.splitlines()[0].removeprefix("会话：")
            nodes = [x["node"] for x in own_events if x["event"] == "graph_node"]
            assert nodes == ["intent", "preferences", "plan", "execute", "observe", "replan"]
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
            cp = PyMySQLSaver(cur.connection).get_tuple({"configurable": {"thread_id": sid}})
            assert cp is not None
            channels = cp.checkpoint["channel_values"]
            assert channels["trace_id"] == seen["trace_id"]
            assert "19900" in channels["reply"]
            order = channels["order_result"]["order"]
            assert order["paid_amount_fen"] == 19900 and "currency" not in order

            foreign, _, _ = cli("--session", empty_sid, "--history")
            assert original not in foreign
            other, other_events, _ = cli("--session", sid, "--message", "查一下 DEMO-2001")
            missing, missing_events, _ = cli("--session", sid, "--message", "查一下 DEMO-9999")
            assert other == missing and other.splitlines()[-1] == UNAVAILABLE, (other_events, missing_events)
            rejected = asyncio.run(check_mcp_permissions(settings.mcp.url, settings.mcp.read_token, sid))
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
        for invalid_sid in ("invalid-uuid", str(uuid4())):
            denied, denied_events, denied_error = cli("--session", invalid_sid, "--history", code=2)
            assert denied == "" and denied_events == [] and "会话不可用" in denied_error
        lock_name = f"order_demo:conversation:{sid}"
        cur.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
        assert cur.fetchone() == (1,)
        try:
            denied, denied_events, denied_error = cli("--session", sid, "--history", code=2)
            assert denied == "" and denied_events == [] and "会话不可用" in denied_error
        finally:
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
            assert cur.fetchone() == (1,)
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
            "nodes": nodes,
            "plan": first["plan"]["plan"], "delegation_id": first["deepagent_delegate"]["delegation_id"],
            "model_tool_call_id": seen["model_tool_call_id"], "mcp_request_id": receipt["mcp_request_id"],
            "tool_event_id": seen["tool_event_id"], "order_result": first["mcp_observation"]["result"],
            "replan": first["replan"]["decision"],
            "other_and_missing": UNAVAILABLE, "disconnected": SERVICE_ERROR,
            "session_rejections": ["invalid_id", "unknown_id", "lock_contention", "wrong_owner"],
            "ordered_messages": len(rows), "bad_arguments_rejected": rejected,
            "unauthenticated_rejected": 1, "refund_write_calls": 0,
        }, ensure_ascii=False), flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, help="JSON 配置文件路径")
    args = parser.parse_args()
    verify(args.config)
