"""政策检索请求和候选来源契约。"""
import hashlib
import math
from typing import Annotated
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from model.status import PolicyEntryType, PolicySearchMode, PolicySearchStatus, status_schema


# 由文档、版本和条目编号生成稳定的政策条目主键。
def policy_chunk_id(document_id: str, version: str, clause_id: str) -> str:
    return hashlib.sha256(f'{document_id}:{version}:{clause_id}'.encode('utf-8')).hexdigest()


# 模型可提出查询，但不能选择检索范围。
class PolicyQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(min_length=1, max_length=2000)


# 限制调用方只能提出查询与由 Host 控制的检索范围。
class SearchPolicyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    query: str = Field(min_length=1, max_length=2000)
    mode: Annotated[PolicySearchMode, BeforeValidator(PolicySearchMode), status_schema(PolicySearchMode, 'Mode')]


# 返回单条重排候选的政策来源和原文。
class PolicyCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    document_id: str
    title: str
    version: str
    clause_id: str
    entry_type: Annotated[PolicyEntryType, BeforeValidator(PolicyEntryType), status_schema(PolicyEntryType, 'Entry Type')]
    text: str


# 描述政策来源条目和 Milvus 主键，字段长度按 UTF-8 字节计算。
class PolicyEntry(PolicyCandidate):
    chunk_id: str

    # 校验各字符串字段的存储长度。
    @model_validator(mode='after')
    def check_lengths(self):
        limits = {'chunk_id': 64, 'document_id': 64, 'title': 256, 'version': 32,
                  'clause_id': 32, 'entry_type': 16, 'text': 4096}
        if any(len(getattr(self, key).encode('utf-8')) > limit for key, limit in limits.items()):
            raise ValueError('政策条目超过字段字节限制')
        if self.chunk_id != policy_chunk_id(self.document_id, self.version, self.clause_id):
            raise ValueError('政策条目主键与来源不匹配')
        return self


# 描述写入 Milvus 的完整政策记录，向量须为归一化的 1024 维有限数。
class PolicyVectorRecord(PolicyEntry):
    embedding: list[float] = Field(min_length=1024, max_length=1024)

    # 阻止错误维度、非有限值或非单位向量进入政策集合。
    @field_validator('embedding')
    @classmethod
    def check_embedding(cls, values: list[float]) -> list[float]:
        if not all(math.isfinite(value) for value in values):
            raise ValueError('政策向量含非有限值')
        norm = math.sqrt(sum(value * value for value in values))
        if not math.isfinite(norm) or abs(norm - 1) > 0.0001:
            raise ValueError('政策向量未归一化')
        return values


# 区分检索失败和成功，并携带检索事件编号。
class PolicySearchResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    status: Annotated[PolicySearchStatus, BeforeValidator(PolicySearchStatus), status_schema(PolicySearchStatus)]
    event_id: str
    candidates: list[PolicyCandidate] = Field(max_length=3)

    # 检索失败时禁止将候选误认为可靠结果。
    @model_validator(mode='after')
    def check_failure(self):
        if self.status is PolicySearchStatus.FAILED and self.candidates:
            raise ValueError('检索失败不得返回候选')
        return self

# 接口调用者负责确保办理任务始终使用 formal；模型不得自行放宽范围。
