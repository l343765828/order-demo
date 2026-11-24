"""显式发布退款政策。"""
import argparse
from pathlib import Path

from pydantic import TypeAdapter

from config import load_config, use_config
from implementations.policy.backend import open_policy_backend
from implementations.policy.catalog import MySQLPolicyCatalog
from implementations.policy.publish import publish_policy
from implementations.policy.source import read_policy_document
from model.policy.catalog import PublisherId, QualificationConfirmation


# 校验发布源和维护者资格确认，报告实际生效的目录修订。
def main() -> None:

    # region 解析政策原件、显式版本和发布者的资格确认
    parser = argparse.ArgumentParser(description='发布退款政策')
    parser.add_argument('--config', type=Path, help='应用 JSON 配置文件')
    parser.add_argument('--source', type=Path, required=True, help='权威 Markdown 原件')
    parser.add_argument('--version', required=True, help='与原件标头一致的版本')
    parser.add_argument('--publisher', required=True, help='确认资格行为的发布者')
    parser.add_argument('--qualification', required=True, type=QualificationConfirmation,
                        choices=list(QualificationConfirmation), help='声明是否改变退款资格')
    parser.add_argument('--confirm-write-ended', action='store_true', help='确认上次发布进程已停止且 Milvus 写入已结束')
    args = parser.parse_args()
    try:
        document = read_policy_document(args.source.read_text(encoding='utf-8-sig'), version=args.version)
        publisher = TypeAdapter[PublisherId](PublisherId).validate_python(args.publisher.strip())
        use_config(args.config)
        settings = load_config()
        policy = settings.policy
        if policy is None:
            raise ValueError('配置文件缺少 policy 设置')
    except (OSError, ValueError) as error:
        parser.exit(1, f'发布失败：{error}\n')
    # endregion

    # region 初始化目录、独立发布并打印可核对的结构化结果
    catalog = MySQLPolicyCatalog(policy.catalog_database, settings.mysql)
    try:
        catalog.initialize()
        result = publish_policy(document, publisher=publisher, qualification=args.qualification,
                                catalog=catalog, open_backend=open_policy_backend,
                                confirm_write_ended=args.confirm_write_ended)
    except ValueError as error:
        parser.exit(1, f'发布失败：{error}\n')
    except Exception as error:
        parser.exit(1, f'发布失败：后端操作失败（{type(error).__name__}），重试前请核对发布记录及写入状态。\n')
    print(result.model_dump_json())
    # endregion


if __name__ == '__main__':
    main()
