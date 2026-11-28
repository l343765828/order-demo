"""Replay the final prompt through the existing isolated Qwen benchmark."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from verify_order_id_extraction_live import ROOT, source_digest
from verify_order_id_prompt_candidates import ASTRA_DATA, INTENT, copy_application
from verify_order_id_source_prompt import FOCUSED_IDS, SAMPLES


PROMPT = ROOT / "evidence/order-id-final-prompt.txt"
PENDING_SLOT = "{{是或否}}"
DATA_SLOT = "{{按时间顺序序列化的对话消息_JSON数组}}"
PROMPT_START = '        prompt = (\n            "判断当前消费者的业务意图和订单号。'


def replace_prompt(original: str, template: str) -> str:
    assert original.count(PROMPT_START) == original.count(ASTRA_DATA) == 1
    assert template.count(PENDING_SLOT) == template.count(DATA_SLOT) == 1
    assert template.endswith(DATA_SLOT + "\n")
    intro, rest = template.removesuffix(DATA_SLOT + "\n").split(PENDING_SLOT)
    serializer = ASTRA_DATA.split("\n", 1)[1]
    replacement = (
        "        prompt = (\n"
        f"            {intro!r}\n"
        '            + ("是" if pending else "否")\n'
        f"            + {rest!r}\n"
        f"{serializer}"
    )
    start = original.index(PROMPT_START)
    end = original.index(ASTRA_DATA, start) + len(ASTRA_DATA)
    return original[:start] + replacement + original[end:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, default=PROMPT, help="Use a separate prompt template")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--full", action="store_true", help="Use all 200 regression samples")
    selection.add_argument("--sample-id", action="append", help="Use only the specified samples")
    parser.add_argument("--score", action="store_true", help="Recompute metrics from existing logs")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    stage = "full" if args.full else "focused"
    if args.sample_id:
        stage = "selected"
    first, retry = (output_dir / f"order-id-final-prompt-{stage}-{phase}.jsonl" for phase in ("live", "retry"))
    benchmark = ROOT / "test/verify_order_id_extraction_live.py"
    command = [sys.executable, "-X", "utf8", str(benchmark)]
    sample_options = ["--samples", str(SAMPLES)]
    if not args.full:
        for sample_id in args.sample_id or FOCUSED_IDS:
            sample_options.extend(["--sample-id", sample_id])

    if args.score:
        subprocess.run([*command, "score", str(first), "--retry", str(retry), *sample_options],
                       cwd=ROOT, check=True)
        return

    assert not first.exists() and not retry.exists(), "Use a new output directory"
    output_dir.mkdir(parents=True, exist_ok=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with TemporaryDirectory(prefix="order-id-final-prompt-") as temporary:
        source = Path(temporary)
        astra_sha = copy_application(source)
        with (ROOT / "evidence/order-id-source-prompt-focused-astra-live.jsonl").open(encoding="utf-8") as log:
            assert astra_sha == json.loads(log.readline())["run"]["source_sha256"]
        intent = source / INTENT
        candidate = replace_prompt(intent.read_text(encoding="utf-8"), args.prompt.read_text(encoding="utf-8"))
        compile(candidate, str(intent), "exec")
        intent.write_text(candidate, encoding="utf-8", newline="\n")
        assert source_digest(source) != astra_sha

        run = [*command, "run", "--source", str(source), "--version", "current",
               "--revision", f"{head}+astra-{astra_sha}+final-prompt", *sample_options]
        subprocess.run([*run, "--workers", "4", "--output", str(first)], cwd=ROOT, check=True)
        subprocess.run([*run, "--workers", "2", "--output", str(retry), "--retry-from", str(first)],
                       cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
