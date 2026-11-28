import json
import sys
from pathlib import Path

import pytest
from langchain_openai.chat_models.base import OpenAIRefusalError
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from implementations.policy.answer import NO_EVIDENCE, POLICY_UNAVAILABLE
from implementations.policy.evidence import verify_policy_evidence
from implementations.policy.eligibility import answer_eligibility, eligibility_reply
from implementations.refund import execution
from model.orders.query import OrderQueryResult
from model.policy.evidence import PolicyCitation, PolicyEvidence
from model.policy.search import PolicyCandidate, PolicySearchResult
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus
from model.refund.flow import OrderToolEvent, PolicyToolEvent
from model.refund.intent import IntentDecision
from model.refund.status import RefundQualification
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
        PolicyEvidence(question="问题", query="查询", status=PolicyEvidenceStatus.SUFFICIENT,
                       catalog_revision=11, citations=[])


def test_successful_mcp_response_requires_revision_even_when_empty():
    assert receipt().catalog_revision == 11
    for revision in (None, "11", True, -1):
        with pytest.raises(ValueError):
            receipt(revision=revision)


def test_restored_policy_state_requires_current_fields():
    with pytest.raises(ValidationError, match="catalog_revision"):
        PolicySearchResult.model_validate({"status": "ok", "event_id": "old", "candidates": []})
    with pytest.raises(ValidationError, match="catalog_revision"):
        PolicyEvidence.model_validate({"question": "问题", "query": "查询", "status": "insufficient", "citations": []})
    with pytest.raises(ValidationError, match="entry_type"):
        PolicyCitation.model_validate({"event_id": "event", "document_id": "doc", "version": "1.0",
                                       "clause_id": "RF-001", "excerpt": "条款原文"})


POPULAR_FAQ = candidate("FAQ-123", "faq", "退款什么时候到？\n没有到账承诺。")
UNRELATED_FAQ = candidate("FAQ-456", "faq", "退款用什么渠道？\n原路退回。")
POLICY = candidate("RF-008", text="本演示不处理银行到账。")


