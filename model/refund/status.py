"""退款业务流程及会话状态模型使用的枚举。"""

from enum import StrEnum


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
