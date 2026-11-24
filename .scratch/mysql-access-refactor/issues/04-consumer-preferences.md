# 04 迁移消费者跨会话记忆

Status: ready-for-agent

Blocked by: [02 退款会话记录](02-conversation-storage.md)

## 目标

在现有 `ManagePreferences` 入口迁移显式长期偏好的查询、设置和删除。保留同消费者跨会话复用及重启恢复行为。参见 [既有长期偏好验收](../../../evidence/preferences-11.md)。

## 范围

新增对应的 Repository 与独立 SQL，复用现有偏好模型。业务接口的实现继续位于所属业务目录，Service 处理连接、事务及对业务结果的映射。保留现有 CLI 和工具调用方。

## 验收标准

- [x] 读取、更新、删除均限定认证消费者，对外状态、回复及缺失时行为一致。
- [x] 偏好写入或删除的事务在返回结果前结束。数据库故障沿用业务失败语义并归还连接。
- [x] 同消费者新会话读取当前有效偏好，其他消费者隔离。删除或更正后，原始消息和旧 Checkpoint 不会让旧偏好重新生效。

## 验证方式

运行 `test/test_preferences.py`，在隔离库复跑 `test/verify_preferences_cli.py` 的读取、修改、删除、归属和重启场景，记录结果至 `evidence/`。

## Comments

2026-11-24 实施与验收。核对专题 01 至 03 的代码、配置和验收证据，直接依赖 02 已验收。目标实例先执行 `SELECT VERSION()`，实测 **MySQL 8.0.46**。改动前全仓 pytest **103 项通过**，偏好与 Core **26 项通过**，旧 CLI 在隔离库完成 **19 轮**真实联验。

CLI 通过同一 SQLAlchemy Core **2.0.58** Engine 注入 `ManagePreferences` 的 MySQL 实现。Repository 使用独立的参数化 SQL 和既有 `ConsumerPreference` 模型，查询归还连接后返回，写入和删除提交后返回。

- `python -X utf8 test/verify_preferences_cli.py --config $config --legacy-root $legacy --evidence evidence/mysql-preferences-04-integration.json` 退出 0。真实 Qwen3-8B、MySQL 和独立 MCP **2.3.0** 下，新版接续旧版写入的偏好与 Checkpoint。三个会话的 19 轮状态、回复、有效详略和恢复标志与旧基线一致，38 条原文完整，8 次查单及实付 19900 分一致，退款写调用为 0。
- `python -X utf8 test/verify_preferences_storage_mysql.py --config $config` 退出 0。双消费者隔离，返回成功前已提交。更新和删除执行后在提交前分别触发真实 MySQL 错误，两次回滚并返回 `failed`，全部调用归还连接。CLI 的真实写入和读取故障场景也通过，隔离库与独立进程均已清理。
- `python -X utf8 -m pytest -q --tb=short test/test_preferences.py` **21 项通过**，全仓 **103 项通过**。会话、跨进程 Checkpoint、mypy、编译、依赖、独立导入和 Git 差异检查通过。Checkpoint 有已有的反序列化注册警告，实际恢复成功。

三项验收均通过，`Status` 保留仓库配置的 `ready-for-agent`。 [验收记录](../../../evidence/mysql-preferences-04.md) · [逐轮回执](../../../evidence/mysql-preferences-04-integration.json)
