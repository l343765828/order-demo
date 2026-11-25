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
    MYSQL_READ_SECONDS: Final[int] = 5  # MySQL 等待读取数据库响应的秒数。
    MYSQL_WRITE_SECONDS: Final[int] = 5  # MySQL 等待发送数据库请求的秒数。
    MYSQL_POOL_CONNECTIONS: Final[int] = 5  # 连接池中的常驻连接容量。
    MYSQL_POOL_OVERFLOW_CONNECTIONS: Final[int] = 0  # 连接池用满时允许的额外连接数。
    MYSQL_POOL_WAIT_SECONDS: Final[int] = 5  # 等待池中可用连接的秒数。
    MYSQL_POOL_RECYCLE_SECONDS: Final[int] = 1800  # 重新借用连接时允许的最长连接存续秒数。
    MYSQL_CONVERSATION_LOCK_WAIT_SECONDS: Final[int] = 0  # 同一会话已被占用时立即拒绝。
    MYSQL_POLICY_LOCK_WAIT_SECONDS: Final[int] = 0  # 同一文档版本已在发布时立即拒绝。
    MYSQL_CHARSET: Final[str] = "utf8mb4"  # MySQL 连线使用的 UTF-8 字符集。
    MYSQL_DATABASE_COLLATION: Final[str] = "utf8mb4_0900_ai_ci"  # 演示库初始化使用的排序规则。
    MYSQL_UTC_INIT_COMMAND: Final[str] = "SET time_zone = '+00:00'"  # 新建 MySQL 会话时统一使用 UTC。
    MCP_HTTP_SECONDS: Final[int] = 90  # MCP HTTP 传输超时秒数。
    POLICY_SESSION_SECONDS: Final[int] = 90  # 政策调查会话读取超时秒数。
    POLICY_CALL_SECONDS: Final[int] = 60  # 政策调查工具调用超时秒数。
    POLICY_BOUND_TOOL_SECONDS: Final[int] = 90  # 独立政策工具调用超时秒数。
    ORDER_CALL_SECONDS: Final[int] = 8  # 查单会话和工具调用超时秒数。


# MCP 调用中 Host 和服务端用于关联业务事件的协议字段。
class McpCorrelationField(StrEnum):
    TRACE_ID = "trace_id"  # 标识同一轮调查和办理。
    TOOL_EVENT_ID = "tool_event_id"  # 标识同一轮内的单次工具调用。


# 声明模拟退款业务读写共用的持久化名称。
class RefundTables:
    ORDERS: Final = "orders"  # 本人订单及实付事实。
    REFUNDS: Final = "refunds"  # 已确认的模拟退款。
    DISPATCHES: Final = "refund_dispatches"  # Host 保存的授权和提交状态。
    SESSIONS: Final = "conversation_sessions"  # 会话归属。
    MESSAGES: Final = "conversation_messages"  # 授权来源原文。
    DISPATCH_REFUND_FOREIGN_KEY: Final = "fk_dispatch_refund"  # 确保提交只引用同订单的退款。


# 声明数据库迁移和退款会话恢复识别的历史人民币字段。
class LegacyCurrency:
    FIELD: Final = "currency"  # 旧数据库列及旧快照金额字段的名称。
    CNY: Final = "CNY"  # 历史演示金额按人民币分保存时使用的值。


# 声明退款会话持久化使用的固定标识。
class ConversationStorage:
    LOCK_PREFIX: Final[str] = "order_demo:conversation:"  # 拼接会话 UUID，保持原 MySQL 命名锁。


# MySQL 政策发布目录使用的表名，建表和读写操作共用。
class PolicyCatalogTables:
    HEAD: Final[str] = 'policy_catalog_head'  # 保存对新调查开放的最新修订。
    RELEASES: Final[str] = 'policy_releases'  # 保存每份文档版本的发布历史。
    VERSION_LOCK_NAMESPACE: Final[str] = 'policy'  # 兼容原发布入口持有的版本锁。


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
    EXCERPT_BOUNDARIES: Final[str] = "。！？；\n"  # 中文条款原文可用的句段分隔符。
    EXPLICIT_SCOPE_WORD: Final[str] = "仅限"  # 提示原文具有明确适用范围的词。
    EXPLICIT_EXCEPTION_WORD: Final[str] = "除外"  # 提示原文有独立排除条件的词。
    MILVUS_CONNECT_TIMEOUT_SECONDS: Final[int] = 10  # 连接 Milvus 的超时秒数。
    MILVUS_SEARCH_TIMEOUT_SECONDS: Final[int] = 15  # Milvus 检索的超时秒数。
    MILVUS_WRITE_TIMEOUT_SECONDS: Final[int] = 30  # Milvus 建表及写入的超时秒数。


# 统一长期偏好表使用的字段长度。
class PreferenceLimits:
    MAX_CONSUMER_ID_CHARS: Final[int] = 64  # 消费者编号的字符上限。
