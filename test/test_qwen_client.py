import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import QwenConfig
from utils.qwen_client import create_qwen_client

SETTINGS = QwenConfig(base_url="http://qwen.test/v1", model="qwen-fixture", max_completion_tokens=73, timeout=19)

def test_agent_name_is_removed_and_unsupported_choices_fail():
    model = create_qwen_client(SETTINGS)
    messages = [HumanMessage(content="hello"), AIMessage(content="ok", name="worker")]
    payload = model._get_request_payload(messages)
    assert payload["messages"][1] == {"role": "assistant", "content": "ok"}
    for choice in ("required", "any"):
        with pytest.raises(ValueError, match="not supported"):
            model._get_request_payload(messages, tool_choice=choice)


@pytest.mark.parametrize("use_async", [False, True])
def test_qwen_sends_no_authorization_header(monkeypatch, use_async):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-sent")
    settings = SETTINGS

    def respond(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "qwen-fixture" and body["max_completion_tokens"] == 73
        assert request.extensions["timeout"]["read"] == 19
        assert "authorization" not in request.headers
        assert request.headers["x-probe"] == "preserved"
        return httpx.Response(200, json={
            "id": "chat-probe", "object": "chat.completion", "created": 0, "model": settings.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        })

    transport = httpx.MockTransport(respond)
    headers = {"Authorization": "Bearer unused", "authorization": "Bearer unused-alias", "x-probe": "preserved"}

    async def request_async():
        async with httpx.AsyncClient(transport=transport) as http:
            return await create_qwen_client(settings, http_async_client=http).ainvoke("Reply OK", extra_headers=headers)

    if use_async:
        result = asyncio.run(request_async())
    else:
        with httpx.Client(transport=transport) as http:
            result = create_qwen_client(settings, http_client=http).invoke("Reply OK", extra_headers=headers)
    assert result.content == "OK"
