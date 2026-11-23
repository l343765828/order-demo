"""退款业务流程及会话状态模型使用的枚举。"""

from enum import StrEnum


# 区分能携带退款授权的消费者原文和助手回复。
class ConversationRole(StrEnum):
    USER = "user"  # 消费者亲自发出的消息，可作为本次授权的依据。
    ASSISTANT = "assistant"  # 演示助理发出的回复，无消费者授权效力。


# 表示消费者本轮的业务意图。
class ConversationIntent(StrEnum):
    ORDER_QUESTION = "order_question"  # 查询本人订单状态或咨询该订单能否退款。
    POLICY_QUESTION = "policy_question"  # 咨询通用退款规则，例如退款条件或到账时间。
    REFUND_REQUEST = "refund_request"  # 明确请求办理退款，包括满足条件时办理的请求。
    CANCEL = "cancel"  # 请求取消尚未执行的退款办理。
    CHANGE_ORDER = "change_order"  # 请求更换本次退款办理的目标订单。
    MEMORY_REQUEST = "memory_request"  # 请求查看、保存、更正或删除长期偏好。
    OUT_OF_SCOPE = "out_of_scope"  # 请求超出退款演示支持的业务范围。
    UNCLEAR = "unclear"  # 当前消息不足以确定意图，例如无法区分咨询和办理，需要先澄清。


# 区分订单状态查询和退款资格咨询。
class OrderGoal(StrEnum):
    STATUS = "status"  # 查询订单的支付、发货或模拟退款状态。
    ELIGIBILITY = "eligibility"  # 结合订单事实和政策解释能否退款，本轮只做咨询。


# 表示调查后继续、结束或失败。
class ReplanAction(StrEnum):
    CONTINUE = "continue"  # 根据已有观察继续调查或补查政策。
    FINISH = "finish"  # 调查已得出结果，可以结束本轮。
    FAIL = "fail"  # 查询失败或证据不足，结束本轮并说明原因。


# 区分订单与政策调查步骤。
class InvestigationKind(StrEnum):
    INSPECT_ORDER = "inspect_order"  # 按当前消费者身份读取一笔订单的业务事实。
    RETRIEVE_POLICY = "retrieve_policy"  # 检索并重排当前版本的退款政策候选。


# 定义由 Host 固定生成的调查计划步骤标识。
class HostStepId(StrEnum):
    ORDER = "order"  # 在退款资格咨询中读取本人订单事实，供政策检索使用。
    POLICY = "policy"  # 在通用咨询或订单资格咨询中检索退款政策。


# 表示调查步骤是否完成、缺少依据或失败。
class ObservationStatus(StrEnum):
    COMPLETED = "completed"  # 步骤正常取得订单结果或政策检索结果。
    NO_EVIDENCE = "no_evidence"  # 步骤正常返回，但未找到可继续调查的本人订单。
    FAILED = "failed"  # 工具查询失败，无法取得本步骤的业务结果。


# 管理命令显式选择初始化或装载退款演示数据。
class DemoDataCommand(StrEnum):
    INIT = "init"  # 创建缺失的库、应用表和外键。
    SEED = "seed"  # 补齐演示订单和示例退款，保留现有业务记录。


# 区分模拟退款执行及回执核对后的业务结果。
class RefundOutcome(StrEnum):
    SIMULATED_SUCCEEDED = "simulated_succeeded"  # 本次执行产生了真实的模拟业务记录。
    ALREADY_REFUNDED = "already_refunded"  # 已有原单，直接返回既有业务记录。
    DENIED = "denied"  # 业务条件未通过，未产生新退款。
    FAILED = "failed"  # 明确没有成功写入，原因属于技术故障。
    UNCONFIRMED = "unconfirmed"  # 当前无法确定结果，保留未决拦截。


# 区分只读查询找到原单、暂未找到和无法查询。
class RefundLookupStatus(StrEnum):
    FOUND = "found"  # 已读取可信退款记录。
    NOT_FOUND = "not_found"  # 当前未查到本人退款，不能据此认定未发送。
    UNAVAILABLE = "unavailable"  # 业务存储或传输未能给出可信观察。


# 标记提交记录对同订单后续办理的拦截和结果。
class DispatchStatus(StrEnum):
    PENDING = "pending"  # 已记录授权并等待一次执行或核对。
    UNCONFIRMED = "unconfirmed"  # 已尝试发送但目前无法确定结果。
    SUCCEEDED = "succeeded"  # 已关联可信的原模拟退款单。
    CLOSED_NO_REFUND = "closed_no_refund"  # 已拒绝或已确认无退款并关闭。


# 为拒绝、技术失败和未决结果记录可追踪的业务原因。
class RefundReason(StrEnum):
    UNAVAILABLE_ORDER = "unavailable_order"  # 本人没有可操作的该订单。
    UNPAID = "unpaid"  # 订单未支付。
    SHIPPED = "shipped"  # 订单已发货。
    INVALID_DISPATCH = "invalid_dispatch"  # 请求缺少匹配的有效提交和授权。
    POLICY_UNAVAILABLE = "policy_unavailable"  # 当前政策依据无法支持执行。
    POLICY_NOT_SAVED = "policy_not_saved"  # 保存最终政策事件未得到确认。
    DISPATCH_NOT_SAVED = "dispatch_not_saved"  # 保存本次提交未得到确认。
    EXECUTION_UNAVAILABLE = "execution_unavailable"  # 执行通道未配置或确认无法使用。
    DATABASE_FAILURE = "database_failure"  # 数据库明确没有提交退款。
    RECEIPT_UNKNOWN = "receipt_unknown"  # 暂时无法确认已尝试的业务结果。


# 区分预置模拟退款与实际执行创建的记录。
class RefundSource(StrEnum):
    SEED = "seed"  # 初始化时装载的模拟结果，未关联运行时提交。
    EXECUTION = "execution"  # 由受控执行创建，关联实际幂等键。


# 记录退款执行中特有的结构化事件。
class RefundEvent(StrEnum):
    POLICY_VERIFIED = "refund_policy_verified"  # 已保存最终政策依据。
    DISPATCH_SAVED = "refund_dispatch_saved"  # 已确认本次带授权提交。
    SUBMIT = "refund_submit"  # Host 开始唯一一次受控调用。
    LOOKUP = "refund_lookup"  # 固定节点只读核对原退款。
    RESULT = "refund_result"  # 业务结果已经确定或仍待核对。
    STORE_FAILED = "refund_store_failed"  # 关键保存或结果状态回写失败。
    REPLY_FAILED = "refund_reply_failed"  # 确定性模板展示发生故障。
