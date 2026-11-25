"""独立 Streamable HTTP 退款业务服务。"""

import argparse
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
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
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from config import AppConfig, load_config
from config.demo import CONSUMER_ID
from config.limits import McpCorrelationField, PolicyLimits
from implementations.orders.query import MySQLOrderQueryService
from implementations.policy.catalog import MySQLPolicyCatalog
from implementations.refund.transactions import MySQLRefundTransactionService
from interfaces.orders.query import OrderQueryService
from interfaces.policy.catalog import PolicyCatalogReader
from interfaces.policy.search import PolicySearchBackend
from interfaces.refund.storage import RefundTransactionService
from mcp_service.policy import search
from model.orders.query import GetOrderRequest, OrderQueryId
from model.policy.search import PolicyCatalogRevision, PolicyQueryText, PolicySearchResult, PolicySearchScope
from model.policy.status import PolicySearchStatus
from model.refund.tool_name import McpToolName
from model.refund.submission import (
    GetRefundResultRequest, RefundIdentifier, RefundLookupResult, RefundResult, SubmitMockRefundRequest,
)
from utils.database import create_mysql_engine
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
    GET_REFUND_RESULT = "mcp_get_refund_result"  # 固定节点核对本人订单的原退款。
    SUBMIT_REFUND = "mcp_submit_mock_refund"  # 受控执行事务给出业务结果。


