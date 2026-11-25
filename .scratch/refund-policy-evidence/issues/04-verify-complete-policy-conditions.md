# 04 核验退款政策依据的条件完整性

**Status:** ready-for-agent

## 问题

工单 01 和 02 共用的政策核验接受了一个结构有效但漏提条件的结果。受控复现向现有演示办理代码提供一条包含新增金卡会员门槛的候选条款，模型引用完整原文，却只提取「已支付」。资格咨询将该条款判为充分依据，授权办理也走到了模拟写工具调用。该样本组合了新增资格条款与原有办理规则，复现使用内存替身。

[工单 01](01-cite-active-policy-for-eligibility.md) · [工单 02](02-verify-policy-in-refund-handling.md) · [关联规格](../spec.md)

## 已确认的要求

开发按已定稿的业务需求在办理代码中实现每项退款资格规则，并使用可信业务事实判断资格。资格发生变化时，配套政策和办理代码一起验收并协调上线。模型提取遗漏某项资格条件时，办理代码仍执行该条件。资格通过后的政策依据核验及提交前持久化继续遵循既有规格。

运营人员可以按约定整理政策条件清单，政策原文及清单由模型阅读并提取。验收人员在验证时发现漏项，按 Bug 提交给开发修复。

## 验收标准

- [x] 用真实 Qwen3-8B 和生效政策验证资格咨询使用的提取结果。样例同时包含已实现的资格条件和例外，例如已支付、未发货的本人订单可整单原路退款，已有模拟退款记录的订单除外。核对模型逐项保留条件、例外及其关系，回复引用实际条款原文和出处。
- [x] 给已发货的本人订单注入漏提「未发货」条件的模型结果，走明确授权办理流程。代码依据可信发货状态拒绝办理，最终政策核验事件保存、新退款提交和退款写调用均为零。
- [x] 用条件完整、订单事实匹配且编号已调整的生效条款验证正常办理。通过真实 CLI 在隔离数据上完成一次模拟退款，核对仅有一次退款提交和一次退款写调用，最终政策核验事件可追溯到本轮检索回执和真实政策原文。

## Comments

### 2026-11-25 条件清单的阅读职责

用户确认由运营人员整理清单，由模型阅读政策原文及清单。当时曾提出使用独立依据自动识别模型遗漏的条件。

### 2026-11-25 资格规则与政策提取的职责

用户明确，生产环境的资格门槛属于代码实现的业务需求。模型回复漏掉条件时，代码仍按已实现的规则判断。此前用未配套实现的新门槛做受控复现，不能据此断定已经实现该门槛的办理代码会放行。初稿拟在模型漏提金卡会员时要求两条业务路径均自动识别漏项，现按上述职责澄清重新确认验收范围。

使用现有「未发货」资格做受控验证。给已发货的本人订单注入漏提「未发货」条件的模型结果，办理仍拒绝，最终政策核验事件保存、提交创建及退款写工具调用均为零。

### 2026-11-25 确认验收范围

用户确认用真实模型验证条件和例外的提取，用受控漏提验证既有业务资格规则，并保留条款改号后仅提交一次的正常办理验收。

### 2026-11-25 实施与验收

基于工作区已有的工单 01 和 02 实现，在退款提交验收脚本增加 `policy-conditions` 组。该组通过真实 CLI 先验收受控漏提，再验收对同一改号条款的正常办理。受控场景只替换政策条件提取的结构化输出，保留真实意图识别、本人查单、政策检索和退款办理链路。应用代码沿用既有资格判断。资格咨询与正常办理共用验收断言，按实际引用的检索回执、候选及逐字摘录核对条件和例外。

两轮分别使用独立 MySQL 数据库、Milvus 集合和 Streamable HTTP MCP 进程。创建隔离数据前均执行 `SELECT VERSION()`，实测 MySQL **8.0.46**。实测 Milvus **3.0.2**，模型为 `Qwen/Qwen3-8B`，使用真实 BGE 向量检索和重排。联验退出码均为 0，证据记录了隔离资源清理结果。[本票验收证据](../../../evidence/refund-policy-conditions-04.jsonl)

#### 真实资格咨询

`DEMO-1001` 咨询引用《演示退款政策与常见问题》2.0 版 `RF-083`。与回复引文一致的模型摘录保留了必须同时满足的本人、已支付和未发货三项条件，单列已有成功模拟退款记录作为例外，结论为整单原路退款。引文及出处与本轮正式检索回执相符，退款办理授权、退款提交和写调用均为零。

#### 受控漏提「未发货」

消费者明确要求办理 `DEMO-1004`。真实本人查单回执为已支付、已发货，办理代码在政策提取前记录 `denied/shipped`。注入的规则引用完整 `RF-083` 原文，保留本人、已支付及退款记录例外，省略未发货条件。CLI 告知已拒绝且未提交，并报告政策提取结论与办理规则冲突。最终政策核验事件文件、新退款提交、新退款记录和 MCP 退款写调用均为零。

#### 改号后的正常办理

隔离发布将 `RF-003` 改为 2.0 版 `RF-083`。真实模型从同轮候选完整提取三项限定和退款记录例外，符合资格的 `DEMO-1001` 经消费者授权产生一笔 ¥199.00 模拟退款。核对一条最终政策核验事件、一条提交记录、一条退款记录和一次跨进程 MCP 退款写调用，幂等键一致。最终核验事件的文档、版本、条款和逐字摘录均能追溯到本轮正式检索回执，事件已在提交和写调用之前保存。

#### 验证命令

`$config` 指向本地验收配置，`$runDir` 为仓库外的临时目录。两轮 JSONL 的联验记录与清理结果合并为上面的本票证据。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_policy_cli.py --config $config --eligibility-switch --eligibility-order DEMO-1001 --evidence (Join-Path $runDir 'eligibility.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config $config --group policy-conditions --evidence (Join-Path $runDir 'handling.jsonl')
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py
.venv/Scripts/python.exe -X utf8 -m pytest -q test
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service repositories utils workflows test cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m pip check
git -c core.safecrlf=false diff --check
git -c core.safecrlf=false diff --cached --check
```

政策测试 **46 项通过**，全量测试 **144 项通过**，mypy 检查 **88 个源文件通过**。编译、依赖和 Git 差异检查通过。三项验收均已实测，无前置阻塞。分诊约定未配置完成状态，结论记录在本条 Comments。
