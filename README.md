# order-demo 的 MySQL 存储

## 项目文档

- [需求与总体设计](.scratch/refund-agent-all-tech-demo/spec.md)涵盖业务范围、流程、异常处理和验收标准。
- [施工总览](.scratch/refund-agent-all-tech-demo/task-overview.md)列出各工单、施工顺序、依赖和验收目标。[模型接口实施计划](.scratch/refund-agent-all-tech-demo/interface-implementation-plan.md)记录 Qwen 原生工具与 Schema 接口的实施步骤。
- 专题设计包括[数据库](.scratch/refund-agent-all-tech-demo/database-design.md)、[存储与记忆](.scratch/refund-agent-all-tech-demo/storage-memory.md)、[上下文压缩](.scratch/refund-agent-all-tech-demo/context-compression.md)、[MCP 接入与部署](.scratch/refund-agent-all-tech-demo/mcp-integration.md)、[模型选型](.scratch/refund-agent-all-tech-demo/model-selection.md)、[接口契约](.scratch/refund-agent-all-tech-demo/integration-contracts.md)和[订单号提取](.scratch/refund-agent-all-tech-demo/order-id-extraction-research.md)。
- [领域术语](GLOSSARY.md)、[架构决策](docs/adr/)和[当前退款政策原文](policy/refund-policy-1.1.md)记录已确认的约定。[MySQL 访问层重构](.scratch/mysql-access-refactor/spec.md)说明业务连接和事务边界。[验收记录](evidence/)保存已执行的验证及结果。

当前退款流程支持查单、只读资格咨询补号、按需政策解释、明确授权的模拟退款和结果核对。资格判断和开发环境切换见 [ADR-0012](docs/adr/0012-refund-policy-for-explanation.md)。历史设计和验收记录用于追溯，当前运行方式见下文。

## 环境配置与启动

以下命令在包含 `cli.py` 的项目目录执行。运行 `py -3 -m pip install -r requirements-protocol.txt` 安装依赖，其中包含 SQLAlchemy Core 2.x 与 PyMySQL。在 `config/local.json` 中配置 MySQL、MCP 和模型服务。仓库中的凭据为占位值，部署时在运行副本中填入实际值，并将该副本保存在版本控制之外。MCP 凭据须为至少 32 个可见 ASCII 字符。

Host 和 MCP 服务各自在进程启动时读取 JSON。部署时配套更新程序和 JSON，再重启相应进程。政策发布命令每次启动时读取配置，可用 `--config` 指定文件。

| 配置块 | 必填字段 | 用途 |
| --- | --- | --- |
| `qwen` | `base_url`、`model`、`max_completion_tokens`、`timeout` | Host 主模型与调查子 Agent 使用同一配置。请求省略 `Authorization` 请求头。 |
| `embedding` | `url`、`model`、`timeout` | 政策条目和查询编码。填写完整 HTTP API 地址，例如 `http://embedding.example/bge/v1/embeddings`。 |
| `reranker` | `url`、`model`、`timeout` | 政策候选重排。填写完整 HTTP API 地址，例如 `http://ranking.example/reranker/v1/rerank`。 |

启用政策咨询和解释时，在 `mysql`、`mcp`、`qwen` 之外填写 `policy`、`embedding` 和 `reranker`。查单、资格判断和模拟退款可使用只保留前三个配置块的配置。模型地址须为有效的 HTTP 或 HTTPS URL，模型名须为非空文本，`timeout` 为正整数秒，`max_completion_tokens` 为正整数 token 数。

CLI 和 MCP 服务各自在进程内复用业务连接池，进程退出时释放。数据库连接使用 UTC 和 `utf8mb4`。主库由 `mysql.database` 指定，默认使用 `order_demo`。`manage.py init`、政策目录初始化和 LangGraph 检查点通过独立 PyMySQL 连接管理。

先初始化数据库，再装载演示种子：

```powershell
py -3 manage.py init
py -3 manage.py seed
```

两条命令都可以重复执行，并保留已有数据。初始化会创建缺失的数据库对象。种子装载在一个事务内写入缺失的订单，仅当本次成功插入 `DEMO-1003` 时才写入配套模拟退款。任一写入失败时，本次装载回滚。

实付和退款金额分别使用 `paid_amount_fen` 和 `amount_fen`，单位为人民币分。

### 重置开发数据

