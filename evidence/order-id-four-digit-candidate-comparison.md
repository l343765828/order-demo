# 工单 02 四位订单号 A、B 提示词同批实测

2026-11-27，A、B 两版分别通过现有退款工作流调用真实 Qwen3-8B，完成同一批 200 条四位订单号样本。**两版均未达到同批回归门槛，工单 02 未通过验收。**

- **A 版**将历史编号误入 `PLAN` 从 9 条降至 0 条。F147 把残缺编号 `DEMO-073` 送入 `PLAN`。精确提取降至 114/118，否定与纠正降至 41/44。
- **B 版**将提取状态与 JSON 字段冲突从 18 条降至 5 条。精确提取和否定与纠正均持平。新增漏提 F047，F067 仍漏提，另有 8 条历史误用和 1 条截断编号进入 `PLAN`。

## 运行条件

两版分别从当前 Astra 源码复制到独立临时目录。通过退款工作流截取送入决定器的提示词，核对只有指定区域变化，并校验除意图模块外 Python、SQL 内容一致。A 替换末尾数据区，历史列表只含本轮之前的消息，本轮业务原文作为独立 JSON 字符串。B 在 Astra 数据区之前插入两个完整 JSON 示例，保留原来的序列化。两版的业务规则和三个检查步骤均逐字保留。

样本来自 [同批 200 条 TSV](../test/order_id_four_digit_live.tsv)，统一 LF 后 SHA-256 为 `f48c8b8d0bf275cc32cb6b15ee3190a48ae564655bfec25f6cbd60e4ac15eb52`。覆盖普通查询 40、否定与纠正 44、多编号 24、缺号或历史指代 24、编号边界 24、提示词注入 20、退款办理 24。该批曾用于定位错例，结果计为同批回归。

三版使用同一 Qwen 接入配置，模型 `Qwen/Qwen3-8B`，温度 0，关闭思考，最多生成 512 token，HTTP 超时 90 秒，SDK 自动重试为 0。运行环境为 Windows、Python 3.11.7、LangGraph 1.2.22、langchain-openai 1.8.0、OpenAI SDK 3.55.1、Pydantic 2.14.3、httpx 0.28.1。各轮配置和指纹见日志头。

| 轮次 | 并发 | 样本数 | HTTP 请求数 | 北京时间 |
| --- | ---: | ---: | ---: | --- |
| Astra 首轮 | 8 | 200 | 254（199 次 HTTP 200、55 次超时） | 00:00:02–00:43:06 |
| Astra 重试 | 2 | 55 | 78（均为 HTTP 200） | 00:43:09–00:56:27 |
| A 首轮 | 4 | 200 | 274（均为 HTTP 200） | 01:46:32–02:35:04 |
| A 重试 | 2 | 0 | 0 | 02:35:07 |
| B 首轮 | 4 | 200 | 287（均为 HTTP 200） | 02:35:10–03:25:30 |
| B 重试 | 2 | 0 | 0 | 03:25:33 |

先完整运行 A，再运行 B。两版只对工作流明确记录 `OpenAITimeoutError` 的样本从新会话重试一次，首轮和重试分别写日志。无超时的重试日志仍有运行头和 0 条完成标记。复算 Astra 时，55 条均以恢复后的整条记录替换首轮记录。Astra 首轮并发为 8，A、B 首轮并发为 4。运行时间也分属不同服务负载，超时和延迟差异不能归因于提示词。

| 源码版本 | 源码 SHA-256（Python、SQL 相对路径和 LF 内容） |
| --- | --- |
| 应用 Astra | `42fb105d5718b85718b3049617b47bebeba6307d5cbe871872e8b3f602fdf116` |
| A | `d1d2a3f66e74216cb43243f35ffe7ea3b49d2d33f44cb91cc8ce781f55d82388` |
| B | `04f7f96e7e97243c50044b9562699e2e08f8ccc0f772f83582ab127ad3b881f8` |

## 三版复算

精确提取和否定与纠正比较实际进入 `PLAN` 的编号，经大写规范化后与标注对照。误选指采用本轮原文中的其他编号或完整的旧格式编号，截断指采用编号的一部分或已标注的残片，编造包括使用仅见于历史的编号。漏提指应有明确编号但没有编号进入 `PLAN`。字段冲突按普通 `INTENT` 的原始 JSON 判断，`extracted` 要求非空编号和依据，`missing`、`ambiguous` 要求两者均为 null。

