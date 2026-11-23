"""咨询政策证据与引用的数据契约。"""
from typing import Annotated

from pydantic import BeforeValidator, Field, model_validator

from model.base import StrictModel
from model.base import status_schema
from model.policy.status import PolicyEvidenceStatus


# 模型选择实际候选的序号，缺少依据时可建议一次正式条款补查。
class PolicySelection(StrictModel):
    # 模型从 Host 提供的候选序号中选出的条目，未选中时返回 None。
    source_index: int | None
    # 模型建议的正式条款补查文本，没有补查建议时返回 None。
    retry_query: str | None


# 保存回答所引用的单条正式政策及本轮检索事件。
class PolicyCitation(StrictModel):
    # 召回被引用条款的政策检索事件编号，用于追溯到本轮实际证据。
    event_id: str = Field(min_length=1)
    # 被引用条款所属的政策文档编号。
    document_id: str = Field(min_length=1)
    # 被引用条款所属的文档版本，用于识别过期依据。
    version: str = Field(min_length=1)
    # 被引用的正式条款编号，用于定位文档中的具体规则。
    clause_id: str = Field(min_length=1)
    # 被引用的政策原文，核验时必须能在实际检索到的条款中找到。
    excerpt: str = Field(min_length=1)


# 保存原始问题、检索查询及拟采用的正式政策依据。
class PolicyEvidence(StrictModel):
    # 本轮等待回答的消费者问题，补充订单号时沿用原资格咨询问题。
    question: str = Field(min_length=1)
    # 当前步骤实际使用的检索文本，可能根据订单事实或补查需要改写。
    query: str = Field(min_length=1)
    # 本轮是否已找到支持答复的政策依据，声明充分时必须提供引用。
    status: Annotated[PolicyEvidenceStatus, BeforeValidator(PolicyEvidenceStatus), status_schema(PolicyEvidenceStatus)]
    # 拟用于本轮答复的正式条款引用，逐条关联文档版本和本轮检索事件。
    citations: list[PolicyCitation]

    # 充分证据必须明确包含至少一条引用。
    @model_validator(mode='after')
    def require_citations(self):
        # region 声明依据充分时至少提供一条正式政策引用
        if (
            # 该结果声明退款政策依据充分。
            self.status is PolicyEvidenceStatus.SUFFICIENT
            # 结果没有附上可支持答复的正式条款引用。
            and not self.citations
        ):
            raise ValueError('充分证据必须包含正式条款')
        return self
        # endregion
