"""从仓库的 SQL 目录加载应用内置语句。"""

from functools import lru_cache
from pathlib import Path
from typing import Final

from sqlalchemy import TextClause, text

_SQL_ROOT: Final = (Path(__file__).resolve().parents[1] / "repositories" / "sql").resolve()


# 加载应用指定的相对路径，可填充配置中的固定表名，查询值仍由调用方绑定。
def load_sql(relative_path: str, **table_names: str) -> TextClause:

    # region 将语句定位在固定 SQL 根目录，拒绝绝对路径与目录外文件
    requested = Path(relative_path)
    if requested.anchor:

        # region 指定了绝对路径时拒绝加载
        raise ValueError("SQL 文件须使用相对路径")
        # endregion

    path = (_SQL_ROOT / requested).resolve()
    if not path.is_relative_to(_SQL_ROOT):

        # region 指向根目录外的文件时拒绝加载
        raise ValueError("SQL 文件须位于仓库 SQL 目录内")
        # endregion

    # endregion

    # region 按实际文件路径和固定表名复用参数化语句
    return _read_sql(path, tuple(sorted(table_names.items())))
    # endregion


# 读取 UTF-8 SQL 并按应用配置填充内部表名，缓存可绑定参数的语句。
@lru_cache
def _read_sql(path: Path, table_names: tuple[tuple[str, str], ...]) -> TextClause:

    # region 读取 SQL 模板并准备参数化语句
    statement = path.read_text(encoding="utf-8")
    if table_names:

        # region 仅填充应用配置指定的表名
        statement = statement.format_map(dict(table_names))
        # endregion

    return text(statement)
    # endregion
