"""退款业务只读调查的 MCP 工具名称。"""

from enum import StrEnum


# 定义服务端发布、Host 绑定和模型工具共同使用的固定名称。
class McpToolName(StrEnum):
    GET_ORDER = "get_order"  # 查询已认证消费者本人的订单。
    SEARCH_POLICY = "search_policy"  # 检索当前版本的退款政策候选。
