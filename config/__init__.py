"""从 JSON 加载演示服务配置。"""
import json
from dataclasses import dataclass
from pathlib import Path

from config.demo import DEMO_DATABASE
from config.limits import ConnectionLimits, PolicyLimits

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
    # 订单、会话和检查点所在库，隔离验收可指向专用库。
    database: str = DEMO_DATABASE


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
    # 固定执行节点和 MCP Server 共用的独立凭据。为空时保持只读运行。
    write_token: str | None = None


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
    # 政策目录所在的 MySQL 数据库，隔离验收时与独立集合配套配置。
    catalog_database: str = PolicyLimits.CATALOG_DATABASE
    # 政策索引集合，与上述目录配套管理。
    milvus_collection: str = PolicyLimits.MILVUS_COLLECTION_NAME


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
    # Host 保存最终政策事件的目录。为空时暂停新写入，原结果仍可查询。
    refund_policy_events_dir: str | None = None


# 校验 MCP 凭据为足够长且可安全放入 HTTP 请求头的字符串。
def _valid_mcp_token(token: object) -> bool:
    # region 核对长度、ASCII 编码及可见字符
    return isinstance(token, str) and len(token) >= ConnectionLimits.MCP_TOKEN_MIN_CHARS and all(
        "!" <= character <= "~" for character in token
    )
    # endregion


# 读取并校验指定 JSON 配置，返回应用配置；错误信息不包含配置值。
def load_config(path: str | Path | None = None) -> AppConfig:
    # region 加载并校验应用配置，失败时报告文件路径
    source = Path(path) if path is not None else _config_path
    try:
        # region 读取 JSON 并构造配置对象
        data = json.loads(source.read_text(encoding="utf-8"))
        config = AppConfig(mysql=MySQLConfig(**data["mysql"]), mcp=MCPConfig(**data["mcp"]),
                           qwen=QwenConfig(**data["qwen"]),
                           policy=PolicyConfig(**data["policy"]) if "policy" in data else None,
                           refund_policy_events_dir=data.get("refund_policy_events_dir"))
        # endregion
        # region 校验连接字段、端口、模型限制和可选政策配置
        if (
            # 必填连接、凭据或模型字段中有值不是非空字符串。
            not all(
                (
                    # 连接配置字段使用字符串。
                    isinstance(value, str)
                    # 必填字段已填写。
                    and value
                )
                for value in (
                    config.mysql.host, config.mysql.user, config.mysql.password, config.mcp.url,
                    config.mysql.database, config.mcp.read_token, config.mcp.host, config.qwen.base_url,
                    config.qwen.api_key, config.qwen.model,
                )
            )
            # MySQL 端口未配置为整数。
            or type(config.mysql.port) is not int
            # MySQL 端口不在有效范围内。
            or not 1 <= config.mysql.port <= 65535
            # MCP 服务端口未配置为整数。
            or type(config.mcp.port) is not int
            # MCP 服务端口不在有效范围内。
            or not 1 <= config.mcp.port <= 65535
            # 模型的最大输出 token 数未配置为整数。
            or type(config.qwen.max_completion_tokens) is not int
            # 模型的最大输出 token 数小于 1。
            or config.qwen.max_completion_tokens < 1
            # 模型请求超时未配置为整数。
            or type(config.qwen.timeout) is not int
            # 模型请求超时小于 1 秒。
            or config.qwen.timeout < 1
        ):
            raise ValueError
        if (
            # 已提供可选的政策检索配置。
            config.policy is not None
            # 政策向量库地址、凭据或模型服务地址中有值不是有效文本。
            and not all(
                (
                    # 政策检索连接字段使用字符串。
                    isinstance(value, str)
                    # 字段去掉首尾空白后仍有内容。
                    and value.strip()
                )
                for value in (config.policy.milvus_uri, config.policy.milvus_token, config.policy.model_base,
                              config.policy.catalog_database, config.policy.milvus_collection)
            )
        ):
            raise ValueError
        # endregion
        # region 校验 MCP 凭据后返回配置
        if (
            # 只读凭据格式不适合 HTTP 认证。
            not _valid_mcp_token(config.mcp.read_token)
            # 已启用执行身份但凭据不合规或与只读凭据相同。
            or (
                # 配置启用了 MCP 受控执行。
                config.mcp.write_token is not None
                # 两个身份未形成独立的有效凭据。
                and (not _valid_mcp_token(config.mcp.write_token) or config.mcp.write_token == config.mcp.read_token)
            )
            # 配置提供了无效的政策事件目录。
            or (
                # Host 配置了关键事件的保存目录。
                config.refund_policy_events_dir is not None
                # 目录值不是可用的非空路径文本。
                and (not isinstance(config.refund_policy_events_dir, str) or not config.refund_policy_events_dir.strip())
            )
        ):
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
