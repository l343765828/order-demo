# 工单 02 订单号提取实模对照

日期：2026-11-26

**结论：两版各完成同一批 200 条真实 Qwen 样本，改造版未通过验收。** 精确提取率为 94.26%，低于 98%。改造版有 3 条本轮原文中不存在的编号和 1 条截断编号进入 `PLAN`。否定与纠正准确率、普通 `INTENT` 调用次数、歧义场景的退款办理授权均达到门槛。

## 样本与环境

验收集在正式回放前固定。200 条消息均由本票独立编写，分别标注目标订单、提取状态和是否应建立退款办理授权。逐条确认与两版意图提示中的示例没有原文重合。替换消息中的演示订单 ID 后，200 条消息仍各不相同。每条样本使用独立内存 checkpoint 和虚构消费者，13 条样本带历史订单，21 条样本预置待补号的资格咨询。

| 样本类别 | 数量 |
| --- | ---: |
| 普通查询及资格补号 | 36 |
| 否定、纠正与排除 | 48 |
| 多个编号与歧义 | 24 |
| 缺号和历史指代 | 24 |
| 编号边界异常 | 24 |
| 提示词注入 | 20 |
| 退款办理授权 | 24 |
| 合计 | 200 |

其中 122 条有明确预期编号，50 条无可用编号，28 条存在歧义。4 条应建立办理授权。验收集 SHA-256（换行规范为 LF）为 `72cd9ba43e897df59e4e3ec43d0edceb014afddc36157f2114e15adef1d5f8c3`。样本见 [order_id_extraction_live.tsv](../test/order_id_extraction_live.tsv)。

| 版本 | 代码版本 | 应用源码 SHA-256 |
| --- | --- | --- |
| 改动前 | 工单 01 固定的 `refs/baselines/order-id-extraction-01`，树 `73840adf4b6adceac95c679dc03c96235b3e4231` | `7e0249c53df9c957685222d897ef7bff83a15f29e6d8c36eecc6f2e3b2249f77` |
| 改造版 | `c4e213e130b868500283a1e8a87eba2710eadb96` 加工单 01 交付后的工作区 | `b16f22dac5444270871ad390c71821c54920aab90f03e0624e66da71c7db3b79` |

源码指纹按相对路径及统一为 LF 的 Python、SQL 内容计算。运行配置由两版共用同一个文件，已单独记录实际使用的 Qwen 参数。对照使用 Windows、Python 3.11.7、LangGraph 1.2.22、langchain-openai 1.8.0、OpenAI SDK 3.55.1、Pydantic 2.14.3 和 httpx 0.28.1。

两版连接配置中的同一免鉴权 Qwen 入口 `http://192.168.18.53:30080/qwen3/v1`，模型为 `Qwen/Qwen3-8B`，温度为 0，关闭思考，最多生成 512 token，HTTP 超时 90 秒，SDK 重试为 0。实测前严格 JSON Schema 探针已通过。

## 验收口径

- 两版均由现有 `run_refund_turn` 入口执行。真实请求经过该版本的 `QwenDecider`，记录 JSON Schema 名称、HTTP 状态、模型原始消息及耗时。订单与政策使用隔离回执。订单查询返回无可操作订单，政策检索返回失败，退款执行组件沿用未找到本人订单时停止办理的分支。未连接 MySQL、MCP 或 Milvus，也未提交退款。
- 仅当节点实际执行时，从运行前 checkpoint 读取进入 `PLAN` 的编号。逐条保留已接受的计划、实际发给隔离工具的订单号、最终决定、消费者回复和授权证据。模型原始编号与工作流采用的编号独立观察。
- 精确提取率以 122 条有明确预期编号的样本为分母，比较大写规范化后的 `PLAN` 入参。否定与纠正准确率用相同算法计算 48 条样本。预期缺号与歧义记录是否停止调查及是否给出对应提示。误选指采用了原文里另一个完整编号，截断指采用了较长编号的一部分或样本明确标注的不完整片段，编造指编号不见于本轮原文。三者均以进入 `PLAN` 为准。
- 普通 `INTENT` 路径的样本分母为 197 条，按实际发送的 HTTP 请求计数。另有 3 条预期走零调用快速路径，包括裸编号补号和两条单笔明确退款指令。总调用次数另计同一轮的既有 `PLAN` 等模型请求。
- 工作流延迟从调用 `run_refund_turn` 前到它返回，用单调时钟计时，包含模型、工作流及本地替身耗时，排除图初始化、前置 checkpoint 注入和结果写盘。HTTP 延迟从出站请求钩子到读取完整响应体，包含网络和服务排队。两版同时开始，各自最多处理 4 个并发样本，P95/P99 均按最近秩 `ceil(p × n)` 计算。结果对应当次服务负载和隔离入口。

