# 07: 凭当前授权和持久政策依据提交一次模拟退款

**Status:** ready-for-agent

**Blocked by:** [06 政策与资格咨询](06-answer-policy-and-order-eligibility.md)。

## 目标

消费者明确要求办理本人合格订单时，经真实接口收到一次模拟退款结果。缺少授权、适用政策依据或持久证据时，新提交停在写工具之前。

## 范围

接通当轮明确指定订单的办理、固定 Host 复核与发送节点、固定只读结果核对、MCP Server 退款事务及确定性结果模板。多轮授权、跨会话回执与维护恢复分别由 08、09、10 扩展验收。此票交付的写入路径本身就对未决提交拦截，并使用 03 验证的单次受控发送。

## 涉及的契约

- `AuthorizedRefundIntent`、`PolicyEvidence`、`refund_dispatches`；提交引用本会话明确授权的原始用户消息，消费者和订单由可信状态校验。
- `get_order`、`get_refund_result`／`RefundLookupResult`（`found`、`not_found`、`unavailable`）、`submit_mock_refund`／`SubmitMockRefundRequest(order_id, idempotency_key)`、`RefundResult`（`simulated_succeeded`、`already_refunded`、`denied`、`failed`、`unconfirmed`）。服务端独立校验受控执行身份，模型和只读适配器没有写凭据。
- 最终政策核验事件先持久保存，包含 `trace_id`、幂等键、采用的文档编号／版本／条款、原文片段和检索事件 ID。确认后以同一标识保存带授权证据的提交，确认后才调用 MCP 写入。 [数据库设计](../database-design.md#关系与退款写入) · [MCP 接入](../mcp-integration.md) · [ADR-0008](../../../docs/adr/0008-refund-authorization-per-dispatch.md) · [ADR-0009](../../../docs/adr/0009-refund-policy-evidence-before-dispatch.md)

## 验收标准

- [ ] 从 CLI 说“如果能退，就帮我退 DEMO-1001”，看到 LangGraph、Plan-and-Execute、官方 DeepAgent、ReAct、真实 RAG 和跨进程 MCP 的关联事件。办理从向量召回开始只查 RF。实际成功记录可按幂等键和 `trace_id` 追溯到本次授权及已持久化的政策核验事件，事件、提交、调用顺序可查。CLI 按真实结果展示订单、¥199.00 和模拟退款单号，写入后无模型改写。
- [ ] 用未支付、已发货、他人订单及复核时状态变化的样本验证拒绝或统一不可操作提示。请求自定金额、账户或渠道不改变整单原路规则，唯一实付来源是 MySQL 中的整数分。`DEMO-1003` 返回原单与金额，不新增退款记录，不要求重新检索政策。
- [ ] 独立 MCP Server 拒绝调查身份、缺失提交、身份与会话不符、订单与提交不符及授权已撤销的首次写入。合格写入在一个事务中先锁提交、再锁订单，复核 `pending`、授权、归属、已有退款及订单状态；只允许一笔成功退款。
- [ ] 政策重排失败、证据不足／引用非法／与订单事实矛盾，均不创建新的退款提交或写调用。分别注入最终核验事件保存失败、确认丢失，以及后续提交保存失败／无法确认，均不发起退款写调用。核验事件失败和重排失败期间，查单、读取原退款结果、通过 `get_refund_result` 只读核对旧提交仍可用。
- [ ] 明确业务拒绝、明确失败、结果未确认和成功使用不同结果契约；发送结果不明时先读原结果，仍不明则保留未决拦截且本轮发送不超过一次。写入后的展示故障保留真实结果，下一次办理可读取原结果。

## 验证方式

使用真实 CLI、Qwen、MCP、MySQL 和政策检索链运行合格种子与负例。捕获实际 MCP 请求、应用事件持久化顺序和库内记录。用调查凭据直接尝试写工具，另注入重排、证据落盘、提交保存、状态变化和模板故障，核对持久记录、回执与调用计数。
