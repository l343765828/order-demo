# 01: 建立 order_demo 专用库、共享表定义与演示种子

**Status:** ready-for-agent

**Blocked by:** 无，可立即开工。

## 目标

让 Host 和 MCP Server 能在同一专用库中读取同一批可复现的演示数据，且 MySQL 能直接拒绝重复成功退款和同订单第二条未决提交。

## 范围

交付可重复执行的应用结构初始化与种子装载、共享 `order-demo/model/` 表定义与连接配置。初始化和种子通过显式命令执行。LangGraph 框架表由 02 验证选型，完整演示数据重置由 13 交付。

## 涉及的契约

- `order_demo` 使用 InnoDB、`utf8mb4`。七张应用表为 `orders`、`refunds`、`refund_dispatches`、`conversation_sessions`、`conversation_messages`、`conversation_summaries`、`consumer_preferences`。
- 两个进程读取同一套 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` 配置，各自管理连接池和事务。Host 管会话、记忆和退款提交，MCP Server 管订单及退款业务记录。
- 金额为人民币整数分，业务时间统一 UTC；授权证据随 `refund_dispatches` 保存。字段、外键、索引、状态及种子定义按定稿。 [数据库设计](../database-design.md) · [ADR-0004](../../../docs/adr/0004-refund-agent-storage-boundaries.md) · [ADR-0008](../../../docs/adr/0008-refund-authorization-per-dispatch.md)

## 验收标准

- [x] 在获准的演示环境初始化后，检查实际 DDL，七张应用表、主外键、金额字段、状态及唯一约束与定稿一致。不存在独立授权表，框架表另计。
- [x] 两个独立进程加载共享定义，分别建池，均可连接到 `order_demo`。关闭一方的池后另一方仍可读；缺失或错误配置能明确报错，凭据不出现在日志中。
- [x] 首次装载生成五笔订单 `DEMO-1001`、`1002`、`1003`、`1004`、`2001`（均带 `DEMO-` 前缀）和 `DEMO-1003` 的示例退款，覆盖本人可退、未支付、已退款、已发货和他人订单。再次执行初始化与种子装载，不重复种子，也不清除已生成的业务和聊天数据。
- [x] 实际 MySQL 拒绝重复订单成功退款、重复执行幂等键、无有效来源外键的提交，以及同订单第二条 `pending`／`unconfirmed` 提交。前条提交终结后，同订单未决唯一约束随之释放，其他订单的未决提交互不干扰。

## 验证方式

在隔离验收数据上执行初始化和种子命令两次，比对表结构、种子、实付金额字段与既有记录。用两个进程分别连接，并用事务夹具触发外键、唯一性和状态迁移用例。保留 `SHOW CREATE TABLE` 和断言结果。

## Comments

2026-11-19 实施与验收：目标实例 `SELECT VERSION()` 实测为 **8.0.46**（8.0.17 和 1064 为历史记录）。实现位于 `order-demo/`：共享 `model` 配置和进程独立连接池，`manage.py init` 显式创建专用库及七张应用表，`manage.py seed` 非破坏性装载种子；框架表不在本票范围。初始化前确认 `order_demo` 不存在。

- Python 3.11.7、PyMySQL 1.2.4、DBUtils 3.2.0；从受控环境注入 `MYSQL_*`，没有将凭据写入本票文件或输出。先后执行 `py -3 order-demo/manage.py init`、`py -3 order-demo/manage.py seed`，重复执行两次以上；五笔订单及 `DEMO-1003` 的 8800 分种子退款各一条，`DEMO-1001` 实付 19900 分。附加临时订单及聊天消息后再次运行两命令，记录保留；测试夹具随后删除。
- `py -3 order-demo/verify_mysql.py` 在随机隔离库中对真实 MySQL 验证重复退款、重复幂等键、无效来源消息、同订单 pending/unconfirmed 互斥、终态释放及不同订单独立；隔离库清理完毕。两个独立子进程各创建共享模块连接池，关闭一方后另一方仍可读取。实际专用库和隔离库的 `SHOW CREATE TABLE` 及断言结果见 `order-demo/evidence/mysql-verification.txt`。未创建独立授权表。
- 缺少主机、无效端口、不可达端口、错误密码分别得到明确失败；错误密码为 MySQL 1045，输出中均未出现密码。`py -3 -m compileall -q order-demo` 通过。
- 全仓库 `py -3 -m pytest -q` 未能进入测试：其他既有演示收集时有 18 项错误，包含缺少 httpx、jsonschema、pymilvus 和 MCP 模块冲突；该结果不影响上述本票真实 MySQL 验收，但全仓库测试未通过。另以临时依赖运行 `py -3 -m mypy --ignore-missing-imports --check-untyped-defs order-demo`，3 个源文件通过。

验收结论：四项工单标准均按上述证据完成。仓库未配置完成状态，按约定保留 `ready-for-agent` 并在此记录结论。

复核修正：种子改为事务写入，只在首次插入 `DEMO-1003` 时同时插入其示例退款；隔离库测试已有订单不新增退款、首次写入正确，以及退款编号冲突时回滚所有新增订单。执行退款的幂等键与订单一致性由后续 MCP 写事务核验，表保留唯一键；移除了退款反向引用提交的外键，避免与提交引用退款形成环，保证后续按定稿先清理提交再清理退款。真实 `order_demo` 已同步该结构并重新运行两轮初始化和种子，隔离验收与双进程测试再次通过。