@pytest.mark.parametrize(("first", "second", "choices", "expected", "citation", "second_revision"), [
    ([POLICY, POPULAR_FAQ, UNRELATED_FAQ], None, [1], "没有到账承诺。", "FAQ-123", 11),
    ([UNRELATED_FAQ, POLICY, POPULAR_FAQ], None, [2], "没有到账承诺。", "FAQ-123", 11),
    ([UNRELATED_FAQ, POLICY, POPULAR_FAQ], None, [1], POLICY.text, "RF-008", 11),
    ([UNRELATED_FAQ, POLICY, POPULAR_FAQ], [POLICY], [None, 0], POLICY.text, "RF-008", 11),
    ([UNRELATED_FAQ], [POLICY], [0, 0], POLICY.text, "RF-008", 11),
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
        if schema is IntentDecision:
            return schema(intent="policy_question", order_id=None, order_goal=None, order_id_status="missing",
                          order_id_evidence=None, continues_eligibility_question=False)
        options = json.loads(prompt.rsplit("材料（数组从 0 起）：", 1)[1])
        candidates = first if len(calls) == 1 else second
        assert options == [f"[{item.entry_type.value}] {item.text}" for item in candidates]
        return schema(source_index=next(selections), retry_query="正式到账条款")

    def search(step, trace_id, mode):
        calls.append((mode, step.catalog_revision, step.query))
        selected = first if len(calls) == 1 else second
        result = (
            PolicySearchResult(status="failed", event_id="failure", candidates=[], catalog_revision=None)
            if selected == "failed"
            else receipt(selected, revision=11 if len(calls) == 1 else second_revision, mode=mode)
        )
        return PolicyToolEvent(event_id=f"tool-{mode}", trace_id=trace_id, model_tool_call_id="model",
                               query=step.query, result=result)

    def no_order(*_):
        pytest.fail("general policy question does not look up orders")

    graph = build_graph(decide, no_order, lambda *_a, **_kw: None, search=search)
    state = graph.invoke({"history": [{"role": "user", "content": "退款多久能到账"}],
                          "trace_id": "trace", "refund_source": None})
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


def rule(index, excerpt, conditions, *, exceptions=(), outcome="full_original", answers_policy_question=False):
    return dict(source_index=index, excerpt=excerpt, conditions=conditions,
                exceptions=[{"conditions": group} for group in exceptions], outcome=outcome,
                **({"answers_policy_question": True} if answers_policy_question else {}))


def interpret_policy(first, extractions, *, second=()):
    interpretations = iter(extractions)

    def decide(schema, _prompt):
        extracted = next(interpretations)
        if isinstance(extracted, Exception):
            raise extracted
        return schema.model_validate({"rules": extracted})

    order = OrderQueryResult.model_validate({"status": "found", "order": {
        "order_id": "DEMO-1002", "payment_status": "paid", "shipping_status": "unshipped",
        "refund_status": "not_refunded", "paid_amount_fen": 100,
    }})
    question = "DEMO-1002 的退款依据是什么？"
    results = [receipt(first)]
    calls = [("consultation", None)]
    answer = answer_eligibility(decide, question, question, results, order, qualification=RefundQualification.ELIGIBLE)
    if answer.retry_query:
        results.append(receipt(second, mode="formal"))
        calls.append(("formal", results[0].catalog_revision))
        answer = answer_eligibility(decide, question, answer.retry_query, results, order,
                                   qualification=RefundQualification.ELIGIBLE)
    return {
        "reply": eligibility_reply(order, RefundQualification.ELIGIBLE, answer.policy_message or POLICY_UNAVAILABLE),
        "observation": {"policy_evidence": answer.evidence.model_dump(mode="json")},
    }, calls


def invoke_eligibility(first, extractions, *, second=(), payment="paid", shipping="unshipped",
                       refund="not_refunded", second_revision=11, handling=None, order_status="found",
                       queries=None, events=None, resume_at=None, question=None, policy_request=None):
    calls = []
    interpretations = iter(extractions)

    def decide(schema, prompt):
        if schema is IntentDecision:
            return schema(
                intent="refund_request" if handling else "order_question", order_id="DEMO-1002",
                order_goal=None if handling else "eligibility", order_id_status="extracted",
                order_id_evidence="DEMO-1002", continues_eligibility_question=False, policy_request=policy_request,
            )
        extracted = next(interpretations)
        if callable(extracted):
            extracted = extracted(prompt)
        if isinstance(extracted, Exception):
            raise extracted
        return schema.model_validate({"rules": extracted})

    def get_order(step, trace_id):
        order = {
            "order_id": "DEMO-1002", "payment_status": payment, "shipping_status": shipping,
            "refund_status": refund, "paid_amount_fen": 100 if payment == "paid" else 0,
        } if order_status == "found" else None
        result = OrderQueryResult.model_validate({"status": order_status, "order": order})
        return OrderToolEvent(event_id="order", trace_id=trace_id, model_tool_call_id="model",
                              order_id=step.order_id, result=result)

    def search(step, trace_id, mode):
        calls.append((mode, step.catalog_revision))
        if queries is not None:
            queries.append((step.step_id, step.query))
        if first == "failed":
            result = PolicySearchResult(status="failed", event_id="failure", candidates=[], catalog_revision=None)
        else:
            initial = step.catalog_revision is None
            result = receipt(first if initial else second, mode=mode,
                             revision=11 if initial else second_revision)
        return PolicyToolEvent(event_id=f"tool-{mode}", trace_id=trace_id, model_tool_call_id="model",
                               query=step.query, result=result)

    def emit(name, **data):
        if events is not None:
            events.append((name, data))

    graph = build_graph(decide, get_order, emit, checkpointer=InMemorySaver() if resume_at else None,
                        search=search, refund=handling, is_live=lambda trace: trace == "trace")
    turn = {"history": [{"role": "user", "content": "DEMO-1002 能退吗？ 联系信息：测试备注"}],
            "trace_id": "trace", "refund_source": None}
    if handling:
        turn["history"][0]["content"] = "请帮我退 DEMO-1002"
        turn["refund_source"] = {"consumer_id": "consumer", "session_id": "session", "source_message_sequence": 1}
    if question is not None:
        turn["history"][0]["content"] = question
    if resume_at:
        config = {"configurable": {"thread_id": "session"}}
        paused = graph.invoke(turn, config, interrupt_after=[resume_at])
        assert graph.get_state(config).next == ("execute",)
        assert [step["step_id"] for step in paused["plan"]["steps"]] == ["order", "policy"]
        assert paused["step_index"] == 1 and calls == []
        state = graph.invoke(None, config)
    else:
        state = graph.invoke(turn)
    assert (state.get("refund_authorization") is not None) == bool(handling)
    return state, calls


@pytest.mark.parametrize("question,policy_request", [
    ("DEMO-1002 适用哪条退款政策？", "basis"),
    ("我想看完条款再决定是否退 DEMO-1002。", "before_refund"),
    ("如果查得到适用的正式条款就帮我退 DEMO-1002。", "before_refund"),
])
def test_explicit_order_policy_cites_applicable_formal_rule_after_checkpoint(question, policy_request):
    policy = candidate("RF-081", text=ONLY_UNSHIPPED).model_copy(update={"version": "2.0"})
    state, calls = invoke_eligibility([], [[rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"],
                                                exceptions=[["refunded"]], answers_policy_question=True)]], second=[policy],
                                      question=question, policy_request=policy_request, resume_at="replan")

    assert calls == [("formal", None), ("formal", 11)]
    assert "2.0 版 RF-081" in state["reply"] and ONLY_UNSHIPPED in state["reply"]
    assert ("本轮未提交退款" in state["reply"]) == (policy_request == "before_refund")


@pytest.mark.parametrize("topic", ["退款到账时限", "退款何时退回付款账户"])
def test_policy_condition_keeps_the_specific_question_without_the_order_number(topic):
    def no_timing_rule(prompt):
        assert topic in prompt and "DEMO-1002" not in prompt
        return [rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]],
                     answers_policy_question=True)]

    queries = []
    state, calls = invoke_eligibility([candidate(text=ONLY_UNSHIPPED)], [no_timing_rule],
                                      question=f"如果查到 DEMO-1002 {topic}的正式条款就帮我退。",
                                      policy_request="before_refund", queries=queries)
    assert calls == [("formal", None), ("formal", 11)]
    assert all(topic in query and "DEMO-1002" not in query for _, query in queries)
    assert all("本人订单退款资格" not in query for _, query in queries)
    assert POLICY_UNAVAILABLE in state["reply"] and "依据：《" not in state["reply"]


