"""在演示数据重置事务中清空退款业务和图状态。"""

from typing import Final

from sqlalchemy import Connection, inspect

from config.demo import CONSUMER_PREFERENCES_TABLE
from config.limits import RefundCheckpointTables, RefundTables
from utils.sql_loader import load_sql

_CLEAR_TABLE_SQL: Final = "demo_data/clear_table.sql"


# 使用调用方的事务连接删除退款演示表中的数据。
def clear_refund_demo(connection: Connection) -> None:

    # region 按外键依赖顺序清空提交、退款、会话、偏好和订单
    for table in (
        RefundTables.DISPATCHES,
        RefundTables.REFUNDS,
        RefundTables.SUMMARIES,
        RefundTables.MESSAGES,
        RefundTables.SESSIONS,
        CONSUMER_PREFERENCES_TABLE,
        RefundTables.ORDERS,
    ):
        connection.execute(load_sql(_CLEAR_TABLE_SQL, table=table))
    # endregion

    # region 清空已初始化的图快照、通道版本和中间写入
    tables = set(inspect(connection).get_table_names())
    for table in (
        RefundCheckpointTables.WRITES,
        RefundCheckpointTables.BLOBS,
        RefundCheckpointTables.CHECKPOINTS,
    ):
        if table in tables:

            # region 检查点表存在时删除全部退款流程数据
            connection.execute(load_sql(_CLEAR_TABLE_SQL, table=table))
            # endregion

    # endregion
