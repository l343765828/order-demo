"""Reproduce the paired live-Qwen benchmark without connecting business services."""

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import platform
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit


SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parent
SAMPLES = SUITE / "order_id_extraction_live.tsv"
BASELINE_TREE = "73840adf4b6adceac95c679dc03c96235b3e4231"
BASELINE_SHA256 = "7e0249c53df9c957685222d897ef7bff83a15f29e6d8c36eecc6f2e3b2249f77"
SOURCE_DIRS = ("config", "implementations", "interfaces", "mcp_service", "model",
               "repositories", "utils", "workflows")
HISTORY_ORDER_ID = "DEMO-9000"
HISTORY = f"请问 {HISTORY_ORDER_ID} 什么时候发货？"
PENDING = "我买错了，这一单能申请整单退款吗？"
FAST_PATH_IDS = {"N32", "R21", "R22"}
PENDING_CONTINUATION = {"N26", "N27", "N28", "N29", "N32", "N33", "C33", "C34"}
PENDING_SWITCH_GOALS = {"N30": "status", "N31": "status", "N34": "eligibility", "C36": "status"}
INCOMPLETE_FRAGMENTS = {
    "B13": {"DEMO-"},
    "B14": {"DEMO-51", "DEMO-51…"},
    "B15": {"DEMO-", "-5115", "5115", "DEMO-****-5115"},
    "B16": {"DEMO-51"},
    "B17": {"DEMO-51", "7", "DEMO-51?7"},
    "B18": {"DEMO‑51", "DEMO‑51□□"},
}


def load_samples(path=SAMPLES, sample_ids=()):
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t"))
    assert len(rows) >= 200
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({row["message"] for row in rows}) == len(rows)
    for row in rows:
        assert row["status"] in {"extracted", "missing", "ambiguous"}
        assert (row["order_id"] != "-") == (row["status"] == "extracted")
        assert row["authorization"] in {"yes", "no"}
        assert row["authorization"] == "no" or row["status"] == "extracted"
        assert row["context"] in {"-", "pending", "history"}
    if sample_ids:
        requested = set(sample_ids)
        rows = [row for row in rows if row["id"] in requested]
        assert len(rows) == len(sample_ids), "Sample IDs must be known and unique"
        raw += b"\0" + "\n".join(row["id"] for row in rows).encode("utf-8")
    return rows, hashlib.sha256(raw).hexdigest()


