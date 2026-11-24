"""管理使用应用连接配置的独立 MySQL 连接。"""

from collections.abc import Iterator
from contextlib import contextmanager

import pymysql
from pymysql.connections import Connection

from config import MySQLConfig, load_config
from config.limits import ConnectionLimits


# 从传入配置或默认 JSON 生成使用 UTC 的管理和 Checkpoint 连接参数。
def connection_config(mysql: MySQLConfig | None = None):

    # region 读取连接配置并生成 PyMySQL 参数
    if mysql is None:

        # region 未指定连接配置时读取当前应用 JSON
        mysql = load_config().mysql
        # endregion

    return dict(host=mysql.host, port=mysql.port, user=mysql.user, password=mysql.password,
                charset=ConnectionLimits.MYSQL_CHARSET, connect_timeout=ConnectionLimits.MYSQL_CONNECT_SECONDS,
                init_command=ConnectionLimits.MYSQL_UTC_INIT_COMMAND)
    # endregion


# 打开数据库连接，在调用结束时释放连接及未提交事务。
@contextmanager
def mysql_connection(database: str | None, **options) -> Iterator[Connection]:

    # region 按指定库名连接或由管理命令在连接后选库，退出时释放资源
    with pymysql.connect(database=database, autocommit=True, **options) as connection:
        yield connection
    # endregion
