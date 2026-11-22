# 两模型部署后的接口核查与对接方案

日期：2026-11-18（Asia/Shanghai）  
状态：用户已确认方案 A，Qwen 服务已发布并通过真实工具往返和 Schema 验收。

2026-11-19 用户授权切换后，Qwen 与 BGE 健康，真实工具调用、相同 ID 的观察回传、Schema 输出及旧接口回归通过。验收范围和证据见部署结果（`D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/DEPLOYMENT-RESULT.md`）。

## 1. 改造前的接口核查（历史）

来源为用户提供的部署结果（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/DEPLOYMENT-RESULT.md`）、该目录的 Qwen payload 源码与验收证据。本轮只对两个已知健康 URL 做 GET，均返回 200：Qwen 报告 `Qwen/Qwen3-8B` / AsyncLLM，BGE 报告 `BAAI/bge-m3`。未发送模型推理请求，未重启或修改服务。

模型分工已可按 Qwen3-8B 承担意图、Plan/Replan、DeepAgent/ReAct、政策回答；BGE-M3 承担政策与查询向量化来设计。以下服务能力仍不满足原设计的直接接入方式：

| 核查项 | 当前能力 | 证据与影响 |
| --- | --- | --- |
| Qwen 顶层字段 | 不接受 tools / tool_choice | 请求白名单（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:14`）；DeepAgent 不能直接提交标准工具定义 |
| Qwen 消息 | 消息只接受 role/content，角色为 system/user/assistant | 消息校验（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:23`）；不能回传 role=tool、tool_call_id 或 assistant.tool_calls |
| 结构化输出 | response_format 仅 text / json_object | 格式校验（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:37`）；JSON 对象不等于符合 ConversationDecision / Plan / Replan 的 Schema |
| 工具 parser | OpenAIServingChat 仅显式配置 reasoning_parser=qwen3，未显式启用自动工具及 tool parser | 服务构造（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:135`）；补接口不只是放开字段白名单 |
| JSON 验证 | 要求正常 stop、合法 JSON object、有限数 | 结果校验（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:56`）；没有业务 Schema 约束 |
| thinking | 默认关闭；JSON 与 thinking 同开为 400 | 模式约束（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:47`）；本 Agent 按已建议的关闭 thinking 对接 |
| 模型错误响应 | 请求非法 400，JSON 失败或截断 422，生成超时 504；422 不返半成品成功内容 | 响应处理（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:171`）；失败应作为明确工具/模型失败传递，不推断办理成功 |
| 响应序列化 | 上游响应直接 model_dump 后返回，非固定字段白名单 | 序列化（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/payload/qwen3_serve.py:194`）；样本中的空 tool_calls 不表示支持真实工具往返 |
| BGE 向量 | 实际未归一化，历史与当前样本模长约 26.136 | 实测记录（`D:/Redemption/Redemption/K8S/qwen3-8b-bge-m3-plan-20261118/DEPLOYMENT-RESULT.md:29`）；文档与 query 均需一致处理 |

部署记录证明基础模型调用与部分并发/重启场景已验证，不证明退款业务或原生工具调用已通过。

## 2. 已确认的对接路径及比较方案

### A：扩展现有 Qwen 服务，提供所需原生协议（用户已确认）

范围聚焦现有 `/qwen3/v1/chat/completions` 包装层及其测试，复用已部署模型。设计目标如下：

| 契约 | 需要的具体行为 |
| --- | --- |
| 工具请求 | 接受受支持的 function tools 及 tool_choice；校验工具定义，配置与 Qwen 模板匹配的 parser |
| 工具响应 | 返回有 ID、名称和参数的 message.tool_calls，允许 content 为空；不把 finish_reason=tool_calls 当作 JSON 截断 |
| 观察回传 | 接受合法的 assistant.tool_calls 与 role=tool / tool_call_id，并能在同一工具链继续决策 |
| Schema 输出 | 接受明确的 json_schema 请求，使用当前 vLLM 版本的约束解码，并由客户端具名数据模型复核 |
| 兼容行为 | 保持现有普通 Chat 和 json_object 的输入、结果、错误语义；JSON/Schema 模式继续关闭 thinking |
| 权限 | 服务只产生工具调用请求；订单查询与退款执行仍由客户端按白名单和业务授权调用，模型不获得直接退款写入权限 |

这种方案沿用 DeepAgent / LangChain 已有的工具消息与结构化结果接口，减少应用层自行翻译协议的工作。仍须验证 vLLM 0.10.2、Qwen 模板、工具 parser 和所用客户端版本的组合，不把自动解析等同于参数与业务一定正确。

首轮工具选择覆盖 `auto`、`none` 和指定函数。`auto` 需要启用自动工具选择与 Hermes parser；指定函数必须对应本次 tools 中存在的函数。vLLM 0.10.2 的[工具调用文档](https://docs.vllm.ai/en/v0.10.2/features/tool_calling.html)将 `required` 的相应约束解码支持限定为 V0 + outlines，当前运行的是 V1，因此不把 `required` 列为首轮已承诺能力，也不因该选项切换引擎。客户端不得将 `required` / `any` 静默降为 `auto`；如框架请求该模式，应显式处理兼容性，未验证前明确拒绝。

Schema 约束用于结构、类型、必填项和枚举，不证明所选订单属于本人或金额正确；这些业务约束继续由业务代码校验。服务产生工具调用请求，应用执行工具并回传观察；模型服务本身不代替客户端执行退款。

线上完成判据是一次真实工具请求、带匹配 ID 的观察回传、下一步工具或最终答复，以及一个真实 Schema 输出均通过。现有 Chat / JSON 回归也须继续通过。

### B：保持模型服务不变，在退款应用内适配文本工具协议（未采用）

客户端将工具定义和观察编码进当前支持的 system/user/assistant 文本，要求模型输出受限 JSON；通过校验后转换为框架的工具调用消息，再将实际工具结果转换回文本继续调用。应用内仍使用官方 DeepAgent，模型适配器提供其所需的 bind_tools / 消息接口。

这条路径能保留现有服务，但客户端需自行承担工具消息序列、调用 ID、工具选择约束、JSON Schema 校验和错误转换。服务端 json_object 只能检查 JSON 形式，不能声称具有服务端 Schema 约束解码或原生工具调用。DeepAgent 默认工具也必须全部经过同一权限与校验边界。

用户于 2026-11-18 明确同意 A，B 仅保留为比较记录。该选择已记录为 [ADR-0003](../../docs/adr/0003-refund-agent-native-model-protocol.md)。本地进度与线上验收见[接口实施计划](interface-implementation-plan.md)。

## 3. RAG 的实现建议

继续使用当前 BGE 服务，保留其原始输出。按已确认 MCP 边界，由 MCP 服务内的 RAG 代码及配套文档入库命令在文档与 query 编码两端均执行 L2 归一化，向量写入或提交集群 Milvus，由 Milvus 使用一致的内积配置执行相似度检索；维度错误、非有限值和零模向量作为检索失败处理，按已确认规则停止本次办理。

对 14 条政策和 7 条 FAQ 建立退款政策专用 Milvus collection，不能沿用旧流程语义路由的集合或分数阈值；这是用户对原内存索引设计的明确修订，详见[存储与记忆设计](storage-memory.md)。MCP `search_policy` 返回具来源的候选条款，Host 再形成充分或不足的政策证据；具体分工见[MCP 接入设计](mcp-integration.md)。用已覆盖问题、同义问法和未覆盖问题校准证据充分性及阈值。BGE 当前部署的本地证据不足以证明历史源码与远端代码哈希完全一致，因此只依据本次实际输出记录判断归一化，其他请求限制在对接时再按当前契约验证。

向量归一化建议不新增业务范围、不改变 BGE 模型服务；本轮存储后端修订另记录在[ADR-0004](../../docs/adr/0004-refund-agent-storage-boundaries.md)，记忆术语按根 GLOSSARY.md 使用。

## 4. 本轮文档变更

- `model-selection.md` 以本次部署为最新基线，旧模型快照标为历史，停止将单卡 Qwen / CPU BGE 建议当作现状。
- `spec.md` 记录实际模型已部署、接口仍待对接，以及未归一化向量的处理方案。
- 本文记录用户已确认的 A 及未采用的 B，补充 `required` 的版本边界；本轮未实现、部署、重启或运行退款 Agent。

## Comments

2026-11-18：用户同意方案 A，扩展原生工具往返和 json_schema，保持现有 Chat / JSON 行为。

2026-11-18：用户转述外部说明，已对照现有源码与官方版本文档复核。四项接口未开放的判断成立；将 required 在 V1 下的限制进一步明确，不把权重能力、服务能力与业务校验混为同一件事。

2026-11-19：本地候选已实现。工具观察按调用 ID 匹配后沿调用顺序传给 Qwen，达到生成上限时返回 422。Schema 支持 Draft 2020-12 对象规则和文档内引用，首轮接口拒绝正则。LangChain 的 `max_completion_tokens` 与旧上限共用校验。详见发布候选（`D:/Redemption/Redemption/K8S/qwen3-native-tools-schema-plan-20261118/README.md`）。

2026-11-19：用户授权后完成线上切换与真实验收，Qwen、BGE 均为 RUNNING。具体接口范围和验证证据以本次部署结果为准。