def test_policy_condition_can_cite_a_formal_clause_about_the_specific_term():
    excerpt = "模拟退款不承诺到账时限。"
    state, calls = invoke_eligibility(
        [candidate("RF-098", text=excerpt)],
        [[rule(0, excerpt, [], outcome="no_explicit_decision", answers_policy_question=True)]],
        question="如果有 DEMO-1002 的到账时限条款就帮我退。", policy_request="before_refund",
    )
    assert calls == [("formal", None)]
    assert "RF-098" in state["reply"] and excerpt in state["reply"] and "本轮未提交退款" in state["reply"]


def test_eligibility_cites_renumbered_policy_after_same_snapshot_followup():
    new = candidate("RF-081", text=ONLY_UNSHIPPED).model_copy(update={"version": "2.0"})
    different_scope = candidate("RF-080", text=ONLY_SHIPPED)
    state, calls = interpret_policy(
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
        [candidate("RF-098", text=SHIP_DENIAL)],
        [[rule(0, SHIP_DENIAL, conditions, outcome="denied")]],
        shipping=shipping, payment=payment,
    )
    assert SHIP_DENIAL in state["reply"] and "RF-098" in state["reply"] and "FAQ-081" not in state["reply"]


def test_denial_resume_and_supplement_use_one_reason_based_step_and_snapshot():
    queries, events = [], []
    state, calls = invoke_eligibility(
        [], [[rule(0, SHIP_DENIAL, ["shipped"], outcome="denied")]],
        second=[candidate("RF-098", text=SHIP_DENIAL)], shipping="shipped", queries=queries, events=events,
        resume_at="replan",
    )
    [initial] = [data["plan"] for name, data in events if name == "plan"]
    assert [step["step_id"] for step in initial["steps"]] == ["order"]
    assert calls == [("formal", None), ("formal", 11)]
    assert [step for step, _ in queries] == ["policy", "policy"]
    assert all("已发货" in query and "DEMO-1002" not in query and "测试备注" not in query for _, query in queries)
    assert state["step_index"] == 1 and len(state["plan"]["steps"]) == 2
    assert "已发货" in state["reply"] and "RF-098" in state["reply"]


