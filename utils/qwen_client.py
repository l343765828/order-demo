"""Qwen native protocol compatibility for LangChain message serialization."""
from typing import Any
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from config import QwenConfig


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


# 使用配置创建 Qwen 聊天模型客户端，并接入指定的网络传输。
def create_qwen_client(config: QwenConfig, **transport) -> QwenChatOpenAI:
    return QwenChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=SecretStr(config.api_key),
        temperature=0,
        max_completion_tokens=config.max_completion_tokens,
        max_retries=0,
        timeout=config.timeout,
        http_socket_options=(),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        **transport,
    )
