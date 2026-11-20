"""只读演示进程共享的身份配置和结构化事件格式。"""

import json
import os
import sys
from config import load_config

CONSUMER_ID = "DEMO-USER-01"
# 从应用配置中读取 MCP 服务使用的只读凭据。
def read_token() -> str:
    return load_config().mcp.read_token


# 向标准错误输出附带进程编号的结构化事件。
def log_event(event: str, **fields) -> None:
    print(json.dumps({"event": event, "pid": os.getpid(), **fields}, ensure_ascii=False),
          file=sys.stderr, flush=True)