| 指标 | Astra 检查步骤版 | A 历史与本轮分区 | B 完整 JSON 示例 |
| --- | ---: | ---: | ---: |
| 首轮精确提取 | 84/118（71.19%） | 114/118（96.61%） | 116/118（98.31%） |
| 补齐后精确提取，门槛 ≥116/118 | 116/118（98.31%） | **114/118（96.61%）** | 116/118（98.31%） |
| 首轮否定与纠正 | 33/44（75.00%） | 41/44（93.18%） | 42/44（95.45%） |
| 补齐后否定与纠正，门槛 ≥95% | 42/44（95.45%） | **41/44（93.18%）** | 42/44（95.45%） |
| 编造或截断进入 `PLAN`，门槛 0 | **9** | **1** | **9** |
| 其中编造 / 历史编号误用 | 9 / 9 | 0 / 0 | 8 / 8 |
| 其中截断 | 0 | 1 | 1 |
| 误选进入 `PLAN` | 12 | 19 | 15 |
| 漏提 / 相对 Astra 新增 | 1 / — | 0 / 0 | 2 / 1 |
| 原始提取状态与 JSON 字段冲突 | 18/197 | 26/197 | 5/197 |
| 缺号样本未进入 `PLAN` | 39/54 | 44/54 | 37/54 |
| 歧义样本未进入 `PLAN` | 23/28 | 22/28 | 21/28 |
| 歧义错误建立退款办理授权，门槛 0 | 0/28 | 0/28 | 0/28 |
| 办理授权正例正确 | 4/4 | 4/4 | 2/4 |
| 普通 `INTENT` 恰好一次调用，门槛 197/197 | 197/197 | 197/197 | 197/197 |
| 首轮超时 / 重试 / 重试后仍超时 | 55 / 55 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| 首轮工作流 P95 / P99（秒） | 163.389 / 168.599 | 90.846 / 95.416 | 90.052 / 95.038 |
| 首轮 `INTENT` HTTP P95 / P99（秒） | 89.366 / 89.949 | 53.679 / 64.431 | 53.879 / 62.316 |
| 同批回归门槛 | 未通过 | 未通过 | 未通过 |

普通 `INTENT` 计数按每条样本选定的一次执行统计，三条快速路径不计入分母。工作流延迟从 `run_refund_turn` 进入到返回，包含请求和本地工作流。HTTP 延迟从出站到读完响应体，按有完整响应计，Astra 首轮为 142 次，A、B 各 197 次。P95、P99 用最近秩。Astra 重试 55 条的工作流 P95/P99 为 42.321/42.778 秒，`INTENT` HTTP 为 25.433/26.182 秒。

## 实际进入 PLAN 的错误编号

| 错误类型 | Astra | A | B |
| --- | --- | --- | --- |
| 历史编号误用（均为 `DEMO-9000`） | F121、F122、F123、F124、F125、F126、F161、F171、F196 | 0 | F121、F122、F123、F124、F125、F126、F171、F196 |
| 其他编造 | 0 | 0 | 0 |
| 截断 | 0 | F147 `DEMO-073` | F147 `DEMO-073` |
| 误选本轮编号 | F063 `DEMO-7145`、F090 `DEMO-7212`、F098 `DEMO-7228`、F103 `DEMO-7238`、F145 `DEMO-73125`、F163 `DEMO-8407`、F177 `DEMO-7501`、F183 `DEMO-7513`、F185 `DEMO-7517`、F186 `DEMO-7518`、F189 `DEMO-7521`、F190 `DEMO-7522` | F063 `DEMO-7145`、F067 `DEMO-7153`、F080 `DEMO-7182`、F090 `DEMO-7212`、F091 `DEMO-7214`、F098 `DEMO-7228`、F101 `DEMO-7234`、F107 `DEMO-7244`、F145 `DEMO-73125`、F158 `DEMO-8402`、F163 `DEMO-8407`、F167 `DEMO-8411`、F177 `DEMO-7501`、F183 `DEMO-7513`、F185 `DEMO-7517`、F186 `DEMO-7518`、F187 `DEMO-7519`、F189 `DEMO-7521`、F190 `DEMO-7522` | F090 `DEMO-7212`、F098 `DEMO-7228`、F145 `DEMO-73125`、F158 `DEMO-8402`、F163 `DEMO-8407`、F165 `DEMO-8409`、F177 `DEMO-7501`、F178 `DEMO-7503`、F179 `DEMO-7505`、F180 `DEMO-7507`、F183 `DEMO-7513`、F185 `DEMO-7517`、F186 `DEMO-7518`、F189 `DEMO-7521`、F190 `DEMO-7522` |

