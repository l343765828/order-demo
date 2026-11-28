# 工单 03 处理主动询问政策和办理授权

验证日期为 2026-11-28。基线为 `main` 的 `c4e213e` 加开工时已有的暂存区与工作区改动。前置工单 01 和 02 的验收项均已完成，分别见[工单 01 验收记录](refund-conditional-policy-01.md)和[工单 02 验收记录](refund-conditional-policy-02.md)。开工全量 pytest 通过 280 项，mypy 检查 90 个源文件通过。

## 实施

- 普通 `INTENT` 在原有一次结构化调用中识别本轮订单目标和政策需求。已有的明确办理指令快速路径可识别独立附在指令后的依据问题。授权仍须核对当前消息明确要求退款、包含唯一目标订单号且两者关联。纯资格咨询补号继续接续原只读问题。
- 本人订单政策咨询先查订单，再按已核实的资格和所问事项安排正式政策调查。消费者要求先看条款或把条款设为办理条件时，本轮只读。之后须在同一条新消息中明确要求办理并写出唯一订单号。
- 办理附带索要依据时，沿用提交前复核、受控单次发送和回执核对。成功后才调查政策，并把真实退款金额和单号放在依据前。政策检索或解释失败时，保留已确认的业务结果并说明依据暂时无法核实。正式补查继续复用原步骤、目录修订和预算。

## 自动验证

```powershell
.venv/Scripts/python.exe -m pytest -q test
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_refund_submission.py -k 'independent_policy_question or policy_condition' --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k specific_question --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k 'specific_question or specific_term' --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k 'consultation_and_same or specific_question' --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_order_id_extraction.py -k bare_refund --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_order_id_extraction.py test/test_refund_submission.py test/test_policy_evidence.py test/test_order_flow.py test/test_preferences.py test/test_mysql_core.py test/test_policy_catalog.py --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -X utf8 -m pytest -q test
.venv/Scripts/python.exe -m py_compile implementations/policy/answer.py implementations/policy/eligibility.py implementations/refund/intent.py implementations/refund/intent_prompt.py model/policy/evidence.py model/refund/flow.py model/refund/intent.py model/refund/status.py workflows/refund.py workflows/refund_nodes.py test/test_refund_submission.py test/test_order_id_extraction.py test/test_policy_evidence.py test/verify_refund_submission.py
.venv/Scripts/python.exe -c "import sys; import model.refund.intent; import model.policy.evidence; assert not any(name in ('cli', 'manage') or name.startswith(('implementations.', 'workflows.')) for name in sys.modules)"
git -c core.safecrlf=false diff --check
git -c core.safecrlf=false diff --cached --check
```

首轮红灯有 3 项失败，分别复现办理附带依据和两种条款前提请求都没有政策检索。审查用例先复现调查丢失到账时限主题，再复现错引资格条款和遗漏明确回答时限的条款。最终检查了首查、补查和解释保留所问事项，引用须有直接回应标记，并通过原文、版本、适用范围和例外核验。

最终定向 **213 项通过**，全量 **296 项通过**，mypy **90 个源文件通过**。语法检查、模型独立导入和两项 Git 差异检查通过。工作流受控用例覆盖目标歧义、否定、引用和历史指代，均无授权或提交。检查点恢复时只读政策步骤沿用同一目录修订，且已完成的办理保持一次提交并返回原回执。

## 代码审查

- 仓库标准审查发现，已可确定拆分的办理指令与依据问题仍让模型确认，以及资格字段重算、新分支中文说明不足。改为复用已有的确定性识别，保留解释采用的可信资格结论，并补齐分支与条件注释。
- 工单规格审查复现了模型漏标「官方文件」前提时误授权、独立追问条款时办理被暂缓，以及把资格条款当成特定时限的依据。已补回归并修正。另复核了关联的历史订单补号和提交后恢复路径。

## 实际入口与隔离验收

```powershell
$verificationDir = Join-Path ([IO.Path]::GetTempPath()) ('refund-conditional-03-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $verificationDir
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group explicit-policy --evidence (Join-Path $verificationDir 'explicit-policy.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group explicit-policy --smoke --evidence (Join-Path $verificationDir 'authorization.jsonl')
```

