"""退款演示使用的固定配置和约束。"""

from typing import Final


# 统一退款业务流程和请求契约使用的长度上限，按字符计数。
class RefundLimits:
    MAX_POLICY_QUERY_CHARS: Final[int] = 2_000  # 政策首次查询及补查文本的字符上限。
    MAX_CONVERSATION_HISTORY_CHARS: Final[int] = 3_500  # 本轮累计会话消息正文的字符上限。
    MAX_ORDER_ID_CHARS: Final[int] = 64  # 订单调查和查单请求接受的订单号字符上限。
    MAX_STEP_ID_CHARS: Final[int] = 64  # 调查步骤编号的字符上限。
    MAX_DECISION_TEXT_CHARS: Final[int] = 160  # 步骤完成条件和重规划原因的字符上限。
    MAX_INVESTIGATION_STEPS: Final[int] = 2  # 单次计划中的调查步骤上限。


# 统一外部连接和工具调用的超时。
class ConnectionLimits:
    MYSQL_CONNECT_SECONDS: Final[int] = 5  # MySQL 连接超时秒数。
    MCP_HTTP_SECONDS: Final[int] = 90  # MCP HTTP 传输超时秒数。
    POLICY_SESSION_SECONDS: Final[int] = 90  # 政策调查会话读取超时秒数。
    POLICY_CALL_SECONDS: Final[int] = 60  # 政策调查工具调用超时秒数。
    POLICY_BOUND_TOOL_SECONDS: Final[int] = 90  # 独立政策工具调用超时秒数。
    ORDER_CALL_SECONDS: Final[int] = 8  # 查单会话和工具调用超时秒数。


# 统一政策向量存储和检索使用的集合名称与约束。
class PolicyLimits:
    MILVUS_COLLECTION_NAME: Final[str] = 'order_demo_refund_policy'  # 退款政策在 Milvus 中的专用集合。
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
