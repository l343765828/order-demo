"""Prove a real DeepAgent delegation, MCP call and matching Qwen tool observation."""

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

import httpx
from deepagents import create_deep_agent
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from protocol_probe_contracts import EchoResult
from verify_mcp_protocol import bind_echo, probe_process
from utils.mcp_client import controlled_mcp_http
from qwen_client import QwenChatOpenAI
from config import load_config


async def run(url: str, log: Path) -> None:
    trace_id = str(uuid.uuid4())
    observations: dict[str, EchoResult] = {}
    returned_ids: set[str] = set()

    async def inspect_request(request: httpx.Request) -> None:
        messages = json.loads(request.content)["messages"]
        for message in messages:
            call_id = message.get("tool_call_id")
            if message["role"] == "tool" and call_id in observations:
                observation = observations[call_id]
                assert EchoResult.model_validate_json(message["content"]) == observation
                assert any(
                    call["id"] == call_id and call["function"]["name"] == "probe_echo"
                    for assistant in messages if assistant["role"] == "assistant"
                    for call in assistant.get("tool_calls", [])
                )
                returned_ids.add(call_id)

    mcp_http = controlled_mcp_http()
    client = Client(streamable_http_client(url, http_client=mcp_http), read_timeout_seconds=20)
    async with mcp_http, client, httpx.AsyncClient(
        event_hooks={"request": [inspect_request]}, trust_env=False,
    ) as http:
        echo = await bind_echo(client)

        @tool
        async def probe_echo(value: str, runtime: ToolRuntime) -> str:
            """Echo a value through the independent probe server."""
            call_id = runtime.tool_call_id
            assert call_id
            data = await echo.call({"value": value, "trace_id": trace_id})
            assert (data.value, data.trace_id) == (value, trace_id)
            assert data.server_pid != os.getpid()
            observations[call_id] = data
            return data.model_dump_json()

        model = QwenChatOpenAI(
            http_async_client=http,
            model="Qwen/Qwen3-8B",
            base_url=load_config().qwen.base_url,
            api_key=SecretStr(load_config().qwen.api_key),
            temperature=0,
            max_completion_tokens=512,
            max_retries=0,
            timeout=120,
            http_socket_options=(),
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        agent = create_deep_agent(
            model=model,
            system_prompt="Delegate the user request to echo-worker using task. Return the worker's answer.",
            subagents=[{
                "name": "echo-worker",
                "description": "Calls probe_echo for echo requests.",
                "system_prompt": "You must call probe_echo with the requested value and report its actual result.",
                "tools": [probe_echo],
            }],
        )
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": "Delegate to echo-worker. Use probe_echo to echo the word blue, then report the result."}]},
            {"recursion_limit": 35},
        )
        messages = result["messages"]
        delegation, = (
            call for message in messages if isinstance(message, AIMessage)
            for call in message.tool_calls if call["name"] == "task"
        )
        assert delegation["args"]["subagent_type"] == "echo-worker"
        assert any(
            isinstance(message, ToolMessage) and message.tool_call_id == delegation["id"]
            for message in messages
        )
        final = messages[-1]
        assert isinstance(final, AIMessage) and not final.tool_calls and "blue" in str(final.content).lower()
        call_id, = observations
        assert returned_ids == {call_id}
        data = observations[call_id]
        assert data.value == "blue"
        requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        receipt, = (request for request in requests if request["method"] == "tools/call")
        assert receipt["trace_id"] == trace_id and str(receipt["request_id"]) == data.request_id
        print(json.dumps({
            "host_pid": os.getpid(), "protocol": client.protocol_version,
            "delegation_id": delegation["id"], "model_tool_call_id": call_id,
            "mcp_observation": data.model_dump(), "tools_call_posts": 1,
            "qwen_observation_id": call_id, "final_reply": final.content,
        }, ensure_ascii=False))

    try:
        _ = client.session
    except RuntimeError:
        print("client_context_exited")
    else:
        raise AssertionError("MCP session remained available after context exit")
    assert http.is_closed and mcp_http.is_closed


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "wire.jsonl"
        with probe_process(log) as (url, _):
            asyncio.run(run(url, log))