## 门槛与结果

| 指标 | 改动前 | 改造版 | 改造版判定 |
| --- | ---: | ---: | --- |
| 精确提取率，以进入 `PLAN` 的编号计 | 62/122（50.82%） | 115/122（94.26%） | 未达 98% |
| 否定与纠正准确率 | 1/48（2.08%） | 46/48（95.83%） | 达到 95% |
| 编造或截断编号进入 `PLAN` | 4 | 4 | 超过 0 |
| 歧义场景错误建立退款办理授权 | 0/28 | 0/28 | 达到 0，含 8 条歧义办理指令 |
| 普通 `INTENT` 路径恰好一次 HTTP 调用 | 197/197 | 197/197 | 达到一次 |

| 观察项 | 改动前 | 改造版 |
| --- | ---: | ---: |
| 原始 `INTENT` 编号准确率，有目标且需要模型的样本 | 114/119（95.80%） | 113/119（94.96%） |
| 原始 `INTENT` 提取状态准确率 | 无该字段 | 165/197（83.76%） |
| 原始编号与实际进入 `PLAN` 的编号不一致 | 100 | 13 |
| 漏提取，有预期编号但没有编号进入 `PLAN` | 60 | 4 |
| 误选本轮原文的其他完整编号 | 6 | 11 |
| 编造，编号不见于本轮原文，包含误用历史编号 | 2（含误用历史 2） | 3（含误用历史 2） |
| 截断或采用明确标注的不完整编号 | 2 | 1 |
| 缺号样本未进入 `PLAN` / 收到补号提示 | 32/50 / 27/50 | 40/50 / 38/50 |
| 歧义样本未进入 `PLAN` / 收到指定单号提示 | 28/28 / 4/28 | 26/28 / 2/28 |
| 只读缺号子集未向 `PLAN` 传递编号 | 34/42 | 36/42 |
| 只读歧义子集未向 `PLAN` 传递编号 | 20/20 | 18/20 |
| 授权正例中正确建立退款办理授权 | 4/4 | 3/4 |
| 全集错误建立退款办理授权 | 0/196 | 0/196 |
| `INTENT` 输出校验失败并降级 | 0 | 12 |

三条预期快速路径均未调用模型。全轮调用数从 241 增至 291，`INTENT` 保持 197 次，`PLAN` 从 44 增至 94 次。两版单轮 0、1、2 次调用的样本数分别为 3、153、44 和 3、103、94。所有模型请求均返回 HTTP 200，响应报告的模型均为 `Qwen/Qwen3-8B`。改造版的 12 次降级发生在收到模型响应后的 `INTENT` 校验。

| 延迟口径 | 改动前 P95 | 改动前 P99 | 改造版 P95 | 改造版 P99 |
| --- | ---: | ---: | ---: | ---: |
| 工作流响应，200 条 | 112.097 秒 | 119.277 秒 | 109.635 秒 | 116.356 秒 |
| `INTENT` HTTP，197 次 | 61.158 秒 | 66.882 秒 | 61.231 秒 | 64.035 秒 |

## 逐例观察

- **A03 和 B14** 分别把拼接的 `DEMO-4105, DEMO-4106` 和已明确不完整的 `DEMO-51…` 带入 `PLAN`。两条都被后续计划核对拒绝，未向隔离订单工具发出查询。
- **M13 和 R20** 将历史里的 `DEMO-9000` 当作本轮编号，已到达隔离订单查询。**A15** 在双单查询中只查询一单。**I01、I03、I07、I09、I17、I20** 将干扰文本中的完整编号带入 `PLAN`，计为误选。
- **C10、C36、B21** 的模型原始结果为缺号。**A24、I01、I03** 已有明确目标，实际进入 `PLAN` 的是其他编号。**R24** 的模型原文选对了编号和办理意图，现有独立授权校验要求重报单号。该轮没有建立授权，也没有进入 `PLAN`。
- 待补号路由的单独评分覆盖 8 条接续、4 条明确切换和 8 条无目标时保留待补问题。改动前分别通过 1、2、0 条，改造版分别通过 8、2、7 条。**N34** 接续了旧问题，**C36** 把新的物流查询保存成待补号资格问题，**I09** 采用干扰编号后清除了待补问题。带待补状态的 **C35** 也参与主指标计数。
- `PLAN` 入参、已接受的订单计划和隔离工具实际收到的编号分别记录。基线 **N30、C36** 的决定携带了预期编号，但走了政策计划。基线共 72 条在 `PLAN` 携带编号，67 条获得含订单的计划。改造版分别为 130 和 128 条。

