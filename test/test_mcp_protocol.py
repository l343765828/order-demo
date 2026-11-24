"""Cross-process acceptance tests for the MCP client boundary."""
import asyncio
import json
from threading import get_ident
import uuid

import httpx2
import pytest
from mcp import Client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from starlette.testclient import TestClient

from protocol_probe_contracts import EchoInput, EchoResult
from verify_mcp_protocol import bind_echo, probe_process
from config.demo import CONSUMER_ID
from mcp_service import server
from implementations.policy import backend
from model.orders.query import GetOrderRequest, OrderDetails, OrderQueryResult
from model.orders.status import OrderQueryStatus, PaymentStatus, RefundStatus, ShippingStatus
from model.policy.search import PolicyCandidate, PolicySearchResult
from model.policy.status import PolicyEntryType, PolicySearchMode, PolicySearchStatus
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
                    args = EchoInput(value=fault, trace_id=trace)
                    if high_level:
                        result = await client.call_tool("probe_echo", args.model_dump(mode="json"))
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


def test_discovery_allows_schema_variations(tmp_path):
    asyncio.run(check_discovery(tmp_path))


async def check_discovery(tmp_path):
    log = tmp_path / "schema.jsonl"
    for schema_fault in ("input", "output"):
        with probe_process(log, schema_fault=schema_fault) as (url, _):
            async with Client(streamable_http_client(url)) as client:
                tool = await bind_echo(client)
                assert (await tool.call(EchoInput(value="blue", trace_id="trace"))).value == "blue"
                if schema_fault == "output":
                    assert (await tool.call(EchoInput(value="zero-pid", trace_id="trace"))).server_pid == 0