def source_digest(source):
    paths = list(source.glob("*.py"))
    for folder in SOURCE_DIRS:
        paths.extend(path for path in (source / folder).rglob("*") if path.suffix in {".py", ".sql"})
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(source).as_posix().encode() + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def live_run(source, config_path, *, version, revision, workers, output, samples_path=SAMPLES, retry_from=None,
             sample_ids=()):
    sys.path.insert(0, str(source))
    import httpx
    from langgraph.checkpoint.memory import InMemorySaver

    from config import load_config
    from implementations.refund.decision import QwenDecider
    from implementations.refund.execution import RefundExecution
    from model.orders.query import OrderQueryResult
    from model.orders.status import OrderQueryStatus
    from model.policy.search import PolicySearchResult
    from model.policy.status import PolicySearchStatus
    from model.refund.flow import ExchangeMessage, InvestigationStep, OrderFlowNode, OrderToolEvent, OrderTurn, PolicyToolEvent
    from model.refund.status import ConversationRole
    from model.refund.submission import RefundTurnSource
    from utils.checkpoint import thread_config
    from utils.qwen_client import create_qwen_client
    from workflows.refund import build_graph, run_refund_turn

    class IsolatedRefund(RefundExecution):
        def __init__(self):
            # Only the real preflight's unavailable-order branch is reachable.
            pass

    def investigate(step, trace_id):
        return OrderToolEvent(event_id="isolated-order", trace_id=trace_id, model_tool_call_id="isolated-call",
                              order_id=step.order_id, result=OrderQueryResult(status=OrderQueryStatus.UNAVAILABLE))

    def search(step, trace_id, _mode):
        return PolicyToolEvent(event_id="isolated-policy", trace_id=trace_id, model_tool_call_id="isolated-call",
                               query=step.query, result=PolicySearchResult(status=PolicySearchStatus.FAILED,
                               event_id="isolated-search", candidates=[], catalog_revision=None))

    settings = load_config(config_path).qwen
    url = urlsplit(settings.base_url)
    assert not any((url.username, url.password, url.query, url.fragment))
    samples, sample_sha = load_samples(samples_path, sample_ids)
    code_sha = source_digest(source)
    if version == "baseline":
        assert (revision, code_sha) == (BASELINE_TREE, BASELINE_SHA256), "Source differs from the ticket 01 baseline"
    started_at = datetime.now(timezone.utc).isoformat()
    meta = {
        "version": version, "revision": revision, "source_sha256": code_sha, "sample_sha256": sample_sha,
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "started_at": started_at, "platform": platform.platform(), "python": platform.python_version(), "workers": workers,
        "packages": {name: importlib.metadata.version(name) for name in
                     ("langgraph", "langchain-openai", "openai", "pydantic", "httpx")},
        "qwen": {**settings.model_dump(), "base_url": urlunsplit((url.scheme, url.netloc, url.path, "", "")),
                 "temperature": 0, "enable_thinking": False, "max_retries": 0},
    }
    if retry_from is not None:
        previous, attempts = read_completed_run(retry_from, samples, sample_sha)
        for key in ("version", "revision", "source_sha256", "qwen", "benchmark_sha256"):
            assert previous[key] == meta[key], f"Retry mismatch: {key}"
        assert [row["id"] for row in attempts] == [sample["id"] for sample in samples]
        samples = [sample for sample, row in zip(samples, attempts, strict=True) if has_timeout(row)]

    def run_sample(sample):
        nodes, events, requests, accessed = [], [], [], []
        session_id = f'live-{sample["id"]}'

        def emit(name, **fields):
            if name == "graph_node":
                nodes.append(fields["node"])
            elif name in {"order_id_extraction", "node_failed", "plan_rejected"}:
                events.append({"name": name, **fields})

        def request_started(request):
            schema = json.loads(request.content)["response_format"]["json_schema"]
            requests.append({"node": nodes[-1], "schema": schema["name"]})
            request.extensions["benchmark_start"] = perf_counter()

        def response_finished(response):
            response.read()
            record = requests[-1]
            record["http_status"] = response.status_code
            record["elapsed_ms"] = round((perf_counter() - response.request.extensions["benchmark_start"]) * 1000, 3)
            if response.is_success:
                try:
                    body = response.json()
                except ValueError:
                    return
                if not isinstance(body, dict):
                    return
                choices = body.get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    return
                choice = choices[0]
                if not isinstance(choice.get("message"), dict):
                    return
                message = {key: value for key, value in choice["message"].items()
                           if key in {"role", "content", "refusal", "tool_calls"}}
                record.update(response_id=body.get("id"), served_model=body.get("model"),
                              finish_reason=choice.get("finish_reason"), message=message)

        def record_order(step, trace_id):
            accessed.append(step.order_id)
            return investigate(step, trace_id)

        with httpx.Client(timeout=settings.timeout, trust_env=False,
                          event_hooks={"request": [request_started], "response": [response_finished]}) as http:
            graph = build_graph(QwenDecider(create_qwen_client(settings, http_client=http), emit), record_order, emit,
                                InMemorySaver(), search=search, refund=IsolatedRefund())
            history = []
            if sample["context"] == "history":
                history.append(ExchangeMessage(role=ConversationRole.USER, content=HISTORY))
            if sample["context"] == "pending":
                history.append(ExchangeMessage(role=ConversationRole.USER, content=PENDING))
                history.append(ExchangeMessage(role=ConversationRole.ASSISTANT, content="请提供要咨询资格的演示订单编号。"))
                previous = OrderTurn(history=history, trace_id=f"seed-{session_id}", refund_source=None)
                graph.update_state(thread_config(session_id), {
                    **previous.model_dump(mode="json"), "eligibility_question": PENDING,
                }, as_node=OrderFlowNode.INTENT.value)
            history.append(ExchangeMessage(role=ConversationRole.USER, content=sample["message"]))
            turn = OrderTurn(history=history, trace_id=session_id,
                             refund_source=RefundTurnSource(consumer_id="isolated", session_id=session_id,
                                                            source_message_sequence=len(history)))
            started = perf_counter()
            state = run_refund_turn(graph, session_id, turn)
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            assert not ({OrderFlowNode.REVIEW_REFUND.value, OrderFlowNode.SEND_REFUND.value} & set(nodes))
            # The checkpoint immediately before PLAN includes the actual routed decision.
            plan_id = None
            if OrderFlowNode.PLAN.value in nodes:
                entry = next(snapshot for snapshot in graph.get_state_history(thread_config(session_id))
                             if OrderFlowNode.PLAN.value in snapshot.next)
                plan_id = entry.values["decision"]["order_id"]
            return {
                "id": sample["id"], "elapsed_ms": elapsed_ms, "nodes": nodes, "events": events,
                "requests": requests, "plan_input_order_id": plan_id,
                "accepted_plan_order_ids": [step.order_id for step in state.plan.steps
                                            if isinstance(step, InvestigationStep)] if state.plan else [],
                "queried_order_ids": accessed, "decision": state.decision.model_dump(mode="json") if state.decision else None,
                "question": state.question, "pending_question": state.eligibility_question, "reply": state.reply,
                "authorization": state.refund_authorization.model_dump(mode="json") if state.refund_authorization else None,
            }

    with output.open("x", encoding="utf-8", newline="\n") as log, ThreadPoolExecutor(workers) as pool:
        log.write(json.dumps({"run": meta}, ensure_ascii=False) + "\n")
        log.flush()
        for index, row in enumerate(pool.map(run_sample, samples), 1):
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            if index % 20 == 0:
                print(f"{version}: {index}/{len(samples)}", flush=True)
        assert code_sha == source_digest(source), "Application source changed during benchmark"
        log.write(json.dumps({"completed": len(samples), "ended_at": datetime.now(timezone.utc).isoformat()}) + "\n")


