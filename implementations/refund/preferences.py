"""Host 直接管理 MySQL 中的消费者当前偏好。"""

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from interfaces.refund.preference import ManagePreferences
from model.refund.preference import (
    DeletePreference, ListPreferences, PreferenceAction, PreferenceCommand,
    PreferenceKey, PreferenceResult, PreferenceStatus, ResponseDetail, SetPreference,
)
from repositories.preference_repository import delete_preference, get_preference, save_preference


# 按可信消费者限定所有读写，命令不携带消费者编号。
class MySQLPreferences(ManagePreferences):
    # 接收 CLI 复用的 MySQL Engine 和可信消费者编号。
    def __init__(self, engine: Engine, *, consumer_id: str) -> None:

        # region 绑定消费者身份和业务连接来源
        self._engine = engine
        self._consumer_id = consumer_id
        # endregion

    # 执行查看、设置或删除，只有读到记录或确认提交后才返回相应成功状态。
    def __call__(self, command: PreferenceCommand, /) -> PreferenceResult:

        # region 执行偏好操作，在事务结束并归还连接后返回结果
        try:
            match command:
                case ListPreferences():

                    # region 查看本人当前偏好，缺失时返回空值
                    with self._engine.connect() as connection:
                        preference = get_preference(
                            connection, consumer_id=self._consumer_id, key=PreferenceKey.RESPONSE_DETAIL,
                        )
                    return PreferenceResult(
                        status=PreferenceStatus.LISTED, detail=preference.value if preference is not None else None,
                    )
                    # endregion

                case SetPreference():

                    # region 在独立短事务中保存本人偏好，确认提交后返回新值
                    with self._engine.begin() as connection:
                        save_preference(connection, consumer_id=self._consumer_id, key=command.key, value=command.value)
                    return PreferenceResult(status=PreferenceStatus.UPDATED, detail=command.value)
                    # endregion

                case DeletePreference():

                    # region 在独立短事务中删除本人偏好，提交后确认无当前值
                    with self._engine.begin() as connection:
                        delete_preference(connection, consumer_id=self._consumer_id, key=command.key)
                    return PreferenceResult(status=PreferenceStatus.DELETED)
                    # endregion

        except (SQLAlchemyError, ValidationError):

            # region 读取、写入或回执出错时返回失败，避免使用旧值
            return PreferenceResult(status=PreferenceStatus.FAILED)
            # endregion

        # endregion


# 用实际命令结果生成消费者可见的短句。
def preference_reply(command: PreferenceCommand, result: PreferenceResult) -> str:

    # region 根据操作结果显示当前值、确认变更或说明未确认
    label = "简短" if result.detail is ResponseDetail.BRIEF else "详细"
    match result.status:
        case PreferenceStatus.LISTED:
            return "当前没有已保存的长期偏好。" if result.detail is None else f"当前长期偏好：订单状态答复{label}。"
        case PreferenceStatus.UPDATED:
            return f"已保存长期偏好：订单状态答复{label}。"
        case PreferenceStatus.DELETED:
            return "已确认删除订单状态答复的详略偏好。"
        case PreferenceStatus.FAILED:
            if command.action is PreferenceAction.LIST:
                return "暂时无法读取当前长期偏好，请稍后再试。"
            return "暂时无法确认长期偏好是否已变更，请稍后查看当前偏好。"
    # endregion
