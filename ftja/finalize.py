"""Last step of /ftja-run: takes the full per-job outcome list (every job
touched this run, whatever stage it stopped at), and:
  1. records all of them in seen.db (so nothing gets re-judged tomorrow)
  2. writes digest-YYYY-MM-DD.md for the ones that passed all stages, plus
     the ones Stage 2 could not settle ("to review")
  3. writes rejected-YYYY-MM-DD.md for the ones that failed Stage1/2, with
     their reasoning. Stage0 drops are only counts, except the
     language-requirement drops: pass filter_stage0's --dropped-out file as
     --stage0-dropped and they are listed too, each with the sentence that
     triggered it. They are NOT written to seen.db, so they are re-checked
     on the next run.
  4. fires a macOS notification and appends run.log — always, even on 0 passed
  5. appends a structured record to runs.jsonl (for the review server's Results tab)

Input: a JSON file, a list of objects shaped like:
{
  "job_url": str, "title": str, "company": str, "location": str,
  "status": "stage0_fail" | "stage1_fail" | "stage2_fail" | "review" | "passed",
  "stage_reached": 0 | 1 | 2,
  "stage2": {"criteria": [{"id", "result", "quote", "why"}],
             "company_line", "employer_requirements", "notes"},
                                 # a Stage 2 job: the subagent's answer,
                                 # exactly as it returned it. Leave `status`
                                 # out for these — finalize checks the quotes
                                 # against the JD and computes
                                 # passed / review / stage2_fail itself
                                 # (ftja.verdict). Needs --criteria and --jobs.
  "verdict": str,               # optional, free text (Stage 1 jobs)
  "reasoning": str,             # Stage 1 fails: why. Stage 2 jobs: written
                                 # by finalize from the criteria.
  "duplicate_job_urls": [str],  # optional — other LinkedIn URLs for the same
                                 # JD (ftja.filter_stage0.dedupe_by_content).
                                 # Each gets marked seen with the same verdict;
                                 # digest/rejected still write ONE entry, noting
                                 # the repost count.
  "t1_blocks": [{"sentence", "keywords", "why"}],
                                 # optional — Stage 1's one-line reading of
                                 # each keyword sentence (ftja.live writes it)
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
"stage0_dropped": {"language": int, "no_tier1_hit": int, "exclude_keyword": int,
"language_requirement": int, "duplicate_content": int},
"criteria_snapshot": {"search_terms": [...], "location": str, "is_remote": bool,
"hours_old": int, "results_wanted": int}} — whatever the orchestrating skill can
report. Fields it can't measure are just omitted; finalize never fabricates a
number it wasn't given. `criteria_snapshot` matters because criteria.json can
change between runs (via /ftja-tune) — a run record should show what was
actually used AT THAT TIME, not whatever criteria.json currently holds.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from ftja.state import connect, mark_seen, rubric_version as get_rubric_version
from ftja.notify import notify
from ftja.verdict import DEFAULT_CRITERIA_PATH, STATUS_FOR_VERDICT, clean_extras, judge, load_criteria, summarize

_RESULT_LABELS = {"met": "Met", "unclear": "Unclear", "not_met": "Not met",
                  "triggered": "Triggered", "not_applicable": "Not applicable"}
_KIND_TITLES = (("pass", "Pass"), ("fail", "Fail"), ("preference", "Preferences"))
_QUIET = ("not_met", "not_applicable")
_FIT_LABELS = {"meets": "Meets", "unclear": "Unclear", "gap": "Gap"}

DEFAULT_DB = "seen.db"
DEFAULT_LOCK = ".ftja.lock"


_REVIEW_NOTE = (
    "Don't fill in application status/reason by hand here — run "
    "`venv/bin/python -m ftja.server` and open http://127.0.0.1:8765 instead. "
    "It writes straight into seen.db, and `/ftja-review` reads the reasons to "
    "propose criteria.json/rubric.md changes."
)


def _criteria_lines(job: dict) -> list[str]:
    """The rubric's three categories, one line per criterion that says
    something about this job. Criteria that are simply not met or don't
    apply are only counted — that is most of them on any one posting. Then
    what the employer asks for, and notes."""
    lines = []
    if job.get("company_line"):
        lines += [f"*{job['company_line']}*", ""]
    for kind, title in _KIND_TITLES:
        items = [c for c in job["criteria"] if c["kind"] == kind]
        if not items:
            continue
        lines.append(f"**{title}**")
        for c in items:
            if c["result"] in _QUIET:
                continue
            lines.append(f"- {_RESULT_LABELS[c['result']]} — {c['label']}" + (f": {c['why']}" if c.get("why") else ""))
            if c.get("quote"):
                unverified = " *(not found in the posting)*" if c.get("quote_verified") is False else ""
                lines.append(f"  > {c['quote']}{unverified}")
        for result in _QUIET:
            n = sum(c["result"] == result for c in items)
            if n:
                lines.append(f"- {n} {_RESULT_LABELS[result].lower()}")
        lines.append("")
    if job.get("employer"):
        lines.append("**What they ask for**")
        for e in job["employer"]:
            kind = "Must" if e["kind"] == "must" else "Preferred"
            lines.append(f"- {_FIT_LABELS[e['candidate']]} — {kind}: {e['label']}" + (f" ({e['why']})" if e.get("why") else ""))
        lines.append("")
    if job.get("notes"):
        lines.append("**Notes**")
        lines += [f"- {n['text']}" for n in job["notes"]]
        lines.append("")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _job_block(job: dict) -> list[str]:
    dup_note = f" (same posting, reposted {len(job['duplicate_job_urls'])}x)" if job.get("duplicate_job_urls") else ""
    lines = [f"## {job.get('title', '(no title)')} — {job.get('company', '')}{dup_note}", f"{job.get('job_url', '')}", ""]
    if job.get("criteria"):
        lines += _criteria_lines(job)
    else:  # judged before per-criterion results existed
        lines += [f"**Reasoning**: {job.get('reasoning', '')}", "", "**Evidence**:"]
        lines += [f"> {sent}" for sent in job.get("evidence_sentences", [])]
    lines.append("")
    return lines


def write_digest(passed: list[dict], out_dir: str = ".", review: list[dict] | None = None) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = f"{out_dir}/digest-{date_str}.md"
    review = review or []
    count = f"{len(passed)} passed" + (f", {len(review)} to review" if review else "")
    lines = [f"# FTJA digest — {date_str}", "", count, "", _REVIEW_NOTE, ""]
    for job in passed:
        lines += _job_block(job)
    if review:
        lines += [f"# To review ({len(review)})", "",
                  "The posting is ambiguous on a point that decides the verdict. Read these yourself.", ""]
        for job in review:
            lines += _job_block(job)
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def write_rejected(rejected: list[dict], out_dir: str = ".", stage0_dropped: list[dict] | None = None) -> str:
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
        if job.get("criteria"):
            lines += _criteria_lines(job)
        else:
            lines.append(f"**Reason**: {job.get('reasoning', '(no record)')}")
        lines.append("")
    if stage0_dropped:
        lines += [f"# Dropped at Stage0 — requires another language ({len(stage0_dropped)})", "",
                  "Dropped by code before any LLM call, and not recorded as seen. "
                  "If one of these was dropped wrongly, the sentence below is why.", ""]
        for job in stage0_dropped:
            lines.append(f"## {job.get('title') or '(no title)'} — {job.get('company') or ''}")
            lines.append(f"{job.get('job_url') or ''}")
            lines.append("")
            lines.append(f"> {job.get('evidence') or '(no record)'}")
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
    review = by_status.get("review", 0)
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
        "stage2_review": review,
        "stage2_fail": stage2_fail,
        **stats,  # ts_start, duration_seconds, total_tokens, scraped_count,
                  # already_seen_skipped, stage0_dropped, criteria_snapshot —
                  # whatever the skill measured
    }
    with open(f"{out_dir}/runs.jsonl", "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def apply_stage2_verdicts(results: list[dict], definitions: list[dict], descriptions: dict[str, str]):
    """For every result carrying the Stage 2 subagent's answer (`stage2`):
    check its quotes against the JD, compute the verdict, and overwrite
    status / reasoning / evidence_sentences from it. The model's own opinion
    of the overall verdict, if it sent one, is ignored.

    Returns how many of them had no JD text to check against. Without it
    quotes go unchecked and nothing is stored for the viewer, so the caller
    reports the count instead of letting it pass silently."""
    missing = 0
    for r in results:
        if r.get("stage2") is None:
            continue
        raw = r.pop("stage2")
        raw = raw if isinstance(raw, dict) else {"criteria": raw}
        description = r.get("description") or descriptions.get(r.get("job_url", ""), "")
        missing += not description
        # the prompt shows title and location above the JD, so a quote may come from them
        checked = "\n".join(filter(None, [r.get("title"), r.get("company"), r.get("location"), description])) if description else ""
        judged = judge(definitions, raw.get("criteria"), checked)
        r.update(clean_extras(raw, checked))
        r.update(
            criteria=judged["criteria"], verdict=judged["verdict"],
            status=STATUS_FOR_VERDICT[judged["verdict"]], stage_reached=2,
            reasoning=summarize(judged), description=description or None,
            evidence_sentences=list(dict.fromkeys(
                c["quote"] for c in judged["criteria"] if c["quote"] and c["quote_verified"] is not False)),
        )
    return missing


def finalize(results: list[dict], db_path: str = DEFAULT_DB, out_dir: str = ".", stats: dict | None = None,
             stage0_dropped: list[dict] | None = None, definitions: list[dict] | None = None,
             descriptions: dict[str, str] | None = None, rubric_path: str | None = None) -> dict:
    stats = stats or {}
    unchecked = 0
    if any(r.get("stage2") is not None for r in results):
        if not definitions:
            raise ValueError("results carry Stage 2 answers but no criteria list was given (--criteria)")
        unchecked = apply_stage2_verdicts(results, definitions, descriptions or {})
        if unchecked:
            print(f"WARNING: {unchecked} Stage 2 job(s) had no JD text — quotes not checked, posting not stored. "
                  "Pass filter_stage0's output as --jobs.", file=sys.stderr)
    passed = [r for r in results if r.get("status") == "passed"]
    review = [r for r in results if r.get("status") == "review"]
    rejected = [r for r in results if r.get("status") in ("stage1_fail", "stage2_fail")]
    rv = get_rubric_version(rubric_path or f"{out_dir}/rubric.md")
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
                    criteria=r.get("criteria"),
                    description=r.get("description"),
                    company_line=r.get("company_line"),
                    employer=r.get("employer"),
                    notes=r.get("notes"),
                    t1_blocks=r.get("t1_blocks"),
                    stage1_reason=r.get("stage1_reason"),
                )

    digest_path = write_digest(passed, out_dir=out_dir, review=review)  # always write, even 0 passed -> no silent gaps
    rejected_path = write_rejected(rejected, out_dir=out_dir, stage0_dropped=stage0_dropped)
    summary = f"run complete: {len(results)} evaluated, {len(passed)} passed, {len(review)} to review -> {digest_path} ({len(rejected)} rejected -> {rejected_path})"
    if unchecked:
        summary += f" [{unchecked} Stage 2 job(s) without JD text: quotes unchecked]"
    if "duration_seconds" in stats:
        summary += f" ({stats['duration_seconds']:.0f}s"
        summary += f", {stats['total_tokens']} tokens)" if "total_tokens" in stats else ")"
    append_log(summary, log_path=f"{out_dir}/run.log")
    append_run_record(results, stats, run_id, out_dir=out_dir)
    if passed or review:
        notify("FTJA", f"{len(passed)} new postings" + (f", {len(review)} to review" if review else ""))
    else:
        notify("FTJA", "No postings passed today")

    return {"total": len(results), "passed": len(passed), "review": len(review),
            "quotes_unchecked": unchecked, "digest_path": digest_path,
            "rejected_path": rejected_path, "run_id": run_id}


def resolve_total_tokens(given: int | None, run_dir: str) -> int:
    """A run that judged anything spent tokens, so a given 0 means "not
    counted", not "free". Take the ledger recorded with
    `ftja.live record-tokens` instead; 0 here means unknown, and the run
    record then leaves the total out rather than state a number."""
    from ftja.live import tokens_total
    return given or tokens_total(run_dir)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="",
                    help="the run directory from `ftja.live start`: reads its results.json, stats.json, "
                         "stage0 files and its copy of the criteria list, and marks the run finished")
    ap.add_argument("--total-tokens", type=int, default=None, help="with --run-dir: tokens spent on Stage 1 and 2; default is the sum recorded with `ftja.live record-tokens`")
    ap.add_argument("--results", default="", help="path to results JSON (list of per-job outcomes)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--stats", default="", help="optional path to a stats JSON (duration_seconds, total_tokens, ...)")
    ap.add_argument("--stage0-dropped", default="",
                    help="optional path to filter_stage0's --dropped-out file, listed in the rejected file")
    ap.add_argument("--criteria", default=DEFAULT_CRITERIA_PATH,
                    help="the rubric's criteria list; read only when a result carries a Stage 2 answer")
    ap.add_argument("--jobs", default="",
                    help="filter_stage0's --out file: where each Stage 2 job's full JD is read from, "
                         "to check quotes against and to store")
    args = ap.parse_args()
    if args.run_dir:
        args.results = os.path.join(args.run_dir, "results.json")
        args.stats = os.path.join(args.run_dir, "stats.json")
        args.stage0_dropped = os.path.join(args.run_dir, "stage0-dropped.json")
        args.jobs = os.path.join(args.run_dir, "stage0.json")
        args.criteria = os.path.join(args.run_dir, DEFAULT_CRITERIA_PATH)
    if not args.results:
        ap.error("--results or --run-dir is required")

    with open(args.results) as f:
        results = json.load(f)

    definitions, descriptions = None, {}
    if any(r.get("stage2") is not None for r in results):
        definitions = load_criteria(args.criteria)["criteria"]
    if args.jobs:
        with open(args.jobs) as f:
            descriptions = {j.get("job_url", ""): j.get("description") or "" for j in json.load(f)}

    stage0_dropped = []
    if args.stage0_dropped:
        with open(args.stage0_dropped) as f:
            stage0_dropped = json.load(f)

    stats = {}
    if args.stats:
        with open(args.stats) as f:
            stats = json.load(f)

    if args.run_dir:
        started = datetime.fromisoformat(stats["ts_start"].replace("Z", "+00:00"))
        stats["duration_seconds"] = (datetime.now(timezone.utc) - started).total_seconds()
        total = resolve_total_tokens(args.total_tokens, args.run_dir)
        if total:
            stats["total_tokens"] = total

    summary = finalize(results, db_path=args.db, out_dir=args.out_dir, stats=stats, stage0_dropped=stage0_dropped,
                       definitions=definitions, descriptions=descriptions,
                       rubric_path=os.path.join(args.run_dir, "rubric.md") if args.run_dir else None)
    if args.run_dir:  # the live view stops showing the run; Results shows the finished one
        with open(os.path.join(args.run_dir, "done.json"), "w") as f:
            json.dump(summary, f)
    print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)


if __name__ == "__main__":
    main()
