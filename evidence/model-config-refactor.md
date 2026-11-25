# 模型服务配置集中化重构验收

验收日期为 2026-11-25，环境为 Windows、Python 3.11.7。连接目标数据库后先执行 `SELECT VERSION()`，实测为 **MySQL 8.0.46**。政策目录联验从 SDK 读取到 **Milvus 3.0.2**。真实验收使用工作区外的临时 JSON、随机命名的 MySQL 库和独立 Milvus 集合。

## 配置及入口

| 顶层块 | 配置类 | 字段和部署值 |
| --- | --- | --- |
| `qwen` | `QwenConfig` | `base_url` 为 `http://192.168.18.53:30080/qwen3/v1`，`model` 为 `Qwen/Qwen3-8B`，`max_completion_tokens` 为 512，`timeout` 为 90 秒。 |
| `embedding` | `EmbeddingConfig` | `url` 为 `http://192.168.18.53:30080/bge/v1/embeddings`，`model` 为 `BAAI/bge-m3`，`timeout` 为 30 秒。 |
| `reranker` | `RerankerConfig` | `url` 为 `http://192.168.18.53:30080/reranker/v1/rerank`，`model` 为 `BAAI/bge-reranker-v2-m3`，`timeout` 为 30 秒。 |
| `mysql`、`mcp` | `MySQLConfig`、`MCPConfig` | 沿用原有字段和配置类。 |
| `policy` | `PolicyConfig` | `milvus_uri`、`milvus_token`，可选 `catalog_database`、`milvus_collection`。 |
| `refund_policy_events_dir` | `AppConfig` 的可选字段 | 政策核验事件目录。 |

三种模型配置使用 `StrictModel`。只查单时可仅保留 `mysql`、`mcp` 和 `qwen`。旧 `qwen.api_key` 在加载时被忽略。删除了 `policy.model_base`、Python 里的默认模型名及超时，以及政策后端的共享地址与路径拼接。部署时同步更新代码和运行 JSON，再重启 Host 和 MCP 服务。

- `config/__init__.py`、`config/local.json`、`config/limits.py` 实现新契约。`README.md` 记录完整和精简配置、迁移及重启步骤。发布副本使用凭据占位值。
- `cli.py`、`implementations/refund/host.py`、`implementations/refund/investigation.py` 共用一次读取的 Qwen 配置。
- `mcp_service/server.py` 和 `publish_policy.py` 将入口读取的配置传给 `implementations/policy/backend.py`。发布入口保留 `--config`。向量编码与重排各用自己的 URL、模型名和超时。
- `test/test_config.py`、`test/test_qwen_client.py`、`test/test_mysql_core.py`、`test/test_policy.py`、`test/test_mcp_protocol.py` 覆盖配置、请求及入口回归。
- `test/order_mysql_fixture.py`、`test/verify_conversation_mysql.py`、`test/verify_deepagent_protocol.py`、`test/verify_model_config_restart.py`、`test/verify_mysql_service_regressions.py`、`test/verify_policy_catalog.py`、`test/verify_policy_cli.py`、`test/verify_policy_faults.py`、`test/verify_policy_store.py`、`test/verify_qwen_endpoint.py`、`test/verify_refund_currency.py`、`test/verify_refund_submission.py` 使用新契约。

## 本地回归

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider test/test_config.py test/test_qwen_client.py test/test_mysql_core.py
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider test
.\.venv\Scripts\python.exe -B -m mypy --follow-imports=silent --disable-error-code=import-untyped config/__init__.py implementations/policy/backend.py implementations/refund/host.py implementations/refund/investigation.py mcp_service/server.py cli.py publish_policy.py
```

配置专项回归 50 项通过，全量回归 180 项通过，mypy 检查 7 个生产模块通过。mypy 跳过第三方缺少类型声明的导入。两个假服务使用不同主机、路径、模型名称和超时，核对了完整 URL 和请求体。无效维度、无效重排索引或 HTTP 失败时返回失败和空候选，空召回跳过重排。Qwen 同步和异步请求均未发送认证头。CLI 重排探针另用两个独立回环地址验证了代理保留上游完整路径及查询参数。

## 真实服务验收

JSONL 先写到临时目录，检查脱敏后归档为下列路径。

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_catalog.py --config "<验收配置路径>" --evidence evidence/model-config-policy-catalog.jsonl
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_cli.py --config "<验收配置路径>" --evidence evidence/model-config-policy-cli.jsonl
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_cli.py --config "<验收配置路径>" --smoke --evidence evidence/model-config-policy-cli-smoke.jsonl
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_cli.py --config "<验收配置路径>" --faults-only --evidence evidence/model-config-policy-cli-faults.jsonl
.\.venv\Scripts\python.exe -X utf8 test/verify_policy_faults.py --config "<验收配置路径>"
.\.venv\Scripts\python.exe -X utf8 test/verify_order_cli.py --config "<验收配置路径>"
.\.venv\Scripts\python.exe -X utf8 test/verify_model_config_restart.py --config "<验收配置路径>"
```

