"""显式发布退款政策；不依赖消费者会话。"""
import argparse
from pathlib import Path
from config import use_config
from mcp_service.policy import parse_policy, validate_policy_source
from mcp_service.policy_backend import PolicyBackend


# 读取固定政策源并发布；缺少完整条款时拒绝写入。
def main():
    parser = argparse.ArgumentParser(description='发布退款政策')
    parser.add_argument('--config', help='JSON 配置文件路径')
    use_config(parser.parse_args().config)
    # region 当前文档版本和条目集合完整时允许发布
    source = Path(__file__).with_name('policy').joinpath('refund-policy.md').read_text(encoding='utf-8')
    validate_policy_source(source)
    entries = parse_policy(source)
    if {e.clause_id for e in entries} != ({f'RF-{i:03d}' for i in range(1, 15)} | {f'FAQ-{i:03d}' for i in range(1, 8)}):
        raise ValueError('政策源条目不完整')
    # endregion

    # region 显式发布验证后的政策条目并释放后端连接
    backend = PolicyBackend()
    try:
        backend.publish(entries)
        print(f'已发布 {len(entries)} 条政策条目')
    finally:
        backend.close()
    # endregion


if __name__ == '__main__':
    main()
