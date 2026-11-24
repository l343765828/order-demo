"""固定退款节点调用的 MCP 工具，模型调查不接触这些适配器。"""

from asyncio import run
from collections.abc import Callable
from contextvars import Context
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from config import MCPConfig
from config.limits import ConnectionLimits
from model.orders.query import GetOrderRequest, OrderQueryResult
from model.refund.submission import GetRefundResultRequest, RefundLookupResult, RefundResult, SubmitMockRefundRequest
from model.refund.tool_name import McpToolName
from utils.mcp_client import bind_mcp_tool, mcp_correlation_meta, open_mcp_client

ToolResult = TypeVar("ToolResult", bound=BaseModel)


# 表示工具会话初始化或发现失败，尚未进入写工具调用。
class RefundToolUnavailable(Exception):
    """固定 MCP 工具尚未开始发送。"""


# 绑定固定 MCP 工具。每次调用只发送一次 SDK 底层请求。
def _call(settings: MCPConfig, token: str, name: McpToolName, request: BaseModel,
          decode: Callable[[dict[str, Any]], ToolResult], trace_id: str,
          before_send: Callable[[], None] | None = None) -> ToolResult:

    # region 使用指定身份独立建立会话并传递本次追踪标识
    attempted = False

    # 发送受控的单次请求并读取具名结果。
    async def invoke() -> ToolResult:

        # region 发现唯一工具并通过底层会话发送一次
        nonlocal attempted
        timeout = ConnectionLimits.REFUND_CALL_SECONDS
        async with open_mcp_client(settings.url, token=token, read_timeout_seconds=timeout) as client:
            tool = await bind_mcp_tool(client, name.value, decode)
            if before_send is not None:
                before_send()
            attempted = True
            return await tool.call(request, timeout=timeout,
                                   meta=mcp_correlation_meta(trace_id, str(uuid4())))
        # endregion

    try:
        return Context().run(run, invoke())
    except Exception as exc:

        # region 尚未调用工具时明确失败，否则保留发送后的原异常
        if not attempted:
            raise RefundToolUnavailable from exc
        raise
        # endregion

    # endregion


# 固定复核节点以调查凭据读取最新本人订单。
def read_order(settings: MCPConfig, order_id: str, trace_id: str) -> OrderQueryResult:

    # region 使用正式订单契约发出不经过模型的只读复核
    return _call(settings, settings.read_token, McpToolName.GET_ORDER,
                 GetOrderRequest(order_id=order_id), OrderQueryResult.from_mcp, trace_id)
    # endregion


# 固定核对节点以调查凭据读取原退款。
def read_refund(settings: MCPConfig, request: GetRefundResultRequest, trace_id: str) -> RefundLookupResult:

    # region 通过只读通道取得真实原退款结果
    return _call(settings, settings.read_token, McpToolName.GET_REFUND_RESULT, request,
                 RefundLookupResult.from_mcp, trace_id)
    # endregion


# 仅固定执行节点使用独立凭据发送已确认提交。
def send_refund(settings: MCPConfig, request: SubmitMockRefundRequest, trace_id: str,
                *, before_send: Callable[[], None]) -> RefundResult:

    # region 执行身份可用时单次发送并校验结构化业务结果
    if settings.write_token is None:
        raise RefundToolUnavailable("退款执行身份未配置")
    return _call(settings, settings.write_token, McpToolName.SUBMIT_MOCK_REFUND, request,
                 RefundResult.from_mcp, trace_id, before_send)
    # endregion
