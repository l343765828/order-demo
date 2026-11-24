# MySQL 业务访问层重构

## 目标与基线

将现有 MySQL 业务查询和写入迁移到 SQLAlchemy 2.x Core。保留显式 SQL、PyMySQL 驱动和 Pydantic v2 模型，统一由 Service 管理业务连接和事务。迁移沿用现有 MySQL 表结构及已有数据。以当前已交付的行为作为回归基线，覆盖本人查单、退款会话记录、模拟退款与退款结果核对、退款提交记录、消费者跨会话记忆、政策发布目录和种子装载。建议本专题验收后接续旧业务工单 08、09。 [原施工总览](../refund-agent-all-tech-demo/task-overview.md) · [退款验收](../../evidence/refund-07.md) · [偏好验收](../../evidence/preferences-11.md)

第一张工单先盘点直接使用 PyMySQL、DBUtils、连接池、游标、提交和回滚的入口，区分业务访问、初始化、第三方 Checkpoint 与测试。改造前在当前检出上建立可复跑基线，历史验收记录用于选择回归场景。

## 代码与配置边界

- 入口读取现有 JSON 配置后创建并复用 Engine，退出时调用 `dispose()`。主库取 `mysql.database`，默认 `order_demo`。政策发布目录取 `policy.catalog_database`，支持指向独立库。现有参数和默认配置路径继续可用，含 `publish_policy.py --config`。
- 公共连接工厂使用 `mysql+pymysql` 和 `URL.create()`，保留 UTC、UTF-8，连接池容量以现有每进程 5 个连接为基线。开启 `pool_pre_ping`，按用途在 `config/` 集中配置回收与超时常量，数据库错误和日志保护凭据与 SQL 参数。
- Repository 文件放在 `repositories/<业务名>_repository.py`，业务 SQL 放在 `repositories/sql/<业务>/`。公共加载器从固定根目录读取 UTF-8 SQL，通过 `lru_cache` 复用 `TextClause`，SQL 路径由应用指定，值一律使用命名参数绑定。
- Service 放在 `implementations/<业务>/`，入口负责组装。已有业务接口和显式实现关系沿用仓库约定。Repository 收到 `Connection` 后执行 SQL，使用 `result.mappings()` 读取记录，再通过 `model_validate()` 映射为 Pydantic 对象。复用现有持久化实体，按实际 JOIN 或投影增加所需具名模型，MCP 请求和响应继续使用现有协议模型。
- `manage.py init` 和政策目录初始化沿用独立 PyMySQL 管理路径，保持已有 DDL 与可重复初始化行为。`manage.py seed` 的业务写入迁入 Service。LangGraph `PyMySQLSaver` 继续使用其原生 PyMySQL 连接，保留现有 Checkpoint 数据结构、节点和恢复入口。

## 事务和业务契约

| 路径 | 事务与可观察行为 |
| --- | --- |
| 本人查单 | Service 借出只读连接，读完归还。SQL 同时限定订单号与认证消费者。结果沿用 `FOUND`、`UNAVAILABLE`、`FAILED`，退款状态取关联记录。 |
| 退款会话记录 | 同连接持有原命名锁，核验归属，按序读写。用户原文在调用流程前提交，助手原文在回复生成后提交。每次追加原文及更新会话时间使用同一短事务。 |
| 退款提交与结果核对 | Host 提交记录持久化后再发送受控 MCP 请求。MCP 在单次事务内完成锁定和退款写入。明确回滚与提交回执不明分别处理，按既有规则核对结果。 |
| 消费者跨会话记忆 | 查询、更新、删除沿用现有消费者边界和状态。每次变更由独立短事务执行。 |
| 政策发布目录 | 同版本发布锁跨 Milvus 操作。先提交写入待确认状态，写入完成后再提交完成状态，校验通过后以独立短事务推进目录修订与生效版本。 |
| 种子装载 | 种子订单和对应退款在同一事务内装载。区分本次新插入与已有订单，已有订单的业务事实及退款状态保持原样。 |

常规写入使用 Service 持有的事务上下文，参与同一写入的 Repository 共享一条连接。保留退款提交时对回执不明的业务映射。会话锁和政策版本锁使用同一物理连接取得与释放，读操作及时收束自动开启的事务。进入模型、MCP 或 Milvus 外部调用前结束当前数据库事务。异常路径无法确认释放命名锁时，丢弃该物理连接。 [MySQL 命名锁](https://dev.mysql.com/doc/refman/8.0/en/locking-functions.html) · [SQLAlchemy 连接与事务](https://docs.sqlalchemy.org/en/20/core/connections.html#using-transactions)

四个现有 MCP 工具继续使用当前名称、输入输出 Schema、认证和日志字段。`get_order`、`search_policy`、`get_refund_result` 保持只读，`submit_mock_refund` 继续要求独立写凭据与当前受控业务入口。沿用同步数据库调用的线程调度。消费者归属、退款授权和业务规则以当前实现及对应 ADR 为准。 [MCP 边界](../../docs/adr/0006-refund-agent-mcp-service-boundary.md) · [退款结果待核对](../../docs/adr/0007-refund-unconfirmed-dispatch.md) · [政策发布目录](../../docs/adr/0010-refund-policy-publication-and-evidence.md) · [访问层决策](../../docs/adr/0011-mysql-service-transaction-boundary.md)

## 施工顺序

| 工单 | 验收范围 | 前置 |
| --- | --- | --- |
| [01 基础设施与本人查单](issues/01-mysql-core-and-order-query.md) | 连接工厂、SQL 加载、查单 Repository 和 MCP 适配 | 无 |
| [02 退款会话记录](issues/02-conversation-storage.md) | 并发互斥、原文提交、Checkpoint 恢复 | 01 |
| [03 模拟退款与退款提交记录](issues/03-refund-transactions.md) | 核对和执行、事务与回执不明、Host 提交记录 | 02 |
| [04 消费者跨会话记忆](issues/04-consumer-preferences.md) | 长期偏好读取、更新和删除 | 02 |
| [05 政策发布目录](issues/05-policy-publication-catalog.md) | 目录快照、发布锁、Milvus 与切换顺序 | 02 |
| [06 种子装载与清理](issues/06-seed-and-cleanup.md) | 幂等种子、回滚、依赖清理、全量交付 | 03、04、05 |

建议按编号推进。每张工单先完成对应回归，再推进依赖它的工单。分阶段替换期间，尚未迁移的调用方沿用原入口。

## 验收与交付

测试和验证脚本位于 `test/`，验收记录位于 `evidence/`。真实写入、回滚、并发和故障注入均使用隔离 MySQL 库，政策联验使用配套隔离集合。回滚用例通过两次真实写入与故障注入核对数据库最终状态，验收资源仅清理本轮创建的范围。涉及写入的旧验证脚本若固定连接业务库，先适配为使用临时 JSON 配置指定的隔离库，再复跑相关场景。缺少外部环境时运行可独立执行的测试，记录未运行项及原因。

最终交付列出变更文件和目录、旧入口到新实现的映射、Service 与 Repository 职责、各事务起止和回滚条件、会话锁及 Checkpoint 处理、依赖变化、实测命令和结果、保留的 PyMySQL 操作及其用途。部署与使用说明随最终阶段更新。
