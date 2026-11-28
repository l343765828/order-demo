# 02 完成订单号提取的实模对照验收

Status: ready-for-agent

Blocked by: 01 改造订单号提取并通过离线回归

## 目标

用实际 Qwen3-8B 服务，在同一批独立样本上比较改造前后的订单号提取和退款办理授权行为，交付可复测的结果和验收结论。参见[改造规格](../spec.md)。

## 验收标准

- [x] 验收集至少包含 200 条独立样本，与提示词示例和调优语料分开。样本标注预期订单号、提取状态及适用的授权结果，覆盖普通查询、否定纠正、多个编号和歧义、缺号和历史指代、编号边界异常、提示词注入及退款授权反例。
- [x] 用 01 固定的改造前基线和改造后版本进行同题对照。两版记录所用代码版本、Qwen 配置、测试环境、延迟统计口径和各类别样本数。分别采集模型原始输出、实际进入 `PLAN` 的编号及授权结果。
- [ ] 精确提取率按有明确预期编号的样本计算，比较规范化为大写后的编号。改造后精确提取率至少 98%，否定与纠正场景准确率至少 95%。缺号、歧义处理、误选和漏提取单独统计。
- [ ] 改造后实模验收中，编造或截断编号进入 `PLAN` 的数量为零，歧义场景错误建立退款办理授权的数量为零。只读路径的零错误由验收样本实测。普通路径 `INTENT` 的模型调用次数为一次。
- [x] 两版报告对比准确率、错误数、调用次数及 P95、P99 响应延迟，给出各门槛的达标情况。测试脚本和对照报告存放在仓库规定的测试与验收记录目录。

## Comments

### 2026-11-26 实施与验收结果

工单 01 的离线验收和固定基线已确认。用该基线和改造版从现有退款工作流入口调用真实 `Qwen/Qwen3-8B`，两版各完成同一批 200 条独立样本。验收已执行完毕，结果未通过。保留票面状态，两项未达标的验收项保持未勾选。

- 精确提取率由 62/122（50.82%）升至 115/122（94.26%），低于 98%。否定与纠正从 1/48 升至 46/48（95.83%），达到 95%。
- 改造版有 3 条本轮原文不存在的编号和 1 条截断编号进入 `PLAN`，未达到零错误门槛。其中包含两条历史编号误用。歧义场景的错误授权为 0/28，197 条普通 `INTENT` 路径均恰好调用一次。明确授权正例 3/4 正确，R24 被独立授权校验拦截。
- 基线全轮调用 241 次，改造版 291 次。工作流 P95/P99 从 112.097/119.277 秒变为 109.635/116.356 秒。离线定向回归 57 项、全量回归 244 项、脚本类型和语法检查均通过。

正式验收使用虚构订单、隔离回执和内存 checkpoint，未连接 MySQL、MCP 或 Milvus，未提交退款。已检查两份日志均无配置中的凭据。报告记录两版代码指纹、配置、延迟口径、完整复测命令和逐例观察。[验收报告](../../../evidence/order-id-extraction-02.md)

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py compare evidence/order-id-extraction-02-baseline.jsonl evidence/order-id-extraction-02-current.jsonl
.\.venv\Scripts\python.exe -m pytest -q test/test_order_id_extraction.py test/test_qwen_client.py
.\.venv\Scripts\python.exe -m pytest -q test
.\.venv\Scripts\python.exe -m mypy --follow-imports=silent --check-untyped-defs test/verify_order_id_extraction_live.py
.\.venv\Scripts\python.exe -m py_compile test/verify_order_id_extraction_live.py
```

解除条件为将精确提取率提升至至少 98%，且同一批实测样本中编造或截断编号进入 `PLAN` 的数量归零。涉及定稿业务行为、数据职责或恢复规则的调整，实施前须由用户确认。

### 2026-11-26 四位订单号提示词复测

已将统一编号格式和独立验收口径写入规格。使用新的 200 条独立样本和真实 Qwen3-8B，经现有退款工作流完成复测。精确提取为 112/118（94.92%），否定与纠正为 41/44（93.18%），两项低于门槛。7 条编造和 2 条截断编号进入 `PLAN`，其中 6 条复用了历史编号。歧义场景错误授权为 0/28，197 条普通 `INTENT` 均恰好调用一次。工单仍未通过，验收勾选保持原状。

原验收样本及两版日志保留，重算仍为 62/122 和 115/122。新旧结果采用各自样本及编号契约。本次全量离线回归 244 项通过。隔离回执和内存 checkpoint 保证本次验收无需连接 MySQL、MCP 或 Milvus，复测在退款提交前结束。报告提供命令、错例和逐条日志。[四位格式复测报告](../../../evidence/order-id-four-digit-live.md)

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-four-digit-live.jsonl --samples test/order_id_four_digit_live.tsv
```

