"""Reads runs.jsonl for the review server's Results tab — one run per
entry, newest first, each carrying its run_id (used to scope /api/jobs)."""
import json
import os


def _all_jsonl(path) -> list[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _normalize_run(r: dict) -> dict:
    """Backfill derived fields for records written before finalize.py computed
    them (stage0_passed/stage1_pass/stage1_fail/stage2_pass/stage2_fail)."""
    by_status = r.get("by_status", {})
    r.setdefault("stage1_fail", by_status.get("stage1_fail", 0))
    r.setdefault("stage2_fail", by_status.get("stage2_fail", 0))
    r.setdefault("stage2_pass", r.get("passed", 0))
    r.setdefault("stage1_pass", r.get("total_evaluated", 0) - r.get("stage1_fail", 0))
    dropped = r.get("stage0_dropped") or {}
    scraped = r.get("scraped_count")
    r.setdefault("stage0_passed", (scraped - sum(dropped.values())) if scraped is not None else None)
    r.setdefault("ts_start", None)
    r.setdefault("already_seen_skipped", None)
    r.setdefault("criteria_snapshot", None)
    r.setdefault("run_id", None)  # records written before the run_id column/field existed
    return r


def list_runs(project_dir: str = ".") -> list[dict]:
    """Newest first. Runs without a run_id (pre-migration records that were
    never backfilled) are still returned but can't be joined to seen.db —
    the caller should treat a null run_id as un-selectable for job drilldown."""
    runs = [_normalize_run(r) for r in _all_jsonl(f"{project_dir}/runs.jsonl")]
    runs.reverse()
    return runs