def test_decorated_tools_keep_the_service_contract(monkeypatch, capsys):
    calls = []
    trace = str(uuid.uuid4())
    policy_event = str(uuid.uuid4())
    details = OrderDetails(order_id="DEMO-1001", payment_status=PaymentStatus.PAID,
                           shipping_status=ShippingStatus.UNSHIPPED, refund_status=RefundStatus.NOT_REFUNDED,
                           paid_amount_fen=19900, currency="CNY")
    candidate = PolicyCandidate(document_id="refund", title="Refund policy", version="1.0", clause_id="1",
                                entry_type=PolicyEntryType.POLICY, text="Policy text")

    class OrderService:
        def query_order(self, request, *, authenticated_consumer_id):
            assert get_ident() != http_request_thread
            calls.append((request, authenticated_consumer_id))
            return OrderQueryResult(status=OrderQueryStatus.FOUND, order=details)

    class Backend:
        def close(self):
            calls.append("closed")

    policy_backend = Backend()
    policy_catalog = object()

    def search_policy(query, mode, connection, *, catalog, catalog_revision):
        assert connection is policy_backend
        assert catalog is policy_catalog
        calls.append((query, mode, catalog_revision))
        return PolicySearchResult(status=PolicySearchStatus.OK, event_id=policy_event, candidates=[candidate],
                                  catalog_revision=catalog_revision)

    monkeypatch.setattr(server.search, "search_policy", search_policy)
    monkeypatch.setattr(backend, "PolicyBackend", lambda: policy_backend)
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}

    from interfaces.refund.storage import RefundTransactionService
    from model.refund.status import RefundLookupStatus, RefundOutcome, RefundReason
    from model.refund.submission import GetRefundResultRequest, RefundLookupResult, RefundResult, SubmitMockRefundRequest

    class RefundService(RefundTransactionService):
        def lookup_refund(self, request, *, authenticated_consumer_id):
            assert get_ident() != http_request_thread
            calls.append((request, authenticated_consumer_id))
            return RefundLookupResult(status=RefundLookupStatus.NOT_FOUND)

        def submit_refund(self, request, *, authenticated_consumer_id):
            assert get_ident() != http_request_thread
            calls.append((request, authenticated_consumer_id))
            return RefundResult(status=RefundOutcome.DENIED, reason=RefundReason.INVALID_DISPATCH)

    app = server.make_app("test-token", "write-token", order_service=OrderService(), refund_service=RefundService(),
                          policy_catalog=policy_catalog)
    http_request_thread = None

    async def observe_asgi_thread(scope, receive, send):
        nonlocal http_request_thread
        if scope['type'] == 'http':
            http_request_thread = get_ident()
        await app(scope, receive, send)

    with TestClient(observe_asgi_thread, base_url="http://127.0.0.1:8000", headers=headers) as http:
        assert http.post("/mcp", json={}).status_code == 401
        http.headers["Authorization"] = "Bearer test-token"

        def request(method, params, request_id=1):
            response = http.post("/mcp", json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            response.raise_for_status()
            return response.json()["result"]

        request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "1"}})
        tools = {tool["name"]: tool for tool in request("tools/list", {})["tools"]}
        assert set(tools) == {"get_order", "search_policy", "get_refund_result", "submit_mock_refund"}
        assert set(tools["get_order"]["inputSchema"]["properties"]) == {"order_id"}
        policy = tools["search_policy"]
        assert set(policy["inputSchema"]["properties"]) == {"query", "mode", "catalog_revision"}
        assert set(policy["inputSchema"]["required"]) == {"query", "mode"}
        assert "catalog_revision" in policy["outputSchema"]["properties"]
        assert {name for name, tool in tools.items() if tool["annotations"]["readOnlyHint"]} == {
            "get_order", "search_policy", "get_refund_result",
        }
        assert set(tools["submit_mock_refund"]["inputSchema"]["properties"]) == {"order_id", "idempotency_key"}
        assert set(tools["get_refund_result"]["inputSchema"]["properties"]) == {"order_id", "idempotency_key"}
        assert set(tools["submit_mock_refund"]["outputSchema"]["properties"]) == {"status", "refund", "reason"}
        assert set(tools["get_refund_result"]["outputSchema"]["properties"]) == {"status", "refund"}
        assert "outputSchema" not in tools["get_order"]

        for name, arguments in (
            ("get_order", {"order_id": 1001}),
            ("get_order", {}),
            ("search_policy", {"query": "refund", "mode": "invalid"}),
            ("search_policy", {"query": "refund", "mode": "formal", "catalog_revision": "7"}),
            ("unknown", {}),
        ):
            assert request("tools/call", {"name": name, "arguments": arguments}) == {"isError": True, "content": []}
        rejected = request("tools/call", {"name": "submit_mock_refund", "arguments": {
            "order_id": "DEMO-1001", "idempotency_key": "unauthorized",
        }})
        assert rejected["isError"] is True
        assert calls == []

        order = request("tools/call", {
            "name": "get_order",
            "arguments": {"order_id": "DEMO-1001", "consumer_id": "DEMO-USER-02"},
            "_meta": {"trace_id": trace},
        }, request_id=2)
        policy = request("tools/call", {
            "name": "search_policy",
            "arguments": {"query": "refund", "mode": "formal", "catalog_revision": 7, "extra": True},
        }, request_id=3)
        order_result = OrderQueryResult.from_mcp(order["structuredContent"])
        assert order_result.status is OrderQueryStatus.FOUND and order_result.order == details
        assert order_result.order.payment_status is PaymentStatus.PAID
        policy_result = PolicySearchResult.from_mcp(policy["structuredContent"])
        assert policy_result.event_id == policy_event and policy_result.candidates == [candidate]
        assert policy_result.catalog_revision == 7
        assert policy_result.candidates[0].entry_type is PolicyEntryType.POLICY
        key = "verification-key"
        tool_event = str(uuid.uuid4())
        arguments = {"order_id": "DEMO-1001", "idempotency_key": key, "consumer_id": "DEMO-USER-02"}
        meta = {"trace_id": trace, "tool_event_id": tool_event}
        lookup = request("tools/call", {"name": "get_refund_result", "arguments": arguments, "_meta": meta}, 4)
        assert RefundLookupResult.model_validate(lookup["structuredContent"]).status is RefundLookupStatus.NOT_FOUND
        http.headers["Authorization"] = "Bearer write-token"
        submission = request("tools/call", {"name": "submit_mock_refund", "arguments": arguments, "_meta": meta}, 5)
        assert RefundResult.model_validate(submission["structuredContent"]).reason is RefundReason.INVALID_DISPATCH

    assert calls == [
        (GetOrderRequest(order_id="DEMO-1001"), CONSUMER_ID), ("refund", PolicySearchMode.FORMAL, 7), "closed",
        (GetRefundResultRequest(order_id="DEMO-1001", idempotency_key=key), CONSUMER_ID),
        (SubmitMockRefundRequest(order_id="DEMO-1001", idempotency_key=key), CONSUMER_ID),
    ]
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    assert [event["reason"] for event in events if event["event"] == "mcp_tool_rejected"] == [
        "bad_arguments", "bad_arguments", "unknown_tool", "write_forbidden",
    ]
    order_event, = [event for event in events if event["event"] == "mcp_get_order"]
    assert order_event["trace_id"] == trace and order_event["mcp_request_id"] == 2
    for event_name, request_id in [("mcp_get_refund_result", 4), ("mcp_submit_mock_refund", 5)]:
        event, = [event for event in events if event["event"] == event_name]
        assert event["trace_id"] == trace and event["tool_event_id"] == tool_event
        assert (event["mcp_request_id"], event["order_id"], event["idempotency_key"]) == (request_id, "DEMO-1001", key)
