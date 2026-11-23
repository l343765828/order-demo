import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from implementations.policy.answer import NO_EVIDENCE, POLICY_UNAVAILABLE
from implementations.policy.evidence import verify_policy_evidence
from model.orders.query import OrderQueryResult
from model.policy.evidence import PolicyCitation, PolicyEvidence
from model.policy.search import PolicyCandidate, PolicySearchResult
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus
from model.refund.flow import OrderToolEvent, PolicyToolEvent
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


@pytest.mark.parametrize("incorrect", [candidate(), candidate("RF-004", "faq")])
def test_unpaid_order_cannot_use_wrong_policy_or_faq(incorrect):
    def decide(schema, _):
        assert schema.__name__ == "ConversationDecision"
        return schema(intent="order_question", order_id="DEMO-1002", order_goal="eligibility")

    def get_order(step, trace_id):
        result = OrderQueryResult.model_validate({"status": "found", "order": {
            "order_id": "DEMO-1002", "payment_status": "unpaid", "shipping_status": "unshipped",
            "refund_status": "not_refunded", "paid_amount_fen": 0, "currency": "CNY",
        }})
        return OrderToolEvent(event_id="order", trace_id=trace_id, model_tool_call_id="model",
                              order_id=step.order_id, result=result)

    def search(step, trace_id, mode):
        result = receipt([incorrect] if mode == "consultation" else [], mode=mode)
        return PolicyToolEvent(event_id=f"tool-{mode}", trace_id=trace_id, model_tool_call_id="model",
                               query=step.query, result=result)

    graph = build_graph(decide, get_order, lambda *_a, **_kw: None, search=search)
    state = graph.invoke({"history": [{"role": "user", "content": "DEMO-1002 能退吗？"}], "trace_id": "trace"})
    assert state["reply"] == NO_EVIDENCE
