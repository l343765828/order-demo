# 03 模拟退款事务与退款提交记录验收

2026-11-24，Windows，Python 3.11.7。对目标实例先执行 `SELECT VERSION()`，返回 **MySQL 8.0.46**。联验使用 SQLAlchemy Core **2.0.58**、PyMySQL **1.2.4**、MCP **2.3.0**、Milvus **3.0.2**、LangGraph **1.2.22**、DeepAgents **0.7.38** 和 PyMySQLSaver **3.0.0**。

## 前置与改动前基线

施工顺序为 01 → 02 → 03，直接依赖 02。检查了 01 的共用 Engine、SQL 加载器和查单入口，及 02 的会话 Service、命名锁、原文提交、CLI 与 PyMySQLSaver 入口。两张工单已逐项验收。基线为 `8f1880e` 加开工时的工作区改动及暂存文件，真实基线从该工作区的副本运行。 [01 验收](mysql-core-01.md) · [02 验收](mysql-conversation-02.md) · [旧退款联验](refund-07.md)

- 全仓 pytest 103 项通过，退款、MCP、查单和 Core 相关 pytest 30 项通过。
- `verify_refund_submission.py --group outcomes --investigation-probe` 在改动前通过 8 个场景，包括丢失成功或拒绝回执、后续核对、状态变化、约束失败、展示异常和写身份初始化失败。隔离库和集合均已核实清理。

## 入口与事务

| 入口 | 新实现与事务范围 |
| --- | --- |
| 独立 MCP `get_refund_result` | `MySQLRefundTransactionService` 通过 `refund_repository` 按认证消费者及订单读取原记录，释放只读连接后返回。幂等键用于关联日志。 |
| 独立 MCP `submit_mock_refund` | 同一连接依次锁定提交、本人订单和退款记录，核验授权来源。已有记录直接返回，首次写入才完整校验当前订单事实并插入。Service 提交成功后确认本次成功；拒绝、已有记录或提交前异常释放行锁并回滚；提交阶段出错返回 `unconfirmed`。 |
| CLI / Host `RefundExecution` | `MySQLRefundDispatchService` 通过 `dispatch_repository` 查询未决提交。确认最终政策证据持久化后，使用短事务保存本次提交，确认提交后才发一次 MCP 写请求。已有拦截转只读核对。回写结果使用独立短事务和原有期望前态。 |

MCP 在查单与退款 Service 间复用一个业务 Engine，CLI 在会话和提交 Service 间复用一个业务 Engine，进程退出时释放。政策目录仍使用原接入，CLI 偏好仍使用原连接池，管理初始化、种子与 PyMySQLSaver 保留原 PyMySQL 路径。表结构、LangGraph 节点和恢复入口以及模型提示词沿用基线。实际 HTTP `tools/list` 对四个工具返回的描述、注解及 Schema 前后相同，规范化 JSON 的 SHA-256 为 `5f1444096e67a4e53fdc3da317475c70c199c5707179d2db790e9fe3bad83b1c`。

## 逐项实测

1. **归属、写权限、规则和幂等。** `business` 组用真实 Qwen3-8B、DeepAgent、ReAct、政策检索和独立 MCP 正常办理，CLI 展示库中同一笔模拟退款及 **19900 分**的实付金额。未支付、已发货、他人订单和已退款样本沿用原结果，自定金额或渠道不改变整单实付规则。独立 MCP 拒绝调查凭据、缺失提交、错误会话消费者、订单不匹配及撤销授权。并发重复提交一笔成功、另一笔返回相同原单，已完成的键仍返回原单。实际核对本人 `DEMO-1003` 返回 `MOCK-RF-1003`，金额 **8800 分**，即使传入未知提交键。已有退款的他人订单与不存在的订单均返回 `not_found`。
2. **Host 持久化、竞争和外部调用边界。** 正常链的 `trace_id` 与幂等键可以关联正式条款、最终核验文件、授权原文、提交、MCP 写入和退款。故障探针在发送前从另一连接读到已提交的 `pending`，并读到同一标识的核验文件；SQLAlchemy 事务事件显示发送和只读核对入口没有活动业务事务。政策重排、证据和核验事件保存故障以及提交保存失败或确认丢失均零写调用，已有结果和未决的只读核对仍可用。两个会话竞争同一订单，在首笔提交未决时另一方只读核对并返回待核对，最终只产生一条提交、一条退款和一次写调用。
3. **回滚、提交回执及恢复。** 见下表。另用独立连接锁住提交行，真实行锁等待超时返回明确失败；释放后同键可成功，仅留一条退款。历史订单字段无法按当前实体校验时，MCP 仍优先返回有效原退款或按撤销授权拒绝；确需首次写入时返回数据库失败。该顺序在独立 MCP 上先复现回归、再验证修复。
4. **协议、日志和调度。** MCP 协议回归覆盖双凭据、请求参数、结构化回执、只读工具注解、`trace_id`、`tool_event_id`、`mcp_request_id`、订单和提交键；退款读写工具仍在 SDK 同步工作线程运行。正常业务组通过真实 LangGraph 和只读调查完成执行前调查，写调用来自原固定节点。准备发送状态恢复回归通过，跨进程 Checkpoint 读写、续跑、按线程清理及会话原文持久化回归通过。

