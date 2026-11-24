"""演示数据和服务共用的固定身份。"""

from typing import Final


# 已认证的只读演示会话使用此消费者编号。
CONSUMER_ID: Final = "DEMO-USER-01"
# 退款演示订单、会话和 checkpoint 默认使用的数据库。
DEMO_DATABASE: Final = "order_demo"
# 保存消费者当前有效长期偏好的表名。
CONSUMER_PREFERENCES_TABLE: Final = "consumer_preferences"
# 实际执行生成的模拟退款单号前缀。
MOCK_REFUND_ID_PREFIX: Final = "MOCK-RF-"


# 保存演示种子的固定业务事实，金额单位为人民币分。
class DemoSeed:
    CURRENCY: Final = "CNY"  # 演示订单与种子退款的币种。
    PAID_AMOUNT_FEN: Final = 19900
    UNPAID_AMOUNT_FEN: Final = 0
    REFUNDED_AMOUNT_FEN: Final = 8800
    REFUND_ID: Final = "MOCK-RF-1003"
    SHIPPED_AMOUNT_FEN: Final = 29900
    OTHER_CONSUMER_ID: Final = "DEMO-USER-02"  # 归属隔离样本。
    OTHER_AMOUNT_FEN: Final = 5900


# 定义订单资格所依据的演示退款政策及升级前的发布基线。
class DemoPolicy:
    DOCUMENT_ID: Final = "DEMO-REFUND-POLICY"  # 订单资格所依据的演示政策文档编号。
    BASELINE_VERSION: Final = "1.0"  # 工单 15 已使用的演示政策版本。
    BASELINE_TITLE: Final = "演示退款政策与常见问题"  # 已发布演示政策的标题。
    ELIGIBILITY_EXCERPT_SHA256: Final = "d2adea0a67a2ede40d6769a7cd08d880e873b83421c4432bb6f4db51ae4c34b0"  # 已知适用资格条款标题及正文摘要。
