"""在现有 SQLAlchemy 连接上管理 MySQL 连接级命名锁。"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from sqlalchemy import Connection, text

_ACQUIRE_LOCK: Final = text("SELECT GET_LOCK(:name, :wait_seconds)")
_RELEASE_LOCK: Final = text("SELECT RELEASE_LOCK(:name)")


# 借用调用方的连接持锁，等待秒数由调用方指定，返回是否取得锁。
@contextmanager
def mysql_named_lock(connection: Connection, *, name: str, wait_seconds: int) -> Iterator[bool]:

    # region 在短事务内加锁，未能确认加锁结果时丢弃连接
    try:
        with connection.begin():
            acquired = connection.execute(_ACQUIRE_LOCK, {"name": name, "wait_seconds": wait_seconds}).scalar_one() == 1
    except BaseException:

        # region 无法获知加锁结果时丢弃物理连接并传播异常
        connection.invalidate()
        raise
        # endregion

    # endregion

    # region 允许调用方在同一物理连接上持锁跨事务工作，退出时清理锁
    try:
        yield acquired
    finally:
        if (
            # 本次已经取得命名锁。
            acquired
            # 持有锁的物理连接仍然可用。
            and not connection.invalidated
        ):

            # region 原物理连接仍持锁时执行解锁，未确认释放则丢弃连接
            released = False
            try:
                with connection.begin():
                    confirmed = connection.execute(_RELEASE_LOCK, {"name": name}).scalar_one() == 1
                released = confirmed
            finally:
                if not released:

                    # region 解锁未确认时阻止该物理连接回到池中
                    connection.invalidate()
                    # endregion

            # endregion

    # endregion
