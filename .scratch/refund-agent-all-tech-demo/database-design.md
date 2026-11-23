# 退款演示 MySQL 表结构

日期：2026-11-19  
状态：首版七张应用表与提交状态已确认，待实施  
关联：[主设计](spec.md)、[存储与记忆](storage-memory.md)、[MCP 两服务设计](mcp-integration.md)、[ADR-0008](../../docs/adr/0008-refund-authorization-per-dispatch.md)

## 已确认范围

在现有 MySQL 实例上为 `order-demo/` 新建独立数据库和表。MySQL 保存模拟订单、退款、聊天和记忆。退款政策向量继续使用专用 Milvus 集合。使用既定的固定演示消费者，订单资格、实际支付金额和退款结果由确定性业务代码判断。此次仅确认表结构，环境按用户说明视为就绪。

专用库名确定为 `order_demo`，采用 InnoDB 与 `utf8mb4`。退款 Agent（下称 Host）和 MCP Server 共用一套 MySQL 账号配置，通过公共 `model` 模块接入。模型只接收声明的只读业务工具，固定退款节点负责写入调用。

MySQL 保存订单、成功退款、带办理授权证据的提交记录、会话与记忆。最终政策核验事件与计划、委派、工具调用等过程事件保存在结构化日志中，提交记录通过 `trace_id` 关联日志。

## 公共 `model` 模块

