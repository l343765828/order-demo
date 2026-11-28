from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from implementations.refund import execution
from implementations.refund.intent import resolve_intent
from implementations.refund.reply import refund_reply
from langgraph.checkpoint.memory import InMemorySaver
from model.orders.query import OrderDetails, OrderQueryResult
from model.policy.evidence import EligibilityExtraction
from model.policy.search import PolicySearchResult
from model.refund.flow import ExchangeMessage, OrderFlowNode, OrderState, OrderToolEvent, OrderTurn, PolicyToolEvent
from model.refund.intent import IntentDecision
from model.refund.submission import AuthorizedRefundIntent, PreparedRefund, RefundReceipt, RefundResult, RefundTurnSource
from model.refund.status import ConversationIntent, RefundOutcome, RefundReason
from utils.checkpoint import thread_config
from workflows.refund import build_graph, run_refund_turn


@pytest.mark.parametrize('scenario', ['eligibility', 'submit', 'independent_policy_question', 'independent_policy_cited',
                                     'independent_policy_followup',
                                     'unavailable', 'failed', 'wrong_order',
                                     'refetch_unavailable', 'no_source', 'no_current_order', 'policy_condition',
                                     'wait_for_policy', 'policy_prerequisite', 'policy_then_refund',
                                     'policy_then_confirmation', 'policy_conditional_short',
                                     'policy_pending', 'policy_first_sentence', 'policy_if_found',
                                     'policy_then_directive', 'quoted_policy_prerequisite',
                                     'unclassified_policy_condition', 'unclassified_policy_document'])
