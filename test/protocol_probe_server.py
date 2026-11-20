"""Loopback MCP server with wire counting and reversible response faults."""

import asyncio
import argparse
import json
import os
from pathlib import Path

import uvicorn
from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from protocol_probe_contracts import EchoInput, EchoResult


def make_app(log: Path, schema_fault: str):
    tool = types.Tool(
        name="probe_echo",
        input_schema=EchoInput.model_json_schema(),
        output_schema=EchoResult.model_json_schema(),
    )
    if schema_fault == "input":
        tool.input_schema["additionalProperties"] = True
    if schema_fault == "output":
        assert tool.output_schema is not None
        tool.output_schema["properties"]["server_pid"]["minimum"] = 0

    def record(method: str, **data) -> None:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"method": method, **data}) + "\n")

    async def list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[tool])

    async def echo(ctx: ServerRequestContext, params: types.CallToolRequestParams):
        args = EchoInput.model_validate(params.arguments)
        record("executed", trace_id=args.trace_id, request_id=ctx.request_id)
        if args.value == "delay-probe":
            await asyncio.sleep(2)
        if args.value == "input-required" and params.request_state is None:
            return types.InputRequiredResult(request_state=args.trace_id)
        observation = EchoResult(
            value=args.value, trace_id=args.trace_id,
            server_pid=os.getpid(), request_id=str(ctx.request_id),
        ).model_dump()
        if args.value == "bad-result":
            observation["server_pid"] = "not a process ID"
        return types.CallToolResult(content=[], structured_content=observation)

    server = Server(
        "order-demo-protocol-probe",
        on_list_tools=list_tools,
        on_call_tool=echo,
        get_tool_input_schema=lambda name: tool.input_schema if name == tool.name else None,
    )
    mcp = server.streamable_http_app(json_response=True, stateless_http=True)
    receipts: dict[str, bytes] = {}

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await mcp(scope, receive, send)
            return
        request = Request(scope, receive)
        if request.method == "GET" and (event_id := request.headers.get("last-event-id")):
            record("GET", last_event_id=event_id)
            response = Response(
                b"event: message\ndata: " + receipts.pop(event_id) + b"\n\n",
                media_type="text/event-stream",
            )
            await response(scope, receive, send)
            return
        if request.method != "POST":
            await mcp(scope, receive, send)
            return

        body = await request.body()
        message = json.loads(body)
        method = message.get("method")
        params = message.get("params") or {}
        args = params.get("arguments") or {}
        trace_id = args.get("trace_id")
        if method == "tools/call":
            record(
                method, trace_id=trace_id, request_id=message["id"],
                trace_header=request.headers.get("mcp-param-trace-id"),
                request_state=params.get("requestState"),
            )
        else:
            record(method)

        received = False

        async def forward_receive() -> Message:
            nonlocal received
            if received:
                return await receive()
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        fault = args.get("value") if method == "tools/call" else None
        drop_reply = fault in ("disconnect", "response-lost")
        redirect = fault == "redirect" and "redirected" not in request.query_params
        if not drop_reply and not redirect:
            await mcp(scope, forward_receive, send)
            return

        # Let the real MCP handler execute, then withhold its JSON-RPC response.
        reply = bytearray()

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.body":
                reply.extend(message.get("body", b""))

        await mcp(scope, forward_receive, capture)
        if redirect:
            await Response(status_code=307, headers={"location": "/mcp?redirected=1"})(scope, receive, send)
            return
        if fault == "response-lost":
            await Response(b": reply withheld\n\n", media_type="text/event-stream")(scope, receive, send)
            return

        assert isinstance(trace_id, str)
        receipts[trace_id] = bytes(reply)
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        await send({
            "type": "http.response.body", "more_body": True,
            "body": f"id: {trace_id}\nretry: 10\ndata:\n\n".encode(),
        })
        # Uvicorn closes the TCP connection with an incomplete HTTP response.
        raise ConnectionError("probe disconnected after sending the SSE event ID")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--schema-fault", default="")
    args = parser.parse_args()
    uvicorn.run(
        make_app(args.log, args.schema_fault),
        host="127.0.0.1", port=args.port, log_level="critical",
    )
