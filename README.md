# order-demo 的 MySQL 存储

## 项目文档

- [需求与总体设计](.scratch/refund-agent-all-tech-demo/spec.md)涵盖业务范围、流程、异常处理和验收标准。
- [施工总览](.scratch/refund-agent-all-tech-demo/task-overview.md)列出各工单、施工顺序、依赖和验收目标。[模型接口实施计划](.scratch/refund-agent-all-tech-demo/interface-implementation-plan.md)记录 Qwen 原生工具与 Schema 接口的实施步骤。
- 专题设计包括[数据库](.scratch/refund-agent-all-tech-demo/database-design.md)、[存储与记忆](.scratch/refund-agent-all-tech-demo/storage-memory.md)、[上下文压缩](.scratch/refund-agent-all-tech-demo/context-compression.md)、[MCP 接入与部署](.scratch/refund-agent-all-tech-demo/mcp-integration.md)、[模型选型](.scratch/refund-agent-all-tech-demo/model-selection.md)、[接口契约](.scratch/refund-agent-all-tech-demo/integration-contracts.md)和[订单号提取](.scratch/refund-agent-all-tech-demo/order-id-extraction-research.md)。
- [领域术语](GLOSSARY.md)、[架构决策](docs/adr/)和[退款政策原文](policy/refund-policy.md)记录已确认的约定。[MySQL 访问层重构](.scratch/mysql-access-refactor/spec.md)说明业务连接和事务边界。[验收记录](evidence/)保存已执行的验证及结果。

目前代码实现了查单、政策检索、订单资格咨询和单轮明确授权的模拟退款。后续多轮办理和维护恢复见施工总览。历史记录中的旧项目路径和部署环境信息用于追溯，当前运行方式见下文。

## 环境配置与启动

以下命令在包含 `cli.py` 的项目目录执行。运行 `py -3 -m pip install -r requirements-protocol.txt` 安装依赖，其中包含 SQLAlchemy Core 2.x 与 PyMySQL。在 `config/local.json` 中配置 MySQL 地址和凭据、免鉴权 Qwen 服务地址以及 MCP 读取凭据。Qwen 请求省略 `Authorization` 请求头。MCP 凭据须为至少 32 个可见 ASCII 字符。程序启动时读取该 JSON。

CLI 和 MCP 服务各自在进程内复用业务连接池，进程退出时释放。数据库连接使用 UTC 和 `utf8mb4`。主库由 `mysql.database` 指定，默认使用 `order_demo`。`manage.py init`、政策目录初始化和 LangGraph 检查点通过独立 PyMySQL 连接管理。

先初始化数据库，再装载演示种子：

```powershell
py -3 manage.py init
py -3 manage.py seed
```

两条命令都可以重复执行，并保留已有数据。初始化会创建缺失的数据库对象，已有表结构不兼容时需另行迁移。种子装载在一个事务内写入缺失的订单，仅当本次成功插入 `DEMO-1003` 时才写入配套模拟退款。任一写入失败时，本次装载回滚。

运行 `py -3 -X utf8 test/verify_mysql.py --config <验收配置>` 进行隔离集成验证。验收配置为工作区外的 JSON，须提供有权创建和清理测试库的 MySQL 账号。脚本先查询实例版本，再创建和清理随机命名的测试库，验证初始化、种子幂等性、事务回滚和进程连接池。

首次运行前初始化 checkpoint 表，然后在独立终端启动 MCP 服务：

```powershell
py -3 cli.py --init-checkpoints
py -3 -m mcp_service.server
```

在同一目录打开另一个终端，运行 `py -3 cli.py`，输入「查一下 DEMO-1001」。MCP 服务代码位于 `mcp_service/`，本人查单服务位于 `implementations/orders/`，查询 SQL 位于 `repositories/sql/orders/`。订单查询契约位于 `model/orders/`，退款政策模型位于 `model/policy/`，退款流程模型位于 `model/refund/`。

## 长期偏好

CLI 支持用聊天管理订单状态答复的详略。输入「请记住以后简短回答」保存偏好，「查看我的长期偏好」查看当前值，「请把我的长期偏好改为详细回答」覆盖旧值，「请删除我的答复详略偏好」删除。偏好随消费者跨会话使用，删除后仍可通过 `--history` 查看原始聊天。

