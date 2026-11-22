"""管理图线程标识和 MySQL checkpoint 连接。"""

from collections.abc import Iterator
from contextlib import contextmanager

import pymysql
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver


# 将业务提供的线程标识转换为 LangGraph 的运行配置。
def thread_config(thread_id: str) -> RunnableConfig:
    # region 将业务提供的线程标识转换为 LangGraph 的运行配置
    return {"configurable": {"thread_id": thread_id}}
    # endregion


# 使用调用方提供的数据库连接参数打开 checkpoint 存储并负责关闭连接。
@contextmanager
def open_mysql_checkpointer(database: str, **connection_options) -> Iterator[PyMySQLSaver]:
    # region 使用调用方提供的数据库连接参数打开 checkpoint 存储并负责关闭连接
    with pymysql.connect(database=database, autocommit=True, **connection_options) as connection:
        yield PyMySQLSaver(connection)
    # endregion
