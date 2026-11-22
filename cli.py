"""消费者 CLI 通过独立 MCP 服务查单及咨询退款政策。"""

import argparse
import sys
from io import TextIOWrapper
from uuid import uuid4

import httpx
import pymysql
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from langchain_core.runnables import RunnableConfig

from config import use_config
from conversations import SessionUnavailable, create_conversation, open_conversation
from implementations.refund.decision import QwenDecider
from model import connection_config, create_pool
from order_agent import execute_order, qwen
from model.refund.flow import (
    ExchangeMessage,
    InvestigationStep,
    OrderState,
    OrderToolEvent,
    OrderTurn,
    PolicyStep,
    PolicyToolEvent,
)
from model.policy.status import PolicySearchMode
from order_flow import build_graph
from order_runtime import CONSUMER_ID, log_event, read_token


# 解析命令行参数，初始化存储并运行会话；返回进程退出码。
def main() -> int:
    parser = argparse.ArgumentParser(description="演示订单查询和退款政策咨询")
    parser.add_argument("--config", help="JSON 配置文件路径")
    parser.add_argument("--session", help="继续本人已有会话")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--message", help="发送一条消费者消息后退出")
    mode.add_argument("--history", action="store_true", help="读取原文与当前 checkpoint 状态")
    mode.add_argument("--init-checkpoints", action="store_true", help="显式初始化 checkpoint 表")
    args = parser.parse_args()
    use_config(args.config)
    if args.history and not args.session:
        parser.error("--history 需要 --session")

    try:
        with pymysql.connect(database="order_demo", autocommit=True, **connection_config()) as connection:
            saver = PyMySQLSaver(connection)
            if args.init_checkpoints:
                saver.setup()
                print("checkpoint 初始化完成。")
                return 0
            pool = create_pool()
            try:
                if not args.history:
                    read_token()
                with httpx.Client(trust_env=False) as http:
                    return run_cli(args, pool, saver, http)
            finally:
                pool.close()
    except SessionUnavailable:
        print("会话不可用。", file=sys.stderr)
        return 2
    except (ValueError, pymysql.MySQLError) as exc:
        log_event("host_unavailable", error_type=type(exc).__name__)
        print("无法运行查单 Host，请检查运行配置和 MySQL 初始化。", file=sys.stderr)
        return 1


# 载入或创建消费者会话，处理交互消息并保存原文与检查点。
def run_cli(args, pool, saver, http: httpx.Client) -> int:
    model = qwen(http_client=http)
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
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        snapshot = graph.get_state(config)
        restored = OrderState.model_validate(snapshot.values) if snapshot.values else None
        trace_id = restored.trace_id if restored else None
        history = conversation.history()
        emit("session_loaded", messages=len(history), checkpoint=bool(snapshot.values),
             pending_nodes=list(snapshot.next))
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
                state = OrderState.model_validate(graph.invoke(turn.model_dump(mode="json"), config))
                reply = state.reply
                assert isinstance(reply, str)
                conversation.append(ExchangeMessage(role="assistant", content=reply))
            # endregion
            emit("turn_completed")
            print(reply, flush=True)
        if args.message is not None:
            return 0


if __name__ == "__main__":
    # 管道与 Windows 控制台都使用 UTF-8 保存原消息。
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
