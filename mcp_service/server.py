"""独立 Streamable HTTP 只读订单服务。"""

import argparse
from enum import StrEnum
from hmac import compare_digest
from typing import Annotated, Any
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
from model.orders.query import GetOrderRequest, OrderQueryId, OrderQueryResult
from model.policy.search import PolicyQueryText, PolicySearchResult, PolicySearchScope
from model.policy.status import PolicySearchStatus
from model.refund.tool_name import McpToolName
from utils.events import log_event


# 将有效的事件标识规范为 UUID 字符串，无效时返回空值。
def _event_uuid(value: object) -> str | None:
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


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
    meta = ctx.meta or {}
    log_event(
        event.value, trace_id=_event_uuid(meta.get("trace_id")),
        tool_event_id=_event_uuid(meta.get("tool_event_id")), mcp_request_id=ctx.request_id, **fields,
    )


# 统一执行演示消费者鉴权及额外参数检查，工具分发交给 SDK。
class _OrderMcpServer(MCPServer):
    # 声明已注册工具只接受签名中定义的参数字段。
    async def list_tools(self) -> list[types.Tool]:
        tools = await super().list_tools()
        for tool in tools:
            tool.input_schema["additionalProperties"] = False
        return tools

    # 校验访问身份及参数字段，保留拒绝结果和查单审计事件。
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None,
    ) -> types.CallToolResult | types.InputRequiredResult:
        # region 仅执行 HTTP 已认证消费者发起的工具调用
        ctx = context.request_context if context is not None else None
        if ctx is None or getattr(getattr(ctx.request, "state", None), "consumer_id", None) != CONSUMER_ID:
            return types.CallToolResult(content=[], is_error=True)
        # endregion

        # region 目标工具未注册时记录拒绝事件
        tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
        if tool is None:
            _log_tool_event(_McpEvent.TOOL_REJECTED, ctx, reason=_RejectionReason.UNKNOWN_TOOL.value)
            return types.CallToolResult(content=[], is_error=True)
        # endregion

        # region 参数没有多余字段时交由 SDK 校验取值并调用工具
        if arguments.keys() <= tool.input_schema["properties"].keys():
            try:
                return await super().call_tool(name, arguments, context)
            except ToolError as exc:
                if isinstance(exc, UnexpectedToolError) or not isinstance(exc.__cause__, ValidationError):
                    raise
        # endregion

        # region 参数无效时拒绝执行，查单请求额外记录拒绝原因
        if name == McpToolName.GET_ORDER.value:
            _log_tool_event(_McpEvent.TOOL_REJECTED, ctx, reason=_RejectionReason.BAD_ARGUMENTS.value)
        return types.CallToolResult(content=[], is_error=True)
        # endregion


# 创建提供查单和政策检索的只读 MCP 应用，并绑定 HTTP 访问凭据。
def make_app(pool, token: str):
    server = _OrderMcpServer("order-demo")
    read_only = types.ToolAnnotations(read_only_hint=True)

    # 根据已认证的演示消费者身份查单，返回结构化结果供 Agent 使用。
    @server.tool(name=McpToolName.GET_ORDER.value, description="读取当前消费者本人的一笔演示订单。",
                 annotations=read_only)
    def get_order(order_id: OrderQueryId, ctx: Context) -> Annotated[types.CallToolResult, OrderQueryResult]:
        # region 按认证身份读取订单并记录查询结果
        result = query_order(pool, GetOrderRequest(order_id=order_id), CONSUMER_ID)
        _log_tool_event(_McpEvent.GET_ORDER, ctx.request_context, status=result.status.value)
        return types.CallToolResult(content=[], structured_content=result.model_dump(mode="json"))
        # endregion

    # 按 Host 指定的范围检索政策候选，并记录关联的检索事件。
    @server.tool(name=McpToolName.SEARCH_POLICY.value, description="检索当前版本退款政策候选；范围由调用方控制。",
                 annotations=read_only)
    def search_policy(
        query: PolicyQueryText, mode: PolicySearchScope, ctx: Context,
    ) -> Annotated[types.CallToolResult, PolicySearchResult]:
        from mcp_service.policy.backend import PolicyBackend

        # region 每次检索独立关闭后端连接，后端异常时返回失败状态及空候选
        try:
            backend = PolicyBackend()
            try:
                result = search.search_policy(query, mode, backend)
            finally:
                backend.close()
        except Exception:
            result = PolicySearchResult(status=PolicySearchStatus.FAILED, event_id=str(uuid4()), candidates=[])
        # endregion

        # region 记录检索状态和事件编号，并返回结构化候选
        _log_tool_event(_McpEvent.SEARCH_POLICY, ctx.request_context,
                        status=result.status.value, policy_event_id=result.event_id)
        return types.CallToolResult(content=[], structured_content=result.model_dump(mode="json"))
        # endregion

    mcp = server.streamable_http_app(json_response=True, stateless_http=True)
    expected = f"Bearer {token}".encode("ascii")

    # 校验 HTTP 访问凭据，在请求上下文中绑定固定演示消费者。
    async def authenticated(scope: Scope, receive: Receive, send: Send) -> None:
        # region HTTP 凭据缺失或不匹配时拒绝请求，校验通过后绑定演示消费者
        if scope["type"] == "http":
            headers = Headers(scope=scope).getlist("authorization")
            if len(headers) != 1 or not compare_digest(headers[0].encode("latin-1"), expected):
                log_event(_McpEvent.AUTH_REJECTED.value)
                await Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
            scope.setdefault("state", {})["consumer_id"] = CONSUMER_ID
        # endregion
        await mcp(scope, receive, send)

    return authenticated


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
