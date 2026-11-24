# 01 MySQL Core 基础设施与本人查单验收

2026-11-24，Windows，Python 3.11.7。目标 MySQL 上首先执行 `SELECT VERSION()`，实测 **8.0.46**。工作区基线为 `8f1880e`。本票在专题施工顺序中排首位，直接依赖为无。历史的 [本人查单](order-cli.md)、[退款提交](refund-07.md) 和 [消费者偏好](preferences-11.md) 验收用于确定回归场景。

## 改动前盘点

使用 `rg -n --glob '*.py'` 搜索 `pymysql`、`dbutils`、`PooledDB`、`cursor()`、`connection()`、`begin()`、`commit()`、`rollback()` 和 `PyMySQLSaver`，并跟到以下调用方。

| 类别与调用方 | 改动前的连接和事务边界 | 归属 |
| --- | --- | --- |
| 本人查单 `mcp_service.server` → `mcp_service/orders/query.py` | 借用 `model.create_pool()` 的 DBUtils 连接，在单次游标查询中同时按订单号和认证消费者筛选，左联退款记录，读完关闭池代理 | 01，已迁移 |
| CLI → `implementations/refund/conversations.py` | DBUtils 连接保存会话、每次追加原文与会话时间一同提交。在同一专用连接上取得会话命名锁，退出回滚并尝试释放该锁 | 02 |
| Host `implementations/refund/dispatches.py` 和 MCP `implementations/refund/transactions.py` | DBUtils 借用连接。核对只读，Host 保存或更新提交记录后提交。MCP 受控事务先锁提交再锁订单，区分明确回滚与提交回执不明 | 03 |
| Host `implementations/refund/preferences.py` | DBUtils 专用连接读取，更新、删除分别提交 | 04 |
| MCP 和 `publish_policy.py` → `implementations/policy/catalog.py` | 通过 `utils/mysql.py` 直连，快照读取使用自动提交，版本发布在一条连接上持有命名锁，记录写入阶段，切换目录使用显式事务。`initialize()` 在独立管理连接执行 DDL | 05，初始化保留原管理路径 |
| `manage.py` → `implementations/refund/demo_data.py` | PyMySQL 独立管理连接执行幂等初始化。装载种子时关闭自动提交，订单和种子退款整批提交或回滚 | 初始化保留原管理路径，种子迁移归 06 |
| `cli.py` → `utils/checkpoint.py` → 第三方 `PyMySQLSaver` | 使用 `utils/mysql.py` 打开自动提交的原生 PyMySQL 连接，适配器管理检查点操作，CLI 退出时关闭连接 | 保留第三方路径，02 验收兼容性 |
| `model/__init__.py` 与 `utils/mysql.py` | 旧配置工厂提供 UTC、UTF-8 和连接超时。每个 DBUtils 池最多五条连接，旧业务借用代理时由 DBUtils 回收未提交事务 | 01 保留供未迁移调用方使用，06 清理 |
| 验证脚本和测试 `test/` | `verify_mysql.py` 直接验证管理路径与分进程旧池。`verify_checkpoint_mysql.py` 直连并调用 `PyMySQLSaver`。`verify_order_query_mysql.py` 原先直接借用旧池只读验种。`verify_order_cli.py`、`verify_preferences_cli.py`、`verify_policy_cli.py`、`verify_policy_catalog.py`、`verify_refund_submission.py` 使用原生连接核对数据副作用、执行隔离准备与清理或注入故障。`test_order_query.py` 原先模拟 PyMySQL 连接故障。`policy_probe_server.py` 原先组装旧池 | 01 更新查单相关调用方，其余验证按原路径和所属工单执行 |

## 改动前可复跑基线

以下命令从仓库根目录执行，`$config` 指向工作区外的有效验收 JSON 文件。仓库默认 JSON 内的 MCP 凭据为占位值，配置校验拒绝加载。实际验收从项目历史记录指向的既有配置取得连接信息，临时配置由测试进程清理。

- `.venv/Scripts/python.exe -m pytest -q`，**98 通过**。
- `.venv/Scripts/python.exe -m pytest test/test_order_query.py test/test_mcp_protocol.py test/test_order_flow.py test/test_refund_submission.py -q`，**38 通过**。
- `.venv/Scripts/python.exe test/verify_mcp_protocol.py`，独立进程完成发现、结构化结果往返及客户端关闭。
- 在基线检出上使用 `.venv/Scripts/python.exe -X utf8 -c "import runpy, sys; from config import use_config; use_config(sys.argv[1]); runpy.run_path('test/verify_order_query_mysql.py', run_name='__main__')" $config` 可复跑原真实查单。只读核对现有演示库有五笔种子，`DEMO-1001` 本人样例实付 19900 分且无退款，`DEMO-1003` 已退款，他人和不存在返回相同不可用结果。
- 改动前 `mypy --ignore-missing-imports --check-untyped-defs mcp_service/server.py mcp_service/orders/query.py model/orders config/limits.py` 通过。旧 `verify_order_cli.py` 固定使用业务库并会创建会话、写入消息和初始化检查点，本轮在脚本改为隔离库后执行该项。

## 实施后验收