def test_refund_turn_separates_policy_investigation_from_authorization(scenario, monkeypatch):
    decisions, events, reads = [], [], []
    order_id = 'DEMO-1001'
    eligible = OrderQueryResult(status='found', order=OrderDetails(
        order_id=order_id, payment_status='paid', shipping_status='unshipped',
        refund_status='not_refunded', paid_amount_fen=19923,
    ))
    unavailable = OrderQueryResult(status='unavailable')
    clause = '本人订单已支付、未发货且尚未退款时，可以办理整单原路退款。'
    mixed = scenario in ('independent_policy_question', 'independent_policy_cited', 'independent_policy_followup')
    prompts = {
        'eligibility': f'{order_id} 可以退款吗？',
        'independent_policy_question': f'请帮我退 {order_id}，并告诉我依据。',
        'independent_policy_cited': f'请帮我退 {order_id}，顺便告诉我依据。',
        'independent_policy_followup': f'请帮我退 {order_id}。另请问适用哪条政策？',
        'no_current_order': '请帮我退刚才那单',
        'policy_condition': f'如果查到适用的正式条款就帮我退 {order_id}',
        'wait_for_policy': f'等你查到正式条款再帮我退 {order_id}',
        'policy_prerequisite': f'请帮我退 {order_id}，前提是你能找出适用条款。',
        'policy_then_refund': f'查到适用的正式条款后，再帮我退 {order_id}。',
        'policy_then_confirmation': f'给我看看条款，等我确认后请帮我退 {order_id}。',
        'policy_conditional_short': f'如能查到适用的正式条款，就帮我退 {order_id}。',
        'policy_pending': f'等正式条款核实完毕，请帮我退 {order_id}。',
        'policy_first_sentence': f'先给我看一下适用条款。确认完再帮我退 {order_id}。',
        'policy_if_found': f'能找到适用条款的话，就帮我退 {order_id}。',
        'policy_then_directive': f'看看适用的正式条款再帮我退 {order_id}。',
        'quoted_policy_prerequisite': f'看完“正式条款”再帮我退 {order_id}。',
        'unclassified_policy_condition': f'如能找到适用的官方细则，就帮我退 {order_id}。',
        'unclassified_policy_document': f'等你核对完官方文件并发给我，再帮我退 {order_id}。',
    }
    question = prompts.get(scenario, f'麻烦帮我退 {order_id}，谢谢。')
    deferred = scenario in prompts and not mixed and scenario not in ('eligibility', 'no_current_order')

    def decide(schema, _prompt):
        decisions.append(schema)
        if scenario == 'independent_policy_cited' and schema is EligibilityExtraction:
            return schema.model_validate({'rules': [{
                'source_index': 0, 'excerpt': clause, 'conditions': ['owned', 'paid', 'unshipped', 'not_refunded'],
                'exceptions': [], 'outcome': 'full_original', 'answers_policy_question': True,
            }]})
        assert schema is IntentDecision
        return schema(intent='order_question' if scenario == 'eligibility' else 'refund_request',
                      order_id=order_id, order_goal='eligibility' if scenario == 'eligibility' else None,
                      order_id_status='extracted', order_id_evidence=order_id, continues_eligibility_question=False,
                      policy_request='before_refund' if deferred and not scenario.startswith('unclassified') else None)

    def investigate(step, trace):
        reads.append(step.order_id)
        result = {
            'unavailable': unavailable,
            'failed': OrderQueryResult(status='failed', error='service error'),
            'wrong_order': eligible.model_copy(update={'order': eligible.order.model_copy(update={'order_id': 'DEMO-9999'})}),
        }.get(scenario, eligible)
        return OrderToolEvent(event_id='observed', trace_id=trace, model_tool_call_id='query',
                              order_id=step.order_id, result=result)

    dispatches = Mock()
    dispatches.blocking_dispatch.return_value = None
    refund = execution.RefundExecution(dispatches, SimpleNamespace(mcp=SimpleNamespace(write_token='writer')),
                                       lambda name, **fields: events.append((name, fields)))
    read_current = Mock(return_value=unavailable if scenario == 'refetch_unavailable' else eligible)
    monkeypatch.setattr(execution.controlled_tools, 'read_order', read_current)
    submit = Mock(return_value=RefundResult(status='simulated_succeeded', refund=RefundReceipt(
        order_id=order_id, refund_id='mock-from-service', amount_fen=19923,
    )))
    monkeypatch.setattr(execution.controlled_tools, 'send_refund', submit)
    def search_policy(step, trace, mode):
        events.append(('policy_called', {'mode': mode, 'query': step.query}))
        if scenario == 'independent_policy_cited':
            result = PolicySearchResult.from_mcp({
                'status': 'ok', 'event_id': 'policy-receipt', 'catalog_revision': 15,
                'candidates': [{'document_id': 'TEST-POLICY', 'title': '本轮正式政策', 'version': '3.0',
                                'clause_id': 'RF-123', 'entry_type': 'policy', 'text': clause}],
            })
            return PolicyToolEvent(event_id='policy-observation', trace_id=trace, model_tool_call_id='policy-call',
                                   query=step.query, result=result)

    search = Mock(side_effect=search_policy)
    resume_policy = scenario == 'independent_policy_cited'
    graph = build_graph(decide, investigate, lambda name, **fields: events.append((name, fields)),
                        checkpointer=InMemorySaver() if resume_policy else None,
                        search=search, refund=refund, is_live=lambda trace: trace == 'current')
    turn = OrderTurn(
        trace_id='current', history=[ExchangeMessage(role='user', content=question)],
        refund_source=None if scenario == 'no_source' else RefundTurnSource(
            session_id='session', consumer_id='consumer', source_message_sequence=1),
    )
    if resume_policy:
        config = thread_config('session')
        graph.invoke(turn.model_dump(mode='json'), config, interrupt_after=[OrderFlowNode.REFUND_REPLY.value])
        submit.assert_called_once()
        search.assert_not_called()
        assert graph.get_state(config).next == (OrderFlowNode.EXECUTE.value,)
        state = OrderState.model_validate(graph.invoke(None, config))
    else:
        state = run_refund_turn(graph, 'session', turn)

    assert decisions == ([EligibilityExtraction] if scenario == 'independent_policy_cited'
                         else [] if mixed or scenario == 'no_current_order' else [IntentDecision])
    assert state.reply
    if deferred or mixed:
        search.assert_called_once()
        assert search.call_args.args[2] == 'formal'
        assert ('3.0 版 RF-123' if scenario == 'independent_policy_cited' else '暂时无法核实退款政策') in state.reply
    else:
        search.assert_not_called()
    assert not any(name == 'refund_policy_verified' for name, _ in events)
    if scenario in ('no_source', 'no_current_order'):
        assert not reads
    if scenario == 'eligibility':
        assert '已支付' in state.reply and '未发货' in state.reply and '符合' in state.reply
    if scenario == 'submit' or mixed:
        assert reads == [order_id]
        read_current.assert_called_once()
        submit.assert_called_once()
        dispatches.reserve_dispatch.assert_called_once()
        request = submit.call_args.args[1]
        prepared = dispatches.reserve_dispatch.call_args.args[0]
        assert request.idempotency_key == prepared.idempotency_key
        assert prepared.authorization.source_message_sequence == 1
        assert '模拟退款成功' in state.reply and '¥199.23' in state.reply and 'mock-from-service' in state.reply
        if mixed:
            names = [name for name, _ in events]
            assert names.index('refund_result') < names.index('policy_called')
            assert state.reply.index('模拟退款成功') < state.reply.index(
                clause if scenario == 'independent_policy_cited' else '暂时无法核实退款政策')
    else:
        submit.assert_not_called()
        dispatches.reserve_dispatch.assert_not_called()
        assert state.refund_authorization is None or not deferred


