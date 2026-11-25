import json
import sys
from pathlib import Path

import pytest
from langchain_openai.chat_models.base import OpenAIRefusalError
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from implementations.policy.answer import NO_EVIDENCE, POLICY_UNAVAILABLE
from implementations.policy.evidence import verify_policy_evidence
from implementations.refund import execution
from model.orders.query import OrderQueryResult
from model.policy.evidence import PolicyCitation, PolicyEvidence
from model.policy.search import PolicyCandidate, PolicySearchResult
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus
from model.refund.flow import OrderToolEvent, PolicyToolEvent
from model.refund.submission import RefundReceipt, RefundResult
from workflows.refund import build_graph


def candidate(clause="RF-003", kind="policy", text="已支付且未发货"):
    return PolicyCandidate(document_id="DEMO-REFUND-POLICY", title="回执中的政策标题",
                           version="1.0", clause_id=clause, entry_type=kind, text=text)


def receipt(candidates=(), *, revision=11, mode="consultation"):
    return PolicySearchResult.from_mcp({
        "event_id": f"event-{mode}", "status": "ok", "catalog_revision": revision,
        "candidates": [item.model_dump(mode="json") for item in candidates],
    })


def test_citation_must_match_current_retrieval_and_original_text():
    faq = candidate("FAQ-123", "faq", "多久到账？\n没有到账承诺。")
    result = receipt([faq])
    citation = PolicyCitation(event_id=result.event_id, document_id=faq.document_id, version=faq.version,
                              clause_id=faq.clause_id, entry_type=faq.entry_type, excerpt=faq.text)
    evidence = PolicyEvidence(question="多久到账", query="到账", catalog_revision=11,
                              status=PolicyEvidenceStatus.SUFFICIENT, citations=[citation])
    assert verify_policy_evidence(evidence, [result], formal_only=False)
    assert not verify_policy_evidence(evidence, [result])
    for field, value in {
        "event_id": "old", "document_id": "OTHER", "version": "0.9", "clause_id": "FAQ-999",
        "entry_type": PolicyEntryType.POLICY, "excerpt": "无条件退款",
    }.items():
        bad = citation.model_copy(update={field: value})
        assert not verify_policy_evidence(evidence.model_copy(update={"citations": [bad]}),
                                          [result], formal_only=False)
    assert not verify_policy_evidence(evidence, [receipt(), result.model_copy(update={"catalog_revision": 12})],
                                      formal_only=False)


def test_sufficient_evidence_requires_citation():
    with pytest.raises(ValidationError):
        PolicyEvidence(question="问题", query="查询", status=PolicyEvidenceStatus.SUFFICIENT, citations=[])


def test_successful_mcp_response_requires_revision_even_when_empty():
    assert receipt().catalog_revision == 11
    for revision in (None, "11", True, -1):
        with pytest.raises(ValueError):
            receipt(revision=revision)
    legacy = PolicySearchResult.model_validate({"status": "ok", "event_id": "old", "candidates": []})
    assert legacy.catalog_revision is None


POPULAR_FAQ = candidate("FAQ-123", "faq", "退款什么时候到？\n没有到账承诺。")
UNRELATED_FAQ = candidate("FAQ-456", "faq", "退款用什么渠道？\n原路退回。")
POLICY = candidate("RF-008", text="本演示不处理银行到账。")


