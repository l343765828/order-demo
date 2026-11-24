"""管理 MCP 会话，发现工具并执行单次调用。"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Generic, TypeVar, cast

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import RequestParamsMeta
from pydantic import BaseModel
from config.limits import ConnectionLimits, McpCorrelationField

Result = TypeVar("Result", bound=BaseModel)


# 生成供 MCP 客户端传递的追踪和单次工具事件标识。
def mcp_correlation_meta(trace_id: str, tool_event_id: str) -> RequestParamsMeta:

    # region 按协议字段名称组装扩展元数据
    return cast(RequestParamsMeta, {
        McpCorrelationField.TRACE_ID.value: trace_id,
        McpCorrelationField.TOOL_EVENT_ID.value: tool_event_id,
    })
    # endregion


# 创建禁止自动重定向的 MCP HTTP 客户端，由调用方负责关闭。
def controlled_mcp_http() -> httpx2.AsyncClient:

    # region 创建禁止自动重定向的 MCP HTTP 客户端，由调用方负责关闭
    """创建受控连接；调用方负责用 async with 关闭。"""
    # MCP SDK 独立处理跳转，必须将 max_redirects 设为 0 才能阻止跳转重发 POST。
    return httpx2.AsyncClient(max_redirects=0, trust_env=False, timeout=ConnectionLimits.MCP_HTTP_SECONDS)
    # endregion


# 通过指定服务地址和只读凭据建立 MCP 会话，退出时先关闭会话，再关闭 HTTP 连接。
@asynccontextmanager
async def open_mcp_client(url: str, *, token: str, read_timeout_seconds: float) -> AsyncIterator[Client]:

    # region 通过指定服务地址和只读凭据建立 MCP 会话，退出时先关闭会话，再关闭 HTTP 连接
    async with controlled_mcp_http() as http:
        http.headers["Authorization"] = f"Bearer {token}"
        transport = streamable_http_client(url, http_client=http)
        async with Client(transport, read_timeout_seconds=read_timeout_seconds) as client:
            yield client
    # endregion


# 保存已发现的 MCP 工具、结果转换函数及当前会话。
class _BoundMcpTool(Generic[Result]):
    # 绑定 MCP 会话、工具名称及结构化结果的转换函数。
    def __init__(
        self,
        client: Client,
        name: str,
        decode_result: Callable[[dict[str, Any]], Result],
    ) -> None:

        # region 绑定 MCP 会话、工具名称及结构化结果的转换函数
        self._session = client.session
        self._name = name
        self._decode_result = decode_result
        # endregion

    # 发送已构造的请求模型，并将 SDK 接收的结构化结果转换成业务模型。
    async def call(
        self, arguments: BaseModel, *, timeout: float | None = None,
        meta: RequestParamsMeta | None = None,
    ) -> Result:

        # region 通过会话接口发送一次请求，避免高层接口自动追加调用
        response = await self._session.call_tool(
            self._name, arguments.model_dump(mode="json"), read_timeout_seconds=timeout, meta=meta,
        )
        # endregion

        # region 拒绝工具错误，转换 SDK 已按工具 Schema 检查的结构化响应
        if response.is_error:
            raise RuntimeError(f"{self._name} returned a tool error")
        return self._decode_result(cast(dict[str, Any], response.structured_content))
        # endregion


# 发现唯一的目标工具并绑定结果转换函数，会话须在调用期间保持打开。
async def bind_mcp_tool(
    client: Client,
    name: str,
    decode_result: Callable[[dict[str, Any]], Result],
) -> _BoundMcpTool[Result]:

    # region 仅绑定单页目录中的唯一匹配，目录同时为 SDK 缓存工具元数据
    page = await client.list_tools()
    tools = [tool for tool in page.tools if tool.name == name]
    if (
        # 工具目录还有后续页，当前页无法确认全目录的匹配项。
        page.next_cursor
        # 当前页缺少指定工具或该工具名称重复。
        or len(tools) != 1
    ):
        raise ValueError(f"Expected exactly one {name} tool in an unpaginated catalog")
    return _BoundMcpTool(client, name, decode_result)
    # endregion