输入「查一下 DEMO-1001，这次请详细回答」可以只改变当次排版。简短和详细答复均包含订单号、支付状态、发货状态、模拟退款状态和实付金额。

## 退款政策发布与检索

演示政策源位于 `policy/refund-policy.md`。在配置文件的 `policy` 部分填写 Milvus 地址、`milvus_token` 和模型服务地址。Milvus 使用账号密码认证时，令牌格式为 `用户名:密码`。MCP 服务和发布命令使用同一政策目录及索引。隔离验收可配套指定 `catalog_database` 和 `milvus_collection`，默认使用 `order_demo` 和 `order_demo_refund_policy`。每套索引的发布历史由对应的目录管理。未配置 `policy` 时仍可查单。

首次启用目录时，先初始化演示库，再发布演示政策 `1.0`，随后启动新版 MCP 服务。原集合已有 `1.0` 条目时，命令验证原件一致且可检索后登记为目录修订 `1`。

```powershell
py -3 publish_policy.py --source policy/refund-policy.md --version 1.0 --publisher demo-maintainer --qualification unchanged
```

发布其他文档或版本时，传入对应 Markdown 原件及其明确版本号。首行填写文档标题，标头使用 `文档编号：`、`版本：`。正式条款以 `## RF-003 — 条款标题` 开头，FAQ 以 `## FAQ-001 — 问题` 开头，后续正文为独立答案。命令完成 Milvus 强一致回读与检索验证后才切换 MySQL 目录。发布者用 `--qualification changed` 声明改变资格的版本，此类版本登记后须等待同版本结构化规则。

切换回执不明确时，重跑同一发布命令，命令先核对版本的发布记录。首次生效前可修正原件并用同版本全量重发。若上一轮 Milvus 写入结果不明，确认上一发布进程已停止且服务器端写入已结束后加 `--confirm-write-ended`。已生效版本的更新与删除通过发布新版本完成。

`search_policy` 首次成功时返回目录修订号，即使候选为空。Host 首次咨询使用 `consultation`，必要时沿用同一修订号，用 `formal` 补查一次。检索失败返回 `failed` 和空候选。

运行 `py -3 test/verify_policy_mcp.py` 可通过独立 MCP 进程验证认证及真实检索。运行 `py -3 test/verify_policy_catalog.py --config <验收配置> --evidence evidence/policy-16-integration.jsonl` 可在真实后端创建随机命名的目录和集合，验证版本切换、故障重试与 Host 读取。脚本退出时清理其创建的目录、集合、隔离会话和检查点。

在 CLI 输入「退款多久能到账？」可咨询通用政策，输入「DEMO-1001 能退吗？」可查询本人订单资格。Host 运行时从 MCP 回执取政策正文和出处，无需政策 Markdown。通用咨询可引用适用的 FAQ 或正式条款。本人订单资格由代码根据实际订单状态计算，并引用正式条款。只问「能退吗」时，CLI 会追问订单编号。

可用 `py -3 -X utf8 test/verify_policy_cli.py --config <验收配置>` 运行真实 CLI 咨询验收，覆盖通用政策、订单资格、意图对照和重排故障。脚本将演示政策发布到随机命名的 MySQL 库和 Milvus 集合，启动独立 MCP 服务，并在运行后清理本轮隔离资源。

## 模拟退款

在 Host 和 MCP Server 配置相同的 `mcp.write_token`，并使用独立的 `mcp.read_token` 调查凭据。为 Host 设置 `refund_policy_events_dir`，指向可写的持久目录。该目录按幂等键保存每次最终政策核验的 JSONL，记录关联的 `trace_id` 和正式条款出处。未配置执行凭据或无法确认核验事件保存时，查单和原退款核对仍可使用。

在 CLI 输入「如果能退，就帮我退 DEMO-1001」。演示按 MySQL 订单的实付金额办理整单原路模拟退款，回复显示订单号、金额和模拟退款单号。重复办理时直接核对原退款。结果待核对时，该订单暂停新的提交。

运行 `py -3 -X utf8 test/verify_refund_submission.py --config <验收配置> --evidence evidence/refund-07-integration.jsonl` 可使用独立 MCP 进程及隔离的 MySQL 库和 Milvus 集合验收。应用的 `mysql.database` 默认使用 `order_demo`，该脚本会在临时配置中覆盖并清理自己创建的资源。

安装 `pytest` 后，运行 `py -3 -m pytest -q test`，执行无需配置外部服务的测试。
