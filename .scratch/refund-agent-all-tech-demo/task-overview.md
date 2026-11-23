# order-demo 首版施工任务总览

本次交付任务总览和本地工单。后续工单负责代码、数据库与政策入库操作、环境验证及集群发布。

## 施工顺序

先建 `order_demo` 专用库、共享表定义和种子数据是合理的，两个进程可以尽早用同一份订单事实进行验收。checkpoint 及模型／MCP 兼容性适合与建库并行验证，以便在接入业务前发现版本和重发行为的阻塞。公共配置与契约随第一个实际使用它们的切片交付。模型客户端随协议验证及查单落地，会话记忆沿查单、多轮授权、偏好和压缩逐步扩展，业务模块按消费者可观察的行为交付。

**第一条真实业务路径在 04。** 消费者在 CLI 输入“查一下 DEMO-1001”，Host 载入并保存会话，经外层 LangGraph 计划和执行、DeepAgent 委派及 ReAct 调用独立的 Streamable HTTP MCP Server，Server 从真实 MySQL 读回本人订单。实付金额为 19900 分，进程、工具与会话均可追溯，全程退款写调用数为零。这条路径验证 01、02、03 可以合用；BGE、Milvus 和重排服务可随 05 并行接入。

**当前续做顺序为 15 → 16 → 07。** 05 和 06 已形成政策检索、咨询和资格说明的验收基线。15 先让 Host 仅凭本轮 MCP 回执完成政策咨询和引用核验，16 再接入发布目录与多文档版本切换。07 使用这套已验收的版本机制接通首次模拟退款。

**第一条完整退款路径在 07，08 补齐多轮办理。** 07 从“如果能退，就帮我退 DEMO-1001”开始，真实查单后从召回阶段仅检索 RF，经正式政策证据核验，严格按政策事件持久化 → 提交持久化 → 受控 MCP 写入的顺序产生一笔模拟退款。08 再用“我要退款 → DEMO-1001”及取消、换单、插入咨询检验多轮授权。

| 工单 | 直接阻塞 | 独立验收结果 |
| --- | --- | --- |
| [01 专用库、共享定义与种子](issues/01-mysql-schema-and-seed.md) | 无 | 两服务连接同一专用库，实际 MySQL 约束防止重复记录 |
| [02 MySQL checkpoint 兼容性](issues/02-verify-mysql-checkpointer.md) | 无 | 实际版本下跨进程恢复、线程隔离及按线程清理 |
| [03 模型与 MCP 协议](issues/03-verify-model-and-mcp-protocols.md) | 无 | 真实原生工具、Schema 往返及受控 MCP 调用不重发 |
| [04 本人查单](issues/04-read-own-order-end-to-end.md) | 01、02、03 | 正常 CLI 经真实 Qwen、MCP、MySQL 查本人订单 |
| [05 政策发布与检索](issues/05-publish-and-search-refund-policy.md) | 03 | 独立入库后经真实 MCP 返回已重排的当前版候选 |
| [06 政策和资格咨询](issues/06-answer-policy-and-order-eligibility.md) | 04、05 | 带正式出处的咨询和真实订单资格解释，全程只读 |
| [15 移除 Host 的政策文件依赖](issues/15-remove-host-policy-file-dependency.md) | 05、06 | 无本地政策 Markdown 时完成 FAQ 与正式条款共同选答、必要的语义补查与引用核验 |
| [16 政策发布目录与多文档版本切换](issues/16-publish-policy-catalog-revisions.md) | 15 | 可独立发布、重试同版本和切换生效版本，旧调查沿用版本快照 |
| [07 首次模拟退款](issues/07-submit-one-mock-refund.md) | 16 | 明确授权后只写一次，政策版本、读取事件、提交和实际退款可追溯 |
| [08 多轮授权](issues/08-manage-multiturn-refund-authorization.md) | 07 | 补单、取消、换单和继续只依据当前会话授权 |
| [09 重复与回执核对](issues/09-reconcile-duplicate-and-lost-receipts.md) | 07 | 并发无重复退款，回执丢失和重启只读核对 |
| [10 维护关闭与恢复办理](issues/10-close-dispatch-and-resume-refund.md) | 08、09 | 安全关闭旧提交并封住迟到请求，两条重新办理路径均重新核验 |
| [11 显式长期偏好](issues/11-manage-explicit-preferences.md) | 04 | 从聊天管理当前偏好，重启不恢复已删除的值 |
| [12 长会话压缩](issues/12-compress-long-conversations.md) | 08、11 | 预算内保留必要上下文，超窗失败不写入 |
| [13 演示数据重置](issues/13-reset-only-demo-data.md) | 10、12 | 停流后重置会话及模拟业务数据，保留政策发布目录和索引 |
| [14 两服务部署与联合验收](issues/14-deploy-and-verify-two-services.md) | 13 | 在目标环境运行首版及关键故障回归 |

