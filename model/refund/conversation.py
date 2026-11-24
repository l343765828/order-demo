"""退款会话归属和消息原文的持久化记录。"""

from datetime import datetime

from pydantic import Field

from config.limits import RefundLimits
from model.base import StrictModel
from model.refund.flow import ExchangeMessage
from model.refund.submission import RefundIdentifier


# 表示退款会话表的一行，供读取边界核对会话归属。
class ConversationSessionRecord(StrictModel):
    # 会话主键，也是 LangGraph 的线程编号。
    session_id: RefundIdentifier
    # 会话记录中的消费者编号，遗留空字符串与当前可信消费者不匹配。
    consumer_id: str = Field(max_length=RefundLimits.MAX_REFUND_IDENTIFIER_CHARS)
    # 会话创建时间，按 UTC 保存。
    created_at: datetime
    # 最近一次成功追加原文的时间，按 UTC 保存。
    updated_at: datetime


# 表示原文表的一行，复用原有会话角色与正文契约。
class ConversationMessageRecord(ExchangeMessage):
    # 会话编号，与序号共同标识一条原文。
    session_id: RefundIdentifier
    # 会话内从 1 开始递增的原文序号。
    sequence: int = Field(gt=0)
    # 原文写入时间，按 UTC 保存。
    created_at: datetime


# 表示数据库计算出的当前会话最末原文序号。
class ConversationSequenceRow(StrictModel):
    # 最末原文的序号，空会话取 0。
    last_sequence: int = Field(ge=0)
