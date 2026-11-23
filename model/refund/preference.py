"""消费者跨会话答复偏好的命令、结果和持久化实体。"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from config.limits import PreferenceLimits
from model.base import StrictModel


# 限定首版可由消费者管理的长期偏好。
class PreferenceKey(StrEnum):
    RESPONSE_DETAIL = "response_detail"  # 订单状态答复的详略。


# 定义可用于当轮答复或持久化的详略值。
class ResponseDetail(StrEnum):
    BRIEF = "brief"  # 紧凑列出全部订单事实。
    DETAILED = "detailed"  # 按项目分别列出订单事实。


# 定义消费者可发出的长期偏好操作。
class PreferenceAction(StrEnum):
    LIST = "list"  # 查看本人当前有效的偏好。
    SET = "set"  # 保存一个受控值，替换同一键的旧值。
    DELETE = "delete"  # 删除一个受控键的当前值。


# 区分成功读取、成功变更和无法确认的操作结果。
class PreferenceStatus(StrEnum):
    LISTED = "listed"  # 已读取本人偏好，也可能没有保存值。
    UPDATED = "updated"  # 已提交新值，包含覆盖已有值。
    DELETED = "deleted"  # 已确认该偏好键无当前值。
    FAILED = "failed"  # 操作失败或持久化结果无法确认。


# 定义固定偏好节点对外记录的事件。
class PreferenceEvent(StrEnum):
    RESULT = "preference_result"  # 当前显式命令的实际持久化结果。


DetailValue = Annotated[ResponseDetail, BeforeValidator(ResponseDetail)]
KeyValue = Annotated[PreferenceKey, BeforeValidator(PreferenceKey)]


# 查看当前消费者的全部有效偏好，消费者身份由 Host 提供。
class ListPreferences(StrictModel):
    # 查看操作不携带偏好键和值。
    action: Annotated[Literal[PreferenceAction.LIST], BeforeValidator(PreferenceAction)]


# 保存或更正当前消费者的单项长期偏好。
class SetPreference(StrictModel):
    # 设置或覆盖同类偏好时使用的固定操作。
    action: Annotated[Literal[PreferenceAction.SET], BeforeValidator(PreferenceAction)]
    # 当前消费者要保存的受控偏好键。
    key: KeyValue
    # 该键要持久保存的新值。
    value: DetailValue


# 删除当前消费者的单项长期偏好。
class DeletePreference(StrictModel):
    # 删除操作只接收受控偏好键。
    action: Annotated[Literal[PreferenceAction.DELETE], BeforeValidator(PreferenceAction)]
    # 要删除的偏好键，无当前记录时仍可确认删除结果。
    key: KeyValue


PreferenceCommand = ListPreferences | SetPreference | DeletePreference


# 记录当前消息中的偏好指令和独立的业务请求原文。
class PreferenceIntent(StrictModel):
    # 剥离明确偏好指令后剩余的当前业务请求，只有偏好时为空。
    business_text: str
    # 本轮明确识别的长期命令，无管理请求时为空。
    command: PreferenceCommand | None = None
    # 对订单答复一次有效的详略要求，不参与持久化。
    temporary_detail: DetailValue | None = None


# 返回固定偏好节点实际取得的结果，供 CLI 和当轮表达使用。
class PreferenceResult(StrictModel):
    # 已确认的读写结果，失败时不可据此宣称持久化成功。
    status: Annotated[PreferenceStatus, BeforeValidator(PreferenceStatus)]
    # 已读取或更新的当前详略值，未设置、已删除或失败时为空。
    detail: DetailValue | None = None


# 描述 consumer_preferences 中首版支持读取的记录。
class ConsumerPreference(StrictModel):
    # 偏好所属消费者，也是组合主键的一部分。
    consumer_id: str = Field(min_length=1, max_length=PreferenceLimits.MAX_CONSUMER_ID_CHARS)
    # 用于唯一定位同类偏好的受控键。
    preference_key: KeyValue
    # 该键当前有效的受控值。
    value: DetailValue
    # 最后一次保存的 UTC 时间。
    updated_at: datetime
