# 工单 02 按拒绝原因检索并解释

验证日期为 2026-11-28。本文先记录工单 01 完成后的本轮实施与验收，再保留前次依赖阻塞时的基线观察。

## 本轮基线与实施

[工单 01](../.scratch/refund-conditional-policy/issues/01-unify-qualification-and-eligible-refund.md) 的五项验收均已勾选，[验收记录](refund-conditional-policy-01.md) 已入工作区。以 `main` 的 `c4e213e` 和开工时已有的暂存区、工作区为基线。开工重跑全量 pytest 通过 271 项，mypy 检查 90 个源文件通过。

- `workflows/refund.py` 和 `workflows/refund_nodes.py` 复用同一套路由处理初查、复核和收到退款回执后的去向。普通资格咨询和办理从单步查单开始。明确拒绝时追加唯一的 `policy` 步骤，将索引由 0 推进到 1。补查继续执行索引 1，沿用首次成功检索的目录修订与一次补查预算。
- `model/refund/flow.py` 保存已确认的拒绝资格，`model/refund/submission.py` 从明确的办理拒绝回执取得对应资格。事务拒绝后清除先前订单查询事实，保留实际拒绝回执，供检查点恢复、政策降级和最终答复使用。新一轮由 `INTENT` 按原生命周期重置状态。
- `implementations/policy/eligibility.py` 根据拒绝资格生成首查及补查文本，检索参数只包含拒绝原因所需的业务信息。政策解释沿用出处、原文、版本和适用范围核验。事务内确认未支付时，发货状态未知。每组例外都须有可信事实证明至少一个条件不成立，才可排除。冲突记录在 `policy_evidence` 事件，退款结果仍由程序规则决定。
- `test/test_policy_evidence.py` 通过编译后的工作流覆盖计划、检查点恢复、同快照补查、核验和业务降级。`test/verify_refund_submission.py` 复用隔离 CLI 和 MCP 验证事务内拒绝及只读政策解释，`README.md` 补充对应运行行为。

