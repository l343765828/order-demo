"""发现并校验 MCP 工具，使用受控连接执行单次调用。"""

from typing import Any, Generic, TypeVar

import httpx2
from mcp import Client
from mcp.types import RequestParamsMeta
from pydantic import BaseModel

Result = TypeVar("Result", bound=BaseModel)


# 创建禁止自动重定向的 MCP HTTP 客户端，由调用方负责关闭。
def controlled_mcp_http() -> httpx2.AsyncClient:
    """创建受控连接；调用方负责用 async with 关闭。"""
    # MCP SDK 独立处理跳转，必须将 max_redirects 设为 0 才能阻止跳转重发 POST。
    return httpx2.AsyncClient(max_redirects=0, trust_env=False, timeout=90)


# 保存已校验输入输出契约的 MCP 工具及当前会话。
class _BoundMcpTool(Generic[Result]):
    """保存已核对契约的工具及其会话；会话须在调用期间保持打开。"""

    # 绑定 MCP 会话、工具名称和请求及结果模型。
    def __init__(
        self,
        client: Client,
        name: str,
        arguments: type[BaseModel],
        result: type[Result],
    ) -> None:
        self._session = client.session
        self._name = name
        self._arguments = arguments
        self._result = result

    # 校验工具参数，发送单次请求，并按具名模型校验结构化结果。
    async def call(
        self, arguments: dict[str, Any], *, timeout: float | None = None,
        meta: RequestParamsMeta | None = None,
    ) -> Result:
        """校验参数，发送一次工具调用，并将结构化结果校验为具名模型。"""
        validated = self._arguments.model_validate(arguments)
        # 高层 call_tool 可能重新发现后重发或自动追加回合，故使用 session 的单次调用接口。
        response = await self._session.call_tool(
            self._name, validated.model_dump(mode="json"), read_timeout_seconds=timeout, meta=meta,
        )
        if response.is_error:
            raise RuntimeError(f"{self._name} returned a tool error")
        # 即使 SDK 校验了服务端 Schema，仍须按调用方的具名模型验证结果。
        return self._result.model_validate(response.structured_content)


# 发现目标工具并核对双方 Schema，匹配时返回已绑定工具。
async def bind_mcp_tool(
    client: Client,
    name: str,
    arguments: type[BaseModel],
    result: type[Result],
) -> _BoundMcpTool[Result]:
    """仅在工具目录和双方输入、输出 Schema 完全一致时绑定工具。"""
    page = await client.list_tools()
    tools = [tool for tool in page.tools if tool.name == name]
    # 当前只接受单页目录，避免遗漏后续页上的同名工具或契约变化。
    if page.next_cursor or len(tools) != 1:
        raise ValueError(f"Expected exactly one {name} tool in an unpaginated catalog")
    if tools[0].input_schema != arguments.model_json_schema():
        raise ValueError(f"{name} input schema does not match {arguments.__name__}")
    if tools[0].output_schema != result.model_json_schema():
        raise ValueError(f"{name} output schema does not match {result.__name__}")
    return _BoundMcpTool(client, name, arguments, result)
