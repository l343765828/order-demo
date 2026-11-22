"""接续退款会话、执行当前轮调查并保存原文。"""

from uuid import uuid4

import httpx

from config import load_config
from config.demo import CONSUMER_ID
from implementations.refund.conversations import create_conversation, open_conversation
from implementations.refund.decision import QwenDecider
from implementations.refund.investigation import execute_order
from model.policy.status import PolicySearchMode
from model.refund.flow import ExchangeMessage, InvestigationStep, OrderToolEvent, OrderTurn, PolicyStep, PolicyToolEvent
from utils.events import log_event
from utils.qwen_client import create_qwen_client
from workflows.refund import build_graph, load_refund_state, run_refund_turn


# 载入或创建消费者会话，处理交互消息并保存原文与检查点。
def run_cli(args, pool, saver, http: httpx.Client) -> int:
    model = create_qwen_client(load_config().qwen, http_client=http)
    session_id = args.session or create_conversation(pool, CONSUMER_ID)
    trace_id: str | None = None

    # 为当前会话和请求关联标识输出结构化过程事件。
    def emit(event: str, **fields) -> None:
        log_event(event, session_id=session_id, trace_id=trace_id, **fields)

    # 将已验证的查单步骤交给订单调查执行器。
    def investigate(step: InvestigationStep, trace_id: str) -> OrderToolEvent | None:
        event = execute_order(step, trace_id, emit)
        return event if isinstance(event, OrderToolEvent) else None

    # 将政策步骤交给 PolicyAgent，检索范围由外层图指定。
    def search(step: PolicyStep, trace_id: str, mode: PolicySearchMode) -> PolicyToolEvent | None:
        event = execute_order(step, trace_id, emit, mode=mode)
        return event if isinstance(event, PolicyToolEvent) else None

    graph = build_graph(QwenDecider(model, emit), investigate, emit, saver, search=search)
    # region 会话归属校验通过后，按会话编号载入原文和 checkpoint 供后续输入使用
    with open_conversation(pool, session_id, CONSUMER_ID) as conversation:
        session_id = conversation.session_id
        restored, pending_nodes = load_refund_state(graph, session_id)
        trace_id = restored.trace_id if restored else None
        history = conversation.history()
        emit("session_loaded", messages=len(history), checkpoint=restored is not None,
             pending_nodes=list(pending_nodes))
    # endregion
    print(f"会话：{session_id}", flush=True)
    # region 仅请求历史时，输出已保存原文并结束本次命令
    if args.history:
        for stored in history:
            print(f"{stored.role}：{stored.content}")
        return 0
    # endregion

    while True:
        try:
            message = args.message if args.message is not None else input("你：")
        except (EOFError, KeyboardInterrupt):
            return 0
        if message.strip():
            # region 收到非空消息时持有会话锁，先保存消费者原文再执行流程并保存回复
            with open_conversation(pool, session_id, CONSUMER_ID) as conversation:
                trace_id = str(uuid4())
                conversation.append(ExchangeMessage(role="user", content=message))
                turn = OrderTurn(history=conversation.history(), trace_id=trace_id)
                state = run_refund_turn(graph, session_id, turn)
                reply = state.reply
                assert isinstance(reply, str)
                conversation.append(ExchangeMessage(role="assistant", content=reply))
            # endregion
            emit("turn_completed")
            print(reply, flush=True)
        if args.message is not None:
            return 0