根据已观测日志模拟本轮原文和格式校验，可挡住 11 条错误，但精确提取仍为 112/118。若实施程序校验，需先取得确认，并针对仍然存在的漏提和语义误选继续改进。

同一模型对 8 条错例和 2 条正确对照分别运行现行提示词及追加检查顺序的版本，结果由 2/10 变为 4/10。两条修正可归到资格咨询分类和去除括号。历史编号误用、多编号语义误选及旧格式编号输出仍然存在。此诊断未计入验收，未实测其他模型。[诊断记录](../../../evidence/order-id-four-digit-prompt-diagnosis.jsonl)

### 2026-11-27 Astra 检查步骤版同批回归

Astra 检查步骤版已接入应用。用 Qwen3-8B 回放同一批 200 条四位格式样本，首轮 55 条 `INTENT` 超时。以并发 2 重跑后全部收到响应。按每条样本选用一条执行记录，精确提取率 116/118（98.31%），否定与纠正 42/44（95.45%），歧义错误授权 0/28，普通 `INTENT` 恰好调用一次 197/197。仍有 9 条历史编号进入 `PLAN`，零错误门槛未达标。该批样本曾用于定位错例，本轮计作同批回归，工单仍未通过。

参考公开提示词仓库的分段初版试跑 63 条，其中正确编号 47 条。分段优化版在用户要求停止时已完成 41 条，其中正确编号 40 条。两个试跑的逐条记录均已保存，结论仅覆盖实际跑完的样本。报告附事件明细和复算命令。[提示词复测报告](../../../evidence/order-id-four-digit-prompt-comparison.md)

### 2026-11-27 提示词 A、B 同批实测

从应用的 Astra 检查步骤版分别派生 A 的历史与本轮分区、B 的完整 JSON 示例，在独立副本中依次通过现有工作流和真实 Qwen3-8B 各跑完 200 条。两版首轮并发 4，超时重试并发 2。本次两版均无超时，分别保留首轮和 0 条重试的记录。Astra 历史记录首轮并发 8，55 条超时以并发 2 重试恢复。

- A 版历史编号误入 `PLAN` 为 0，F147 的截断编号 `DEMO-073` 仍进入 `PLAN`。精确提取 114/118（96.61%），否定与纠正 41/44（93.18%），两项均低于门槛。
- B 版精确提取 116/118（98.31%），否定与纠正 42/44（95.45%），与 Astra 补齐后持平。提取状态与 JSON 字段冲突从 18 条降至 5 条，新增漏提 F047。仍有 8 条历史编号及 F147 截断编号进入 `PLAN`。
- 两版歧义错误授权均为 0/28，普通 `INTENT` 恰好调用一次均为 197/197。B 的明确办理正例有两条被分类成资格咨询，未建立办理授权。

两版均未通过同批回归门槛，未触发独立样本与工单 01 固定基线的正式实模验收。工单仍未通过，保留原验收勾选。解除条件为同批满足提取、纠正、零编造和截断、零歧义错误授权及普通路径一次调用的全部门槛，再完成独立实模对照。定向回归 57 项、全量回归 244 项和验证脚本的类型与语法检查均通过。隔离运行未提交退款，未连接 MySQL、MCP 或 Milvus。[三版对照、错例及逐条记录](../../../evidence/order-id-four-digit-candidate-comparison.md)

```powershell
.\.venv\Scripts\python.exe -X utf8 test/score_order_id_prompt_candidates.py
```

### 2026-11-27 Astra 订单号来源说明定向实测

仅在 Astra 第一步加入历史、本轮业务原文和提取字段的来源说明。在相同真实 Qwen3-8B 配置下，从隔离副本完成 16 条同题对照。候选修复 F067 的漏提。F047 和两条明确退款办理授权保持正确。F063 仍误选，9 条 `DEMO-9000` 和 F145 的 `DEMO-73125` 仍进入 `PLAN`。F147 在两版均被输出契约校验拦下。两版各 16 条均恰好调用一次 `INTENT`，无超时或重试，未提交退款。

候选未达到定向验证的历史误用归零和格式边界条件，未触发 200 条回归及独立样本验收。工单仍未通过。 [定向对照报告和逐条日志](../../../evidence/order-id-source-prompt-focused.md)

