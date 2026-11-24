"""适配免鉴权 Qwen 服务的 LangChain 请求。"""
from typing import Any

from langchain_openai import ChatOpenAI
from openai import omit

from config import QwenConfig


# 适配 Qwen 服务所需的 LangChain 请求消息格式。
class QwenChatOpenAI(ChatOpenAI):
    # 省略认证头，移除助手名称并拒绝 Qwen 不支持的工具选择方式。
    def _get_request_payload(self, *args: Any, **kwargs: Any) -> dict:

        # region 构造不带认证头的请求并适配 Qwen 原生消息格式
        payload = super()._get_request_payload(*args, **kwargs)
        headers = {
            name: value
            for name, value in (payload.get("extra_headers") or {}).items()
            if name.lower() != "authorization"
        }
        payload["extra_headers"] = {**headers, "Authorization": omit}
        choice = payload.get("tool_choice")
        if (
            # 请求以字符串指定工具选择模式。
            isinstance(choice, str)
            # 请求使用当前 Qwen 接入不支持的强制工具选择模式。
            and choice in {"required", "any"}
        ):
            raise ValueError("Qwen tool_choice required/any is not supported")
        for message in payload["messages"]:
            if message.get("role") == "assistant":
                message.pop("name", None)
        return payload
        # endregion


# 用空值提供器初始化 SDK，同时禁止从环境回退获取 API 密钥。
def _empty_api_key() -> str:

    # region 返回空凭据供免鉴权客户端使用
    return ""
    # endregion


# 使用配置创建 Qwen 聊天模型客户端，并接入指定的网络传输。
def create_qwen_client(config: QwenConfig, **transport) -> QwenChatOpenAI:

    # region 使用配置创建 Qwen 聊天模型客户端，并接入指定的网络传输
    return QwenChatOpenAI(
        model=config.model,
        base_url=config.base_url,
        api_key=_empty_api_key,
        temperature=0,
        max_completion_tokens=config.max_completion_tokens,
        max_retries=0,
        timeout=config.timeout,
        http_socket_options=(),
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        **transport,
    )
    # endregion