@pytest.mark.parametrize('message,order_id', [
    ('如果能退，就帮我退 DEMO-1001', 'DEMO-1001'),
    ('请把 DEMO-1001 和 DEMO-1002 都退掉', None),
    ('请帮我退款', None),
    ('请帮我退款', 'DEMO-1001'),
    ('能退 DEMO-1001 吗？', 'DEMO-1001'),
    ('不要退 DEMO-1001', 'DEMO-1001'),
    ('请解释“帮我退 DEMO-1001”的含义', 'DEMO-1001'),
])
def test_only_the_single_explicit_current_order_can_continue(message, order_id):
    def decide(schema, _prompt):
        return schema(intent=ConversationIntent.REFUND_REQUEST, order_id=order_id, order_goal=None,
                      order_id_status="extracted" if order_id else "missing",
                      order_id_evidence=order_id, continues_eligibility_question=False)

    resolution = resolve_intent([ExchangeMessage(role='user', content=message)], None, decide, lambda *a, **k: None)
    assert (resolution.reply is None) == (message == '如果能退，就帮我退 DEMO-1001')


def test_refund_option_preference_does_not_change_unconditional_authorization():
    def decide(*_):
        pytest.fail("明确退款指令和独立金额偏好可直接识别")

    message = '请帮我办理 DEMO-1005 的退款，我希望只退 1 元到其他账户，使用其他渠道。'
    resolution = resolve_intent([ExchangeMessage(role='user', content=message)], None, decide, lambda *a, **k: None)
    assert resolution.reply is None and resolution.decision.order_id == 'DEMO-1005'


def test_unavailable_refund_reply_only_reports_that_no_refund_was_submitted():
    result = RefundResult(status=RefundOutcome.FAILED, reason=RefundReason.EXECUTION_UNAVAILABLE)

    assert refund_reply(result, lambda *args, **kwargs: None) == "退款服务暂时不可用，未提交退款。"


def test_resuming_prepared_checkpoint_without_live_consumer_turn_cannot_send():
    def forbidden(*_, **__):
        pytest.fail("恢复 checkpoint 时不得开始调查或发送写请求")

    graph = build_graph(forbidden, forbidden, lambda *_a, **_k: None, InMemorySaver(),
                        search=forbidden, is_live=lambda _: False)
    config = thread_config('previous-session')
    authorization = AuthorizedRefundIntent(consumer_id='consumer', session_id='previous-session',
                                           source_message_sequence=1, order_id='DEMO-1001')
    prepared = PreparedRefund(
        trace_id='prior-trace', idempotency_key='prior-key', authorization=authorization,
    )
    graph.update_state(config, {
        'history': [ExchangeMessage(role='user', content='请帮我退 DEMO-1001').model_dump(mode='json')],
        'trace_id': 'prior-trace',
        'refund_source': {'consumer_id': 'consumer', 'session_id': 'previous-session', 'source_message_sequence': 1},
        'refund_authorization': authorization.model_dump(mode='json'),
        'prepared_refund': prepared.model_dump(mode='json'),
    }, as_node=OrderFlowNode.REVIEW_REFUND.value)
    result = graph.invoke(None, config)
    assert result['refund_result']['status'] == 'unconfirmed'
    assert '结果未确认' in result['reply']
