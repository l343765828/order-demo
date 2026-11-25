"""查单流程使用的结构化数据模型。"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, WithJsonSchema, model_validator

from config.limits import LegacyCurrency, RefundLimits
from model.base import StrictModel
from model.orders.query import OrderQueryResult
from model.policy.search import PolicyCatalogRevision, PolicySearchResult
from model.policy.evidence import PolicyEvidence
from model.base import status_schema
from model.refund.preference import DetailValue, PreferenceCommand, PreferenceResult
from model.refund.status import ConversationIntent, ConversationRole, InvestigationKind, ObservationStatus, OrderGoal, ReplanAction
from model.refund.submission import AuthorizedRefundIntent, RefundPolicyVerification, RefundResult, RefundTurnSource


# 定义退款业务流程使用的 LangGraph 节点标识。
class OrderFlowNode(StrEnum):
    # 节点标识以字符串传给 LangGraph，显式声明兼容 PyCharm 对 StrEnum.value 的类型检查。
    value: str

    INTENT = "intent"  # 识别本轮意图并初始化调查状态。
    PREFERENCES = "preferences"  # 处理当前长期偏好命令并准备当轮表达。
    PLAN = "plan"  # 制定或核对本轮只读调查计划。
    EXECUTE = "execute"  # 执行当前调查步骤并取得工具结果。
    OBSERVE = "observe"  # 核对工具结果是否属于本轮和当前步骤。
    REPLAN = "replan"  # 根据已核对的结果继续调查或生成回复。
    REVIEW_REFUND = "review_refund"  # 固定业务节点重查订单并持久保存最终核验依据。
    SEND_REFUND = "send_refund"  # 固定业务节点持久保存提交并受控发送一次。
    REFUND_REPLY = "refund_reply"  # 将可信退款结果填入消费者回复。


# 标识兼容历史币种时读取的退款检查点通道和嵌套字段。
class RefundCheckpointField(StrEnum):
    EVENT = "event"  # 调查步骤产生的工具事件。
    RESULT = "result"  # 工具事件中的查单结果。
    OBSERVATION = "observation"  # 当前工具事件经核对后的观察。
    ORDER_RESULT = "order_result"  # 观察中或图状态中独立保存的查单结果。
    ORDER = "order"  # 成功查单时返回的本人订单详情。
    REFUND_RESULT = "refund_result"  # 退款办理或原单核对后保存的业务回执。
    REFUND = "refund"  # 回执中已确认的模拟退款记录。


# 保存模型识别的业务意图以及当前消息中的订单号。
class BusinessConversationDecision(StrictModel):
    # 消费者本轮请求的意图，用于选择查单、政策咨询或澄清等路径。
    intent: Annotated[ConversationIntent, BeforeValidator(ConversationIntent), status_schema(ConversationIntent, "Intent")]
    # 从本轮消息中识别的订单号，消费者未提供时为 None。
    order_id: str | None
    # 查单的具体目标是了解订单状态或咨询退款资格，其他意图默认无查单目标。
    order_goal: Annotated[
        Annotated[OrderGoal, BeforeValidator(OrderGoal)] | None,
        WithJsonSchema({"anyOf": [{"type": "string", "enum": [goal.value for goal in OrderGoal]},
                                 {"type": "null"}], "title": "Order Goal"}),
    ] = None


# 将模型业务判断和当前消息中解析出的偏好指令组合成完整意图。
class ConversationDecision(BusinessConversationDecision):
    # 当前消息中的显式长期偏好命令，可与业务意图同时存在。
    memory_request: PreferenceCommand | None = None
    # 仅本轮适用的订单状态表达方式，不写入偏好表。
    temporary_detail: DetailValue | None = None


# 描述一个查单步骤，包括订单、依赖关系和完成条件。
class InvestigationStep(StrictModel):
    # 本轮计划中的查单步骤标识。普通查单由模型生成，资格咨询由 Host 指定。
    step_id: str = Field(min_length=1, max_length=RefundLimits.MAX_STEP_ID_CHARS)
    # 固定为查单，表示读取当前消费者的订单事实。
    kind: Annotated[Literal[InvestigationKind.INSPECT_ORDER], BeforeValidator(InvestigationKind)]
    # 本步骤要查询的订单号，须与本轮意图中已核验的目标订单一致。
    order_id: str
    # 本步骤的前置步骤标识。当前查单可直接执行，填写空列表。
    depends_on: list[str]
    # 计划中对预期结果的说明，例如取得目标订单的支付、发货和模拟退款状态。
    completion: str = Field(min_length=1, max_length=RefundLimits.MAX_DECISION_TEXT_CHARS)


# 描述在可选查单依赖后执行的政策检索步骤。
class PolicyStep(StrictModel):
    # Host 在本轮计划中指定的政策步骤标识，用于关联执行记录和观察结果。
    step_id: str = Field(min_length=1, max_length=RefundLimits.MAX_STEP_ID_CHARS)
    # 固定为退款政策检索，校验时把输入值转换为该动作的枚举成员。
    kind: Annotated[Literal[InvestigationKind.RETRIEVE_POLICY], BeforeValidator(InvestigationKind)]
    # 用于检索退款政策的查询文本，可根据订单事实或补查需要替换。
    query: str = Field(min_length=1, max_length=RefundLimits.MAX_POLICY_QUERY_CHARS)
    # Host 为补查填入首次成功检索的目录修订，初始计划不指定修订。
    catalog_revision: PolicyCatalogRevision | None = None
    # 检索前所依赖的步骤标识列表，空列表表示可直接检索。
    depends_on: list[str]
    # 描述步骤预期取得的结果。流程依据实际检索状态和证据决定后续处理。
    completion: str = Field(min_length=1, max_length=RefundLimits.MAX_DECISION_TEXT_CHARS)


# 保存本轮查单、政策检索或有序组合的调查计划。
class InvestigationPlan(StrictModel):
    # 按执行顺序列出的调查步骤。查询订单退款资格时先查单，再检索适用政策。
    steps: list[InvestigationStep | PolicyStep] = Field(min_length=1, max_length=RefundLimits.MAX_INVESTIGATION_STEPS)


# 关联追踪标识、模型工具调用和实际订单查询结果。
class OrderToolEvent(StrictModel):
    # Host 为本次 MCP 查单调用分配的工具事件编号，用于关联调用和观察记录。
    event_id: str = Field(min_length=1)
    # 归属的会话轮次追踪编号，供流程核对结果是否来自本轮。
    trace_id: str
    # 模型发出的工具调用编号，用于核对真实查单结果是否已回传给模型。
    model_tool_call_id: str = Field(min_length=1)
    # 本次工具实际查询的订单号，供流程与计划中的订单号核对。
    order_id: str
    # MCP 返回的查单结果，可表示查到本人订单、订单不可操作或查询失败。
    result: OrderQueryResult


# 关联 Host 请求、模型工具调用及 MCP 实际政策检索结果。
class PolicyToolEvent(StrictModel):
    # Host 为本次 MCP 政策查询调用分配的工具事件编号，用于关联调用和观察记录。
    event_id: str = Field(min_length=1)
    # 归属的会话轮次追踪编号，供流程核对候选是否来自本轮。
    trace_id: str
    # 模型发出的工具调用编号，用于核对真实政策结果是否已回传给模型。
    model_tool_call_id: str = Field(min_length=1)
    # 实际发送给政策检索工具的查询文本，供流程与执行时的查询核对。
    query: str
    # MCP 返回的政策检索结果，包含检索服务分配的事件编号和候选条款。
    result: PolicySearchResult


# 记录计划步骤的执行状态、工具事件和订单结果。
class TaskObservation(StrictModel):
    # 产生本次观察的调查步骤标识，可在本轮计划中定位。
    step_id: str
    # 根据工具结果判定的步骤状态，包括完成、缺少证据和失败。
    status: Annotated[ObservationStatus, BeforeValidator(ObservationStatus), status_schema(ObservationStatus)]
    # 产生本次观察的 MCP 工具事件编号，供回查实际结果。
    tool_event_id: str
    # 已核对的查单结果，仅查单观察携带此字段。
    order_result: OrderQueryResult | None = None
    # 已核对调用归属的政策检索结果，仅政策观察携带此字段。
    policy_result: PolicySearchResult | None = None
    # 政策候选经评估后形成的依据记录，尚未评估或属于查单观察时为 None。
    policy_evidence: PolicyEvidence | None = None

    # 每个观察必须对应一种工具结果，政策证据只能附在政策观察上。
    @model_validator(mode="after")
    def check_payload(self):

        # region 观察须恰有一种工具结果，退款政策依据只可附于政策观察
        if (self.order_result is None) == (self.policy_result is None):
            raise ValueError("观察必须携带一种工具结果")
        if (
            # 当前步骤的观察携带退款政策依据。
            self.policy_evidence is not None
            # 当前观察没有相应的政策检索结果。
            and self.policy_result is None
        ):
            raise ValueError("订单观察不能携带政策证据")
        return self
        # endregion


# 表示调查结束后的完成或失败决定及其原因。
class ReplanDecision(StrictModel):
    # 调查后的处理动作，用于记录继续调查、结束本轮或报告失败。
    action: Annotated[ReplanAction, BeforeValidator(ReplanAction), status_schema(ReplanAction, "Action")]
    # 说明为何根据本次观察选择该动作，供过程记录和复盘使用。
    reason: str = Field(min_length=1, max_length=RefundLimits.MAX_DECISION_TEXT_CHARS)


# 描述会话原文中的消息角色和内容。
class ExchangeMessage(StrictModel):
    # 发言方，取 user 时是消费者，取 assistant 时是助手。
    role: Annotated[ConversationRole, BeforeValidator(ConversationRole)]
    # 发言方的消息原文，供会话回放和后续意图识别使用。
    content: str


# 描述传入图的一轮会话历史和请求追踪标识。
class OrderTurn(StrictModel):
    # 按消息序号排列的会话原文，最后一条是本轮待处理的消费者消息。
    history: list[ExchangeMessage]
    # 本轮请求唯一的追踪编号，用于串联图节点、工具调用和政策依据核对。
    trace_id: str
    # 实际 Host 提供的本轮原始用户消息位置，旧 checkpoint 可缺省。
    refund_source: RefundTurnSource | None = None


# 描述节点可以写入的字段。省略字段表示保留旧值，显式传入 None 表示清空。
class OrderStateUpdate(StrictModel):
    # 本轮识别出的消费者意图、订单号和查询目标。
    decision: ConversationDecision | None = None
    # 本轮显式偏好命令的真实结果，隐式读取不填此字段。
    preference_result: PreferenceResult | None = None
    # 本轮有效的订单答复详略，新轮从当前偏好或临时要求重新计算。
    response_detail: DetailValue | None = None
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
    # 本轮成功的检索回执，第一项确定本轮目录修订。
    policy_results: list[PolicySearchResult] = Field(default_factory=list)
    # 仅明确办理且本轮订单已绑定时存在，换轮重置。
    refund_authorization: AuthorizedRefundIntent | None = None
    # 已确认持久保存的政策核验事件，提交前还须核对属于当前活跃轮次。
    refund_verification: RefundPolicyVerification | None = None
    # 固定退款节点获得的业务回执或阻断原因。
    refund_result: RefundResult | None = None


# 将本轮输入与已合并的调查状态提供给图节点，持久化时沿用字典和字符串枚举。
class OrderState(OrderTurn, OrderStateUpdate):
    # 读取历史图状态时移除已知订单详情及退款回执中的人民币币种。
    @model_validator(mode="before")
    @classmethod
    def read_legacy_amounts(cls, value: object) -> object:

        # region 兼容历史工具事件、调查观察、查单结果及退款回执
        if not isinstance(value, dict):

            # region 非映射交由状态模型原有校验
            return value
            # endregion

        updates = legacy_amount_updates(value)
        return {**value, **updates} if updates else value
        # endregion


# 提取必须重新写入的历史金额通道，供图状态验证和快照保存共用。
def legacy_amount_updates(values: dict[str, Any]) -> dict[str, Any]:

    # region 按持久化状态的四种已知形态整理含旧字段的通道
    updates = {}
    field = RefundCheckpointField
    for channel, path in (
        (field.EVENT, (field.RESULT, field.ORDER)),
        (field.OBSERVATION, (field.ORDER_RESULT, field.ORDER)),
        (field.ORDER_RESULT, (field.ORDER,)),
        (field.REFUND_RESULT, (field.REFUND,)),
    ):
        original = values.get(channel)
        converted = _without_legacy_currency(original, path)
        if converted is not original:

            # region 仅保存发生升级的通道及其无币种内容
            updates[channel.value] = converted
            # endregion

    return updates
    # endregion


# 沿指定快照字段路径移除旧人民币币种，保留原快照与其他字段供原有校验使用。
def _without_legacy_currency(value: object, path: tuple[RefundCheckpointField, ...]) -> object:

    # region 缺少历史字段时直接复用快照节点
    if not isinstance(value, dict):

        # region 当前路径没有可转换的历史映射
        return value
        # endregion

    if not path:

        # region 仅移除合法的历史人民币值，其余值保留并由严格模型拒绝
        if value.get(LegacyCurrency.FIELD) != LegacyCurrency.CNY:

            # region 没有旧人民币字段时保留原值
            return value
            # endregion

        result = value.copy()
        del result[LegacyCurrency.FIELD]
        return result
        # endregion

    # endregion

    # region 仅在子值转换后复制当前字段映射
    field, *remaining = path
    original = value.get(field)
    converted = _without_legacy_currency(original, tuple(remaining))
    return value if converted is original else {**value, field.value: converted}
    # endregion