def percentile(values, quantile):
    return sorted(values)[ceil(quantile * len(values)) - 1] if values else None


def latency(values):
    return {"n": len(values), "p95_ms": percentile(values, .95), "p99_ms": percentile(values, .99)}


def raw_intent(call):
    content = call.get("message", {}).get("content")
    if not isinstance(content, str):
        return {}
    try:
        output = json.loads(content)
    except ValueError:
        return {}
    return output if isinstance(output, dict) else {}


def metric_rows(meta, rows, samples):
    counts: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    ids_matched_by_category: Counter[str] = Counter()
    issues = {}
    raw_plan_disagreements = {}
    call_counts: Counter[int] = Counter()
    intent_counts: Counter[int] = Counter()
    ordinary_intent_counts: Counter[int] = Counter()
    intent_http_ms: list[float] = []
    for sample, row in zip(samples, rows, strict=True):
        identifier, category = sample["id"], sample["category"]
        assert identifier == row["id"]
        categories[category] += 1
        expected = sample["order_id"] if sample["order_id"] != "-" else None
        observed = row["plan_input_order_id"]
        observed = observed.upper() if observed is not None else None
        status = sample["status"]
        intent_calls = [request for request in row["requests"] if request["node"] == "intent"]
        call_counts[len(row["requests"])] += 1
        intent_counts[len(intent_calls)] += 1
        ordinary = sample.get("fast_path", "yes" if identifier in FAST_PATH_IDS else "no") == "no"
        if ordinary:
            ordinary_intent_counts[len(intent_calls)] += 1
        intent_http_ms.extend(call["elapsed_ms"] for call in intent_calls if "elapsed_ms" in call)
        auth = row["authorization"]
        labels: list[str] = []
        ids_matched_by_category[category] += observed == expected
        if ordinary:
            raw = raw_intent(intent_calls[0]) if intent_calls else {}
            counts["raw_intent_parseable"] += bool(raw)
            raw_id = raw.get("order_id")
            raw_upper = raw_id.upper() if isinstance(raw_id, str) else None
            if expected is not None:
                counts["raw_exact_n"] += 1
                counts["raw_exact_correct"] += raw_upper == expected
            if meta["version"] == "current":
                counts["raw_status_n"] += 1
                counts["raw_status_correct"] += raw.get("order_id_status") == status
            if raw_upper != observed:
                raw_plan_disagreements[identifier] = {"raw": raw_id, "plan": observed}
        if expected is not None:
            counts["exact_n"] += 1
            counts["exact_correct"] += observed == expected
            if observed is None:
                labels.append("missed")
        elif "plan" not in row["nodes"]:
            counts[f"{status}_stopped"] += 1
            if status == "missing" and row["reply"] in {
                "请提供要咨询资格的演示订单编号。", "请提供一个要查询的演示订单编号。",
            }:
                counts["missing_prompt"] += 1
            if status == "ambiguous" and row["reply"] in {
                "请明确指定一个要查询的演示订单编号。", "请明确指定一个要办理退款的演示订单编号。",
            }:
                counts["ambiguous_prompt"] += 1
        if expected is None:
            counts[f"{status}_n"] += 1
            if category != "refund":
                counts[f"read_only_{status}_n"] += 1
                if observed is None:
                    counts[f"read_only_{status}_without_order"] += 1
        if observed is not None:
            tokens = re.findall(r"[A-Z0-9_-]+", sample["message"].upper())
            if observed != expected:
                fragments = (sample["incomplete"].split("|") if "incomplete" in sample
                             else INCOMPLETE_FRAGMENTS.get(identifier, ()))
                if observed in fragments or (
                    observed not in tokens and any(observed in token for token in tokens)
                ):
                    labels.append("truncated")
                elif observed in tokens:
                    labels.append("wrong_selection")
                else:
                    labels.append("fabricated")
                    if sample["context"] == "history" and observed == HISTORY_ORDER_ID:
                        labels.append("historical_reuse")
            counts["plan_with_order"] += 1
            counts["accepted_plan_with_order"] += bool(row["accepted_plan_order_ids"])
        if auth is not None and sample["authorization"] == "no":
            labels.append("wrong_authorization")
            if status == "ambiguous":
                labels.append("ambiguous_wrong_authorization")
        if sample["authorization"] == "yes":
            counts["authorization_n"] += 1
            if not auth or auth["order_id"] != expected:
                labels.append("missed_or_wrong_authorization")
        if status == "ambiguous" and category == "refund":
            counts["refund_ambiguity_n"] += 1
        pending_goal = sample.get("pending_goal", "continue" if identifier in PENDING_CONTINUATION
                                  else PENDING_SWITCH_GOALS.get(identifier, ""))
        if pending_goal:
            continuation = pending_goal == "continue"
            outcome = "pending_continuation" if continuation else "pending_switch"
            question = PENDING if continuation else sample["message"]
            goal = "eligibility" if continuation else pending_goal
            counts[f"{outcome}_n"] += 1
            decision = row["decision"] or {}
            if (observed == expected and row["question"] == question and row["pending_question"] is None
                    and decision.get("intent") == "order_question" and decision.get("order_goal") == goal):
                counts[f"{outcome}_correct"] += 1
            else:
                labels.append(outcome)
        if sample["context"] == "pending" and expected is None:
            counts["pending_no_target_n"] += 1
            if observed is None and row["pending_question"] == PENDING:
                counts["pending_no_target_retained"] += 1
            else:
                labels.append("pending_no_target")
        counts["http_non_success"] += sum(not 200 <= call.get("http_status", 0) < 300 for call in row["requests"])
        counts["node_failures"] += sum(event["name"] == "node_failed" for event in row["events"])
        counts.update(labels)
        if labels:
            issues[identifier] = labels

    return {
        "version": meta["version"], "revision": meta["revision"], "source_sha256": meta["source_sha256"],
        "sample_counts": dict(categories), "order_id_matches_by_category": dict(ids_matched_by_category),
        "counts": dict(sorted(counts.items())), "errors_by_id": issues,
        "raw_plan_disagreements": raw_plan_disagreements,
        "workflow_latency": latency([row["elapsed_ms"] for row in rows]),
        "intent_http_latency": latency(intent_http_ms),
        "total_call_distribution": dict(sorted(call_counts.items())),
        "intent_call_distribution": dict(sorted(intent_counts.items())),
        "ordinary_intent_call_distribution": dict(sorted(ordinary_intent_counts.items())),
    }


