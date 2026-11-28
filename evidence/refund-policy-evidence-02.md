# 工单 02 在退款办理中核对并引用政策依据

## 2026-11-28 复验基线

- 当前工作区以 `c4e213e` 为基线，带有演示重置、订单号提取和政策维护等既有暂存及未暂存改动。前置工单 01 的实现、五项验收和联验证据齐全。
- 仓库中的正式政策文档仍要求提交前核验依据。ADR-0012 的 RF-013 协调切换工单尚无完成记录，本次按工单 02 与 ADR-0009 验收。
- 实施前全量测试 248 项通过，定向测试 61 项通过，mypy 检查 90 个源文件通过。

## 实施与测试

- 在现有退款图入口验证三组受控提取。漏引拒绝条款紧随的「除外」句时，基线错误引用 `RF-098`。漏引明确限定退款办理的条件句时，基线进入模拟发送。直接按「只有」筛选的首次修复误拦截了无关的查单说明。三组用例均先复现失败，最终均通过。
- 资格咨询与授权办理共用已有的相邻例外检查。授权办理的整单原路退款依据增加必要条件句检查，保留对现有条件词、例外、业务资格和真实出处的核对。
- `code-review` 分别审查仓库规范与规格。已修正两处可复现的摘录遗漏，并验证独立查单说明继续允许合格办理。原有摘录校验仍按可识别句式保守核对。同一条款中独立规则的「仅限」仍可能使摘录无法作为充分依据。自由表述的全量条件识别需要另行确定证据契约。
- 最终定向测试 **64 项通过**，全量回归 **251 项通过**，mypy 检查 **90 个源文件通过**。编译和依赖检查通过。
- MCP 事务内才发现状态变化的并发窗口本轮未实测。工单沿用该路径的真实业务回执和确定性回复。

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k omitted_denial_exception --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k omitted_scope --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py -k independent_scope --tb=short
.venv/Scripts/python.exe -X utf8 -m pytest -q test/test_policy_evidence.py test/test_refund_submission.py
.venv/Scripts/python.exe -X utf8 -m pytest -q test
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m compileall -q config implementations interfaces model mcp_service repositories utils workflows test cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m pip check
git -c core.safecrlf=false diff --check
git -c core.safecrlf=false diff --cached --check
```

## 真实 CLI 联验

命令使用本地验收配置。`$runDir` 指向仓库外的临时目录。两轮均在创建隔离数据前执行 `SELECT VERSION()`，实测 MySQL **8.0.46**。真实链路使用 Python 3.11.7、Qwen/Qwen3-8B、BGE 编码和重排、Milvus 3.0.2、Streamable HTTP MCP 2.3.0、DeepAgents 0.7.38 及 LangGraph 1.2.22。各轮独立创建 MySQL 库、Milvus 集合及 MCP 进程，通过发布 CLI 激活测试政策 2.0 版。

完整组启动后加入摘录校验改动。正常组在相邻例外检查落地后启动，其成功用例运行早于必要条件句筛选的最终调整。新增句式和独立查单说明由最终代码的定向测试及全量回归验证。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group policy-handling --evidence (Join-Path $runDir 'live.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group policy-handling --smoke --evidence (Join-Path $runDir 'final-smoke.jsonl')
```

| 已核对场景 | 实测观察 |
| --- | --- |
| 同会话拒绝 | 两轮均在同一会话依次授权 `DEMO-1002`、`DEMO-1004`。每笔执行一次只读正式政策检索，逐字引用实际 `DEMO-REFUND-POLICY` 2.0 版 `RF-004` 中各自适用的句段。回复给出未支付或已发货的拒绝结论。提交与写调用均为零。 |
| 拒绝降级 | 给未支付订单注入重排失败，给已发货订单分别注入空候选和政策冲突。每次保留已确认的订单事实和未提交结论，说明依据暂不可核实或存在冲突。退款提交、退款记录和写调用均为零。 |
| 改号后成功 | 发布 CLI 将 `RF-003` 调整为 `RF-083`。每轮在各自隔离库中，`DEMO-1001` 仅生成一笔 19900 人民币分的模拟退款。核对文档 `DEMO-REFUND-POLICY`、2.0 版、条款、逐字原文、本轮检索事件与当前授权消息。实测顺序为 `refund_policy_verified`、`refund_dispatch_saved`、`refund_submit`、`refund_result`。 |
| 新退款被拦截 | 对合格订单注入重排失败、空候选、政策冲突和非法检索事件引用。无新提交和写调用。分别注入最终核验事件保存失败和保存后确认丢失，事件文件分别增加 0 份与 1 份。两种故障下退款提交、退款记录和写调用均为零。 |
| 他人订单与原退款 | 正常组对 `DEMO-2001` 返回不可操作，对 `DEMO-1003` 返回 `MOCK-RF-1003` 和 ¥88.00。两者的政策检索和退款写调用均为零。 |
| 故障期间的只读路径 | 在重排失败、事件保存失败、事件保存后确认丢失三种场景中，`DEMO-1003` 均返回原退款单号及 ¥88.00。对 `DEMO-1007` 的旧提交核对得到 `not_found`，CLI 保持待核对状态。每组观测到三次查单和两次只读退款结果核对，政策检索和退款写调用均为零。重排失败组再次确认旧提交保留 `pending`。 |
| 提交事务保护 | 注入提交保存失败和保存后确认丢失时，均记录一份已确认的政策核验事件，退款记录和写调用为零。保存失败场景无提交记录，确认丢失场景保留 `pending` 提交供原流程核对。 |

成功路径引用「仅限已付款且尚未发货的本人订单按原支付渠道退回整笔实付款，已有成功模拟退款记录的订单除外。」。完整组对应检索事件 `dcfdc203-f358-4c59-b8c6-c5d32665a6b6`，退款幂等键 `2f4fadde-68ee-4ab0-987a-7a68e60788d3`。正常组对应检索事件 `237582bc-bb15-448b-ad73-407951009381`，退款幂等键 `8d27a209-4c9b-4a25-834c-64030ce5495d`。

完整组退出码 0，共 22 条结构化记录，隔离资源为 `order_demo_r07_bbac78b0481b`。正常组退出码 0，共 8 条结构化记录，隔离资源为 `order_demo_r07_7ebabe061040`。两组均核实了隔离 MySQL 库和 Milvus 集合已删除，独立 MCP 进程退出。暂存与未暂存差异检查均通过。