停止使用目标开发库的 Agent 和 MCP 服务，确认在途写入已结束，再用 `reset` 重置已经按当前表结构初始化的退款演示库。运行 `reset` 时，必须用 `--config` 指定开发配置文件：

```powershell
py -3 manage.py --config "$env:LOCALAPPDATA\agent-order-demo\development.json" reset
```

命令使用配置文件中 `mysql.database` 指定的数据库，在一个事务中清空退款演示的订单、退款、提交记录、会话原文、摘要、长期偏好和图检查点，再写入五笔种子订单及一笔配套模拟退款。写入失败时，事务回滚。重置后继续使用当前发布的政策目录和 Milvus 索引。

### 切换到 RF-013 新版

1. 确认目标开发库符合当前表结构，记录程序版本、现用政策目录修订、Milvus 集合和旧事件目录。发现旧表结构时暂停切换，先确认处理方式。
2. 停止使用该库的 Agent 和 MCP 服务，确认在途写入结束。执行上述 `reset`，旧的 `pending`、`unconfirmed` 测试提交一并清空。
3. 在目标运行配置的 `refund_policy_events_dir` 中清理旧流程生成的政策核验 JSONL。按正文识别 `refund_policy_verified` 事件和对应的幂等键文件名。配置未设置目录时检查目标旧运行配置，仓库 `evidence/` 独立保留。
4. 在同一停流窗口内使用下文的发布入口启用政策 `1.1`。核对目录的生效版本、原文和条款，确认重置保留原有发布目录、Milvus 数据与其他演示数据。确认程序和政策配套后重新启动服务，用新会话验收查单、只读补号、办理与未决退款防重。

运行 `py -3 -X utf8 test/verify_mysql.py --config <验收配置>` 进行隔离集成验证。验收配置为工作区外的 JSON，须提供有权创建和清理测试库的 MySQL 账号。脚本先查询实例版本，再创建和清理随机命名的测试库，验证初始化、种子幂等性、事务回滚、开发数据重置和进程连接池。

首次运行前初始化 checkpoint 表，然后在独立终端启动 MCP 服务：

```powershell
py -3 cli.py --init-checkpoints
py -3 -m mcp_service.server
```

在同一目录打开另一个终端，运行 `py -3 cli.py`，输入「查一下 DEMO-1001」。MCP 服务代码位于 `mcp_service/`，本人查单服务位于 `implementations/orders/`，查询 SQL 位于 `repositories/sql/orders/`。订单查询契约位于 `model/orders/`，退款政策模型位于 `model/policy/`，退款流程模型位于 `model/refund/`。

指定了独立开发配置时，验收的 checkpoint、MCP 和 CLI 也使用同一文件。在各自终端运行对应命令，完成切换后新启 CLI 会话。

```powershell
$developmentConfig = "$env:LOCALAPPDATA\agent-order-demo\development.json"
py -3 test/launch_with_config.py cli $developmentConfig --init-checkpoints
py -3 test/launch_with_config.py mcp_service.server $developmentConfig
py -3 test/launch_with_config.py cli $developmentConfig
```

## 长期偏好

CLI 支持用聊天管理订单状态答复的详略。输入「请记住以后简短回答」保存偏好，「查看我的长期偏好」查看当前值，「请把我的长期偏好改为详细回答」覆盖旧值，「请删除我的答复详略偏好」删除。偏好随消费者跨会话使用，删除后仍可通过 `--history` 查看原始聊天。

输入「查一下 DEMO-1001，这次请详细回答」可以只改变当次排版。简短和详细答复均包含订单号、支付状态、发货状态、模拟退款状态和实付金额。

## 退款政策发布与检索

当前演示政策原件为 [1.1 版](policy/refund-policy-1.1.md)，[1.0 版](policy/refund-policy.md)保留供历史版本核对。在 `policy` 中填写 `milvus_uri` 和 `milvus_token`。Milvus 使用账号密码认证时，令牌格式为 `用户名:密码`。MCP 服务和发布命令对同一份政策发布目录和 Milvus 集合使用同一套部署配置。隔离验收可配套指定 `catalog_database` 和 `milvus_collection`，默认使用 `order_demo` 和 `order_demo_refund_policy`。现存向量继续使用原 Embedding 模型查询。更换 Embedding 模型时需另行重编码并迁移索引。

