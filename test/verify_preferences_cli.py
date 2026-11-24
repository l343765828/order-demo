"""在真实 Qwen、MySQL 和独立 MCP 进程验证消费者偏好。"""

import argparse
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

import pymysql
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

from launch_with_config import application_command

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_config
from config.demo import CONSUMER_ID
from utils.mysql import connection_config


def run(module, config, *args, code=0, root=ROOT):
    launcher = root / "test" / "launch_with_config.py"
    result = subprocess.run([sys.executable, "-X", "utf8", str(launcher), module, str(config), *args],
                            cwd=root, capture_output=True, encoding="utf-8", timeout=180)
    events = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    assert result.returncode == code, (module, result.returncode, events)
    return result.stdout, events, result.stderr


def exercise(db, config, evidence, legacy_root=None):
    with db.cursor() as cur:
        foreign = str(uuid4())
        cur.execute("INSERT INTO conversation_sessions VALUES (%s, %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))",
                    (foreign, "DEMO-USER-02"))
        cur.execute("INSERT INTO consumer_preferences VALUES (%s, %s, %s, UTC_TIMESTAMP(6))",
                    ("DEMO-USER-02", "response_detail", "detailed"))
        for table in ("orders", "refunds", "refund_dispatches"):
            cur.execute(f"SELECT * FROM {table} ORDER BY 1")
            evidence[table] = cur.fetchall()
        stored = {}
        order_results = []

        def details():
            cur.execute("SELECT consumer_id, preference_key, value FROM consumer_preferences ORDER BY consumer_id")
            return cur.fetchall()

        def turn(message, *, session=None, status=None, root=ROOT):
            args = ("--session", session) if session else ()
            output, events, _ = run("cli", config, *args, "--message", message, root=root)
            session_id, reply = output.rstrip("\n").split("\n", 1)
            sid = session_id.removeprefix("会话：")
            loaded = next(event for event in events if event["event"] == "session_loaded")
            assert loaded["checkpoint"] == (session is not None)
            results = [event["result"] for event in events if event["event"] == "preference_result"]
            assert [item["status"] for item in results] == ([status] if status else []), (message, reply, results, events)
            checkpoint = PyMySQLSaver(db).get_tuple({"configurable": {"thread_id": sid}})
            assert checkpoint is not None
            channels = checkpoint.checkpoint["channel_values"]
            if channels.get("order_result") is not None:
                order_results.append(channels["order_result"])
            stored.setdefault(sid, []).extend((("user", message), ("assistant", reply)))
            evidence["turns"].append({"session_id": sid, "host_pid": loaded["pid"], "restored": loaded["checkpoint"],
                                      "trace_id": channels["trace_id"], "message": message, "reply": reply,
                                      "effective_detail": channels.get("response_detail"), "results": results})
            print(f"{message} => {reply.replace(chr(10), ' / ')}", flush=True)
            failures = [event for event in events if event["event"] in {"node_failed", "plan_rejected", "observation_rejected"}]
            if failures:
                print(json.dumps(failures, ensure_ascii=False), flush=True)
            return sid, reply, channels

        foreign_row = (("DEMO-USER-02", "response_detail", "detailed"),)
        brief_row = ((CONSUMER_ID, "response_detail", "brief"), *foreign_row)
        detailed_row = ((CONSUMER_ID, "response_detail", "detailed"), *foreign_row)
        first, reply, _ = turn("请记住，以后用中文简短回答。", status="updated", root=legacy_root or ROOT)
        assert "已保存" in reply and "简短" in reply and details() == brief_row
        evidence["legacy_host_wrote_first_turn"] = legacy_root is not None
        second, reply, _ = turn("查看我的长期偏好。", status="listed")
        assert second != first and "简短" in reply and "详细" not in reply
        order = "查一下 DEMO-1001"
        _, reply, channels = turn(order, session=second)
        assert "DEMO-1001｜已支付｜未发货｜无模拟退款记录｜实付 19900 分" in reply
        assert channels["response_detail"] == "brief"

        _, reply, channels = turn("我平时喜欢详细的说明。", session=first)
        assert "明确要求记住" in reply and channels.get("eligibility_question") is None
        assert details() == brief_row
        _, reply, channels = turn("查一下 DEMO-1001，这次请详细回答。", session=second)
        assert "实付金额：19900 分" in reply and channels["response_detail"] == "detailed"
        assert details() == brief_row
        _, reply, channels = turn(order, session=second)
        assert "实付 19900 分" in reply and channels["response_detail"] == "brief"

        _, reply, channels = turn("查一下 DEMO-1001，顺便请把我的长期偏好改为详细回答。",
                                  session=first, status="updated")
        assert "实付金额：19900 分" in reply and "已保存长期偏好" in reply and details() == detailed_row
        assert channels["decision"]["intent"] == "order_question"
        assert channels["refund_authorization"] is None
        _, reply, channels = turn(order, session=second)
        assert "实付金额：19900 分" in reply and channels["response_detail"] == "detailed"
        assert "简短" not in turn("查看我的长期偏好。", session=second, status="listed")[1]

        _, reply, _ = turn("请删除我的答复详略偏好。", session=second, status="deleted")
        assert "已确认删除" in reply and details() == foreign_row
        _, reply, channels = turn(order, session=first)
        assert "订单 DEMO-1001：已支付，未发货，无模拟退款记录，实付 19900 分。" in reply
        assert channels.get("response_detail") is None
        _, reply, _ = turn("查看我的长期偏好。", session=first, status="listed")
        assert "没有已保存" in reply and details() == foreign_row
        _, reply, _ = turn("查看我的长期偏好。", status="listed")
        assert "没有已保存" in reply
        denied, _, err = run("cli", config, "--session", foreign, "--history", code=2)
        assert not denied and "会话不可用" in err
        _, reply, _ = turn("请查看消费者 DEMO-USER-02 的长期偏好。", session=first)
        assert "查看本人的长期偏好" in reply and "详细" not in reply and details() == foreign_row

        cur.execute("INSERT INTO consumer_preferences VALUES (%s, %s, %s, UTC_TIMESTAMP(6))",
                    (CONSUMER_ID, "response_detail", "detailed"))
        for verb in ("INSERT", "UPDATE", "DELETE"):
            cur.execute(f"CREATE TRIGGER block_{verb.lower()} BEFORE {verb} ON consumer_preferences "
                        "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='isolated preference write fault'")
        _, reply, _ = turn("请记住以后简短回答。", session=first, status="failed")
        assert "无法确认" in reply and "已保存长期偏好" not in reply and details() == detailed_row
        _, reply, channels = turn("查一下 DEMO-1001，顺便请把我的长期偏好改为简短回答。",
                                  session=first, status="failed")
        assert "无法确认" in reply and "实付金额：19900 分" in reply
        assert channels["response_detail"] == "detailed" and details() == detailed_row
        _, reply, _ = turn("请删除我的答复详略偏好。", session=first, status="failed")
        assert "无法确认" in reply and "已确认删除" not in reply and details() == detailed_row
        cur.execute("RENAME TABLE consumer_preferences TO isolated_preferences_read_fault")
        try:
            _, reply, _ = turn("查看我的长期偏好。", session=first, status="failed")
            assert "无法读取" in reply and "没有已保存" not in reply
            _, reply, channels = turn(order, session=first)
            assert "实付 19900 分" in reply and channels.get("response_detail") is None
        finally:
            cur.execute("RENAME TABLE isolated_preferences_read_fault TO consumer_preferences")

        for sid, expected in stored.items():
            history, loaded, _ = run("cli", config, "--session", sid, "--history")
            assert loaded[0]["checkpoint"] and loaded[0]["messages"] == len(expected)
            assert history == f"会话：{sid}\n" + "".join(f"{role}：{text}\n" for role, text in expected)
            cur.execute("SELECT role, content FROM conversation_messages WHERE session_id=%s ORDER BY sequence", (sid,))
            assert cur.fetchall() == tuple(expected)
        for table in ("orders", "refunds", "refund_dispatches"):
            cur.execute(f"SELECT * FROM {table} ORDER BY 1")
            assert cur.fetchall() == evidence.pop(table)
        evidence["preserved_messages"] = sum(map(len, stored.values()))
        assert len(order_results) == 8 and all(result == order_results[0] for result in order_results)
        evidence["unchanged_order_result"] = order_results[0]
        evidence["isolated_consumer_and_write_failures"] = True


