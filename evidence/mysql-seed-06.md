# 06 种子装载与 MySQL 访问层验收

验收日期为 2026-11-24。运行环境为 Windows、Python 3.11.7、SQLAlchemy Core 2.0.58、PyMySQL 1.2.4、MCP SDK 2.3.0 和 PyMySQLSaver 3.0.0。本轮在回环地址启动独立 MySQL Community Server，连接后第一条 SQL 为 `SELECT VERSION()`，返回 **8.0.46**。后续验证均使用本轮隔离实例和随机库，工作区外的临时 JSON 提供连接配置。

## 前置条件与开工基线

以 `8f1880e` 加开工时已有暂存及工作区改动为基线。按 01 至 05 核对共用 Engine 和查单入口、会话命名锁与检查点接入，以及直接依赖的 03 退款事务、04 偏好事务和 05 政策目录事务。现有代码、配置组装和验收记录支持这些依赖。仓库状态标签保留 `ready-for-agent`。 [01](mysql-core-01.md) · [02](mysql-conversation-02.md) · [03](mysql-refund-03.md) · [04](mysql-preferences-04.md) · [05](mysql-policy-05.md)

修改前全仓 pytest **107 项通过**。直接运行旧 `test/verify_mysql.py` 时，仓库默认 JSON 在连接前被配置校验拒绝。随后在开工源码副本中使用本轮本机隔离实例和临时配置，运行旧 `manage.py init`、`manage.py seed`、`test/verify_mysql.py`、`test/verify_order_query_mysql.py --config $config` 和 `test/verify_checkpoint_mysql.py --config $config`，均退出 0。旧版种子、数据库外键和唯一约束、双进程 DBUtils 连接池、独立 MCP 查单及跨进程检查点均通过。

## 入口、职责与事务

| 原业务入口 | 实现及数据库边界 |
| --- | --- |
| `manage.py seed` 的直接 PyMySQL 游标 | `manage.py` 创建并释放 Engine。 `implementations/refund/demo_data.py` 中的 `MySQLSeedService` 用一次 `Engine.begin()` 持有连接。订单与退款 Repository 各执行自己的绑定 SQL。普通订单插入成功才计为本次新增，订单主键重复保留原行。种子退款写入出错或其他订单错误使整批回滚。 |
| 独立 MCP `get_order` | `implementations/orders/query.py` 借用只读连接，订单 Repository 限定认证消费者与订单并关联退款。读完归还连接。 |
| CLI 与 Host 的退款会话记录 | `implementations/refund/conversations.py` 同连接持有命名锁。归属和历史读取结束事务，用户原文在流程前提交，助手原文在生成后提交。每条原文与会话更新时间一起提交。释放回执不明时丢弃物理连接。 |
| MCP 退款核对、模拟退款及 Host 提交记录 | `implementations/refund/transactions.py` 读原退款时归还只读连接，执行时在同事务依次锁定提交、本人订单和退款。提交前错误回滚，提交回执不明进入既有待核对逻辑。 `dispatches.py` 保存提交和回写结果各用短事务，MCP 调用在事务之外。 |
| CLI 显式长期偏好 | `implementations/refund/preferences.py` 限定认证消费者读取。写入和删除各用独立短事务，返回前提交及归还连接。 |
| MCP 政策快照和独立政策发布命令 | `implementations/policy/catalog.py` 读取单次目录快照。同物理连接跨短事务持有版本命名锁，分别提交写入待确认、写入完成和生效修订，Milvus 操作在事务之外。锁释放无法确认时丢弃物理连接。 |

Repository 和 SQL 分别位于 `repositories/` 与 `repositories/sql/`。本票新增订单插入 SQL，并复用既有退款插入 SQL。 `model/orders/record.py` 定义样例订单编号枚举、订单写入事实及带时间字段的持久化记录，`config/demo.py` 集中定义样例金额、币种和配套退款编号。申请、恢复规则和 MCP 输入输出 Schema 沿用基线。

`requirements.txt` 保留 SQLAlchemy 2.0.58 和 PyMySQL 1.2.4，移除 DBUtils，以及 `model/__init__.py` 的旧池工厂与 `utils/mysql.py` 的旧池借用函数。直接连接参数移至 `utils/mysql.py`。 `manage.py init` 建库、建表及补外键，政策目录初始化执行 DDL 和目录头初始化，LangGraph 检查点通过 `utils/checkpoint.py` 与原生 `PyMySQLSaver` 管理。上述操作和测试观测仍使用 PyMySQL。显式传入 `--legacy-root` 复验历史检查点或偏好时，运行旧源码的验收解释器还须安装旧检出所要求的 DBUtils。当前应用运行依赖无 DBUtils。

CLI、MCP、发布命令及种子命令分别负责其 Engine 的释放。MCP 只在政策目录使用独立库时创建第二个 Engine。按包独立导入配置、工具及 Repository 未加载业务编排、工作流或应用入口。README 已按简体中文更新安装、部署 JSON、初始化、种子和隔离验证用法。

## 逐项实测