def read_completed_run(path, samples, sample_sha):
    with path.open(encoding="utf-8") as stream:
        first, *rows, last = map(json.loads, stream)
    meta = first["run"]
    assert meta["sample_sha256"] == sample_sha
    assert last["completed"] == len(samples) == len(rows)
    return meta, rows


def has_timeout(row):
    return any(event["name"] == "node_failed" and event["error_type"] == "OpenAITimeoutError"
               for event in row["events"])


def score(path, samples_path, retry_path=None, *, sample_ids=()):
    samples, sample_sha = load_samples(samples_path, sample_ids)
    meta, rows = read_completed_run(path, samples, sample_sha)
    first_attempt = metric_rows(meta, rows, samples)
    if retry_path is None:
        print(json.dumps(first_attempt, ensure_ascii=False, indent=2))
        return

    timed_out = {row["id"] for row in rows if has_timeout(row)}
    retry_samples = [sample for sample in samples if sample["id"] in timed_out]
    retry_meta, retries = read_completed_run(retry_path, retry_samples, sample_sha)
    for key in ("version", "source_sha256", "qwen", "benchmark_sha256"):
        assert retry_meta[key] == meta[key], f"Retry mismatch: {key}"
    assert {row["id"] for row in retries} == timed_out, "Retry inputs must match the first attempt's timeouts"
    recovered = {row["id"]: row for row in retries if not has_timeout(row)}
    final_rows = [recovered.get(row["id"], row) for row in rows]
    after_retry = metric_rows(meta, final_rows, samples)
    # The selected rows were collected under different concurrency settings.
    del after_retry["workflow_latency"], after_retry["intent_http_latency"]
    print(json.dumps({
        "first_attempt": first_attempt,
        "after_retry": after_retry,
        "retry": {
            "first_attempt_timeouts": len(timed_out),
            "recovered": len(recovered),
            "remaining_timeouts": len(timed_out) - len(recovered),
            "model_requests": sum(len(row["requests"]) for row in retries),
        },
    }, ensure_ascii=False, indent=2))


