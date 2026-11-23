"""退款演示使用的固定配置和约束。"""

from enum import StrEnum
from typing import Final


# 统一退款业务流程和请求契约使用的长度上限，按字符计数。
class RefundLimits:
    FEN_PER_YUAN: Final[int] = 100  # 一元人民币对应的整数分数。
    MAX_REFUND_IDENTIFIER_CHARS: Final[int] = 64  # 退款单、提交、会话和追踪标识的字符上限。
    MAX_POLICY_QUERY_CHARS: Final[int] = 2_000  # 政策首次查询及补查文本的字符上限。
    MAX_CONVERSATION_HISTORY_CHARS: Final[int] = 3_500  # 本轮累计会话消息正文的字符上限。
    MAX_ORDER_ID_CHARS: Final[int] = 64  # 订单调查和查单请求接受的订单号字符上限。
    MAX_STEP_ID_CHARS: Final[int] = 64  # 调查步骤编号的字符上限。
    MAX_DECISION_TEXT_CHARS: Final[int] = 160  # 步骤完成条件和重规划原因的字符上限。
    MAX_INVESTIGATION_STEPS: Final[int] = 2  # 单次计划中的调查步骤上限。


# 统一外部连接和工具调用的超时。
class ConnectionLimits:
    MCP_TOKEN_MIN_CHARS: Final[int] = 32  # MCP 读取及执行凭据的最小字符数。
    REFUND_CALL_SECONDS: Final[int] = 15  # 固定退款读写工具单次调用超时秒数。
    MYSQL_CONNECT_SECONDS: Final[int] = 5  # MySQL 连接超时秒数。
    MCP_HTTP_SECONDS: Final[int] = 90  # MCP HTTP 传输超时秒数。
    POLICY_SESSION_SECONDS: Final[int] = 90  # 政策调查会话读取超时秒数。
    POLICY_CALL_SECONDS: Final[int] = 60  # 政策调查工具调用超时秒数。
    POLICY_BOUND_TOOL_SECONDS: Final[int] = 90  # 独立政策工具调用超时秒数。
    ORDER_CALL_SECONDS: Final[int] = 8  # 查单会话和工具调用超时秒数。


# MCP 调用中 Host 和服务端用于关联业务事件的协议字段。
class McpCorrelationField(StrEnum):
    TRACE_ID = "trace_id"  # 标识同一轮调查和办理。
    TOOL_EVENT_ID = "tool_event_id"  # 标识同一轮内的单次工具调用。


# 声明模拟退款业务读写共用的持久化表名。
class RefundTables:
    ORDERS: Final = "orders"  # 本人订单及实付事实。
    REFUNDS: Final = "refunds"  # 已确认的模拟退款。
    DISPATCHES: Final = "refund_dispatches"  # Host 保存的授权和提交状态。
    SESSIONS: Final = "conversation_sessions"  # 会话归属。
    MESSAGES: Final = "conversation_messages"  # 授权来源原文。


# MySQL 政策发布目录使用的表名，建表和读写操作共用。
class PolicyCatalogTables:
    HEAD: Final[str] = 'policy_catalog_head'  # 保存对新调查开放的最新修订。
    RELEASES: Final[str] = 'policy_releases'  # 保存每份文档版本的发布历史。


# 统一政策向量存储和检索使用的集合名称与约束。
class PolicyLimits:
    MILVUS_COLLECTION_NAME: Final[str] = 'order_demo_refund_policy'  # 退款政策在 Milvus 中的专用集合。
    CATALOG_DATABASE: Final[str] = 'order_demo'  # 政策目录所在的演示数据库。
    CATALOG_REVISION_MAX: Final[int] = (1 << 64) - 1  # MySQL 目录修订字段可保存的最大无符号整数。
    PUBLISHER_MAX_CHARS: Final[int] = 128  # 发布者标识的字符上限。
    READBACK_BATCH_SIZE: Final[int] = 128  # 强一致回读时 Milvus 每批条数。
    # 政策字段在 Milvus 中允许的最大 UTF-8 字节数。
    FIELD_BYTES: Final[dict[str, int]] = {
        'chunk_id': 64, 'document_id': 64, 'title': 256, 'version': 32,
        'clause_id': 32, 'entry_type': 16, 'text': 4096,
    }
    VECTOR_DIMENSIONS: Final[int] = 1024  # 政策向量的维度。
    VECTOR_NORM_TOLERANCE: Final[float] = 0.0001  # 单位向量模长的允许误差。
    MAX_RECALLED_ENTRIES: Final[int] = 8  # 单次检索最多召回的条目数。
    MAX_RANKED_ENTRIES: Final[int] = 3  # 单次检索最多返回的重排条目数。
    MILVUS_CONNECT_TIMEOUT_SECONDS: Final[int] = 10  # 连接 Milvus 的超时秒数。
    MILVUS_SEARCH_TIMEOUT_SECONDS: Final[int] = 15  # Milvus 检索的超时秒数。
    MILVUS_WRITE_TIMEOUT_SECONDS: Final[int] = 30  # Milvus 建表及写入的超时秒数。
    MODEL_TIMEOUT_SECONDS: Final[int] = 30  # 向量编码及重排请求的超时秒数。


# 统一长期偏好表使用的字段长度。
class PreferenceLimits:
    MAX_CONSUMER_ID_CHARS: Final[int] = 64  # 消费者编号的字符上限。
