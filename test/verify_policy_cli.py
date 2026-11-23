"""用真实服务和隔离会话验证只读政策咨询。"""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from uuid import uuid4

import pymysql
import httpx
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DEFAULT_PATH, load_config, use_config
from config.demo import DemoPolicy
from implementations.policy.backend import PolicyBackend
from model import connection_config
from implementations.policy.answer import NO_EVIDENCE, POLICY_UNAVAILABLE
from implementations.policy.source import SOURCE_PATH, parse_policy
from implementations.policy.evidence import verify_policy_evidence
from model.policy.evidence import PolicyEvidence
from model.policy.search import PolicyEntry, PolicySearchResult, SearchPolicyRequest, policy_chunk_id
from model.policy.status import PolicyEntryType, PolicySearchMode, PolicySearchStatus
from utils.mcp_client import bind_mcp_tool, open_mcp_client
from launch_with_config import application_command
from verify_order_cli import read_events
import verify_policy_faults as faults


class Proxy(faults.Proxy):
    order = ()
    replace_embedding = None

    def do_POST(self):
        if faults.mode == "rank_probe":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "/embeddings" in self.path and self.replace_embedding and "正式退款条款：" not in body["input"][0]:
                body["input"] = [self.replace_embedding]
            upstream = httpx.post(faults.model_base + self.path, json=body, timeout=30, trust_env=False)
            status, response = upstream.status_code, upstream.content
            if "/rerank" in self.path and status == 200 and "正式退款条款：" not in body["query"]:
                documents = body["documents"]
                if all(text in documents for text in self.order):
                    first = [documents.index(text) for text in self.order]
                    indexes = first + [item["index"] for item in upstream.json()["results"]
                                       if item["index"] not in first]
                    response = json.dumps({"results": [{"index": i} for i in indexes]}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        if faults.mode != "unrelated_faq" or "/rerank" not in self.path:
            return super().do_POST()
        body = self.rfile.read(int(self.headers["Content-Length"]))
        documents = json.loads(body)["documents"]
        wrong_faq = next(item.text for item in parse_policy(SOURCE_PATH.read_text(encoding="utf-8"))
                         if item.clause_id == "FAQ-002")
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
def server(settings, folder, *, empty_probe=False, supplement_probe=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    path = Path(folder) / "session-config.json"
    settings["mcp"] = {"url": f"http://127.0.0.1:{port}/mcp", "port": port,
                       "read_token": secrets.token_urlsafe(32)}
    path.write_text(json.dumps(settings), encoding="utf-8")
    env = {**os.environ, "PYTHONUTF8": "1"}
    command = application_command("mcp_service.server", path)
    if empty_probe or supplement_probe is not None:
        command = [sys.executable, str(ROOT / "test" / "policy_probe_server.py"), str(port), str(path)]
        command += ["--empty-first"] if empty_probe else ["--pause-supplement", str(supplement_probe)]
    with tempfile.TemporaryFile() as log, subprocess.Popen(
        command, cwd=ROOT, env=env, stderr=log,
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


@contextmanager
def isolated_policy_entries(question, answer):
    marker = uuid4().hex[:12]
    entries = [
        PolicyEntry(
            chunk_id=policy_chunk_id(DemoPolicy.DOCUMENT_ID, version, f"FAQ-{marker}"),
            document_id=DemoPolicy.DOCUMENT_ID, title="演示退款政策与常见问题",
            version=version, clause_id=f"FAQ-{marker}", entry_type=PolicyEntryType.FAQ,
            text=f"{question}\n{body}",
        )
        for version, body in ((DemoPolicy.BASELINE_VERSION, answer), (f"0.9-{marker}", "历史版本不应进入当前回执。"))
    ]
    backend = PolicyBackend()
    try:
        keys = [entry.chunk_id for entry in entries]
        assert not backend.client.get(backend.collection, ids=keys, consistency_level="Strong")
        try:
            backend.publish(entries)
            assert len(backend.client.get(backend.collection, ids=keys,
                                          consistency_level="Strong")) == len(keys)
            yield entries[0]
        finally:
            backend.client.delete(backend.collection, ids=keys)
            backend.client.flush(backend.collection)
            assert not backend.client.get(backend.collection, ids=keys, consistency_level="Strong")
            print("isolated current and historical Milvus entries cleaned", flush=True)
    finally:
        backend.close()


async def check_revision_receipts(path):
    settings = load_config(path).mcp
    async with open_mcp_client(settings.url, token=settings.read_token, read_timeout_seconds=90) as client:
        tool = await bind_mcp_tool(client, "search_policy", PolicySearchResult.from_mcp)
        query = "退款后多久能到账？"
        empty = await tool.call(SearchPolicyRequest(query=query, mode=PolicySearchMode.CONSULTATION))
        assert empty.status is PolicySearchStatus.OK and not empty.candidates
        assert empty.catalog_revision is not None
        followup = await tool.call(SearchPolicyRequest(query=query, mode=PolicySearchMode.FORMAL,
                                                       catalog_revision=empty.catalog_revision))
        assert followup.status is PolicySearchStatus.OK and followup.candidates
        assert followup.catalog_revision == empty.catalog_revision
        assert all(item.version == DemoPolicy.BASELINE_VERSION and item.entry_type is PolicyEntryType.POLICY
                   for item in followup.candidates)
        unknown = await tool.call(SearchPolicyRequest(query=query, mode=PolicySearchMode.FORMAL,
                                                      catalog_revision=empty.catalog_revision + 1))
        assert unknown.status is PolicySearchStatus.FAILED and not unknown.candidates and unknown.catalog_revision is None
        return {"case": "跨进程空回执、同修订正式补查及未知修订", "tool_results": [
            result.model_dump(mode="json") for result in (empty, followup, unknown)
        ]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_PATH, type=Path)
    parser.add_argument("--evidence", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--faults-only", action="store_true")
    mode.add_argument("--ticket15-extra", action="store_true")
    args = parser.parse_args()
    use_config(args.config)
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    evidence = []
    sessions = []
    with tempfile.TemporaryDirectory() as folder, pymysql.connect(
        database="order_demo", autocommit=True, **connection_config()
    ) as db, db.cursor() as cur:
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        print("SELECT VERSION():", version, flush=True)
        assert version == "8.0.46"
        saver = PyMySQLSaver(db)

        host = Path(folder) / "host"
        host.mkdir()
        for name in ("config", "implementations", "interfaces", "model", "utils", "workflows"):
            shutil.copytree(ROOT / name, host / name, ignore=shutil.ignore_patterns("__pycache__", "*.json"))
        shutil.copyfile(ROOT / "cli.py", host / "cli.py")
        launcher = host / "test" / "launch_with_config.py"
        launcher.parent.mkdir()
        shutil.copyfile(ROOT / "test" / launcher.name, launcher)
        assert not list(host.rglob("refund-policy.md"))

        def counts():
            cur.execute("SELECT (SELECT COUNT(*) FROM refunds), (SELECT COUNT(*) FROM refund_dispatches)")
            return cur.fetchone()

        baseline = counts()

        def turn(path, message, expected, intent, session=None, tools=None):
            command = [sys.executable, str(launcher), "cli", str(path)]
            if session:
                command += ["--session", session]
            command += ["--message", message]
            try:
                process = subprocess.run(command, cwd=host, capture_output=True, encoding="utf-8", timeout=600)
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
                if "server_events" in case or "events" not in case:
                    continue
                observed = [e for e in case["events"] if e["event"] == "mcp_observation"]
                related = [receipts[e["tool_event_id"]] for e in observed]
                for host, receipt in zip(observed, related):
                    assert host["trace_id"] == receipt["trace_id"] and host["pid"] != receipt["pid"]
                    if receipt["event"] == "mcp_search_policy":
                        assert host["result"]["event_id"] == receipt["policy_event_id"]
                        assert host["result"]["catalog_revision"] == receipt["catalog_revision"]
                policy_calls = [e for e in related if e["event"] == "mcp_search_policy"]
                if policy_calls:
                    assert policy_calls[0]["requested_catalog_revision"] is None
                if len(policy_calls) == 2:
                    first, second = policy_calls
                    assert second["mode"] == "formal"
                    assert second["requested_catalog_revision"] == first["catalog_revision"]
                    if second["status"] == "ok":
                        assert second["catalog_revision"] == first["catalog_revision"]
                policy_ids = {e["policy_event_id"] for e in related if "policy_event_id" in e}
                case["server_events"] = related + [e for e in events if e["event"] == "policy_retrieval"
                                                  and e["policy_event_id"] in policy_ids]
                verified = [e["evidence"] for e in case["events"] if e["event"] == "policy_evidence"]
                if verified and verified[-1]["status"] == "sufficient":
                    results = [PolicySearchResult.from_mcp(e["result"]) for e in observed
                               if "candidates" in e["result"]]
                    policy_evidence = PolicyEvidence.model_validate(verified[-1])
                    formal_only = any(e["event"] == "mcp_get_order" for e in related)
                    assert verify_policy_evidence(policy_evidence, results, formal_only=formal_only)
                    [citation] = policy_evidence.citations
                    result = next(item for item in results if item.event_id == citation.event_id)
                    [source] = [item for item in result.candidates
                                if (item.document_id, item.version, item.clause_id, item.entry_type)
                                == (citation.document_id, citation.version, citation.clause_id, citation.entry_type)]
                    assert citation.excerpt == source.text
                    assert f"《{source.title}》{source.version} 版 {source.clause_id}" in case["reply"]
                    for field, change in {
                        "event_id": "wrong-event", "document_id": "wrong-document", "version": "0.9",
                        "clause_id": "wrong-clause", "excerpt": "伪造政策原文",
                        "entry_type": PolicyEntryType.FAQ if formal_only else PolicyEntryType.POLICY,
                    }.items():
                        if field == "entry_type" and change == citation.entry_type:
                            change = PolicyEntryType.FAQ
                        forged = policy_evidence.model_copy(update={
                            "citations": [citation.model_copy(update={field: change})],
                        })
                        assert not verify_policy_evidence(forged, results, formal_only=formal_only)
                    if source.entry_type is PolicyEntryType.FAQ:
                        assert not verify_policy_evidence(policy_evidence, results, formal_only=True)
            assert not any(e["event"] == "mcp_submit_mock_refund" for e in events)

        try:
            with server(settings, folder) as (path, log):
                cases = [
                    ("退款多久能到账？", ("本演示没有真实资金到账过程", "《演示退款政策与常见问题》",
                                      "FAQ-004"), "policy_question", ["search_policy"]),
                    ("钱什么时候回到我的账户？", ("本演示没有真实资金到账过程", "FAQ-004"),
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
                if args.faults_only or args.ticket15_extra:
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
                if not args.smoke and not args.faults_only and not args.ticket15_extra:
                    sid = turn(path, "能退吗", ("请提供", "订单编号"), "order_question", tools=[])
                    turn(path, "DEMO-1001", ("符合整单原路退款条件", "RF-003"),
                         "order_question", session=sid, tools=["get_order", "search_policy"])
                capture(log)
                if failed:
                    raise failed[0]

            if not args.smoke and not args.ticket15_extra:
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

            if args.ticket15_extra:
                with server(settings, folder, empty_probe=True) as (path, log):
                    record = asyncio.run(check_revision_receipts(path))
                    log.seek(0)
                    record["server_events"] = [
                        item for item in read_events(log.read().decode("utf-8"))
                        if item["event"] in {"mcp_search_policy", "policy_retrieval"}
                    ]
                    evidence.append(record)
                question = "演示退款的专用回执校验词是什么？"
                answer = "专用回执校验词是青柚。"
                shipped = "订单已经交给物流并发货了，还能申请退款吗？"
                data = {item.clause_id: item.text for item in parse_policy(SOURCE_PATH.read_text(encoding="utf-8"))}
                faults.model_base = settings["policy"]["model_base"].rstrip("/")
                with isolated_policy_entries(question, answer) as faq, ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as proxy:
                    threading.Thread(target=proxy.serve_forever, daemon=True).start()
                    settings["policy"]["model_base"] = f"http://127.0.0.1:{proxy.server_port}"
                    faults.mode = "rank_probe"
                    try:
                        with server(settings, folder) as (path, log):
                            Proxy.order = (data["RF-008"], faq.text)
                            # 用真实候选的自然召回验证 FAQ 在有正式条款的结果中被压到第二位。
                            Proxy.replace_embedding = None
                            turn(path, question, (answer, faq.clause_id), "policy_question", tools=["search_policy"])
                            Proxy.order = (data["FAQ-003"], data["RF-004"])
                            turn(path, shipped, ("已发货的订单超出本演示支持范围", "RF-004"),
                                 "policy_question", tools=["search_policy"])
                            Proxy.replace_embedding = "退款执行前能否取消办理或更换订单？"
                            Proxy.order = (data["FAQ-006"], data["RF-010"], data["RF-007"])
                            turn(path, "退款金额是否可以自行指定？", ("不允许用户指定退款金额", "RF-005"),
                                 "policy_question", tools=["search_policy", "search_policy"])
                            turn(path, "会员积分如何兑换航空里程？", (NO_EVIDENCE,),
                                 "policy_question", tools=["search_policy", "search_policy"])
                            capture(log)
                            first, second, recovered, insufficient = evidence[-4:]
                            for case, expected_ids, total in (
                                (first, [faq.clause_id], 1), (second, ["RF-004"], 1),
                                (recovered, ["RF-005"], 2), (insufficient, [], 2),
                            ):
                                calls = [e for e in case["server_events"] if e["event"] == "mcp_search_policy"]
                                assert len(calls) == total
                                citations = [e["evidence"]["citations"] for e in case["events"]
                                             if e["event"] == "policy_evidence"][-1]
                                assert [item["clause_id"] for item in citations] == expected_ids
                            ranked = next(e["ranked"] for e in first["server_events"] if e["event"] == "policy_retrieval")
                            assert ranked[0]["entry_type"] == "policy" and ranked[1]["clause_id"] == faq.clause_id
                            assert all(item["version"] == DemoPolicy.BASELINE_VERSION for item in ranked)
                            ranked = next(e["ranked"] for e in second["server_events"] if e["event"] == "policy_retrieval")
                            assert [item["clause_id"] for item in ranked[:2]] == ["FAQ-003", "RF-004"]
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
