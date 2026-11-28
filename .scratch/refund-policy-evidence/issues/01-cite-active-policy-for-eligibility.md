# 01: 在订单资格咨询中引用生效政策

**Status:** ready-for-agent

**Blocked by:** 无。

**What to build:** 消费者询问本人订单是否符合退款条件时，系统按可信订单事实判断资格，检索本轮生效政策，并使用现有 Qwen3-8B 从候选原文提取结构化条件、例外及结论。代码核对条件、结论和检索回执，使用相关原文和真实出处解释办理结论。

## 验收标准

- [x] 从 CLI 咨询合格、未支付、已发货及已有模拟退款的本人订单。办理代码根据已确认的归属、支付、发货和退款记录得出结论，回复包含相关政策原文与真实出处。资格咨询没有退款办理授权或退款写调用。
- [x] 在保持资格规则一致的两个政策版本中调整条款编号和表述。运营上传已审批的政策原文后，CLI 引用本轮生效版本的条款号与原文。一次调查的后续补查继续使用同一政策版本快照。
- [x] 用同一测试样本提供表述相近但适用范围不同的候选。验证结构化提取保留「仅限」「除外」等条件，代码只采用条件与订单事实匹配、结论一致且出处可核对的正式条款。可办理的依据须支持整单原路退款。
- [x] 模型输出无法核实的附加条件、提取失败、引用不在本轮回执或检索依据不足时，回复给出已确认的订单事实并说明无法核实政策依据。适用政策与办理规则冲突时说明冲突。
- [x] 用受控候选重复验证例外和冲突场景，并通过真实 Qwen3-8B 与现有检索链核对代表性中文条款的提取、引用和答复。

## 关联规格

[退款政策依据与单币种数据契约](../spec.md)

## Comments

### 2026-11-25 实施与验收

资格咨询依据本人查单结果和既有办理规则得出结论。Qwen3-8B 从本轮正式候选原文提取限定、例外、结论和摘录。代码按订单事实核对适用范围和结论，并按检索回执核对原文及出处。依据不足或冲突时，答复保留已确认的订单事实和代码确定的资格结论。后续补查沿用首次成功回执的政策版本快照。

本轮验证使用随机命名的 MySQL 隔离库、Milvus 集合和独立 MCP 进程。创建数据前执行 `SELECT VERSION()`，实测 MySQL **8.0.46**。验证命令中的 `$config` 指向可用的验收配置。

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py
.venv/Scripts/python.exe -X utf8 -m pytest -q test
.venv/Scripts/python.exe -X utf8 test/verify_policy_cli.py --config $config --eligibility-switch --evidence evidence/refund-policy-eligibility-01-switch-run.jsonl
.venv/Scripts/python.exe -X utf8 test/verify_policy_cli.py --config $config --eligibility-switch --eligibility-order DEMO-1002 --evidence evidence/refund-policy-eligibility-01-unpaid-run.jsonl
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service repositories utils workflows test cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

受控资格和政策用例 **37 项通过**，覆盖同批近似候选、例外中的并且关系、适用拒绝和可退结论冲突、提取解析失败及拒绝、无法核实的条件、无效摘录、错误修订和检索失败。全仓 **135 项通过**，mypy 检查 **88 个源文件通过**。编译、依赖、独立导入和 Git 差异检查通过。

真实链路使用 Qwen3-8B、BGE 向量检索和重排、Milvus **3.0.2** 及独立 Streamable HTTP MCP。发布 CLI 激活 2.0 版时，进行中的资格调查在正式补查中仍请求并收到修订 1，答复引用 1.0 版 `RF-003`。新调查引用 2.0 版的 `RF-083`，结构化结果保留了本人、已支付、未发货以及已有成功模拟退款记录除外的适用条件。已发货和已有模拟退款记录的订单分别引用 2.0 版 `RF-004` 和 `RF-009`。

版本切换联验退出 1，未支付样例已查到正确条款，但模型当时未给出可核验的适用条件，程序保留订单事实并提示无法核实。进一步区分条款中的适用条件与拒绝理由后，未支付样例的真实 CLI 定向复跑退出 0，引用 2.0 版 `RF-004` 的拒绝句。MCP 回执、Host 证据与消费者回复已对账。检查点中的退款授权和核验结果为空，退款记录保持一笔种子退款，提交记录和退款写调用均为零。隔离库、集合、会话和检查点均已清理。汇总保留两轮实测结果。[联验记录](../../../evidence/refund-policy-eligibility-01.jsonl)

五项验收分别有真实联验或受控回执证据。本轮以两轮联验合并作为真实验收记录，修正提取提示后执行了未支付订单的定向复跑。分诊约定未配置完成状态，实施结论记在此处。

### 2026-11-28 复验与补充修正

五项验收均已复验。资格咨询现会排除漏引紧接的独立「除外」分句的摘录，并保留同一条款内其他独立规定的有效引用。受控工作流验证了例外、冲突、依据不足和回执不匹配。政策依据测试 51 项、定向测试 120 项、全量回归 248 项通过，mypy 检查 90 个源文件通过。

最终代码通过真实 CLI 全场景联验。隔离环境首条 MySQL 查询 `SELECT VERSION()` 实测为 8.0.46，Milvus 为 3.0.2。Qwen3-8B 从修订 2 的 `RF-083` 提取本人、已支付、未发货及已有模拟退款记录除外。切版前已开始的调查在补查时继续读取修订 1，引用 1.0 版 `RF-003`。新调查分别按订单事实引用 2.0 版 `RF-083`、`RF-004` 或 `RF-009`。五个会话的退款办理授权和退款写调用均为零，数据库保持 1 笔种子退款、0 笔提交，隔离资源已清理。[本轮验收记录](../../../evidence/refund-policy-evidence-01.md)
