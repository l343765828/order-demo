# 07 单轮模拟退款验收

验收日期为 2026-11-23。连接目标数据库后先执行 `SELECT VERSION()`，返回 **MySQL 8.0.46**。Milvus 返回 **3.0.2**。联验使用独立进程的 Streamable HTTP MCP 2.3.0、DeepAgent 0.7.38、LangGraph 1.2.22、Qwen3-8B、BGE-M3 和 BGE reranker。

## 前置条件

- 工单 16 的目录、版本快照和真实服务验收已具备，工单 15 的本轮回执核验和工单 03 的受控单次发送适配器已接入。详见 [目录验收](policy-16.md)、[回执验收](policy-15.md)和[协议验收](protocol-03.md)。
- 目标环境默认 `order_demo` 中的五张相关业务表存在，尚未建立政策发布目录。联验为每组创建随机命名的 MySQL 库及 Milvus 集合，通过发布入口在隔离目录激活演示政策 1.0。生产启用须先给默认库登记生效政策，再配置独立的 MCP 执行凭据和 Host 的持久事件目录。

## 复跑命令

`$issue07Config` 指向仓库外、含经授权连接配置的 JSON。脚本派生自己的临时配置、MCP 凭据和持久事件目录。证据按验收组记录版本、运行方式及各组清理确认。

```powershell
.venv/Scripts/python.exe -X utf8 test/verify_refund_submission.py --config $issue07Config --evidence evidence/refund-07-integration.jsonl
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short
.venv/Scripts/python.exe -X utf8 -m mypy --ignore-missing-imports --check-untyped-defs config implementations/refund model/orders model/refund workflows mcp_service/server.py utils/events.py utils/mysql.py utils/mcp_client.py cli.py
.venv/Scripts/python.exe -X utf8 -m compileall -q cli.py config implementations interfaces model mcp_service workflows test utils
.venv/Scripts/python.exe -m pip check
git diff --check
git diff --cached --check
```

`--group business` 验证完整 CLI、服务端和订单限制，`--group amount` 单独复跑自定金额及渠道，`--group safety` 验证政策和持久化故障，`--group outcomes` 验证执行回执及展示。故障组可加 `--investigation-probe`，由探针发出确定的只读工具调用，仍使用同一 CLI、LangGraph、独立 MCP 进程、真实 MySQL 和 RAG。此模式专门验收故障与副作用，记录中有 `probe_readonly_investigation` 事件。

## 已观察到的结果

- 从真实 CLI 输入「如果能退，就帮我退 DEMO-1001」。记录中有 LangGraph、Plan-and-Execute、官方 DeepAgent、ReAct 和独立 MCP 进程的关联事件。第一条 Milvus 查询已带 `entry_type == "policy"` 条件。最终核验文件、提交、原始用户消息及退款记录按 `trace_id` 和幂等键对应，按核验文件、提交、单次 MCP 写入的顺序发生。CLI 展示 ¥199.00 和库内模拟退款单号。
- 未支付和已发货拒绝，其他消费者订单返回统一不可操作提示。DEMO-1003 返回已存的 MOCK-RF-1003 和 ¥88.00，政策检索及写调用都为零。消费者另行提出只退 1 元、改账户及渠道，实际仍按订单实付 19900 分办理，MCP 写工具只收到订单号和幂等键。故障探针把合格订单在最终重查前改为已发货，Host 拒绝办理，提交和写调用都为零。
- 独立服务拒绝调查凭据、缺失提交、会话归属不符、订单与提交不符及授权已撤销的首次写入。同提交的并发请求只产生一笔退款，另一个返回原记录。完成的幂等键仍能读回同一记录。
- 重排失败、正式候选为空、矛盾条款与非法引用均无最终政策文件、提交或写调用。核验文件写前失败无文件，文件同步后丢失确认留下一份文件，均无提交或写调用。提交保存前失败保留政策文件，提交确认丢失留下政策文件和一条 `pending` 提交，两者写调用为零。事件保存故障及重排故障期间，查单和原退款读取成功，旧提交经 `get_refund_result` 得到 `not_found` 后仍保留拦截。
- 在 MySQL 提交成功后丢弃实际 MCP 回执，本轮先只读核对并返回原单，提交记为 `succeeded`。丢弃明确拒绝后的回执并暂查无原单，记录为 `unconfirmed`，下一会话只读核对。两种场景都各有一次 MCP 写调用。MySQL 约束故障返回明确失败，提交关闭。错误执行凭据使 MCP 初始化明确失败，提交关闭，写调用为零。模板异常时 CLI 展示实际订单、19900 分和模拟退款单号，再次办理读到同一记录。

全仓 pytest **94 项通过**，mypy **37 个源文件通过**。编译、依赖、独立导入和 Git 差异检查通过。业务、金额和两组故障探针的隔离库、向量集合及随库创建的检查点均已清理。

首轮完整模型调查在故障组曾达到 180 秒子进程时限。该轮先使用只读探针，通过真实 MCP、MySQL 和政策检索验证故障行为。

## 工单 11 完成后补验

2026-11-23，使用包含工单 11 最终改动的同一份固定代码快照，依次运行 `--group safety` 和 `--group outcomes`。两组均使用真实 Qwen、官方 DeepAgent、ReAct、独立 MCP、MySQL 8.0.46、Milvus 3.0.2 和原故障注入。Safety 组 11 个用例、Outcomes 组 8 个用例全部通过，两组退出码均为 0。两套隔离库、集合和临时配置均已清理。当前工作区全仓 pytest 98 项通过。

[首轮联验回执与库内记录](refund-07-integration.jsonl) · [完整模型链故障组复跑证据](refund-07-full-chain.jsonl)
