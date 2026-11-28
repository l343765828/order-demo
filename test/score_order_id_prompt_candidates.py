"""Compare the recorded Astra, A and B attempts without sending model requests."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from verify_order_id_extraction_live import ROOT, has_timeout, load_samples, metric_rows, raw_intent, read_completed_run


MIN_EXACT_CORRECT = 116
MIN_CORRECTION_PERCENT = 95


def compare_attempts(first: Path, retry: Path, samples_path: Path) -> dict:
    samples, sample_sha = load_samples(samples_path)
    meta, rows = read_completed_run(first, samples, sample_sha)
    assert [row["id"] for row in rows] == [sample["id"] for sample in samples]
    timed_out = {row["id"] for row in rows if has_timeout(row)}
    retry_samples = [sample for sample in samples if sample["id"] in timed_out]
    retry_meta, retries = read_completed_run(retry, retry_samples, sample_sha)
    assert [row["id"] for row in retries] == [sample["id"] for sample in retry_samples]
    for key in ("version", "source_sha256", "qwen", "benchmark_sha256", "platform", "python", "packages"):
        assert retry_meta[key] == meta[key], f"Retry mismatch: {key}"
    recovered = {row["id"]: row for row in retries if not has_timeout(row)}
    selected = {row["id"]: recovered.get(row["id"], row) for row in rows}
    score = metric_rows(meta, list(selected.values()), samples)
    del score["workflow_latency"], score["intent_http_latency"]

    conflicts = {}
    malformed_ids = {}
    unavailable_responses: dict[str, list[dict[str, str | int | None]]] = {}
    for sample_id, row in selected.items():
        order_id = row["plan_input_order_id"]
        if order_id is not None and not re.fullmatch(r"DEMO-[0-9]{4}", order_id.upper()):
            malformed_ids[sample_id] = order_id
        for call in row["requests"]:
            if not 200 <= call.get("http_status", 0) < 300:
                unavailable_responses.setdefault(sample_id, []).append({
                    "node": call["node"], "http_status": call.get("http_status"),
                })
            if call["node"] != "intent":
                continue
            raw = raw_intent(call)
            status = raw.get("order_id_status")
            fields = (raw.get("order_id"), raw.get("order_id_evidence"))
            if status == "extracted" and not all(isinstance(value, str) and value.strip() for value in fields):
                conflicts[sample_id] = raw
            elif status in ("missing", "ambiguous") and any(value is not None for value in fields):
                conflicts[sample_id] = raw

    counts = score["counts"]
    errors = score["errors_by_id"]
    exact = counts["exact_correct"]
    correction = score["order_id_matches_by_category"]["correction"]
    correction_n = score["sample_counts"]["correction"]
    ordinary_n = sum(sample["fast_path"] == "no" for sample in samples)
    assert (len(samples), counts["exact_n"], correction_n, ordinary_n) == (200, 118, 44, 197)
    gates = {
        "exact": exact >= MIN_EXACT_CORRECT,
        "correction": correction * 100 >= correction_n * MIN_CORRECTION_PERCENT,
        "fabricated_or_truncated": not (counts.get("fabricated", 0) or counts.get("truncated", 0)),
        "ambiguous_authorization": not counts.get("ambiguous_wrong_authorization", 0),
        "ordinary_intent_calls": score["ordinary_intent_call_distribution"] == {1: ordinary_n},
    }
    remaining_timeouts = sorted(timed_out - recovered.keys())
    score.update({
        "gates": gates,
        "regression_gates_met": all(gates.values()),
        "regression_passed": all(gates.values()) and not remaining_timeouts and not unavailable_responses,
        "status_field_conflicts": conflicts,
        "non_four_digit_plan_ids": malformed_ids,
        "plan_error_ids": {key: selected[key]["plan_input_order_id"] for key, labels in errors.items()
                           if {"fabricated", "truncated", "wrong_selection"}.intersection(labels)},
        "first_attempt": metric_rows(meta, rows, samples),
        "retry": metric_rows(retry_meta, retries, retry_samples),
        "recovered_ids": sorted(recovered),
        "remaining_timeout_ids": remaining_timeouts,
        "unavailable_responses": unavailable_responses,
    })
    for label, path, attempt_meta, attempt_rows in (("first", first, meta, rows), ("retry", retry, retry_meta, retries)):
        requests = [call for row in attempt_rows for call in row["requests"]]
        score[f"{label}_run"] = {
            **attempt_meta,
            "ended_at": json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["ended_at"],
            "rows": len(attempt_rows),
            "timeouts": sum(has_timeout(row) for row in attempt_rows),
            "http_statuses": dict(Counter(str(call.get("http_status", "no_response")) for call in requests)),
            "served_models": sorted({call["served_model"] for call in requests if call.get("served_model")}),
        }
    return score


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, default=ROOT / "evidence")
    args = parser.parse_args()
    samples = ROOT / "test/order_id_four_digit_live.tsv"
    results = {}
    for name, directory, prefix in (
        ("astra", ROOT / "evidence", "order-id-four-digit-checklist"),
        ("a", args.candidate_dir, "order-id-four-digit-candidate-a"),
        ("b", args.candidate_dir, "order-id-four-digit-candidate-b"),
    ):
        score = compare_attempts(directory / f"{prefix}-live.jsonl", directory / f"{prefix}-retry.jsonl", samples)
        results[name] = score
        if name != "astra":
            assert score["revision"].endswith(f"+astra-{results['astra']['source_sha256']}+candidate-{name}")
            assert score["first_run"]["revision"] == score["retry_run"]["revision"]
            astra_errors = results["astra"]["errors_by_id"]
            score["new_missed_vs_astra"] = [sample_id for sample_id, labels in score["errors_by_id"].items()
                                             if "missed" in labels and "missed" not in astra_errors.get(sample_id, [])]
    for stage in ("first_run", "retry_run"):
        for key in ("qwen", "platform", "python", "packages", "workers", "benchmark_sha256"):
            assert results["a"][stage][key] == results["b"][stage][key], f"Candidate mismatch: {stage}.{key}"
    assert len({score["source_sha256"] for score in results.values()}) == 3, "Source fingerprints must differ"
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
