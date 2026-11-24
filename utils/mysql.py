"""管理使用应用连接配置的独立 MySQL 连接。"""

from collections.abc import Iterator
from contextlib import closing, contextmanager

import pymysql
from pymysql.connections import Connection

# 打开数据库连接，在调用结束时释放连接及未提交事务。
@contextmanager
def mysql_connection(database: str, **options) -> Iterator[Connection]:

    # region 连接指定数据库并在退出时释放资源
    with pymysql.connect(database=database, autocommit=True, **options) as connection:
        yield connection
    # endregion


# 从连接池借用连接，退出时由池回滚未提交事务并回收。
@contextmanager
def pooled_mysql_connection(pool, *, shareable: bool = True) -> Iterator[Connection]:

    # region 依据事务是否需要独占连接借用并归还连接
    with closing(pool.connection(shareable=shareable)) as connection:
        yield connection
    # endregion
