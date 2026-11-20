"""Named contracts used by the Host and the side-effect-free probe server."""

from pydantic import BaseModel, ConfigDict, Field


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str
    trace_id: str = Field(min_length=1, json_schema_extra={"x-mcp-header": "trace-id"})


class EchoResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str
    trace_id: str = Field(min_length=1)
    server_pid: int = Field(gt=0)
    request_id: str = Field(min_length=1)
