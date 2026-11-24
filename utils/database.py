"""创建供业务 Service 复用的 MySQL Engine。"""

from sqlalchemy import Engine, URL, create_engine

from config import MySQLConfig
from config.limits import ConnectionLimits


# 使用应用的 MySQL 配置创建 Engine，独立库调用方可以显式指定库名。
def create_mysql_engine(mysql: MySQLConfig, *, database: str | None = None) -> Engine:

    # region 用原始凭据以及选定的库名构造 MySQL 地址
    url = URL.create(
        "mysql+pymysql", username=mysql.user, password=mysql.password, host=mysql.host,
        port=mysql.port, database=mysql.database if database is None else database,
        query={"charset": ConnectionLimits.MYSQL_CHARSET},
    )
    # endregion

    # region 配置 UTC 会话、连接超时及有界连接池并返回 Engine
    return create_engine(
        url,
        connect_args={
            "connect_timeout": ConnectionLimits.MYSQL_CONNECT_SECONDS,
            "read_timeout": ConnectionLimits.MYSQL_READ_SECONDS,
            "write_timeout": ConnectionLimits.MYSQL_WRITE_SECONDS,
            "init_command": ConnectionLimits.MYSQL_UTC_INIT_COMMAND,
        },
        pool_size=ConnectionLimits.MYSQL_POOL_CONNECTIONS,
        max_overflow=ConnectionLimits.MYSQL_POOL_OVERFLOW_CONNECTIONS,
        pool_timeout=ConnectionLimits.MYSQL_POOL_WAIT_SECONDS,
        pool_recycle=ConnectionLimits.MYSQL_POOL_RECYCLE_SECONDS,
        pool_pre_ping=True,
        hide_parameters=True,
    )
    # endregion