| 验收项 | 实际结果 |
| --- | --- |
| 空库种子及幂等 | 两个种子命令并发执行均成功。独立连接读取到 5 笔样例订单与 `DEMO-1003` 配套的唯一 `MOCK-RF-1003`，金额 8800 分，来源 `seed`，提交键为空。普通重跑保持全部字段和时间。修改既有订单归属、发货状态、金额及原退款后重跑，数值与时间保持原样。删除原退款后重跑，配套订单仍无退款。以同一 SQLAlchemy 方言实测旧式 `ON DUPLICATE KEY UPDATE` 空更新的 `rowcount` 为 1，新实现按插入结果区分。 [SQLAlchemy 行数语义](https://docs.sqlalchemy.org/en/20/dialects/mysql.html#rowcount-support) |
| 两个 Repository 共享事务 | 真 MySQL 触发器在退款 INSERT 处报错 1644。SQLAlchemy 执行事件记录前三笔订单和配套退款使用同一活动事务连接。命令返回 1，独立连接读到订单及退款均为空。移除触发器后从正常命令入口装载成功，两个表均可独立读到。 |
| 初始化及检查点 | `manage.py init` 创建尚不存在的库和表。移除复合退款外键后重跑补齐，第二次重跑仍只有一个外键，已有订单保持原样。约束拒绝重复退款和错单引用。政策目录原生初始化重复运行保留已有目录头。跨进程 PyMySQLSaver 写入、读取、恢复与按线程清理均通过。 |
| 连接、依赖和协议 | 隔离实例验证两进程各自释放 Core 池且另一进程继续读取。查单独立 MCP 核实本人、他人、缺失、真实数据库故障、认证、结构化回执及参数绑定。退款 MCP 实测双凭据、提交归属和撤销拒绝、并发只创建一次退款、再次返回原单及历史订单事实处理。会话和偏好 MySQL 验证通过。政策快照真实读回修订 1、2，持锁连接跨短事务，注入目录头失败 1644 后切换回滚，解锁异常丢弃连接。政策 MCP 对隔离的不可用向量端点返回 `failed` 和空候选。 |
| 文档与记录 | README、入口和旧路径映射、事务及锁、保留的直接操作和复跑边界记录于本页及工单。 |

## 执行命令

`$python` 为项目虚拟环境 Python，`$config` 为工作区外的授权验收 JSON。

```powershell
& $python -X utf8 test/verify_mysql.py --config $config
& $python -X utf8 test/verify_mysql_service_regressions.py --config $config
& $python -X utf8 test/verify_order_query_mysql.py --config $config
& $python -X utf8 test/verify_conversation_mysql.py --config $config
& $python -X utf8 test/verify_checkpoint_mysql.py --config $config
& $python -X utf8 test/verify_preferences_storage_mysql.py --config $config
& $python -X utf8 test/verify_mcp_protocol.py
& $python -X utf8 -m pytest -q --tb=short
& $python -m mypy --ignore-missing-imports --check-untyped-defs manage.py cli.py publish_policy.py config implementations interfaces mcp_service model repositories utils workflows
& $python -m compileall -q manage.py cli.py publish_policy.py config implementations interfaces mcp_service model repositories test utils workflows
& $python -m pip check
git diff --check
git diff --cached --check
```

退款与政策的独立本地回归由 `verify_mysql_service_regressions.py` 执行。它复用既有退款 MCP、政策目录锁和政策工具验证函数。 `verify_policy_cli.py` 已改为使用随机业务库、配套目录和集合，并在隔离的 CLI 源码副本中包含 Repository。实测该源码副本可启动并初始化检查点。使用不可用的回环 Milvus 运行 `test/verify_policy_cli.py --config $config --smoke`，命令按预期失败并核实移除其隔离库。

上列命令块中的独立验证均退出 0，测试脚本核对其隔离库已清理。应用改动前运行新增管理入口回归，3 项先失败，实施后通过。最终全仓 pytest **110 项通过**，阻断 DBUtils 导入复跑也通过。mypy **87 个源文件通过**，编译、依赖一致性和 Git 差异检查通过。规范复核将固定样例值、MySQL 排序规则和外键名集中定义，契约复核确认政策 CLI 隔离及旧版本验收依赖并更新说明。

## 复跑限制

本轮取得本机独立实例的版本与验证结果。仓库默认配置含占位值，先前集群验收所用的授权 JSON 本轮不可用，因此未连接集群 MySQL，未复跑依赖真实 Qwen、嵌入、重排及 Milvus 的完整 CLI、退款提交故障联验和政策发布检索联验。解除条件为提供可用的授权验收 JSON，并允许测试脚本创建和清理由本轮使用的隔离库及 Milvus 集合。先前专题验收仍可在对应工单和证据中查阅。

收尾时核实本机验收库均已删除，关闭独立 MySQL 进程并移除两份临时配置。运行环境拒绝递归删除本轮临时安装目录，目录仍在系统临时路径。四个应跟踪的新文件已分别暂存，核对开工时的 236 条暂存区索引记录保持原样。

## 2026-11-24 集群补验

用户补充集群连接信息后，`config/local.json` 已通过应用配置校验。集群 MySQL 的 `SELECT VERSION()` 返回 **8.0.46**，Milvus 为 **3.0.2**。使用真实 **Qwen/Qwen3-8B**，免鉴权请求通过工具调用、工具结果回传和严格 JSON Schema 验证。真实向量编码返回 1024 维向量，政策联验同时覆盖重排。上节记录的是首次实施时的环境限制。

| 已完成的集群复跑 | 观察结果 |
| --- | --- |
| `test/verify_mysql.py --config config/local.json` | 并发种子最终为 5 笔订单和 1 笔配套退款。旧式空更新返回行数 1，已有无退款订单保持原样。配套退款写入触发 MySQL 1644 后，独立连接确认整批回滚。初始化、补外键及独立连接池检查通过。 |
| `test/verify_order_cli.py --config config/local.json` | 真实 Qwen 与独立 MCP 查询本人订单实付 19900 分，他人和不存在的订单结果一致。会话重开后保留 8 条有序原文，退款写调用为 0。 |
| `test/verify_policy_catalog.py --config config/local.json --evidence $policyOutput` | 真实目录、Milvus 与独立 MCP 验证当前政策检索、同一调查固定修订、版本切换、并发冲突和故障重试。目录库、订单库及集合均核实清理。 |
| `test/verify_qwen_protocol.py`、`test/verify_deepagent_protocol.py` | 使用项目客户端完成原生工具调用、观察回传、严格结构化输出和独立 MCP 委派。异步真实请求核对无认证头。 |

### 退款与长期偏好复跑命令

`$output` 为工作区外的临时结果目录。脚本分别创建随机命名的 MySQL 库。退款每组另配独立 Milvus 集合和临时 MCP 读写凭据，偏好联验使用临时 MCP 读取凭据。

```powershell
$python = '.\.venv\Scripts\python.exe'
$config = 'config/local.json'
& $python -X utf8 test/verify_refund_submission.py --config $config --evidence "$output/refund-business.jsonl" --group business
& $python -X utf8 test/verify_refund_submission.py --config $config --evidence "$output/refund-outcomes.jsonl" --group outcomes --investigation-probe
& $python -X utf8 test/verify_refund_submission.py --config $config --evidence "$output/refund-safety.jsonl" --group safety --investigation-probe
& $python -X utf8 test/verify_preferences_cli.py --config $config --evidence "$output/preferences.json"
```

### 退款与长期偏好复跑结果

四条命令于北京时间 23:50 至 23:59 执行，均退出 0。各组连接集群后执行的 `SELECT VERSION()` 均返回 **8.0.46**。退款组实测 Milvus **3.0.2**，共同运行环境为 Python **3.11.7**、SQLAlchemy Core **2.0.58**、PyMySQL **1.2.4**、MCP **2.3.0**、LangGraph **1.2.22** 和 DeepAgents **0.7.38**。偏好恢复使用 PyMySQLSaver **3.0.0**。

| 验证路径 | 实际观察 |
| --- | --- |
| 退款正常业务，7 个场景 | 真实 Qwen、政策检索和独立 MCP 完成合格订单的模拟退款，金额为实付 19900 分。双凭据、退款归属、撤销、并发重复请求及已有退款核对均通过。未支付、已发货、他人订单和已有退款保留原处理，额外指定金额和渠道仍按整单实付规则处理。 |
| 退款结果恢复，19 个场景 | 验证丢失成功或拒绝回执、真实 INSERT 后回滚、提交成功后断连，以及未完成提交时断连。提交成功分支重开原会话或新开会话后均核对到原退款。未完成提交分支两种会话均查无退款并保持待核对。四个重开场景均无重复写调用。两会话竞争场景仅 1 次写工具调用，产生 1 笔退款。 |
| 退款安全边界，11 个场景 | 政策重排、证据、事件保存和提交保存故障均在新退款写入前拦截。指定故障下，本人查单和原退款只读核对仍可用。 |
| 长期偏好，3 个会话的 19 轮 | 实测保存、跨会话读取、更正、当轮覆盖、删除、重开和新会话读取。删除后旧会话读取为空，查单恢复默认详略。其他消费者隔离及读写故障语义通过。8 次独立 MCP 查单结果一致，逐条核对 38 条原文，退款写调用为 0。 |

退款正常业务组通过真实 Qwen 调查。两组故障联验以确定性调查探针驱动真实 CLI、LangGraph、独立 MCP、MySQL 和 Milvus，逐例核对库内结果与写调用次数。偏好组每轮使用真实 Qwen，并通过原 CLI 入口读取历史和 Checkpoint。偏好运行期间有既有 LangGraph 历史模型类型注册警告，读取与恢复断言通过。

三组退款联验结束后，按各组实际隔离名称重新查询 MySQL 和 Milvus，确认三套库与集合均已删除。长期偏好联验核实独立 MCP 已退出且 MySQL 隔离库已删除。配置、种子、查单、政策、退款及长期偏好的上述集群补验均已完成。
