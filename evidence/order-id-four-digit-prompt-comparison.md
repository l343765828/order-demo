# 四位订单号提示词同批复测

应用已接入 Astra 检查步骤版。200 条同批回归补齐超时后，精确提取率为 98.31%，否定与纠正准确率为 95.45%。仍有 9 条历史编号进入 `PLAN`，工单 02 的零错误门槛未达标。

四位格式版和 Astra 版均跑完 `test/order_id_four_digit_live.tsv` 中的 200 条样本。分段初版和分段优化版只跑完该批样本的前缀。该批样本曾用于定位错例，本次成绩作为同批回归证据。测试经退款工作流运行，订单与政策使用隔离回执，退款授权进入内存工作流状态，退款提交未执行。评分依据从 `PLAN` 入口记录的实际编号。

## 提示词版本

- 四位格式版提供固定格式和小写编号示例。已有实模记录作为同批基准。[四位格式版记录](order-id-four-digit-live.jsonl)
- 检查步骤版由 Astra 撰写，在对话数据之前加入先核对本轮编号、再判断歧义、最后判断咨询和办理的三步规则。应用传入的文字与小样本诊断日志的 `run.addition` 逐字相同。[小样本诊断](order-id-four-digit-prompt-diagnosis.jsonl)
- 分段初版用标题组织任务、选单与输出、业务意图、资格咨询补号和示例。历史对话以 JSON 列表传入，本轮原文作为独立 JSON 字符串放在最后。编号与原文依据明确取自本轮。对照例覆盖带括号的合法编号、误报纠正、多单歧义、历史指代、超长编号和补号。分段与示例的写法参考公开提示词汇编，历史和本轮数据的独立区块借鉴 Qwen Code 的提示词分层方式。[系统提示词汇编](https://github.com/x1xhlol/system-prompts-and-models-of-ai-tools/tree/main/Open%20Source%20prompts) · [分段与示例说明](https://github.com/dontriskit/awesome-ai-system-prompts#2-structured-instructions-and-organization) · [Qwen Code 提示词源码](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/core/prompts.ts)
- 分段优化版在初版基础上为三个提取状态明确给出 `order_id_status`、`order_id` 和 `order_id_evidence` 的字段组合，并给出完整 JSON 示例，针对初版出现的字段冲突。

## 评分方式

- 精确提取率对 118 条有唯一正确编号的样本计算。否定与纠正准确率对 44 条相应样本计算。比较 `PLAN` 实际输入的大写规范值。
- 工单 02 的门槛为精确提取率至少 98%，否定与纠正至少 95%，编造或截断编号进入 `PLAN` 及歧义错误授权均为零，普通识别恰好调用一次模型。
- 只重试工作流事件明确记录为 `OpenAITimeoutError` 的样本。每条重试样本从独立会话重新执行，重试未再超时时用整条结果替换原样本以计算补齐口径。首轮与重试结果分别保留。
- 各轮新版复测顺序执行。检查步骤版首轮并发 8，分段初版和分段优化版首轮并发 4，重试并发 2。延迟供各轮运行参考。并发和服务负载均会影响超时率和延迟。

## 分段初版试跑

前 63 条都需要唯一编号，其中 `PLAN` 输入正确 47 条，模型响应 93 次 HTTP 200、4 次 HTTP 422。多个输出包含正确编号和依据，状态却为 `missing`，应用按既有契约拒绝。已有 16 条编号未正确进入 `PLAN`，即使剩余全部正确，也达不到 98%。因此停止该版，保留 63 条原始记录，用分段优化版验证明确字段组合是否能减少这类错误。[分段初版试跑记录](order-id-four-digit-structured-live.jsonl)

## Astra 检查步骤版

| 指标 | 四位格式版 | Astra 首轮 | Astra 补齐超时后 |
| --- | ---: | ---: | ---: |
| `PLAN` 编号精确提取率，门槛 ≥98% | 112/118（94.92%） | 84/118（71.19%） | 116/118（98.31%） |
| 否定与纠正准确率，门槛 ≥95% | 41/44（93.18%） | 33/44（75.00%） | 42/44（95.45%） |
| 编造或截断编号进入 `PLAN`，门槛 0 | 9 | 3 | 9 |
| 其中误用历史编号 | 6 | 3 | 9 |
| 歧义场景错误授权，门槛 0 | 0/28 | 0/28 | 0/28 |
| 普通 `INTENT` 一次调用，按每条所选执行记录计 | 197/197 | 197/197 | 197/197 |
| 超时样本 | 2 | 55 | 0 |

首轮 55 条在 `INTENT` 超时，以并发 2 重跑后，78 次请求均返回 HTTP 200。原始首轮 254 次请求和重试 78 次请求均保存在日志中。补齐口径为每条样本保留一条执行记录。应用工作流首轮 P95/P99 为 163.389/168.599 秒。

补齐后，9 条计入零错误门槛的错误全部为历史编号误用，截断编号为 0。另有 1 条五位数字编号 `DEMO-73125` 被整段采用，计为误选。对同批有效编号的 4 条净改善来自修复 5 条、回退 1 条。回退样本 F067 的正确编号未进入 `PLAN`，F063 仍选中了消费者排除的编号。

应用中实际传入的检查步骤逐字复用了诊断版。运行使用 Qwen3-8B，源码 SHA-256 为 `42fb105d5718b85718b3049617b47bebeba6307d5cbe871872e8b3f602fdf116`，采集脚本 SHA-256 为 `c474d852502b37695ca3096ec7b87150137810cc79b8329eb86205bc73a9df9c`。首轮为北京时间 2026-11-27 00:00 至 00:43，重试于同一日 00:56 完成。详细请求配置与各轮时间见日志首尾。[首轮记录](order-id-four-digit-checklist-live.jsonl) · [重试记录](order-id-four-digit-checklist-retry.jsonl)

## 分段优化版试跑

按用户要求停止时已完成前 41 条，40 条将正确编号送入 `PLAN`，69 次模型请求均返回 HTTP 200。F005 仍误将资格咨询识别为退款办理，导致正确编号未进入 `PLAN`。F032 虽提取到正确编号，仍误接续旧的资格问题。这批样本来自语料前缀，主要是常规样本，未覆盖历史编号及残缺编号等后段用例。[分段优化版试跑记录](order-id-four-digit-structured-fields-live.jsonl)

## 复算与本地验证

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-four-digit-live.jsonl --samples test/order_id_four_digit_live.tsv
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-four-digit-checklist-live.jsonl --samples test/order_id_four_digit_live.tsv --retry evidence/order-id-four-digit-checklist-retry.jsonl
```

`score --retry` 校验首轮和重试的样本指纹、源码、Qwen 配置及采集脚本。输出分别给出首轮和补齐指标。全量离线测试 244 项通过，应用意图模块与评分脚本类型检查通过。退款执行节点未触发，新增日志未检出配置凭据。
