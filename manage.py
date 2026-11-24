"""演示数据库初始化与种子装载命令入口。"""
import argparse

from pymysql import MySQLError
from sqlalchemy.exc import SQLAlchemyError

from config import load_config
from implementations.refund.demo_data import MySQLSeedService, initialize_demo_database
from model.refund.status import DemoDataCommand
from utils.database import create_mysql_engine


# 解析管理命令，显式初始化演示数据库结构或装载种子数据。
def main() -> None:

    # region 读取管理命令和应用数据库配置
    parser = argparse.ArgumentParser()
    parser.add_argument("command", type=DemoDataCommand, choices=tuple(DemoDataCommand))
    args = parser.parse_args()
    mysql = load_config().mysql
    # endregion

    # region 执行初始化或种子事务并报告命令结果
    try:
        match args.command:

            # region init 使用独立管理连接补齐数据库结构
            case DemoDataCommand.INIT:
                initialize_demo_database(mysql)
            # endregion

            # region seed 使用专用 Engine 装载种子并释放连接池
            case DemoDataCommand.SEED:
                engine = create_mysql_engine(mysql)
                try:
                    MySQLSeedService(engine).seed()
                finally:
                    engine.dispose()
            # endregion

    except (MySQLError, SQLAlchemyError):

        # region 数据库操作失败时返回非零退出码并隐藏数据库详情
        parser.exit(1, "演示数据库操作失败。\n")
        # endregion

    print(args.command + " completed")
    # endregion


if __name__ == "__main__":
    main()
