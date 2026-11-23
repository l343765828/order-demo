"""定义 MCP 读取政策版本快照所需的目录接口。"""

from typing import Protocol

from model.policy.catalog import PolicySnapshot


# 解析最新或既有的政策版本快照。
class PolicyCatalogReader(Protocol):
    # 指定修订时读取该历史快照，省略时读取最新修订。
    def snapshot(self, revision: int | None = None) -> PolicySnapshot:
        # region 声明目录快照读取接口
        ...
        # endregion
