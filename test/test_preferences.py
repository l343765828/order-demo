import sys
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model.refund.flow import ConversationDecision, ExchangeMessage, OrderTurn
from model.refund.preference import PreferenceResult, PreferenceStatus, ResponseDetail
from workflows.refund import build_graph, run_refund_turn


def test_explicit_list_returns_dedicated_preference_result():
    def decide(schema, _prompt):
        pytest.fail("明确的查看命令可由应用直接解析")

    def investigate(*_args):
        pytest.fail("查看偏好不应调用调查工具")

    graph = build_graph(decide, investigate, lambda *args, **fields: None, search=investigate)
    turn = OrderTurn(history=[ExchangeMessage(role="user", content="查看我的长期偏好")], trace_id="list-test", refund_source=None)
    state = run_refund_turn(graph, "test", turn)
    assert state.decision.memory_request.action == "list"
    assert state.preference_result.status == "failed"


@pytest.mark.parametrize("command", [
    {"action": "list", "consumer_id": "DEMO-USER-02"},
    {"action": "list", "value": "brief"},
    {"action": "delete", "key": "response_detail", "value": "brief"},
    {"action": "set", "key": "refund_amount", "value": "brief"},
    {"action": "set", "key": "response_detail", "value": "custom"},
])
def test_preference_contract_rejects_identity_and_uncontrolled_fields(command):
    with pytest.raises(ValidationError):
        ConversationDecision(intent="memory_request", order_id=None, memory_request=command)


def test_preference_checkpoint_preserves_structured_command():
    payload = {"intent": "memory_request", "order_id": None,
               "memory_request": {"action": "set", "key": "response_detail", "value": "brief"}}
    state = ConversationDecision.model_validate(payload)
    snapshot = state.model_dump(mode="json")
    assert snapshot["memory_request"] == {"action": "set", "key": "response_detail", "value": "brief"}
    assert ConversationDecision.model_validate(snapshot) == state


@pytest.mark.parametrize("current", [
    "你好", "有人说：‘你好，请记住以后简短回答’。", "这次请简短回答。",
    "请记住这笔订单。订单状态这次请简短回答。", "请记住这次要简短回答，以后按默认方式。",
    "请记住这个示例‘以后简短回答’。",
    "我举个例子，请记住以后简短回答。", "请记住以后简短回答；这只是个示例，不要真的保存。",
    "我不是在下指令，请记住以后简短回答。",
])
def test_old_or_quoted_instruction_cannot_restore_deleted_value(current):
    saver = InMemorySaver()
    old = ExchangeMessage(role="user", content="请帮我记住以后简短回答。")
    def decide(schema, _prompt):
        return schema(intent="memory_request", order_id=None, order_goal=None, order_id_status="missing",
                      order_id_evidence=None, continues_eligibility_question=False)

    def apply(_command):
        return PreferenceResult(status=PreferenceStatus.UPDATED, detail=ResponseDetail.BRIEF)

    def investigate(*_args):
        pytest.fail("偏好命令不应调用调查工具")

    graph = build_graph(decide, investigate, lambda *args, **fields: None, saver,
                        search=investigate, is_live=lambda _: True, preferences=apply)
    first = run_refund_turn(graph, "old-session", OrderTurn(history=[old], trace_id="old", refund_source=None))
    assert first.preference_result.status is PreferenceStatus.UPDATED
    turn = OrderTurn(history=[old, ExchangeMessage(role="user", content=current)], trace_id="new", refund_source=None)
    state = run_refund_turn(graph, "old-session", turn)
    assert state.preference_result is None and state.response_detail is None


def test_mixed_policy_command_keeps_policy_question_independent():
    def decide(schema, _prompt):
        return schema(intent="policy_question", order_id=None, order_goal=None, order_id_status="missing",
                      order_id_evidence=None, continues_eligibility_question=False)

    def search(step, *_args):
        assert step.query == "退款多久到账？"
        return None

    graph = build_graph(decide, lambda *_args: pytest.fail("政策咨询不应查单"),
                        lambda *args, **fields: None, search=search)
    turn = OrderTurn(history=[ExchangeMessage(role="user", content="退款多久到账？顺便查看我的长期偏好。")],
                     trace_id="mixed", refund_source=None)
    state = run_refund_turn(graph, "test", turn)
    assert state.preference_result.status is PreferenceStatus.FAILED
    assert state.reply == "暂时无法核实退款政策，请稍后再试。"


def test_business_intent_cannot_add_a_preference_command():
    def decide(schema, _prompt):
        return schema(intent="policy_question", order_id=None, order_goal=None, order_id_status="missing", order_id_evidence=None,
                      continues_eligibility_question=False, memory_request={"action": "delete", "key": "response_detail"})

    def search(*_args):
        pytest.fail("模型违反业务契约时不应启动政策调查")

    graph = build_graph(decide, lambda *_args: pytest.fail("政策咨询不应查单"),
                        lambda *args, **fields: None, search=search)
    turn = OrderTurn(history=[ExchangeMessage(role="user", content="退款多久到账？")], trace_id="policy", refund_source=None)
    state = run_refund_turn(graph, "test", turn)
    assert state.preference_result is None
    assert state.reply == "本轮未能完成订单查询，请稍后再试。"


def test_casual_style_statement_creates_neither_preference_nor_pending_order():
    def unexpected(*_args):
        pytest.fail("普通偏好陈述不应调用模型或调查工具")

    graph = build_graph(unexpected, unexpected, lambda *args, **fields: None, search=unexpected)
    turn = OrderTurn(history=[ExchangeMessage(role="user", content="我平时喜欢详细的说明。")], trace_id="casual", refund_source=None)
    state = run_refund_turn(graph, "test", turn)
    assert state.preference_result is None and state.eligibility_question is None
    assert "明确要求记住" in state.reply


def test_business_model_failure_preserves_independent_preference_result():
    def decide(*_args):
        raise ValueError("unavailable business classification")

    def apply(_command):
        return PreferenceResult(status=PreferenceStatus.LISTED, detail=ResponseDetail.BRIEF)

    def investigate(*_args):
        pytest.fail("无法识别业务意图时不得调用调查工具")

    graph = build_graph(decide, investigate, lambda *args, **fields: None,
                        search=investigate, preferences=apply, is_live=lambda _: True)
    turn = OrderTurn(history=[ExchangeMessage(role="user", content="退款多久到账？顺便查看我的长期偏好。")],
                     trace_id="mixed-model-error", refund_source=None)
    state = run_refund_turn(graph, "test", turn)
    assert state.reply == "本轮未能识别业务请求，请稍后再试。"
    assert state.preference_result.status is PreferenceStatus.LISTED


def test_resuming_pending_preference_node_does_not_replay_write():
    def unexpected(*_args):
        pytest.fail("恢复检查点不应重新运行模型、调查或偏好命令")

    graph = build_graph(unexpected, unexpected, lambda *args, **fields: None, InMemorySaver(),
                        search=unexpected, is_live=lambda _: False, preferences=unexpected)
    checkpoint = {"configurable": {"thread_id": "interrupted"}}
    graph.update_state(checkpoint, {
        "history": [{"role": "user", "content": "请记住以后简短回答。"}], "trace_id": "interrupted-turn",
        "refund_source": None,
        "decision": {"intent": "memory_request", "order_id": None,
                     "memory_request": {"action": "set", "key": "response_detail", "value": "brief"}},
    }, as_node="intent")
    assert graph.get_state(checkpoint).next == ("preferences",)
    state = graph.invoke(None, checkpoint)
    assert state["preference_result"] == {"status": "failed", "detail": None}
