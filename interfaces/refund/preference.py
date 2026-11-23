"""固定偏好节点使用的消费者偏好接口。"""

from typing import Protocol

from model.refund.preference import PreferenceCommand, PreferenceResult


# 提供已绑定可信消费者身份的长期偏好读写能力。
class ManagePreferences(Protocol):
    # 执行偏好命令并返回实际读写结果。
    def __call__(self, command: PreferenceCommand, /) -> PreferenceResult:
        # region 声明固定节点需要的偏好操作契约
        ...
        # endregion