def verify(source, evidence, legacy_root=None):
    settings = load_config(source)
    name = f"order_demo_issue11_{uuid4().hex[:16]}"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["mysql"]["database"] = name
    payload["mcp"] = {"url": f"http://127.0.0.1:{port}/mcp", "port": port, "read_token": secrets.token_urlsafe(32)}
    payload.pop("policy", None)
    payload.pop("refund_policy_events_dir", None)
    if legacy_root is not None:
        payload["qwen"].setdefault("api_key", "unused-by-local-qwen")

    with tempfile.TemporaryDirectory(prefix="order-demo-issue11-") as directory:
        config = Path(directory) / "settings.json"
        config.write_text(json.dumps(payload), encoding="utf-8")
        with pymysql.connect(autocommit=True, **connection_config(settings.mysql)) as db, db.cursor() as cur:
            cur.execute("SELECT VERSION()")
            evidence["mysql"] = cur.fetchone()[0]
            print(f"SELECT VERSION(): {evidence['mysql']}", flush=True)
            assert evidence["mysql"] == "8.0.46"
            cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            try:
                db.select_db(name)
                run("manage", config, "init")
                run("manage", config, "seed")
                run("cli", config, "--init-checkpoints")
                with tempfile.TemporaryFile() as log, subprocess.Popen(
                    application_command("mcp_service.server", config), cwd=ROOT, stderr=log,
                ) as server:
                    try:
                        deadline = time.monotonic() + 15
                        while time.monotonic() < deadline:
                            assert server.poll() is None
                            try:
                                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                                    break
                            except OSError:
                                time.sleep(0.1)
                        else:
                            raise TimeoutError("isolated MCP server did not start")
                        exercise(db, config, evidence, legacy_root)
                    finally:
                        if server.poll() is None:
                            if sys.platform == "win32":
                                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                                               check=True, capture_output=True, timeout=15)
                            else:
                                server.terminate()
                            server.wait(timeout=15)
                    log.seek(0)
                    events = [json.loads(line) for line in log.read().decode("utf-8").splitlines() if line.startswith("{")]
                    order_events = [item for item in events if item["event"] == "mcp_get_order"]
                    assert len(order_events) == 8, order_events
                    mcp_pid, = {item["pid"] for item in order_events}
                    assert mcp_pid not in {turn["host_pid"] for turn in evidence["turns"]}
                    evidence["mcp_pid"] = mcp_pid
                    assert not any(item["event"] == "mcp_submit_mock_refund" for item in events)
                    evidence["mcp_order_reads"] = len(order_events)
                    evidence["mcp_refund_writes"] = 0
            finally:
                cur.execute(f"DROP DATABASE `{name}`")
                cur.execute("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (name,))
                assert cur.fetchone()[0] == 0
                evidence["isolated_db_removed"] = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--legacy-root", type=Path, help="由指定检出写入首条偏好、原文和 checkpoint")
    args = parser.parse_args()
    report = {"python": sys.version.split()[0], "versions": {name: version(name) for name in (
        "sqlalchemy", "pymysql", "langgraph", "langgraph-checkpoint-mysql", "deepagents", "mcp", "langchain-openai",
    )}, "turns": []}
    verify(args.config, report, args.legacy_root.resolve() if args.legacy_root else None)
    args.evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
