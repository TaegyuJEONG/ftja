"""Last step of /ftja-run: takes the full per-job outcome list (every job
touched this run, whatever stage it stopped at), and:
  1. records all of them in seen.db (so nothing gets re-judged tomorrow)
  2. writes digest-YYYY-MM-DD.md for the ones that passed all stages
  3. writes rejected-YYYY-MM-DD.md for the ones that failed Stage1/2, with
     their free-text reasoning (Stage0 fails aren't included — there are no
     stage0_fail entries, they never left filter_stage0.py's dropped counts)
  4. fires a macOS notification and appends run.log — always, even on 0 passed
  5. appends a structured record to runs.jsonl (for the review server's Results tab)

Input: a JSON file, a list of objects shaped like:
{
  "job_url": str, "title": str, "company": str, "location": str,
  "status": "stage0_fail" | "stage1_fail" | "stage2_fail" | "passed",
  "stage_reached": 0 | 1 | 2,
  "verdict": str,               # optional, free text
  "evidence_sentences": [str],  # required when status == "passed"
  "reasoning": str,             # required when status == "passed"
  "duplicate_job_urls": [str],  # optional — other LinkedIn URLs for the same
                                 # JD (ftja.filter_stage0.dedupe_by_content).
                                 # Each gets marked seen with the same verdict;
                                 # digest/rejected still write ONE entry, noting
                                 # the repost count.
  "t1_matched_keywords": [str], # optional — which tier1 keyword(s) hit this
                                 # job's Stage0 blocks (ftja.text_blocks.
                                 # find_matched_with_context's matched_keywords)
  "t1_matched_sentences": [str] # optional — the actual [T1] sentence(s) that
                                 # matched. Both persisted to seen.db so
                                 # keyword-matching quality can be spot-checked
                                 # later (`sqlite3 seen.db "SELECT title,
                                 # t1_matched_keywords, t1_matched_sentences
                                 # FROM seen_jobs"`) without a separate report
                                 # file — seen.db already is the per-job ledger.
}

Optional --stats file (JSON): {"ts_start": iso8601, "duration_seconds": float,
"total_tokens": int, "scraped_count": int, "already_seen_skipped": int,
"stage0_dropped": {"language": int, "no_tier1_hit": int, "exclude_keyword": int},
"criteria_snapshot": {"search_terms": [...], "location": str, "is_remote": bool,
"hours_old": int, "results_wanted": int}} — whatever the orchestrating skill can
report. Fields it can't measure are just omitted; finalize never fabricates a
number it wasn't given. `criteria_snapshot` matters because criteria.json can
change between runs (via /ftja-tune) — a run record should show what was
actually used AT THAT TIME, not whatever criteria.json currently holds.
"""
import argparse
import json
import sys
from datetime import datetime, timezone

from ftja.state import connect, mark_seen, rubric_version as get_rubric_version
from ftja.notify import notify

DEFAULT_DB = "seen.db"
DEFAULT_LOCK = ".ftja.lock"


_REVIEW_NOTE = (
    "Don't fill in application status/reason by hand here — run "
    "`venv/bin/python -m ftja.server` and open http://127.0.0.1:8765 instead. "
    "It writes straight into seen.db, and `/ftja-review` reads the reasons to "
    "propose criteria.json/rubric.md changes."
)


