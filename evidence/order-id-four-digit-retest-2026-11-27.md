# 工单 02 四位订单号同批复测

日期：2026-11-27

**结论：200 条同批回归全部跑完，工单 02 仍未通过。** 精确提取和否定纠正均达标，仍有 9 条历史编号进入 `PLAN`。本轮未启动新独立样本与工单 01 基线的正式对照。

## 复测版本与口径

从当前工作区的现有退款工作流入口调用真实 `Qwen/Qwen3-8B`。工作区基于 `c4e213e130b868500283a1e8a87eba2710eadb96`，应用源码 SHA-256 为 `42fb105d5718b85718b3049617b47bebeba6307d5cbe871872e8b3f602fdf116`，与先前 Astra 检查步骤版的指纹一致。当前业务代码仍在使用检查步骤版。第 03 轮候选留在定向试验材料中，单独报告了 11 条定向测试。

[Astra 同批记录](order-id-four-digit-prompt-comparison.md) · [候选定向试验](order-id-history-source-f121/report.md)

本轮复用前次回归的 200 条四位编号样本，样本 SHA-256（规范为 LF）为 `f48c8b8d0bf275cc32cb6b15ee3190a48ae564655bfec25f6cbd60e4ac15eb52`。其中 118 条有唯一目标，44 条属否定与纠正，28 条属歧义，197 条为普通 `INTENT` 路径。编号准确率按实际进入 `PLAN` 的大写编号计算，工作流延迟从 `run_refund_turn` 调用到返回，以最近秩计算 P95/P99。首轮并发 4，使用相同的模型超时和生成配置。该批样本已用于定位错例，仅用于同批回归。

每条样本使用独立内存 checkpoint 和虚构消费者。订单和政策检索通过隔离回执完成，未连接 MySQL、MCP、Milvus、`bge-m3` 或 `reranker`，未执行退款提交。

## 验收结果

| 指标 | 本轮实测 | 判定 |
| --- | ---: | --- |
| 精确提取率 | 116/118（98.31%） | 达到 98% |
| 否定与纠正准确率 | 42/44（95.45%） | 达到 95% |
| 编造或截断编号进入 `PLAN` | 9 条历史编号，0 条截断 | 超过零错误门槛 |
| 歧义场景错误建立退款办理授权 | 0/28 | 达标 |
| 普通 `INTENT` 恰好调用模型一次 | 197/197 | 达标 |

- F121–F126、F161、F171、F196 均将历史里的 `DEMO-9000` 带入 `PLAN` 和隔离查单工具。F145 将五位旧格式 `DEMO-73125` 原样送入 `PLAN`，按既有口径计入误选。F147 的原始模型输出含截断编号 `DEMO-073`，在 `INTENT` 校验时被拒绝。
- 共 12 条误选、1 条漏提。F063 误选了排除编号，F067 没有编号进入 `PLAN`。54 条缺号样本中 39 条未进入 `PLAN`，32 条收到补号提示。28 条歧义样本中 23 条未进入 `PLAN`。4 条明确办理正例均建立了正确授权。
- 原始 `INTENT` 编号准确率为 113/115，提取状态准确率为 162/197。277 次 HTTP 请求全部返回 200，无超时或重试。其中 `INTENT` 197 次、`PLAN` 80 次。应用收到响应后有 18 次 `INTENT` 契约校验失败并降级。
- 工作流 P95/P99 为 89.086/99.011 秒，`INTENT` HTTP P95/P99 为 58.027/62.733 秒。前次 Astra 同批回归首轮并发 8，另有超时重试，延迟口径中的负载与本轮不同。
- 全量离线回归 244 项通过，验收脚本的类型和语法检查通过。配置凭据值比对未在新增日志中发现匹配。

本轮于北京时间 19:18 至 20:05 运行。模型温度为 0，关闭思考，最多生成 512 token，HTTP 超时 90 秒，SDK 重试 0 次。环境为 Windows、Python 3.11.7，依赖版本记录在日志首行。采集脚本 SHA-256 为 `874886c5999fd2bba6f49257a357c08175de55f4e0282955987fcfe1168e2423`。逐条日志 SHA-256（规范为 LF）为 `868a151c2779bc353b73b32961596216ab1dfa60a77e425651ce33bc1d5a4151`。[逐条原始记录](order-id-four-digit-retest-2026-11-27.jsonl)

## 复测命令

```powershell
$revision = git rev-parse HEAD
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py run --source . --version current --revision "$revision+working-copy" --workers 4 --samples test/order_id_four_digit_live.tsv --output evidence/order-id-four-digit-retest-2026-11-27.jsonl
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-four-digit-retest-2026-11-27.jsonl --samples test/order_id_four_digit_live.tsv
.\.venv\Scripts\python.exe -m pytest -q test
.\.venv\Scripts\python.exe -m mypy --follow-imports=silent --check-untyped-defs test/verify_order_id_extraction_live.py
.\.venv\Scripts\python.exe -m py_compile test/verify_order_id_extraction_live.py
```
