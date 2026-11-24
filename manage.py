"""演示数据库初始化与种子装载命令入口。"""
import argparse

from implementations.refund.demo_data import manage_demo_data
from model.refund.status import DemoDataCommand


# 解析管理命令，显式初始化演示数据库结构或装载种子数据。
def main() -> None:

    # region 解析管理命令，显式初始化演示数据库结构或装载种子数据
    parser = argparse.ArgumentParser()
    parser.add_argument("command", type=DemoDataCommand, choices=tuple(DemoDataCommand))
    args = parser.parse_args()
    manage_demo_data(args.command)
    print(args.command + " completed")
    # endregion


if __name__ == "__main__":
    main()