### 2026-11-27 最终合并提示词定向复测

在隔离副本中使用最终合并提示词发送同一组 16 条定向样本。16 次请求均返回 HTTP 404，无模型输出、超时或重试。原 Astra 的 F171 对照同样返回 404。Ray Serve 的路由表为空，`/qwen3/health` 返回 404。待 `/qwen3` 路由恢复后从定向复测开始。工单验收仍待实测。全量离线测试 244 项通过，退款提交为零。[复测报告和逐条日志](../../../evidence/order-id-final-prompt-focused.md)

### 2026-11-27 服务恢复后完成最终合并提示词定向实测

服务恢复后用相同提示词及配置从隔离副本完成 16 条定向样本。25 个 HTTP 请求均返回 200，16 条普通路径各调用一次 `INTENT`，无超时或重试，退款提交为零。F171 正确判为缺号。F121–F126、F161、F196 仍各有一条历史编号 `DEMO-9000` 进入 `PLAN`，F145 的五位旧编号仍进入 `PLAN`。精确提取 4/5，否定与纠正 2/3，明确办理授权 2/2。未达到历史误用归零与编号边界门槛，未启动 200 条同批回归和独立验收。工单仍未通过。[服务恢复后的复测报告和逐条日志](../../../evidence/order-id-final-prompt-service-restored/report.md)

### 2026-11-27 固定长度提示词的 F145 单例实测

在最终合并提示词中明确完整订单号总长度为 9 个字符，只运行 F145。Qwen3-8B 仍将十字符旧编号 `DEMO-73125` 标为 `extracted` 并送入 `PLAN`。两次 HTTP 请求均返回 200，无超时或重试，退款提交为零。F145 仍未通过。[完整提示词与单例报告](../../../evidence/order-id-fixed-length-f145/report.md)

### 2026-11-27 当前工作区 200 条同批复测

通过现有退款工作流和真实 Qwen3-8B 完整复测 200 条四位编号样本。应用源码指纹与 Astra 检查步骤版一致，最新版定向候选未出现在本轮运行的应用代码中。277 个模型请求均返回 HTTP 200，无超时或重试。

精确提取率 116/118（98.31%），否定与纠正 42/44（95.45%），歧义错误授权 0/28，普通 `INTENT` 一次调用 197/197。9 条历史编号进入 `PLAN` 和隔离查单，截断编号为零。F145 的五位旧格式仍进入 `PLAN`，另记为误选。工作流 P95/P99 为 89.086/99.011 秒。全量离线回归 244 项通过。

工单仍未通过，验收勾选保持原状。同批回归的历史编号误用归零后，才能进入新独立样本与工单 01 固定基线的正式对照。本次只连接 Qwen，政策检索和订单查询使用隔离回执，未连接 MySQL、MCP、Milvus、`bge-m3` 或 `reranker`，未执行退款提交。[复测报告与逐条原始记录](../../../evidence/order-id-four-digit-retest-2026-11-27.md)

```powershell
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-four-digit-retest-2026-11-27.jsonl --samples test/order_id_four_digit_live.tsv
.\.venv\Scripts\python.exe -m pytest -q test
```

### 2026-11-27 当前提示词追加缺号说明后的定向复测

在应用的 `INTENT` 提示词中加入「历史有编号、本轮只说同一单时，视为本轮缺订单号」，并指示模型返回 `missing / null / null`。用当前工作区、真实 Qwen3-8B 和与上一轮相同的模型配置，从现有工作流入口复测 F121–F126、F161、F171、F196。首轮并发 2，采用独立内存 checkpoint、虚构历史和隔离业务回执，只连接 Qwen。

9 条均返回 `extracted`，订单号均为历史里的 `DEMO-9000`，依据均为历史原句「请问 DEMO-9000 什么时候发货？」。9 条均进入 `PLAN` 并查询该历史编号，回复均为「未找到可操作的订单。」缺号识别为 0/9，与上次这 9 条的结果相同。各条恰好调用一次 `INTENT`，16 次 HTTP 请求均返回 200，超时、重试、办理授权和退款提交均为零。原始日志未匹配到本地配置中的凭据值。[逐条日志](../../../evidence/order-id-current-turn-missing-2026-11-27.jsonl)

定向复测未通过，工单 02 仍未通过。验收勾选保留，正式验收需完成新独立样本与工单 01 固定基线的实模对照并达到各项门槛。

