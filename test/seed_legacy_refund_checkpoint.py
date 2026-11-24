"""用旧检出的会话存储和业务图保存待发送的退款检查点，供恢复联验。"""

import json
from pathlib import Path
import sys


def seed(legacy_root, config_path):
    sys.path.insert(0, str(legacy_root))
    from config import load_config, use_config
    from config.demo import CONSUMER_ID
    from implementations.refund.conversations import create_conversation, open_conversation
    from model import connection_config, create_pool
    from model.policy.evidence import PolicyCitation, PolicyEvidence
    from model.policy.status import PolicyEntryType, PolicyEvidenceStatus
    from model.refund.flow import ExchangeMessage, OrderFlowNode
    from model.refund.submission import AuthorizedRefundIntent, RefundPolicyVerification
    from utils.checkpoint import open_mysql_checkpointer, thread_config
    from workflows.refund import build_graph

    def unexpected(*_args, **_kwargs):
        raise AssertionError('写入待办样例时执行了业务流程')

    use_config(config_path)
    mysql = load_config().mysql
    message = ExchangeMessage(role='user', content='请帮我退 DEMO-1001')
    pool = create_pool()
    try:
        sid = create_conversation(pool, CONSUMER_ID)
        with open_conversation(pool, sid, CONSUMER_ID) as conversation:
            sequence = conversation.append(message)
    finally:
        pool.close()

    authorization = AuthorizedRefundIntent(consumer_id=CONSUMER_ID, session_id=sid,
                                           source_message_sequence=sequence, order_id='DEMO-1001')
    verification = RefundPolicyVerification(
        trace_id='mysql-02-legacy-pending', idempotency_key='mysql-02-legacy-pending', authorization=authorization,
        evidence=PolicyEvidence(question=message.content, query='已付未发货', status=PolicyEvidenceStatus.SUFFICIENT,
                                catalog_revision=1, citations=[
            PolicyCitation(event_id='policy-event', document_id='demo-policy', version='1.0', clause_id='RF-003',
                           entry_type=PolicyEntryType.POLICY, excerpt='隔离测试条款'),
        ]),
    )
    with open_mysql_checkpointer(mysql.database, **connection_config(mysql)) as saver:
        graph = build_graph(unexpected, unexpected, unexpected, saver, search=unexpected)
        config = thread_config(sid)
        graph.update_state(config, {
            'history': [message.model_dump(mode='json')], 'trace_id': verification.trace_id,
            'refund_authorization': authorization.model_dump(mode='json'),
            'refund_verification': verification.model_dump(mode='json'),
        }, as_node=OrderFlowNode.REVIEW_REFUND.value)
        snapshot = graph.get_state(config)
        assert snapshot.next == ('send_refund',)
    print(json.dumps({'session': sid, 'trace': verification.trace_id, 'message': message.content,
                      'checkpoint_id': snapshot.config['configurable']['checkpoint_id']}, ensure_ascii=False))


if __name__ == '__main__':
    seed(Path(sys.argv[1]), Path(sys.argv[2]))
