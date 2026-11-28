"""通过 Qwen 生成退款业务流程的结构化决定。"""

from collections.abc import Callable

from langchain_core.exceptions import ModelRefusalError

from interfaces.refund.decision import Decide, Decision
from model.policy.evidence import EligibilityExtraction, PolicySelection
from model.policy.status import PolicyEvent
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

        # region 使用严格 Schema 生成决定，缺少业务输出时报告契约错误
        try:
            decision = self.model.with_structured_output(schema, method="json_schema", strict=True).invoke(prompt)
        except ModelRefusalError as exc:

            # region 模型返回拒绝而没有结构化决定时交给现有降级路径
            raise ValueError("模型未提供结构化决定") from exc
            # endregion

        if not isinstance(decision, schema):

            # region 工具调用等非结构化结果不能作为业务决定
            raise ValueError("模型未返回符合契约的业务决定")
            # endregion

        # endregion

        # region 记录本轮政策选择和资格提取并返回已校验的决定
        if isinstance(decision, PolicySelection):
            self.emit("policy_selection", selection=decision.model_dump())
        if isinstance(decision, EligibilityExtraction):

            # region 记录资格候选的结构化提取结果，供本轮依据追溯
            self.emit(PolicyEvent.ELIGIBILITY_EXTRACTION.value, extraction=decision.model_dump(mode="json"))
            # endregion

        return decision
        # endregion
