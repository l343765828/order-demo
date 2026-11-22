"""输出演示进程的结构化事件。"""

import json
import os
import sys


# 向标准错误输出附带进程编号的结构化事件。
def log_event(event: str, **fields) -> None:
    # region 向标准错误输出附带进程编号的结构化事件
    print(json.dumps({"event": event, "pid": os.getpid(), **fields}, ensure_ascii=False),
          file=sys.stderr, flush=True)
    # endregion
