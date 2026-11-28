# 订单号来源说明提示词定向实测

2026-11-27，使用真实 Qwen3-8B 对原 Astra 和来源说明候选分别执行同一组 16 条。**候选未达到进入 200 条回归的条件。** F067 修复，9 条历史编号和 1 条五位旧编号仍进入 `PLAN`。

## 候选与运行条件

在 Astra 第一步识别完整编号的句子之后插入已确认的来源说明。说明对话数组最后一项的 `content` 是本轮业务原文，历史用于判断是否在补号，`order_id` 与 `order_id_evidence` 均从最后一项原样提取。验收脚本从应用源码复制出两个隔离版本，验证 Astra 源码指纹后只在候选副本执行这一处插入。三个检查步骤按原顺序执行，使用相同的对话 JSON 和输出字段。[复测脚本](../test/verify_order_id_source_prompt.py)

16 条来自已用于诊断的 200 条样本，包含 9 条历史来源错误、5 条选单或格式边界、2 条明确退款办理。两版顺序回放，首轮并发 4。单次超时 90 秒，SDK 自动重试为 0，工作流记录为超时的样本以并发 2 重试一次。同用 `Qwen/Qwen3-8B`，温度 0，关闭思考，最多生成 512 token。两轮所有模型请求均返回 HTTP 200。

| 版本 | 首轮时间（北京时间） | 工作流样本 | HTTP 请求 | 首轮超时 / 重试 |
| --- | --- | ---: | ---: | ---: |
| Astra | 10:02:47–10:06:48 | 16 | 24 | 0 / 0 |
| 来源说明候选 | 10:06:54–10:11:12 | 16 | 25 | 0 / 0 |

两版使用独立内存 checkpoint、虚构订单及隔离回执，采集真实模型响应和实际进入 `PLAN` 的编号。MySQL、MCP、Milvus 未接入，退款提交为零。四份日志均已用运行配置中的非空凭据检查。

## 定向对照

| 观察项 | Astra | 来源说明候选 |
| --- | ---: | ---: |
| 历史编号误入 `PLAN` | 9/9 | 9/9 |
| 五位旧编号误入 `PLAN` | 1/1 | 1/1 |
| 截断编号误入 `PLAN` | 0/1 | 0/1 |
| 有预期编号的样本精确提取 | 3/5 | 4/5 |
| 明确退款办理授权正确 | 2/2 | 2/2 |
| 普通 `INTENT` 恰好调用一次 | 16/16 | 16/16 |

| 样本 | 预期进入 `PLAN` 的编号 | Astra | 来源说明候选 |
| --- | --- | --- | --- |
| F121、F122、F123、F124、F125、F126、F161、F171、F196 | 空 | 均为 `DEMO-9000` | 均为 `DEMO-9000` |
| F047 | `DEMO-7113` | `DEMO-7113` | `DEMO-7113` |
| F063 | `DEMO-7146` | `DEMO-7145` | `DEMO-7145` |
| F067 | `DEMO-7154` | 空 | `DEMO-7154` |
| F145 | 空 | `DEMO-73125` | `DEMO-73125` |
| F147 | 空 | 空 | 空 |
| F199 | `DEMO-7529` | `DEMO-7529`，授权匹配 | `DEMO-7529`，授权匹配 |
| F200 | `DEMO-7530` | `DEMO-7530`，授权匹配 | `DEMO-7530`，授权匹配 |

F147 在两版的原始 JSON 中均为 `missing` 且携带截断编号 `DEMO-073`，应用输出校验产生 `ValidationError`，该编号未进入 `PLAN`。9 条历史误用均被模型标为 `extracted`。F063 在两版都选中了本轮明确排除的 `DEMO-7145`。

历史误用归零和格式边界通过这两项均未满足。200 条回归及独立样本验收未触发。此次定向实测未覆盖完整歧义样本集。

## 逐条记录与复测

- Astra [首轮 16 条](order-id-source-prompt-focused-astra-live.jsonl) · [重试 0 条](order-id-source-prompt-focused-astra-retry.jsonl)
- 候选 [首轮 16 条](order-id-source-prompt-focused-source-live.jsonl) · [重试 0 条](order-id-source-prompt-focused-source-retry.jsonl)

应用 Astra 的源码指纹为 `42fb105d5718b85718b3049617b47bebeba6307d5cbe871872e8b3f602fdf116`，候选为 `1ec58f7c0bdc8488d385eab8e70f8e15dbca3656604e97a2753a8cf8141c6eaf`，定向选集指纹为 `e1a903febc0ac2654fe43ea53961aad17ed09c929e855fe2eb9c614c86e88d1a`。完整元数据见日志头。

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_source_prompt.py --output-dir evidence --score
$sourcePromptLogs = Join-Path ([IO.Path]::GetTempPath()) ('order-id-source-trial-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $sourcePromptLogs | Out-Null
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_source_prompt.py --output-dir $sourcePromptLogs
```

定向离线测试 57 项和全量离线测试 244 项通过。两个验证脚本的类型检查和语法检查通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q test/test_order_id_extraction.py test/test_qwen_client.py
.\.venv\Scripts\python.exe -m pytest -q test
.\.venv\Scripts\python.exe -m mypy --follow-imports=silent --check-untyped-defs test/verify_order_id_extraction_live.py test/verify_order_id_source_prompt.py
.\.venv\Scripts\python.exe -m py_compile test/verify_order_id_extraction_live.py test/verify_order_id_source_prompt.py
```
