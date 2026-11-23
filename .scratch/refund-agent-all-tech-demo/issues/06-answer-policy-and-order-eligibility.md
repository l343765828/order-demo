# 06: 回答带正式依据的政策咨询与个人订单资格咨询

**Status:** ready-for-agent

**Blocked by:** [04 本人查单](04-read-own-order-end-to-end.md)、[05 政策发布与检索](05-publish-and-search-refund-policy.md)。

## 目标

消费者可以问通用退款政策或具体订单能否退款，得到基于当前政策和实际订单状态的解释，咨询全程只读。

## 范围

将 PolicyAgent、只读 `retrieve_policy` 计划和 Host 证据核验接入 CLI。覆盖 FAQ 支持条件、RF 补查、具体订单资格解释、意图区分及政策不可用提示。已有 04 的纯查单入口在政策故障时继续可用。

## 涉及的契约

- `ConversationDecision` 区分 `policy_question`、`order_question`、`refund_request` 和 `unclear`。含糊时追问，明确条件式办理语句归办理，由 07 接入执行。
- `search_policy`／`PolicySearchResult`、`get_order`／`OrderQueryResult`、`TaskObservation`、`PolicyEvidence`。Host 核验本次检索事件、文档／版本／条款、原文和适用性，必要时在预算内补查正式条款。 [主设计](../spec.md#5-rag-文档与检索流程) · [政策知识源](../../../policy/refund-policy.md) · [MCP 接入](../mcp-integration.md)

## 验收标准

- [x] 通用咨询可检索 FAQ 与 RF。第一名 FAQ 确实回答问题、且本次检索中有当前正式条款支持时，接受 FAQ 答案并附文档名及 RF 编号。不相关的首条 FAQ、无正式出处或过期出处不能作为答案依据。
- [x] “DEMO-1001 能退吗？”经真查单和政策检索，仅解释资格；用 `DEMO-1002`、`1004`、`2001` 覆盖未付、已发货和他人订单，解释符合真实状态及归属边界。资格咨询不创建办理待办或授权。
- [x] 同表对照“能退 DEMO-1001 吗”“如果能退，就帮我退 DEMO-1001”“这单退款？”分别进入咨询、明确办理意图和澄清分支。只说“能退吗”或先问资格、再补订单号，均无退款提交或写工具调用。
- [x] 未覆盖问题按证据不足处理。意外的条款 ID、版本、摘录或与事实冲突的解释无法通过核验。实际重排超时／失败时回复暂时无法核实，纯查单仍返回真实结果。

## 验证方式

从 CLI 跑通用问题、同义问法、已知与未知问题及订单资格对照集，关联真实 MCP 工具事件、检索候选及最终引用。对 FAQ 语义或出处不匹配及重排失败分别注入故障，检查会话授权、提交表和退款写调用计数。

## Comments

2026-11-20 本轮部分实施与验证：先连接目标 MySQL 执行 `SELECT VERSION()`，结果为 **8.0.46**，`order_demo.orders` 有 5 条种子。临时 JSON 配置由现有部署配置生成，验证后删除，未输出凭据。

- 直接依赖 05：使用真实 BGE、Milvus、重排和独立 MCP 进程运行 `.venv/Scripts/python.exe order-demo/test/verify_policy_mcp.py --config <临时文件>`，退出码 0，咨询与正式条款检索及未覆盖问题候选检查通过；检索成功不等于证据充分。
- 直接依赖 04：现有验收脚本的工具发现断言仍只允许 `get_order`，与 05 已增加的 `search_policy` 不符；更新断言后运行 `.venv/Scripts/python.exe order-demo/test/verify_order_cli.py --config <临时文件>`，退出码 0。真实 Qwen、独立 MCP 和 MySQL 查得 DEMO-1001 实付 19900 分；同会话重启恢复、其他消费者与不存在订单边界、断连、认证与非法参数检查通过，退款写调用 0；隔离会话及 checkpoint 已清理。
- 增加 `model/policy_evidence.py` 的具名证据与引用来源核验：只接受本轮成功检索事件中、当前文档版本已知正式条款的非空原文摘录。测试覆盖伪造事件、旧版本、未知条款、错误及空白摘录。该核验仅检查来源，**不证明语义支持或与订单事实一致**，尚未接入实际 Host。
- `.venv/Scripts/python.exe -m pytest order-demo/test -q --tb=short`：43 passed；`python -m mypy --ignore-missing-imports --check-untyped-defs order-demo/model/policy_evidence.py`、`python -m compileall -q order-demo/model/policy_evidence.py order-demo/test/test_policy_evidence.py`、`python -m pip check` 通过（均使用工作区 `.venv`）。

验收结论：**本票未完成，四项验收均不勾选**。04、05 的真实依赖复验通过，但当前图仍仅支持查单，尚未实现 PolicyAgent、政策计划与补查、语义充分性及事实一致性检查、资格解释、意图区分与多轮资格追问，故未进行 06 的 CLI 对照集或重排故障端到端验证。解除条件是完成上述实现并按本票验证方式用真实服务及故障注入验收；不需要改变已定稿业务行为。Status 保持 `ready-for-agent`（仓库未配置完成状态）。

2026-11-20 续做并完成验收。CLI 已接入 PolicyAgent、只读检索计划、一次正式条款补查及 Host 证据核验。通用咨询由模型选择相关候选，由 Host 从本轮真实检索结果核对出处和原文。个人资格解释以经认证的订单事实及适用条款为准。咨询与补单不生成退款办理授权或待办。

- 目标 MySQL `SELECT VERSION()` 为 **8.0.46**。运行 `.venv/Scripts/python.exe order-demo/test/verify_policy_cli.py --config <临时文件> --evidence order-demo/evidence/policy-06-final-events.jsonl`，真实 Qwen、跨进程 MCP、MySQL、BGE-M3、Milvus 与重排链共验收 16 轮。两个到账问法都采用 FAQ-004 原文并引用 RF-001、RF-008；不相关 FAQ 被强制置顶也没有被采用。
- DEMO-1001、1002、1004、2001 的资格和归属结果符合实际订单。资格咨询、条件式办理、歧义澄清及跨进程重启后在同会话补单均按预期分流。重排 503 和超时均提示暂时无法核实，故障期间纯查单仍返回 19900 分。退款写调用为 0，退款和提交表记录数一致，隔离会话及 checkpoint 已清理。
- `python -m pytest order-demo/test -q --tb=short` 50 项通过。mypy、compileall、pip check 通过。全仓 pytest 仍因同名模块冲突和 SQLite checkpoint 依赖缺失等报 12 项收集错误。详细命令、版本、事件和观察见 [06 验收记录](../../../evidence/policy-06.md)。

验收结论：**本票四项验收均通过**。Status 保持 `ready-for-agent`，仓库未配置完成状态。当前 Git HEAD 缺少 05 的政策模块与配置目录迁移，本票依赖工作区中的这些已有改动，实施结果保留在工作区。

2026-11-23，05 和 06 的实现已纳入当前仓库。用户确认在继续办理退款前，由 [15](15-remove-host-policy-file-dependency.md) 将咨询答复及引用核验改为依据本轮 MCP 回执，再由 [16](16-publish-policy-catalog-revisions.md) 实现多文档版本切换，随后开始 [07](07-submit-one-mock-refund.md)。
