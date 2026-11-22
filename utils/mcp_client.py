"""管理 MCP 会话，发现工具并执行单次调用。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Generic, TypeVar

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import RequestParamsMeta
from pydantic import BaseModel
from config.limits import ConnectionLimits

Result = TypeVar("Result", bound=BaseModel)


# 创建禁止自动重定向的 MCP HTTP 客户端，由调用方负责关闭。
def controlled_mcp_http() -> httpx2.AsyncClient:
    """创建受控连接；调用方负责用 async with 关闭。"""
    # MCP SDK 独立处理跳转，必须将 max_redirects 设为 0 才能阻止跳转重发 POST。
    return httpx2.AsyncClient(max_redirects=0, trust_env=False, timeout=ConnectionLimits.MCP_HTTP_SECONDS)


# 通过指定服务地址和只读凭据建立 MCP 会话，退出时先关闭会话，再关闭 HTTP 连接。
@asynccontextmanager
async def open_mcp_client(url: str, *, token: str, read_timeout_seconds: float) -> AsyncIterator[Client]:
    async with controlled_mcp_http() as http:
        http.headers["Authorization"] = f"Bearer {token}"
        transport = streamable_http_client(url, http_client=http)
        async with Client(transport, read_timeout_seconds=read_timeout_seconds) as client:
            yield client


# 保存已发现的 MCP 工具、结果契约及当前会话。
class _BoundMcpTool(Generic[Result]):
    # 绑定 MCP 会话、工具名称和用来校验响应的结果模型。
    def __init__(
        self,
        client: Client,
        name: str,
        result: type[Result],
    ) -> None:
        self._session = client.session
        self._name = name
        self._result = result

    # 发送已构造的请求模型，并按具名模型校验实际返回的结构化结果。
    async def call(
        self, arguments: BaseModel, *, timeout: float | None = None,
        meta: RequestParamsMeta | None = None,
    ) -> Result:
        # region 通过会话接口发送一次请求，避免高层接口自动追加调用
        response = await self._session.call_tool(
            self._name, arguments.model_dump(mode="json"), read_timeout_seconds=timeout, meta=meta,
        )
        # endregion
        # region 拒绝工具错误，并按客户端契约校验成功响应
        if response.is_error:
            raise RuntimeError(f"{self._name} returned a tool error")
        return self._result.model_validate(response.structured_content)
        # endregion


# 发现唯一的目标工具并绑定结果模型，会话须在调用期间保持打开。
async def bind_mcp_tool(
    client: Client,
    name: str,
    result: type[Result],
) -> _BoundMcpTool[Result]:
    # region 仅绑定单页目录中的唯一匹配，目录同时为 SDK 缓存工具元数据
    page = await client.list_tools()
    tools = [tool for tool in page.tools if tool.name == name]
    if page.next_cursor or len(tools) != 1:
        raise ValueError(f"Expected exactly one {name} tool in an unpaginated catalog")
    # endregion
    return _BoundMcpTool(client, name, result)
