# MySQL 业务事务由 Service 管理

2026-11-24，决定以 SQLAlchemy Core 和显式 SQL 迁移订单、退款、会话、长期偏好、政策发布目录及种子写入。与继续在各业务模块直接操作 PyMySQL 游标相比，统一的连接生命周期和 Service 事务边界让多个 Repository 可以共享一次提交或回滚，显式 SQL 则保留消费者归属过滤、命名锁和行锁的可见性。迁移成本包括保留会话原文的提交时机、政策发布的分段持久化及退款提交回执不明的处理，并为这些行为在隔离库补做回归。

Service 管理 SQLAlchemy 连接和事务，Repository 使用传入连接执行 SQL。会话锁和政策版本锁跨短事务，第三方 Checkpoint 和独立 DDL 初始化继续使用直接 PyMySQL 接入。存储位置和应用间边界沿用 [ADR-0004](0004-refund-agent-storage-boundaries.md) 与 [ADR-0006](0006-refund-agent-mcp-service-boundary.md)。范围、顺序和验收见 [MySQL 业务访问层重构](../../.scratch/mysql-access-refactor/spec.md)。
