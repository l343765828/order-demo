"""输出演示进程的结构化事件。"""

import json
import os
import sys
from pathlib import Path


# 向标准错误输出附带进程编号的结构化事件。
def log_event(event: str, **fields) -> None:

    # region 向标准错误输出附带进程编号的结构化事件
    print(json.dumps({"event": event, "pid": os.getpid(), **fields}, ensure_ascii=False),
          file=sys.stderr, flush=True)
    # endregion


# 独占创建一条结构化事件并同步到本地持久存储，无法确认时抛出异常。
def persist_event(directory: Path, event_id: str, event: str, **fields) -> None:

    # region 写入单条独立事件文件并确认同步，不覆盖或重发已存在的事件
    if not directory.is_dir():
        raise OSError("事件目录不可用")
    with (directory / f"{event_id}.jsonl").open("xb") as stream:
        stream.write((json.dumps({"event": event, "pid": os.getpid(), **fields}, ensure_ascii=False) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    # endregion

    # region 平台支持时同步目录项，确保文件名和事件内容均可恢复
    if hasattr(os, "O_DIRECTORY"):
        descriptor = os.open(directory, getattr(os, "O_DIRECTORY") | os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    # endregion
