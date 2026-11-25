# 模型服务配置集中化重构

## 目标与范围

将 Qwen、Embedding 和 Reranker 的连接参数集中到 `config/local.json`。Embedding 和 Reranker 各自使用完整的 HTTP API 地址，可以部署在不同服务上。以现有运行值为迁移基线，保留模型协议、正常请求和故障降级行为。

本次范围包括配置加载、模型客户端的配置传递、现有验证脚本迁移和 README 更新。回归以现有 LangGraph 流程和 checkpoint、MCP 工具契约、退款业务规则、政策版本快照、MySQL 表结构以及 Milvus 集合与索引为准。向量维度、召回和重排数量等固定约束继续使用现有常量。[Host 与 MCP 边界](../../docs/adr/0006-refund-agent-mcp-service-boundary.md) · [政策发布与检索](../../docs/adr/0010-refund-policy-publication-and-evidence.md)

## 配置契约

| 顶层配置 | 字段 | 现有运行值 |
| --- | --- | --- |
| `qwen` | `base_url`、`model`、`max_completion_tokens`、`timeout` | `http://192.168.18.53:30080/qwen3/v1`、`Qwen/Qwen3-8B`、512 token、90 秒 |
| `embedding` | `url`、`model`、`timeout` | `http://192.168.18.53:30080/bge/v1/embeddings`、`BAAI/bge-m3`、30 秒 |
| `reranker` | `url`、`model`、`timeout` | `http://192.168.18.53:30080/reranker/v1/rerank`、`BAAI/bge-reranker-v2-m3`、30 秒 |

- 三个模型配置使用现有的 Pydantic `StrictModel`，保留根配置 `AppConfig` 和其他配置类。模型配置块内的字段均显式填写。地址须为有效 HTTP 或 HTTPS URL，模型名去掉首尾空白后须有内容，模型请求超时和 Qwen 最大生成 token 数须为正整数。
- 模型配置块拒绝未知字段。加载时继续忽略旧 `qwen.api_key`，Qwen 请求沿用现有免鉴权行为。配置加载错误保持脱敏。
- 只查单可使用精简配置，保留 `mysql`、`mcp` 和 `qwen`，省略 `policy`、`embedding` 和 `reranker`。提供 `policy` 时，`embedding` 和 `reranker` 均必填。凡提供模型配置块，均按相同规则校验。
- 删除并拒收旧 `policy.model_base`，同时移除 Python 中的模型地址、名称和请求超时默认值及废弃常量。随应用发布的 `config/local.json` 配齐三种模型，沿用现有文件路径和 Git 管理方式。使用旧结构的独立配置随代码迁移。
- 同一套政策发布目录和 Milvus 集合使用同一份部署配置。现有向量继续由原 Embedding 模型查询，将来更换向量模型时另行安排重编码和索引迁移。

## 施工顺序

1. **锁定配置行为。** 在 `test/test_config.py` 覆盖完整配置、只查单精简配置、启用政策时缺少任一模型块、非法或缺失字段、未知字段、旧字段迁移和错误脱敏。更新 `config/__init__.py` 和 `config/local.json`，让新契约通过这些测试。迁移直接构造 Qwen 配置或生成旧 JSON 的现有测试夹具。
2. **接入启动时读取的配置。** Host 主模型和调查子 Agent 共用启动时读取的 Qwen 配置。MCP 服务和 `publish_policy.py` 各自从入口将已读配置传给 `PolicyBackend`，沿用 `publish_policy.py --config`。两个政策模型按各自配置发起请求。沿用 HTTP 和 Milvus 客户端的创建、释放及代理设置，以现有请求字段、响应校验和降级结果作为回归基线。
3. **迁移依赖和文档。** 检查全仓旧字段、共享地址拼接、旧超时常量及直接构造后端的调用方。更新 `test/verify_policy_faults.py`、`test/verify_policy_cli.py` 和 `test/verify_refund_submission.py` 等现有验证入口，让代理按模型转发至各自的完整地址，保留超时、无效向量、HTTP 失败等原有断言。更新简体中文 README，写明完整与精简配置、旧文件迁移和重启生效规则。

## 验收

- 本地配置与调用测试使用两个不同的假服务地址，核对完整 URL、模型名、超时和请求体。验证向量维度错误及重排索引错误沿用现有处理，空候选跳过重排，政策后端故障返回既有结果。
- 验证进程启动后修改 JSON 时，现有入口仍使用启动时读到的模型配置，重新启动后使用新值。同时验证只查单配置可以启动并查单。
- 从项目根目录运行配置、Qwen 客户端和启动相关回归，再运行完整测试套件。

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider test/test_config.py test/test_qwen_client.py test/test_mysql_core.py
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider test
```

- 内网可用时，用隔离的 MySQL 库和 Milvus 集合验证政策发布、向量写入、召回、重排、通用退款政策咨询、指定订单资格咨询、政策补查及失败降级。沿用现有脚本和选项，将实际验收记录放入 `evidence/`。

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_catalog.py --config "<验收配置路径>" --evidence evidence/model-config-policy-catalog.jsonl
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_cli.py --config "<验收配置路径>"
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_faults.py --config "<验收配置路径>"
```

- 实施交付时列出变更文件、最终 JSON 结构和配置类、旧字段及硬编码移除范围、调用入口检查结果、测试命令与结果。逐项标记因环境不可用而未执行的集成验证。部署时配套更新代码与运行 JSON，再重启相应进程。