def test_refunded_order_excluded_from_permission_and_uses_no_repeat_rule():
    state, _ = invoke_eligibility(
        [candidate(text=ONLY_UNSHIPPED), candidate("RF-099", text=NO_REPEAT)],
        [[rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]]),
          rule(1, NO_REPEAT, ["refunded"], outcome="denied")]],
        refund="refunded",
    )
    assert NO_REPEAT in state["reply"] and "RF-099" in state["reply"]


@pytest.mark.parametrize(("permission", "outcome"), [
    ("本人已支付且未发货的订单可以办理整单原路退款。", "full_original"),
    ("本人已支付且未发货的订单可以办理退款。", "method_unspecified"),
])
def test_refunded_order_does_not_apply_permission_excerpt_omitting_next_sentence_exception(permission, outcome):
    text = permission + "已有模拟退款记录的订单除外。"
    state, _ = invoke_eligibility(
        [candidate("RF-081", text=text), candidate("RF-099", text=NO_REPEAT)],
        [[rule(0, permission, ["owned", "paid", "unshipped"], outcome=outcome),
          rule(1, NO_REPEAT, ["refunded"], outcome="denied")]],
        refund="refunded",
    )
    assert NO_REPEAT in state["reply"] and "RF-099" in state["reply"]
    assert "冲突" not in state["reply"] and "RF-081" not in state["reply"]


@pytest.mark.parametrize("denial", [
    rule(0, "拒绝重复办理的规定仅限已有模拟退款记录的订单。", ["refunded"], outcome="denied"),
    rule(0, "非本人订单不可办理退款，经权利人书面授权的订单除外。", ["other_owner"],
         exceptions=[["unknown"]], outcome="denied"),
])
def test_eligibility_cites_permission_when_next_sentence_limits_an_independent_denial(denial):
    permission = "本人已支付且未发货的订单可以办理整单原路退款。"
    state, _ = interpret_policy(
        [candidate("RF-081", text=permission + denial["excerpt"])],
        [[rule(0, permission, ["owned", "paid", "unshipped"]), denial]],
    )
    assert permission in state["reply"] and "RF-081" in state["reply"]


def test_higher_ranked_similar_policy_does_not_override_applicable_scope():
    state, calls = interpret_policy(
        [candidate("RF-080", text=ONLY_SHIPPED), candidate("RF-081", text=ONLY_UNSHIPPED)],
        [[rule(0, ONLY_SHIPPED, ["owned", "paid", "shipped"]),
          rule(1, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]])]],
    )
    assert calls == [("consultation", None)]
    assert ONLY_UNSHIPPED in state["reply"] and "RF-081" in state["reply"]
    assert ONLY_SHIPPED not in state["reply"] and "RF-080" not in state["reply"]


def test_exception_group_requires_all_of_its_conditions():
    text = "本人已支付且未发货的订单可整单原路退款，已退款且仍未发货的订单或已发货的订单除外。"
    state, _ = interpret_policy(
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
    state, _ = interpret_policy([candidate(text=UNKNOWN_SCOPE)], [extraction])
    assert "已支付，未发货，无模拟退款记录" in state["reply"]
    assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]


def test_applicable_conflict_takes_precedence_over_consistent_clause():
    state, _ = interpret_policy(
        [candidate(text=ONLY_UNSHIPPED), candidate("RF-055", text="未发货的订单也不办理退款。")],
        [[rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]]),
          rule(1, "未发货的订单也不办理退款。", ["unshipped"], outcome="denied")]],
    )
    assert "已支付，未发货，无模拟退款记录" in state["reply"]
    assert "冲突" in state["reply"] and "依据：《" not in state["reply"]


