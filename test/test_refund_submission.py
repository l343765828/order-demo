import pytest

from implementations.refund.intent import resolve_intent
from implementations.refund.reply import refund_reply
from langgraph.checkpoint.memory import InMemorySaver
from model.policy.evidence import PolicyCitation, PolicyEvidence
from model.policy.status import PolicyEntryType, PolicyEvidenceStatus
from model.refund.flow import ExchangeMessage, OrderFlowNode
from model.refund.submission import AuthorizedRefundIntent, RefundPolicyVerification, RefundResult
from model.refund.status import ConversationIntent, RefundOutcome, RefundReason
from utils.checkpoint import thread_config
from workflows.refund import build_graph


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
        return schema(intent=ConversationIntent.REFUND_REQUEST, order_id=order_id)

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
    verification = RefundPolicyVerification(
        trace_id='prior-trace', idempotency_key='prior-key', authorization=authorization,
        evidence=PolicyEvidence(question='请帮我退 DEMO-1001', query='已付未发货',
                                status=PolicyEvidenceStatus.SUFFICIENT, catalog_revision=1, citations=[
            PolicyCitation(event_id='policy-event', document_id='demo-policy', version='1.0', clause_id='RF-003',
                           entry_type=PolicyEntryType.POLICY, excerpt='示例正式条款'),
        ]),
    )
    graph.update_state(config, {
        'history': [ExchangeMessage(role='user', content='请帮我退 DEMO-1001').model_dump(mode='json')],
        'trace_id': 'prior-trace',
        'refund_authorization': authorization.model_dump(mode='json'),
        'refund_verification': verification.model_dump(mode='json'),
    }, as_node=OrderFlowNode.REVIEW_REFUND.value)
    result = graph.invoke(None, config)
    assert result['refund_result']['status'] == 'unconfirmed'
    assert '结果未确认' in result['reply']
