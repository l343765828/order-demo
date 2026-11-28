# 收紧提取条件后的 F145 单例实测

2026-11-27，只复测 F145。以五位尾号反例版为对照，替换第二步中「唯一目标明确时」这一句。新文字要求对完整候选做整串匹配，第四位后还有数字时排除整个候选。只有通过格式检查且被明确选为唯一目标时，才允许填 `extracted`。[对照提示词](../order-id-five-digit-counterexample-f145/prompt.txt#L35) · [本轮提示词](prompt.txt#L35)

输入为「查询支付，我只有旧格式单号 DEMO-73125。」预期 INTENT 的 `order_id_status / order_id / order_id_evidence` 为 `missing / null / null`，流程回复「请提供一个要查询的演示订单编号。」并结束。

| 验收项 | 五位尾号反例版 | 收紧提取条件版 |
| --- | --- | --- |
| INTENT 原始输出 | `extracted`，编号 `DEMO-73125`，依据为整句 | `missing`，编号 `DEMO-73125`，依据为 `null` |
| 流程 | 编号进入 `PLAN`，计划接受并触发隔离查单 | `INTENT` 校验失败，未进入 `PLAN`，未查单 |
| 最终回复 | 未找到可操作的订单。 | 本轮未能完成订单查询，请稍后再试。 |

**模型和工作流均未通过。** 原始响应的六个字段中，`order_id_status` 和 `order_id_evidence` 符合预期，`order_id` 仍保留了五位编号。将原始响应交给同一个 `IntentDecision` 校验可重现 `ValidationError`，错误为「缺号或歧义时订单号和原文依据必须为 null」。评分器记录 `raw_status_correct=1` 和 `missing_stopped=1`，补号回复计数为零。[对照日志](../order-id-five-digit-counterexample-f145/order-id-final-prompt-selected-live.jsonl#L2) · [本轮日志](order-id-final-prompt-selected-live.jsonl#L2) · [提取校验](../../model/refund/intent.py#L45)

本轮在北京时间 17:04:10–17:04:24 运行。使用与对照相同的样本、依赖、评分脚本和 Qwen 配置，在隔离应用副本及内存 checkpoint 中执行。Qwen3-8B 的唯一一次 INTENT 请求返回 HTTP 200，无超时或重试，退款提交为零。两份本轮日志均已与本地配置中的非空凭据核对。[重试记录](order-id-final-prompt-selected-retry.jsonl)

```powershell
$caseLogs = Join-Path ([IO.Path]::GetTempPath()) ('order-id-extracted-condition-f145-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-extracted-condition-f145/prompt.txt --sample-id F145 --output-dir $caseLogs
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-extracted-condition-f145/prompt.txt --sample-id F145 --output-dir $caseLogs --score
```
