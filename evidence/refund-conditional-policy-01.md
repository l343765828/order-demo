# 工单 01 验收记录

2026-11-28 · [统一资格判断，打通合格办理](../.scratch/refund-conditional-policy/issues/01-unify-qualification-and-eligible-refund.md) · [ADR-0012](../docs/adr/0012-refund-policy-for-explanation.md)

## 基线与范围

工单无前置依赖。起始提交为 `c4e213e130b868500283a1e8a87eba2710eadb96`，基线测试在已有暂存和工作区改动之上运行。系统 Python 缺少项目依赖，切换到仓库已有的 `.venv` 后，基线全量 pytest 251 项通过，mypy 检查 90 个源文件通过。

## 实施与调用链

- 初次规划只读取本人订单。普通查单保持原有答复，合格资格咨询按已核实的支付、发货和退款状态直接答复。确认拒绝时继续使用已有的只读政策调查。
- 合格办理从 `REPLAN` 进入独立订单复核。通过后在流程状态保存本轮授权、追踪标识和唯一提交键。`SEND_REFUND` 核对当前轮次，持久保存提交记录，再发送一次 MCP 写请求。
- MCP 事务先校验提交、归属和已有退款，再读取锁定后的最新订单。资格咨询、办理预检和事务使用同一套状态规则。原退款、未决提交、幂等与回执丢失继续按既有路径处理。
- `implementations/refund/` 负责共享资格规则、办理授权和执行，`workflows/` 负责调查和办理路由，`model/refund/`、接口和仓储传递与持久化提交信息。退款办理链路已移除政策核验文件的写入门槛。

## 定向测试

```powershell
.venv/Scripts/python.exe -m pytest -q test/test_refund_submission.py -k needs_trusted
.venv/Scripts/python.exe -m pytest -q test/test_refund_submission.py test/test_order_flow.py test/test_order_id_extraction.py test/test_policy_evidence.py
```

实施前新增用例有 4 项按预期失败，复现合格咨询、合格办理、复核不可用和带政策前提请求仍触发政策搜索。最终定向组 **153 项通过**。工作流注入测试核对普通 `INTENT` 恰调用一次，资格规则不调用模型，订单证据不可用或不匹配、授权缺失及带政策前提的请求均不提交。

## 类型检查

```powershell
.venv/Scripts/python.exe -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
.venv/Scripts/python.exe -m py_compile test/verify_refund_submission.py test/verify_policy_cli.py
.venv/Scripts/python.exe -c "import sys; import repositories.dispatch_repository; assert not any(name in ('cli', 'manage') or name.startswith(('implementations.', 'workflows.')) for name in sys.modules)"
```

mypy 检查 **90 个源文件通过**。验证入口通过语法检查。独立加载提交仓储成功，且未加载业务实现、工作流或应用入口。

## 全量回归

```powershell
.venv/Scripts/python.exe -m pytest -q test
git -c core.safecrlf=false diff --check
git -c core.safecrlf=false diff --cached --check
```

全量 pytest **271 项通过**。工作区及暂存区的 Git 差异检查通过。

## 实际入口与隔离业务验收

```powershell
$probe = Join-Path ([System.IO.Path]::GetTempPath()) 'agent-order-demo-workorder01'
New-Item -ItemType Directory -Path $probe -Force | Out-Null
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group qualification --evidence (Join-Path $probe 'qualification.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group qualification --investigation-probe --evidence (Join-Path $probe 'qualification-probe.jsonl')
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config config/local.json --group outcomes --investigation-probe --evidence (Join-Path $probe 'outcomes-final.jsonl')
```

连接后先执行 `SELECT VERSION()`，实测 **MySQL 8.0.46**。脚本为每个运行生成独立的临时配置和随机命名的隔离库，并启动独立 MCP 进程。两组均在没有政策后端配置的进程中运行。记录已确认各通过运行的隔离库均已清理，且未创建 Milvus 集合。

`qualification` 使用真实 CLI、Qwen、MCP 和 MySQL，**10 个场景通过**。同命令复跑并写入 `qualification-final.jsonl`，10 项再次通过。启用 `--investigation-probe` 的隔离运行也通过 **10 个场景**。探针替换只读调查的模型委派，并固定部分意图和计划输出，仍经实际 MCP 获取订单事实，使用同一提交路径和真实 MySQL 事务。

| 场景 | 政策检索 | 退款提交 | 实测观察 |
| --- | ---: | ---: | --- |
| 普通状态查询 | 0 | 0 | DEMO-1001 已支付、未发货、无模拟退款记录，实付 19900 分 |
| 合格资格咨询 | 0 | 0 | 同一订单按程序规则回复符合整单原路退款条件 |
| 合格且已授权 | 0 | 1 | 保存 1 条带本轮原文授权的提交，写入 1 条金额为 19900 分的退款，并按原回执回复 ¥199.00 和退款编号 |
| 重复办理、历史指代、非本人订单 | 0 | 0 | 分别返回同一原退款单号、提示本轮写明订单号、返回不可操作 |
| 未支付或已发货 | 仅用于拒绝后的解释 | 资格咨询与办理预检均为 0 | 两笔订单在咨询、办理预检、直接 MCP 事务三处结论一致，分别为未支付和已发货。直接事务各拒绝一次，退款记录均为 0 |
| 复核后且事务前变为已发货 | 0 | 1 | 锁定后拒绝，提交关闭为 `closed_no_refund`，原因 `shipped`，退款记录为 0 |
| 直接 MCP 授权和幂等 | 0 | 同键并发 2 次 | 无写凭据、无有效提交或归属不匹配的请求无法写入。同键并发只产生 1 条退款，再次请求返回原回执 |

真实合格办理样例返回模拟退款单号 `MOCK-RF-072eac57f32a4a188104948324278a60`。同一编号出现在该隔离库退款记录和后续重复办理回复中。提交记录含本轮保存的消费者消息，政策核验事件数为 0。真实普通状态和资格咨询各观察到 1 次意图提取事件，资格咨询没有资格提取或政策选择事件。

`outcomes --investigation-probe` 的 **19 个场景通过**，覆盖复核时状态变化、发送后回执丢失、明确回滚、提交确认丢失、提交断连、原会话及新会话核对、锁竞争、消费者隔离与 Host 并发。失败后可确认无退款的记录被关闭，结果仍不明的订单继续拦截重复办理。初次运行失败定位到验收探针仍检查旧政策 JSONL，改为核对持久化提交记录后重跑通过。

## 代码审查

- **仓库标准**。统一了重规划日志事件、资格规则的具名输入以及提交契约用语，补齐新业务分支的中文区域说明。工作范围按实施前快照核对。
- **工单规格**。审查用现有工作流复现带政策前提的措辞在移除政策门槛后可能误提交。独立授权判定已收紧，新增反例经同一入口验证均为零提交，并保留明确办理后另问依据的授权。

## 后续验收

正式拒绝依据、主动询问政策及其完整的多轮办理流转由工单 02 和 03 验收。本轮的政策 CLI 旧场景已同步调整断言并通过语法检查，未运行正式政策后端版本切换。RF-013 发布、目标开发库重置和旧政策核验 JSONL 清理由工单 04 在切换窗口验收。
