"""用真实服务和隔离会话验证只读政策咨询。"""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DEFAULT_PATH, load_config
from config.demo import DemoPolicy
from implementations.policy.backend import PolicyBackend
from implementations.policy.answer import NO_EVIDENCE, POLICY_UNAVAILABLE
from implementations.policy.source import SOURCE_PATH, parse_policy
from implementations.policy.evidence import verify_policy_evidence
from model.policy.evidence import PolicyEvidence
from model.policy.search import PolicyEntry, PolicySearchResult, SearchPolicyRequest, policy_chunk_id
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus, PolicySearchMode, PolicySearchStatus
from model.refund.status import RefundEvent
from utils.mcp_client import bind_mcp_tool, open_mcp_client
from utils.mysql import connection_config, mysql_connection
from launch_with_config import application_command
from order_mysql_fixture import isolated_order_database
from policy_condition_checks import assert_complete_policy_extraction
from verify_order_cli import read_events
import verify_policy_faults as faults


class Proxy(faults.Proxy):
    order = ()
    replace_embedding = None

    def do_POST(self):
        if faults.mode == "rank_probe":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.model_section == "embedding" and self.replace_embedding and "正式退款条款：" not in body["input"][0]:
                body["input"] = [self.replace_embedding]
            upstream = self.forward(json=body)
            status, response = upstream.status_code, upstream.content
            if self.model_section == "reranker" and status == 200 and "正式退款条款：" not in body["query"]:
                documents = body["documents"]
                if all(text in documents for text in self.order):
                    first = [documents.index(text) for text in self.order]
                    indexes = first + [item["index"] for item in upstream.json()["results"]
                                       if item["index"] not in first]
                    response = json.dumps({"results": [{"index": i} for i in indexes]}).encode()
            self.reply(status, response)
            return
        if faults.mode != "unrelated_faq" or self.model_section != "reranker":
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
            upstream = self.forward(content=body, headers={"content-type": "application/json"})
            status, response = upstream.status_code, upstream.content
        self.reply(status, response)


@contextmanager
def server(settings, folder, *, empty_probe=False, supplement_probe=None, supplement_query=None):
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
        if supplement_query is not None:
            command += ["--supplement-query", supplement_query]
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
def isolated_policy_entries(settings, question, answer):
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
    backend = PolicyBackend(settings)
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


def copy_host_without_policy(folder):
    host = Path(folder) / "host"
    host.mkdir()
    for name in ("config", "implementations", "interfaces", "model", "repositories", "utils", "workflows"):
        shutil.copytree(ROOT / name, host / name, ignore=shutil.ignore_patterns("__pycache__", "*.json"))
    shutil.copyfile(ROOT / "cli.py", host / "cli.py")
    launcher = host / "test" / "launch_with_config.py"
    launcher.parent.mkdir()
    shutil.copyfile(ROOT / "test" / launcher.name, launcher)
    assert not list(host.rglob("refund-policy.md"))
    return host, launcher


V2_ELIGIBLE = "仅限已付款且尚未发货的本人订单按原支付渠道退回整笔实付款，已有成功模拟退款记录的订单除外。"
V2_SHIPPED = "本人订单即使已经支付，发货后也不办理整单原路退款。"
ELIGIBILITY_TOOLS = ["get_order", "search_policy"]
READ_ONLY_REFUND_PROMPT = "如果能退，就帮我退 DEMO-1001"
READ_ONLY_REFUND_REPLY = "退款服务暂时不可用，未提交退款。"
READ_ONLY_REFUND_RESULT = {"status": "failed", "refund": None, "reason": "execution_unavailable"}


