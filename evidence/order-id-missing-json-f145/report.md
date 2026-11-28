# 补全缺号 JSON 示例后的 F145 单例实测

2026-11-27，只复测 F145。以收紧提取条件版为对照，把 `DEMO-00421` 五位尾号反例末尾的两行填值说明替换成填写 `order_id` 的规则和六字段 JSON 示例。[对照提示词](../order-id-extracted-condition-f145/prompt.txt#L25) · [本轮提示词](prompt.txt#L25)

输入为「查询支付，我只有旧格式单号 DEMO-73125。」预期 INTENT 同时输出 `order_id_status=missing`、`order_id=null` 和 `order_id_evidence=null`，保留支付状态查询意图，并由流程直接请消费者补号。

| 验收项 | 收紧提取条件版 | 完整 JSON 反例版 |
| --- | --- | --- |
| INTENT 原始输出 | `missing / DEMO-73125 / null` | `missing / null / null` |
| `IntentDecision` 校验 | `ValidationError` | 通过 |
| 工作流 | INTENT 异常结束，回复「本轮未能完成订单查询，请稍后再试。」 | INTENT 正常结束，回复「请提供一个要查询的演示订单编号。」 |

**F145 的模型输出和工作流均通过。** 与对照相比，六个原始字段中只有 `order_id` 从 `DEMO-73125` 变为 `null`。流程未进入 `PLAN`，查单和退款提交均为零。评分器记录 `missing_prompt=1`、`node_failures=0`，本轮共执行一个样本。[对照日志](../order-id-extracted-condition-f145/order-id-final-prompt-selected-live.jsonl#L2) · [本轮日志](order-id-final-prompt-selected-live.jsonl#L2)

本轮在北京时间 17:14:07–17:14:20 运行。使用与对照相同的样本、依赖、评分脚本和 Qwen 配置，在隔离应用副本及内存 checkpoint 中执行。Qwen3-8B 的唯一一次 INTENT 请求返回 HTTP 200，无超时或重试。两份本轮日志均已与本地配置中的非空凭据核对。[重试记录](order-id-final-prompt-selected-retry.jsonl)

```powershell
$caseLogs = Join-Path ([IO.Path]::GetTempPath()) ('order-id-missing-json-f145-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-missing-json-f145/prompt.txt --sample-id F145 --output-dir $caseLogs
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-missing-json-f145/prompt.txt --sample-id F145 --output-dir $caseLogs --score
```
