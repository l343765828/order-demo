# 07: 凭当前授权和持久政策依据提交一次模拟退款

**Status:** ready-for-agent

**Blocked by:** [16 政策发布目录与多文档版本切换](16-publish-policy-catalog-revisions.md)。政策读取与核验由 [15](15-remove-host-policy-file-dependency.md) 交付。

## 目标

消费者明确要求办理本人合格订单时，经真实接口收到一次模拟退款结果。缺少授权、适用政策依据或持久证据时，新提交停在写工具之前。

## 范围

接通当轮明确指定订单的办理、固定 Host 复核与发送节点、固定只读结果核对、MCP Server 退款事务及确定性结果模板。多轮授权、跨会话回执与维护恢复分别由 08、09、10 扩展验收。此票交付的写入路径本身就对未决提交拦截，并使用 03 验证的单次受控发送。

## 涉及的契约

- `AuthorizedRefundIntent`、`PolicyEvidence`、`refund_dispatches`；提交引用本会话明确授权的原始用户消息，消费者和订单由可信状态校验。
- `get_order`、`get_refund_result`／`RefundLookupResult`（`found`、`not_found`、`unavailable`）、`submit_mock_refund`／`SubmitMockRefundRequest(order_id, idempotency_key)`、`RefundResult`（`simulated_succeeded`、`already_refunded`、`denied`、`failed`、`unconfirmed`）。服务端独立校验受控执行身份，模型和只读适配器没有写凭据。
- 本次办理按政策版本快照核验正式条款，代码按可信订单事实确定资格。最终政策核验事件先持久保存，包含 `trace_id`、幂等键、实际采用的文档编号／版本／条款、原文片段和对应的检索事件 ID。确认后以同一标识保存带授权证据的提交，确认后才调用 MCP 写入。 [数据库设计](../database-design.md#关系与退款写入) · [MCP 接入](../mcp-integration.md) · [ADR-0008](../../../docs/adr/0008-refund-authorization-per-dispatch.md) · [ADR-0009](../../../docs/adr/0009-refund-policy-evidence-before-dispatch.md)

## 验收标准

- [x] 从 CLI 说“如果能退，就帮我退 DEMO-1001”，看到 LangGraph、Plan-and-Execute、官方 DeepAgent、ReAct、真实 RAG 和跨进程 MCP 的关联事件。办理从向量召回开始只查 RF。实际成功记录可按幂等键和 `trace_id` 追溯到本次授权及已持久化的政策核验事件，事件、提交、调用顺序可查。CLI 按真实结果展示订单、¥199.00 和模拟退款单号，写入后无模型改写。
- [x] 用未支付、已发货、他人订单及复核时状态变化的样本验证拒绝或统一不可操作提示。请求自定金额、账户或渠道不改变整单原路规则，唯一实付来源是 MySQL 中的整数分。`DEMO-1003` 返回原单与金额，不新增退款记录，不要求重新检索政策。
- [x] 独立 MCP Server 拒绝调查身份、缺失提交、身份与会话不符、订单与提交不符及授权已撤销的首次写入。合格写入在一个事务中先锁提交、再锁订单，复核 `pending`、授权、归属、已有退款及订单状态；只允许一笔成功退款。
- [x] 政策重排失败、证据不足／引用非法／与订单事实矛盾，均不创建新的退款提交或写调用。分别注入最终核验事件保存失败、确认丢失，以及后续提交保存失败／无法确认，均不发起退款写调用。核验事件失败和重排失败期间，查单、读取原退款结果、通过 `get_refund_result` 只读核对旧提交仍可用。
- [x] 明确业务拒绝、明确失败、结果未确认和成功使用不同结果契约；发送结果不明时先读原结果，仍不明则保留未决拦截且本轮发送不超过一次。写入后的展示故障保留真实结果，下一次办理可读取原结果。

## 验证方式

使用真实 CLI、Qwen、MCP、MySQL 和政策检索链运行合格种子与负例。捕获实际 MCP 请求、应用事件持久化顺序和库内记录。用调查凭据直接尝试写工具，另注入重排、证据落盘、提交保存、状态变化和模板故障，核对持久记录、回执与调用计数。

## Comments

2026-11-23

- 已按工单 16 的版本快照接通本轮明确授权、固定复核、政策事件持久化、提交持久化和单次受控发送。目标先执行 `SELECT VERSION()`，返回 MySQL **8.0.46**。联验 Milvus **3.0.2**、MCP **2.3.0**、DeepAgent **0.7.38**、LangGraph **1.2.22**。默认库有业务表，政策发布目录在隔离库中初始化并通过发布入口激活。
- 执行 `python -X utf8 test/verify_refund_submission.py --config <临时配置> --evidence <输出路径>`，记录正常 CLI、金额及订单守卫。DEMO-1001 成功写一笔 ¥199.00，原始授权消息、正式条款、最终政策事件、提交与退款单可关联。DEMO-1003 返回原单。独立服务权限及并发唯一性通过。
- 分别以 `--group safety --investigation-probe` 和 `--group outcomes --investigation-probe` 在隔离资源中验证故障。政策或提交保存失败及确认丢失时均无退款写调用，已有结果和只读核对可用。丢失执行回执后每轮最多写一次，查无原单时继续拦截。确定性模板故障后仍可读取真实原单。两组完整模型调查请求曾超时，仍待全量复跑。故障探针通过同一 CLI、独立 MCP、真实 MySQL 和 RAG 验证上述行为。
- `python -m pytest -q --tb=short` 94 项通过，相关 mypy 检查 37 个源文件通过。已确认隔离资源清理。[验收记录](../../../evidence/refund-07.md) · [结构化回执](../../../evidence/refund-07-integration.jsonl)

2026-11-23 补验

- 工单 11 完成后，使用固定代码快照依次执行 `python -X utf8 test/verify_refund_submission.py --config <临时配置> --evidence <输出路径> --group safety` 和同命令的 `--group outcomes`。两组依次通过 11 和 8 个用例，退出码均为 0。实际使用 Qwen、官方 DeepAgent、ReAct、独立 MCP、MySQL 8.0.46 和真实政策检索。首轮记录中的完整模型链故障回归已补齐，两个隔离库及 Milvus 集合已清理。全仓 pytest 98 项通过。[复跑证据](../../../evidence/refund-07-full-chain.jsonl)