| 工单验收项 | 实测与观察 |
| --- | --- |
| 调用清单、旧事务边界及基线 | 盘点和改动前输出见上文 |
| Engine 初始化、复用、会话设置和退出 | `test/test_mysql_core.py` 验证特殊字符密码原值传给 PyMySQL、URL 掩码、主库和显式库选择、池大小、参数隐藏、独立进程导入，以及 MCP 入口在正常和异常退出时执行 `dispose()`。真实 MySQL 检查会话 UTC `+00:00`、`utf8mb4`、查单归还连接、物理连接复用，以及杀掉本次验收空闲连接后由存活检查换用新连接。 |
| SQL 加载、绑定、数据验证和事务归属 | `test/test_mysql_core.py` 从临时工作目录加载固定根路径下的 UTF-8 文件，同一文件的等价路径复用 `TextClause`，目录穿越被拒绝。Repository 使用配置中的固定表名、注入连接和命名参数，在数据库边界验证 JOIN 结果。Service 退出连接上下文时结束只读事务。隔离库写入违规币种时，MCP 返回受控 `FAILED`。 |
| 查单业务结果与故障隔离 | 真实 MCP 查询本人未退款与已退款订单，实付 19900 分。其他消费者与不存在返回相同 `UNAVAILABLE`。订单号和消费者编号分别使用带引号、反斜线、百分号和四字节字符的固定样例。仅在隔离库临时改名退款表触发真实 SQL 错误，客户端只收到 `failed`、空订单和固定提示，恢复表名后同一服务再次查单成功。 |
| MCP 协议、认证、日志、线程及写权限 | 真实独立 MCP 进程发现四个工具，查单输入只有订单号，输出保持 `structuredContent` 且发现结果不声明输出 Schema。未认证返回 HTTP 401，附加的消费者字段无法改变认证身份，错误类型被拒绝。读凭据调用写工具被拒绝，独立写凭据进入原退款校验并因无提交返回 `denied`，退款和提交记录计数仍为 `1`、`0`。查单日志关联 trace、工具事件及 MCP 请求，应用入口测试观察到同步查单运行在 HTTP 事件循环以外的工作线程。 |

验证命令与版本

- `.venv/Scripts/python.exe -m pytest -q`，**103 通过**。包括 `test/test_order_query.py`、`test/test_mysql_core.py` 和 `test/test_mcp_protocol.py`。
- `.venv/Scripts/python.exe -X utf8 test/verify_order_query_mysql.py --config $config`，退出码 0。MySQL **8.0.46**，MCP 服务 PID `143708`，trace `947bdcba-2029-4e45-b86c-ee81fdd3e8e1`，隔离库 `order_demo_core01_4866a9a099c6` 已删除并通过 `information_schema.SCHEMATA` 核实。
- `.venv/Scripts/python.exe -X utf8 test/verify_order_cli.py --config $config`，退出码 0。真实 Qwen3-8B、独立 MCP 服务 PID `173228` 与 MySQL **8.0.46** 返回本人订单实付 19900 分。同一 trace `ae570537-97b7-48fb-99d8-9d4cf65a6761` 关联计划、DeepAgent 委派、ReAct 工具回执及重规划。CLI 对他人和不存在返回相同提示，服务断开后返回服务不可用。另一进程恢复同会话，核对八条有序原文、检查点、消费者归属和零次退款写调用。隔离库 `order_demo_core01_5542447c45c8` 已删除并核实。LangGraph 对现有检查点模型类型发出反序列化注册警告，恢复验证通过。
- `.venv/Scripts/python.exe test/verify_mcp_protocol.py`，退出码 0，结构化结果有效，客户端连接关闭。
- `.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config/limits.py model/__init__.py model/orders/query_row.py utils/database.py utils/sql_loader.py repositories/order_repository.py interfaces/orders/query.py implementations/orders/query.py mcp_service/server.py`，9 个源文件通过。`compileall`、`pip check` 和 `git diff HEAD --check` 通过。
- SQLAlchemy **2.0.58**，PyMySQL **1.2.4**，DBUtils **3.2.0**，Pydantic **2.14.3**，MCP **2.3.0**，DeepAgents **0.7.38**，MySQL checkpointer **3.0.0**。

规范和工单契约分别审查。按反馈统一固定表名、规范化 SQL 缓存键、补齐连接池配置及隔离验收进程清理。调整后复跑全仓测试、真实 MySQL/MCP 和 CLI。工单五项均有本轮实测证据。

新路径为 MCP 工具 `get_order` → `MySQLOrderQueryService` → 订单 Repository → `repositories/sql/orders/get_order.sql`。SQLAlchemy Core 使用 PyMySQL 驱动。MCP 入口加载配置后创建一个 Engine 与原退款池，服务退出时释放两者。迁移过渡期两个池独立，各最多五条连接，同一 MCP 进程的两个主库连接池合计可持有十条连接。查单执行只读 SQL，Service 归还连接时结束自动开启的事务。会话锁、第三方检查点、退款事务、政策访问和种子装载继续沿旧入口运行。连接工厂和池行为见 [SQLAlchemy Engine](https://docs.sqlalchemy.org/en/20/core/engines.html) 和 [连接池文档](https://docs.sqlalchemy.org/en/20/core/pooling.html)。
