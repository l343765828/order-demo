# 退款提交携带本次办理的授权证据

2026-11-19，用户确认发送前由当前会话管理办理意图，准备发送时把本次授权的会话与来源消息一起保存到 `refund_dispatches`，因此首版省去独立的 `refund_authorizations` 表。一次授权证据对应一次提交，MCP 可在同一提交行上核验授权撤销和执行状态，代价是发送前的办理意图随当前会话管理。旧提交关闭后，原会话恢复待办时引用消费者本次明确继续的消息，新会话受理新申请时引用本会话的明确办理指令。两种情况都重新校验并使用新幂等键，受 [ADR-0007](0007-refund-unconfirmed-dispatch.md) 的未决拦截规则约束。详见[数据库表结构](../../.scratch/refund-agent-all-tech-demo/database-design.md)和 [MCP 接入设计](../../.scratch/refund-agent-all-tech-demo/mcp-integration.md)。
