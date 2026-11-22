"""消费者 CLI 通过独立 MCP 服务查单及咨询退款政策。"""

import argparse
import sys
from io import TextIOWrapper

import httpx
import pymysql

from implementations.refund.conversations import SessionUnavailable
from implementations.refund.host import run_cli
from model import connection_config, create_pool
from utils.checkpoint import open_mysql_checkpointer
from utils.events import log_event


# 解析命令行参数，初始化存储并运行会话；返回进程退出码。
def main() -> int:
    parser = argparse.ArgumentParser(description="演示订单查询和退款政策咨询")
    parser.add_argument("--session", help="继续本人已有会话")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--message", help="发送一条消费者消息后退出")
    mode.add_argument("--history", action="store_true", help="读取原文与当前 checkpoint 状态")
    mode.add_argument("--init-checkpoints", action="store_true", help="显式初始化 checkpoint 表")
    args = parser.parse_args()
    if args.history and not args.session:
        parser.error("--history 需要 --session")

    try:
        with open_mysql_checkpointer("order_demo", **connection_config()) as saver:
            if args.init_checkpoints:
                saver.setup()
                print("checkpoint 初始化完成。")
                return 0
            pool = create_pool()
            try:
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


if __name__ == "__main__":
    # 管道与 Windows 控制台都使用 UTF-8 保存原消息。
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        if isinstance(stream, TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main())
