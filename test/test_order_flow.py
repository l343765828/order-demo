import asyncio
import sys
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from implementations.refund.investigation import investigate_order
from model.refund.flow import ExchangeMessage, InvestigationStep, OrderToolEvent, OrderTurn
from workflows.refund import build_graph, load_refund_state, run_refund_turn
from model.orders.query import GetOrderRequest, OrderQueryResult
from model.refund.status import ConversationIntent, OrderGoal, ReplanAction


def test_decision_enums_preserve_string_contract():
    from model.refund.flow import ConversationDecision, ReplanDecision
    decision = ConversationDecision.model_validate({
        "intent": "order_question", "order_id": "DEMO-1001", "order_goal": "eligibility",
    })
    assert decision.intent is ConversationIntent.ORDER_QUESTION
    assert decision.order_goal is OrderGoal.ELIGIBILITY
    assert decision.model_dump(mode="json") == {
        "intent": "order_question", "order_id": "DEMO-1001", "order_goal": "eligibility",
    }
    assert ReplanDecision.model_validate({"action": "finish", "reason": "完成"}).action is ReplanAction.FINISH
from model.orders.status import OrderQueryStatus


def unexpected_policy_search(*_args):
    pytest.fail("此测试不应触发政策检索")


@pytest.mark.parametrize("plan", [
    [{"step_id": "order", "kind": "inspect_order", "order_id": "DEMO-1001",
      "depends_on": ["absent"], "completion": "获得订单状态"}],
    [{"step_id": "order", "kind": "inspect_order", "order_id": "DEMO-1001",
      "depends_on": ["order"], "completion": "获得订单状态"}],
    [{"step_id": "order", "kind": "inspect_order", "order_id": "DEMO-1002",
      "depends_on": [], "completion": "获得订单状态"}],
])
def test_graph_rejects_invalid_plan_before_dispatch(plan):
    def decide(schema, _prompt):
        if schema.__name__ == "ConversationDecision":
            return schema(intent="order_question", order_id="DEMO-1001")
        return schema.model_validate({"steps": plan})

    def execute(_step, _trace_id):
        pytest.fail("invalid plans must not dispatch a tool")

    graph = build_graph(decide, execute, lambda *_args, **_fields: None, search=unexpected_policy_search)
    state = graph.invoke({"history": [{"role": "user", "content": "查一下 DEMO-1001"}],
                          "trace_id": "test"})
    assert state["reply"] == "本轮未能完成订单查询，请稍后再试。"


@pytest.mark.parametrize("result_data, action, reply", [
    ({"status": "found", "order": {
        "order_id": "DEMO-1001", "payment_status": "paid", "shipping_status": "unshipped",
        "refund_status": "not_refunded", "paid_amount_fen": 19900, "currency": "CNY",
    }}, "finish", "订单 DEMO-1001：已支付，未发货，无模拟退款记录，实付 19900 分。"),
    ({"status": "unavailable"}, "finish", "未找到可操作的订单。"),
    ({"status": "failed", "error": "查询服务不可用"}, "fail", "订单服务暂时不可用，请稍后再试。"),
])
def test_plain_order_query_finishes_without_another_model_call(result_data, action, reply):
    def decide(schema, _prompt):
        if schema.__name__ == "ConversationDecision":
            return schema(intent="order_question", order_id="DEMO-1001")
        if schema.__name__ == "InvestigationPlan":
            return schema(steps=[InvestigationStep(
                step_id="order", kind="inspect_order", order_id="DEMO-1001",
                depends_on=[], completion="获得订单查询结果",
            )])
        pytest.fail("普通查单收尾不应再次调用模型")

    def investigate(step, trace_id):
        return OrderToolEvent(event_id="order-event", trace_id=trace_id, model_tool_call_id="model-call",
                              order_id=step.order_id, result=OrderQueryResult.model_validate(result_data))

    events = []
    graph = build_graph(decide, investigate, lambda name, **fields: events.append((name, fields)),
                        search=unexpected_policy_search)
    state = graph.invoke({"history": [{"role": "user", "content": "查一下 DEMO-1001"}],
                          "trace_id": "test"})
    assert state["reply"] == reply
    assert state["replan_decision"]["action"] == action
    assert [fields["decision"] for name, fields in events if name == "replan"] == [state["replan_decision"]]