- MCP 故障与重启验收退出码为 0。启动后将两个模型地址从故障代理改回真实端点。旧进程继续使用启动配置，无效向量只触发 Embedding，重排 HTTP 503 和超时各触发一次 Embedding 和 Reranker，三个场景均返回 `failed` 和空候选。重启后查询返回真实重排候选，代理计数保持原值。隔离库和集合均已清理。
- 本人查单验收退出码为 0。Host PID 167236 通过独立 MCP PID 173304 查询本人订单，取得实付 19900 分。校验了恢复会话、无权限查询、MCP 不可用降级及零退款写入。隔离库已清理。
- Host 重启验收退出码为 0。运行 JSON 仅含 `mysql`、`mcp`、`qwen`。改写 JSON 后，旧 Host PID 176196 仍通过原地址查询，重启后的 PID 73252 使用新地址。两次分别记录 6 个真实 Qwen 请求，均覆盖结构化主模型请求和调查子 Agent 的查单工具请求，均省略认证头并返回实付 19900 分。隔离库已清理。
- 政策 CLI 默认命令退出码为 1。到账通用问题引用 FAQ-004，指定订单资格咨询引用 RF-003 或 RF-004。跨进程事件回执可按事件 ID 对齐，两个正式补查均沿用了首轮目录修订号 1。其中「钱什么时候回到我的账户？」补查后仍无足够依据，未返回用例期待的 FAQ-004。条件退款请求进入订单与正式政策调查，答复为退款服务不可用且未发送请求，未逐字包含核验引文，触发 `capture()` 中的原有断言，后续 CLI 故障子场景未执行。故障脚本独立完成了真实后端降级验证。隔离会话、检查点、库和集合均已清理。
- 政策 CLI 的 `--smoke` 退出码为 0。通用到账咨询引用 FAQ-004，指定订单 DEMO-1001 的资格咨询引用 RF-003。两轮均完成 Host 与独立 MCP 的回执和出处核验，隔离会话、检查点、库和集合均已清理。
- 政策 CLI 的 `--faults-only` 退出码为 0。四项分别覆盖无关 FAQ、重排 HTTP 503、重排超时及故障期间继续查单。两次重排故障均返回空候选，Host 提示暂时无法核实政策。查单返回实付 19900 分，隔离会话、检查点、库和集合均已清理。
- 政策目录脚本最终退出码为 0。真实发布入口激活了两份文档，完成向量写入、回读、召回和重排。独立 MCP 正常检索，并在 Milvus 不可用时返回失败及空候选。同一个 Host 在发布新版本后，进行中的正式补查仍读取修订号 2，后续调查读取修订号 3，两个版本均完成引文核验。写入不完整、回读失败、提交回执丢失、并发发布冲突和资格变更保护均通过，最终活动修订号为 6。隔离订单库、目录库和 Milvus 集合均已清理。

首次并行验收期间出现 Python 子进程 `MemoryError`。降低并行度后，配置专项、全量回归、Host 重启及 MCP 重启均通过。政策目录脚本首次等候 Host 答复超过 120 秒，验收脚本的整轮等待预算调至 300 秒。复跑通过 Host 答复和目录版本固定检查，并发发布探针暴露一个后端子类仍用旧构造签名。补齐配置参数后，完整重跑通过。

本轮迁移了退款提交验证脚本并检查了语法，独立退款提交全链路未纳入本规格的真实服务验收。真实服务均可连接。

## 2026-11-25 后续处理