@pytest.mark.parametrize(("first", "second", "choices", "expected", "citation", "second_revision"), [
    ([POLICY, POPULAR_FAQ, UNRELATED_FAQ], None, [1], "没有到账承诺。", "FAQ-123", 11),
    ([UNRELATED_FAQ, POLICY, POPULAR_FAQ], None, [2], "没有到账承诺。", "FAQ-123", 11),
    ([UNRELATED_FAQ, POLICY, POPULAR_FAQ], None, [1], POLICY.text, "RF-008", 11),
    ([UNRELATED_FAQ, POLICY, POPULAR_FAQ], [POLICY], [None, 0], POLICY.text, "RF-008", 11),
    ([UNRELATED_FAQ], [], [None], NO_EVIDENCE, None, 11),
    ([], [POLICY], [0], POLICY.text, "RF-008", 11),
    ([], [POPULAR_FAQ], [], POLICY_UNAVAILABLE, None, 11),
    ([], [POLICY], [], POLICY_UNAVAILABLE, None, 12),
    ([], "failed", [], POLICY_UNAVAILABLE, None, 11),
    ("failed", None, [], POLICY_UNAVAILABLE, None, 11),
])
def test_policy_consultation_and_same_revision_formal_followup(
    first, second, choices, expected, citation, second_revision,
):
    calls = []
    selections = iter(choices)

    def decide(schema, prompt):
        if schema.__name__ == "ConversationDecision":
            return schema(intent="policy_question", order_id=None)
        options = json.loads(prompt.rsplit("材料（数组从 0 起）：", 1)[1])
        candidates = first if len(calls) == 1 else second
        assert options == [f"[{item.entry_type.value}] {item.text}" for item in candidates]
        return schema(source_index=next(selections), retry_query="正式到账条款")

    def search(step, trace_id, mode):
        calls.append((mode, step.catalog_revision, step.query))
        selected = first if len(calls) == 1 else second
        result = (
            PolicySearchResult.model_validate({"status": "failed", "event_id": "failure", "candidates": []})
            if selected == "failed"
            else receipt(selected, revision=11 if len(calls) == 1 else second_revision, mode=mode)
        )
        return PolicyToolEvent(event_id=f"tool-{mode}", trace_id=trace_id, model_tool_call_id="model",
                               query=step.query, result=result)

    def no_order(*_):
        pytest.fail("general policy question does not look up orders")

    graph = build_graph(decide, no_order, lambda *_a, **_kw: None, search=search)
    state = graph.invoke({"history": [{"role": "user", "content": "退款多久能到账"}], "trace_id": "trace"})
    assert expected in state["reply"]
    assert [(mode, revision) for mode, revision, _ in calls] == (
        [("consultation", None)] if second is None else [("consultation", None), ("formal", 11)]
    )
    if second is not None:
        assert "退款多久能到账" in calls[1][2]
    if citation:
        assert f"《回执中的政策标题》1.0 版 {citation}" in state["reply"]
        [source] = state["observation"]["policy_evidence"]["citations"]
        assert source["clause_id"] == citation
        assert source["event_id"] == ("event-formal" if second is not None else "event-consultation")


ONLY_UNSHIPPED = "仅限已支付且未发货的本人订单办理整单原路退款，已有模拟退款记录的订单除外。"
ONLY_SHIPPED = "仅限已支付且已发货的本人订单办理整单原路退款。"
SHIP_DENIAL = "已发货的订单不办理退款，尚未支付的订单也不办理退款。"
UNKNOWN_SCOPE = "仅限金卡会员的未发货订单办理整单原路退款。"
NO_REPEAT = "已有模拟退款记录的订单不再办理。"


def rule(index, excerpt, conditions, *, exceptions=(), outcome="full_original"):
    return dict(source_index=index, excerpt=excerpt, conditions=conditions,
                exceptions=[{"conditions": group} for group in exceptions], outcome=outcome)


