# 退款业务 Agent 模型选型建议

日期：2026-11-18  
性质：模型与原生工具/Schema 接口已部署，退款 Agent 实施与业务验收待完成。  
关联：[业务设计](spec.md)、[政策知识源](../../policy/refund-policy.md)

## 0. 最新部署基线：取代下文部署前的资源假设

用户已提供模型部署结果（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/DEPLOYMENT-RESULT.md`）。2026-11-19 原生工具和 Schema 接口已完成上线及真实验收（`D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/DEPLOYMENT-RESULT.md`）。以下模型资源和旧接口行以原部署记录为基线，实际接口能力以上线验收为准。

| 项目 | 已部署状态 |
| --- | --- |
| 聊天模型 | `Qwen/Qwen3-8B`，BF16，vLLM V1 AsyncLLM，TP=2、PP=1；跨 node4/node5，一个副本 |
| 向量模型 | `BAAI/bge-m3`，FP16 GPU 稠密向量服务，TP=2、PP=1；跨 node4/node5，一个副本 |
| 重排模型 | `BAAI/bge-reranker-v2-m3`，FP16，TP=2、PP=1；跨 node4/node5，一个副本 |
| GPU 配额 | 三模型记录中每节点 Qwen、BGE-M3、reranker 分别预留 0.50、0.25、0.25 GPU。 |
| Qwen 入口 | `http://192.168.18.53:30080/qwen3/v1/chat/completions`，模型名 `Qwen/Qwen3-8B`，已完成原生工具和 Schema 基础验收 |
| BGE 入口 | `http://192.168.18.53:30080/bge/v1/embeddings`，模型名 `BAAI/bge-m3` |
| 重排入口 | `http://192.168.18.53:30080/reranker/v1/rerank`，模型名 `BAAI/bge-reranker-v2-m3` |
| 服务边界 | Qwen 8192 总上下文、单并发序列、默认关闭 thinking；支持非流式 Chat、json_object、json_schema 与原生工具子集 |
| 向量语义 | BGE 原始向量未归一化；RAG 应在客户端对文档和查询使用一致的归一化/相似度方案 |
| 旧应用 | 原 vllm-app 和 qwen3-4b-instruct-2507 已停止，模型和配置未删除；不作为在线回退服务 |
| 已完成验证 | 三模型部署记录报告三轮短请求及一轮长输入并发通过，长输入轮重排正确文档排第一；本地测试 51 项及 15 项子测试。 |
| 仍未证明 | 完整退款 Agent、长期稳定性、严格队列饱和及全部 8192 输入加输出场景 |

Qwen revision 为 `b968826d9c46dd6066d109eabc6255188de91218`。旧包装层的接口限制记录在[历史接口核查](integration-contracts.md)。新包装层已通过真实工具调用、同 ID 观察回传和 Schema 基础验收；业务节点仍按第4节分工。

## 1. 早期推荐与选择理由

选型采用 `Qwen/Qwen3-8B` BF16，生成与工具决策节点共用这一个模型服务。不同节点保留独立的提示词、结构化输出 Schema 和工具权限，不要求为每种技术加载一份模型。早期的 node4 单卡建议不再作为实际部署布局，实际 TP2 布局以第0节为准。

`BAAI/bge-m3` 单独负责政策条款和查询的向量化。当前只处理 14 条政策、7 条 FAQ，使用第0节中已经部署的 GPU dense embedding 服务；不再按先前 CPU 进程建议安排对接。

这是一项基于功能、部署资源和接口适配复杂度的工程选择，不是“8B 已证明比原有 4B 更稳”的实测结论。当前先验收已部署的 8B 与 BGE 组合，不要求恢复已停用的 4B 服务。`Qwen/Qwen3-14B-FP8` 保留为进阶候选，仅在现有组合出现明确缺口时再评估。

最初考虑 14B-FP8 为主候选；补查发现当前 worker 使用 vLLM 0.10.2 后，按稳定性优先调整为 8B BF16 起步。这减少量化内核这个适配变量，不等于宣称 BF16 在本业务上已取得更高正确率。

## 2. 已确认的选型约束

- 只接受自托管模型，不使用云端模型 API。
- 优先中文多轮、工具调用和结构化输出稳定性，允许较慢响应。
- 运行退款演示时可以重新分配已有资源，旧演示应用可按需停用；用户提供的部署结果已记录旧模型应用停止。本轮文档同步没有修改服务。
- 五项技术全部使用；退款身份、资格、金额、幂等与真实结果由业务代码处理。
- 退款执行前必须具备充分政策依据；业务成功后的结果使用模板呈现。

## 3. 部署前历史环境快照（已被第0节替代）

