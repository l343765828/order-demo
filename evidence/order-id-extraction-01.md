# 工单 01 订单号提取离线验收

日期：2026-11-26

## 改动前基线

| 项目 | 记录 |
| --- | --- |
| 工作分支 | `main` |
| 原始提交 | `c4e213e130b868500283a1e8a87eba2710eadb96` |
| 本地基线引用 | `refs/baselines/order-id-extraction-01` |
| 基线文件树 | `73840adf4b6adceac95c679dc03c96235b3e4231` |
| 源码与 SQL 差异的 SHA-256 | `4c569c17bf8247c7af6f71ddb8e6f2085cda5de059c2874e69b41e40cbe6aa24` |

基线在修改代码或测试前固定。以原始提交为底，将当时工作区相对原始提交的所有已跟踪 Python 和 SQL 改动应用到临时索引，再写入带本地引用的 Git 文件树。该过程保留原工作区和暂存区。基线捕获了 40 个文件的既有改动，包括 checkpoint 旧契约清理、演示数据重置、政策发布和证据契约调整、Host 和 CLI 接入及相关测试。`config/local.json` 沿用原始提交，机器本地配置与隔离验收数据独立提供。

```powershell
git rev-parse refs/baselines/order-id-extraction-01
git diff --name-status c4e213e130b868500283a1e8a87eba2710eadb96 refs/baselines/order-id-extraction-01
git diff --binary --no-ext-diff --no-color c4e213e130b868500283a1e8a87eba2710eadb96 refs/baselines/order-id-extraction-01 -- '*.py' '*.sql'
```

本地引用可在同一仓库供工单 02 使用。复测时可用下列命令解包到独立目录，原工作区继续保留当前改动。

```powershell
$orderIdRepo = (Get-Location).Path
$orderIdBaseline = Join-Path ([IO.Path]::GetTempPath()) ('order-id-baseline-' + [guid]::NewGuid())
$orderIdBaselineZip = "$orderIdBaseline.zip"
git archive --format=zip --output="$orderIdBaselineZip" refs/baselines/order-id-extraction-01
Expand-Archive -LiteralPath $orderIdBaselineZip -DestinationPath $orderIdBaseline
Push-Location $orderIdBaseline
try { & "$orderIdRepo\.venv\Scripts\python.exe" -m pytest -q test }
finally { Pop-Location }
```

已经分别在改动前工作区及使用该引用解包后的独立目录运行 `python -m pytest -q test`，两次均为 190 个用例通过。运行环境为 Windows、Python 3.11.7、pytest 9.2.0、pydantic 2.14.3、langgraph 1.2.22、langchain-openai 1.8.0、openai 3.55.1 和 httpx 0.28.1。

## 工作流离线回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q test/test_order_id_extraction.py
.\.venv\Scripts\python.exe -m pytest -q test
```

- 测试从 `run_refund_turn` 进入现有图。用 `httpx.MockTransport` 检查 Qwen 客户端发送的严格 JSON Schema、关闭思考模式及普通路径的一次 `INTENT` 调用。普通状态查询后续仍单独执行原有 `PLAN` 调用。无效 JSON、超时、拒答、意外工具调用和不符合契约的结果沿用降级回复。
- 核对单号查询、纠正和排除旧订单、大小写、缺号、历史指代和歧义。离线回执显示归一化的目标进入调查，原样编号、依据和状态由 `order_id_extraction` 事件记录。普通只读路径按规格直接采用模型选定的编号。
- 在内存 checkpoint 中写入并读回待补号会话。裸编号零次模型调用，带短句补号一次 `INTENT` 调用。两者接续原资格咨询。历史指代或歧义继续追问，明确查发货或明确提出新的资格问题时采用本轮问题。补号识别失败时沿用降级回复，保留原资格问题供重试。会话决定保持原有字段。
- 从工作流入口回归退款办理。单笔明确办理建立授权，随后由原有归属检查拒绝不可操作的订单。模型选单后仍单独核对消费者的办理指令是否明确关联该编号。测试覆盖多个编号、否定、引用、假设、仅资格咨询、原文无编号及编号只属于同轮物流查询，也验证转述结束后另行提出的明确办理指令可以建立授权。
- 订单号提取定向回归 54 个用例通过。全量离线套件 244 个用例通过，覆盖既有查单、资格咨询、办理、政策咨询和偏好管理。

## 类型与依赖检查

```powershell
.\.venv\Scripts\python.exe -m mypy implementations/refund/decision.py implementations/refund/intent.py model/refund/flow.py model/refund/intent.py model/refund/status.py workflows/refund_nodes.py
.\.venv\Scripts\python.exe -m py_compile test/verify_refund_submission.py
.\.venv\Scripts\python.exe -c "import sys; import implementations.refund.decision; assert not any(n == 'cli' or n.startswith('workflows.') or n == 'implementations.refund.host' for n in sys.modules)"
git diff --check refs/baselines/order-id-extraction-01 -- '*.py' '*.sql'
```

6 个源码文件的类型检查、验证脚本语法检查、决定器独立导入及 diff 检查均通过。规范审查指出的枚举引用、中文注释和折叠区域问题已修复。规格审查复现的授权和补号重试问题已增加工作流回归并通过复查。

## 实测范围

验收使用虚构订单、内存 checkpoint 和模拟 HTTP 响应。没有连接实际 MySQL。真实 Qwen、同批至少 200 条独立样本和延迟对照由工单 02 验收，本票的离线结果不代表真实服务的准确率或编造编号的发生率。
