# 最终合并提示词定向复测

2026-11-27，使用最终合并提示词向配置中的 Qwen3-8B 服务发送 16 条定向样本。**16 条请求均返回 HTTP 404，收到的模型输出为 0 条，提示词效果待验证。** 同一入口上的原 Astra 单条对照也返回 HTTP 404。工单 02 仍未通过验收。

## 运行条件与结果

定向样本与前一轮一致，包含 9 条历史来源、3 条选单纠正、2 条编号边界、2 条明确退款办理。脚本验证 Astra 源码指纹后，在隔离副本中替换提示词，将原有补号状态和对话 JSON 填入两个占位符。逐字核对了两种补号状态下实际渲染的提示词。[本轮提示词](order-id-final-prompt.txt) · [上一轮定向实测](order-id-source-prompt-focused.md)

模型配置为 `Qwen/Qwen3-8B`、温度 0、关闭思考、生成上限 512 token、单次超时 90 秒。首轮并发 4，SDK 自动重试为 0。只有明确超时的样本才以并发 2 重试一次。原 Astra 对照选用 F171，并发 1。

| 运行 | 完成记录 | HTTP 404 | 模型输出 | 超时 | 重试 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 最终提示词定向复测 | 16 | 16 | 0 | 0 | 0 |
| 原 Astra 对照 F171 | 1 | 1 | 0 | 0 | — |

首轮为北京时间 11:25:58–11:25:59，原 Astra 对照为 11:27:07。所有 404 都由工作流记录为 `OpenAIModelNotFoundError`。对配置中的网关复查时，`/qwen3/health`、`/qwen3/v1/models` 与最小聊天请求均返回 404。Ray Serve 的 `/-/routes` 返回 HTTP 200 和空路由表，当前没有注册 `/qwen3` 路由。

逐条记录中的缺号、未进入 `PLAN` 等结果来自请求失败后的降级。精确提取、历史借号、格式边界和退款办理授权的提示词效果均待验证。服务恢复后，先完成 16 条定向复测，再按门槛决定是否启动同批 200 条及独立验收。

本轮使用内存 checkpoint 和隔离业务数据，退款提交次数为零，MySQL、MCP 和 Milvus 均未接入。源文件指纹、样本指纹及配置记录在日志头。三份日志已与本地配置中的非空凭据比对。

## 逐条记录与复跑

- [最终提示词首轮 16 条](order-id-final-prompt-focused-live.jsonl)
- [重试记录 0 条](order-id-final-prompt-focused-retry.jsonl)
- [原 Astra 对照 F171](order-id-final-prompt-astra-control.jsonl)

从仓库根目录执行。使用新目录保存服务恢复后的复测记录。

```powershell
$finalPromptLogs = Join-Path ([IO.Path]::GetTempPath()) ('order-id-final-prompt-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --output-dir $finalPromptLogs
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --output-dir $finalPromptLogs --score
```

全量离线测试 244 项通过，复测脚本类型检查和语法检查通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q test
.\.venv\Scripts\python.exe -m mypy --follow-imports=silent --check-untyped-defs test/verify_order_id_final_prompt.py
.\.venv\Scripts\python.exe -m py_compile test/verify_order_id_final_prompt.py
```