以下为部署前 2026-11-18 21:58–22:00（Asia/Shanghai）的只读 metadata 与 GPU 快照，仅保留选型时的历史证据，不代表新部署完成后的应用、剩余资源和 GPU 占用。查询未发送模型推理请求，未修改集群。动态快照由本轮环境核查子任务采集，原始响应未落盘。

| 项目 | 当前核查结果 | 对选型的影响 |
| --- | --- | --- |
| 运维平台 | 远程 Linux GPU 节点，K8s、KubeRay、Ray Serve、vLLM；K8s readyz 正常 | 推理服务放远程 Linux GPU 节点，CLI 作为客户端 |
| node4 worker 版本 | Ray 2.50.1、vLLM 0.10.2、PyTorch 2.8.0+cu128、CUDA runtime 包 12.8.90、Transformers 4.55.2；基础镜像 latest-py311-cu128，实测 imageID 以 a20a2492365b 开头 | 来自包 metadata，不加载模型；按 0.10.2 文档核对接口，不将最新版特性视作现状；部署时固定 digest |
| node4 | RTX 5090，32607 MiB 总显存，21408 MiB 空闲 | 主候选的单卡目标；已有应用占用尚未释放 |
| node5 | RTX 5080，16303 MiB 总显存，5868 MiB 空闲 | 不能按“16GB 全空闲”直接新增模型 |
| Ray GPU 资源 | 现有每卡 PG 预留 0.75 GPU，PG 外每卡仅余 0.25；两卡合计余 0.50 逻辑 GPU | 物理空闲显存与可调度 GPU 份额是两类约束；新完整单卡服务需重排 |
| 运行应用 | `vllm-app`、`qwen3-4b-instruct-2507` 均 RUNNING/HEALTHY | 健康状态只证明服务就绪，未证明本退款流程通过 |
| 现有 Qwen3 | `Qwen/Qwen3-4B-Instruct-2507`，BF16，TP2/PP1，8192 服务上下文，单序列 | 可以复用权重作基线；不应照搬跨两卡配置到新单卡服务 |
| 现有 Qwen3 协议 | 仅文本 Chat + `json_object`；未开放 tools、tool_choice、tool 角色、json_schema 和 streaming | 原接口不能直接作为标准 DeepAgent/native tools 与 Schema 输出入口 |
| 旧 DeepSeek 演示 | 配置名为 DeepSeek-R1-Distill-Qwen-1.5B，当前 TP1/PP2，纯 prompt 接口；未独立核验权重发行版哈希 | 不作为本方案默认 Planner 或工具执行模型 |
| BGE | 存在 BGE-M3 模型资产、配置和历史调用，但当前 Ray 应用无 BGE 服务 | 属于可部署资产，不能宣称当前已可复用成功 |
| Reranker | 有历史 BGE reranker 部署，当前未运行 | 本轮不新增重排模型，先验收 dense 检索 |

32GB 与 16GB 是不同节点上的两张显卡，不能视作单张 48GB 卡。跨节点 TP/PP 会引入通信与不对称资源约束；单卡布局只是早期选型建议，当前采用第0节已部署并验证基础调用的 TP2 布局。

可复核的本地证据：

- 现有部署记录（`D:/Redemption/Redemption/K8S/qwen3-4b-2507-plan-20261116/DEPLOYMENT-RESULT.txt:27`）：Qwen 模型与服务参数、共存和停用记录；历史文件与本轮快照分开解释。
- Qwen 服务字段白名单（`D:/Redemption/Redemption/K8S/qwen3-4b-2507-plan-20261116/payload/qwen3_serve.py:14`）：实际入口能力限制。
- 旧演示模型配置（`D:/Agent/Agent/langgraph/models.json:10`）：纯 prompt 调用协议。
- 现有 embedding 配置（`D:/Agent/Agent/mcp/workflow/json/config.json:2`）：BGE 资产与接口配置，不代表当前服务在线。

## 4. 节点与模型映射

下表为退款 Agent 的目标节点分工。所有 Qwen 生成节点调用已部署的同一份 Qwen3-8B BF16 自托管服务，按节点切换约束；子 Agent 可以隔离上下文而共享模型权重。原生工具和 Schema 基础协议已上线，业务节点尚需实现和验收。

