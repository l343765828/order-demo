"""从 JSON 加载演示服务配置。"""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("local.json")
_config_path = DEFAULT_PATH


# 保存 MySQL 主机、端口和连接账号配置。
@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str


# 保存 MCP 服务地址、监听端口和只读访问凭据。
@dataclass(frozen=True)
class MCPConfig:
    url: str
    port: int
    read_token: str
    host: str = "127.0.0.1"


# 保存 Qwen 模型服务地址和访问凭据。
@dataclass(frozen=True)
class QwenConfig:
    base_url: str
    api_key: str
    model: str = "Qwen/Qwen3-8B"
    max_completion_tokens: int = 512
    timeout: int = 90


# 保存政策检索使用的 Milvus 与模型服务连接配置。
@dataclass(frozen=True)
class PolicyConfig:
    milvus_uri: str
    milvus_token: str
    model_base: str


# 汇总演示应用使用的 MySQL、MCP、Qwen 和政策检索配置。
@dataclass(frozen=True)
class AppConfig:
    mysql: MySQLConfig
    mcp: MCPConfig
    qwen: QwenConfig
    policy: PolicyConfig | None = None


# 读取并校验指定 JSON 配置，返回应用配置；错误信息不包含配置值。
def load_config(path: str | Path | None = None) -> AppConfig:
    source = Path(path) if path is not None else _config_path
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        config = AppConfig(mysql=MySQLConfig(**data["mysql"]), mcp=MCPConfig(**data["mcp"]),
                           qwen=QwenConfig(**data["qwen"]),
                           policy=PolicyConfig(**data["policy"]) if "policy" in data else None)
        if (not all(isinstance(value, str) and value for value in (
                config.mysql.host, config.mysql.user, config.mysql.password, config.mcp.url,
                config.mcp.read_token, config.mcp.host, config.qwen.base_url,
                config.qwen.api_key, config.qwen.model))
                or type(config.mysql.port) is not int or not 1 <= config.mysql.port <= 65535
                or type(config.mcp.port) is not int or not 1 <= config.mcp.port <= 65535
                or type(config.qwen.max_completion_tokens) is not int or config.qwen.max_completion_tokens < 1
                or type(config.qwen.timeout) is not int or config.qwen.timeout < 1):
            raise ValueError
        if config.policy is not None and not all(isinstance(value, str) and value.strip() for value in (
                config.policy.milvus_uri, config.policy.milvus_token, config.policy.model_base)):
            raise ValueError
        token = config.mcp.read_token
        if len(token) < 32 or not token.isascii() or not token.isprintable() or any(c.isspace() for c in token):
            raise ValueError
        return config
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError(f"无法加载配置 {source}，请检查 JSON 字段和取值。") from None


# 设置后续加载配置时使用的路径，未指定时恢复默认路径。
def use_config(path: str | Path | None) -> None:
    global _config_path
    _config_path = Path(path) if path else DEFAULT_PATH
