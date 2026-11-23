"""Shared MySQL connection configuration and per-process pool."""
import pymysql
from dbutils.pooled_db import PooledDB
from config import MySQLConfig, load_config
from config.limits import ConnectionLimits


# 从应用配置生成使用 UTC 的 MySQL 连接参数。
def connection_config(mysql: MySQLConfig | None = None):
    # region 从应用配置读取 MySQL 连接设置
    if mysql is None:
        mysql = load_config().mysql
    # endregion
    # region 生成使用 UTC 和 UTF-8 的 MySQL 连接参数
    return dict(host=mysql.host, port=mysql.port, user=mysql.user, password=mysql.password,
                charset="utf8mb4", connect_timeout=ConnectionLimits.MYSQL_CONNECT_SECONDS, init_command="SET time_zone = '+00:00'")
    # endregion


# 为当前进程创建连接 order_demo 数据库的连接池。
def create_pool():
    # region 为当前进程创建连接 order_demo 数据库的连接池
    mysql = load_config().mysql
    return PooledDB(pymysql, maxconnections=5, blocking=True,
                    database=mysql.database, **connection_config(mysql))
    # endregion
