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

- [ ] 用 CLI 明确要求记住一项稳定偏好后，另一会话可查看或使用。更正同类偏好后只使用最新值，删除后重开 Host／会话也无法从旧聊天或 checkpoint 恢复旧值。原始聊天完整保留。
- [ ] 普通聊天和当轮临时表达要求不新增或覆盖长期偏好。身份不可由模型指定，不得查看他人偏好，查询只返回当前有效偏好。偏好不改变订单、授权、资格、金额或政策判定。
- [ ] 注入 MySQL 写入或读取失败，CLI 准确呈现实际命令结果，不宣称已记住、更改或删除。调查 Agent 没有任意偏好写入、SQL 或退款权限。

## 验证方式

使用两个会话对同一属性做记住、查看、更正、删除和重启复读，检查 MySQL 记录、上下文有效值以及保留的原文。用另一个消费者夹具和数据库故障验证隔离与失败语义。
