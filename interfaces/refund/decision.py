"""退款业务流程的结构化模型决策接口。"""

from typing import Protocol, TypeVar

from model.base import StrictModel

Decision = TypeVar("Decision", bound=StrictModel)


# 定义退款业务流程调用模型的接口，用于识别意图、规划调查、调整计划和选择政策依据。
class Decide(Protocol):
    # 接收决定的数据模型和提示词，返回按该数据模型校验的决定。
    def __call__(self, schema: type[Decision], prompt: str, /) -> Decision:
        # region 声明结构化模型决策的输入与返回类型
        ...
        # endregion
