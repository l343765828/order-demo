"""演示数据库初始化、种子装载和重置入口。"""
import argparse
from pathlib import Path

from pymysql import MySQLError
from sqlalchemy.exc import SQLAlchemyError

from config import load_config
from implementations.refund.demo_data import MySQLSeedService, initialize_demo_database
from model.refund.status import DemoDataCommand
from utils.database import create_mysql_engine


# 解析管理命令，按指定配置初始化、装载或重置演示库。
def main() -> None:

    # region 读取命令和可选配置路径
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, help="应用配置文件路径")
    parser.add_argument("command", type=DemoDataCommand, choices=tuple(DemoDataCommand))
    args = parser.parse_args()
    if (
        # 本次管理操作将清空演示数据。
        args.command is DemoDataCommand.RESET
        # 操作者未明确指定目标库所使用的配置文件。
        and args.config is None
    ):

        # region 缺少重置目标配置时在打开连接前结束
        parser.error("reset 需要 --config 指定目标开发库配置")
        # endregion

    # endregion

    # region 执行管理命令并报告结果
    try:
        mysql = load_config(args.config).mysql
        match args.command:

            # region init 使用独立管理连接补齐数据库结构
            case DemoDataCommand.INIT:
                initialize_demo_database(mysql)
            # endregion

            # region 装载或重置演示数据并释放连接池
            case DemoDataCommand.SEED | DemoDataCommand.RESET:
                engine = create_mysql_engine(mysql)
                try:
                    service = MySQLSeedService(engine)
                    if args.command is DemoDataCommand.RESET:

                        # region 显式重置时清空数据并重建种子
                        service.reset()
                        # endregion

                    else:

                        # region 装载种子时仅补齐缺失的样例
                        service.seed()
                        # endregion

                finally:
                    engine.dispose()
            # endregion

    except (ValueError, MySQLError, SQLAlchemyError):

        # region 数据库操作失败时返回非零退出码并隐藏数据库详情
        parser.exit(1, "演示数据库操作失败。\n")
        # endregion

    print(args.command + " completed")
    # endregion


if __name__ == "__main__":
    main()
