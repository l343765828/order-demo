# 订单编号来源实测与验收

2026-11-27，使用 `Qwen/Qwen3-8B` 实测 F121。Astra（`gpt-6.2-astra`，`max`）根据原始响应和流程日志定位首个偏差并提出单一假设。Sol（`gpt-6.2-sol`，`xhigh`）把提示词应用到隔离副本，执行实测与评分。样本、历史、模型参数、Schema、工作流和评分逻辑沿用既有验证环境。

## 第 00 轮 基线复现

基线是已通过 F145 的完整模板，快照 SHA-256 为 `a905b27a6cfee999b88d0c974ca0aec8a883753bc8bb299785047cc2c4ef5ceb`。模板把待补号槽位渲染为「否」，对话槽位依次放入历史「请问 DEMO-9000 什么时候发货？」和本轮「我想接着查你刚刚那条答复说的订单物流。」，两项角色均为 `user`。模板已明确要求只扫描数组末项。[F145 基线验收](../order-id-missing-json-f145/report.md) · [本轮模板快照](00-baseline/prompt.txt)

首个可观察偏差出现在 `INTENT` 原始响应。查询类型和接续标记正确，模型却把历史编号和历史整句分别填进 `order_id` 和 `order_id_evidence`，并将状态标为 `extracted`。`IntentDecision` 校验通过，提取事件保留这三项。流程经过 `intent → plan → execute → observe → replan`，`PLAN` 收到 `DEMO-9000`，隔离查单也收到 `DEMO-9000`，实际回复「未找到可操作的订单。」两次模型请求均为 HTTP 200，超时和脚本重试均为零。评分标记 `historical_reuse` 和 `fabricated`。[原始日志](00-baseline/order-id-final-prompt-selected-live.jsonl#L2) · [重试日志](00-baseline/order-id-final-prompt-selected-retry.jsonl) · [评分](00-baseline/score.log)

## 第 01 轮 演示从末项逐字找编号

Astra 根据基线原始 JSON 定位到首个错误字段 `order_id`，随后的提取状态和依据也错用历史。Astra 的假设是，模型把用于解释当前指代的历史对象同时当成可照录的编号。在实际数组之前增加一组历史相同、末项不同的对照示例，分别演示本轮有有效编号和本轮仅用指代时的填值。负例展示完整六字段 JSON。本轮预测前三个正确字段保持原值，提取三字段变为 `missing / null / null`，流程在 `INTENT` 校验后直接补号。[第 01 轮模板](01-last-message-examples/prompt.txt)

| F121 原始字段 | 第 00 轮 | 第 01 轮 |
| --- | --- | --- |
| `intent` | `order_question` | `order_question` |
| `order_goal` | `status` | `status` |
| `continues_eligibility_question` | `false` | `false` |
| `order_id_status` | `extracted` | `missing` |
| `order_id` | `DEMO-9000` | `null` |
| `order_id_evidence` | 请问 DEMO-9000 什么时候发货？ | `null` |

F121 的 `IntentDecision` 校验通过，提取事件为 `missing / null / null`。只经过 `intent`，`node_failed`、`PLAN` 和查单均为零，回复「请提供一个要查询的演示订单编号。」唯一一次模型请求为 HTTP 200，重试为零。评分中 `missing_prompt=1`、`node_failures=0`。Astra 的预测与本轮两层结果一致。[原始日志](01-last-message-examples/order-id-final-prompt-selected-live.jsonl#L2) · [重试日志](01-last-message-examples/order-id-final-prompt-selected-retry.jsonl) · [评分](01-last-message-examples/score.log)

同一模板全量回归的 11 条全部通过 `IntentDecision` 校验。F121 的原始响应与单例复测一致。9 条历史来源样本的提取状态都是 `missing`，编号和依据均为 `null`，历史编号进入 `PLAN` 或查单均为零。F023 提取并实际查了本轮的 `DEMO-7033`，F145 正常追问查询编号。12 次请求均为 HTTP 200，重试为零。[首轮回归原始日志](01-regression/order-id-final-prompt-selected-live.jsonl) · [重试日志](01-regression/order-id-final-prompt-selected-retry.jsonl) · [评分](01-regression/score.log)

逐条核对仍发现业务分类偏差。F122 正确追问资格咨询编号，但原始接续标记为 `true`，当时没有待补号的资格咨询。F161、F171 把退款状态查询分成资格咨询，并将接续标为 `true`，最终追问资格咨询编号。F196 把办理意图识别成状态查询，最终追问通用查询编号。评分器将这 10 条缺号回复都记入 `missing_prompt`，因此继续用原始响应和实际业务分支验收。[F122](01-regression/order-id-final-prompt-selected-live.jsonl#L4) · [F161](01-regression/order-id-final-prompt-selected-live.jsonl#L10) · [F171](01-regression/order-id-final-prompt-selected-live.jsonl#L11) · [F196](01-regression/order-id-final-prompt-selected-live.jsonl#L12)

## 第 02 轮 独立判断业务意图和目标

Astra 假设，模型在缺号时沿用了示例中的业务值，或者根据「退款」和历史指代直接归类。仅替换实际对话前的最后一段指示，在同样缺号的条件下演示查退款进度、咨询退款资格和要求办理三种请求。接续字段使用应用给出的待补号状态，最后按既有规则从数组末项填写编号字段。预测 F122 的接续标记为 `false`，F161、F171 归入 `status` 且接续为 `false`，F196 的原始意图为 `refund_request`，已通过的提取结果保持原值。[第 02 轮模板](02-business-fields/prompt.txt)

| 样本 | 相对第 01 轮的原始字段变化 | 实际流程 |
| --- | --- | --- |
| F023、F121、F125、F145 | 六字段均保持 | F023 只查本轮 `DEMO-7033`。其余正常追问查询编号。 |
| F122、F123、F124、F126 | `missing / null / null` 变为 `extracted / DEMO-9000 / 历史原句`。其余三字段保持。 | 四条均把历史编号送入 `PLAN` 并实际查单，回复「未找到可操作的订单。」 |
| F161、F171 | 接续标记 `true` 变为 `false`。其余五字段保持，查询目标仍为 `eligibility`。 | 只执行 `intent`，仍追问资格咨询编号。 |
| F196 | 查询目标 `status` 变为 `eligibility`。其余五字段保持，意图仍为 `order_question`。 | 只执行 `intent`，从追问查询编号改为追问资格咨询编号。 |

15 次模型请求均为 HTTP 200，无重试。11 条均通过 `IntentDecision` 校验，提取事件与原始提取字段一致，无 `node_failed`、退款授权或提交。评分记录 `historical_reuse=4`。四条已通过的来源样本发生回退，因此弃用此版。[原始日志](02-business-fields/order-id-final-prompt-selected-live.jsonl) · [重试日志](02-business-fields/order-id-final-prompt-selected-retry.jsonl) · [评分](02-business-fields/score.log)

## 第 03 轮 演示省略状态二字的退款查询

以第 01 轮为父模板，保留对话数组前的提取示例和指示。Astra 假设，原业务描述展示了「退款状态」，模型在遇到省略「状态」的退款查询时补出了资格咨询含义。仅替换前部的状态查询说明，用同类但不同措辞演示六字段输出。预测 F161、F171 转为 `status / false` 并正常追问查询编号，原本正确的订单提取结果保持。[第 03 轮模板](03-refund-status/prompt.txt)

相对第 02 轮，F122、F123、F124、F126 的状态、编号和依据恢复为 `missing / null / null`，停止历史查单，其余三字段保持。F161、F171 的六字段保持，仍是 `eligibility / false`，未支持本轮预测。F196 仅接续标记从 `false` 变为 `true`，仍是资格咨询。F023、F121、F125、F145 的最终六字段和流程保持。其中 F125 首次请求超时且无原始模型决定，脚本重试一次返回 HTTP 200，正常补号。以重试后有效记录计，11 条均通过 `IntentDecision` 校验，提取事件与原始字段一致。`PLAN` 和查单仅发生在 F023，查的是本轮的 `DEMO-7033`。[原始日志](03-refund-status/order-id-final-prompt-selected-live.jsonl) · [F125 重试](03-refund-status/order-id-final-prompt-selected-retry.jsonl#L2) · [评分](03-refund-status/score.log)

## 第 04 轮 在有历史的缺号示例中演示退款状态查询

Astra 假设，第 03 轮首部的单句退款示例无法迁移到带历史的场景，而靠近实际数组的双轮缺号示例只展示了支付查询。本轮只将该双轮示例的请求从支付查询改成退款进度查询，保留历史、六字段示例及提取规则。预期 F161、F171 的 `order_goal` 转为 `status`，其他五字段保持，最终追问查询编号。先测两例和 F121、F122、F196、F023、F145 五条对照。[第 04 轮模板](04-history-refund-status/prompt.txt)

7 条均通过 `IntentDecision` 校验，8 次请求均为 HTTP 200，重试为零。F161、F171 的唯一原始变化是 `order_goal` 从 `eligibility` 变为 `status`，均改为追问查询编号，符合预测。F121、F023、F145 和 F122 六字段及流程均与上一轮相同。F122 实际追问资格编号，接续仍误填 `true`。F196 的接续从 `true` 变为 `false`，仍被识别为资格咨询。六条缺号样本仅经过 `intent`，无历史查单、退款授权或提交。F023 实际只查本轮 `DEMO-7033`。[原始日志](04-history-refund-status/order-id-final-prompt-selected-live.jsonl) · [重试日志](04-history-refund-status/order-id-final-prompt-selected-retry.jsonl) · [评分](04-history-refund-status/score.log)

## 第 05 轮 以应用待补号状态判断是否接续

Astra 假设，模型把消费者提及同一订单误当作正在响应资格补号追问。仅替换模板前部的接续判断。当应用记录为「否」时，接续位固定为 `false`。示例明确展示新的资格咨询可以同时填 `eligibility / false`。主预测是 F122 的 `continues_eligibility_question` 从 `true` 改为 `false`，其余五字段和资格补号回复保持。同轮复测全部 11 条，覆盖第 04 轮尚未实测的四条历史查询。[第 05 轮模板](05-pending-gate/prompt.txt)

与第 04 轮实测的重叠样本中，F023、F121、F122、F145、F161、F171、F196 的六字段和流程均保持，F122 的接续预测未实现。与第 03 轮记录相比，F124、F125 的六字段和补号回复保持，F123、F126 的提取三字段从 `missing / null / null` 回退成 `extracted / DEMO-9000 / 本轮整句`。本轮整句未出现该编号，校验仍通过，两个提取事件与模型一致。两个流程都进入 `PLAN` 并实际查历史编号，回复「未找到可操作的订单。」13 次请求均为 HTTP 200，无重试、`node_failed`、退款授权或提交。评分记录 `historical_reuse=2`，弃用此版。[原始日志](05-pending-gate/order-id-final-prompt-selected-live.jsonl) · [重试日志](05-pending-gate/order-id-final-prompt-selected-retry.jsonl) · [评分](05-pending-gate/score.log)

### 第 04 轮原样扩测

为核对回退出现在哪个版本，用原样第 04 轮模板完整重跑 11 条。F123、F124、F126 的提取状态均为 `extracted`，编号均为 `DEMO-9000`，实际进入 `PLAN` 并查历史单。F123 依据仍为不含编号的本轮整句，F124、F126 的依据为历史原句。第 03 轮这三条正常补号。第 04 轮七条定向样本的六字段与流程均复现，其中 F145 首轮超时，脚本重试后恢复。该扩测表明第 05 轮改动前已出现借号，原样第 04 轮也不能入围。[原始日志](04-regression/order-id-final-prompt-selected-live.jsonl) · [F145 重试](04-regression/order-id-final-prompt-selected-retry.jsonl#L2) · [评分](04-regression/score.log)

## 第 06 轮 依据须取自本轮且包含完整编号

第 04 轮完整回归有两种证据问题。F123 的依据取自本轮但不含编号，F124 和 F126 的依据包含编号但整句取自历史。Astra 假设，模型分别为编号和依据填值时没有做连续的原文定位。本轮以第 04 轮为父模板，只替换对话数组前的最后一句说明，要求在当前 `content` 内逐字找到连续依据，再从依据内逐字抄出完整编号。预期 F123、F124、F126 恢复为缺号并正常补号，同时保持 F121、F161、F171、F023 和 F145 的通过结果。[第 06 轮模板](06-evidence-includes-id/prompt.txt)

相对第 04 轮完整回归，F124、F126 保持前三字段，提取三字段恢复为 `missing / null / null`，校验通过后仅经过 `intent` 并正常补号。F123 只把依据换成历史原句，仍输出 `extracted / DEMO-9000`，实际进入 `PLAN` 并查询历史编号。F161 的唯一变化是 `order_goal` 从 `status` 回退为 `eligibility`，最终重新追问资格编号。F023、F121、F122、F125、F145、F171、F196 原始六字段和有效流程保持。全部通过 `IntentDecision` 校验，13 次请求均为 HTTP 200，无重试、节点失败、退款授权或提交。评分记录 `historical_reuse=1`。该版仍出现实际历史查单，因此选用已全量验证历史来源问题的第 03 轮。[原始日志](06-evidence-includes-id/order-id-final-prompt-selected-live.jsonl) · [重试日志](06-evidence-includes-id/order-id-final-prompt-selected-retry.jsonl) · [评分](06-evidence-includes-id/score.log)

## F196 的固定流程阻塞

使用原样 F196 历史和本轮原文、无待补号状态，把已通过 `IntentDecision.model_validate_json` 的 `refund_request / null / false / missing / null / null` 交给现有 `resolve_intent`。`_REFUND_REQUEST_WORDS` 对「我现在想退了」无匹配，`directives` 为空，流程先回复「请明确说明是否要办理这笔订单的退款。」并返回，未到「请明确指定一个要办理退款的演示订单编号。」分支。此诊断说明，即使提示词让模型正确识别办理意图，现有流程仍会在补号前要求确认是否办理。[诊断输出](f196-routing.log) · [办理指令匹配](../../implementations/refund/intent.py#L29) · [提前追问分支](../../implementations/refund/intent.py#L253) · [办理补号分支](../../implementations/refund/intent.py#L277)

用相同输入和已校验的办理决定在内存 checkpoint 中重放完整工作流。流程只经过 `intent`，提取事件为 `missing / null / null`，无退款授权、查单或提交，仍回复「请明确说明是否要办理这笔订单的退款。」此重放用于定位固定分支，Qwen 的真实输出以各轮原始日志为准。[工作流重放记录](f196-graph-replay.jsonl)

## 交付候选验收表

第 03 轮用于复用和复测。它让全部 9 条历史来源样本在原始响应中输出 `missing / null / null`，且历史编号进入 `PLAN` 和查单的次数均为零。F023 的依据「新单号是 DEMO-7033」来自该轮原文。评分和下表采用 F125 成功重试后的结果。各有效 `INTENT` 响应均通过 `IntentDecision` 校验，提取事件与原始提取字段一致。全组退款授权和提交为零。[完整候选模板](03-refund-status/prompt.txt) · [首轮日志](03-refund-status/order-id-final-prompt-selected-live.jsonl) · [重试日志](03-refund-status/order-id-final-prompt-selected-retry.jsonl) · [评分](03-refund-status/score.log)

| 样本 | 原始意图 / 目标 / 接续 | 原始状态 / 编号 / 依据 | 实际流程与回复 | 验收 |
| --- | --- | --- | --- | --- |
| F121 | `order_question / status / false` | `missing / null / null` | 仅 `INTENT`，请提供一个要查询的演示订单编号。 | 通过 |
| F122 | `order_question / eligibility / true` | `missing / null / null` | 仅 `INTENT`，请提供要咨询资格的演示订单编号。 | 资格补号通过，接续误标 |
| F123 | `order_question / status / false` | `missing / null / null` | 仅 `INTENT`，请提供一个要查询的演示订单编号。 | 通过 |
| F124 | `order_question / status / false` | `missing / null / null` | 仅 `INTENT`，请提供一个要查询的演示订单编号。 | 通过 |
| F125 | `order_question / status / false` | `missing / null / null` | 重试仅 `INTENT`，请提供一个要查询的演示订单编号。 | 重试后通过 |
| F126 | `order_question / status / false` | `missing / null / null` | 仅 `INTENT`，请提供一个要查询的演示订单编号。 | 通过 |
| F161 | `order_question / eligibility / false` | `missing / null / null` | 仅 `INTENT`，请提供要咨询资格的演示订单编号。 | 查询目标错误 |
| F171 | `order_question / eligibility / false` | `missing / null / null` | 仅 `INTENT`，请提供要咨询资格的演示订单编号。 | 查询目标错误 |
| F196 | `order_question / eligibility / true` | `missing / null / null` | 仅 `INTENT`，请提供要咨询资格的演示订单编号。无退款授权或提交。 | 办理补号未通过，另有固定流程阻塞 |
| F023 | `order_question / status / false` | `extracted / DEMO-7033 / 新单号是 DEMO-7033` | `PLAN` 与隔离查单均只收到 `DEMO-7033`。回复「未找到可操作的订单。」 | 通过 |
| F145 | `order_question / status / false` | `missing / null / null` | 仅 `INTENT`，请提供一个要查询的演示订单编号。 | 通过 |

## 重放命令

```powershell
$casePrompt = 'evidence/order-id-history-source-f121/03-refund-status/prompt.txt'
$caseOutput = 'evidence/order-id-history-source-f121/replay-' + [guid]::NewGuid().ToString('N')
$caseOptions = @('--prompt', $casePrompt, '--output-dir', $caseOutput)
foreach ($caseId in @('F121', 'F122', 'F123', 'F124', 'F125', 'F126', 'F161', 'F171', 'F196', 'F023', 'F145')) {
    $caseOptions += @('--sample-id', $caseId)
}
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py @caseOptions
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py @caseOptions --score
```