# 区分工具拒绝事件的协议原因。
class _RejectionReason(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"  # 调用目标未在服务上注册。
    BAD_ARGUMENTS = "bad_arguments"  # 查单参数字段、类型或取值不符合契约。
    WRITE_FORBIDDEN = "write_forbidden"  # 调查身份或缺少可信请求上下文。


# 将工具日志与客户端追踪标识和 SDK 请求标识关联。
def _log_tool_event(event: _McpEvent, ctx: ServerRequestContext, **fields) -> None:

    # region 将工具日志与客户端追踪标识和 SDK 请求标识关联
    meta = ctx.meta or {}
    log_event(
        event.value, trace_id=_event_uuid(meta.get(McpCorrelationField.TRACE_ID.value)),
        tool_event_id=_event_uuid(meta.get(McpCorrelationField.TOOL_EVENT_ID.value)),
        mcp_request_id=ctx.request_id, **fields,
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
                case None if name != McpToolName.SUBMIT_MOCK_REFUND.value:
                    _log_tool_event(_McpEvent.TOOL_REJECTED, ctx, reason=_RejectionReason.UNKNOWN_TOOL.value)
                case None:
                    pass
                case ValidationError() if name == McpToolName.GET_ORDER.value:
                    _log_tool_event(_McpEvent.TOOL_REJECTED, ctx, reason=_RejectionReason.BAD_ARGUMENTS.value)
                case ValidationError():
                    pass
                case _:
                    raise
            return types.CallToolResult(content=[], is_error=True)
            # endregion

        # endregion


# 创建退款 MCP 应用，认证调查及受控执行凭据，按请求打开注入的政策后端。
def make_app(
    token: str, write_token: str | None = None, *,
    order_service: OrderQueryService, refund_service: RefundTransactionService, policy_catalog: PolicyCatalogReader,
    open_backend: Callable[[], AbstractContextManager[PolicySearchBackend]],
) -> ASGIApp:

    # region 初始化 MCP 服务和只读工具注解
    server = _OrderMcpServer("order-demo")
    read_only = types.ToolAnnotations(read_only_hint=True)
    expected = f"Bearer {token}".encode("ascii")
    if write_token == token:
        raise ValueError("调查和执行必须使用不同凭据")
    expected_write = f"Bearer {write_token}".encode("ascii") if write_token else None
    # endregion

    # region 注册固定只读结果核对和受控退款写入
    # 只读核对已有退款并关联调用和提交标识。
    @server.tool(name=McpToolName.GET_REFUND_RESULT.value, description="核对本人订单的原模拟退款记录。",
                 annotations=read_only)
    def get_refund_result(
        order_id: OrderQueryId, ctx: Context, idempotency_key: RefundIdentifier | None = None,
    ) -> RefundLookupResult:

        # region 读取真实原退款并记录关联事件
        result = refund_service.lookup_refund(
            GetRefundResultRequest.model_construct(order_id=order_id, idempotency_key=idempotency_key),
            authenticated_consumer_id=CONSUMER_ID,
        )
        _log_tool_event(_McpEvent.GET_REFUND_RESULT, ctx.request_context,
                        order_id=order_id, idempotency_key=idempotency_key, status=result.status.value)
        return result
        # endregion

    # 校验传输层执行凭据，合格时按提交及订单事实完成退款事务。
    @server.tool(name=McpToolName.SUBMIT_MOCK_REFUND.value, description="依据有效提交执行整单模拟退款。",
                 annotations=types.ToolAnnotations(read_only_hint=False))
    def submit_mock_refund(order_id: OrderQueryId, idempotency_key: RefundIdentifier, ctx: Context) -> RefundResult:

        # region 调查身份或缺少执行身份时拒绝调用
        context = ctx.request_context
        if (
            # 服务未配置受控执行身份。
            expected_write is None
            # 调用没有经过 HTTP 认证入口。
            or not isinstance(context.request, Request)
            # 调用未持有独立执行凭据。
            or not compare_digest(context.request.headers.get("authorization", "").encode("latin-1"), expected_write)
        ):
            _log_tool_event(_McpEvent.TOOL_REJECTED, context, reason=_RejectionReason.WRITE_FORBIDDEN.value)
            raise ToolError("退款执行身份不可用")
        # endregion

        # region 执行退款事务并记录结果供跨进程对账
        result = refund_service.submit_refund(
            SubmitMockRefundRequest.model_construct(order_id=order_id, idempotency_key=idempotency_key),
            authenticated_consumer_id=CONSUMER_ID,
        )
        _log_tool_event(_McpEvent.SUBMIT_REFUND, context, order_id=order_id,
                        idempotency_key=idempotency_key, status=result.status.value)
        return result
        # endregion

    # endregion

    # region 注册本人订单查询工具
    # 根据已认证的演示消费者身份查单，返回结构化结果供 Agent 使用。
    @server.tool(name=McpToolName.GET_ORDER.value, description="读取当前消费者本人的一笔演示订单。",
                 annotations=read_only)
    def get_order(order_id: OrderQueryId, ctx: Context) -> types.CallToolResult:

        # region 查询固定演示消费者的订单并返回结构化结果
        result = order_service.query_order(
            GetOrderRequest.model_construct(order_id=order_id), authenticated_consumer_id=CONSUMER_ID,
        )
        _log_tool_event(_McpEvent.GET_ORDER, ctx.request_context, status=result.status.value)
        return types.CallToolResult(content=[], structured_content=result.model_dump(mode="json"))
        # endregion

    # endregion

    # region 注册退款政策检索工具
    # 按 Host 指定的范围检索政策候选，并记录关联的检索事件。
    @server.tool(name=McpToolName.SEARCH_POLICY.value, description="检索指定目录修订内的退款政策候选；首次调用可省略修订号。",
                 annotations=read_only)
    def search_policy(
        query: PolicyQueryText, mode: PolicySearchScope, ctx: Context,
        catalog_revision: PolicyCatalogRevision | None = None,
    ) -> PolicySearchResult:

        # region 按指定范围检索政策并释放后端连接
        try:
            with open_backend() as backend:
                result = search.search_policy(query, mode, backend, catalog=policy_catalog,
                                              catalog_revision=catalog_revision)
        except Exception:

            # region 后端发生异常时生成失败结果和空候选
            result = PolicySearchResult(status=PolicySearchStatus.FAILED, event_id=str(uuid4()), candidates=[])
            # endregion

        # endregion

        # region 记录检索状态和事件编号并返回结构化结果
        _log_tool_event(_McpEvent.SEARCH_POLICY, ctx.request_context,
                        status=result.status.value, policy_event_id=result.event_id, mode=mode.value,
                        requested_catalog_revision=catalog_revision, catalog_revision=result.catalog_revision)
        return result
        # endregion

    # endregion

    # region 创建无状态 MCP Streamable HTTP 应用
    mcp = server.streamable_http_app(json_response=True, stateless_http=True)
    # endregion

    # region 封装 HTTP 认证并返回 ASGI 应用
    # 校验 HTTP 请求的 Bearer 凭据，放行后将 ASGI 事件交给 MCP 应用。
    async def authenticated(scope: Scope, receive: Receive, send: Send) -> None:

        # region Authorization 请求头缺失、重复或凭据不匹配时返回 401
        if scope["type"] == "http":
            headers = Headers(scope=scope).getlist("authorization")
            if (
                # HTTP 请求缺少 Authorization 请求头或重复提供了该请求头。
                len(headers) != 1
                # 唯一的 Authorization 请求头与服务要求的 Bearer 凭据不匹配。
                or not (
                    # 请求持有调查凭据。
                    compare_digest(headers[0].encode("latin-1"), expected)
                    # 请求持有已配置的执行凭据。
                    or (
                        # 服务已启用独立执行身份。
                        expected_write is not None
                        # 请求头与执行凭据完全匹配。
                        and compare_digest(headers[0].encode("latin-1"), expected_write)
                    )
                )
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


# 根据启动时的配置组装业务和政策目录 Service，退出时释放连接池。
@contextmanager
def open_app(settings: AppConfig) -> Iterator[ASGIApp]:

    # region 绑定启动设置，收到政策检索请求时才加载并打开后端
    # 以已读取的设置打开单次检索使用的政策后端。
    def open_backend() -> AbstractContextManager[PolicySearchBackend]:

        # region 延后加载政策后端并传入启动配置
        from implementations.policy.backend import open_policy_backend

        return open_policy_backend(settings)
        # endregion

    # endregion

    # region 按数据库分配连接池并为 MCP 工具注入 Service
    with ExitStack() as resources:
        engine = create_mysql_engine(settings.mysql)
        resources.callback(engine.dispose)
        database = settings.policy.catalog_database if settings.policy is not None else PolicyLimits.CATALOG_DATABASE
        catalog_engine = engine
        if database != settings.mysql.database:

            # region 政策目录配置了独立库时使用该库的连接池
            catalog_engine = create_mysql_engine(settings.mysql, database=database)
            resources.callback(catalog_engine.dispose)
            # endregion

        yield make_app(
            settings.mcp.read_token, settings.mcp.write_token, order_service=MySQLOrderQueryService(engine),
            refund_service=MySQLRefundTransactionService(engine), policy_catalog=MySQLPolicyCatalog(catalog_engine),
            open_backend=open_backend,
        )
    # endregion


# 加载应用配置并运行 MCP 服务，退出时释放业务和目录连接池。
def main() -> None:

    # region 解析启动参数并读取 JSON 配置
    parser = argparse.ArgumentParser(description="订单 MCP 服务")
    parser.parse_args()
    settings = load_config()
    mcp_config = settings.mcp
    # endregion

    # region 组装工具依赖并运行服务，退出时释放连接池
    with open_app(settings) as app:
        uvicorn.run(app, host=mcp_config.host, port=mcp_config.port, access_log=False, log_level="warning")
    # endregion


if __name__ == "__main__":
    main()
