"""通过改造前的业务模型、图和 MySQL Saver 写入历史形态的隔离快照。"""

import json
from pathlib import Path
import sys
from uuid import uuid4


def seed(root, config):
    sys.path.insert(0, str(root))
    from config import load_config
    from config.demo import CONSUMER_ID
    from implementations.refund.conversations import MySQLConversationService
    from model.refund.flow import ExchangeMessage, OrderFlowNode, OrderStateUpdate
    from utils.checkpoint import open_mysql_checkpointer, thread_config
    from utils.database import create_mysql_engine
    from utils.mysql import connection_config
    from workflows.refund import build_graph

    def unexpected(*_args, **_kwargs):
        raise AssertionError('写入历史样例时执行了业务调用')

    mysql = load_config(config).mysql
    engine = create_mysql_engine(mysql)
    sessions = {}
    try:
        conversations = MySQLConversationService(engine)
        with open_mysql_checkpointer(mysql.database, **connection_config(mysql)) as saver:
            saver.setup()
            graph = build_graph(unexpected, unexpected, unexpected, saver, search=unexpected)
            for kind, order, amount in (('original', 'DEMO-1003', 8800), ('pending', 'DEMO-1001', 19900)):
                sid, trace = conversations.create(CONSUMER_ID), str(uuid4())
                history = [ExchangeMessage(role='user', content=f'请帮我退 {order}')]
                if kind == 'original':
                    history.append(ExchangeMessage(
                        role='assistant', content='该订单已完成模拟退款，退款金额 ¥88.00，模拟退款单号 MOCK-RF-1003。',
                    ))
                with conversations.open(sid, CONSUMER_ID) as conversation:
                    for message in history:
                        conversation.append(message)
                result = {'status': 'found', 'order': {
                    'order_id': order, 'payment_status': 'paid', 'shipping_status': 'unshipped',
                    'refund_status': 'refunded' if kind == 'original' else 'not_refunded',
                    'paid_amount_fen': amount, 'currency': 'CNY',
                }}
                update = {
                    'order_result': result,
                    'event': {'event_id': 'old-order', 'trace_id': trace, 'model_tool_call_id': 'old-call',
                              'order_id': order, 'result': result},
                    'observation': {'step_id': 'order', 'tool_event_id': 'old-order', 'status': 'completed',
                                    'order_result': result},
                }
                node = OrderFlowNode.REFUND_REPLY
                if kind == 'original':
                    update.update(reply=history[-1].content, refund_result={'status': 'already_refunded', 'refund': {
                        'order_id': order, 'refund_id': 'MOCK-RF-1003', 'amount_fen': amount, 'currency': 'CNY',
                    }})
                else:
                    authorization = {'consumer_id': CONSUMER_ID, 'session_id': sid, 'source_message_sequence': 1,
                                     'order_id': order}
                    update.update(refund_authorization=authorization, refund_verification={
                        'trace_id': trace, 'idempotency_key': str(uuid4()), 'authorization': authorization,
                        'evidence': {'question': history[0].content, 'query': '已付未发货', 'status': 'sufficient',
                                     'catalog_revision': 1, 'citations': [
                            {'event_id': 'old-policy', 'document_id': 'demo-policy', 'version': '1.0',
                             'clause_id': 'RF-003', 'entry_type': 'policy', 'excerpt': '隔离测试条款'},
                        ]},
                    })
                    node = OrderFlowNode.REVIEW_REFUND
                graph.update_state(thread_config(sid), {
                    'history': [msg.model_dump(mode='json') for msg in history], 'trace_id': trace,
                    **OrderStateUpdate.model_validate(update).model_dump(mode='json', exclude_unset=True),
                }, as_node=node.value)
                snapshot = graph.get_state(thread_config(sid))
                assert snapshot.next == (() if kind == 'original' else ('send_refund',))
                sessions[kind] = {'id': sid, 'trace': trace, 'next': list(snapshot.next),
                                  'checkpoint_id': snapshot.config['configurable']['checkpoint_id']}
    finally:
        engine.dispose()
    print(json.dumps(sessions))


if __name__ == '__main__':
    seed(Path(sys.argv[1]), Path(sys.argv[2]))