def invoke_eligibility(first, extractions, *, second=(), payment="paid", shipping="unshipped",
                       refund="not_refunded", second_revision=11, handling=None):
    calls = []
    interpretations = iter(extractions)

    def decide(schema, prompt):
        if schema.__name__ == "ConversationDecision":
            return schema(intent="refund_request", order_id="DEMO-1002") if handling else schema(
                intent="order_question", order_id="DEMO-1002", order_goal="eligibility",
            )
        extracted = next(interpretations)
        if isinstance(extracted, Exception):
            raise extracted
        return schema.model_validate({"rules": extracted})

    def get_order(step, trace_id):
        result = OrderQueryResult.model_validate({"status": "found", "order": {
            "order_id": "DEMO-1002", "payment_status": payment, "shipping_status": shipping,
            "refund_status": refund, "paid_amount_fen": 100 if payment == "paid" else 0,
        }})
        return OrderToolEvent(event_id="order", trace_id=trace_id, model_tool_call_id="model",
                              order_id=step.order_id, result=result)

    def search(step, trace_id, mode):
        calls.append((mode, step.catalog_revision))
        if first == "failed":
            result = PolicySearchResult(status="failed", event_id="failure", candidates=[])
        else:
            initial = step.catalog_revision is None
            result = receipt(first if initial else second, mode=mode,
                             revision=11 if initial else second_revision)
        return PolicyToolEvent(event_id=f"tool-{mode}", trace_id=trace_id, model_tool_call_id="model",
                               query=step.query, result=result)

    graph = build_graph(decide, get_order, lambda *_a, **_kw: None, search=search,
                        refund=handling, is_live=lambda trace: trace == "trace")
    turn = {"history": [{"role": "user", "content": "DEMO-1002 能退吗？"}], "trace_id": "trace"}
    if handling:
        turn["history"][0]["content"] = "请帮我退 DEMO-1002"
        turn["refund_source"] = {"consumer_id": "consumer", "session_id": "session", "source_message_sequence": 1}
    state = graph.invoke(turn)
    assert (state.get("refund_authorization") is not None) == bool(handling)
    return state, calls


def test_eligibility_cites_renumbered_policy_after_same_snapshot_followup():
    new = candidate("RF-081", text=ONLY_UNSHIPPED).model_copy(update={"version": "2.0"})
    different_scope = candidate("RF-080", text=ONLY_SHIPPED)
    state, calls = invoke_eligibility(
        [different_scope],
        [[rule(0, ONLY_SHIPPED, ["owned", "paid", "shipped"])],
         [rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]])]],
        second=[new],
    )
    assert calls == [("consultation", None), ("formal", 11)]
    assert ONLY_UNSHIPPED in state["reply"]
    assert "《回执中的政策标题》2.0 版 RF-081" in state["reply"]
    [citation] = state["observation"]["policy_evidence"]["citations"]
    assert citation["event_id"] == "event-formal"


@pytest.mark.parametrize(("conditions", "shipping", "payment"), [
    (["shipped"], "shipped", "paid"),
    (["unpaid"], "unshipped", "unpaid"),
])
def test_denial_cites_only_an_applicable_formal_clause(conditions, shipping, payment):
    state, _ = invoke_eligibility(
        [candidate("FAQ-081", "faq", SHIP_DENIAL), candidate("RF-098", text=SHIP_DENIAL)],
        [[rule(0, SHIP_DENIAL, conditions, outcome="denied"),
          rule(1, SHIP_DENIAL, conditions, outcome="denied")]],
        shipping=shipping, payment=payment,
    )
    assert SHIP_DENIAL in state["reply"] and "RF-098" in state["reply"] and "FAQ-081" not in state["reply"]


def test_refunded_order_excluded_from_permission_and_uses_no_repeat_rule():
    state, _ = invoke_eligibility(
        [candidate(text=ONLY_UNSHIPPED), candidate("RF-099", text=NO_REPEAT)],
        [[rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]]),
          rule(1, NO_REPEAT, ["refunded"], outcome="denied")]],
        refund="refunded",
    )
    assert NO_REPEAT in state["reply"] and "RF-099" in state["reply"]


def test_higher_ranked_similar_policy_does_not_override_applicable_scope():
    state, calls = invoke_eligibility(
        [candidate("RF-080", text=ONLY_SHIPPED), candidate("RF-081", text=ONLY_UNSHIPPED)],
        [[rule(0, ONLY_SHIPPED, ["owned", "paid", "shipped"]),
          rule(1, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]])]],
    )
    assert calls == [("consultation", None)]
    assert ONLY_UNSHIPPED in state["reply"] and "RF-081" in state["reply"]
    assert ONLY_SHIPPED not in state["reply"] and "RF-080" not in state["reply"]