def verify_eligibility_switch(settings, folder, turn, capture, publish, evidence, *, order_id=None):
    prompt = "DEMO-1001 能退吗？"
    source = SOURCE_PATH.read_text(encoding="utf-8")
    clauses = {item.clause_id: item.text.split("\n", 1)[1] for item in parse_policy(source)}
    first_eligible_sentence = clauses["RF-003"].split("。", 1)[0] + "。"
    v2 = folder / "policy-v2.md"
    v2.write_text(source.replace("版本：1.0", "版本：2.0").replace("RF-003", "RF-083")
                  .replace(first_eligible_sentence, V2_ELIGIBLE)
                  .replace("## FAQ-001", f"## RF-084 — 已发货的付款订单\n\n{V2_SHIPPED}\n\n## FAQ-001"),
                  encoding="utf-8")
    probe = folder / "eligibility-probe" if order_id is None else None
    if probe is not None:
        probe.mkdir()
    with server(settings, folder, supplement_probe=probe, supplement_query="本人订单退款资格") as (path, log):
        if probe is None:
            evidence.append({"case": "publish_v2", "release": publish(v2, "2.0", 2)})
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(turn, path, prompt, (first_eligible_sentence, "1.0 版 RF-003"), "order_question",
                                          tools=[*ELIGIBILITY_TOOLS, "search_policy"])
                try:
                    deadline = time.monotonic() + 180
                    while not (probe / "before-supplement").exists():
                        if pending.done():
                            pending.result()
                            raise AssertionError("资格咨询未发起正式补查")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("资格咨询未到达正式补查")
                        time.sleep(.05)
                    assert (probe / "before-supplement").read_text(encoding="ascii") == "1"
                    release = publish(v2, "2.0", 2)
                finally:
                    (probe / "resume").touch()
                pending.result()
            capture(log)
            supplement = [e for e in evidence[-1]["server_events"] if e["event"] == "mcp_search_policy"]
            assert len(supplement) == 2 and supplement[1]["requested_catalog_revision"] == 1
            assert supplement[1]["catalog_revision"] == 1
            evidence.append({"case": "publish_v2_during_eligibility_followup", "release": release})

        cases = [
            (prompt, (V2_ELIGIBLE, "2.0 版 RF-083")),
            ("DEMO-1002 能退吗？", (clauses["RF-004"].split("。", 1)[0], "2.0 版 RF-004")),
            ("DEMO-1003 能退吗？", (clauses["RF-009"].split("。")[1], "2.0 版 RF-009")),
            ("DEMO-1004 能退吗？", ("已发货", "不办理", "2.0 版", "依据：《")),
        ]
        if order_id is not None:
            cases = [case for case in cases if case[0].startswith(order_id)]
        first_case = len(evidence)
        failed = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            requests = [executor.submit(turn, path, question, expected, "order_question", tools=ELIGIBILITY_TOOLS)
                        for question, expected in cases]
            for request in requests:
                try:
                    request.result()
                except AssertionError as exc:
                    failed.append(exc)
        capture(log)
        if failed:
            raise failed[0]
        new_cases = evidence[first_case:]
        assert len(new_cases) == len(cases) and all(case["server_events"] for case in new_cases)
        eligible = next((case for case in new_cases if case["question"] == prompt), None)
        if eligible is None:
            return
        [verified] = [e["evidence"] for e in eligible["events"] if e["event"] == "policy_evidence"
                      and e["evidence"]["status"] == PolicyEvidenceStatus.SUFFICIENT]
        [citation] = verified["citations"]
        assert citation["excerpt"] == V2_ELIGIBLE
        assert_complete_policy_extraction(eligible["events"], citation)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=DEFAULT_PATH, type=Path)
    parser.add_argument("--evidence", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--faults-only", action="store_true")
    mode.add_argument("--read-only-refund", action="store_true", help="仅验收只读环境中的条件退款")
    mode.add_argument("--ticket15-extra", action="store_true")
    mode.add_argument("--eligibility-switch", action="store_true")
    parser.add_argument("--eligibility-order", choices=("DEMO-1001", "DEMO-1002", "DEMO-1003", "DEMO-1004"))
    args = parser.parse_args()
    if args.eligibility_order and not args.eligibility_switch:
        parser.error("--eligibility-order 需要 --eligibility-switch")
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    if load_config(args.config).policy is None:
        parser.error("验收配置必须包含 policy 后端设置")
    evidence = []
    sessions = []
    with ExitStack() as resources:
        order_config, cur = resources.enter_context(isolated_order_database(args.config))
        folder = Path(resources.enter_context(tempfile.TemporaryDirectory()))
        mysql = load_config(order_config).mysql
        settings["mysql"]["database"] = mysql.database
        settings["policy"].update(catalog_database=mysql.database, milvus_collection=mysql.database)
        isolation = folder / "isolated-config.json"
        isolation.write_text(json.dumps(settings), encoding="utf-8")
        isolated_settings = load_config(isolation)
        with mysql_connection(mysql.database, **connection_config(mysql)) as db:
            PyMySQLSaver(db).setup()

        backend = PolicyBackend(isolated_settings)
        resources.callback(backend.close)
        assert not backend.client.has_collection(backend.collection), "isolated collection name is already in use"

        def remove_collection():
            if backend.client.has_collection(backend.collection):
                backend.client.drop_collection(backend.collection)
            assert not backend.client.has_collection(backend.collection)
            print("isolated Milvus collection removed", flush=True)

        resources.callback(remove_collection)
        def publish(source, version, revision):
            published = subprocess.run(
                [sys.executable, "-X", "utf8", str(ROOT / "publish_policy.py"), "--config", str(isolation),
                 "--source", str(source), "--version", version,
                 "--publisher", "policy-cli-verification", "--qualification", "unchanged"],
                cwd=ROOT, capture_output=True, encoding="utf-8", timeout=300,
            )
            assert published.returncode == 0, f"isolated policy publication exited {published.returncode}"
            result = json.loads(published.stdout)
            assert result["catalog_revision"] == revision
            return result

        published = publish(SOURCE_PATH, DemoPolicy.BASELINE_VERSION, 1)
        if args.eligibility_switch or args.read_only_refund:
            cur.execute("SELECT VERSION()")
            evidence.append({"case": "environment", "mysql": cur.fetchone()[0],
                             "milvus": backend.client.get_server_version(),
                             "model": isolated_settings.qwen.model, "isolated_database": mysql.database,
                             "release": published})
        host, launcher = copy_host_without_policy(folder)

        def counts(cursor):
            cursor.execute("SELECT (SELECT COUNT(*) FROM refunds), (SELECT COUNT(*) FROM refund_dispatches)")
            return cursor.fetchone()

        baseline = counts(cur)

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
            assert all(event["event"] not in {RefundEvent.DISPATCH_SAVED, RefundEvent.SUBMIT} for event in events)
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
                read_only_refund = case["question"] == READ_ONLY_REFUND_PROMPT
                if read_only_refund:
                    assert verified and verified[-1]["status"] == "sufficient"
                    refund_results = [e["result"] for e in case["events"] if e["event"] == RefundEvent.RESULT]
                    assert refund_results == [READ_ONLY_REFUND_RESULT]
                    assert case["reply"] == READ_ONLY_REFUND_REPLY
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
                    if formal_only:
                        assert citation.excerpt in source.text
                    else:
                        assert citation.excerpt == source.text
                    if not read_only_refund:
                        assert not formal_only or citation.excerpt in case["reply"]
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
            if args.eligibility_switch:
                verify_eligibility_switch(settings, folder, turn, capture, publish, evidence,
                                          order_id=args.eligibility_order)

            else:
                with server(settings, folder) as (path, log):
                    read_only_case = (READ_ONLY_REFUND_PROMPT, (READ_ONLY_REFUND_REPLY,), "refund_request", ELIGIBILITY_TOOLS)
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
                        read_only_case,
                        ("这单退款？", ("想咨询", "还是要办理"), "unclear", []),
                    ]
                    if args.smoke:
                        cases = cases[:2] + cases[3:4]
                    elif args.read_only_refund:
                        cases = [read_only_case]
                    elif args.faults_only or args.ticket15_extra:
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
                    if not (args.smoke or args.faults_only or args.ticket15_extra or args.read_only_refund):
                        sid = turn(path, "能退吗", ("请提供", "订单编号"), "order_question", tools=[])
                        turn(path, "DEMO-1001", ("符合整单原路退款条件", "RF-003"),
                             "order_question", session=sid, tools=["get_order", "search_policy"])
                    capture(log)
                    if failed:
                        raise failed[0]

            if not (args.smoke or args.ticket15_extra or args.eligibility_switch or args.read_only_refund):
                with faults.model_proxy(settings, Proxy) as routed:
                    with server(routed, folder) as (path, log):
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
                with (
                    isolated_policy_entries(isolated_settings, question, answer) as faq,
                    faults.model_proxy(settings, Proxy) as routed,
                ):
                    faults.mode = "rank_probe"
                    with server(routed, folder) as (path, log):
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


            refund_sessions = {case["session_id"] for case in evidence
                               if case.get("question") == READ_ONLY_REFUND_PROMPT}
            with mysql_connection(mysql.database, **connection_config(mysql)) as db, db.cursor() as cursor:
                assert counts(cursor) == baseline
                saver = PyMySQLSaver(db)
                for sid in set(sessions):
                    item = saver.get_tuple({"configurable": {"thread_id": sid}})
                    assert item is not None
                    channels = item.checkpoint["channel_values"]
                    assert channels.get("refund_verification") is None and channels.get("pending_refund") is None
                    if sid in refund_sessions:
                        assert channels["refund_authorization"]["order_id"] == "DEMO-1001"
                        assert channels["refund_authorization"]["session_id"] == sid
                        assert channels["refund_result"] == READ_ONLY_REFUND_RESULT
                    else:
                        assert channels.get("refund_authorization") is None and channels.get("refund_result") is None
            if args.eligibility_switch:
                evidence.append({"case": "consultation_side_effects", "refund_count": baseline[0],
                                 "dispatch_count": baseline[1], "authorization_created": False,
                                 "refund_write_calls": 0, "sessions": len(set(sessions))})
            if refund_sessions:
                evidence.append({"case": "read_only_refund_side_effects", "refund_count": baseline[0],
                                 "dispatch_count": baseline[1], "refund_write_calls": 0})
            print("refunds and dispatches unchanged; no refund write calls", flush=True)
        finally:
            if args.evidence:
                args.evidence.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n"
                                                 for case in evidence), encoding="utf-8")
            with mysql_connection(mysql.database, **connection_config(mysql)) as db, db.cursor() as cursor:
                saver = PyMySQLSaver(db)
                for sid in set(sessions):
                    saver.delete_thread(sid)
                    cursor.execute("DELETE FROM conversation_messages WHERE session_id=%s", (sid,))
                    cursor.execute("DELETE FROM conversation_sessions WHERE session_id=%s", (sid,))
            print("isolated sessions and checkpoints cleaned", flush=True)


if __name__ == "__main__":
    main()
