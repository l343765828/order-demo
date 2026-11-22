"""通过 Qwen 生成退款业务流程的结构化决定。"""

from collections.abc import Callable
from typing import cast

from interfaces.refund.decision import Decide, Decision
from model.policy.evidence import PolicySelection
from utils.qwen_client import QwenChatOpenAI


# 为退款业务流程调用 Qwen，并记录政策依据的选择结果。
class QwenDecider(Decide):
    # 接收已配置的 Qwen 客户端和当前会话的事件记录函数。
    def __init__(self, model: QwenChatOpenAI, emit: Callable[..., None]) -> None:
        # region 接收已配置的 Qwen 客户端和当前会话的事件记录函数
        self.model = model
        self.emit = emit
        # endregion

    # 使用指定模型类约束输出，将政策选择写入当前请求的事件日志。
    def __call__(self, schema: type[Decision], prompt: str, /) -> Decision:
        # region 使用指定模型类约束输出，将政策选择写入当前请求的事件日志
        decision = cast(Decision, self.model.with_structured_output(schema, method="json_schema", strict=True).invoke(prompt))
        if isinstance(decision, PolicySelection):
            self.emit("policy_selection", selection=decision.model_dump())
        return decision
        # endregion