def test_typed_graph_continues_legacy_checkpoint_without_losing_partial_updates():
    def decide(*_):
        pytest.fail("补充订单号时不应再次分类")

    def investigate(step, trace_id):
        assert step.order_id == "DEMO-1001"
        return OrderToolEvent(event_id="order-event", trace_id=trace_id, model_tool_call_id="model-call",
                              order_id=step.order_id, result=OrderQueryResult(status=OrderQueryStatus.UNAVAILABLE))

    graph = build_graph(decide, investigate, lambda *_a, **_kw: None, InMemorySaver(),
                        search=unexpected_policy_search)
    session = "old-conversation"
    config = {"configurable": {"thread_id": session}}
    previous = [{"role": "user", "content": "能退吗"}, {"role": "assistant", "content": "请提供订单号"}]
    graph.update_state(config, {"history": previous, "trace_id": "old-turn", "eligibility_question": "能退吗",
                                "decision": {"intent": "order_question", "order_id": None, "order_goal": "eligibility"},
                                "reply": "请提供订单号"}, as_node="intent")
    snapshot, _ = load_refund_state(graph, session)
    assert snapshot is not None
    assert snapshot.history[0].content == "能退吗"
    assert snapshot.decision is not None and snapshot.decision.order_goal == "eligibility"

    turn = OrderTurn(history=[*snapshot.history, ExchangeMessage(role="user", content="DEMO-1001")],
                     trace_id="new-turn")
    result = run_refund_turn(graph, session, turn)
    raw = graph.get_state(config).values
    assert result.question == "能退吗"
    assert result.eligibility_question is None and raw["eligibility_question"] is None
    assert result.decision is not None and result.decision.order_goal == "eligibility"
    assert result.order_result is not None and result.order_result.status is OrderQueryStatus.UNAVAILABLE
    assert result.reply == "未找到可操作的订单。"
    assert type(raw["order_result"]["status"]) is str
    assert raw["order_result"]["status"] == "unavailable"


class ScriptedModel(FakeMessagesListChatModel):
    catalogs: list[list[str]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.catalogs.append([t.name for t in tools])
        return self


def task(name="order-agent"):
    return {"name": "task", "args": {"subagent_type": name, "description": "untrusted"}, "id": "delegate"}


def call(call_id, **args):
    return {"name": "get_order", "args": args, "id": call_id}


@pytest.mark.parametrize("worker_calls, sends, rejected", [
    ([call("one", order_id="DEMO-1001"), call("two", order_id="DEMO-1001")], 1, "tool_budget"),
    ([call("one", order_id="DEMO-1001", consumer_id="DEMO-USER-02")], 0, "bad_arguments"),
    ([call("one", order_id="DEMO-1002")], 0, "wrong_tool_or_order"),
    ([], 0, "agent_observation_rejected"),
])
def test_order_agent_requires_real_observation_and_bounds_calls(worker_calls, sends, rejected):
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[task()]),
        AIMessage(content="Claim: DEMO-1001 was found", tool_calls=worker_calls),
        AIMessage(content="Claim: DEMO-1001 was found"),
        AIMessage(content="Claim: DEMO-1001 was found"),
    ])
    results, events = [], []

    async def fetch(request, event_id):
        assert request == GetOrderRequest(order_id="DEMO-1001")
        results.append(event_id)
        return OrderQueryResult(status=OrderQueryStatus.UNAVAILABLE)

    step = InvestigationStep(step_id="x", kind="inspect_order", order_id="DEMO-1001",
                             depends_on=[], completion="read order")
    observation = asyncio.run(investigate_order(
        model, step, "trace", fetch,
        lambda name, **data: events.append(data.get("reason", name)),
    ))
    assert len(results) == sends
    assert rejected in events
    assert {tuple(tools) for tools in model.catalogs} == {("task",), ("get_order",)}
    if sends:
        assert observation is not None and observation.result.status is OrderQueryStatus.UNAVAILABLE
    else:
        assert observation is None


def test_supervisor_cannot_delegate_to_default_or_execute():
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[
            task("general-purpose"),
            {"name": "execute", "args": {"command": "echo denied"}, "id": "shell"},
        ]),
        AIMessage(content="done"),
    ])

    async def fetch(_request, _event_id):
        pytest.fail("denied delegation must never dispatch")

    step = InvestigationStep(step_id="x", kind="inspect_order", order_id="DEMO-1001",
                             depends_on=[], completion="read order")
    assert asyncio.run(investigate_order(model, step, "trace", fetch, lambda *a, **kw: None)) is None
    assert model.catalogs and all(tools == ["task"] for tools in model.catalogs)