def test_exception_group_requires_all_of_its_conditions():
    text = "本人已支付且未发货的订单可整单原路退款，已退款且仍未发货的订单或已发货的订单除外。"
    state, _ = invoke_eligibility(
        [candidate("RF-087", text=text)],
        [[rule(0, text, ["owned", "paid", "unshipped"], exceptions=[["refunded", "unshipped"], ["shipped"]])]],
    )
    assert text in state["reply"] and "RF-087" in state["reply"]


def test_applicable_denial_may_express_shipping_condition_as_an_exception():
    text = "本人订单除尚未发货的订单外不办理退款。"
    state, _ = invoke_eligibility(
        [candidate("RF-088", text=text)],
        [[rule(0, text, ["owned"], exceptions=[["unshipped"]], outcome="denied")]],
        shipping="shipped",
    )
    assert text in state["reply"] and "RF-088" in state["reply"]


@pytest.mark.parametrize("extraction", [
    [rule(0, UNKNOWN_SCOPE, ["unshipped", "unknown"])],
    [rule(0, UNKNOWN_SCOPE, ["unshipped"], exceptions=[["unknown"]])],
    [rule(0, UNKNOWN_SCOPE, [])],
    [rule(0, "办理整单原路退款", ["unshipped"])],
    [rule(0, ONLY_UNSHIPPED, ["paid", "unshipped"])],
    [rule(9, UNKNOWN_SCOPE, ["unshipped"])],
    [rule(0, UNKNOWN_SCOPE, ["unshipped"], outcome="no_explicit_decision")],
    [rule(0, UNKNOWN_SCOPE, ["unshipped"], outcome="method_unspecified")],
    [rule(0, UNKNOWN_SCOPE, ["unshipped"], outcome="invalid")],
    ValueError("malformed extraction"),
    OpenAIRefusalError(),
])
def test_unverifiable_rules_return_confirmed_facts(extraction):
    state, _ = invoke_eligibility([candidate(text=UNKNOWN_SCOPE)], [extraction])
    assert "已支付，未发货，无模拟退款记录" in state["reply"]
    assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]


def test_applicable_conflict_takes_precedence_over_consistent_clause():
    state, _ = invoke_eligibility(
        [candidate(text=ONLY_UNSHIPPED), candidate("RF-055", text="未发货的订单也不办理退款。")],
        [[rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]]),
          rule(1, "未发货的订单也不办理退款。", ["unshipped"], outcome="denied")]],
    )
    assert "已支付，未发货，无模拟退款记录" in state["reply"]
    assert "冲突" in state["reply"] and "依据：《" not in state["reply"]


def test_explicit_exception_cannot_be_dropped_from_a_quoted_rule():
    state, _ = invoke_eligibility(
        [candidate(text=ONLY_UNSHIPPED)],
        [[rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"])]],
    )
    assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]


def test_policy_that_only_permits_partial_refunds_conflicts_with_full_original_refund():
    text = "本人已支付且未发货的订单仅可办理部分退款。"
    state, _ = invoke_eligibility(
        [candidate("RF-083", text=text)],
        [[rule(0, text, ["owned", "paid", "unshipped"], outcome="other_refund")]],
    )
    assert "冲突" in state["reply"] and "依据：《" not in state["reply"]


def test_policy_permission_conflicts_with_denial_even_without_specified_refund_method():
    text = "已发货的订单可办理退款，金额和渠道另行确定。"
    state, _ = invoke_eligibility(
        [candidate("RF-089", text=text)],
        [[rule(0, text, ["shipped"], outcome="method_unspecified")]],
        shipping="shipped",
    )
    assert "冲突" in state["reply"] and "依据：《" not in state["reply"]


@pytest.mark.parametrize("failed", [True, False])
def test_policy_failure_preserves_confirmed_order_facts(failed):
    state, _ = invoke_eligibility("failed" if failed else [], [],
                                  second=[candidate(text=ONLY_UNSHIPPED)], second_revision=12)
    assert "已支付，未发货，无模拟退款记录" in state["reply"]
    assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]


