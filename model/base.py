"""跨业务共用的数据模型基础。"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, WithJsonSchema


# 为业务数据模型统一启用严格类型校验并禁止额外字段。
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# 保留原有内联字符串枚举 Schema，供按完整 Schema 绑定工具的 MCP 客户端使用。
def status_schema(status: type[StrEnum], title: str = "Status") -> WithJsonSchema:

    # region 保留原有内联字符串枚举 Schema，供按完整 Schema 绑定工具的 MCP 客户端使用
    return WithJsonSchema({"type": "string", "enum": [member.value for member in status], "title": title})
    # endregion
