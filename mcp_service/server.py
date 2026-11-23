"""独立 Streamable HTTP 只读订单服务。"""

import argparse
from enum import StrEnum
from hmac import compare_digest
from typing import Any
from uuid import UUID, uuid4

import uvicorn
from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from config import load_config
from config.demo import CONSUMER_ID
from mcp_service.orders.query import query_order
from mcp_service.policy import search
from model import create_pool
from model.orders.query import GetOrderRequest, OrderQueryId
from model.policy.search import PolicyQueryText, PolicySearchResult, PolicySearchScope
from model.policy.status import PolicySearchStatus
from model.refund.tool_name import McpToolName
from utils.events import log_event


# 将有效的事件标识规范为 UUID 字符串，无效时返回空值。
def _event_uuid(value: object) -> str | None:
    # region 将有效的事件标识规范为 UUID 字符串，无效时返回空值
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None
    # endregion


# 定义 MCP 服务对外记录的认证、拒绝及工具结果事件。
class _McpEvent(StrEnum):
    AUTH_REJECTED = "mcp_auth_rejected"  # HTTP 凭据缺失或无效。
    TOOL_REJECTED = "mcp_tool_rejected"  # 工具未注册或查单参数无效。
    GET_ORDER = "mcp_get_order"  # 查询本人订单后记录业务结果。
    SEARCH_POLICY = "mcp_search_policy"  # 政策检索结束后记录状态及检索事件标识。


# 区分工具拒绝事件的协议原因。
class _RejectionReason(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"  # 调用目标未在服务上注册。
    BAD_ARGUMENTS = "bad_arguments"  # 查单参数字段、类型或取值不符合契约。


# 将工具日志与客户端追踪标识和 SDK 请求标识关联。
def _log_tool_event(event: _McpEvent, ctx: ServerRequestContext, **fields) -> None:
    # region 将工具日志与客户端追踪标识和 SDK 请求标识关联
    meta = ctx.meta or {}
    log_event(
        event.value, trace_id=_event_uuid(meta.get("trace_id")),
        tool_event_id=_event_uuid(meta.get("tool_event_id")), mcp_request_id=ctx.request_id, **fields,
    )
    # endregion


# 将 SDK 拒绝工具调用的结果记录到现有审计日志。
class _OrderMcpServer(MCPServer):
    # 由 SDK 校验和执行工具，按拒绝原因保留现有失败响应和日志。
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        # region 执行 SDK 工具调用并处理可识别的拒绝结果
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            # region 缺少请求上下文或发生非预期工具错误时继续抛出
            if (
                # 无法从调用上下文取得本次 MCP 请求信息。
                context is None
                # 工具执行出现非预期错误，需要向上层传播。
                or isinstance(exc, UnexpectedToolError)
            ):
                raise
            # endregion
            # region 按拒绝原因记录未知工具或无效查单参数并返回工具错误
            ctx = context.request_context
            match exc.__cause__:
                case None:
                    _log_tool_event(_McpEvent.TOOL_REJECTED, ctx, reason=_RejectionReason.UNKNOWN_TOOL.value)
                case ValidationError() if name == McpToolName.GET_ORDER.value:
                    _log_tool_event(_McpEvent.TOOL_REJECTED, ctx, reason=_RejectionReason.BAD_ARGUMENTS.value)
                case ValidationError():
                    pass
                case _:
                    raise
            return types.CallToolResult(content=[], is_error=True)
            # endregion
        # endregion


# 创建提供查单和政策检索的只读 MCP 应用，并绑定 HTTP 访问凭据。
def make_app(pool, token: str):
    # region 初始化 MCP 服务和只读工具注解
    server = _OrderMcpServer("order-demo")
    read_only = types.ToolAnnotations(read_only_hint=True)
    # endregion

    # region 注册本人订单查询工具
    # 根据已认证的演示消费者身份查单，返回结构化结果供 Agent 使用。
    @server.tool(name=McpToolName.GET_ORDER.value, description="读取当前消费者本人的一笔演示订单。",
                 annotations=read_only)
    def get_order(order_id: OrderQueryId, ctx: Context) -> types.CallToolResult:
        # region 查询固定演示消费者的订单并返回结构化结果
        result = query_order(pool, GetOrderRequest.model_construct(order_id=order_id), CONSUMER_ID)
        _log_tool_event(_McpEvent.GET_ORDER, ctx.request_context, status=result.status.value)
        return types.CallToolResult(content=[], structured_content=result.model_dump(mode="json"))
        # endregion
    # endregion

    # region 注册退款政策检索工具
    # 按 Host 指定的范围检索政策候选，并记录关联的检索事件。
    @server.tool(name=McpToolName.SEARCH_POLICY.value, description="检索当前版本退款政策候选；范围由调用方控制。",
                 annotations=read_only)
    def search_policy(
        query: PolicyQueryText, mode: PolicySearchScope, ctx: Context,
    ) -> types.CallToolResult:
        # region 按指定范围检索政策并释放后端连接
        from mcp_service.policy.backend import PolicyBackend

        try:
            backend = PolicyBackend()
            try:
                result = search.search_policy(query, mode, backend)
            finally:
                backend.close()
        except Exception:
            # region 后端发生异常时生成失败结果和空候选
            result = PolicySearchResult(status=PolicySearchStatus.FAILED, event_id=str(uuid4()), candidates=[])
            # endregion
        # endregion

        # region 记录检索状态和事件编号并返回结构化结果
        _log_tool_event(_McpEvent.SEARCH_POLICY, ctx.request_context,
                        status=result.status.value, policy_event_id=result.event_id)
        return types.CallToolResult(content=[], structured_content=result.model_dump(mode="json"))
        # endregion
    # endregion

    # region 创建无状态 MCP Streamable HTTP 应用
    mcp = server.streamable_http_app(json_response=True, stateless_http=True)
    # endregion

    # region 封装 HTTP 认证并返回 ASGI 应用
    expected = f"Bearer {token}".encode("ascii")

    # 校验 HTTP 请求的 Bearer 凭据，放行后将 ASGI 事件交给 MCP 应用。
    async def authenticated(scope: Scope, receive: Receive, send: Send) -> None:
        # region Authorization 请求头缺失、重复或凭据不匹配时返回 401
        if scope["type"] == "http":
            headers = Headers(scope=scope).getlist("authorization")
            if (
                # HTTP 请求缺少 Authorization 请求头或重复提供了该请求头。
                len(headers) != 1
                # 唯一的 Authorization 请求头与服务要求的 Bearer 凭据不匹配。
                or not compare_digest(headers[0].encode("latin-1"), expected)
            ):
                log_event(_McpEvent.AUTH_REJECTED.value)
                await Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
        # endregion
        # region 将其余 ASGI 事件交给 MCP 应用
        await mcp(scope, receive, send)
        # endregion

    return authenticated
    # endregion


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="订单 MCP 服务")
    parser.parse_args()
    mcp_config = load_config().mcp
    pool = create_pool()
    try:
        uvicorn.run(make_app(pool, mcp_config.read_token), host=mcp_config.host,
                    port=mcp_config.port, access_log=False, log_level="warning")
    finally:
        pool.close()