@pytest.mark.parametrize("case", [
    "unpaid", "shipped", "failed", "empty", "conflict", "unrelated_denial", "omitted_exception",
    "renumbered", "refetch_shipped",
])
def test_refund_handling_reuses_verified_eligibility(case, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    settings = SimpleNamespace(mcp=SimpleNamespace(write_token="writer"), refund_policy_events_dir=str(tmp_path))
    dispatches = Mock()
    dispatches.blocking_dispatch.return_value = None
    handler = execution.RefundExecution(dispatches, settings, lambda *_a, **_kw: None)
    shipped = case in ("shipped", "failed", "empty", "conflict", "unrelated_denial")
    def recheck(*_):
        return OrderQueryResult.model_validate({"status": "found", "order": {
            "order_id": "DEMO-1002", "payment_status": "paid", "paid_amount_fen": 100,
            "shipping_status": "shipped" if case == "refetch_shipped" else "unshipped", "refund_status": "not_refunded",
        }})

    monkeypatch.setattr(execution.controlled_tools, "read_order", recheck)
    sent = []
    def send(event):
        stored = json.loads((tmp_path / f"{event.idempotency_key}.jsonl").read_text(encoding="utf-8"))
        assert stored["authorization"]["session_id"] == "session"
        assert stored["authorization"]["source_message_sequence"] == 1
        assert stored["evidence"]["citations"][0]["clause_id"] == "RF-081"
        sent.append(event)
        return RefundResult(status="simulated_succeeded", refund=RefundReceipt(
            order_id="DEMO-1002", refund_id="mock", amount_fen=100,
        ))

    monkeypatch.setattr(handler, "send", send)
    denial = candidate("RF-098", text=SHIP_DENIAL)
    deny = [rule(0, SHIP_DENIAL, ["unpaid" if case == "unpaid" else "shipped"], outcome="denied")]
    allow = [rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]])]
    if case in ("renumbered", "refetch_shipped"):
        first, extractions = [candidate("RF-081", text=ONLY_UNSHIPPED)], [allow, deny]
    elif case == "conflict":
        first, extractions = [candidate("RF-082", text=ONLY_SHIPPED)], [[rule(0, ONLY_SHIPPED, ["paid", "shipped"])]]
    elif case == "unrelated_denial":
        text = "已支付的本人订单不办理退款。"
        first, extractions = [candidate("RF-082", text=text)], [[rule(0, text, ["paid", "owned"], outcome="denied")]]
    elif case == "omitted_exception":
        text = "本人已支付且未发货的订单可办理整单原路退款。"
        first = [candidate("RF-081", text=f"{text}支付超过七天的订单除外。")]
        extractions = [[rule(0, text, ["owned", "paid", "unshipped"])]]
    elif case in ("failed", "empty"):
        first, extractions = ("failed" if case == "failed" else []), []
    else:
        first, extractions = [denial], [deny]
    state, calls = invoke_eligibility(
        first, extractions, second=[denial] if case == "refetch_shipped" else [],
        payment="unpaid" if case == "unpaid" else "paid", shipping="shipped" if shipped else "unshipped",
        handling=handler,
    )
    assert calls == [("formal", None)] + (
        [("formal", 11)] if case in ("empty", "unrelated_denial", "omitted_exception", "refetch_shipped") else []
    )
    if case == "renumbered":
        assert len(sent) == 1 and "模拟退款成功" in state["reply"]
        return
    assert not sent and not list(tmp_path.iterdir())
    assert "未提交退款" in state["reply"] and "本轮仅解释资格" not in state["reply"]
    if case in ("unpaid", "shipped", "refetch_shipped"):
        assert "退款办理已拒绝" in state["reply"] and SHIP_DENIAL in state["reply"] and "RF-098" in state["reply"]
    elif case == "conflict":
        assert "冲突" in state["reply"] and "依据：《" not in state["reply"]
    else:
        assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]
