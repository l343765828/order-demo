"""独立 Streamable HTTP 只读订单服务。"""

import argparse
from hmac import compare_digest
from uuid import UUID

import uvicorn
from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from model import create_pool
from config import load_config, use_config
from mcp_service.orders.query import query_order
from model.orders.query import GetOrderRequest, OrderQueryResult
from model.policy.search import SearchPolicyRequest, PolicySearchResult
from model.policy.status import PolicySearchStatus
from model.refund.tool_name import McpToolName
from mcp_service.policy import search_policy
from order_runtime import CONSUMER_ID, log_event, read_token


# 将有效的事件标识规范为 UUID 字符串，无效时返回空值。
def _event_uuid(value: object) -> str | None:
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


# 创建仅开放只读订单查询并校验访问凭据的 MCP 应用。
def make_app(pool, token: str):
    tool = types.Tool(
        name=McpToolName.GET_ORDER.value, description="读取当前消费者本人的一笔演示订单。",
        input_schema=GetOrderRequest.model_json_schema(),
        output_schema=OrderQueryResult.model_json_schema(),
        annotations=types.ToolAnnotations(read_only_hint=True),
    )
    expected = f"Bearer {token}".encode("ascii")
    policy_tool = types.Tool(
        name=McpToolName.SEARCH_POLICY.value, description="检索当前版本退款政策候选；范围由调用方控制。",
        input_schema=SearchPolicyRequest.model_json_schema(),
        output_schema=PolicySearchResult.model_json_schema(),
        annotations=types.ToolAnnotations(read_only_hint=True),
    )

    # 返回本服务允许客户端发现的查单工具定义。
    async def list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[tool, policy_tool])

    # 校验认证上下文和工具参数，查询本人订单并记录结果事件。
    async def get_order(ctx: ServerRequestContext, params: types.CallToolRequestParams):
        # region 认证上下文匹配已配置消费者后才接收工具调用并关联请求事件
        consumer = getattr(getattr(ctx.request, "state", None), "consumer_id", None)
        if consumer != CONSUMER_ID:
            return types.CallToolResult(content=[], is_error=True)
        meta = ctx.meta or {}
        event = dict(
            trace_id=_event_uuid(meta.get("trace_id")),
            tool_event_id=_event_uuid(meta.get("tool_event_id")),
            mcp_request_id=ctx.request_id,
        )
        # endregion

        match params.name:
            # region 政策请求校验检索参数，后端异常时返回失败状态
            case policy_tool.name:
                try:
                    policy_request = SearchPolicyRequest.model_validate(params.arguments)
                except ValidationError:
                    return types.CallToolResult(content=[], is_error=True)

                # 每次请求独立关闭后端连接，连接或检索失败均返回失败状态。
                def find_policy():
                    from mcp_service.policy_backend import PolicyBackend
                    from uuid import uuid4
                    try:
                        backend = PolicyBackend()
                        try:
                            return search_policy(policy_request.query, policy_request.mode, backend)
                        finally:
                            backend.close()
                    except Exception:
                        return PolicySearchResult(status=PolicySearchStatus.FAILED, event_id=str(uuid4()), candidates=[])

                result = await run_in_threadpool(find_policy)
                log_event("mcp_search_policy", status=result.status.value, policy_event_id=result.event_id, **event)
                return types.CallToolResult(content=[], structured_content=result.model_dump(mode="json"))
            # endregion

            # region 查单参数校验通过后，按认证身份读取订单
            case tool.name:
                try:
                    request = GetOrderRequest.model_validate(params.arguments)
                except ValidationError:
                    log_event("mcp_tool_rejected", reason="bad_arguments", **event)
                    return types.CallToolResult(content=[], is_error=True)
                result = await run_in_threadpool(query_order, pool, request, consumer)
                log_event("mcp_get_order", status=result.status.value, **event)
                return types.CallToolResult(content=[], structured_content=result.model_dump(mode="json"))
            # endregion

            # region 工具名称不在白名单时拒绝调用
            case _:
                log_event("mcp_tool_rejected", reason="unknown_tool", **event)
                return types.CallToolResult(content=[], is_error=True)
            # endregion

    server = Server("order-demo", on_list_tools=list_tools, on_call_tool=get_order)
    mcp = server.streamable_http_app(json_response=True, stateless_http=True)

    # 校验 HTTP 访问凭据，在请求上下文中绑定固定演示消费者。
    async def authenticated(scope: Scope, receive: Receive, send: Send) -> None:
        # region HTTP 凭据缺失或不匹配时拒绝请求，校验通过后绑定演示消费者
        if scope["type"] == "http":
            headers = Headers(scope=scope).getlist("authorization")
            if len(headers) != 1 or not compare_digest(headers[0].encode("latin-1"), expected):
                log_event("mcp_auth_rejected")
                await Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
            scope.setdefault("state", {})["consumer_id"] = CONSUMER_ID
        # endregion
        await mcp(scope, receive, send)

    return authenticated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="订单 MCP 服务")
    parser.add_argument("--config", help="JSON 配置文件路径")
    args = parser.parse_args()
    use_config(args.config)
    token = read_token()
    pool = create_pool()
    try:
        uvicorn.run(make_app(pool, token), host=load_config().mcp.host,
                    port=load_config().mcp.port, access_log=False, log_level="warning")
    finally:
        pool.close()
