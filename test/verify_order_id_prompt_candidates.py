"""Run the two specified prompt edits through the existing live benchmark."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from verify_order_id_extraction_live import ROOT, SOURCE_DIRS, source_digest


INTENT = Path("implementations/refund/intent.py")
ASTRA_DATA = '''            "对话数据（最后一项是当前消费者的业务原文）：\\n"
            + json.dumps([*(m.model_dump(mode="json") for m in history[:-1]),
                          ExchangeMessage(role=ConversationRole.USER, content=current).model_dump(mode="json")],
                         ensure_ascii=False)'''
A_DATA = '''            "【历史对话】\\n"
            + json.dumps([m.model_dump(mode="json") for m in history[:-1]], ensure_ascii=False)
            + "\\n\\n【本轮消费者业务原文】\\n"
            "order_id 和 order_id_evidence 只能从下面这一条消费者原文中提取。\\n"
            + json.dumps(current, ensure_ascii=False)'''
B_EXAMPLES = '''示例一
消费者原文：帮我查 demo-0042 的发货状态
输出：
{
  "intent": "order_question",
  "order_goal": "status",
  "order_id_status": "extracted",
  "order_id": "demo-0042",
  "order_id_evidence": "帮我查 demo-0042 的发货状态",
  "continues_eligibility_question": false
}

示例二
消费者原文：查一下发货了没
输出：
{
  "intent": "order_question",
  "order_goal": "status",
  "order_id_status": "missing",
  "order_id": null,
  "order_id_evidence": null,
  "continues_eligibility_question": false
}

'''


def copy_application(target: Path) -> str:
    source_sha = source_digest(ROOT)
    paths = list(ROOT.glob("*.py"))
    for folder in SOURCE_DIRS:
        paths.extend(path for path in (ROOT / folder).rglob("*") if path.suffix in {".py", ".sql"})
    for path in paths:
        copy = target / path.relative_to(ROOT)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
    assert source_digest(target) == source_sha, "The isolated copy differs from the working application"
    return source_sha


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--samples", type=Path, default=ROOT / "test/order_id_four_digit_live.tsv")
    parser.add_argument("--config", type=Path, default=ROOT / "config/local.json")
    args = parser.parse_args()
    evidence = args.output_dir.resolve()
    benchmark = ROOT / "test/verify_order_id_extraction_live.py"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    with TemporaryDirectory(prefix="order-id-prompt-") as temp:
        astra = Path(temp) / "astra"
        astra_sha = copy_application(astra)
        with (ROOT / "evidence/order-id-four-digit-checklist-live.jsonl").open(encoding="utf-8") as log:
            expected_astra = json.loads(log.readline())["run"]["source_sha256"]
        assert astra_sha == expected_astra, "The working application differs from the recorded Astra source"
        original = (astra / INTENT).read_text(encoding="utf-8")
        assert original.count(ASTRA_DATA) == 1
        candidates = {"a": A_DATA, "b": f"            {B_EXAMPLES!r}\n{ASTRA_DATA}"}
        for name, data in candidates.items():
            source = Path(temp) / name
            shutil.copytree(astra, source)
            candidate = original.replace(ASTRA_DATA, data, 1)
            compile(candidate, str(source / INTENT), "exec")
            (source / INTENT).write_text(candidate, encoding="utf-8", newline="\n")

        for name in candidates:
            run = [sys.executable, "-X", "utf8", str(benchmark), "run", "--source", str(Path(temp) / name),
                   "--config", str(args.config.resolve()), "--samples", str(args.samples.resolve()),
                   "--version", "current", "--revision", f"{head}+astra-{astra_sha}+candidate-{name}"]
            first = evidence / f"order-id-four-digit-candidate-{name}-live.jsonl"
            retry = evidence / f"order-id-four-digit-candidate-{name}-retry.jsonl"
            subprocess.run([*run, "--workers", "4", "--output", str(first)], cwd=ROOT, check=True)
            subprocess.run([*run, "--workers", "2", "--output", str(retry), "--retry-from", str(first)],
                           cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
