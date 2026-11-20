"""Shared MySQL connection configuration and per-process pool."""
import pymysql
from dbutils.pooled_db import PooledDB
from config import load_config


# 从应用配置生成使用 UTC 的 MySQL 连接参数。
def connection_config():
    # region 校验共享连接配置
    mysql = load_config().mysql
    # endregion
    return dict(host=mysql.host, port=mysql.port, user=mysql.user, password=mysql.password,
                charset="utf8mb4", connect_timeout=5, init_command="SET time_zone = '+00:00'")


# 为当前进程创建连接 order_demo 数据库的连接池。
def create_pool():
    return PooledDB(pymysql, maxconnections=5, blocking=True,
                    database="order_demo", **connection_config())