def test_explicit_exception_cannot_be_dropped_from_a_quoted_rule():
    state, _ = interpret_policy(
        [candidate(text=ONLY_UNSHIPPED)],
        [[rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"])]],
    )
    assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]


def test_policy_that_only_permits_partial_refunds_conflicts_with_full_original_refund():
    text = "本人已支付且未发货的订单仅可办理部分退款。"
    state, _ = interpret_policy(
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
                                  shipping="shipped", second=[candidate(text=SHIP_DENIAL)], second_revision=12)
    assert "已支付，已发货，无模拟退款记录" in state["reply"]
    assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]


def test_denial_does_not_cite_faq_returned_outside_the_formal_scope():
    state, calls = invoke_eligibility([candidate("FAQ-081", "faq", SHIP_DENIAL)], [], shipping="shipped")
    assert calls == [("formal", None)]
    assert "已发货" in state["reply"] and "无法核实退款政策" in state["reply"]
    assert "依据：《" not in state["reply"]


@pytest.mark.parametrize(("status", "expected"), [
    ("unavailable", "未找到可操作的订单"), ("failed", "订单服务暂时不可用"),
])
def test_unavailable_order_does_not_start_denial_investigation(status, expected):
    state, calls = invoke_eligibility([], [], order_status=status)
    assert expected in state["reply"] and "政策" not in state["reply"]
    assert calls == []


