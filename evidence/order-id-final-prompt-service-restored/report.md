# Qwen 服务恢复后的最终提示词实测

2026-11-27，Qwen3-8B 服务恢复后，使用最终合并提示词完成同一组 16 条定向样本。**F171 由误用历史编号变为缺号，但仍有 8 条历史编号和 1 条五位旧编号进入 `PLAN`，未达到启动 200 条回归的门槛。**

## 定向结果

| 指标 | 原 Astra | 来源说明候选 | 最终合并提示词 |
| --- | ---: | ---: | ---: |
| 历史编号误入 `PLAN` | 9/9 | 9/9 | 8/9 |
| 五位旧编号误入 `PLAN` | 1/1 | 1/1 | 1/1 |
| 截断编号误入 `PLAN` | 0/1 | 0/1 | 0/1 |
| 有预期编号的样本精确提取 | 3/5 | 4/5 | 4/5 |
| 否定与纠正样本准确 | 1/3 | 2/3 | 2/3 |
| 明确退款办理授权正确 | 2/2 | 2/2 | 2/2 |
| 普通 `INTENT` 恰好调用一次 | 16/16 | 16/16 | 16/16 |
| 首轮超时 / 重试 | 0 / 0 | 0 / 0 | 0 / 0 |

前两列复用此前相同样本、Qwen 配置及首轮并发的日志。三版运行时间不同。[原 Astra 与来源说明候选的对照报告](../order-id-source-prompt-focused.md)

| 样本 | 预期进入 `PLAN` 的编号 | 最终合并提示词 |
| --- | --- | --- |
| F121、F122、F123、F124、F125、F126、F161、F196 | 空 | 均为 `DEMO-9000`，原文依据也是历史消息 |
| F171 | 空 | `missing`，编号及依据均为 `null` |
| F145 | 空 | `DEMO-73125` |
| F147 | 空 | 模型输出 `missing` 却仍携带 `DEMO-073`，输出契约校验报 `ValidationError`，未进入 `PLAN` |
| F047 | `DEMO-7113` | `DEMO-7113` |
| F063 | `DEMO-7146` | `DEMO-7145`，这是消费者明确排除的编号 |
| F067 | `DEMO-7154` | `DEMO-7154` |
| F199、F200 | 分别为 `DEMO-7529`、`DEMO-7530` | 编号和授权均匹配 |

F171 是本轮相对上一候选唯一改变了实际选单结果的样本。F067 延续了上一候选相对原 Astra 的修复。定向样本未覆盖完整歧义集。200 条同批回归与独立验收均未启动，工单 02 仍未通过。

## 运行条件与复算

启动前，Ray Serve 已注册 `/qwen3` 路由，`/qwen3/health` 返回 HTTP 200。北京时间 11:39:20–11:43:31，用同一份最终提示词在隔离副本中运行。首轮并发 4，模型 `Qwen/Qwen3-8B`，温度 0，关闭思考，生成上限 512 token，超时 90 秒，SDK 自动重试为 0。规则只对超时样本以并发 2 重试。本轮 25 个 HTTP 请求均返回 200，16 个 `INTENT` 原始输出均可解析为 JSON。16 条样本的工作流 P95/P99 均为 80.350 秒，`INTENT` HTTP P95/P99 均为 51.507 秒，使用最近秩。

候选源码指纹 `c88d923176fa9e1252eea0b6a8e07979cf69f76248211c18a336cef1a81dee32` 与此前服务中断时的尝试相同。使用内存 checkpoint 与隔离业务数据，退款提交为零，MySQL、MCP、Milvus 未接入。两份新日志均已比对本地配置中的非空凭据，未检出匹配。[实测提示词](../order-id-final-prompt.txt) · [此前的 404 记录](../order-id-final-prompt-focused.md)

- [最终版首轮 16 条](order-id-final-prompt-focused-live.jsonl)
- [重试记录 0 条](order-id-final-prompt-focused-retry.jsonl)

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --output-dir evidence/order-id-final-prompt-service-restored --score
$finalPromptLogs = Join-Path ([IO.Path]::GetTempPath()) ('order-id-final-prompt-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_final_prompt.py --output-dir $finalPromptLogs
```
