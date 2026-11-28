# 固定长度提示词 F145 单例实测

2026-11-27，在最终合并提示词的格式规则后增加总长度恰好为 9 个字符的要求。原文候选按完整写法核对，长度不足或超过 9 个字符均无效。保留原有 `DEMO-0042` 示例。[完整提示词](prompt.txt)

只运行 F145。输入为「查询支付，我只有旧格式单号 DEMO-73125。」预期 `order_id_status` 为 `missing`，`order_id` 和 `order_id_evidence` 均为 `null`，不进入 `PLAN`。

**F145 未通过。** Qwen 仍将 `DEMO-73125` 标为 `extracted`，依据为「我只有旧格式单号 DEMO-73125」。该十字符编号进入 `PLAN`。模型的六个输出字段和实际进入 `PLAN` 的编号与上一轮 F145 一致。[本轮首轮记录](order-id-final-prompt-selected-live.jsonl) · [上一轮 F145](../order-id-final-prompt-service-restored/order-id-final-prompt-focused-live.jsonl#L11)

北京时间 11:51:31–11:51:52。复用隔离副本、内存 checkpoint 和既有 Qwen3-8B 配置。两个 HTTP 请求均返回 200，普通 `INTENT` 调用一次，无超时或重试，退款提交为零。两份日志已用本地配置中的非空凭据核对。[重试记录](order-id-final-prompt-selected-retry.jsonl)

```powershell
$fixedLengthLogs = Join-Path ([IO.Path]::GetTempPath()) ('order-id-fixed-length-f145-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-fixed-length-f145/prompt.txt --sample-id F145 --output-dir $fixedLengthLogs
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --prompt evidence/order-id-fixed-length-f145/prompt.txt --sample-id F145 --output-dir $fixedLengthLogs --score
```
