# 02: 在退款办理中核对并引用政策依据

**Status:** ready-for-agent

**Blocked by:** [01 在订单资格咨询中引用生效政策](01-cite-active-policy-for-eligibility.md)。

**What to build:** 消费者明确要求退款时，系统复用资格咨询的政策依据核验。明确因资格被拒时，检索并引用正式条款解释拒绝原因。合格订单在有效授权及最终政策依据均已核实后才发起新的退款提交。

## 验收标准

- [x] 对同一会话明确授权的未支付、已发货本人订单，各验证一次拒绝路径。系统执行只读政策检索，回复包含办理结论、相关原文和出处，退款写调用为零。
- [x] 拒绝路径遇到检索失败或依据不足时，仍告知已确认的订单事实、本次未提交退款及暂无法核实政策依据。适用政策与办理规则冲突时说明冲突。
- [x] 合格订单凭当前有效办理授权和本轮正式政策依据产生一笔模拟退款。条款编号变化后仍可办理，退款记录可追溯到本次授权和已确认持久化的最终政策核验事件。核验记录能定位实际文档版本、条款、原文和检索事件。
- [x] 对拟发起的新退款验证依据不足、政策冲突、最终政策核验事件持久化失败或结果不可确认的场景。各场景均未创建新的退款提交，且未调用退款写工具。
- [x] 已有退款记录返回原退款单号和原金额。政策检索失败时，已有结果的读取及旧提交的只读核对仍可运行。

## 关联规格

[退款政策依据与单币种数据契约](../spec.md) · [原工单 07 首次模拟退款](../../refund-agent-all-tech-demo/issues/07-submit-one-mock-refund.md)

## Comments

### 2026-11-25 实施与验收

授权办理复用工单 01 的原文提取、条件匹配和本轮出处核验。本人订单因未支付或已发货被拒时，按实际拒因查找正式条款。准备发起新退款时，固定节点重查本人订单并核对本轮证据，确认最终核验事件已持久保存后才创建提交并调用退款写工具。移除了写入路径对固定条款编号、预置正文摘要和二次政策解释的依赖。

两轮均先执行 `SELECT VERSION()`，实测 MySQL **8.0.46**。联验使用 Qwen3-8B、BGE 向量检索和重排、Milvus **3.0.2**、独立 Streamable HTTP MCP，以及真实 CLI。两轮使用不同的隔离数据库、集合和临时配置，退出码均为 0，已核实隔离资源清理。完整组运行期间增加了拒因匹配和遗漏例外的回归校验，随后用最终代码复跑正常组。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group policy-handling --evidence evidence/refund-policy-handling-02.jsonl
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group policy-handling --smoke --evidence evidence/refund-policy-handling-02-normal.jsonl
.venv/Scripts/python.exe -X utf8 -m pytest -q test
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service repositories utils workflows test cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m pip check
git -c core.safecrlf=false diff --check
git -c core.safecrlf=false diff --cached --check
```

- 在同一会话分别授权办理 `DEMO-1002` 和 `DEMO-1004`，CLI 说明未支付和已发货的拒绝结论，逐字引用 2.0 版 `RF-004` 的适用原文。两轮均核对了正式检索回执，退款提交和写调用均为零。
- 对办理拒绝注入重排失败、空候选和适用政策冲突，回复均保留已确认的订单事实，并说明本次未提交及依据无法核实或存在冲突。符合资格的订单遇到依据不足、政策冲突或出处异常时，也没有退款提交和写调用。
- 将正式条款从 `RF-003` 改为 `RF-083` 并调整表述，通过发布 CLI 发布 2.0 版。实际办理 `DEMO-1001` 各产生一笔 ¥199.00 模拟退款。按幂等键和追踪标识对账原授权消息、最终核验事件、真实检索回执、提交和退款记录，顺序为政策事件确认、提交确认、MCP 写调用、结果返回。
- 注入最终核验事件保存失败和保存后确认丢失，事件文件分别新增零份和一份，两种情况下均无新退款提交、退款记录或写调用。
- 重排故障期间，`DEMO-1003` 返回原模拟退款单号 `MOCK-RF-1003` 与 ¥88.00。旧提交通过只读工具取得 `not_found`，CLI 保留结果待核对及原提交的拦截。两条路径均没有调用政策检索或退款写工具。[完整联验记录](../../../evidence/refund-policy-handling-02.jsonl) · [最终代码正常组复跑](../../../evidence/refund-policy-handling-02-normal.jsonl)

全仓 pytest **144 项通过**，mypy **88 个源文件通过**。编译、依赖、独立导入及 Git 差异检查通过。固定复核发现订单已发货的同快照补查由受控图用例验证。写调用发出后才在 MCP 事务内因并发变更被拒的场景使用现有业务回执，本轮没有实测该并发窗口，也未增加发送后的政策补查。五项验收均有证据，分诊约定未配置完成状态，结论记录在此处。
