"""查单流程使用的结构化数据模型。"""

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, WithJsonSchema, model_validator

from model.order_query import OrderQueryResult, StrictModel
from model.policy import PolicySearchResult
from model.policy_evidence import PolicyEvidence
from model.status import ConversationIntent, InvestigationKind, ObservationStatus, OrderGoal, ReplanAction, status_schema


# 保存模型识别出的消费者意图及当前消息中的订单号。
class ConversationDecision(StrictModel):
    intent: Annotated[ConversationIntent, BeforeValidator(ConversationIntent), status_schema(ConversationIntent, "Intent")]
    order_id: str | None
    order_goal: Annotated[
        Annotated[OrderGoal, BeforeValidator(OrderGoal)] | None,
        WithJsonSchema({"anyOf": [{"type": "string", "enum": [goal.value for goal in OrderGoal]},
                                 {"type": "null"}], "title": "Order Goal"}),
    ] = None


# 描述一个查单步骤，包括订单、依赖关系和完成条件。
class InvestigationStep(StrictModel):
    step_id: str = Field(min_length=1, max_length=64)
    kind: Annotated[Literal[InvestigationKind.INSPECT_ORDER], BeforeValidator(InvestigationKind)]
    order_id: str
    depends_on: list[str]
    completion: str = Field(min_length=1, max_length=160)


# 描述在可选查单依赖后执行的政策检索步骤。
class PolicyStep(StrictModel):
    step_id: str = Field(min_length=1, max_length=64)
    kind: Annotated[Literal[InvestigationKind.RETRIEVE_POLICY], BeforeValidator(InvestigationKind)]
    query: str = Field(min_length=1, max_length=2000)
    depends_on: list[str]
    completion: str = Field(min_length=1, max_length=160)


# 保存本轮查单、政策检索或有序组合的调查计划。
class InvestigationPlan(StrictModel):
    steps: list[InvestigationStep | PolicyStep] = Field(min_length=1, max_length=2)


# 关联追踪标识、模型工具调用和实际订单查询结果。
class OrderToolEvent(StrictModel):
    event_id: str = Field(min_length=1)
    trace_id: str
    model_tool_call_id: str = Field(min_length=1)
    order_id: str
    result: OrderQueryResult


# 关联 Host 请求、模型工具调用及 MCP 实际政策检索结果。
class PolicyToolEvent(StrictModel):
    event_id: str = Field(min_length=1)
    trace_id: str
    model_tool_call_id: str = Field(min_length=1)
    query: str
    result: PolicySearchResult


# 记录计划步骤的执行状态、工具事件和订单结果。
class TaskObservation(StrictModel):
    step_id: str
    status: Annotated[ObservationStatus, BeforeValidator(ObservationStatus), status_schema(ObservationStatus)]
    tool_event_id: str
    order_result: OrderQueryResult | None = None
    policy_result: PolicySearchResult | None = None
    policy_evidence: PolicyEvidence | None = None

    # 每个观察必须对应一种工具结果，政策证据只能附在政策观察上。
    @model_validator(mode="after")
    def check_payload(self):
        if (self.order_result is None) == (self.policy_result is None):
            raise ValueError("观察必须携带一种工具结果")
        if self.policy_evidence is not None and self.policy_result is None:
            raise ValueError("订单观察不能携带政策证据")
        return self


# 表示调查结束后的完成或失败决定及其原因。
class ReplanDecision(StrictModel):
    action: Annotated[ReplanAction, BeforeValidator(ReplanAction), status_schema(ReplanAction, "Action")]
    reason: str = Field(min_length=1, max_length=160)


# 描述会话原文中的消息角色和内容。
class ExchangeMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str


# 描述传入图的一轮会话历史和请求追踪标识。
class OrderTurn(StrictModel):
    history: list[ExchangeMessage]
    trace_id: str


# 描述节点可以写入的字段。省略字段表示保留旧值，显式传入 None 表示清空。
class OrderStateUpdate(StrictModel):
    # 本轮识别出的消费者意图、订单号和查询目标。
    decision: ConversationDecision | None = None
    # 本轮查单和政策检索的调查步骤。
    plan: InvestigationPlan | None = None
    # 当前步骤产生的订单或政策工具调用事件。
    event: OrderToolEvent | PolicyToolEvent | None = None
    # 当前工具事件经过核验后的步骤观察。
    observation: TaskObservation | None = None
    # 纯查单分支保存的重规划决定。
    replan_decision: ReplanDecision | None = None
    # 本轮给消费者的回复；有值时结束图执行。
    reply: str | None = None
    # 当前执行的调查步骤在计划中的位置，从 0 开始。
    step_index: int = 0
    # 已核验的订单查询结果，供后续政策调查使用。
    order_result: OrderQueryResult | None = None
    # 缺少订单号时暂存的资格咨询问题，等待同会话补充。
    eligibility_question: str | None = None
    # 本轮要回答的问题；补充订单号时恢复为原资格问题。
    question: str = ""
    # 政策依据不足时用于正式条款补查的查询文本。
    retry_query: str | None = None
    # 政策补查前保留的检索结果，用于与补查结果一起核验证据。
    policy_results: list[PolicySearchResult] = Field(default_factory=list)


# 将本轮输入与已合并的调查状态提供给图节点，持久化时沿用字典和字符串枚举。
class OrderState(OrderTurn, OrderStateUpdate):
    pass


