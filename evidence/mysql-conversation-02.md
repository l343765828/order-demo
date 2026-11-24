# 02 退款会话记录与 Checkpoint 恢复验收

验收日期为 2026-11-24。连接目标 MySQL 后先执行 `SELECT VERSION()`，实测 **8.0.46**。运行环境为 Windows、Python **3.11.7**、SQLAlchemy **2.0.58**、PyMySQL **1.2.4**、DBUtils **3.2.0**、Pydantic **2.14.3**、LangGraph **1.2.22**、`langgraph-checkpoint` **4.3.2**、`langgraph-checkpoint-mysql` **3.0.0**、DeepAgents **0.7.38**、MCP **2.3.0** 和真实 Qwen3-8B。

## 前置条件与改造前基线

02 在 01 之后实施。开工检出为 `8f1880e` 加工作区已有的 01 实施。核实 `requirements.txt` 的 SQLAlchemy 依赖、`utils/database.py` 的主库 Engine 工厂、`utils/sql_loader.py` 的固定根目录加载和缓存、订单 Repository 的命名参数绑定，以及 MCP 入口注入查单 Service。01 的五项已有验收证据。本轮改造前通过当前检出上的真实查单和全仓测试再次确认该入口。见 [01 工单](../.scratch/mysql-access-refactor/issues/01-mysql-core-and-order-query.md)及 [01 验收](mysql-core-01.md)。

- `.venv/Scripts/python.exe -m pytest -q`，**103 通过**。`test_mysql_core.py`、`test_order_flow.py`、`test_refund_submission.py`、`test_preferences.py` 和 `test_mcp_protocol.py` 合计 **57 通过**。
- `.venv/Scripts/python.exe -X utf8 test/verify_order_cli.py --config $config`，原会话实现通过真实 Qwen、独立 MCP 和 MySQL 查得实付 **19900 分**，重开后恢复会话和 Checkpoint，原文八条，退款写调用数为零。隔离库 `order_demo_core01_5cf119d26505` 已删除并核实。
- 原 `verify_checkpoint_mysql.py` 通过配置启动器向三个子进程传递同一份验收 JSON，以真实 MySQL 复验两个线程暂停、恢复、继续保存及按线程清理。脚本删除隔离库并核实。改造后脚本可直接接收 `--config`。
- 在另一隔离库用原 Service 读取空会话历史，在同一 PyMySQL 连接执行 `DO 0` 后观察到 `SERVER_STATUS_IN_TRANS` 仍成立。读取必须在进入流程前结束事务的断言按预期失败。隔离库 `order_demo_core01_5f92178ab2e8` 已删除并核实。

## 入口与事务边界

`cli.py` 加载 JSON 后组装 `MySQLConversationService`，在退出时释放 Engine。Host 通过它创建、打开和读取会话、追加原文。`repositories/conversation_repository.py` 在调用方传入的连接上执行显式 SQL，按命名参数传值，使用具名模型验证 MySQL 行，再由 Service 形成原有 `ExchangeMessage`。

