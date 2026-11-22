import sys
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import SecretStr
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.qwen_client import QwenChatOpenAI

def test_agent_name_is_removed_and_unsupported_choices_fail():
    model = QwenChatOpenAI(model="Qwen/Qwen3-8B", api_key=SecretStr("test"))
    messages = [HumanMessage(content="hello"), AIMessage(content="ok", name="worker")]
    payload = model._get_request_payload(messages)
    assert payload["messages"][1] == {"role": "assistant", "content": "ok"}
    for choice in ("required", "any"):
        with pytest.raises(ValueError, match="not supported"):
            model._get_request_payload(messages, tool_choice=choice)
