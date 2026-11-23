"""退款业务调查、执行和结果核对的 MCP 工具名称。"""

from enum import StrEnum


# 定义服务端发布、Host 绑定和模型工具共同使用的固定名称。
class McpToolName(StrEnum):
    GET_ORDER = "get_order"  # 查询已认证消费者本人的订单。
    SEARCH_POLICY = "search_policy"  # 检索当前版本的退款政策候选。
    GET_REFUND_RESULT = "get_refund_result"  # 固定节点只读核对本人订单原退款。
    SUBMIT_MOCK_REFUND = "submit_mock_refund"  # 固定执行身份发送一次模拟退款请求。
