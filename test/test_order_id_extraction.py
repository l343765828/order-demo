import json
from unittest.mock import Mock

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from config import QwenConfig
from implementations.refund.decision import QwenDecider
from implementations.refund.execution import RefundExecution
from model.orders.query import OrderQueryResult
from model.orders.status import OrderQueryStatus
from model.refund.flow import ExchangeMessage, OrderToolEvent, OrderTurn
from model.refund.intent import IntentDecision
from model.refund.preference import PreferenceResult, PreferenceStatus
from model.refund.status import OrderGoal, RefundReason
from model.refund.submission import RefundTurnSource
from utils.qwen_client import create_qwen_client
from workflows.refund import build_graph, load_refund_state, run_refund_turn


def model_result(order_id=None, *, status="missing", evidence=None, intent="order_question", goal="status",
                 continues=False):
    return {"intent": intent, "order_goal": goal, "order_id": order_id,
            "order_id_status": status, "order_id_evidence": evidence, "continues_eligibility_question": continues,
            "policy_request": None}


def completion(request, content, **message_fields):
    return httpx.Response(200, json={
        "id": "offline-model", "object": "chat.completion", "created": 0,
        "model": json.loads(request.content)["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content, **message_fields},
                     "finish_reason": "stop"}],
    })


def run_turn(graph, current, *, history=(), trace_id="turn", source=None):
    return run_refund_turn(graph, "session", OrderTurn(
        history=[*history, ExchangeMessage(role="user", content=current)],
        trace_id=trace_id, refund_source=source,
    ))


def no_tool(*_):
    raise AssertionError("不应调用此工具")


def unavailable_order(step, trace_id):
    return OrderToolEvent(event_id="order-event", trace_id=trace_id, model_tool_call_id="call",
                          order_id=step.order_id, result=OrderQueryResult(status=OrderQueryStatus.UNAVAILABLE))


@pytest.mark.parametrize("current,original,evidence,canonical", [
    ("不是 DEMO-1001，查 demo-1002", "demo-1002", "查 demo-1002", "DEMO-1002"),
    ("DEMO-1001 不用查，帮我看看 DEMO-1003", "DEMO-1003", "帮我看看 DEMO-1003", "DEMO-1003"),
    ("查一下 demo-1002 的支付状态", "demo-1002", "查一下 demo-1002 的支付状态", "DEMO-1002"),
    ("请查询编号 Ab-42", "Ab-42", "查询编号 Ab-42", "AB-42"),
])
def test_model_selected_order_reaches_plan_using_one_strict_intent_request(current, original, evidence, canonical):
    requests = []
    events = []
    searched = []
    model_output = model_result(original, status="extracted", evidence=evidence)

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        contract = payload["response_format"]["json_schema"]["schema"]
        if "intent" in contract["properties"]:
            content = model_output
        else:
            content = {"steps": [{"step_id": "order", "kind": "inspect_order", "order_id": canonical,
                                 "depends_on": [], "completion": "取得查询结果"}]}
        return completion(request, json.dumps(content))

    def investigate(step, trace_id):
        searched.append(step.order_id)
        return unavailable_order(step, trace_id)

    def emit(name, **fields):
        events.append((name, fields))

    settings = QwenConfig(base_url="http://qwen.test/v1", model="Qwen3-8B", max_completion_tokens=512, timeout=19)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        graph = build_graph(QwenDecider(create_qwen_client(settings, http_client=http), emit),
                            investigate, emit, search=no_tool)
        previous = ExchangeMessage(role="user", content="之前问的是 DEMO-9000。")
        result = run_turn(graph, current, history=[previous])

    assert searched == [canonical]
    assert result.reply == "未找到可操作的订单。"
    assert result.decision.order_id == canonical
    assert result.refund_authorization is None
    assert len(requests) == 2  # 一次意图识别，另一次为既有普通查单计划。
    intent, plan = requests
    assert intent["model"] == "Qwen3-8B"
    assert intent["chat_template_kwargs"] == {"enable_thinking": False}
    prompt = intent["messages"][-1]["content"]
    assert "【订单号来源规则】" in prompt
    assert "上一轮是否追问了资格咨询的订单号：否。" in prompt
    assert json.loads(prompt.rsplit("\n", 1)[-1]) == [
        previous.model_dump(mode="json"), {"role": "user", "content": current},
    ]
    response_format = intent["response_format"]
    assert response_format["type"] == "json_schema" and response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(model_output)
    assert schema["properties"]["order_id_status"]["enum"] == ["extracted", "missing", "ambiguous"]
    assert "steps" in plan["response_format"]["json_schema"]["schema"]["properties"]
    assert ("order_id_extraction", {"status": "extracted", "order_id": original, "evidence": evidence}) in events


