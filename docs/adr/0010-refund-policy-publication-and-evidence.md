# MCP 管理退款政策版本，Host 核验本轮引用

[ADR-0012](0012-refund-policy-for-explanation.md) 沿用本决定的发布目录、版本快照和引用核验，并取消新退款提交前的政策核验事件持久化要求。

2026-11-23，用户确认由权威文档源维护政策原文，通过独立发布流程同步 Milvus，MCP 按 MySQL 政策发布目录确定生效版本，并为一次退款调查固定目录修订号。Host 从本轮检索回执选答并核验出处，避免本地政策副本使正确回执失效，也让政策可以独立于 Host 部署。先移除 Host 的文件依赖，再接入发布目录，随后实现首次模拟退款。持久化最终政策依据仍遵循 [ADR-0009](0009-refund-policy-evidence-before-dispatch.md)，实施与验收见[主设计](../../.scratch/refund-agent-all-tech-demo/spec.md#5-rag-文档与检索流程)。
