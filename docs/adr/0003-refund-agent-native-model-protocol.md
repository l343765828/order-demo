# 退款 Agent 使用模型服务的原生工具与 Schema 协议

2026-11-18，用户确认扩展已部署 Qwen3-8B 服务，提供原生 function tools、工具观察回传和 json_schema 输出，保留已有 Chat / json_object 行为。协议集中在模型服务边界，使官方 DeepAgent 与其他调用方共用工具消息契约，也要求服务包装层和兼容性测试同步改造。2026-11-19，接口已上线并通过真实工具往返验收。参见[接口设计](../../.scratch/refund-agent-all-tech-demo/integration-contracts.md)。
