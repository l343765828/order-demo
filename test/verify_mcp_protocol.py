"""Start a dedicated MCP server process and verify a normal round trip."""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from protocol_probe_contracts import EchoInput, EchoResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.mcp_client import bind_mcp_tool, controlled_mcp_http


@contextmanager
def probe_process(log: Path, *, schema_fault: str = "") -> Iterator[tuple[str, int]]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("protocol_probe_server.py")),
         "--port", str(port), "--log", str(log), "--schema-fault", schema_fault],
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"probe server exited with code {process.returncode}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("probe server did not start")
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/mcp", process.pid
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        # On Windows the venv launcher can exit before the real server releases its port.
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    pass
            except OSError:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("probe server still accepts connections after termination")
            time.sleep(0.05)


def bind_echo(client: Client):
    return bind_mcp_tool(client, "probe_echo", EchoResult)


async def run(url: str, launcher_pid: int) -> None:
    trace = str(uuid.uuid4())
    async with controlled_mcp_http() as http, Client(
        streamable_http_client(url, http_client=http), read_timeout_seconds=10,
    ) as client:
        tool = await bind_echo(client)
        data = await tool.call(EchoInput(value="blue", trace_id=trace))
        assert (data.value, data.trace_id) == ("blue", trace) and data.server_pid != os.getpid()
        print(
            f"host_pid={os.getpid()} server_pid={data.server_pid} launcher_pid={launcher_pid} "
            f"trace_id={trace} mcp_request_id={data.request_id} structured_result=valid"
        )
    assert http.is_closed
    print("client_context_exited")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as folder:
        with probe_process(Path(folder) / "wire.jsonl") as (url, launcher_pid):
            asyncio.run(run(url, launcher_pid))
