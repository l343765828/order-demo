"""Real Qwen compatibility probe; no business tools or credentials are persisted."""
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.qwen_client import QwenChatOpenAI
from config import load_config

class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(description="Short label")
    count: int = Field(ge=0, le=10)

@tool
def probe_echo(value: str) -> str:
    """Return the given probe value unchanged."""
    return value

def main():
    endpoint = load_config().qwen.base_url.rstrip("/")
    key = load_config().qwen.api_key
    model = QwenChatOpenAI(
        model="Qwen/Qwen3-8B", base_url=endpoint, api_key=SecretStr(key),
        temperature=0, max_completion_tokens=512, max_retries=0, timeout=90,
        http_socket_options=(), extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    message = HumanMessage(content="Call probe_echo with value 'blue'.")
    first = model.bind_tools([probe_echo], tool_choice="auto").invoke([message])
    print("tool_calls:", first.tool_calls, flush=True)
    assert len(first.tool_calls) == 1
    call = first.tool_calls[0]
    assert call["name"] == "probe_echo" and call["args"] == {"value": "blue"} and call["id"]
    observation = ToolMessage(content=probe_echo.invoke(call["args"]), tool_call_id=call["id"])
    second = model.bind_tools([probe_echo], tool_choice="none").invoke([message, first, observation])
    print("observation_id:", observation.tool_call_id, "answer:", second.content, flush=True)
    assert second.content and not second.tool_calls
    named = model.bind_tools([probe_echo], tool_choice="probe_echo").invoke([message])
    named_call, = named.tool_calls
    assert named_call["name"] == "probe_echo" and named_call["args"] == {"value": "blue"} and named_call["id"]
    print("named_tool_id:", named_call["id"])
    for choice in ("required", "any"):
        try:
            model.bind_tools([probe_echo], tool_choice=choice).invoke([message])
        except ValueError as exc:
            assert "not supported" in str(exc)
            print(f"{choice}: rejected by client")
        else:
            raise AssertionError(f"{choice} was not rejected")

    result = model.with_structured_output(ProbeResult, method="json_schema", strict=True).invoke(
        "Return label blue and count 3."
    )
    print("schema:", result, flush=True)
    assert result == ProbeResult(label="blue", count=3)
    for bad_result in ({"label": "blue", "count": 3, "extra": True}, {"label": "blue", "count": "3"}):
        try:
            ProbeResult.model_validate(bad_result)
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid named result passed")
    print("named_schema_rejects_extra_and_wrong_type")

    base = {
        "model": "Qwen/Qwen3-8B", "messages": [{"role": "user", "content": message.content}],
        "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
    }
    with httpx.Client(timeout=90, trust_env=False, headers={"Authorization": f"Bearer {key}"}) as http:
        for label, patch, expected in (
            ("extra_request_field", {"unrecognised_probe_field": True}, 400),
            ("missing_named_tool", {
                "tools": [convert_to_openai_tool(probe_echo)],
                "tool_choice": {"type": "function", "function": {"name": "missing"}},
            }, 400),
            ("truncated_schema_result", {
                "max_completion_tokens": 1,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "ProbeResult", "strict": True, "schema": ProbeResult.model_json_schema(),
                }},
            }, 422),
        ):
            response = http.post(f"{endpoint}/chat/completions", json={**base, **patch})
            assert response.status_code == expected, (label, response.status_code)
            print(f"{label}: HTTP {response.status_code}")

if __name__ == "__main__":
    main()

