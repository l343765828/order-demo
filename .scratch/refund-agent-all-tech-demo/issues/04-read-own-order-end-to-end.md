# 04: 从消费者 CLI 经真实 MCP 查询本人订单

**Status:** ready-for-agent

**Blocked by:** [01 专用库与种子](01-mysql-schema-and-seed.md)、[02 MySQL checkpoint](02-verify-mysql-checkpointer.md)、[03 模型与 MCP 协议](03-verify-model-and-mcp-protocols.md)。

## 目标

消费者输入“查一下 DEMO-1001”，通过正常 CLI 入口看到本人订单的实际状态。该路径是首个可以独立演示的业务切片。

## 范围

接通最小 CLI、Host 与独立 MCP Server 的应用入口。Host 保存聊天原文和会话状态，经 LangGraph 的 Plan／Execute／Observe／Replan、官方 DeepAgent 的 OrderAgent 与 ReAct 完成一次只读查单。本票限定直接查单，政策问答由 06 接入。

## 涉及的契约

- `ConversationDecision`、`InvestigationPlan`、`TaskObservation`、`ReplanDecision`。计划使用允许的只读任务，观察来自真实工具事件，外层校验依赖及执行预算。
- `get_order`、`GetOrderRequest`、`OrderQueryResult`；固定模拟消费者 `DEMO-USER-01` 由可信运行上下文提供，MCP 从认证上下文确定消费者。Host 业务查单只经 MCP，直接访问 MySQL 管会话和 checkpoint。 [主设计](../spec.md#6-输入输出与状态契约) · [MCP 接入](../mcp-integration.md) · [ADR-0006](../../../docs/adr/0006-refund-agent-mcp-service-boundary.md)

## 验收标准

- [x] 从 CLI 查询 `DEMO-1001`，用真实 Qwen、真实跨进程 Streamable HTTP MCP 和真实 MySQL 得到本人订单状态与实付 19900 分。可关联会话、LangGraph 节点、实际计划、DeepAgent 委派、ReAct 调用与观察、重规划决定、MCP 调用和业务结果。
- [x] 查 `DEMO-2001` 和不存在订单均得到同样的不可操作提示，无订单详情；冒用消费者参数、普通客户端名称或 thread ID 无法越权。服务故障与未找到可操作订单可区分。
- [x] OrderAgent 只得到 `get_order`。监督层及默认子 Agent 无法访问写工具、任意 Shell、网络浏览或宿主文件写入。非法计划依赖、非法工具入参、伪造观察或超预算均不会触发额外业务调用。
- [x] 原始消费者和助手消息按序保存，重开 Host 后可按同一消费者、同一会话载入历史与 checkpoint。换会话或消费者拿不到该会话的受限上下文。全组用例的退款写调用计数为零。

## 验证方式

在 01 的种子库上分别启动两个应用进程，由 CLI 运行本人、他人、不存在和断连场景。检查进程日志、原文表与 checkpoint，重启 Host 后重复读取；用无效 Schema 和越权调用固定负例。

## Comments

2026-11-19 部分实施与复核：通过 `pod.yaml` 中的现有配置在进程环境中连接目标实例，`SELECT VERSION()` 实测 **8.0.46**；只读检查五笔种子存在，DEMO-1001 实付 19900 分。历史 8.0.17 和 1064 不是当前实例结果。执行 `order-demo/test/verify_checkpoint_mysql.py`，在临时隔离库复验 02：两个线程独立进程恢复并继续写入，重复清理一个线程后另一线程可读写、七张业务表不变；临时库已删除并核实。Python 3.11.7、PyMySQL 1.2.4、langgraph 1.2.22、langgraph-checkpoint 4.3.2、langgraph-checkpoint-mysql 3.0.0、deepagents 0.7.38。

03 基础路径复测：`python order-demo/test/verify_qwen_protocol.py` 获得真实工具调用、匹配 ID 的观察回传和具名 Schema；`python order-demo/test/verify_mcp_protocol.py` 完成独立进程 Streamable HTTP 发现和结构化往返并关闭 Client。03 工单仍未完成严格 Schema、异常协议头、断连时序及自动追加回合等验收，不能将其认作完整通过或声称受控发送已经覆盖所有场景。

新增 `order-demo/order_query.py` 的具名严格请求／结果和只读查询；消费者身份必须由未来认证层提供，不能由工具参数指定。运行 `python order-demo/test/verify_order_query_mysql.py` 直接读取真实种子库，验证本人 19900 分、他人和不存在订单一致不可操作、示例退款状态；未修改订单或退款数据。安装 `order-demo/requirements-protocol.txt` 后，`pytest order-demo/test/test_order_query.py -q` 6 项通过，校验伪造消费者字段、空订单号、结果状态与详情不一致及数据库故障不泄露连接信息；`pytest order-demo/test -q` 14 项通过，新增文件 mypy 和 compileall 通过，`pip check` 无冲突。全仓 `pytest -q` 收集阶段 7 项错误，原因包括已有 LangGraph 测试同名模块冲突及缺少 `langgraph.checkpoint.sqlite`。

验收结论：**本票未完成，四项均未勾选**。该查询函数尚未接入经认证的 MCP Server、CLI、LangGraph 和 DeepAgent；其内部消费者参数不是认证机制，也不能证明客户端身份隔离。仍须完成 03 尚缺的协议验证，并实施和验收应用链路、严格服务端认证与工具权限、计划与预算／真实观察校验、消息和 checkpoint 的消费者归属及重启恢复，核实退款写调用数为零。解除 03 前置条件需补齐其工单列出的失败注入、Schema 和受控发送证据。当前不更改 Status（仓库无完成状态）。

2026-11-19 续做与完整验收。03 最新提交 `cfb3244` 已通过全部协议验收。本轮连接目标 MySQL，`SELECT VERSION()` 为 **8.0.46**，五笔种子齐全。复用 03 的受控 MCP 客户端，在 CLI Host 接上 LangGraph、同步 PyMySQLSaver、官方 DeepAgent 与独立 MCP 服务。异步子 Agent 单独运行，避免继承外层同步 checkpointer 后调用未实现的 `aget_tuple`。

- 运行 `python order-demo/test/verify_order_cli.py`。真实 Qwen 和跨进程 Streamable HTTP MCP 查得本人订单已支付、未发货、实付 **19900 分**。记录包含会话、trace、实际计划、五个节点、委派、模型调用与观察、MCP 请求和重规划决定。Windows 进程 ID、关联标识、依赖版本和复跑命令见[验收记录](../../../evidence/order-cli.md)。
- 他人订单和不存在订单返回相同提示，无详情。关闭 MCP 后返回服务不可用。独立 MCP 拒绝冒用消费者、非法参数及仅使用普通客户端名称和 thread ID 的未认证请求。固定模型负例确认工具目录只含各自允许的 `task` 或 `get_order`，非法计划依赖、伪造观察和预算溢出不增加业务调用。
- 重新启动 Host 读回本人原文和 checkpoint。不同会话无该历史，归属为另一消费者的原会话 ID 被拒绝。检查八条原文的顺序和角色，退款写调用为 **0**，退款与提交表记录数一致，隔离会话和 checkpoint 已清理。
- `python -m pytest order-demo/test -q` **23 项通过**，mypy、compileall、pip check 通过。全仓 pytest 仍有七项既有收集错误，涉及同名测试模块与缺少 SQLite checkpoint 依赖。针对仓库规范与工单契约的代码审查均无待修问题。

验收结论：**04 的四项标准全部通过**。仓库未配置完成状态，Status 保留 `ready-for-agent`。
