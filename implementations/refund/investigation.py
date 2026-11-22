"""DeepAgent 调查执行器及受限的订单与政策子 Agent。"""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import Context
from typing import cast, overload
from uuid import uuid4

import httpx
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call, wrap_tool_call
from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from mcp.types import RequestParamsMeta
from pydantic import ValidationError

from config import load_config
from config.limits import ConnectionLimits
from utils.mcp_client import bind_mcp_tool, open_mcp_client
from model.refund.flow import InvestigationStep, OrderToolEvent, PolicyStep, PolicyToolEvent
from model.policy.search import PolicyQuery, PolicySearchResult, SearchPolicyRequest
from model.orders.query import GetOrderRequest, OrderQueryResult
from model.policy.status import PolicySearchMode
from model.refund.tool_name import McpToolName
from utils.qwen_client import create_qwen_client

FetchOrder = Callable[[GetOrderRequest, str], Awaitable[OrderQueryResult]]


FetchPolicy = Callable[[PolicyQuery, str], Awaitable[PolicySearchResult]]


# 声明订单调查的输入和真实工具事件类型。
@overload
async def investigate_order(model: BaseChatModel, step: InvestigationStep, trace_id: str,
                            fetch: FetchOrder, emit: Callable[..., None]) -> OrderToolEvent | None: ...


# 声明政策调查的输入和真实工具事件类型。
@overload
async def investigate_order(model: BaseChatModel, step: PolicyStep, trace_id: str,
                            fetch: FetchPolicy, emit: Callable[..., None]) -> PolicyToolEvent | None: ...


# 委派当前步骤对应的只读子 Agent，并仅返回已回传给模型的真实工具事件。
async def investigate_order(
    model: BaseChatModel, step: InvestigationStep | PolicyStep, trace_id: str,
    fetch: FetchOrder | FetchPolicy, emit: Callable[..., None],
) -> OrderToolEvent | PolicyToolEvent | None:
    # region 按订单或政策步骤固定子 Agent、工具和参数，初始化本次调用额度
    attempted_call = False
    delegation_id: str | None = None
    evidence: OrderToolEvent | PolicyToolEvent | None = None
    observation_returned = False
    is_order = isinstance(step, InvestigationStep)
    agent = 'order-agent' if is_order else 'policy-agent'
    name = (McpToolName.GET_ORDER if is_order else McpToolName.SEARCH_POLICY).value
    expected = (GetOrderRequest(order_id=step.order_id) if isinstance(step, InvestigationStep)
                else PolicyQuery(query=step.query))
    task = f"Call {name} exactly once with these exact arguments: {expected.model_dump_json()}."
    # endregion

    # 记录被拒绝的工具调用，并生成关联原调用编号的错误消息。
    def reject(request, reason: str) -> ToolMessage:
        emit("agent_tool_rejected", reason=reason)
        return ToolMessage(content="Tool call disallowed.", tool_call_id=request.tool_call["id"], status="error")

    # 调用已绑定的 MCP 工具，保存请求追踪及结构化观察。
    async def invoke_tool(args: GetOrderRequest | PolicyQuery, runtime: ToolRuntime) -> str:
        # region 关联本轮 MCP 调用和真实结果，调用异常时返回服务不可用
        nonlocal evidence
        assert runtime.tool_call_id
        event_id = str(uuid4())
        emit("react_call", tool=name, model_tool_call_id=runtime.tool_call_id, tool_event_id=event_id)
        try:
            if isinstance(args, GetOrderRequest):
                result = await cast(FetchOrder, fetch)(args, event_id)
                evidence = OrderToolEvent(event_id=event_id, trace_id=trace_id, order_id=args.order_id,
                                          model_tool_call_id=runtime.tool_call_id, result=result)
            else:
                policy = await cast(FetchPolicy, fetch)(args, event_id)
                evidence = PolicyToolEvent(event_id=event_id, trace_id=trace_id, query=args.query,
                                           model_tool_call_id=runtime.tool_call_id, result=policy)
        except Exception as exc:
            emit("mcp_call_failed", error_type=type(exc).__name__, tool_event_id=event_id)
            return "Service unavailable."
        emit("mcp_observation", tool_event_id=event_id, result=evidence.result.model_dump(mode="json"))
        return evidence.result.model_dump_json()
        # endregion

    # 提供当前消费者的精确订单查询。
    @tool(McpToolName.GET_ORDER.value)
    async def get_order(order_id: str, runtime: ToolRuntime) -> str:
        """Get the authenticated consumer's order by its exact order ID."""
        return await invoke_tool(GetOrderRequest(order_id=order_id), runtime)

    # 提供政策查询，检索范围由 Host 选择。
    @tool(McpToolName.SEARCH_POLICY.value)
    async def search_policy(query: str, runtime: ToolRuntime) -> str:
        """Retrieve ranked refund policy candidates using the exact supplied query."""
        return await invoke_tool(PolicyQuery(query=query), runtime)

    # 在转发前核对工具、参数与预算，平行调用也只能发送一次。
    @wrap_tool_call
    async def check_tool_call(request, handler):
        # region 只转发工具和参数都匹配当前步骤的首次调用
        nonlocal attempted_call
        call = request.tool_call
        try:
            args = type(expected).model_validate(call["args"])
        except ValidationError:
            return reject(request, "bad_arguments")
        if call["name"] != name or args != expected:
            return reject(request, "wrong_tool_or_order")
        if attempted_call:
            return reject(request, "tool_budget")
        attempted_call = True
        return await handler(request)
        # endregion

    # 核对回传模型的工具消息是否与实际调用编号及结果一致。
    @wrap_model_call
    async def verify_observation(request, handler):
        nonlocal observation_returned
        # region 已收到工具结果时，按调用编号和正文核验结果确实回传给模型
        if evidence is not None and not observation_returned:
            for msg in request.messages:
                if isinstance(msg, ToolMessage) and msg.tool_call_id == evidence.model_tool_call_id:
                    if msg.content == evidence.result.model_dump_json():
                        observation_returned = True
                        emit("react_observation", model_tool_call_id=evidence.model_tool_call_id,
                             tool_event_id=evidence.event_id)
                    break
        # endregion
        return await handler(request)

    # 限制监督 Agent 的可见工具，只允许它发起子任务委派。
    @wrap_model_call
    async def delegate_only(request, handler):
        return await handler(request.override(tools=[t for t in request.tools if t.name == "task"]))

    # 只允许委派给当前步骤对应的子 Agent，并使用 Host 校验过的任务描述。
    @wrap_tool_call
    async def check_delegation(request, handler):
        # region 只允许首次委派对应的子 Agent，并由 Host 提供已校验的任务描述
        nonlocal delegation_id
        call = request.tool_call
        if call["name"] != "task" or call["args"].get("subagent_type") != agent:
            return reject(request, "wrong_delegate")
        if delegation_id is not None:
            return reject(request, "delegation_budget")
        delegation_id = call["id"]
        emit("deepagent_delegate", delegation_id=delegation_id, agent=agent)
        # 子任务只接收已校验步骤，监督模型的任意文字不进入任务描述。
        return await handler(request.override(tool_call={
            **call, "args": {"description": task, "subagent_type": agent},
        }))
        # endregion

    # region 以受限的子 Agent 和委派工具运行本次只读调查
    worker = create_agent(
        model, tools=[get_order if is_order else search_policy],
        system_prompt=f"You are {agent}. Call {name} with the specified exact arguments once. After the tool returns, reply only DONE.",
        middleware=[check_tool_call, verify_observation],
    )
    supervisor = create_deep_agent(
        model=model, backend=StateBackend(),
        system_prompt=f"Delegate to {agent} using task exactly once. After it returns, reply only DONE.",
        middleware=[delegate_only, check_delegation],
        subagents=[
            {"name": agent, "description": f"Uses the read-only {name} tool.", "runnable": worker},
            {"name": "general-purpose", "description": "Unavailable for order investigations.",
             "runnable": create_agent(model)},
        ],
    )
    result = await supervisor.ainvoke(
        {"messages": [{"role": "user", "content": f"Delegate to {agent} with this task: {task}"}]},
        {"recursion_limit": 20},
    )
    # endregion

    # region 委派成功且真实结果已回传时接收观察，否则拒绝采用模型回复
    if delegation_id and observation_returned and any(
        isinstance(msg, ToolMessage) and msg.tool_call_id == delegation_id and msg.status != "error"
        for msg in result["messages"]
    ):
        return evidence
    emit("agent_observation_rejected")
    return None
    # endregion


