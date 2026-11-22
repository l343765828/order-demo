# 03: 验证原生模型协议和跨进程 MCP 的兼容组合

**Status:** ready-for-agent

**Blocked by:** 无，可立即开工。

## 目标

确认选定客户端能调用已部署 Qwen 原生接口，且 Host 能对独立 MCP 进程进行 Schema 校验和单次受控发送。

## 范围

交付可复跑的兼容性探针、最小客户端接线和经实测的依赖组合。探针使用无业务副作用的工具，实际身份与订单权限由 04、07 交付。与 02 对齐最终 LangGraph／DeepAgent 版本。

## 涉及的契约

- Qwen3-8B BF16 的原生 `tool_calls`、匹配 `tool_call_id` 的观察回传和服务端 `json_schema`，调用端再做具名 Schema 校验。关闭 thinking。覆盖当前支持的 `auto`、`none` 与指定函数；未验证的 `required`／`any` 明确拒绝或显式处理兼容性，不静默降级。
- 官方 DeepAgent 的子任务委派与 ReAct 工具往返、MCP Streamable HTTP 初始化与发现、具名工具 Schema、结构化结果和 Client 生命周期。 [接口核查](../integration-contracts.md) · [模型部署基线](../model-selection.md) · [MCP 接入](../mcp-integration.md) · [ADR-0003](../../../docs/adr/0003-refund-agent-native-model-protocol.md) · [ADR-0006](../../../docs/adr/0006-refund-agent-mcp-service-boundary.md)

## 验收标准

- [x] 在锁定的应用客户端与框架组合上，通过官方 DeepAgent 发起真实 Qwen 子任务工具请求，按真实调用 ID 回传工具观察，再收到下一步或最终结果；另用 `json_schema` 获得合法具名对象。额外字段、非法参数或无效结果能被明确拦截。
- [x] 独立 MCP 进程通过 Streamable HTTP 完成发现、Schema 核对、工具调用及结构化结果回传。保存模型调用 ID、MCP 请求标识和应用 `trace_id` 的关联记录，并验证退出时关闭 Client。
- [x] 在无副作用探针模拟响应丢失、断连、协议头异常和 SDK 自动追加回合。受控调用一次意图只发出一次工具调用，底层与重连路径均不重发。记录选择的调用接口和限制。

## 验证方式

分别启动探针 Host 和 MCP Server，调用现有 Qwen 入口，保存去除凭据的请求／观察、两进程标识及请求计数。注入坏 Schema、丢响应和协议故障，核对服务端实际收包次数；仅得到健康响应无法满足本票。

## Comments

2026-11-19 部分实施与实测：以 02 的 LangGraph 1.2.22、DeepAgent 0.7.38 为候选，隔离环境加入 LangChain OpenAI 1.8.0 与 MCP 2.3.0，`pip check` 无冲突。实际 Qwen 健康和普通 Chat 为 200；`python order-demo/test/verify_qwen_endpoint.py` 对 tools、tool_choice、max_completion_tokens 分别得到 400 不支持字段，对 json_schema 得到 400 仅支持 text/json_object。`python order-demo/test/verify_qwen_protocol.py` 的 LangChain 原生工具请求也得到 400，说明当前实际入口不能按定稿完成模型协议验收；部署事实需由服务环境恢复或定位正确入口后重新验证。未改动模型集群。

独立进程 `python order-demo/test/verify_mcp_protocol.py` 完成 Streamable HTTP 初始化、发现、输入 Schema 字段检查、底层 `session.call_tool` 结构化往返与 Client 上下文退出，Host、Server PID 和 trace 见 `order-demo/evidence/protocol-03.md`。这仅是正常路径，缺模型调用 ID 和 MCP 请求 ID 关联；丢响应、断连、异常头、自动回合与不重发尚未验证。四个探针 mypy、编译检查通过；全仓库 pytest 收集阶段 7 项错误，原因见证据文件。验收结论：三项均未完全通过，保持现有 `ready-for-agent`，不勾选。解除条件是可用的原生 Qwen 入口及完成上述关联、故障注入与受控发送验证；当前探针不能用于业务写入。

2026-11-19 Qwen 修复后续测：入口字段复测 200，真实 Qwen 工具调用及匹配 ID 观察回传、具名 Schema 输出通过。LangChain DeepAgent 子任务 assistant 消息含 name 导致 400；客户端接线移除可选 name 后真实子任务通过 MCP 工具并返回最终结果，模型调用 ID、MCP 请求 ID、trace ID 已关联记录。独立 MCP 超时探针记录服务端仅执行一次且重新连接不自动重发。细节与命令见 `order-demo/evidence/protocol-03.md`。第一项的非法参数、额外字段和无效结果拦截尚未完整验收；第二项完整 Schema 比对及生命周期边界尚待加强；第三项协议头异常、其他断连时序和自动追加回合尚未完成，三项暂不勾选。状态保留 `ready-for-agent`。

2026-11-19 完整复测。Python 3.11.7，LangGraph 1.2.22，DeepAgent 0.7.38，LangChain OpenAI 1.8.0，MCP 2.3.0。运行 `verify_qwen_protocol.py`、`verify_deepagent_protocol.py`、`verify_mcp_protocol.py` 以及 `pytest order-demo/test/test_mcp_protocol.py order-demo/test/test_qwen_client.py -q -s`。真实 Qwen 工具观察与原调用 ID 匹配，最终回复包含真实 MCP 结果，独立 `json_schema` 请求返回合法具名对象。完整输入、输出 Schema 不一致均在发现阶段拦截，非法参数未产生工具 POST，无效结构化结果被拒绝，Client 退出后会话不可用。

受控接口在超时、响应丢失、断连、协议头异常、补充输入、同源 307 跳转场景各产生 1 个工具 POST。断连后通过 1 个带 `Last-Event-ID` 的 GET 恢复原响应。协议头异常、补充输入和跟随跳转的 SDK 默认行为对照均产生 2 个工具 POST。复跑命令、真实调用 ID、进程 ID 和计数见 [完整验收记录](../../../evidence/protocol-03.md)。本票 9 项 pytest 通过，全仓库 pytest 仍有 7 项既有收集错误。三项验收标准均通过。仓库未配置完成状态，`Status` 保留 `ready-for-agent`。
