"""Qwen native protocol compatibility for LangChain message serialization."""
from typing import Any
from langchain_openai import ChatOpenAI

# 适配 Qwen 服务所需的 LangChain 请求消息格式。
class QwenChatOpenAI(ChatOpenAI):
    # 移除助手消息中不兼容的名称字段，并拒绝未支持的工具选择方式。
    def _get_request_payload(self, *args: Any, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(*args, **kwargs)
        choice = payload.get("tool_choice")
        if isinstance(choice, str) and choice in {"required", "any"}:
            raise ValueError("Qwen tool_choice required/any is not supported")
        for message in payload["messages"]:
            if message.get("role") == "assistant":
                message.pop("name", None)
        return payload