## 定向测试、类型检查与全量回归

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k denial_resume
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k unknown_exception --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py test/test_policy_catalog.py test/test_refund_submission.py test/test_order_flow.py test/test_mysql_core.py --tb=short
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m pytest -q test
.venv/Scripts/python.exe -m py_compile test/test_policy_evidence.py test/verify_refund_submission.py
.venv/Scripts/python.exe -c "import sys; import model.refund.flow; import model.refund.submission; assert not any(name in ('cli', 'manage') or name.startswith(('implementations.', 'workflows.')) for name in sys.modules)"
git -c core.safecrlf=false diff --check
git -c core.safecrlf=false diff --cached --check
```

新增恢复用例先复现拒绝首查仍使用 `consultation`。在初查重规划后的检查点恢复时，观察到计划仅含 `order` 和 `policy`，待执行节点为 `execute`，索引为 1。恢复后的两次检索均执行 `policy`，第二次沿用修订 11，两次查询均无订单号或测试备注。事务拒绝后暂停并恢复的用例观察到一次提交和一次正式检索。未知例外用例先复现错误引用，再验证继续补查、无引用并保留未支付原因。检索或解释失败、不适用、目录修订不匹配和混入 FAQ 均只回复已确认的业务原因与政策降级提示。冲突用例核对 `policy_evidence` 为 `conflict`。

最终定向 **122 项通过**，全量 **280 项通过**，mypy **90 个源文件通过**。验证脚本语法检查、业务模型独立导入和两项 Git 差异检查均通过。

## 实际入口与隔离验收

```powershell
$probeDirectory = Join-Path ([IO.Path]::GetTempPath()) ('refund-conditional-02-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $probeDirectory
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group policy-handling --evidence (Join-Path $probeDirectory 'policy-handling.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group outcomes --investigation-probe --evidence (Join-Path $probeDirectory 'outcomes.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group qualification --investigation-probe --evidence (Join-Path $probeDirectory 'qualification.jsonl')
```

三个运行在建立 MySQL 连接后都先执行 `SELECT VERSION()`，实测版本为 **MySQL 8.0.46**。每次使用随机命名的库、临时配置和独立 MCP 进程。`policy-handling` 使用真实 CLI、Qwen、Milvus **3.0.2** 和隔离政策目录，发布的 2.0 版为从现有源生成的测试版本。`outcomes` 和 `qualification` 禁用政策后端，探针替换只读调查的模型委派，部分结构化意图和计划由探针固定，查单、提交、事务和结果核对仍由独立 MCP 与实际 MySQL 执行。

| 场景 | 正式政策检索 | 退款服务调用 | 新退款数 | 实测结果 |
| --- | ---: | ---: | ---: | --- |
| 初查确认未支付或已发货 | 各 1 | 0 | 0 | 初始计划只有 `inspect_order`。分别说明未支付或已发货并引用 2.0 版 RF-004 的对应原文。 |
| 提交前复核才确认已发货 | 1 | 0 | 0 | 按实际已发货原因答复。禁用政策后端时提示暂时无法核实依据。 |
| 提交事务才确认已发货且回执完整 | 1 | 1 | 0 | MCP 先记录拒绝，再收到正式政策查询。提交记录关闭为 `closed_no_refund`，原因 `shipped`。答复使用该拒绝原因及 2.0 版 RF-004，未沿用旧状态。 |
| 重排失败、结果为空、适用规则冲突 | 分别 1、2、1 | 0 | 0 | 空结果补查请求及回执均沿用修订 1，查询不含订单号。冲突记为 `conflict`。三种场景均保留业务拒绝，且无引用。 |
| 非本人订单 | 0 | 0 | 0 | 返回未找到可操作的订单。查单失败回执另由工作流用例验证。 |
| 拒绝回执丢失或事务提交断连 | 0 | 各 1 | 0 | 只读核对后继续提示结果未确认，续办和新会话没有重发。 |

`policy-handling` 通过 **12 个场景**，`outcomes` 通过 **19 个场景**，`qualification` 通过 **10 个场景**。三个随机隔离库与 `policy-handling` 使用的隔离集合均已确认清理。`qualification` 用最终代码在无政策后端的条件下复测事务拒绝，观察到一次退款服务调用、零条新退款和明确的已发货答复。带正式引用的事务拒绝在例外核验审查修复前完成实测，最终代码通过工作流回归复核该引用和未知例外。

## 代码审查

- **仓库标准**。按开工时的文件快照核对本轮差异。补齐中文区域说明，移除解释模块中重复资格判断，合并两个隔离验收组的事务拒绝探针。独立审查进程因工作区额度中断，已结合其返回的发现完成复核。
- **工单规格**。独立审查发现部分事务事实不能排除未知例外。已通过工作流测试重现并修复，另查复核和事务拒绝、失败降级与重复提交路径。

## 切换范围

正式 RF-013 发布、目标开发库重置和旧政策核验 JSONL 清理由[工单 04](../.scratch/refund-conditional-policy/issues/04-coordinate-policy-release-and-verify-cutover.md) 验收。本轮政策检索使用隔离版本，查单失败、解释失败和未知例外使用受控回执覆盖。

## 前次依赖阻塞时的基线

### 前置依赖

- [工单 01](../.scratch/refund-conditional-policy/issues/01-unify-qualification-and-eligible-refund.md) 的五项验收均未勾选，未发现 `evidence/refund-conditional-policy-01.md`。
- `workflows/refund_nodes.py` 的 `plan` 为指定订单的资格咨询和授权办理直接生成 `inspect_order`、`retrieve_policy` 两个步骤。复核时输入包含已有的政策依据。
- `implementations/refund/qualification.py` 的支付及发货规则已被办理前校验和事务内核验复用。`implementations/refund/execution.py` 的 `review` 仍核验政策依据并持久化政策事件。`workflows/refund.py` 中 `SEND_REFUND` 仍直接进入 `REFUND_REPLY`。
- 工单 02 需要工单 01 交付从可信订单事实取得的统一资格结果，以及政策后端离线时仍可提交合格退款的主路径。前置验收通过后，才有工单 02 的实现和代码审查基准。

### 测试基线

```powershell
.venv/Scripts/python.exe -m pytest -q test
.venv/Scripts/python.exe -m pytest -q test/test_order_flow.py test/test_policy_evidence.py test/test_policy_catalog.py test/test_refund_submission.py test/test_mysql_core.py
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
git -c core.safecrlf=false diff --check
git diff --cached --check
```

- 全量 pytest **251 项通过**，定向 pytest **93 项通过**，mypy **90 个源文件通过**，两项 Git 差异检查退出码均为 0。
- 定向基线包括引用出处与原文核对、适用条件、相关例外、冲突、同快照补查、检索故障降级，以及从准备完成的检查点恢复时拦截无本轮输入的退款发送。
- 通过 `test/test_policy_evidence.py` 的 `invoke_eligibility` 调用编译后的工作流，捕获 `plan` 事件。对已支付未发货及已发货两组，初始步骤均为 `inspect_order`、`retrieve_policy`，注入检索失败后均观察到一次检索和无法核实政策的提示，已发货组仍报告已发货。
- 在同一入口用 `invoke_eligibility([], [], second=[], shipping="shipped")` 模拟空结果，记录传给检索工具的参数。首查无指定目录修订，补查沿用首次回执的目录修订 `11`。两次查询均包含已发货原因，补查还传入了消费者原问题中的订单号 `DEMO-1002`。

### 隔离业务实测

```powershell
$probeDirectory = Join-Path ([IO.Path]::GetTempPath()) ('refund-conditional-02-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $probeDirectory
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group outcomes --investigation-probe --evidence (Join-Path $probeDirectory 'outcomes.jsonl')
```

脚本连接 MySQL 后首条查询 `SELECT VERSION()` 返回 **8.0.46**。使用随机命名的隔离库和 Milvus 集合，由 CLI 调用独立 MCP 进程。现有 `outcomes` 组 **19 个场景通过**，退出码为 0。脚本确认已清理隔离库和集合。

| 现有场景 | 退款服务调用 | 新退款记录 | 观察 |
| --- | ---: | ---: | --- |
| `refetch_shipped` | 0 | 0 | 提交前重查确认已发货，业务结果为 `denied / shipped`。本轮共两次政策检索，分别在初次查单后和再次查单后。 |
| `lost_denial` | 1 | 0 | 订单在发起提交时变为已发货，MCP 返回 `denied`。故障注入丢弃回执，Host 核对无记录后报告 `unconfirmed / receipt_unknown`。后续办理再次核对，未重复提交。 |
| `commit_disconnect` | 1 | 0 | 事务未提交且通信断开，核对无记录后报告 `unconfirmed / receipt_unknown`。原会话恢复和新会话办理均未重复提交。 |

### 当时的待验收项

| 工单验收项 | 当前状态与解除条件 |
| --- | --- |
| 初始计划、拒绝后追加、索引、恢复与补查复用 | 受控工作流已复现现有双步初始计划。工单 01 主路径就绪后，实现并验证拒绝后仅追加一次，以及从检查点恢复和补查时复用步骤。 |
| 基于拒绝原因的最小查询、版本快照、预算 | 现有补查复用了版本快照，同时传入了原问题中的订单号。接入统一资格结果后，验证首查和补查均仅传必要业务信息，且继续复用快照和既有预算。 |
| 条款核验、失败降级、冲突与程序结果 | 现有政策核验基线通过。接入拒绝解释后，从同一入口复核失败、空结果、不适用、解释失败和冲突下的业务答复及提交结果。 |
| 提交前与事务内已发货的服务调用、退款记录及答复 | 隔离基线分别观察到 0 次调用和 0 条记录、1 次调用和 0 条记录。事务内拒绝用例注入了回执丢失。工单 02 实现后，复验两个时点的计数，并测试 Host 收到完整拒绝回执时能在退出事务后解释该次原因。 |
| 查单不可用、通信故障、待核对与只读解释 | 现有恢复及业务保护有基线覆盖。工单 02 实现后，复核三个异常分支及新增只读解释对调用次数和业务结果的影响。 |