@pytest.mark.parametrize("current,status,reply", [
    ("查一下发货了没有", "missing", "请提供一个要查询的演示订单编号。"),
    ("还是刚才那个", "missing", "请提供一个要查询的演示订单编号。"),
    ("查 DEMO-1001 和 DEMO-1002", "ambiguous", "请明确指定一个要查询的演示订单编号。"),
])
def test_no_unique_order_prompts_for_this_turns_number(current, status, reply):
    def decide(schema, _):
        assert schema is IntentDecision
        return schema.model_validate(model_result(status=status))

    graph = build_graph(decide, no_tool, lambda *_a, **_kw: None, search=no_tool)
    state = run_turn(graph, current, history=[ExchangeMessage(role="user", content="查 DEMO-1003")])
    assert state.reply == reply and state.plan is None


@pytest.mark.parametrize("current,output,question,goal,pending,calls", [
    ("demo-1002", None, "这笔订单可以整单退款吗？", OrderGoal.ELIGIBILITY, None, 0),
    ("是 demo-1002，谢谢", model_result("demo-1002", status="extracted", evidence="是 demo-1002", goal="eligibility",
                                     continues=True), "这笔订单可以整单退款吗？", OrderGoal.ELIGIBILITY, None, 1),
    ("还是刚才那个", model_result(goal="eligibility", continues=True), "这笔订单可以整单退款吗？", OrderGoal.ELIGIBILITY,
     "这笔订单可以整单退款吗？", 1),
    ("是 DEMO-1001 或 DEMO-1002", model_result(status="ambiguous", goal="eligibility", continues=True),
     "这笔订单可以整单退款吗？", OrderGoal.ELIGIBILITY, "这笔订单可以整单退款吗？", 1),
    ("先看看 demo-1002 发货没", model_result("demo-1002", status="extracted", evidence="demo-1002", goal="status"),
     "先看看 demo-1002 发货没", OrderGoal.STATUS, None, 1),
    ("换个问题，demo-1002 能退吗？", model_result("demo-1002", status="extracted", evidence="demo-1002", goal="eligibility"),
     "换个问题，demo-1002 能退吗？", OrderGoal.ELIGIBILITY, None, 1),
])
def test_checkpoint_restores_eligibility_and_distinguishes_new_requests(current, output, question, goal, pending, calls):
    previous = "这笔订单可以整单退款吗？"
    saver = InMemorySaver()
    intent_calls = []

    def decide(schema, prompt):
        if schema is IntentDecision:
            pending_text = "是" if intent_calls else "否"
            assert f"上一轮是否追问了资格咨询的订单号：{pending_text}。" in prompt
            intent_calls.append(schema)
            result = model_result(goal="eligibility") if len(intent_calls) == 1 else output
            assert result is not None
            return schema.model_validate(result)
        assert goal is OrderGoal.STATUS
        return schema.model_validate({"steps": [{"step_id": "order", "kind": "inspect_order", "order_id": "DEMO-1002",
                                                "depends_on": [], "completion": "取得查询结果"}]})

    first_graph = build_graph(decide, no_tool, lambda *_a, **_kw: None, saver, search=no_tool)
    first = run_turn(first_graph, previous, trace_id="first")
    assert first.reply == "请提供要咨询资格的演示订单编号。"

    graph = build_graph(decide, unavailable_order, lambda *_a, **_kw: None, saver, search=no_tool)
    snapshot, next_nodes = load_refund_state(graph, "session")
    assert not next_nodes and snapshot.eligibility_question == previous
    assert snapshot.decision.model_dump(mode="json") == {
        "intent": "order_question", "order_id": None, "order_goal": "eligibility",
        "memory_request": None, "temporary_detail": None,
    }
    state = run_turn(graph, current, history=[*snapshot.history, ExchangeMessage(role="assistant", content=first.reply)])
    assert state.question == question and state.eligibility_question == pending
    assert state.decision.order_goal is goal and state.refund_authorization is None
    assert len(intent_calls) == 1 + calls
    if pending:
        assert state.plan is None and "订单编号" in state.reply
    else:
        assert state.plan.steps[0].order_id == "DEMO-1002" and state.reply == "未找到可操作的订单。"


