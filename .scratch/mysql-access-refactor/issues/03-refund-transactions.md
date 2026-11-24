# 03 迁移模拟退款、退款结果核对和退款提交记录

Status: ready-for-agent

Blocked by: [02 退款会话记录](02-conversation-storage.md)

## 目标

迁移 MCP 侧的退款核对、模拟退款事务及 Host 侧的退款提交记录，保留已交付的权限、结果和故障恢复行为。参见 [退款结果待核对](../../../docs/adr/0007-refund-unconfirmed-dispatch.md) 与 [政策核验持久化](../../../docs/adr/0009-refund-policy-evidence-before-dispatch.md)。

## 范围

迁移 `implementations/refund/transactions.py` 与 `implementations/refund/dispatches.py` 当前使用的业务 SQL，复用 `OrderRecord`、`RefundRecord`、`RefundDispatch` 等现有契约。Service 管理连接、行锁和提交边界。Host 的最终政策核验、保存提交、发出写请求及结果核对顺序沿用现有行为。

## 验收标准

- [x] `get_refund_result` 按认证消费者与订单核对既有模拟退款。`submit_mock_refund` 保持现有写凭据、Schema、退款校验和幂等行为。所有行锁和退款写入使用同一连接与事务。
- [x] Host 在已确认保存最终政策核验事件和当前退款提交后才发送写请求。写请求和只读核对在数据库事务之外执行。竞争时保留当前未决拦截与拒绝语义。
- [x] 写入或校验在提交前异常时回滚，提交回执不明时沿用结果待核对。锁竞争、重复请求、提交后断连及重开后核对继续遵循已验收行为。单笔订单最多产生一条成功模拟退款。
- [x] 双凭据行为、日志追踪字段、只读工具绑定和 LangGraph 调度与基线一致。

## 验证方式

运行 `test/test_refund_submission.py`。在隔离 MySQL 和现有独立 MCP 验证路径注入写入失败、提交前后故障与并发，读取数据库最终状态。复用 `test/verify_refund_submission.py` 和 [工单 07 验收](../../../evidence/refund-07.md) 的故障基线，记录执行结果及环境受限项。

## Comments

2026-11-24，完成 03 实施与验收。核对了直接依赖 02 及上游 01 的代码、配置与验收记录。目标 MySQL 首先执行 `SELECT VERSION()`，实际为 **8.0.46**。改动前全仓 pytest **103 通过**，相关用例 **30 通过**，隔离环境的旧执行结果故障基线 **8 个场景通过**。

MCP 退款查询和执行、Host 提交查询和写入已迁至 SQLAlchemy Core **2.0.58** 与 PyMySQL **1.2.4**。运行 `test/verify_refund_submission.py --config $config --evidence $output` 的 `--group business`，以真实 Qwen3-8B、LangGraph、Milvus **3.0.2** 和独立 MCP **2.3.0** 完成合格退款。以 `--group safety --investigation-probe`、`--group outcomes --investigation-probe` 通过 **11** 和 **19** 个故障与竞争场景。发送前可从独立连接读到 `pending` 和对应核验文件，MCP 写请求和只读核对时无活动业务事务。两会话竞争只发生一次写调用，只产生一条退款。

真实 INSERT 后提交前故障回滚，提交关闭且无退款。COMMIT 成功后断连由只读核对确认原单，未完成提交时断连继续拦截。重开原会话和另开会话均只读核对，写调用为零。独立 MCP 的四个工具描述、注解和 Schema 与基线一致。另复验会话原文与命名锁、跨进程 Checkpoint 保存与续跑。隔离库和集合均已核实清理。

`test/test_refund_submission.py` 与相关回归 **30 通过**，最终全仓 **103 通过**。mypy、编译、依赖与 Git 差异检查通过。四项验收通过，`Status` 沿用仓库配置的 `ready-for-agent`。故障组的模型调查使用确定性只读探针，正常业务组使用真实模型。 [验收证据与复跑命令](../../../evidence/mysql-refund-03.md)