两个服务都引用 `order-demo/model/`，共享七张应用表的定义、MySQL 连接配置与创建连接的代码。模块从运行环境统一读取 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER` 和 `MYSQL_PASSWORD`，并连接 `order_demo`。两个服务各自初始化和管理所在进程的连接池，每次操作的事务随所属服务执行。

Host 管理会话、记忆和提交记录，在会话中识别办理意图，并在准备发送时将授权证据写入提交记录。MCP Server 负责订单查询、退款结果核对与模拟退款事务。共享表定义后，消费者的订单及退款操作仍从 Host 经 MCP 发起。LangGraph 检查点使用同一套 MySQL 连接配置，其初始化和框架内部表由 Host 的适配器管理。

## 逻辑表与关键字段

下表列出七张应用表的字段。未标记可空的列均为 `NOT NULL`。订单号和消费者编号用 `VARCHAR(64)`，应用生成的不透明标识用 `VARCHAR(64)`，英文状态和类型用 `VARCHAR(24)`。所有时间用 `DATETIME(6)` 并统一写入 UTC，金额用 `BIGINT UNSIGNED` 存人民币整数分，币种用 `CHAR(3)` 默认 `CNY`。每个订单的成功退款至多一条。

| 表 | 写入方 | 关键字段 | 约束和用途 |
| --- | --- | --- | --- |
| `orders` | MCP Server 管理订单数据，样例由初始化命令录入 | `order_id`、`consumer_id`、`payment_status`、`shipping_status`、`paid_amount_fen`、`currency`、`created_at`、`updated_at` | `order_id` 主键，索引 `(consumer_id, order_id)`。支付状态为 `unpaid` / `paid`，发货状态为 `unshipped` / `shipped`。未支付金额为 0，已支付金额大于 0，是否已退款由 `refunds` 判断。 |
| `refunds` | MCP Server | `refund_id`、`order_id`、`idempotency_key`（样例可空）、`amount_fen`、`currency`、`source`、`created_at` | `refund_id` 主键，`order_id` 引用订单，`order_id` 和 `idempotency_key` 各自唯一。每行都是成功的模拟退款，金额等于订单实付金额。`source=seed` 时幂等键可空。`source=execution` 时必须填入事务内锁定的本订单提交的幂等键，以此追溯该次办理的授权证据。 |
| `conversation_sessions` | Host | `session_id`、`consumer_id`、`created_at`、`updated_at` | `session_id` 主键并用作 LangGraph `thread_id`，索引 `(consumer_id, updated_at)`。按可信消费者和会话标识加载上下文。 |
| `conversation_messages` | Host | `session_id`、`sequence BIGINT UNSIGNED`、`role`、`content LONGTEXT`、`created_at` | `(session_id, sequence)` 主键，`session_id` 引用会话。`role` 为 `user` 或 `assistant`，保存聊天原文，按会话顺序分配消息序号。工具调用与结果事件保存在同一追踪链的结构化日志。 |
| `conversation_summaries` | Host | `session_id`、`revision INT UNSIGNED`、`covered_through_sequence BIGINT UNSIGNED`、`summary_text LONGTEXT`、`generation_event_id`、`created_at` | `(session_id, revision)` 主键，覆盖从本会话第一条消息至截止序号，`(session_id, covered_through_sequence)` 引用原消息。新摘要验证成功后追加新版本，按最新有效版本恢复覆盖范围。 |
| `consumer_preferences` | Host | `consumer_id`、`preference_key VARCHAR(64)`、`value VARCHAR(255)`、`updated_at` | `(consumer_id, preference_key)` 主键，保存当前有效的显式偏好。更正更新同一键，删除移除该记录，历史消息和摘要不参与回填。 |
| `refund_dispatches` | Host 保存授权证据、撤销和提交结果，维护命令核对或关闭旧提交 | `idempotency_key`、`order_id`、`session_id`、`source_message_sequence BIGINT UNSIGNED`、`revoked_at`（可空）、`status`、`refund_id`（可空）、`trace_id`、`error_code VARCHAR(64)`（可空）、`created_at`、`updated_at`、`blocking_order_id VARCHAR(64)`（可空计算列） | `idempotency_key` 主键。订单、已核对的退款单和 `(session_id, source_message_sequence)` 分别引用订单、退款及本会话原始消息。消费者从该会话读取。`revoked_at` 记录授权撤销时间，`status` 记录提交结果。索引 `(order_id, status)`，`blocking_order_id` 唯一。 |

另外预留 **LangGraph checkpoint 存储**，属于 Host。它以 `thread_id` 为入口保存外层图的计划、任务观察、当前会话办理意图、执行位置和恢复数据。实际物理表数量由与目标 MySQL 版本兼容的适配器决定。已发起提交的授权证据和原退款结果分别以 `refund_dispatches` 和 `refunds` 为准。

## 政策发布目录

[工单 16](issues/16-publish-policy-catalog-revisions.md) 在七张演示应用表之外接入 MySQL 政策发布目录。目录记录每份文档的生效版本及发布历史，旧目录修订可还原本次退款调查采用的版本组合。发布记录须能区分曾经生效的版本与可清理重试的版本，并保存发布者对资格行为的确认。

发布命令在验证 Milvus 条目可读可检索后，同一 MySQL 事务提交生效版本和目录修订，按发布开始时的生效版本识别同文档的并发切换。MCP 读取目录，Host 通过工具回执取得目录修订号。目录和政策索引由发布流程管理，演示重置保留两者。见[主设计](spec.md#52-发布重试与资格规则)和 [ADR-0010](../../docs/adr/0010-refund-policy-publication-and-evidence.md)。

## 关系与退款写入

- 准备发送前，办理意图属于当前会话。Host 校验消息角色、会话归属及本次绑定的订单。提交记录保存本次授权或明确继续的原始用户消息序号。分两轮先授权、再补单号时，提交记录引用明确授权的消息，授权核验事件通过 `trace_id` 同时关联补单消息。
- 校验通过且确需新执行时，Host 生成本次幂等键，先持久保存一条最终政策核验事件。该事件记录 `trace_id`、幂等键、实际采用的 `(document_id, version, clause_id)`、原文片段及对应的检索事件 ID。确认保存成功后，才以同一追踪标识和幂等键保存提交记录并发出写调用。保存失败或无法确认时，暂停本次新提交并告知消费者。查单、查看已有退款结果和核对旧提交照常进行。见 [ADR-0009](../../docs/adr/0009-refund-policy-evidence-before-dispatch.md)。
- 一笔订单可有多次退款提交，每条提交携带一次办理的授权证据，订单最多有一条成功退款。重复办理按本人订单核对并返回原退款单号。执行产生的退款以幂等键关联实际创建它的提交，提交引用已存在的同订单退款时保留原退款的幂等键。
- MCP 在写入事务内先按幂等键锁定提交记录，再锁定目标订单。首次写入要求提交属于请求订单、会话消费者与认证身份一致、状态为 `pending` 且 `revoked_at` 为空，并复核订单归属、原退款记录、支付和发货状态，使用实际支付金额。已有结果分支验证归属后返回原退款。
- Host 处理同一会话的取消、换单和创建提交时须串行。提交已保存时，撤销通过锁定同一提交记录写入 `revoked_at`。先完成的撤销阻止首次写入，已成功的退款仍可核对并返回。撤销只改变授权状态，未决提交继续拦截同订单，直到核对成功或事务内确认无退款并关闭。
- 回执丢失后先只读核对。查到原退款记录就保存已确认结果。暂时查无记录时保留待核对状态，拦截同订单的新提交，覆盖跨会话与并发请求。其他订单照常办理。见 [ADR-0007](../../docs/adr/0007-refund-unconfirmed-dispatch.md)。
- 正常重启按已有规则恢复会话和待办。显式演示重置时先停流并确认在途写入结束，清理提交记录后再清理其引用的原始消息、退款和订单，最后装载初始订单与示例退款。重置保留政策发布目录和 Milvus 政策索引。

## 提交记录状态

| 状态 | 含义 | 拦截同订单新提交 |
| --- | --- | --- |
| `pending` | Host 已存提交记录，等待执行或确认结果。 | 是 |
| `unconfirmed` | 当前无法确认写入结果。 | 是 |
| `succeeded` | 已核对成功退款，关联原退款单。 | 否，后续直接返回原结果 |
| `closed_no_refund` | 拒绝、已确认回滚，或核实未产生退款且该请求已关闭。具体原因写入 `error_code`。 | 否 |

`blocking_order_id` 在 `pending` 或 `unconfirmed` 时等于订单号，在终态为空。创建新提交时由唯一约束阻止同订单出现第二条未决记录。

## 维护核对与恢复

1. 演示维护命令和 MCP 退款事务使用同一锁顺序，先按 `idempotency_key` 锁提交记录，再锁对应订单。维护命令在同一事务中核对当前退款记录。
2. 已终结的提交直接返回其已确认状态。对未决提交，有退款时更新为 `succeeded`，关联并返回原退款单。没有退款时将旧提交更新为 `closed_no_refund`，释放该订单的新提交限制。普通查询仍是只读操作。
3. MCP 首次写入要求提交仍为 `pending` 且授权未撤销。锁和状态检查防止迟到的旧请求通过。回执按原幂等键及期望前态处理，`succeeded` 或 `closed_no_refund` 保持终态。
4. 旧提交已确认无退款并关闭后，消费者在原会话明确继续时，Host 按原待办重新办理，并引用本次继续的消息。消费者在新会话明确指定订单并要求办理时，Host 受理新申请，并引用新会话的授权消息。两条路径都重新核验订单、政策依据和办理授权，并为新提交生成新幂等键。已有退款则直接返回原结果。
