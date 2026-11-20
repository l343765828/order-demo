"""用真实服务和隔离会话验证只读政策咨询。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

import pymysql
import httpx
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import use_config
from model import connection_config
from policy_answer import NO_EVIDENCE, POLICY_UNAVAILABLE, published_clauses
from verify_order_cli import read_events
import verify_policy_faults as faults


class Proxy(faults.Proxy):
    def do_POST(self):
        if faults.mode != "unrelated_faq" or "/rerank" not in self.path:
            return super().do_POST()
        body = self.rfile.read(int(self.headers["Content-Length"]))
        documents = json.loads(body)["documents"]
        wrong_faq = published_clauses()["FAQ-002"].text
        if wrong_faq in documents:
            first = documents.index(wrong_faq)
            indexes = [first] + [i for i in range(len(documents)) if i != first]
            status = 200
            response = json.dumps({"results": [{"index": i} for i in indexes]}).encode()
        else:
            upstream = httpx.post(faults.model_base + self.path, content=body,
                                  headers={"content-type": "application/json"}, timeout=30, trust_env=False)
            status, response = upstream.status_code, upstream.content
        self.send_response(status)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


@contextmanager
def server(settings, folder):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = Path(folder) / "session-config.json"
    settings["mcp"] = {"url": f"http://127.0.0.1:{port}/mcp", "port": port,
                       "read_token": secrets.token_urlsafe(32)}
    path.write_text(json.dumps(settings), encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1"}
    with tempfile.TemporaryFile() as log, subprocess.Popen(
        [sys.executable, "-m", "mcp_service.server", "--config", str(path)],
        cwd=ROOT, env=env, stderr=log,
    ) as process:
        try:
            deadline = time.monotonic() + 25
            while True:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("MCP did not start")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            yield path, log
        finally:
            process.terminate()
            process.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--evidence", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--faults-only", action="store_true")
    args = parser.parse_args()
    use_config(args.config)
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    evidence = []
    sessions = []
    with tempfile.TemporaryDirectory() as folder, pymysql.connect(
        database="order_demo", autocommit=True, **connection_config()
    ) as db, db.cursor() as cur:
        cur.execute("SELECT VERSION()")
        print("SELECT VERSION():", cur.fetchone()[0], flush=True)
        saver = PyMySQLSaver(db)

        def counts():
            cur.execute("SELECT (SELECT COUNT(*) FROM refunds), (SELECT COUNT(*) FROM refund_dispatches)")
            return cur.fetchone()

        baseline = counts()

        def turn(path, message, expected, intent, session=None, tools=None):
            command = [sys.executable, str(ROOT / "cli.py"), "--config", str(path)]
            if session:
                command += ["--session", session]
            command += ["--message", message]
            try:
                process = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=600)
                stdout = process.stdout.splitlines()
                stderr = process.stderr
                returncode = process.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = (exc.stdout or b"").decode("utf-8", errors="replace").splitlines()
                stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
                returncode = -1
            if stdout and stdout[0].startswith("会话："):
                session = stdout[0].split("：", 1)[1]
                sessions.append(session)
            events = read_events(stderr)
            observed = [e["tool"] for e in events if e["event"] == "react_call"]
            decisions = [e["decision"] for e in events if e["event"] == "intent"]
            record = {"question": message, "session_id": session, "reply": "\n".join(stdout[1:]),
                      "events": events}
            evidence.append(record)
            print(json.dumps({"case": message, "reply": record["reply"], "tools": observed},
                             ensure_ascii=True), flush=True)
            assert returncode == 0 and decisions and decisions[0]["intent"] == intent, record
            assert all(fragment in record["reply"] for fragment in expected), record
            assert not any("refund" in tool for tool in observed), record
            if tools is not None:
                assert observed[:len(tools)] == tools and len(observed) <= len(tools) + ("search_policy" in tools), record
            return session

        def capture(log):
            log.seek(0)
            events = read_events(log.read().decode("utf-8"))
            receipts = {e["tool_event_id"]: e for e in events if e.get("tool_event_id")}
            for case in evidence:
                if "server_events" in case:
                    continue
                observed = [e for e in case["events"] if e["event"] == "mcp_observation"]
                related = [receipts[e["tool_event_id"]] for e in observed]
                for host, receipt in zip(observed, related):
                    assert host["trace_id"] == receipt["trace_id"] and host["pid"] != receipt["pid"]
                    if receipt["event"] == "mcp_search_policy":
                        assert host["result"]["event_id"] == receipt["policy_event_id"]
                policy_ids = {e["policy_event_id"] for e in related if "policy_event_id" in e}
                case["server_events"] = related + [e for e in events if e["event"] == "policy_retrieval"
                                                  and e["policy_event_id"] in policy_ids]
                if "本演示没有真实资金到账过程" in case["reply"]:
                    candidate = next(e for e in case["server_events"] if e["event"] == "policy_retrieval")
                    assert candidate["ranked"][0]["clause_id"] == "FAQ-004"
                    verified = [e["evidence"] for e in case["events"] if e["event"] == "policy_evidence"][-1]
                    assert {citation["clause_id"] for citation in verified["citations"]} == {"RF-001", "RF-008"}
                    assert all(citation["event_id"] in policy_ids for citation in verified["citations"])
            assert not any(e["event"] == "mcp_submit_mock_refund" for e in events)

        try:
            with server(settings, folder) as (path, log):
                cases = [
                    ("退款多久能到账？", ("本演示没有真实资金到账过程", "《演示退款政策与常见问题》",
                                      "RF-001、RF-008"), "policy_question", ["search_policy"]),
                    ("钱什么时候回到我的账户？", ("本演示没有真实资金到账过程", "RF-001、RF-008"),
                     "policy_question", ["search_policy"]),
                    ("会员积分如何兑换航空里程？", (NO_EVIDENCE,), "policy_question", ["search_policy"]),
                    ("DEMO-1001 能退吗？", ("符合整单原路退款条件", "RF-003"), "order_question", ["get_order", "search_policy"]),
                    ("DEMO-1002 能退吗？", ("未支付", "RF-004"), "order_question", ["get_order", "search_policy"]),
                    ("DEMO-1004 能退吗？", ("已发货", "RF-004"), "order_question", ["get_order", "search_policy"]),
                    ("DEMO-2001 能退吗？", ("未找到可操作的订单",), "order_question", ["get_order"]),
                    ("能退 DEMO-1001 吗", ("符合整单原路退款条件", "RF-003"), "order_question", ["get_order", "search_policy"]),
                    ("如果能退，就帮我退 DEMO-1001", ("退款办理请求",), "refund_request", []),
                    ("这单退款？", ("想咨询", "还是要办理"), "unclear", []),
                ]
                if args.smoke:
                    cases = cases[:1] + cases[3:4]
                if args.faults_only:
                    cases = []
                failed = []
                with ThreadPoolExecutor(max_workers=3) as group:
                    runs = [group.submit(turn, path, question, expected, intent, tools=tools)
                            for question, expected, intent, tools in cases]
                    for run in runs:
                        try:
                            run.result()
                        except AssertionError as exc:
                            failed.append(exc)
                if not args.smoke and not args.faults_only:
                    sid = turn(path, "能退吗", ("请提供", "订单编号"), "order_question", tools=[])
                    turn(path, "DEMO-1001", ("符合整单原路退款条件", "RF-003"),
                         "order_question", session=sid, tools=["get_order", "search_policy"])
                capture(log)
                if failed:
                    raise failed[0]

            if not args.smoke:
                faults.model_base = settings["policy"]["model_base"].rstrip("/")
                with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as proxy:
                    threading.Thread(target=proxy.serve_forever, daemon=True).start()
                    settings["policy"]["model_base"] = f"http://127.0.0.1:{proxy.server_port}"
                    try:
                        with server(settings, folder) as (path, log):
                            faults.mode = "unrelated_faq"
                            turn(path, "会员积分如何兑换航空里程？", (NO_EVIDENCE,),
                                 "policy_question", tools=["search_policy"])
                            for mode in ("rerank_failure", "rerank_timeout"):
                                faults.mode = mode
                                prior = faults.counts["rerank"]
                                turn(path, "未发货能退款吗？", (POLICY_UNAVAILABLE,),
                                     "policy_question", tools=["search_policy"])
                                assert faults.counts["rerank"] == prior + 1
                            turn(path, "查一下 DEMO-1001", ("实付 19900 分",),
                                 "order_question", tools=["get_order"])
                            capture(log)
                            injected = next(e for e in evidence[-4]["server_events"]
                                            if e["event"] == "policy_retrieval")
                            assert injected["ranked"][0]["clause_id"] == "FAQ-002"
                    finally:
                        proxy.shutdown()

            assert counts() == baseline
            for sid in set(sessions):
                item = saver.get_tuple({"configurable": {"thread_id": sid}})
                assert item is not None
                channels = item.checkpoint["channel_values"]
                assert not any("authoriz" in key or "dispatch" in key or "pending_refund" in key for key in channels)
            print("refunds and dispatches unchanged; no refund authorization or write tools", flush=True)
        finally:
            for sid in set(sessions):
                saver.delete_thread(sid)
                cur.execute("DELETE FROM conversation_messages WHERE session_id=%s", (sid,))
                cur.execute("DELETE FROM conversation_sessions WHERE session_id=%s", (sid,))
            print("isolated sessions and checkpoints cleaned", flush=True)
            if args.evidence:
                args.evidence.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n"
                                                 for case in evidence), encoding="utf-8")


if __name__ == "__main__":
    main()