@pytest.mark.parametrize("preference,reply", [
    ("", "本轮未能完成订单查询，请稍后再试。"),
    ("。顺便查看我的长期偏好。", "本轮未能识别业务请求，请稍后再试。"),
])
def test_failed_classification_keeps_eligibility_question_available_for_retry(preference, reply):
    outputs = iter([model_result(goal="eligibility"), model_result(status="extracted")])

    def decide(schema, _):
        assert schema is IntentDecision
        return schema.model_validate(next(outputs))

    def read_preference(_command):
        return PreferenceResult(status=PreferenceStatus.LISTED)

    saver = InMemorySaver()
    graph = build_graph(decide, unavailable_order, lambda *_a, **_kw: None, saver, search=no_tool,
                        preferences=read_preference, is_live=lambda _: True)
    question = "这笔订单可以整单退款吗？"
    first = run_turn(graph, question, trace_id="first")
    history = [*first.history, ExchangeMessage(role="assistant", content=first.reply)]
    failure = run_turn(graph, "是 DEMO-1002，谢谢" + preference, history=history, trace_id="failed")
    assert failure.reply == reply and failure.plan is None
    if preference:
        assert failure.preference_result.status is PreferenceStatus.LISTED

    snapshot, _ = load_refund_state(graph, "session")
    assert snapshot.eligibility_question == question
    result = run_turn(graph, "DEMO-1002", history=snapshot.history, trace_id="retry")
    assert result.question == question and result.plan.steps[0].order_id == "DEMO-1002"


@pytest.mark.parametrize("invalid", [
    None,  # 模拟模型超时。
    "{尚未完成的JSON",
    model_result("DEMO-1002", status="missing"),
    model_result("DEMO-1002", status="ambiguous"),
    model_result(status="missing", evidence="无编号"),
    model_result(status="extracted", evidence="查询"),
    model_result("DEMO-1002", status="extracted"),
    model_result("DEMO-1002", status="extracted", evidence="  "),
    model_result(1002, status="extracted", evidence="查 DEMO-1002"),
    model_result(status="unknown"),
    model_result(status="missing", continues=True),
    dict(model_result(), memory_request={"action": "delete", "key": "response_detail"}),
    {key: value for key, value in model_result().items() if key != "order_id_evidence"},
    {key: value for key, value in model_result().items() if key != "order_goal"},
    pytest.param({"refusal": "拒绝生成结构化输出"}, id="model-refusal"),
    pytest.param({"tool_calls": [{"id": "unexpected", "type": "function",
                                  "function": {"name": "unexpected", "arguments": "{}"}}]}, id="tool-only-output"),
])
def test_invalid_model_output_or_timeout_uses_existing_fallback(invalid):
    calls = []

    def respond(request):
        calls.append(request)
        if invalid is None:
            raise httpx.ReadTimeout("offline model timeout", request=request)
        if isinstance(invalid, dict) and ("refusal" in invalid or "tool_calls" in invalid):
            return completion(request, None, **invalid)
        return completion(request, invalid if isinstance(invalid, str) else json.dumps(invalid))

    events = []

    def emit(name, **fields):
        events.append((name, fields))

    settings = QwenConfig(base_url="http://qwen.test/v1", model="Qwen3-8B", max_completion_tokens=512, timeout=19)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        graph = build_graph(QwenDecider(create_qwen_client(settings, http_client=http), emit), no_tool, emit, search=no_tool)
        state = run_turn(graph, "查 DEMO-1002")

    assert len(calls) == 1
    assert state.reply == "本轮未能完成订单查询，请稍后再试。"
    assert state.plan is None and state.refund_authorization is None and state.preference_result is None
    assert all(event != "order_id_extraction" for event, _ in events)