首次启用目录时，先初始化演示库，再将演示政策 `1.1` 发布到配套集合，随后启动 MCP 服务。`1.1` 修订 RF-013 的政策使用方式，订单资格条件保持一致。

```powershell
py -3 publish_policy.py --config "$env:LOCALAPPDATA\agent-order-demo\development.json" --source policy/refund-policy-1.1.md --version 1.1 --publisher demo-maintainer --qualification unchanged
```

发布其他文档或版本时，传入对应 Markdown 原件及其明确版本号。首行填写文档标题，标头使用 `文档编号：`、`版本：`。正式条款以 `## RF-003 — 条款标题` 开头，FAQ 以 `## FAQ-001 — 问题` 开头，后续正文为独立答案。命令完成 Milvus 强一致回读与检索验证后才切换 MySQL 目录。发布者用 `--qualification changed` 声明改变资格的版本，此类版本登记后须等待同版本结构化规则。

切换回执不明确时，重跑同一发布命令，命令先核对版本的发布记录。首次生效前可修正原件并用同版本全量重发。若上一轮 Milvus 写入结果不明，确认上一发布进程已停止且服务器端写入已结束后加 `--confirm-write-ended`。已生效版本的更新与删除通过发布新版本完成。

`search_policy` 首次成功时返回目录修订号，即使候选为空。Host 首次咨询使用 `consultation`，必要时沿用同一修订号，用 `formal` 补查一次。检索失败返回 `failed` 和空候选。

运行 `py -3 test/verify_policy_mcp.py` 可通过独立 MCP 进程验证认证及真实检索。运行 `py -3 test/verify_policy_catalog.py --config <验收配置> --evidence evidence/policy-16-integration.jsonl` 可在真实后端创建随机命名的目录和集合，验证版本切换、故障重试与 Host 读取。脚本退出时清理其创建的目录、集合、隔离会话和检查点。

在 CLI 输入「退款多久能到账？」可咨询通用政策，输入「DEMO-1001 能退吗？」可查询本人订单资格。Host 运行时从 MCP 回执取政策正文和出处，无需政策 Markdown。通用咨询可引用适用的 FAQ 或正式条款。本人订单资格由代码根据实际订单状态计算，明确拒绝后可检索适用条款用于解释。只问「能退吗」时，CLI 会追问订单编号。

可用 `py -3 -X utf8 test/verify_policy_cli.py --config <验收配置>` 运行真实 CLI 咨询验收，覆盖通用政策、订单资格、意图对照和重排故障。脚本将演示政策发布到随机命名的 MySQL 库和 Milvus 集合，启动独立 MCP 服务，并在运行后清理本轮隔离资源。

## 模拟退款

在 Host 和 MCP Server 配置相同的 `mcp.write_token`，并使用独立的 `mcp.read_token` 调查凭据。普通查单和合格的资格咨询直接按本人订单事实答复。已明确授权的办理先复核最新订单，再保存带本轮授权的提交记录，最后由 MCP 在锁定本人订单后重新核验并写入模拟退款。资格判断、复核和事务内核验共用程序规则。合格办理在政策后端不可用时仍可执行。未配置执行凭据时，查单和原退款核对仍可使用。

在 CLI 输入「如果能退，就帮我退 DEMO-1001」。演示按 MySQL 订单的实付金额办理整单原路模拟退款，回复显示订单号、金额和模拟退款单号。重复办理时直接核对原退款。结果待核对时，该订单暂停新的提交。

提交前确认订单未支付或已发货时，流程按拒绝原因检索正式条款。事务内才确认资格拒绝时，流程在事务结束后按退款服务回执解释。两种路径均先说明已经确认的业务原因，只有核实出处、版本、原文和适用范围后才附上条款。补查最多一次，沿用首次检索的政策版本快照。

运行 `py -3 -X utf8 test/verify_refund_submission.py --config <验收配置> --group qualification --evidence <结果文件>`，可通过 CLI、独立 MCP 进程和隔离的 MySQL 库核对冷启动时的查单、资格咨询、办理和政策降级。完整业务验收可省略 `--group qualification`，同时在隔离集合验证政策切换与版本快照。使用 `--group explicit-policy` 验证主动索要依据、条款前提和随后办理授权。

安装 `pytest` 后，运行 `py -3 -m pytest -q test`，执行无需配置外部服务的测试。
