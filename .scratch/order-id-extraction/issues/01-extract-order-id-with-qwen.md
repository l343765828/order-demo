# 01 改造订单号提取并通过离线回归

Status: ready-for-agent

Blocked by: 无

## 目标

消费者可以在本轮消息里纠正要查的订单号，也可以用自然语言补充退款资格咨询所需的编号。普通路径复用 Qwen3-8B 一次识别意图和订单号，退款办理继续独立校验授权。参见[改造规格](../spec.md)。

## 验收标准

- [x] 修改行为前记录可复现的基线代码版本及影响对照结果的工作区改动，供 02 使用。
- [x] 普通路径一次调用 Qwen3-8B 完成 `INTENT` 的意图识别和订单号提取。模型输出通过严格 JSON Schema 和 Pydantic 校验，提供订单号、原文依据及 `extracted`、`missing`、`ambiguous` 状态。无效输出或超时进入现有降级路径。
- [x] 「不是 DEMO-1001，查 demo-1002」和明确排除旧编号的消息可以直接查询更正后的订单。普通只读路径直接采用模型明确提取的编号，转大写后进入 `PLAN`。该路径移除正则候选集合限制、原文核对及完整编号边界校验，沿用下游订单存在性、归属和回执校验。
- [x] 缺号或只说「还是刚才那个」时请消费者补充本轮编号。多笔订单无法确定目标时请消费者明确。资格咨询待补号时，单独发送合法编号走现有快速路径，「是 DEMO-1002，谢谢」也能接续原资格问题。明确提出新需求时按本轮意图处理。
- [x] 当前契约写入的 checkpoint 可读取并接续待补号问题。图状态继续使用现有业务决定格式，模型提取依据和状态用于本轮的输出观察与验收。
- [x] 通过现有工作流入口回归退款办理。单笔明确办理仍经过现有独立授权和归属校验。多个不同编号、否定、引用、假设和资格咨询等场景不因模型选出编号而获得办理授权。
- [x] 原有 LangGraph 节点和后续路由继续使用。离线用例覆盖当前消息的选单、缺号或歧义、大小写、补号接续、模型失败及契约错误。现有查单、资格咨询、退款办理、政策咨询和偏好测试全部通过。

## Comments

### 2026-11-26 实施与验收

工单 01 实施及离线验收完成，7 项验收标准均有离线证据。普通 `INTENT` 使用一次严格结构化调用完成意图识别和订单号提取。只读查单将明确选出的编号转为大写交给 `PLAN`。原样编号、依据和状态记录为本轮事件，checkpoint 保留既有业务决定字段。带短句的补号可以接续原资格咨询，模型校验失败时保留原待补问题供重试。

从现有工作流入口验证退款办理。独立授权拦截多笔订单、否定、引用和假设。同轮物流查询中的编号无法授权办理，带客服背景的独立明示指令仍能进入原有归属检查。无效模型输出、拒答、意外工具调用和超时沿用降级回复。

改动前固定 `refs/baselines/order-id-extraction-01`，基线树为 `73840adf4b6adceac95c679dc03c96235b3e4231`，包含原始提交 `c4e213e130b868500283a1e8a87eba2710eadb96` 上影响对照的 40 个既有源码和 SQL 改动。独立目录重放基线时，190 个用例通过。复现命令和变更清单查询见[离线验收记录](../../../evidence/order-id-extraction-01.md)。

```powershell
.\.venv\Scripts\python.exe -m pytest -q test/test_order_id_extraction.py
.\.venv\Scripts\python.exe -m pytest -q test
.\.venv\Scripts\python.exe -m mypy implementations/refund/decision.py implementations/refund/intent.py model/refund/flow.py model/refund/intent.py model/refund/status.py workflows/refund_nodes.py
.\.venv\Scripts\python.exe -m py_compile test/verify_refund_submission.py
git diff --check refs/baselines/order-id-extraction-01 -- '*.py' '*.sql'
```

定向回归 54 个用例通过，全量离线回归 244 个用例通过。6 个源码文件的类型检查、验证脚本语法检查和 diff 检查均通过。规范与规格两项审查的发现已修复并复查。

本票使用虚构订单、内存 checkpoint 和模拟 HTTP 响应，未连接真实 Qwen 或 MySQL。同批至少 200 条独立样本的真实 Qwen 对照及延迟统计属于工单 02，复测可直接使用上述本地基线引用。