@pytest.mark.parametrize("case", [
    "unpaid", "shipped", "failed", "empty", "conflict", "unrelated_denial", "omitted_exception",
    "omitted_scope", "omitted_denial_exception",
    "renumbered", "independent_scope", "refetch_shipped", "transaction_shipped", "transaction_policy_failed",
    "transaction_extraction_failed", "transaction_unpaid_with_unknown_shipping", "transaction_unpaid_with_unknown_exception",
])
def test_policy_can_explain_a_refund_denial_and_never_gates_an_eligible_refund(case, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock

    settings = SimpleNamespace(mcp=SimpleNamespace(write_token="writer"), refund_policy_events_dir=str(tmp_path))
    dispatches = Mock()
    dispatches.blocking_dispatch.return_value = None
    handler = execution.RefundExecution(dispatches, settings, lambda *_a, **_kw: None)
    shipped = case in ("shipped", "failed", "empty", "conflict", "unrelated_denial", "omitted_denial_exception")
    def recheck(*_):
        return OrderQueryResult.model_validate({"status": "found", "order": {
            "order_id": "DEMO-1002", "payment_status": "paid", "paid_amount_fen": 100,
            "shipping_status": "shipped" if case == "refetch_shipped" else "unshipped", "refund_status": "not_refunded",
        }})

    monkeypatch.setattr(execution.controlled_tools, "read_order", recheck)
    sent = []
    def send(event):
        assert event.authorization.session_id == "session"
        assert event.authorization.source_message_sequence == 1
        sent.append(event)
        if case.startswith("transaction_"):
            reason = "unpaid" if case.startswith("transaction_unpaid_") else "shipped"
            return RefundResult(status="denied", reason=reason)
        return RefundResult(status="simulated_succeeded", refund=RefundReceipt(
            order_id="DEMO-1002", refund_id="mock", amount_fen=100,
        ))

    monkeypatch.setattr(handler, "send", send)
    denial = candidate("RF-098", text=SHIP_DENIAL)
    deny = [rule(0, SHIP_DENIAL, ["unpaid" if case == "unpaid" else "shipped"], outcome="denied")]
    allow = [rule(0, ONLY_UNSHIPPED, ["owned", "paid", "unshipped"], exceptions=[["refunded"]])]
    if case == "refetch_shipped":
        first, extractions = [denial], [deny]
    elif case == "renumbered":
        first, extractions = [candidate("RF-081", text=ONLY_UNSHIPPED)], [allow]
    elif case == "independent_scope":
        text = f"{ONLY_UNSHIPPED}只有查询订单时才需要提供订单号。只有提供订单号才能办理退款进度查询。"
        first, extractions = [candidate("RF-081", text=text)], [allow]
    elif case == "conflict":
        first, extractions = [candidate("RF-082", text=ONLY_SHIPPED)], [[rule(0, ONLY_SHIPPED, ["paid", "shipped"])]]
    elif case == "unrelated_denial":
        text = "已支付的本人订单不办理退款。"
        first, extractions = [candidate("RF-082", text=text)], [[rule(0, text, ["paid", "owned"], outcome="denied")]]
    elif case == "omitted_exception":
        text = "本人已支付且未发货的订单可办理整单原路退款。"
        first = [candidate("RF-081", text=f"{text}支付超过七天的订单除外。")]
        extractions = [[rule(0, text, ["owned", "paid", "unshipped"])]]
    elif case == "omitted_scope":
        text = "本人已支付的订单可办理整单原路退款。"
        first = [candidate("RF-081", text=f"{text}只有已发货的订单才能办理退款。")]
        extractions = [[rule(0, text, ["owned", "paid"])]]
    elif case == "omitted_denial_exception":
        text = "已发货的订单不办理退款。"
        first = [candidate("RF-098", text=f"{text}错发商品的订单除外。")]
        extractions = [[rule(0, text, ["shipped"], outcome="denied")]]
    elif case in ("failed", "empty", "transaction_policy_failed"):
        first, extractions = ([] if case == "empty" else "failed"), []
    elif case == "transaction_extraction_failed":
        first, extractions = [denial], [OpenAIRefusalError()]
    elif case == "transaction_unpaid_with_unknown_shipping":
        text = "未支付且未发货的订单不办理退款。"
        first, extractions = [candidate("RF-098", text=text)], [[rule(0, text, ["unpaid", "unshipped"], outcome="denied")]]
    elif case == "transaction_unpaid_with_unknown_exception":
        text = "未支付的订单不办理退款，未发货的订单除外。"
        first = [candidate("RF-098", text=text)]
        extractions = [[rule(0, text, ["unpaid"], exceptions=[["unshipped"]], outcome="denied")]]
    else:
        first, extractions = [denial], [deny]
    queries, events = [], []
    state, calls = invoke_eligibility(
        first, extractions, second=[denial] if case == "refetch_shipped" else [],
        payment="unpaid" if case == "unpaid" else "paid", shipping="shipped" if shipped else "unshipped",
        handling=handler, queries=queries, events=events,
        resume_at="send_refund" if case == "transaction_shipped" else None,
    )
    if case.startswith("transaction_"):
        [result] = [data["result"] for name, data in events if name == "refund_result"]
        unpaid = case.startswith("transaction_unpaid_")
        assert result["status"] == "denied" and result["reason"] == ("unpaid" if unpaid else "shipped")
        expected_calls = [("formal", None)]
        if unpaid or case == "transaction_extraction_failed":
            expected_calls.append(("formal", 11))
        assert len(sent) == 1 and calls == expected_calls
        assert all(("未支付" if unpaid else "已发货") in query and "DEMO-1002" not in query for _, query in queries)
        assert "退款办理已拒绝" in state["reply"] and "未发货" not in state["reply"]
        assert "未提交退款" not in state["reply"] and state["order_result"] is None
        if case == "transaction_shipped":
            assert "RF-098" in state["reply"]
        else:
            assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]
        return
    if case == "omitted_denial_exception":
        assert "退款办理已拒绝" in state["reply"] and "已发货" in state["reply"]
        assert "RF-098" not in state["reply"]
    assert not list(tmp_path.iterdir())
    if case in ("renumbered", "independent_scope", "omitted_exception", "omitted_scope"):
        assert not calls
        assert len(sent) == 1 and "模拟退款成功" in state["reply"]
        return
    assert calls == [("formal", None)] + (
        [("formal", 11)] if case in (
            "empty", "unrelated_denial", "omitted_denial_exception",
        ) else []
    )
    assert not sent
    assert "未提交退款" in state["reply"] and "本轮仅解释资格" not in state["reply"]
    if case in ("unpaid", "shipped", "refetch_shipped"):
        assert "退款办理已拒绝" in state["reply"] and SHIP_DENIAL in state["reply"] and "RF-098" in state["reply"]
    elif case == "conflict":
        assert "冲突" in state["reply"] and "依据：《" not in state["reply"]
        assert any(name == "policy_evidence" and data["evidence"]["status"] == "conflict" for name, data in events)
    else:
        assert "无法核实退款政策" in state["reply"] and "依据：《" not in state["reply"]
