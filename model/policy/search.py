"""政策检索请求和候选来源契约。"""
import hashlib
import math
from typing import Annotated, Any, Self
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from config.limits import PolicyLimits, RefundLimits
from model.base import status_schema
from model.policy.status import PolicyEntryType, PolicySearchMode, PolicySearchStatus


# 检索文本及由 Host 指定的范围，供工具签名和请求模型使用。
PolicyQueryText = Annotated[str, Field(strict=True, min_length=1, max_length=RefundLimits.MAX_POLICY_QUERY_CHARS)]
PolicySearchScope = Annotated[PolicySearchMode, BeforeValidator(PolicySearchMode), status_schema(PolicySearchMode, 'Mode')]
# 目录修订号由 MCP 分配，Host 在同一次调查的后续检索传回。
PolicyCatalogRevision = Annotated[int, Field(strict=True, gt=0)]


# 由文档、版本和条目编号生成稳定的政策条目主键。
def policy_chunk_id(document_id: str, version: str, clause_id: str) -> str:

    # region 由文档、版本和条目编号生成稳定的政策条目主键
    return hashlib.sha256(f'{document_id}:{version}:{clause_id}'.encode('utf-8')).hexdigest()
    # endregion


# 模型可提出查询，但不能选择检索范围。
class PolicyQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # 子 Agent 提交的政策查询文本，转发前由 Host 与本步骤的查询核对。
    query: PolicyQueryText


# 限制调用方只能提出查询与由 Host 控制的检索范围。
class SearchPolicyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # MCP 用于召回和重排政策候选的查询文本。
    query: PolicyQueryText
    # Host 指定的检索范围，可允许常见问题或只召回正式条款。
    mode: PolicySearchScope
    # 首次检索省略，后续检索使用首次成功回执提供的修订号。
    catalog_revision: PolicyCatalogRevision | None = None


# 返回单条重排候选的政策来源和原文。
class PolicyCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # 政策来源文档的稳定编号，用于核验候选和引用出自同一文档。
    document_id: str
    # 来源文档的标题，用于在答复中标明引用出处。
    title: str
    # 候选条目所属的政策文档版本，用于核验条款仍属于当前发布版本。
    version: str
    # 来源文档内的条目编号，用于定位正式条款或常见问题。
    clause_id: str
    # 区分正式条款与常见问题，用于筛选检索范围和核验答复依据。
    entry_type: Annotated[PolicyEntryType, BeforeValidator(PolicyEntryType), status_schema(PolicyEntryType, 'Entry Type')]
    # 来源条目的标题与正文，供重排、选答和核对引用原文使用。
    text: str


# 描述政策来源条目和 Milvus 主键，字段长度按 UTF-8 字节计算。
class PolicyEntry(PolicyCandidate):
    # Milvus 条目主键，由文档编号、版本和条目编号共同派生。
    chunk_id: str

    # 校验各字符串字段的存储长度。
    @model_validator(mode='after')
    def check_lengths(self):

        # region 按 UTF-8 字节核对字段长度，并确保主键来自文档编号、版本和条目编号
        if any(len(getattr(self, key).encode('utf-8')) > limit for key, limit in PolicyLimits.FIELD_BYTES.items()):
            raise ValueError('政策条目超过字段字节限制')
        if self.chunk_id != policy_chunk_id(self.document_id, self.version, self.clause_id):
            raise ValueError('政策条目主键与来源不匹配')
        return self
        # endregion


# 描述写入 Milvus 的完整政策记录，向量须为归一化的 1024 维有限数。
class PolicyVectorRecord(PolicyEntry):
    # 政策原文的 1024 维归一化向量，用于在 Milvus 中按语义相似度召回。
    embedding: list[float] = Field(min_length=PolicyLimits.VECTOR_DIMENSIONS, max_length=PolicyLimits.VECTOR_DIMENSIONS)

    # 阻止错误维度、非有限值或非单位向量进入政策集合。
    @field_validator('embedding')
    @classmethod
    def check_embedding(cls, values: list[float]) -> list[float]:

        # region 向量含非有限值或模长超出允许误差时拒绝写入
        if not all(math.isfinite(value) for value in values):
            raise ValueError('政策向量含非有限值')
        norm = math.sqrt(sum(value * value for value in values))
        if (
            # 政策向量的模长无法表示为有限数值。
            not math.isfinite(norm)
            # 模长与归一化目标 1 的偏差超过允许范围。
            or abs(norm - 1) > PolicyLimits.VECTOR_NORM_TOLERANCE
        ):
            raise ValueError('政策向量未归一化')
        return values
        # endregion


# 区分检索失败和成功，并携带检索事件编号。
class PolicySearchResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # 政策检索是否成功完成，供流程判断可否继续核验候选。
    status: Annotated[PolicySearchStatus, BeforeValidator(PolicySearchStatus), status_schema(PolicySearchStatus)]
    # 检索服务分配的事件编号，用于将政策引用追溯到实际检索和候选。
    event_id: str = Field(min_length=1)
    # 按重排顺序返回的政策候选，检索失败或没有候选时为空列表。
    candidates: list[PolicyCandidate] = Field(max_length=PolicyLimits.MAX_RANKED_ENTRIES)
    # 成功回执携带本次检索使用的修订号。旧 checkpoint 可缺少此字段。
    catalog_revision: PolicyCatalogRevision | None = None

    # 将 SDK 接收的检索结果恢复为具名模型和业务枚举。
    @classmethod
    def from_mcp(cls, data: dict[str, Any]) -> Self:

        # region 校验 MCP 回执，成功结果必须携带可用于补查的修订号
        result = cls.model_validate(data)
        if (
            # 本次检索已成功完成，候选列表可能为空。
            result.status is PolicySearchStatus.OK
            # 服务端未返回本轮所用的目录修订号。
            and result.catalog_revision is None
        ):
            raise ValueError('成功检索缺少目录修订号')
        return result
        # endregion

    # 检索失败时禁止将候选误认为可靠结果。
    @model_validator(mode='after')
    def check_failure(self):

        # region 检索失败时只允许空候选列表
        if (
            # 政策检索服务报告本轮失败。
            self.status is PolicySearchStatus.FAILED
            # 失败回执仍携带候选或可能被视为已固定的目录修订号。
            and (
                # 失败回执仍携带了可能被当作有效结果的政策候选。
                self.candidates
                # 失败回执仍声明了目录修订号。
                or self.catalog_revision is not None
            )
        ):
            raise ValueError('检索失败不得返回候选或目录修订号')
        return self
        # endregion

# 接口调用者负责确保办理任务始终使用 formal；模型不得自行放宽范围。
