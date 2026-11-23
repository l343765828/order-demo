"""定义退款政策检索流程需要的后端能力。"""

from typing import Protocol

from model.policy.catalog import PolicyVersion
from model.policy.search import PolicyEntry
from model.policy.status import PolicySearchMode


# 定义政策检索使用的查询编码、候选召回和重排接口。
class PolicySearchBackend(Protocol):
    # 将输入文本转换为符合政策向量维度的归一化向量。
    def embed(self, text: str) -> list[float]:
        # region 声明文本编码及向量返回类型
        ...
        # endregion

    # 按检索范围和查询向量召回当前版本的政策条目。
    def recall(self, vector: list[float], mode: PolicySearchMode, versions: list[PolicyVersion]) -> list[PolicyEntry]:
        # region 声明候选召回的查询范围和条目类型
        ...
        # endregion

    # 按查询与候选的相关程度重排，返回本轮采用的候选条目。
    def rerank(self, query: str, candidates: list[PolicyEntry]) -> list[PolicyEntry]:
        # region 声明候选重排的输入与返回类型
        ...
        # endregion
