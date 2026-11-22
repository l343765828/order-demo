"""显式发布退款政策。"""
import argparse

from implementations.policy.publish import publish_policy


# 发布退款政策并报告实际发布条数。
def main() -> None:
    parser = argparse.ArgumentParser(description='发布退款政策')
    parser.parse_args()
    print(f'已发布 {publish_policy()} 条政策条目')


if __name__ == '__main__':
    main()