```powershell
$revision = git rev-parse --short HEAD
$caseFlags = @('--sample-id','F121','--sample-id','F122','--sample-id','F123','--sample-id','F124','--sample-id','F125','--sample-id','F126','--sample-id','F161','--sample-id','F171','--sample-id','F196')
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py run --source . --version current --revision "$revision+working-copy" --workers 2 --samples test/order_id_four_digit_live.tsv --output evidence/order-id-current-turn-missing-2026-11-27.jsonl @caseFlags
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-current-turn-missing-2026-11-27.jsonl --samples test/order_id_four_digit_live.tsv @caseFlags
```

### 2026-11-27 明确从对话数组最后一项取号后的定向复测

当前应用的 `INTENT` 提示词明确要求从对话数组最后一项的 `content` 选取唯一目标订单，填入的完整编号须能在该消息中逐字找到。通过现有退款工作流和真实 `Qwen/Qwen3-8B` 复测 F121–F126、F161、F171、F196。与上一轮使用相同样本和 Qwen 配置。将本次措辞改动在内存中还原后，计算出的应用源码指纹与上一轮日志一致。

- 正确识别本轮缺号由 0/9 变为 1/9。F171 返回 `missing / null / null`，没有进入 `PLAN`，回复「请提供一个要查询的演示订单编号。」
- F121–F126、F161、F196 仍返回 `extracted / DEMO-9000`，依据均为历史原句「请问 DEMO-9000 什么时候发货？」。8 条均将该编号送入 `PLAN` 和隔离查单，回复「未找到可操作的订单。」F122 的查询目标由退款资格咨询变为状态查询。
- 9 条普通路径各调用一次 `INTENT`。16 次 HTTP 请求均返回 200，无超时、重试、节点失败、退款办理授权或提交。使用独立内存 checkpoint 和隔离业务回执，只连接 Qwen。已核对逐条日志没有匹配到本地配置的凭据值。

定向复测未达到历史编号误用归零的要求。工单 02 验收仍未通过，验收勾选保持原状。本轮覆盖上述 9 条既有错例。[本轮逐条日志](../../../evidence/order-id-current-message-prompt-retest-2026-11-27.jsonl) · [上一轮逐条日志](../../../evidence/order-id-current-turn-missing-2026-11-27.jsonl)

```powershell
$revision = (git rev-parse HEAD).Trim() + '+working-copy'
$caseOptions = @('--sample-id', 'F121', '--sample-id', 'F122', '--sample-id', 'F123', '--sample-id', 'F124', '--sample-id', 'F125', '--sample-id', 'F126', '--sample-id', 'F161', '--sample-id', 'F171', '--sample-id', 'F196')
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py run --source . --version current --revision $revision --workers 2 --samples test/order_id_four_digit_live.tsv --output evidence/order-id-current-message-prompt-retest-2026-11-27.jsonl @caseOptions
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-current-message-prompt-retest-2026-11-27.jsonl --samples test/order_id_four_digit_live.tsv @caseOptions
```

### 2026-11-28 带完整对照示例的提示词定向复测

将确认的完整提示词载入当前应用的隔离副本，保留原生 JSON Schema 和工作流。复测 F121–F126、F161、F171、F196。应用副本替换提示词之前的源码指纹、9 条样本、Qwen3-8B 配置和并发数 2 均与上一轮一致。已核对提示词未包含这 9 条本轮原话或测试夹具的历史原句。

- 正确返回 `missing / null / null` 并追问编号的样本由 1/9 增至 7/9。F121–F125、F161 改为缺号，F171 保持缺号。
- 历史编号进入 `PLAN` 和隔离查单的次数由 8 降为 2。F126 填入 `DEMO-9000`，提取依据取自本轮但不含该编号。F196 填入同一历史编号，提取依据取自历史整句，并仍被分类为状态查询。两条均回复「未找到可操作的订单。」
- F122 恢复为退款资格咨询，但在没有待补号时仍把接续标记填成 `true`。F161 和 F171 均被判为退款资格咨询，其中 F171 上一轮正确判为退款状态查询。
- 9 条各调用一次 `INTENT`。全部 11 次 HTTP 请求返回 200，超时、重试、节点失败、退款办理授权和提交均为零。使用内存会话和隔离业务回执，只连接 Qwen。提示词快照和逐条日志均未匹配到本地配置中的凭据值。

