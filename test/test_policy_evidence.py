import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
import pytest

from model.policy import PolicyCandidate, PolicySearchResult
from model.policy_evidence import PolicyCitation, PolicyEvidence, verify_policy_evidence
from model.order_flow import OrderToolEvent, PolicyToolEvent
from model.order_query import OrderQueryResult
from model.status import PolicyEvidenceStatus, PolicySearchStatus
from order_flow import build_graph
from policy_answer import NO_EVIDENCE, POLICY_UNAVAILABLE, published_clauses


def candidate():
    return PolicyCandidate(document_id='DEMO-REFUND-POLICY', title='演示退款政策与常见问题',
                           version='1.0', clause_id='RF-003', entry_type='policy', text='已支付且未发货')


def test_citation_must_match_current_retrieval_and_original_text():
    result = PolicySearchResult(status=PolicySearchStatus.OK, event_id='event-1', candidates=[candidate()])
    citation = PolicyCitation(event_id='event-1', document_id='DEMO-REFUND-POLICY', version='1.0',
                              clause_id='RF-003', excerpt='已支付且未发货')
    evidence = PolicyEvidence(question='能退吗', query='退款条件',
                              status=PolicyEvidenceStatus.SUFFICIENT, citations=[citation])
    assert verify_policy_evidence(evidence, [result])
    for bad in (citation.model_copy(update={'event_id': 'old'}),
                citation.model_copy(update={'version': '0.9'}),
                citation.model_copy(update={'clause_id': 'RF-999'}),
                citation.model_copy(update={'excerpt': ' '}),
                citation.model_copy(update={'excerpt': '无条件退款'})):
        assert not verify_policy_evidence(evidence.model_copy(update={'citations': [bad]}), [result])
    failed = PolicySearchResult(status=PolicySearchStatus.FAILED, event_id='event-1', candidates=[])
    assert not verify_policy_evidence(evidence, [failed])
    forged = candidate().model_copy(update={'clause_id': 'RF-999'})
    invented = citation.model_copy(update={'clause_id': 'RF-999'})
    assert not verify_policy_evidence(evidence.model_copy(update={'citations': [invented]}),
                                      [result.model_copy(update={'candidates': [forged]})])


def test_sufficient_evidence_requires_formal_citation():
    with pytest.raises(ValidationError):
        PolicyEvidence(question='问题', query='查询', status=PolicyEvidenceStatus.SUFFICIENT, citations=[])


@pytest.mark.parametrize(("ranks", "selection", "expected", "modes"), [
    (("FAQ-004", "RF-001", "RF-008"), 0, "不承诺到账期限", ["consultation"]),
    (("FAQ-004", "RF-008"), 0, NO_EVIDENCE, ["consultation", "formal"]),
    (("FAQ-004", "RF-001@old", "RF-008"), 0, NO_EVIDENCE, ["consultation", "formal"]),
    (("FAQ-002", "RF-005"), None, NO_EVIDENCE, ["consultation", "formal"]),
    (("FAQ-004", "RF-001", "RF-008"), 99, NO_EVIDENCE, ["consultation", "formal"]),
    ((), None, POLICY_UNAVAILABLE, ["consultation"]),
])
def test_policy_consultation_requires_relevant_supported_current_evidence(ranks, selection, expected, modes):
    actual_modes = []

    def decide(schema, prompt):
        if schema.__name__ == "ConversationDecision":
            return schema(intent="policy_question", order_id=None)
        return schema(source_index=selection, retry_query="到账期限正式政策")

    def search(step, trace_id, mode):
        actual_modes.append(mode)
        clauses = published_clauses()
        candidates = [
            clauses[clause.partition("@")[0]].model_copy(update={"version": "0.9"})
            if clause.endswith("@old") else clauses[clause]
            for clause in ranks if mode == "consultation" or clause.startswith("RF")
        ]
        result = PolicySearchResult(
            event_id=f"retrieval-{mode}", status=PolicySearchStatus.OK if ranks else PolicySearchStatus.FAILED,
            candidates=candidates,
        )
        return PolicyToolEvent(event_id=f"tool-{mode}", trace_id=trace_id, model_tool_call_id="model",
                               query=step.query, result=result)

    def no_order(*_):
        pytest.fail("general policy question does not look up orders")

    graph = build_graph(decide, no_order, lambda *_a, **_kw: None, search=search)
    state = graph.invoke({"history": [{"role": "user", "content": "退款多久能到账"}], "trace_id": "trace"})
    assert expected in state["reply"]
    assert actual_modes == modes
    assert type(state["observation"]["status"]) is str
    assert type(state["observation"]["policy_result"]["status"]) is str
    if len(modes) == 1 and ranks:
        assert "RF-001、RF-008" in state["reply"]


def test_unpaid_order_cannot_use_eligible_paid_order_clause():
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
        result = PolicySearchResult(event_id=f"retrieval-{mode}", status=PolicySearchStatus.OK,
                                    candidates=[published_clauses()["RF-003"]])
        return PolicyToolEvent(event_id=f"tool-{mode}", trace_id=trace_id, model_tool_call_id="model",
                               query=step.query, result=result)

    graph = build_graph(decide, get_order, lambda *_a, **_kw: None, search=search)
    state = graph.invoke({"history": [{"role": "user", "content": "DEMO-1002 能退吗？"}], "trace_id": "trace"})
    assert state["reply"] == NO_EVIDENCE