创建会话在独立事务中提交。打开会话时以原锁名 `order_demo:conversation:<UUID>` 零等待加锁，随后核验归属。加锁、归属查询、历史读取及解锁分别结束短事务。每次追加在同一短事务里读取末序号、插入原文和更新会话时间，任一步失败则回滚。锁覆盖本轮流程，用户原文在调用流程前对其他连接可见，助手原文在回复生成后提交。解锁返回值无法确认释放时关闭该物理连接。见 [MySQL 命名锁](https://dev.mysql.com/doc/refman/8.0/en/locking-functions.html)及 [SQLAlchemy 连接事务](https://docs.sqlalchemy.org/en/20/core/connections.html#using-transactions)。

CLI 继续用原生 PyMySQL 连接运行 `PyMySQLSaver`，原会话 UUID 继续作为 `thread_id`。退款提交、偏好仍由 Host 的旧 DBUtils 池访问，两个业务池分别沿用每池五条连接的限制。管理命令仍用原 PyMySQL 初始化和种子装载路径。会话来源消息序号、CLI 输出、MCP 工具与模型消息沿用现有契约，无需更新工具 Schema 或提示词。

## 实测结果

| 工单验收项 | 实际观察 |
| --- | --- |
| 创建、绑定消费者、有序原文和拒绝行为 | 新会话与可信消费者绑定。历史按序号返回 `user` 和 `assistant` 原文，包含换行、引号、反斜线、百分号和四字节字符。大写 UUID 规范化后定位原会话。无效、未知、跨消费者及锁竞争均拒绝，CLI 返回 `会话不可用。` 和退出码 `2`。真实 MySQL 中遗留的空消费者字段也按归属不匹配拒绝。 |
| 连接级锁与资源清理 | 独立连接在读、写和回滚结束后观察到同一持锁连接，竞争打开仍被拒绝。正常退出、处理异常、归属拒绝后都能再次取得锁。跳过解锁并注入返回 `0` 时，观察到对应 MySQL 物理连接失效事件。另一连接接着获得原锁，下一次打开使用不同的物理连接。 |
| 原文提交顺序与事务回滚 | 外部连接在 Host 调用流程前读到已提交的用户原文，并确认会话锁仍持有。SQLAlchemy 和 MySQL 会话状态均显示事务已结束。故障触发器让追加中的第二条写入在成功插入后报 MySQL `1644`，外部连接核实原文条数、序号和会话更新时间均未变化。撤除故障后继续得到序号 `2`。在流程入口注入异常时，仅保留已提交用户原文，退出后可重新获取锁。真实 CLI 成功轮次在回复生成后保存助手原文。 |
| 旧数据与 Checkpoint | 从 `8f1880e` 导出旧 CLI。该进程将会话、两条原文和实际业务图 Checkpoint 写入隔离库，新版 CLI 进程恢复原追踪标识、历史和 Checkpoint，再接续同一会话。原生 `PyMySQLSaver` 读回已有图状态，历史模式没有执行计划节点。真实 CLI 返回实付 19900 分、八条有序原文和零次退款写调用。独立 Checkpoint 脚本验证两个暂停线程恢复并完成，再重复清理一个线程，同时逐行核对七张业务表保持原样。 |

旧退款待办另用可复跑的隔离样例验证。样例进程加载 `8f1880e` 的会话实现和真实退款图，写入一条用户原文，并通过旧图的 `update_state` 构造下一节点为 `send_refund` 的 Checkpoint。新版 CLI `--history` 读回原文、追踪标识和同一待办节点，加载事件是唯一的业务事件。再次用 `PyMySQLSaver` 查询，Checkpoint ID 未推进。整组退款与退款提交记录计数均保持初始值。

## 复跑命令

`$config` 指向工作区外、含已授权 MySQL 与 Qwen 连接的 JSON，`$legacy` 指向 `git archive 8f1880e` 解压后的临时目录。验收脚本用随机隔离库，CLI 联验派生随机 MCP 凭据与本机监听端口，结束时清理并核实隔离库。临时配置不随仓库发布。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_conversation_mysql.py --config $config
.venv/Scripts/python.exe -X utf8 test/verify_checkpoint_mysql.py --config $config
.venv/Scripts/python.exe -X utf8 test/verify_order_cli.py --config $config
.venv/Scripts/python.exe -X utf8 test/verify_order_cli.py --config $config --legacy-root $legacy
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs cli.py implementations/refund/host.py implementations/refund/conversations.py model/refund/conversation.py repositories/conversation_repository.py utils/mysql_lock.py config/limits.py
.venv/Scripts/python.exe -m compileall -q cli.py config implementations model repositories test utils
.venv/Scripts/python.exe -m pip check
git diff HEAD --check
git diff --cached --check
```

数据库故障验证在 `order_demo_core01_fd4befd4bd18` 通过，隔离库已删除并核实。Checkpoint 验证在隔离库通过，跨进程写入、继续与清理的三个阶段均退出码 0。全仓 **103 通过**，工单相关回归 **57 通过**。七个应用文件通过 mypy，编译和依赖检查通过。隔离 Python 进程单独导入会话 Repository、命名锁工具和配置，未加载应用入口、业务用例编排或工作流。

新版 CLI 创建与恢复联验退出码 0，首轮 Host PID `23932`，独立 MCP PID `175780`，重开 Host PID `96572`，首轮 trace `757e2668-2d85-4fc1-8db1-578c3cc33f5f`。隔离库 `order_demo_core01_6eae0d779af9` 已删除并核实。

旧版写入与新版恢复联验退出码 0，首轮旧 Host PID `68952`，独立 MCP PID `61288`，新版 Host PID `165460`，首轮 trace `d73da314-9e41-48af-8e38-27522c330766`。输出确认 `legacy_host_wrote_first_turn` 和 `pending_refund_checkpoint` 均为 `true`。隔离库 `order_demo_core01_886c9d68c630` 已删除并核实。

规范与工单契约分别审查。复跑覆盖了旧会话空归属的拒绝、旧退款待办的恢复、原文参数的模型序列化和取锁异常分支的注释。四项验收均有实测证据。LangGraph 对历史模型类型发出已有反序列化注册警告，本轮读取和恢复通过。
