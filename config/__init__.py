"""从 JSON 加载演示服务配置。"""
import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("local.json")
_config_path = DEFAULT_PATH


# 保存 MySQL 主机、端口和连接账号配置。
@dataclass(frozen=True)
class MySQLConfig:
    # 保存订单、退款模拟记录和会话数据的 MySQL 主机地址。
    host: str
    # 应用连接 MySQL 使用的 TCP 端口。
    port: int
    # 应用连接 MySQL 使用的数据库账号。
    user: str
    # 数据库账号的连接密码，从本地配置读取。
    password: str


# 保存 MCP 服务地址、监听端口和只读访问凭据。
@dataclass(frozen=True)
class MCPConfig:
    # Host 调用独立 MCP 查单和政策检索服务的地址。
    url: str
    # MCP 服务启动时监听的 TCP 端口。
    port: int
    # MCP 服务用于验证只读调用的 Bearer 凭据。
    read_token: str
    # MCP 服务的监听地址，默认只接受本机连接。
    host: str = "127.0.0.1"


# 保存 Qwen 模型服务地址和访问凭据。
@dataclass(frozen=True)
class QwenConfig:
    # Host 和调查子 Agent 调用 Qwen 的 API 基址。
    base_url: str
    # 通过模型服务认证所需的 API 密钥，从本地配置读取。
    api_key: str
    # 意图识别、计划生成和调查使用的模型标识。
    model: str = "Qwen/Qwen3-8B"
    # 单次模型请求允许生成的最大 token 数。
    max_completion_tokens: int = 512
    # 模型 HTTP 调用的超时设置，单位为秒。
    timeout: int = 90


# 保存政策检索使用的 Milvus 与模型服务连接配置。
@dataclass(frozen=True)
class PolicyConfig:
    # 保存并检索退款政策向量的 Milvus 服务地址。
    milvus_uri: str
    # 连接 Milvus 政策集合所需的访问凭据。
    milvus_token: str
    # 政策原文和查询使用的向量服务及候选重排服务的共同基址。
    model_base: str


# 汇总演示应用使用的 MySQL、MCP、Qwen 和政策检索配置。
@dataclass(frozen=True)
class AppConfig:
    # 订单、模拟退款和会话持久化使用的数据库配置。
    mysql: MySQLConfig
    # 只读业务工具的 MCP 服务连接、监听和认证配置。
    mcp: MCPConfig
    # 退款对话和调查使用的模型服务配置。
    qwen: QwenConfig
    # 政策检索和发布所需的后端配置，未提供时为 None。
    policy: PolicyConfig | None = None


# 读取并校验指定 JSON 配置，返回应用配置；错误信息不包含配置值。
def load_config(path: str | Path | None = None) -> AppConfig:
    # region 加载并校验应用配置，失败时报告文件路径
    source = Path(path) if path is not None else _config_path
    try:
        # region 读取 JSON 并构造配置对象
        data = json.loads(source.read_text(encoding="utf-8"))
        config = AppConfig(mysql=MySQLConfig(**data["mysql"]), mcp=MCPConfig(**data["mcp"]),
                           qwen=QwenConfig(**data["qwen"]),
                           policy=PolicyConfig(**data["policy"]) if "policy" in data else None)
        # endregion
        # region 校验连接字段、端口、模型限制和可选政策配置
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
        # endregion
        # region 校验 MCP 凭据后返回配置
        if len(token) < 32 or not token.isascii() or not token.isprintable() or any(c.isspace() for c in token):
            raise ValueError
        return config
        # endregion
    except (OSError, ValueError, TypeError, KeyError):
        # region 配置无法读取、解析或校验时抛出统一错误
        raise ValueError(f"无法加载配置 {source}，请检查 JSON 字段和取值。") from None
        # endregion
    # endregion


# 设置后续加载配置时使用的路径，未指定时恢复默认路径。
def use_config(path: str | Path | None) -> None:
    # region 设置后续加载配置时使用的路径，未指定时恢复默认路径
    global _config_path
    _config_path = Path(path) if path else DEFAULT_PATH
    # endregion
