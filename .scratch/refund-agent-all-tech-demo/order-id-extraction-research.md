# 客服对话中的订单号提取

查阅日期为 2026-11-20。

**会。** Google 和 AWS 的公开实现展示了模型从对话收集订单号、把它作为参数交给业务工具，由工具查询订单数据库。订单号也可以通过正则或训练过的实体模型提取。选用哪种方法取决于编号格式和输入方式。以下资料分别来自官方产品文档、参考实现和一则客户访谈，能说明公开方案的行为。各大公司的内部部署和行业采用比例仍缺少可核实的资料。

## 模型如何拿到订单号

- **Google Dialogflow CX** 的 Shopping Assistant 是对话式 Playbook 参考实现。查单示例先询问订单号，用户回复 `ee6b4cc7`，随后 `order-lookup` 工具按该编号查询 Firestore。Playbook 参数文档也明确用订单号举例，说明如何在对话里收集后把它写入输出参数，或将已知订单号作为输入交给工具。Dialogflow CX 另外提供正则实体，用于有固定格式的标识。 [Shopping Assistant](https://docs.cloud.google.com/dialogflow/cx/docs/concept/playbook/prebuilt/shopping-assistant) · [Playbook 参数](https://docs.cloud.google.com/dialogflow/cx/docs/concept/playbook/parameter) · [正则实体](https://docs.cloud.google.com/dialogflow/cx/docs/concept/entity-regexp)
- **AWS Amazon Lex V2** 的练习明确演示从一段自然语言中抽取 `OrderNumber: 12345`。普通识别未解析成功时，可配置 Bedrock 大模型做第二次解析。字母数字槽位的这个回退功能适用于未启用正则的配置，示例包括口述交易编号和预订确认码。配置正则的同类槽位会检查格式，失败时提示重输。 [客服机器人练习](https://docs.aws.amazon.com/lexv2/latest/dg/exercise-3.html#generative-slot-resolution) · [大模型辅助解析](https://docs.aws.amazon.com/lexv2/latest/dg/assisted-slot.html) · [辅助解析示例](https://docs.aws.amazon.com/lexv2/latest/dg/assisted-slot-examples.html) · [字母数字槽位与正则](https://docs.aws.amazon.com/lexv2/latest/dg/built-in-slot-alphanumeric.html)
- **Microsoft CLU 与 Copilot Studio** 支持用标注话语训练模型，按上下文定位实体，也支持用正则提取满足固定格式的文本。CLU 可组合多个组件并指定必需组件。微软用票数演示了按上下文区分一句话里的多个数字，在 Copilot Studio 文档中则直接将运单追踪 ID 列为正则实体的例子。 [CLU 实体组件](https://learn.microsoft.com/en-us/azure/ai-services/language-service/conversational-language-understanding/concepts/entity-components) · [Copilot Studio 实体与槽位](https://learn.microsoft.com/en-us/power-virtual-agents/advanced-entities-slot-filling#regular-expression-regex-entities)
- **阿里云**百炼给通义晓蜜对话分析模型的示例提示词提供一段客服对话，要求抽取其中的订单号并写入 JSON。这展示的是对已有对话文本的分析。伶鹊另有多轮收集运单号的示例，机器人将已收集的部分运单号发送给业务方实现的接口，由接口返回完整性、长度和格式检查结果。 [通义晓蜜对话分析](https://help.aliyun.com/zh/model-studio/dialogue-analysis) · [伶鹊单号校验 API](https://help.aliyun.com/zh/ccs/beebot/user-guide/interactive-parameter-collection-order-number-validation-api)
- **已披露的客服实践**。零售集团 CSC Generation 的技术负责人在 AWS 官方访谈中举例，用生成式 AI 判断口述的第一串还是第二串数字才是订单号，并替换真实订单号等信息，只向所述模型提供句子结构。访谈披露了对话理解的做法，完整的查单实现仍未公开。 [CSC Generation 访谈](https://aws.amazon.com/blogs/contact-center/csc-generation-transforms-customer-support-with-amazon-connect/)

## 后端如何查单

- **AWS 的 Claude 与 LangGraph 参考实现**由模型产生 `get_order_by_id` 调用，工具契约要求字符串类型的 `order_id`。应用执行工具并查询 Amazon RDS for PostgreSQL，再把结果返回模型。AWS 还公开了另一种短信查单样例，用 Lex 的字母数字槽位收集订单号，再由履约 Lambda 查 DynamoDB。 [Claude 与 LangGraph 参考实现](https://aws.amazon.com/blogs/machine-learning/build-a-serverless-conversational-ai-agent-using-claude-with-langgraph-and-managed-mlflow-on-amazon-sagemaker-ai/) · [Lex 短信查单样例](https://aws.amazon.com/blogs/messaging-and-targeting/build-ai-and-ml-into-email-sms-for-customer-engagement/)
- **Salesforce Agentforce** 的 `GetOrderByOrderNumber` 是 Flow 动作，按联系人 ID 和订单号返回查询结果。官方身份配置示例先从已认证会话获取联系人 ID，再同时按联系人和订单号查询 `Order`。该文档说明查询与授权范围，订单号如何从对话中提取取决于对话配置。 [按订单号查单动作](https://help.salesforce.com/s/articleView?id=ai.copilot_actions_ref_get_order_by_order_number.htm&language=en_US&type=5) · [Agentforce 动作身份校验](https://help.salesforce.com/s/articleView?id=service.service_agentforce_implement_agents.htm&language=en_US&type=5)

## 对退款演示的参考

本项目的常规路径已经让 Qwen 根据语境填写 `ConversationDecision.order_id`。正则同时从本轮原文找出候选，进入订单咨询分支后核对是否只有一个候选、模型编号是否与它一致。同一会话追问后只输入完整订单号时，意图节点可直接沿用资格咨询。最终 `get_order` 按已认证的消费者身份和订单号查询业务数据。 [意图节点](../../order_flow.py#L56) · [订单查询](../../mcp_service/orders/query.py#L7) · [MCP 服务边界](../../docs/adr/0006-refund-agent-mcp-service-boundary.md)