## 原始证据

两版于北京时间 20:17 同时开始，改动前在 21:10 完成，改造版在 21:17 完成。两份日志均包含 200 条同序样本及完成标记。采集时脚本 SHA-256 为 `6bf0832b5dd35479bb3e3a676c7fe2f0445930e806244777b434fa67af980c1f`（换行规范为 LF）。最终脚本在同一份采集数据上重算，细化了含省略号的截断片段、历史编号误用和补号路由判断。

| 原始日志 | SHA-256（LF） |
| --- | --- |
| [改动前逐条记录](order-id-extraction-02-baseline.jsonl) | `15a8c3b8e11182a06fcca65f7909bb81e4efacb84f79d518dce8eeb71a55723f` |
| [改造版逐条记录](order-id-extraction-02-current.jsonl) | `1eecc7c77a01e45bc0f8f217c30069d289a7165696df54432ddb5693bec9f9e2` |

已在本地用配置中的凭据值检查两份日志，均无匹配。订单存在性、归属和政策检索使用隔离替身，实测在退款提交前结束。本轮实测结论覆盖订单提取、路由、授权及相应延迟。解除验收阻塞需让精确提取率达到 98% 且编造或截断编号进入 `PLAN` 的数量归零，并用同一批样本复测。涉及调整定稿的业务行为、数据职责或恢复规则，实施前须由用户确认。

## 离线核对

改造版定向回归 57 项通过，全量离线回归 244 项通过。用模拟 HTTP 把 200 条样本分别送入两版工作流，核对脚本的补号 checkpoint、订单工具回执、授权记录和计数分母。两次演练均完整写出 200 条观测。规范和规格两项审查复查了基线校验、响应记录、计数分母和补号路由。

```powershell
.\.venv\Scripts\python.exe -m pytest -q test/test_order_id_extraction.py test/test_qwen_client.py
.\.venv\Scripts\python.exe -m pytest -q test
.\.venv\Scripts\python.exe -m mypy --follow-imports=silent --check-untyped-defs test/verify_order_id_extraction_live.py
.\.venv\Scripts\python.exe -m py_compile test/verify_order_id_extraction_live.py
```

## 复测命令

从仓库根目录执行，未跟踪的本机配置可用两次相同的 `--config` 路径提供。

用已交付的日志重算本次对照。

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py compare evidence/order-id-extraction-02-baseline.jsonl evidence/order-id-extraction-02-current.jsonl
```

重新发送真实模型请求。

```powershell
$baselineTree = Join-Path ([IO.Path]::GetTempPath()) ('order-id-02-' + [guid]::NewGuid().ToString('N'))
$baselineRevision = git rev-parse refs/baselines/order-id-extraction-01
$currentRevision = git rev-parse HEAD
git archive --format=zip --output="$baselineTree.zip" $baselineRevision
Expand-Archive -LiteralPath "$baselineTree.zip" -DestinationPath $baselineTree
$retestRoot = (Get-Location).Path
$retestRun = {
    param($repo, $source, $version, $revision)
    Set-Location -LiteralPath $repo
    .\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py run --source $source --version $version --revision $revision --workers 4 --output "evidence/order-id-extraction-02-$version.retest.jsonl"
    if ($LASTEXITCODE -ne 0) { throw "$version 回放失败，退出码 $LASTEXITCODE" }
}
$baselineJob = Start-Job -ScriptBlock $retestRun -ArgumentList $retestRoot, $baselineTree, 'baseline', $baselineRevision
$currentJob = Start-Job -ScriptBlock $retestRun -ArgumentList $retestRoot, $retestRoot, 'current', "$currentRevision+working-copy"
Receive-Job -Job $baselineJob,$currentJob -Wait
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py compare evidence/order-id-extraction-02-baseline.retest.jsonl evidence/order-id-extraction-02-current.retest.jsonl
```
