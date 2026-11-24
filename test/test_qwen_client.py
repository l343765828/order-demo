import asyncio
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import QwenConfig
from utils.qwen_client import create_qwen_client

def test_agent_name_is_removed_and_unsupported_choices_fail():
    model = create_qwen_client(QwenConfig(base_url="http://qwen.test/v1"))
    messages = [HumanMessage(content="hello"), AIMessage(content="ok", name="worker")]
    payload = model._get_request_payload(messages)
    assert payload["messages"][1] == {"role": "assistant", "content": "ok"}
    for choice in ("required", "any"):
        with pytest.raises(ValueError, match="not supported"):
            model._get_request_payload(messages, tool_choice=choice)


@pytest.mark.parametrize("use_async", [False, True])
def test_qwen_sends_no_authorization_header(monkeypatch, use_async):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-sent")
    settings = QwenConfig(base_url="http://qwen.test/v1")

    def respond(request):
        assert request.url.path == "/v1/chat/completions"
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
