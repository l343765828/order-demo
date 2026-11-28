# 五位尾号反例 F145 单例实测

2026-11-27，只复测 F145。以此前的固定长度提示词为对照，在 `DEMO-0042` 正例后插入一个 `DEMO-00421` 反例，演示排除完整候选、保留支付查询意图并将订单号三项填为 `missing / null / null`。两份模板的差异只有这四行。[对照提示词](../order-id-fixed-length-f145/prompt.txt) · [本轮提示词](prompt.txt)

输入为「查询支付，我只有旧格式单号 DEMO-73125。」模型侧预期是 `intent=order_question`、`order_goal=status`、`continues_eligibility_question=false`，同时 `order_id_status=missing`、`order_id=null`、`order_id_evidence=null`。工作流侧预期是仅运行 `INTENT`，直接回复「请提供一个要查询的演示订单编号。」

| 验收项 | 固定长度版 | 增加反例版 |
| --- | --- | --- |
| INTENT 原始输出 | `extracted`，编号 `DEMO-73125`，依据「我只有旧格式单号 DEMO-73125」 | `extracted`，编号 `DEMO-73125`，依据「查询支付，我只有旧格式单号 DEMO-73125。」 |
| 工作流 | `DEMO-73125` 进入 `PLAN`，计划接受该编号并调用隔离查单 | `DEMO-73125` 进入 `PLAN`，计划接受该编号并调用隔离查单 |
| 最终回复 | 未找到可操作的订单。 | 未找到可操作的订单。 |

**模型和工作流均未通过。** 原始响应的六个字段中只有 `order_id_evidence` 发生变化。本轮评分为 `wrong_selection`。本次结论适用于 F145 的这一次对照。[对照日志](../order-id-fixed-length-f145/order-id-final-prompt-selected-live.jsonl#L2) · [本轮日志](order-id-final-prompt-selected-live.jsonl#L2)

本轮在北京时间 16:51:28–16:51:49 运行。两轮保留相同的样本、评分脚本、依赖版本和 Qwen 配置。本轮使用隔离应用副本、内存 checkpoint 和隔离查单。INTENT 和 PLAN 各有一次 HTTP 200 响应，无超时或重试，退款提交为零。两份本轮日志均已与本地配置中的非空凭据核对。[重试记录](order-id-final-prompt-selected-retry.jsonl)

```powershell
$caseLogs = Join-Path ([IO.Path]::GetTempPath()) ('order-id-five-digit-counterexample-f145-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-five-digit-counterexample-f145/prompt.txt --sample-id F145 --output-dir $caseLogs
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-five-digit-counterexample-f145/prompt.txt --sample-id F145 --output-dir $caseLogs --score
```
