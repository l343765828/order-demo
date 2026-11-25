"""消费者 CLI 通过独立 MCP 服务查单、咨询政策和办理模拟退款。"""

import argparse
import sys
from io import TextIOWrapper

import httpx
import pymysql
from sqlalchemy.exc import SQLAlchemyError

from config import load_config
from config.demo import CONSUMER_ID
from implementations.refund.conversations import MySQLConversationService, SessionUnavailable
from implementations.refund.dispatches import MySQLRefundDispatchService
from implementations.refund.host import run_cli
from implementations.refund.preferences import MySQLPreferences
from utils.checkpoint import open_mysql_checkpointer
from utils.database import create_mysql_engine
from utils.events import log_event
from utils.mysql import connection_config
from workflows.refund_checkpoint import RefundCheckpointSaver


# 解析命令行参数，初始化存储并运行会话；返回进程退出码。
def main() -> int:

    # region 解析命令行参数并核对历史模式所需的会话编号
    parser = argparse.ArgumentParser(description="演示订单查询、退款政策咨询与模拟退款")
    parser.add_argument("--session", help="继续本人已有会话")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--message", help="发送一条消费者消息后退出")
    mode.add_argument("--history", action="store_true", help="读取原文与当前 checkpoint 状态")
    mode.add_argument("--init-checkpoints", action="store_true", help="显式初始化 checkpoint 表")
    args = parser.parse_args()
    if (
        # 调用方请求查看会话历史。
        args.history
        # 未指定要查看的会话编号。
        and not args.session
    ):
        parser.error("--history 需要 --session")
    # endregion

    # region 按命令模式初始化检查点或运行会话并处理启动失败
    try:
        settings = load_config()
        mysql = settings.mysql
        with open_mysql_checkpointer(
            mysql.database, saver_type=RefundCheckpointSaver, **connection_config(mysql),
        ) as saver:

            # region 仅初始化检查点时创建所需表并退出
            if args.init_checkpoints:
                saver.setup()
                print("checkpoint 初始化完成。")
                return 0
            # endregion

            # region 用同一 Engine 组装会话、退款提交和偏好 Service，运行结束后释放连接
            engine = create_mysql_engine(mysql)
            try:
                with httpx.Client(trust_env=False) as http:
                    return run_cli(
                        args, saver, http, settings=settings, conversations=MySQLConversationService(engine),
                        dispatches=MySQLRefundDispatchService(engine),
                        preferences=MySQLPreferences(engine, consumer_id=CONSUMER_ID),
                    )
            finally:
                engine.dispose()
            # endregion

    except SessionUnavailable:

        # region 会话不可用时提示并返回退出码 2
        print("会话不可用。", file=sys.stderr)
        return 2
        # endregion

    except (ValueError, pymysql.MySQLError, SQLAlchemyError) as exc:

        # region 配置或数据库出错时记录失败并返回退出码 1
        log_event("host_unavailable", error_type=type(exc).__name__)
        print("无法运行查单 Host，请检查运行配置和 MySQL 初始化。", file=sys.stderr)
        return 1
        # endregion

    # endregion


if __name__ == "__main__":
    # 管道与 Windows 控制台都使用 UTF-8 保存原消息。
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
