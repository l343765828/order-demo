"""在偏好 Service 提供的连接上读写消费者当前偏好。"""

from typing import Final

from sqlalchemy import Connection

from config.demo import CONSUMER_PREFERENCES_TABLE
from model.refund.preference import ConsumerPreference, PreferenceKey, ResponseDetail
from utils.sql_loader import load_sql

_GET_PREFERENCE_SQL: Final = "preferences/get_preference.sql"
_SAVE_PREFERENCE_SQL: Final = "preferences/save_preference.sql"
_DELETE_PREFERENCE_SQL: Final = "preferences/delete_preference.sql"


# 查询可信消费者的指定偏好，返回已校验记录或 None。
def get_preference(connection: Connection, *, consumer_id: str, key: PreferenceKey) -> ConsumerPreference | None:

    # region 按消费者和偏好键读取并校验数据库记录
    row = connection.execute(
        load_sql(_GET_PREFERENCE_SQL, preferences=CONSUMER_PREFERENCES_TABLE),
        {"consumer_id": consumer_id, "preference_key": key.value},
    ).mappings().one_or_none()
    return ConsumerPreference.model_validate(dict(row)) if row is not None else None
    # endregion


# 保存可信消费者的受控偏好值，已有同类偏好时原子覆盖。
def save_preference(connection: Connection, *, consumer_id: str, key: PreferenceKey, value: ResponseDetail) -> None:

    # region 按消费者和偏好键插入或更新当前值
    connection.execute(
        load_sql(_SAVE_PREFERENCE_SQL, preferences=CONSUMER_PREFERENCES_TABLE),
        {"consumer_id": consumer_id, "preference_key": key.value, "value": value.value},
    )
    # endregion


# 删除可信消费者的指定偏好，缺失时保持无当前记录。
def delete_preference(connection: Connection, *, consumer_id: str, key: PreferenceKey) -> None:

    # region 仅删除该消费者对应的偏好记录
    connection.execute(
        load_sql(_DELETE_PREFERENCE_SQL, preferences=CONSUMER_PREFERENCES_TABLE),
        {"consumer_id": consumer_id, "preference_key": key.value},
    )
    # endregion