| 业务节点 | 推荐模型或处理器 | 调用方式与输出 |
| --- | --- | --- |
| 理解意图、关联多轮、提取订单号 | Qwen3-8B BF16 | 无业务工具的 Schema 输出：`ConversationDecision` |
| Plan：生成调查计划 | Qwen3-8B BF16 | Schema 输出：`InvestigationPlan`，仅允许 inspect_order / retrieve_policy |
| Observe / Replan | 代码校验 + Qwen3-8B BF16 | 先核对工具事实，再输出 `ReplanDecision` |
| DeepAgent 调查执行器 | Qwen3-8B BF16 | Native tool calling，仅允许向两个专用子 Agent 委派当前任务 |
| OrderAgent / ReAct | Qwen3-8B BF16 | Native tool calling，只开放经 MCP 的 `get_order` 只读适配器，回传匹配的 tool observation |
| PolicyAgent / ReAct、检索 query 改写 | Qwen3-8B BF16 | Native tool calling，只开放经 MCP 的 `search_policy` 只读适配器，按预算最多一次 query 补查 |
| RAG 政策文档入库与查询编码 | BAAI/bge-m3 | MCP 服务端及文档入库命令复用同模型、同配置编码文档与 query，生成 1024 维向量 |
| RAG 检索与重排 | Milvus 与 `BAAI/bge-reranker-v2-m3` | Milvus 召回最多 8 条，重排后取前 3 条，返回来源和原文；Host 再核验证据。 |
| 政策证据充分性判断、问答与写入前说明 | Qwen3-8B BF16 + 代码核验 | 基于检索条款组织回答，引用 ID/原文与版本校验；无依据停止 |
| 重新查单、资格与金额判定、模拟退款 | 固定 Host 节点经 MCP 调用业务代码 | 无模型决定身份、金额或最终资格；MCP 服务端 MySQL 事务复核与幂等写入 |
| 退款成功、已退款或拒绝的最终业务结果 | 确定性模板 | 读取实际 `RefundResult`，可附写入前已核验的政策说明；写入后不再模型改写 |

保留一个共享聊天模型有利于先定位流程、权限和协议问题。将多个模型分配给不同节点不是使用五项技术的必要条件；后续只有在本业务对照证明有收益时，再将轻量意图节点或查询改写迁移到 4B。

## 5. 候选排序与备选

| 候选 | 推荐定位 | 资源与取舍 |
| --- | --- | --- |
| `Qwen/Qwen3-8B`，BF16 | 已部署主聊天模型 | 按第0节 TP2 服务对接，关闭 thinking；尚未完成本退款 Agent 的业务验收 |
| `Qwen/Qwen3-14B-FP8` | 进阶对照候选 | 官方 FP8 权重，目标同为 node4；本任务关闭 thinking。需先验证 RTX50、CUDA、PyTorch、vLLM 的 FP8 block kernel 组合，再比较业务效果 |
| `Qwen/Qwen3-4B-Instruct-2507`，BF16 | 停用的历史基线 | 本身仅 non-thinking，模型文件保留；不自动恢复或切换到旧服务 |
| `BAAI/bge-m3` | 已部署 embedding 模型 | 使用现有 GPU 服务，客户端统一处理未归一化向量；尚未完成政策检索验收 |

本方案不把需要更多显存的 32B BF16、30B BF16 模型或跨节点混合显存作为默认方案。也不选择小型纯推理模型承担主 Agent：当前任务更需要短指令、工具循环与受限结果输出，增加显式长推理未证明能提高成功率。

14B-FP8 的完整显存峰值和本业务效果仍未知；参数量不等于质量结论，模型权重大小也不等于完整服务显存。

## 6. 推理服务准入条件

模型、服务入口和客户端三者必须匹配。现有 Qwen 服务已支持原生工具与 Schema 协议，并通过 Chat / json_object 基础回归。接口边界与验收状态见[接口核查](integration-contracts.md)。

1. **原生工具往返。** 入口接受 tools、tool_choice 与 role=tool，模型调用返回真实 `tool_calls` 和调用 ID，框架按 ID 回填观察。Qwen 官方给出的 Hermes 模板与解析器可作为依据；具体启用方式按所锁定版本核对。
2. **Schema 输出。** 意图、计划和重规划使用服务支持的 `response_format.type=json_schema`，再做 Pydantic 与业务校验；不能仅用 `json_object` 冒充字段约束。vLLM 0.10.2 官方文档已展示该 response_format，当前自定义 HTTP 包装层没有透传。0.10.2 文档中的 guided_json 与较新版本的 structured_outputs.json 按实际版本使用。
3. **工具参数校验。** 自动工具调用解析成功，不等于工具参数都符合 Schema。仅在锁定版本与 parser 确实支持时使用 strict；应用端始终验证入参、枚举、权限和业务事实。
4. **统一关闭显式 thinking。** 14B/8B 使用 `chat_template_kwargs.enable_thinking=false` 硬开关；4B-Instruct-2507 本身不提供 thinking。此建议旨在减少 reasoning 字段与格式边界适配，规划和 ReAct 工具循环仍存在，不等于将 Agent 变成固定回答。
5. **版本匹配。** 固定镜像、模型 revision、CUDA/PyTorch/vLLM 与模板版本。旧文章的 guided_json、reasoning parser 和新文档参数不能混用；当前服务健康不代表具备最新功能。0.10.2 的 FP8 页面没有给出 Qwen3-14B block FP8 与 RTX5090 组合的可用保证，因此不把它当作已验证默认；若另选较新镜像，需对其实际版本重新验收。

