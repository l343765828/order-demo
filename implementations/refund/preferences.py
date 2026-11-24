"""Host 直接管理 MySQL 中的消费者当前偏好。"""

import pymysql
from dbutils.pooled_db import PooledDB
from pydantic import ValidationError

from config.demo import CONSUMER_PREFERENCES_TABLE
from interfaces.refund.preference import ManagePreferences
from model.refund.preference import (
    ConsumerPreference, DeletePreference, ListPreferences, PreferenceAction, PreferenceCommand,
    PreferenceKey, PreferenceResult, PreferenceStatus, ResponseDetail, SetPreference,
)
from utils.mysql import pooled_mysql_connection


# 按可信消费者限定所有读写，命令不携带消费者编号。
class MySQLPreferences(ManagePreferences):
    # 接收 Host 创建的 MySQL 连接池和已确认的消费者编号。
    def __init__(self, pool: PooledDB, consumer_id: str) -> None:

        # region 绑定消费者身份和连接池
        self.pool = pool
        self.consumer_id = consumer_id
        # endregion

    # 执行查看、设置或删除，只有读到记录或确认提交后才返回相应成功状态。
    def __call__(self, command: PreferenceCommand, /) -> PreferenceResult:

        # region 使用独立连接处理真实偏好操作并释放资源
        try:
            with pooled_mysql_connection(self.pool, shareable=False) as connection, connection.cursor() as cursor:
                match command:
                    case ListPreferences():

                        # region 只读取本消费者的受控键并校验外部存储数据
                        cursor.execute(
                            f"SELECT consumer_id, preference_key, value, updated_at FROM {CONSUMER_PREFERENCES_TABLE} "
                            "WHERE consumer_id=%s AND preference_key=%s",
                            (self.consumer_id, PreferenceKey.RESPONSE_DETAIL.value),
                        )
                        row = cursor.fetchone()
                        if row is None:
                            return PreferenceResult(status=PreferenceStatus.LISTED)
                        preference = ConsumerPreference(
                            consumer_id=row[0], preference_key=row[1], value=row[2], updated_at=row[3],
                        )
                        return PreferenceResult(status=PreferenceStatus.LISTED, detail=preference.value)
                        # endregion

                    case SetPreference():

                        # region 原子更新当前值，确认提交后返回新偏好
                        cursor.execute(
                            f"INSERT INTO {CONSUMER_PREFERENCES_TABLE} (consumer_id, preference_key, value, updated_at) "
                            "VALUES (%s, %s, %s, UTC_TIMESTAMP(6)) "
                            "ON DUPLICATE KEY UPDATE value=%s, updated_at=UTC_TIMESTAMP(6)",
                            (self.consumer_id, command.key.value, command.value.value, command.value.value),
                        )
                        connection.commit()
                        return PreferenceResult(status=PreferenceStatus.UPDATED, detail=command.value)
                        # endregion

                    case DeletePreference():

                        # region 只删除本消费者对应键，确认提交后说明当前无该值
                        cursor.execute(
                            f"DELETE FROM {CONSUMER_PREFERENCES_TABLE} WHERE consumer_id=%s AND preference_key=%s",
                            (self.consumer_id, command.key.value),
                        )
                        connection.commit()
                        return PreferenceResult(status=PreferenceStatus.DELETED)
                        # endregion

        except (pymysql.MySQLError, ValidationError):

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
