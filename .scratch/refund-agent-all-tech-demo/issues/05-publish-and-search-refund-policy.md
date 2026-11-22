# 05: 发布退款政策并经真实 MCP 检索当前版本

**Status:** ready-for-agent

**Blocked by:** [03 模型与 MCP 协议](03-verify-model-and-mcp-protocols.md)。

## 目标

维护人员可显式发布退款政策，调用者能经真实 MCP 获得带来源的当前版本候选；重排不可用时能识别本次检索失败。

## 范围

交付政策入库命令、MCP Server 的 `search_policy` 和可独立运行的 MCP 验收入口。维护命令只管理本演示政策集合。Host 的咨询解答与最终政策核验由 06、07 完成。

## 涉及的契约

- 专用集合 `order_demo_refund_policy` 的必填字段为 `chunk_id VARCHAR(64)` 主键且 `auto_id=false`、`document_id VARCHAR(64)`、`title VARCHAR(256)`、`version VARCHAR(32)`、`clause_id VARCHAR(32)`、`entry_type VARCHAR(16)`、`text VARCHAR(4096)`、`embedding FLOAT_VECTOR(1024)`。字符串长度按 UTF-8 字节，`chunk_id` 为规范化文档编号、版本及条目编号的 SHA-256。
- `SearchPolicyRequest`／`PolicySearchResult` 携带受限检索意图或等价的受控过滤语义以及检索状态、候选原文与来源。集合、文档与发布版本、维度、过滤及索引配置由服务端控制。由 Host 确定咨询／正式条款范围，模型不得指定任意过滤参数或放宽办理检索。
- `BAAI/bge-m3` 文档和查询同规则 L2 归一化。Milvus `FLAT` + `IP`，过滤后召回最多 8 条，经 `BAAI/bge-reranker-v2-m3` 重排后返回前 3 条。 [存储定稿](../storage-memory.md#5-milvus-的接入边界) · [政策知识源](../../../policy/refund-policy.md) · [主设计](../spec.md#5-rag-文档与检索流程) · [ADR-0004](../../../docs/adr/0004-refund-agent-storage-boundaries.md)

## 验收标准

- [x] 发布 `DEMO-REFUND-POLICY` 1.0 后检查实际 schema 和数据，当前版本恰有 14 条 `policy`、7 条 `faq`。重复入库行数不增；只有问答文本、无出处字段的 FAQ 可入库。独立的历史版本样本保留且不出现在当前版本结果。
- [x] 抓取带来源的 MCP 实际检索结果，确认咨询可以召回 FAQ 与 RF，正式条款模式在向量召回阶段已排除 FAQ。能核对预过滤、至多 8 个重排输入和重排后至多 3 个输出的顺序及对应原文。
- [x] 文档和查询编码均验证 1024 维、有限值及非零模，归一化后模长约为 1。畸形向量、Milvus 不可用、重排失败或超时均返回检索失败，无未重排候选或内存索引回退。
- [x] 未认证请求被拒绝。政策工具不直接查询消费者订单，不接受任意集合或数据库配置，也不产生授权和退款。发布命令独立于消费者工具和演示数据重置。

## 验证方式

用真实 BGE-M3、Milvus 和重排服务跑入库与 MCP 往返，检查集合 schema、版本和行数。用同义问法、未覆盖问题、历史版本混入、故障代理及计数探针固定候选与失败预期，保留检索事件 ID 与来源。


## Comments

2026-11-20 实施并验收：独立发布命令、政策集合、受认证 MCP 检索、Host 固定检索范围的工具适配器及验证入口已交付。实际 BGE-M3、Milvus、reranker 与独立 MCP 进程完成正常和故障路径验证；14 条 policy、7 条 FAQ，重复发布不增行，历史无出处 FAQ 保留且被当前版本过滤。真实检索关联事件、召回和重排原文见 [完整证据](../../../evidence/policy-05.md) 和其 JSONL。零向量、重排失败/超时、Milvus 不可用均返回失败和空候选；Host 工具不允许模型指定模式。未覆盖问题可返回相似候选，06/07 仍须判断政策依据充分性。本目录测试 32 项通过，mypy、compileall 和 pip check 通过；全仓库 pytest 收集有 10 项既有模块冲突或缺依赖错误，详见证据。验收结论：本票四项通过；仓库未配置完成状态，保留 `ready-for-agent`。

2026-11-20 配置修正：遵照用户要求，政策服务地址和凭据改从 JSON 的 `policy` 配置读取，不再读取政策环境变量；发布与验收脚本支持 `--config`。临时配置下真实发布、Milvus、MCP 和故障路径重测通过，`order-demo/test` 36 项通过，详情见 [验收记录](../../../evidence/policy-05.md)。无 `policy` 的原查单配置仍可加载，但政策检索和发布需先填写该配置。
