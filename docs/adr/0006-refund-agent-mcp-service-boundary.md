# 退款业务工具通过独立 MCP 服务调用

2026-11-19，用户确认集群采用退款 Agent 服务与 MCP 工具服务两个应用，通过 Streamable HTTP 接入订单查询、政策检索、退款结果核对和模拟退款执行；聊天、检查点、摘要和偏好由退款 Agent 直接使用 MySQL，订单与退款业务数据、Milvus 政策检索由 MCP 服务负责。该边界使业务工具独立于会话编排，保留 LangGraph 的持久化职责，代价是维护协议契约、跨服务身份和结果不明时的一致性处理；不再为 MCP Server 增加自主规划模型。模型子 Agent 只参与只读调查，退款执行仍仅由固定业务节点调用，具体契约、权限与恢复约束见[MCP 接入设计](../../.scratch/refund-agent-all-tech-demo/mcp-integration.md)；尚未实施这两个退款应用。