定向复测仍未达到历史编号误用归零要求，工单 02 验收未通过。未运行完整 200 条或独立样本正式对照。[提示词快照](../../../evidence/order-id-reviewed-prompt-2026-11-28/prompt.txt) · [本轮原始日志](../../../evidence/order-id-reviewed-prompt-2026-11-28/live.jsonl) · [上一轮原始日志](../../../evidence/order-id-current-message-prompt-retest-2026-11-27.jsonl)

从仓库根目录复现。原始记录使用 `live.jsonl`，下面的命令把新运行写入 `rerun.jsonl`。

```powershell
@'
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, 'test')
from verify_order_id_final_prompt import INTENT, SAMPLES, copy_application, replace_prompt

cases = ['F121', 'F122', 'F123', 'F124', 'F125', 'F126', 'F161', 'F171', 'F196']
sample_options = ['--samples', str(SAMPLES)]
for case in cases:
    sample_options.extend(['--sample-id', case])
folder = Path('evidence/order-id-reviewed-prompt-2026-11-28')
output = folder / 'rerun.jsonl'
benchmark = [sys.executable, '-X', 'utf8', 'test/verify_order_id_extraction_live.py']
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
with TemporaryDirectory(prefix='order-id-reviewed-prompt-') as temporary:
    source = Path(temporary)
    base_sha = copy_application(source)
    intent = source / INTENT
    candidate = replace_prompt(intent.read_text(encoding='utf-8'), (folder / 'prompt.txt').read_text(encoding='utf-8'))
    compile(candidate, str(intent), 'exec')
    intent.write_text(candidate, encoding='utf-8', newline='\n')
    subprocess.run([*benchmark, 'run', '--source', str(source), '--version', 'current',
                    '--revision', f'{head}+base-{base_sha}+reviewed-prompt', '--workers', '2',
                    '--output', str(output), *sample_options], check=True)
subprocess.run([*benchmark, 'score', str(output), *sample_options], check=True)
'@ | .\.venv\Scripts\python.exe -X utf8 -
```

### 2026-11-28 强化订单号依据与退款意图后的定向复测

在当前应用的隔离副本中加载确认后的完整提示词，用真实 Qwen3-8B 重跑 F121–F126、F161、F171、F196。与上一轮的应用基础源码、样本、验证脚本、Qwen 配置及并发数 2 一致。提示词快照不包含这 9 条样本的本轮原话和测试历史原句，3 组完整示例均通过 `IntentDecision` 校验。

- 正确返回 `missing / null / null` 由 7/9 降至 3/9，仅 F125、F161、F171。F121–F124 由缺号回退为提取历史编号，F126 仍误用历史编号，提取依据由本轮不含编号的句子变为历史整句。这 5 条都把 `DEMO-9000` 送入 `PLAN` 和隔离查单，上一轮为 2 条。
- F196 的模型意图由 `order_question / status` 变为 `refund_request / null`，仍提取了历史编号 `DEMO-9000`，依据是本轮不含编号的原话。流程回复「请明确说明是否要办理这笔订单的退款。」没有进入 `PLAN` 或建立退款办理授权。本轮模型原始输出共 6 条使用历史编号，实际进入 `PLAN` 的有 5 条。
- F161、F171 的编号字段均正确，退款状态查询仍分错。F161 判为办理退款并收到办理确认追问，F171 判为资格咨询并收到资格咨询补号追问。F122 的资格咨询再次被判成状态查询。
- 9 条各调用一次 `INTENT`。14 次 HTTP 请求均为 200，超时、重试、节点失败、退款办理授权和提交均为零。使用独立内存会话和隔离回执，只连接 Qwen。提示词和日志均未匹配到本地配置的凭据值。

定向复测未满足历史编号误用归零要求，工单 02 仍未通过。本轮覆盖 9 条既有错例，完整 200 条和独立样本对照尚未运行。[本轮提示词](../../../evidence/order-id-revised-prompt-2026-11-28/prompt.txt) · [本轮逐条日志](../../../evidence/order-id-revised-prompt-2026-11-28/live.jsonl) · [上一轮逐条日志](../../../evidence/order-id-reviewed-prompt-2026-11-28/live.jsonl)

复现可沿用上一节的隔离运行命令，将 `folder` 改为 `Path('evidence/order-id-revised-prompt-2026-11-28')`，将版本标记末尾改为 `+revised-prompt`。复算本轮原始日志的命令如下。

```powershell
$cases = @('F121','F122','F123','F124','F125','F126','F161','F171','F196')
$flags = foreach ($case in $cases) { '--sample-id'; $case }
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-revised-prompt-2026-11-28/live.jsonl --samples test/order_id_four_digit_live.tsv @flags
```