- 开发库使用 MySQL 8.0.46，政策集合使用 Milvus 3.0.2。项目 `config/local.json` 的工作副本已实测连接两项服务。仓库外开发配置已补齐独立的 MCP 写入凭据和可写的政策核验事件目录。用该配置启动独立 MCP 进程，写入身份可发现退款工具，读取身份可查到实付 19900 分。此项仅验证配置，退款提交使用隔离数据验收。
- 条件退款在政策核验通过但执行服务不可用时，按确认规则只回复「退款服务暂时不可用，未提交退款。」读取 MySQL 实测版本后，用真实 Qwen、向量检索、重排和独立 MCP 复验。内部政策依据与本轮回执一致，消费者回复未附引文，订单实付正常回读，退款与提交记录数不变，写调用为零。隔离资源已清理。[只读条件退款证据](model-config-policy-cli-read-only-refund.jsonl)
- 到账同义问法第一次检索已召回 FAQ-004 并排在首位，旧选答提示返回空索引，随后正式条款补查排除了 FAQ。调整选答提示以理解退款语境下的口语表达。用同一份真实回执回放，调整前 3 次均未选中 FAQ-004，调整后 3 次均选中并通过引用核验。
- 默认政策 CLI 复跑退出码为 0，共完成 16 轮。两种到账问法都通过一次检索引用 FAQ-004，条件退款回复符合确认文案，无关咨询与重排故障仍正常降级，退款写调用为零。首次复跑在汇总时遇到 MySQL 断连，已清理该隔离库。验收脚本改用新连接执行收尾校验和清理，并通过主动断开原隔离连接的测试。最终复跑确认隔离数据库和 Milvus 集合均已清理。[默认 CLI 复跑证据](model-config-policy-cli-followup.jsonl)
- 退款主链路用 `--group business --smoke` 真实复跑，退出码为 0。独立 MCP 收到一次退款写调用，MySQL 保存一条关联退款记录及 19900 分金额，CLI 显示 ¥199.00，最终政策核验事件及原文出处通过对账。隔离库和集合已清理。[退款主链路证据](model-config-refund-submission.jsonl)
- 扩展的 `--group all` 在 `event_ack` 场景等待 CLI 子进程超过 300 秒，脚本退出并确认清理隔离库和集合。超时前已通过独立 MCP 幂等验证、四项订单防护、金额和渠道验证、重排故障、无候选、政策冲突、伪造引用和政策事件写入前失败等场景。随后用相同隔离入口和真实后端单独注入 `event_ack`，确认政策事件已入盘但回执丢失时，退款、提交和 MCP 写调用均为零。其余扩展用例本轮未执行。
- 全量 pytest 181 项通过，mypy 检查 89 个生产源文件通过。

```powershell
$devConfig = Join-Path $env:LOCALAPPDATA 'agent-order-demo/development.json'
.venv/Scripts/python.exe -X utf8 test/verify_policy_cli.py --config $devConfig --read-only-refund --evidence evidence/model-config-policy-cli-read-only-refund.jsonl
.venv/Scripts/python.exe -X utf8 test/verify_policy_cli.py --config $devConfig --evidence evidence/model-config-policy-cli-followup.jsonl
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config $devConfig --group business --smoke --evidence evidence/model-config-refund-submission.jsonl
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
```

### 再次复核

- 从初次失败的默认 CLI 记录取出「钱什么时候回到我的账户？」的首份真实 MCP 回执，使用同一问题、候选顺序和真实 Qwen 做对照。原选答提示返回 `source_index=null`，当前提示返回 0，核验通过并引用 FAQ-004，本轮未触发正式补查。初次失败发生在 Host 的语义选答阶段。
- 运行 `.venv/Scripts/python.exe -B -X utf8 -m pytest -q -p no:cacheprovider --tb=short test`，181 项通过。
- 默认政策 CLI 再次退出 0，完成 16 轮。两种到账问法均在首次检索后引用 FAQ-004。只读条件退款的正文仅为「退款服务暂时不可用，未提交退款。」结构化结果为 `execution_unavailable`，退款与提交记录数均与运行前一致，MCP 退款写调用为零。[本次默认 CLI 记录](model-config-policy-cli-recheck.jsonl)
- 模拟退款的业务主链路再次退出 0。独立 MCP 收到一次退款提交调用，MySQL 写入一条成功提交和一条金额为 19900 分的模拟退款记录，CLI 显示 ¥199.00。核对了最终政策核验事件、引文和幂等键。两项操作均在首次连接 MySQL 后执行 `SELECT VERSION()`，实测 8.0.46。Milvus 实测 3.0.2，验收后两个隔离库及集合均已清理。[本次模拟退款记录](model-config-refund-submission-recheck.jsonl)

两次联验的 JSONL 从临时目录通过凭据检查后归档。复现命令如下。

```powershell
$devConfig = Join-Path $env:LOCALAPPDATA 'agent-order-demo/development.json'
.venv/Scripts/python.exe -B -X utf8 test/verify_policy_cli.py --config $devConfig --evidence evidence/model-config-policy-cli-recheck.jsonl
.venv/Scripts/python.exe -B -X utf8 test/verify_refund_submission.py --config $devConfig --group business --smoke --evidence evidence/model-config-refund-submission-recheck.jsonl
```