# 建立 MCP 和模型连接，执行当前只读调查步骤并返回观察。
def execute_order(
    step: InvestigationStep | PolicyStep, trace_id: str, emit: Callable[..., None],
    *, mode: PolicySearchMode = PolicySearchMode.CONSULTATION,
) -> OrderToolEvent | PolicyToolEvent | None:
    # 在异步连接生命周期内绑定当前步骤需要的 MCP 工具。
    async def run():
        mcp = load_config().mcp
        read_timeout = ConnectionLimits.POLICY_SESSION_SECONDS if isinstance(step, PolicyStep) else ConnectionLimits.ORDER_CALL_SECONDS
        async with httpx.AsyncClient(trust_env=False) as http, open_mcp_client(
            mcp.url, token=mcp.read_token, read_timeout_seconds=read_timeout,
        ) as client:
            # region 政策步骤绑定 Host 指定的检索范围并关联检索事件
            if isinstance(step, PolicyStep):
                policy_tool = await bind_mcp_tool(client, McpToolName.SEARCH_POLICY.value, PolicySearchResult)

                # 绑定 Host 选择的范围并关联服务端检索事件。
                async def fetch_policy(request: PolicyQuery, event_id: str) -> PolicySearchResult:
                    return await policy_tool.call(SearchPolicyRequest(query=request.query, mode=mode),
                        timeout=ConnectionLimits.POLICY_CALL_SECONDS, meta=cast(RequestParamsMeta, {
                            "trace_id": trace_id, "tool_event_id": event_id,
                        }))

                return await investigate_order(create_qwen_client(load_config().qwen, http_async_client=http), step, trace_id, fetch_policy, emit)
            # endregion

            # region 查单步骤绑定只读查单工具并关联订单读取事件
            bound = await bind_mcp_tool(client, McpToolName.GET_ORDER.value, OrderQueryResult)

            # 携带追踪和工具事件标识，调用 MCP 查单并返回结构化结果。
            async def fetch(request: GetOrderRequest, event_id: str) -> OrderQueryResult:
                return await bound.call(
                    request, timeout=ConnectionLimits.ORDER_CALL_SECONDS,
                    meta=cast(RequestParamsMeta, {"trace_id": trace_id, "tool_event_id": event_id}),
                )

            return await investigate_order(create_qwen_client(load_config().qwen, http_async_client=http), step, trace_id, fetch, emit)
            # endregion

    # region 隔离异步调查上下文，异常时只记录错误类型并返回无观察
    try:
        # 异步 Agent 不继承外层图的同步 checkpoint 和线程配置。
        return Context().run(asyncio.run, run())
    except Exception as exc:
        cause: BaseException = exc
        while isinstance(cause, BaseExceptionGroup) and len(cause.exceptions) == 1:
            cause = cause.exceptions[0]
        emit("investigation_failed", error_type=type(cause).__name__)
        return None
    # endregion
