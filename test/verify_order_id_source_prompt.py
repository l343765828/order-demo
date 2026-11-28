"""Replay Astra and its first-step source clarification through the live workflow."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from verify_order_id_extraction_live import ROOT, source_digest
from verify_order_id_prompt_candidates import INTENT, copy_application


SAMPLES = ROOT / "test/order_id_four_digit_live.tsv"
FOCUSED_IDS = (
    "F047", "F063", "F067", "F121", "F122", "F123", "F124", "F125",
    "F126", "F145", "F147", "F161", "F171", "F196", "F199", "F200",
)
STEP_ONE_START = '            "第一步，只扫描最后一条消费者业务原文中的完整 DEMO- 加四个 ASCII 数字的片段。"\n'
SOURCE_RULE = (
    "对话数组最后一项的 content 是本轮消费者业务原文，之前各项都是历史。"
    "历史可以帮助判断本轮是否在回应资格咨询的补号追问。"
    "填写 order_id 和 order_id_evidence 时，两者都只取自最后一项的 content："
    "从中原样取出选定的完整编号，并从中原样取出支持选单的连续文字。"
    "只出现在历史中的编号不能作为本轮候选。"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--full", action="store_true", help="Use all 200 regression samples")
    parser.add_argument("--score", action="store_true", help="Recompute metrics from existing logs")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    benchmark = ROOT / "test/verify_order_id_extraction_live.py"
    command = [sys.executable, "-X", "utf8", str(benchmark)]
    sample_options = ["--samples", str(SAMPLES)]
    if not args.full:
        for sample_id in FOCUSED_IDS:
            sample_options.extend(["--sample-id", sample_id])

    run_names = ("astra", "source")
    stem = "order-id-source-prompt-full" if args.full else "order-id-source-prompt-focused"
    outputs = {
        name: tuple(output_dir / f"{stem}-{name}-{phase}.jsonl" for phase in ("live", "retry"))
        for name in run_names
    }
    if args.score:
        for name, (first, retry) in outputs.items():
            print(name, flush=True)
            subprocess.run([*command, "score", str(first), "--retry", str(retry), *sample_options],
                           cwd=ROOT, check=True)
        return

    assert not any(path.exists() for paths in outputs.values() for path in paths), "Use a new output directory"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    with TemporaryDirectory(prefix="order-id-source-prompt-") as directory:
        astra = Path(directory) / "astra"
        astra_sha = copy_application(astra)
        with (ROOT / "evidence/order-id-four-digit-checklist-live.jsonl").open(encoding="utf-8") as log:
            assert astra_sha == json.loads(log.readline())["run"]["source_sha256"]
        source = Path(directory) / "source"
        shutil.copytree(astra, source)
        original = (astra / INTENT).read_text(encoding="utf-8")
        assert original.count(STEP_ONE_START) == 1
        added = f'            "{SOURCE_RULE}"\n'
        candidate = original.replace(STEP_ONE_START, STEP_ONE_START + added, 1)
        compile(candidate, str(source / INTENT), "exec")
        (source / INTENT).write_text(candidate, encoding="utf-8", newline="\n")
        assert source_digest(source) != astra_sha

        for name, (first, retry) in outputs.items():
            print(f"Running {name}", flush=True)
            run = [*command, "run", "--source", str(Path(directory) / name),
                   "--version", "current", "--revision", f"{head}+astra-{astra_sha}+source-prompt-{name}",
                   *sample_options]
            subprocess.run([*run, "--workers", "4", "--output", str(first)], cwd=ROOT, check=True)
            subprocess.run([*run, "--workers", "2", "--output", str(retry), "--retry-from", str(first)],
                           cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