运行使用实际 CLI、Qwen/Qwen3-8B、独立 MCP、随机命名的 MySQL 库和 Milvus 集合。建立 MySQL 连接后首条显式查询为 `SELECT VERSION()`，实测 **MySQL 8.0.46**。Milvus 实测 **3.0.2**。脚本只向隔离目录和集合发布验收用 1.0 版，开启检索故障场景时使用隔离 MCP 故障注入。配置与细粒度运行日志保存在临时目录。

首次实模多轮对话中，Qwen 把上一轮政策前提带入了「帮我退」，观察到额外政策检索、零次退款提交。补了两条红灯用例，再让明确缺号和仅指代历史订单的办理措辞直接追问本轮订单号。疑似来自历史的模型政策标记也须在本轮原文找到政策或阅览线索。普通消息、确定性追问和资格补号各按自身路径统计模型调用。

另一次实测中，检索候选只有资格条款，Qwen 将支付后申请退款期限误标为回答到账时限。修正后，到账问题按所问主题检索。即使模型标记相关，原文没有到账主题表述时也不会引用。修正后的隔离 CLI 验证引用 RF-008。通用咨询选错条款和「何时退回付款账户」的等效问法由受控工作流回归验证。

分轮覆盖以下 **13 个场景**。完整组的中间运行因上述断言退出，已通过的场景保留实测记录。针对失败项的最终 `--smoke` 运行以退出码 0 完成，覆盖条款确认、到账时限、缺号、历史指代和新授权共 5 个场景。各轮均确认隔离库和集合已清理。

| 场景 | `INTENT` 模型调用 | 政策检索 | 退款调用 / 新退款 | 实测答复与核对 |
| --- | ---: | --- | ---: | --- |
| 通用政策咨询 | 1 | `consultation` 1 次 | 0 / 0 | 引用 1.0 版 FAQ-004，核对目录修订和原文。 |
| 本人订单政策咨询 | 1 | `formal` 1 次 | 0 / 0 | 按本人订单事实解释资格，引用 1.0 版 RF-003。 |
| 资格咨询缺号 | 1 | 0 次 | 0 / 0 | 追问资格咨询所需的订单号。 |
| 同会话只补编号 | 0 | 0 次 | 0 / 0 | 接续原资格咨询，答复可信订单事实。 |
| 办理并索要依据 | 0 | `formal` 1 次 | 1 / 1 | 独立 MCP 先提交、后检索。回复给出 ¥199.00 和与数据库一致的原回执，再引用 RF-003。 |
| 看完条款再决定 | 1 | `formal` 1 次 | 0 / 0 | 引用 RF-003，明确本轮未提交。 |
| 以查到正式条款为条件 | 1 | `formal` 1 次 | 0 / 0 | 引用 RF-003，等待后续明确办理指令。 |
| 先看指定订单的到账时限条款 | 1 | `formal` 1 次 | 0 / 0 | 引用 RF-008，回复原文与目录修订一致。 |
| 条款前提遇到空候选 | 1 | `formal` 2 次 | 0 / 0 | 复用原政策步骤和目录快照，提示依据暂时无法核实。 |
| 办理附带依据时重排失败 | 0 | `formal` 1 次失败 | 1 / 1 | 保留退款单号和 ¥199.00，随后说明依据暂时无法核实。 |
| 看完条款后只说「帮我退」 | 0 | 0 次 | 0 / 0 | 请消费者在同一条消息中写出唯一订单号并明确办理。 |
| 看完条款后说「退刚才那单」 | 0 | 0 次 | 0 / 0 | 同样追问当前消息的明确编号。 |
| 同会话明确写出订单号并授权 | 0 | 0 次 | 1 / 1 | 按本轮消息办理一次，返回真实业务回执。 |

## 验收范围

目标歧义、否定和引用办理措辞的误授权回归使用实际工作流及受控模型结果。历史指代另通过实际 CLI 多轮执行。正式 RF-013 新版发布、目标开发库重置及旧政策核验 JSONL 清理由[工单 04](../.scratch/refund-conditional-policy/issues/04-coordinate-policy-release-and-verify-cutover.md)安排实测。