当前服务上下文为 8192、单并发，生成上限按节点分别限制；上下文包含框架提示词、工具定义、同会话必要历史、状态和检索证据，需测量 token 占用并预留输出预算。原始长历史完整归档到 MySQL，模型当次输入与原文存储分别管理；上下文压缩方案见[已确认设计](context-compression.md)，窗口不足时不能静默丢弃当前订单和授权证据。

`gpu_memory_utilization` 是单个推理实例相对整张 GPU 的预算参数，不是“剩余显存百分比”。第3节的部署前预算不再用于已运行的 TP2 布局；本轮对接设计沿用用户提供的部署结果，不要求恢复旧单卡布局，也未改变现有资源配置。

## 7. 退款 Agent 对接仍需完成的验证

两个模型的基础部署和新接口真实调用验证已完成；退款 Agent 尚未实现，业务实施阶段至少验证：

- 协议：一次原生工具调用、工具结果回传后的下一步、Schema 输出、无效参数被拒绝、工具调用预算生效。
- 中文多轮：补订单号、插入政策问题后继续、取消后补单不复活旧授权、更换订单清除旧证据。
- 退款：本人可退、非本人、未支付、已发货、已退款、同订单跨会话重复申请。
- 政策：条款引用正确、未知问题明确无依据、检索故障不执行退款。
- 容量：8K 上下文及包含 DeepAgent 提示词的真实请求无 OOM；记录峰值显存、单轮耗时和各节点调用次数。

协议与业务硬约束有任一失败，不能仅凭模型“回答看起来正确”接受。先验收已经部署的 8B 与 BGE 组合，只有出现明确质量或容量缺口时才评估其他候选。切换候选需要显式配置，不自动回退，不静默换模型。

## 8. 文档与决定

用户已决定自托管、稳定性优先与可重新分配资源，并已提供 Qwen3-8B BF16 与 BGE-M3 GPU 服务的部署结果；8K 单序列和默认关闭 thinking 已写入部署。接口方式已确定为方案 A：扩展原生工具与 Schema 协议。14B-FP8 只是进阶候选，不重新选择模型或恢复旧布局。

型号与运行参数记录在本功能选型文档，不新建型号选择 ADR。原生工具/Schema 与客户端文本适配之间的边界选择影响后续 SDK 和客户端契约，另记录为 [ADR-0003](../../docs/adr/0003-refund-agent-native-model-protocol.md)。根 GLOSSARY 不存放模型名、地址或部署参数。

## 9. 官方来源

- [Qwen3-14B-FP8 官方模型卡](https://huggingface.co/Qwen/Qwen3-14B-FP8)：官方 FP8 checkpoint、模式硬开关与部署说明。
- [Qwen3-8B 官方模型卡](https://huggingface.co/Qwen/Qwen3-8B)：BF16 对照候选。
- [Qwen3-4B-Instruct-2507 官方模型卡](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)：已有权重、non-thinking 和自托管用法。
- [Qwen Function Calling](https://qwen.readthedocs.io/en/stable/framework/function_call.html)：Hermes 工具模板、工具调用与观察回传。
- [vLLM 0.10.2 Tool Calling](https://docs.vllm.ai/en/v0.10.2/features/tool_calling.html)：与当前版本对应的工具解析接口。
- [vLLM 0.10.2 Structured Outputs](https://docs.vllm.ai/en/v0.10.2/features/structured_outputs.html)：与当前版本对应的 Schema 约束解码与输出格式。
- [vLLM 0.10.2 FP8](https://docs.vllm.ai/en/v0.10.2/features/quantization/fp8.html)：当前版本 FP8 支持范围，不能替代目标组合实测。
- [vLLM 最新 Tool Calling](https://docs.vllm.ai/en/stable/features/tool_calling/)：仅用于比较新版能力，strict 等特性不默认视作 0.10.2 已支持。
- [vLLM Engine Arguments](https://docs.vllm.ai/en/stable/configuration/engine_args/)：显存预算与执行参数。
- [BGE-M3 官方模型卡](https://huggingface.co/BAAI/bge-m3)：多语言 embedding、1024 维向量与使用方法。
