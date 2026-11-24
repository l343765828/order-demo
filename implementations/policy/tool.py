"""将 Host 固定的政策检索范围绑定到模型工具。"""
from langchain_core.tools import StructuredTool
from config.limits import ConnectionLimits
from model.policy.search import PolicyQuery, SearchPolicyRequest
from model.policy.status import PolicySearchMode
from model.refund.tool_name import McpToolName


# 创建只暴露查询文本的模型工具，mode 只能由可信 Host 在绑定时指定。
def make_policy_tool(bound, mode: str):

    # region 绑定 Host 指定的检索范围并返回政策查询工具
    scope = PolicySearchMode(mode)

    # 保持服务端返回的检索状态和来源，不在工具内判断证据充分性。
    async def search_policy(query: str):

        # region 按绑定范围调用 MCP 并返回检索状态和候选
        result = await bound.call(SearchPolicyRequest(query=query, mode=scope),
                                  timeout=ConnectionLimits.POLICY_BOUND_TOOL_SECONDS)
        return result.model_dump(mode='json')
        # endregion

    return StructuredTool.from_function(coroutine=search_policy, name=McpToolName.SEARCH_POLICY.value,
                                        description='检索退款政策候选及其来源。', args_schema=PolicyQuery)
    # endregion
