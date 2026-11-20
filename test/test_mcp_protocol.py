"""Cross-process acceptance tests for the MCP client boundary."""
import asyncio
import json
import uuid

import httpx2
import pytest
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from protocol_probe_contracts import EchoResult
from verify_mcp_protocol import bind_echo, probe_process
from utils.mcp_client import controlled_mcp_http


@pytest.mark.parametrize(
    ("fault", "expected", "resends"),
    [
        ("delay-probe", -32001, False),
        ("response-lost", types.CONNECTION_CLOSED, False),
        ("disconnect", None, False),
        ("redirect", types.INVALID_REQUEST, True),
        ("bad-header", types.HEADER_MISMATCH, True),
        ("input-required", RuntimeError, True),
        ("bad-result", RuntimeError, False),
    ],
)
def test_faults(fault, expected, resends, tmp_path):
    asyncio.run(check_fault(fault, expected, resends, tmp_path))


async def check_fault(fault, expected, resends, tmp_path):
    # The high-level control must resend in the two scenarios selected by the SDK.
    for high_level in ([False, True] if resends else [False]):
        trace = str(uuid.uuid4())
        log = tmp_path / f"{fault}-{high_level}.jsonl"
        header_faults: list[str] = []

        async def corrupt_first_header(request):
            header = "mcp-param-trace-id"
            if fault == "bad-header" and header in request.headers and not header_faults:
                header_faults.append(request.headers[header])
                request.headers[header] = "incorrect"

        with probe_process(log) as (url, _):
            transport = httpx2.AsyncClient(trust_env=False) if high_level else controlled_mcp_http()
            async with transport as http:
                http.event_hooks["request"].append(corrupt_first_header)
                client = Client(streamable_http_client(url, http_client=http), read_timeout_seconds=5)
                async with client:
                    tool = await bind_echo(client)
                    assert client.protocol_version == "2026-07-28"
                    args = {"value": fault, "trace_id": trace}
                    if high_level:
                        result = await client.call_tool("probe_echo", args)
                        assert EchoResult.model_validate(result.structured_content).trace_id == trace
                    elif isinstance(expected, type):
                        with pytest.raises(expected, match="input|output|structured|schema"):
                            await tool.call(args)
                    elif isinstance(expected, int):
                        with pytest.raises(MCPError) as error:
                            await tool.call(args, timeout=0.5 if fault == "delay-probe" else 5)
                        assert error.value.code == expected
                    else:
                        assert (await tool.call(args)).trace_id == trace
                with pytest.raises(RuntimeError, match="async context"):
                    _ = client.session
            assert http.is_closed
            async with controlled_mcp_http() as http, Client(
                streamable_http_client(url, http_client=http), read_timeout_seconds=5,
            ) as fresh:
                await bind_echo(fresh)

            events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            calls = [e for e in events if e["method"] == "tools/call"]
            resumes = [e for e in events if e["method"] == "GET" and e.get("last_event_id") == trace]
            executions = [e for e in events if e["method"] == "executed" and e["trace_id"] == trace]
            assert len(calls) == (2 if high_level else 1)
            assert all(e["trace_id"] == trace for e in calls)
            assert len(resumes) == (1 if fault == "disconnect" else 0)
            rejected_calls = 1 if fault == "bad-header" else 0
            assert len(executions) == len(calls) - rejected_calls
            if fault == "bad-header":
                assert header_faults == [trace]
                assert [e["trace_header"] for e in calls] == (
                    ["incorrect", trace] if high_level else ["incorrect"]
                )
            if fault == "input-required":
                assert [e["request_state"] for e in calls] == (
                    [None, trace] if high_level else [None]
                )
            print(
                f"{fault} high_level={high_level} trace={trace} POST={len(calls)} "
                f"resume_GET={len(resumes)} executed={len(executions)}"
            )


def test_incompatible_discovery_and_invalid_arguments(tmp_path):
    asyncio.run(check_schema(tmp_path))


async def check_schema(tmp_path):
    log = tmp_path / "schema.jsonl"
    for schema_fault in ("input", "output"):
        with probe_process(log, schema_fault=schema_fault) as (url, _):
            async with Client(streamable_http_client(url)) as client:
                with pytest.raises(ValueError, match=f"{schema_fault} schema"):
                    await bind_echo(client)
    with probe_process(log) as (url, _):
        async with Client(streamable_http_client(url)) as client:
            tool = await bind_echo(client)
            for args in (
                {"value": 1, "trace_id": "trace"},
                {"value": "blue", "trace_id": "trace", "extra": True},
                {"value": "blue"},
            ):
                with pytest.raises(ValidationError):
                    await tool.call(args)
    assert '"method": "tools/call"' not in log.read_text(encoding="utf-8")
