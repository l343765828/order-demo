# 11: 在聊天中查看、更正和删除显式长期偏好

**Status:** ready-for-agent

**Blocked by:** [04 本人查单](04-read-own-order-end-to-end.md)。

## 目标

消费者可以在聊天中维护明确指定的稳定偏好，在后续会话使用当前有效值，删除或覆盖的值不会重新生效。

## 范围

由意图节点解析偏好命令，固定应用节点管理 MySQL 当前偏好，CLI 根据持久化结果回复。同句包含业务与偏好请求时分别保留意图及实际结果。同步验证原文与 checkpoint 读取边界，摘要后的验收由 12 补齐。

## 涉及的契约

- `ConversationDecision.memory_request`、`PreferenceCommand`（`list`、`set`、`delete`）、`PreferenceResult`、`consumer_preferences(consumer_id, preference_key)`；只允许受控偏好键，消费者来自可信上下文。
- 当前 MySQL 记录决定有效偏好；临时表达要求只作用于当轮。偏好只影响允许的表达方式，固定节点管理持久化写入。 [主设计](../spec.md#6-输入输出与状态契约) · [存储与记忆](../storage-memory.md#61-长期偏好管理的执行边界) · [ADR-0004](../../../docs/adr/0004-refund-agent-storage-boundaries.md)

## 验收标准

- [x] 用 CLI 明确要求记住一项稳定偏好后，另一会话可查看或使用。更正同类偏好后只使用最新值，删除后重开 Host／会话也无法从旧聊天或 checkpoint 恢复旧值。原始聊天完整保留。
- [x] 普通聊天和当轮临时表达要求不新增或覆盖长期偏好。身份不可由模型指定，不得查看他人偏好，查询只返回当前有效偏好。偏好不改变订单、授权、资格、金额或政策判定。
- [x] 注入 MySQL 写入或读取失败，CLI 准确呈现实际命令结果，不宣称已记住、更改或删除。调查 Agent 没有任意偏好写入、SQL 或退款权限。

## 验证方式

使用两个会话对同一属性做记住、查看、更正、删除和重启复读，检查 MySQL 记录、上下文有效值以及保留的原文。用另一个消费者夹具和数据库故障验证隔离与失败语义。

## Comments

2026-11-23 实施与验收。04 的真实查单及 checkpoint 验收已满足前置条件。连接目标实例后先执行 `SELECT VERSION()`，结果为 **8.0.46**。已实现 `response_detail` 的记住、查看、更正和删除。意图节点从本轮原文解析明确命令，固定节点按可信身份持久化。早期扩展 Qwen Schema 的探针遇到 422，最终沿用原有业务 Schema，由应用组合 `ConversationDecision.memory_request`。

- `.venv/Scripts/python.exe -X utf8 test/verify_preferences_cli.py --config $issue11Config --evidence evidence/preferences-11-integration.json` 退出 0。实际使用 Qwen3-8B、MySQL 8.0.46、LangGraph 1.2.22、PyMySQLSaver 3.0.0 和独立 MCP 2.3.0。19 轮 CLI 验证了跨会话覆盖和删除、重启后回读、临时详略、消费者隔离与真实数据库故障。38 条原始消息完整保留，8 次查单取得同一结果及 19900 分，退款写入为 0。隔离库和 MCP 进程已清理。
- `.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short` **98 项通过**。mypy、compileall、pip check、独立导入和 Git 差异检查通过。自动化测试覆盖政策原文分离、旧 checkpoint 禁止重放、非授权指令边界及调查工具权限。

本票三项验收通过。真实政策检索和模拟退款写入沿用其他工单的联验证据，本票实测范围为只读查单及偏好读写。摘要后的验证由工单 12 完成。仓库未配置完成状态，保留 `ready-for-agent`。[验收记录](../../../evidence/preferences-11.md) · [逐轮回执](../../../evidence/preferences-11-integration.json)