def write_digest(passed: list[dict], out_dir: str = ".") -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = f"{out_dir}/digest-{date_str}.md"
    lines = [f"# FTJA digest — {date_str}", "", f"{len(passed)} passed", "", _REVIEW_NOTE, ""]
    for job in passed:
        dup_note = f" (same posting, reposted {len(job['duplicate_job_urls'])}x)" if job.get("duplicate_job_urls") else ""
        lines.append(f"## {job.get('title', '(no title)')} — {job.get('company', '')}{dup_note}")
        lines.append(f"{job.get('job_url', '')}")
        lines.append("")
        lines.append(f"**Reasoning**: {job.get('reasoning', '')}")
        lines.append("")
        lines.append("**Evidence**:")
        for sent in job.get("evidence_sentences", []):
            lines.append(f"> {sent}")
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def write_rejected(rejected: list[dict], out_dir: str = ".") -> str:
    """Stage1/2 fail reasoning has no fixed category the way Stage0's does (it's
    free-text LLM output, not a code branch) — rather than force an artificial
    taxonomy onto it, just keep the per-job reasoning readable after the run
    ends. Without this, a rejected job's reasoning existed only in the async
    Agent-call notification during the run and was gone once finalize ran."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = f"{out_dir}/rejected-{date_str}.md"
    lines = [f"# FTJA rejected — {date_str}", "", f"{len(rejected)} failed", ""]
    for job in rejected:
        stage = job.get("stage_reached", "?")
        dup_note = f", same posting reposted {len(job['duplicate_job_urls'])}x" if job.get("duplicate_job_urls") else ""
        lines.append(f"## {job.get('title', '(no title)')} — {job.get('company', '')} (failed at Stage{stage}{dup_note})")
        lines.append(f"{job.get('job_url', '')}")
        lines.append("")
        lines.append(f"**Reason**: {job.get('reasoning', '(no record)')}")
        lines.append("")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def append_log(message: str, log_path: str = "run.log"):
    ts = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a") as f:
        f.write(f"[{ts}] {message}\n")


def append_run_record(results: list[dict], stats: dict, run_id: str, out_dir: str = "."):
    """Append one structured JSON line to runs.jsonl — the source the review
    server's Results tab reads (full file, not just the last line). run_id
    joins this record to the seen_jobs rows finalize() wrote in the same call."""
    by_status = {}
    for r in results:
        by_status[r.get("status", "unknown")] = by_status.get(r.get("status", "unknown"), 0) + 1

    stage1_fail = by_status.get("stage1_fail", 0)
    stage2_fail = by_status.get("stage2_fail", 0)
    passed = by_status.get("passed", 0)
    total_evaluated = len(results)

    stage0_dropped = stats.get("stage0_dropped") or {}
    scraped_count = stats.get("scraped_count")
    stage0_passed = (scraped_count - sum(stage0_dropped.values())) if scraped_count is not None else None

    record = {
        "run_id": run_id,
        "ts_end": datetime.now(timezone.utc).isoformat(),
        "total_evaluated": total_evaluated,
        "passed": passed,
        "by_status": by_status,
        # derived stage-level breakdown, so the review server doesn't recompute it
        "stage0_passed": stage0_passed,
        "stage1_pass": total_evaluated - stage1_fail,
        "stage1_fail": stage1_fail,
        "stage2_pass": passed,
        "stage2_fail": stage2_fail,
        **stats,  # ts_start, duration_seconds, total_tokens, scraped_count,
                  # already_seen_skipped, stage0_dropped, criteria_snapshot —
                  # whatever the skill measured
    }
    with open(f"{out_dir}/runs.jsonl", "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def finalize(results: list[dict], db_path: str = DEFAULT_DB, out_dir: str = ".", stats: dict | None = None) -> dict:
    stats = stats or {}
    passed = [r for r in results if r.get("status") == "passed"]
    rejected = [r for r in results if r.get("status") in ("stage1_fail", "stage2_fail")]
    rv = get_rubric_version(f"{out_dir}/rubric.md")
    # ts_start is already unique+sortable per run; fall back to "now" if the
    # orchestrating skill didn't measure it (stats is best-effort throughout).
    run_id = stats.get("ts_start") or datetime.now(timezone.utc).isoformat()

    with connect(db_path) as conn:
        for r in results:
            for url in [r["job_url"], *r.get("duplicate_job_urls", [])]:
                mark_seen(
                    conn,
                    job_url=url,
                    status=r.get("status", "unknown"),
                    stage_reached=r.get("stage_reached", 0),
                    verdict=r.get("verdict", ""),
                    title=r.get("title", ""),
                    company=r.get("company", ""),
                    location=r.get("location", ""),
                    t1_matched_keywords=r.get("t1_matched_keywords"),
                    t1_matched_sentences=r.get("t1_matched_sentences"),
                    reasoning=r.get("reasoning", ""),
                    evidence_sentences=r.get("evidence_sentences"),
                    rubric_version=rv,
                    run_id=run_id,
                )

    digest_path = write_digest(passed, out_dir=out_dir)  # always write, even 0 passed -> no silent gaps
    rejected_path = write_rejected(rejected, out_dir=out_dir)
    summary = f"run complete: {len(results)} evaluated, {len(passed)} passed -> {digest_path} ({len(rejected)} rejected -> {rejected_path})"
    if "duration_seconds" in stats:
        summary += f" ({stats['duration_seconds']:.0f}s"
        summary += f", {stats['total_tokens']} tokens)" if "total_tokens" in stats else ")"
    append_log(summary, log_path=f"{out_dir}/run.log")
    append_run_record(results, stats, run_id, out_dir=out_dir)
    notify("FTJA", f"{len(passed)} new postings" if passed else "No postings passed today")

    return {"total": len(results), "passed": len(passed), "digest_path": digest_path,
            "rejected_path": rejected_path, "run_id": run_id}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="path to results JSON (list of per-job outcomes)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--stats", default="", help="optional path to a stats JSON (duration_seconds, total_tokens, ...)")
    args = ap.parse_args()

    with open(args.results) as f:
        results = json.load(f)

    stats = {}
    if args.stats:
        with open(args.stats) as f:
            stats = json.load(f)

    summary = finalize(results, db_path=args.db, out_dir=args.out_dir, stats=stats)
    print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