| 隔离故障场景 | 独立 MCP 和库内观察 |
| --- | --- |
| MySQL 约束拒绝写入 | 写工具 `failed`，提交 `closed_no_refund`，退款零条。 |
| 实际 INSERT 后、COMMIT 前抛出异常 | 观察到真实 INSERT，写工具 `failed`，提交 `closed_no_refund`，退款零条。 |
| 实际 COMMIT 成功后断开驱动连接并丢失确认 | 写工具 `unconfirmed`，随后的只读核对 `found`。提交 `succeeded`，退款一条，金额 19900 分。 |
| 进入 COMMIT 时断开驱动连接，未完成提交 | 写工具 `unconfirmed`，随后的只读核对 `not_found`。提交 `unconfirmed`，退款零条。 |
| 上述两种 COMMIT 断连后，重开原会话及从新会话再次明确办理 | 原会话原文可读。两种会话均只核对一次、写调用零次，成功分支读到原单，未决分支继续拦截。库内状态保持一致。 |
| 丢弃独立 MCP 已返回的成功或拒绝回执 | 原有 07 场景通过。成功后核对原单；拒绝回执丢失且查无记录时保留拦截。 |

## 复跑命令

`$python` 为项目虚拟环境的 Python，`$config` 为仓库外的授权验收 JSON，`$output` 为临时输出目录。基线组在开工前工作区副本中执行。各组自行派生本机 MCP 端口与凭据及隔离库、集合，退出时核实清理。

```powershell
& $python -X utf8 -m pytest -q --tb=short
& $python -X utf8 -m pytest -q test/test_refund_submission.py test/test_mcp_protocol.py test/test_mysql_core.py test/test_order_query.py --tb=short
& $python -X utf8 test/verify_refund_submission.py --config $config --evidence "$output/baseline-outcomes.jsonl" --group outcomes --investigation-probe
& $python -X utf8 test/verify_refund_submission.py --config $config --evidence "$output/business.jsonl" --group business
& $python -X utf8 test/verify_refund_submission.py --config $config --evidence "$output/safety.jsonl" --group safety --investigation-probe
& $python -X utf8 test/verify_refund_submission.py --config $config --evidence "$output/outcomes.jsonl" --group outcomes --investigation-probe
& $python -X utf8 test/verify_conversation_mysql.py --config $config
& $python -X utf8 test/verify_checkpoint_mysql.py --config $config
& $python -m mypy --ignore-missing-imports --check-untyped-defs cli.py implementations/refund implementations/orders interfaces/refund mcp_service repositories model/orders model/refund utils/database.py
& $python -m compileall -q cli.py config implementations interfaces model mcp_service repositories workflows test utils
& $python -m pip check
git diff HEAD --check
git diff --cached --check
```

正常业务组通过 **7** 个场景，政策与保存故障组通过 **11** 个场景，执行结果故障组通过 **19** 个场景，三组退出码均为 0，MySQL 隔离库和 Milvus 集合均已核实清理。会话与 Checkpoint 两条独立验证退出码均为 0。结束时全仓 pytest **103 通过**，相关 pytest **30 通过**，mypy **46 个源文件通过**，编译、依赖及 Git 差异检查通过。

故障组的调查由确定性只读探针驱动，使用真实 CLI、LangGraph、独立 MCP、MySQL、Milvus、向量和重排服务。早期同时启动多个验收组时，本机子进程导入依赖出现 `MemoryError`，该组隔离资源已清理，随后单独复跑通过。故障组本轮未逐例重跑模型调查；真实模型全链覆盖正常业务组。