def compare(baseline, current):
    samples, sample_sha = load_samples()
    outputs = []
    settings = []
    for path, version in ((baseline, "baseline"), (current, "current")):
        meta, rows = read_completed_run(path, samples, sample_sha)
        assert meta["version"] == version
        if version == "baseline":
            assert (meta["revision"], meta["source_sha256"]) == (BASELINE_TREE, BASELINE_SHA256)
        settings.append((meta["qwen"], meta["platform"], meta["python"], meta["packages"],
                         meta["benchmark_sha256"], meta["workers"]))
        outputs.append(metric_rows(meta, rows, samples))
    assert settings[0] == settings[1], "The two runs must use the same Qwen, script and environment"
    assert outputs[0]["source_sha256"] != outputs[1]["source_sha256"], "The source versions must differ"
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--source", required=True, type=Path)
    run.add_argument("--config", type=Path, default=ROOT / "config" / "local.json")
    run.add_argument("--version", choices=["baseline", "current"], required=True)
    run.add_argument("--revision", required=True)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("--samples", type=Path, default=SAMPLES)
    run.add_argument("--sample-id", action="append", default=[])
    run.add_argument("--retry-from", type=Path)
    pair = subparsers.add_parser("compare")
    pair.add_argument("baseline", type=Path)
    pair.add_argument("current", type=Path)
    single = subparsers.add_parser("score")
    single.add_argument("results", type=Path)
    single.add_argument("--samples", type=Path, default=SAMPLES)
    single.add_argument("--sample-id", action="append", default=[])
    single.add_argument("--retry", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        live_run(args.source.resolve(), args.config.resolve(), version=args.version, revision=args.revision, workers=args.workers,
                 output=args.output.resolve(), samples_path=args.samples.resolve(), retry_from=args.retry_from,
                 sample_ids=args.sample_id)
    elif args.command == "score":
        score(args.results, args.samples, args.retry, sample_ids=args.sample_id)
    else:
        compare(args.baseline, args.current)


if __name__ == "__main__":
    main()