### 2026-11-28 按用户提供的提示词定向复测

在本地应用的隔离副本中替换普通 `INTENT` 提示词，运行真实 `Qwen/Qwen3-8B`。保留用户提供的规则、例句、顺序和空行，将消息中的 Markdown 强制换行转为普通换行，补入模板末尾的换行。与上一轮使用相同的基础源码、验证脚本、9 条样本、并发数 2、Python 及依赖版本和 Qwen 配置。F196 的前半句出现在提示词例句中，本轮使用既有错例作定向回归。

| 指标 | 上一版 | 本版 |
| --- | ---: | ---: |
| 模型返回 `missing / null / null` | 3/9 | 1/9 |
| 模型输出历史编号 | 6/9 | 8/9 |
| 历史编号进入 `PLAN` 并用于隔离查单 | 5/9 | 8/9 |
| 截断编号进入 `PLAN` | 0 | 0 |
| 普通 `INTENT` 恰好调用一次 | 9/9 | 9/9 |
| 错误建立退款办理授权 | 0 | 0 |

- F121–F126、F161、F196 均返回 `extracted / DEMO-9000`，提取依据均为历史整句「请问 DEMO-9000 什么时候发货？」。这 8 条均进入 `PLAN` 并查同一历史编号。F125、F161、F196 为本轮新增的历史查单。
- F161 由办理退款改为正确的 `order_question / status`，编号由缺号回退为误用历史号。F171 由资格咨询改为正确的退款状态查询，继续正确填缺号，工作流要求提供查询编号。
- F122 仍把资格咨询判为状态查询。F196 从正确的 `refund_request` 回退为 `order_question / status`，并查历史订单。两条各自的接续标记均为 `false`。
- 17 次 HTTP 请求均为 200，超时、重试、节点失败及退款提交均为零。9 条端到端 P95/P99 均为 40.606 秒，`INTENT` HTTP P95/P99 均为 30.440 秒。使用虚构订单、隔离回执和内存 checkpoint，只连接 Qwen。未连接 MySQL、MCP 或 Milvus，未用 bge-m3 或 reranker。提示词和日志均未匹配到本地配置中的凭据值。

工单 02 验收仍未通过。本轮有 8 条历史编号进入 `PLAN`，超过零错误门槛。正式的 200 条独立同题对照、精确提取率与否定纠正准确率尚未对这版提示词实测。[提示词快照](../../../evidence/order-id-user-prompt-2026-11-28/prompt.txt) · [本轮逐条日志](../../../evidence/order-id-user-prompt-2026-11-28/live.jsonl) · [上一版逐条日志](../../../evidence/order-id-revised-prompt-2026-11-28/live.jsonl)

复测可沿用前文的隔离运行命令，将 `folder` 改为 `Path('evidence/order-id-user-prompt-2026-11-28')`，将版本标记末尾改为 `+user-prompt`，另写 `rerun.jsonl`。复算本轮日志的命令如下。

```powershell
$cases = @('F121','F122','F123','F124','F125','F126','F161','F171','F196')
$flags = foreach ($case in $cases) { '--sample-id'; $case }
.\.venv\Scripts\python.exe -X utf8 test/verify_order_id_extraction_live.py score evidence/order-id-user-prompt-2026-11-28/live.jsonl --samples test/order_id_four_digit_live.tsv @flags
```


### 2026-11-28 切换普通 INTENT 提示词

按用户确认，将本轮提供的「订单号来源规则」版本接入普通 `INTENT`。提示词由 `implementations/refund/intent_prompt.py` 提供，应用填入待补号状态及按时间顺序序列化的对话数组。逐字核对渲染文本，两组完整输出示例均通过 `IntentDecision` 校验。离线工作流测试覆盖两种待补号状态、实际请求中对话顺序及原生严格 JSON Schema，全部 244 项通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q test
```

本版的 Qwen 实模结果待验证。上节的定向复测结果对应上节提示词快照，工单 02 的实模验收仍待通过。

### 2026-11-28 确认退款架构改造的验收口径

按用户确认，退款架构改造采用 [ADR-0012](../../../docs/adr/0012-refund-policy-for-explanation.md) 的按需政策检索和办理路由。工单 02 继续使用工单 01 的固定基线和既定提取及授权门槛。正式报告记录两版的代码及退款流程版本，分别统计 `INTENT` 模型调用和端到端延迟，并结合后续路由变化解释端到端差异。
