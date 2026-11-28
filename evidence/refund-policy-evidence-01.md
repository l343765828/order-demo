# 工单 01 在订单资格咨询中引用生效政策

验收日期为 2026-11-28。工作区起点为 `c4e213e`，已有未提交改动。工单无前置依赖，已有实现和上轮真实联验记录。本轮在当前工作区复核资格咨询，检查摘录是否遗漏紧接其后的独立「除外」分句。授权办理沿用原有检查。

## 测试基线与回归

使用仓库现有的退款工作流测试和 CLI 联验入口。

```powershell
$python = '.venv/Scripts/python.exe'
& $python -X utf8 -m pytest -q test
& $python -X utf8 -m pytest -q test/test_policy_evidence.py::test_refunded_order_does_not_apply_permission_excerpt_omitting_next_sentence_exception
& $python -X utf8 -m pytest -q test/test_policy_evidence.py -k next_sentence
& $python -X utf8 -m pytest -q test/test_policy_evidence.py
& $python -X utf8 -m pytest -q test/test_policy_evidence.py test/test_order_flow.py test/test_order_id_extraction.py
& $python -m mypy --ignore-missing-imports --check-untyped-defs config implementations interfaces model mcp_service repositories utils workflows cli.py manage.py publish_policy.py
& $python -m compileall -q config implementations interfaces model mcp_service repositories utils workflows test cli.py manage.py publish_policy.py
& $python -m pip check
git diff --check
git diff --cached --check
```

- 修改前全量 244 项通过，定向 116 项通过，mypy 检查 90 个源文件通过。
- 先运行新增回归测试，观察到已退款订单因一段漏掉下一句「除外」的许可摘录被误报为政策冲突。审查期间再以测试复现两种问题，包括许可原文未指定退款方式时的误报，以及独立后续规则有自己的限定或例外时丢失有效引用。修正后，前一种场景引用适用的「不重复办理」原文和 `RF-099` 出处，后一种场景保留有效许可引用。
- 修正后政策依据测试 51 项通过，定向 120 项通过，全量 248 项通过，mypy 检查 90 个源文件通过。编译、依赖和 Git 差异检查通过。
- 受控工作流覆盖同批近似候选、正式条款和 FAQ、例外条件的合取关系、遗漏例外、适用政策冲突、部分退款、未知条件、错误原文、错误回执、提取失败和检索失败。依据不足时保留已确认订单事实。

## 真实 CLI 联验

```powershell
$config = Join-Path $env:LOCALAPPDATA 'agent-order-demo/development.json'
$run = Join-Path ([System.IO.Path]::GetTempPath()) ('agent-order-eligibility-01-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
& $python -X utf8 test/verify_policy_cli.py --config $config --eligibility-switch --evidence (Join-Path $run 'switch.jsonl')
```

`$run` 是仓库外的临时目录。各轮使用随机命名的 MySQL 库和 Milvus 集合、独立 Streamable HTTP MCP 及临时 Host 副本。首次 MySQL 查询 `SELECT VERSION()` 返回 **8.0.46**，然后才创建隔离数据。实际 Milvus 为 **3.0.2**，结构化推理由 `Qwen/Qwen3-8B` 提供，政策通过现有向量检索和重排链读取。

### 基线与最终复验

基线和最终代码的全场景真实联验均退出 0。最终复验以相同命令将临时输出文件名改为 `revised-switch.jsonl`。两轮均观察到发布 CLI 在同一调查正式补查前激活目录修订 2。该调查的两次回执依次为 `consultation` 无指定修订、返回 1，及 `formal` 指定 1、返回 1，答复引用 **1.0 版 `RF-003`**。后续四次新调查的回执均为修订 2。下列出处均来自回执中的《演示退款政策与常见问题》（文档编号 `DEMO-REFUND-POLICY`），与两轮可见答复核对一致。

| 订单 | 已确认事实和办理结论 | 2.0 版原文摘录及出处 |
| --- | --- | --- |
| `DEMO-1001` | 已支付、未发货、尚未模拟退款，符合整单原路退款条件 | `RF-083`「仅限已付款且尚未发货的本人订单按原支付渠道退回整笔实付款，已有成功模拟退款记录的订单除外。」 |
| `DEMO-1002` | 未支付，没有可退款款项 | `RF-004`「未支付的订单没有可退款的已支付款项，不办理退款。」 |
| `DEMO-1004` | 已发货，超出演示退款范围 | `RF-004`「已发货的订单超出本演示支持范围，不办理退款；本演示不继续处理退货、物流或验收。」 |
| `DEMO-1003` | 已有模拟退款记录，不能重复办理 | `RF-009`「同一订单只产生一笔成功的模拟退款。订单已经成功退款时，返回原模拟退款单号和原退款金额，说明该订单已完成模拟退款，不再次办理。」 |

`RF-083` 的实际结构化输出包含 `paid`、`unshipped`、`owned`，排除 `refunded`，结论为 `full_original`。每轮五个会话的 Checkpoint 均无退款办理授权或办理结果，数据库前后保持 1 笔种子退款和 0 笔提交，MCP 退款写调用为 0。两轮均验证了隔离会话、Checkpoint、集合和库已清理。复验日志和本记录中均未发现配置凭据。

### 中间复跑

一次中间版本的真实联验超过脚本 180 秒正式补查等待窗口，退出 1。随后收到的 CLI 答复引用 1.0 版 `RF-003`，该次运行未完成切版过程与四场景核对。隔离会话、Checkpoint、集合和库均已清理。最终复验通过了同一脚本的全部断言。

## 代码审查

- 仓库规范审查核对了中文注释、代码区域、具名枚举和测试目录，复核通过。
- 工单规格审查用受控工作流复现三种遗漏后置例外的资格结论，并核对同一条款内两条独立规定各自的限定。修订后通过复测。审查抽查的 100 组办理摘录核验与起点一致。
- 摘录校验使用原文标点识别紧接的「除外」分句。其他语义表述继续由结构化提取提供限定与例外，再由代码核对订单事实和检索回执。
