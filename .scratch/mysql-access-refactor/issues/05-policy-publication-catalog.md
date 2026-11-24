# 05 迁移政策发布目录

Status: ready-for-agent

Blocked by: [02 退款会话记录](02-conversation-storage.md)

## 目标

迁移 MCP 读取和独立发布命令使用的政策发布目录，保留政策版本快照、同版本发布互斥与版本切换行为。参见 [ADR-0010](../../../docs/adr/0010-refund-policy-publication-and-evidence.md)。

## 范围

迁移目录查询、发布尝试和生效切换的业务 SQL。MCP 和发布命令传入已配置的目录 Engine，复用现有目录模型、接口和业务结果。独立 PyMySQL 初始化继续负责目录表和初始目录头。Milvus 的向量写入、回读和检索校验按现有流程执行。

## 验收标准

- [x] MCP 按当前修订或指定历史修订读取政策版本快照，错误处理和工具 Schema 一致。自定义 `policy.catalog_database` 和发布命令的 `--config` 指向预期隔离库。
- [x] 同物理连接维持版本命名锁，第二个同版本发布被拒绝。锁在正常退出、失败和重试路径释放，释放无法确认时丢弃物理连接。
- [x] Milvus 写入前已提交待确认状态，完成后另行提交完成状态。网络读写和检索校验期间没有未提交的 MySQL 事务。切换时用短事务锁住目录头，原子提交版本首个生效修订与目录头推进。
- [x] 并发版本切换冲突、Milvus 部分写入或校验失败、提交后回执丢失、同版本重试和历史修订均与当前行为一致。失败之后已生效的旧版本仍可提供检索。

## 验证方式

运行 `test/test_policy_catalog.py`、`test/verify_policy_mcp.py` 和 `test/verify_policy_catalog.py --config <验收配置> --evidence <验收路径>`。复用 [工单 16 验收](../../../evidence/policy-16.md) 的并发、重试与切换故障场景，使用隔离 MySQL 库与 Milvus 集合。

## Comments

2026-11-24，工单 05 实施和验收。核对工单 01、02 的代码、配置、验收记录及专题施工顺序。目标 MySQL 首先执行 `SELECT VERSION()`，返回 **8.0.46**。Milvus 实测 **3.0.2**，MCP SDK 为 **2.3.0**。改造前相关回归 **46 项通过**，真实独立 MCP 与原版目录联验退出 0。

- 政策目录通过 SQLAlchemy Core **2.0.58** 与 PyMySQL **1.2.4** 访问，Service 管理同物理连接的版本锁及分段事务，命令仍通过独立 PyMySQL 管理路径初始化。经 `python -X utf8 test/verify_policy_mcp.py --config <已发布的隔离配置>` 的正常及 `--expect-failure` 模式实测，工具发现、只读 Schema、认证与失败回执一致。
- `python -X utf8 test/verify_policy_catalog.py --config <验收配置> --evidence evidence/mysql-policy-05-integration.jsonl` 退出 0。独立发布命令写入自定义政策目录库，Host 经独立 MCP 在新旧快照间得到预期版本。真实 MySQL 验证同版本拒绝、跨事务持锁及未确认解锁时丢弃连接。独立连接观察到 Milvus 操作前后的已提交状态，目录头更新注入错误 1644 后两条切换写入一起回滚。
- 真实部分写入、回读失败、提交后回执丢失、同版本重试及并发切换均符合基线。故障期间旧版本可继续检索，恢复后仅成功的一项并发发布推进目录。最终相关回归 **23 项通过**，全仓 **107 项通过**，mypy、编译、依赖、独立导入及 Git 差异检查通过。数据库和集合均已清理。 [验收记录与命令](../../../evidence/mysql-policy-05.md) · [联验回执](../../../evidence/mysql-policy-05-integration.jsonl)

四项验收全部通过。仓库未配置完成状态，`Status` 保留 `ready-for-agent`。
