"""使用隔离配置在子进程中运行应用入口。"""

import runpy
import sys
from pathlib import Path


def application_command(module: str, config_path: Path, *app_args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), module, str(config_path), *app_args]


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from config import use_config

    launcher, module, config_path, *app_args = sys.argv
    use_config(config_path)
    sys.argv = [launcher, *app_args]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