## 并行与汇合点

- **开工前沿为 01、02、03。** 先开建库票，同时验证 checkpointer 与协议。02、03 各自收敛可用依赖，合入 04 前对齐两者的 LangGraph／DeepAgent 版本。
- **03 完成后可做 05。** 政策发布与检索可独立验收，04 继续取决于实际 MySQL 和 checkpoint。04 完成后可开 11，04 和 05 汇合后开 06。
- **05、06 验收后做 15，再做 16。** 15 验收 Host 和 MCP 的新读取契约，16 验收用该契约读取新旧版本，随后开始 07。
- **07 完成后并行做 08、09。** 08 负责当前会话意图和撤销，09 负责跨会话提交状态和回执核对。两者汇合后做 10。08 和 11 完成后，12 可与 09 或 10 的剩余工作并行。10、12 汇合后做 13，最后交给 14 联验。

## 外部前提与实际卡点

| 卡点 | 确认方式及影响 |
| --- | --- |
| 目标 MySQL 与 checkpointer 的版本差 | 本地记录为 **MySQL 8.0.17**，低于候选适配器记录的 **8.0.19**。02 查实际版本。若仍为 8.0.17，须找到兼容的 MySQL 实现，并通过真实保存、重启恢复和线程清理才解除 04 的依赖。 |
| `order_demo` 与受控验收环境 | 01 要有建库建表、种子装载和读写权限，02 需要获准的隔离验收空间。文档已有目标端点记录，历史上工作机到多个服务曾超时或连接失败。须从后续实际运行位置确认路由、凭据与权限，历史结果不足以判断当前可达性。 |
| 应用 SDK 与受控写入 | Qwen 原生工具和 Schema 有基础上线验收记录。03 仍须使用所锁 LangChain／DeepAgent／MCP 组合实测观察回传、结构化结果与自动重发行为。依赖组合有缺口时，04 不能完成接入；无法限制自动重发时，07 不能验收写入。 |
| 检索三段链路 | 05 需要可用的 BGE-M3、Milvus 和 BGE reranker，外加专用集合的创建／入库权限。缺一会阻塞政策链的真实验收，04 的纯查单仍可独立交付。 |
| 政策核验事件可确认持久化 | 07 需在验收环境证明结构化事件存储会确认持久化，并能按 `trace_id` 和幂等键回查。仅调用日志接口而无法确认落盘时，新退款写入暂停。 |
| 发布窗口与重置操作权 | 14 需要目标环境发布权限、服务凭据和演示数据操作窗口；13 需要可确认的跨进程停流与写入排空。未确认排空时，维护入口须停止重置。 |

## 定稿依据

业务语义采用术语表、当前专题定稿和 ADR。七张原应用表负责会话及模拟业务数据，16 另行交付政策发布目录。正常重启保留数据，重排失败时政策检索失败，旧提交关闭后由原会话或新会话明确发起恢复办理。

- [术语表](../../GLOSSARY.md) · [主设计](spec.md) · [数据库设计](database-design.md) · [存储与记忆](storage-memory.md) · [MCP 接入](mcp-integration.md) · [上下文压缩](context-compression.md) · [退款政策](../../policy/refund-policy.md) · [模型选型](model-selection.md) · [接口契约](integration-contracts.md)
- [ADR-0003 原生模型协议](../../docs/adr/0003-refund-agent-native-model-protocol.md) · [ADR-0004 存储](../../docs/adr/0004-refund-agent-storage-boundaries.md) · [ADR-0005 压缩](../../docs/adr/0005-refund-conversation-compression-owner.md) · [ADR-0006 MCP 边界](../../docs/adr/0006-refund-agent-mcp-service-boundary.md) · [ADR-0007 未决提交](../../docs/adr/0007-refund-unconfirmed-dispatch.md) · [ADR-0008 办理授权](../../docs/adr/0008-refund-authorization-per-dispatch.md) · [ADR-0009 政策持久证据](../../docs/adr/0009-refund-policy-evidence-before-dispatch.md)
- [ADR-0010 政策版本与本轮引用](../../docs/adr/0010-refund-policy-publication-and-evidence.md)