A 的四条精确提取失败均为误选（F063、F067、F080、F107）。前三条属于否定与纠正。F145 的五位编号在三版均被完整采用，作为误选单列。

B 的剩余字段冲突为 F094、F097、F102、F144、F146。新增漏提 F047 需要查第一笔 `DEMO-7113`，模型输出 `missing`，工作流要求补号。F067 应改查 `DEMO-7154`，Astra 与 B 均漏提。B 修复了 Astra 的 F063 误选，因此总精确提取数持平。F199、F200 在 B 版均提取到正确编号，但两条明示办理被分类为资格咨询，未建立办理授权。

## 结论与解除条件

A 的分区达到了历史编号不进入 `PLAN` 的专项观察目标，同时放大了选单误差。B 的完整示例降低了字段冲突，同时新增漏提并留下历史误用及截断。两版均未达到同批晋级门槛。

解除条件是在同批达到至少 116/118、否定与纠正至少 95%、编造或截断进入 `PLAN` 为零、歧义错误办理授权为零、普通 `INTENT` 恰好一次，随后用至少 200 条未参与调词的独立样本与工单 01 固定基线实测达到工单标准。独立实模对照本轮未触发。若需调整已定稿的业务行为、数据职责或恢复规则，实施前取得用户确认。

本次使用虚构订单、隔离订单及政策回执、独立内存 checkpoint，实测未连接 MySQL、MCP 或 Milvus。真实订单存在性、归属数据库、政策检索及退款提交均未实测，退款提交次数 0。新增四份逐条日志已用运行配置的非空凭据值核对，未检出匹配。

## 逐条记录与复测

| 版本 | 首轮 | 重试 |
| --- | --- | --- |
| Astra | [200 条首轮](order-id-four-digit-checklist-live.jsonl) | [55 条重试](order-id-four-digit-checklist-retry.jsonl) |
| A | [200 条首轮](order-id-four-digit-candidate-a-live.jsonl) | [0 条重试](order-id-four-digit-candidate-a-retry.jsonl) |
| B | [200 条首轮](order-id-four-digit-candidate-b-live.jsonl) | [0 条重试](order-id-four-digit-candidate-b-retry.jsonl) |

原分段初版和分段优化版记录继续见[前次复测报告](order-id-four-digit-prompt-comparison.md)。从仓库根目录复算三版：

```powershell
.\.venv\Scripts\python.exe -X utf8 test/score_order_id_prompt_candidates.py
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-four-digit-checklist-live.jsonl --samples test/order_id_four_digit_live.tsv --retry evidence/order-id-four-digit-checklist-retry.jsonl
```

复跑实模时使用新目录存放结果：

```powershell
$candidateLogDir = Join-Path ([IO.Path]::GetTempPath()) ('order-id-prompt-candidates-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $candidateLogDir | Out-Null
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_prompt_candidates.py --output-dir $candidateLogDir
.\.venv\Scripts\python.exe -X utf8 test/score_order_id_prompt_candidates.py --candidate-dir $candidateLogDir
```

定向回归 57 项通过，全量回归 244 项通过，三个验证脚本的类型和语法检查通过。离线核对也覆盖 A、B 的提示词序列化、无超时重试和只重试超时样本。

```powershell
.\.venv\Scripts\python.exe -m pytest -q test/test_order_id_extraction.py test/test_qwen_client.py
.\.venv\Scripts\python.exe -m pytest -q test
.\.venv\Scripts\python.exe -m mypy --follow-imports=silent --check-untyped-defs test/verify_order_id_extraction_live.py test/verify_order_id_prompt_candidates.py test/score_order_id_prompt_candidates.py
.\.venv\Scripts\python.exe -m py_compile test/verify_order_id_extraction_live.py test/verify_order_id_prompt_candidates.py test/score_order_id_prompt_candidates.py
```