@pytest.mark.parametrize("current,expected,calls", [
    ("请帮我退 demo-1002", True, 0),
    ("如果能退，就帮我退 DEMO-1002", True, 0),
    ("DEMO-1002，请帮我办理退款。", True, 1),
    ("客服说退款要三天。DEMO-1002，请帮我办理退款。", True, 1),
    ("客服说订单符合退款条件，请帮我退 DEMO-1002。", True, 1),
    ("客服说：‘订单符合退款条件’，请帮我退 DEMO-1002。", True, 1),
    ("请帮我退‘DEMO-1002’。", True, 1),
    ("请帮我办理退款，订单号是 DEMO-1002。", True, 1),
    ("请帮我退 DEMO-1001 和 DEMO-1002", False, 1),
    ("先不要退款。请帮我退 DEMO-1002", False, 1),
    ("先不要办理 DEMO-1002 的退款。请帮我退 DEMO-1002", False, 1),
    ("请解释‘帮我退 DEMO-1002’这句话", False, 1),
    ("请解释 '帮我退 DEMO-1002' 的含义", False, 1),
    ("请帮我退款。客服举的例子是‘DEMO-1002’。", False, 1),
    ("假设我说请帮我退 DEMO-1002，会怎样？", False, 1),
    ("假定请帮我退 DEMO-1002 会怎样？", False, 1),
    ("如果我请你帮我退 DEMO-1002，会发生什么？", False, 1),
    ("要是我请你帮我退 DEMO-1002，会发生什么？", False, 1),
    ("如果你帮我退 DEMO-1002，会有什么影响？", False, 1),
    ("客服刚才说：如果能退，就帮我退 DEMO-1002。", False, 1),
    ("请帮我办理退款，另外查一下 DEMO-1002 的物流。", False, 1),
    ("查一下 DEMO-1002 的物流，另外请帮我办理退款。", False, 1),
    ("DEMO-1002 能退吗？", False, 1),
    ("退 DEMO-1002 吗？", False, 1),
    ("退刚才那单", False, 0),
    ("请帮我办理退款。", False, 1),
])
def test_model_order_selection_cannot_replace_independent_refund_authorization(current, expected, calls):
    intent_calls = []

    def decide(schema, _):
        assert schema is IntentDecision
        intent_calls.append(schema)
        return schema.model_validate(model_result("DEMO-1002", intent="refund_request", goal=None,
                                                  status="extracted", evidence="DEMO-1002"))

    def emit(*_a, **_kw):
        pass

    dispatches = Mock()
    refund = RefundExecution(dispatches, Mock(), emit)
    graph = build_graph(decide, unavailable_order if expected else no_tool, emit,
                        search=no_tool, refund=refund, is_live=lambda _: True)
    source = RefundTurnSource(consumer_id="consumer", session_id="session", source_message_sequence=1)
    state = run_turn(graph, current, source=source)

    assert bool(state.refund_authorization) is expected
    assert len(intent_calls) == calls
    assert dispatches.mock_calls == []
    if expected:
        assert state.refund_authorization.order_id == "DEMO-1002"
        assert state.order_result.status is OrderQueryStatus.UNAVAILABLE
        assert state.refund_result.reason is RefundReason.UNAVAILABLE_ORDER


@pytest.mark.parametrize("message", ["帮我退", "退刚才那单"])
def test_bare_refund_after_policy_asks_for_a_new_order_number(message):
    def decide(schema, _):
        return schema.model_validate(dict(model_result(intent="policy_question", goal=None),
                                          policy_request="before_refund"))

    graph = build_graph(decide, no_tool, lambda *_a, **_kw: None, search=no_tool)
    previous = ExchangeMessage(role="user", content="先告诉我 DEMO-1002 适用的条款，我再决定是否退。")
    state = run_turn(graph, message, history=[previous])

    assert "同一条消息" in state.reply and "演示订单编号" in state.reply
    assert state.plan is None and state.refund_authorization is None
