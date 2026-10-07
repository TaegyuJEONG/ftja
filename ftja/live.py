"""One run's working directory, and the live view of it.

Every /ftja-run keeps its files in `.ftja-run/<run id>/` instead of loose
/tmp files. Two things follow from that:

  - the steps that used to be assembled by hand in the skill (merge the
    scrapes, fill the prompt templates, collect the verdicts into one
    results file) are commands here, so they come out the same every run;
  - the viewer can show a run WHILE it happens. Nothing has to remember to
    report progress: `state()` just looks at which files exist. A Stage 1
    or Stage 2 job is "done" the moment its subagent's output file lands.

Layout:
  run.json                 {run_id, ts_start, criteria_snapshot}
  rubric-criteria.json,    copies taken at start — the rubric can be edited in
  rubric.md                the viewer mid-run; this run judges against the copies
  scrape/<n>.json          jobs scraped for search term n
  scrape/<n>.progress.json {term, count, wanted, done}, rewritten every batch
  stage0.json, stage0-dropped.json, stage0-counts.json
  s1/manifest.json         jobs sent to Stage 1; s1/<n>.txt prompt, s1/<n>.out.json verdict
  s2/manifest.json         jobs sent to Stage 2; s2/<n>.txt prompt, s2/<n>.out.json answer
  results.json, stats.json what finalize reads
  done.json                written by finalize; the run is no longer live

Live Stage 2 cards are judged with the same `ftja.verdict.judge` finalize
uses, on the same JD text, so the live card and the final one can't differ.
"""
import argparse
import json
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone

from ftja.lock import is_locked
from ftja.state import connect, is_seen
from ftja.verdict import (DEFAULT_CRITERIA_PATH, STATUS_FOR_VERDICT, clean_extras, judge, load_criteria,
                          render_criteria_list)

RUNS_DIR = ".ftja-run"
KEEP_RUNS = 5
SNAPSHOT_KEYS = ("search_terms", "location", "is_remote", "hours_old", "results_wanted", "exact_phrase_search")
PAGE_SIZE = 10
# A replay has no lock to say it is alive; it counts as running while it keeps writing.
REPLAY_STALE_SECONDS = 120


# ---- files -----------------------------------------------------------------

def _read_json(path: str, default=None):
    """Missing, empty or half-written (a subagent is mid-write) all read as `default`."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: str, value):
    """Write-then-rename, so a poll never reads half a file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _mtime(path: str) -> float:
    """0 for a file that vanished between being listed and being looked at
    (a `.tmp` that was just renamed into place)."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _read_answer(path: str) -> dict | None:
    """A subagent's output file. Tolerates a ```json fence around the object."""
    try:
        with open(path) as f:
            text = f.read().strip()
    except OSError:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _template(path: str) -> str:
    """A prompt file's text after its `---` line (above it are notes for people)."""
    with open(path) as f:
        text = f.read()
    return text.split("\n---\n", 1)[-1].strip()


def run_dir_for(project_dir: str, run_id: str) -> str:
    return os.path.join(project_dir, RUNS_DIR, re.sub(r"[^0-9A-Za-z._-]", "-", run_id))


def _run_dirs(project_dir: str) -> list[str]:
    root = os.path.join(project_dir, RUNS_DIR)
    if not os.path.isdir(root):
        return []
    dirs = [os.path.join(root, d) for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "run.json"))]
    return sorted(dirs, key=lambda d: os.path.getmtime(os.path.join(d, "run.json")), reverse=True)


# ---- the run's steps ---------------------------------------------------------

def start(project_dir: str = ".") -> dict:
    """Step 0: fix the run id and create the directory. From this moment the
    run shows in the viewer's Results tab."""
    criteria = _read_json(os.path.join(project_dir, "criteria.json"), {}) or {}
    ts_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_dir = run_dir_for(project_dir, ts_start)
    if os.path.exists(run_dir):  # the id is the start time to the second; never reuse a directory
        time.sleep(1)
        return start(project_dir)
    for old in _run_dirs(project_dir)[KEEP_RUNS - 1:]:
        shutil.rmtree(old, ignore_errors=True)
    os.makedirs(run_dir, exist_ok=True)
    for name in (DEFAULT_CRITERIA_PATH, "rubric.md"):
        shutil.copyfile(os.path.join(project_dir, name), os.path.join(run_dir, name))
    snapshot = {k: criteria.get(k) for k in SNAPSHOT_KEYS if k in criteria}
    run = {"run_id": ts_start, "ts_start": ts_start, "criteria_snapshot": snapshot}
    _write_json(os.path.join(run_dir, "run.json"), run)
    for n, term in enumerate(snapshot.get("search_terms") or []):
        _write_json(os.path.join(run_dir, "scrape", f"{n}.progress.json"),
                    {"term": term, "count": 0, "wanted": snapshot.get("results_wanted"), "done": False})
    return {**run, "run_dir": run_dir}


def scrape_term(run_dir: str, n: int) -> int:
    """Step 1, one search term: scrape it, reporting the count after every batch."""
    from ftja.scrape import clean_jobs, scrape  # jobspy is slow to import; only here

    snapshot = _read_json(os.path.join(run_dir, "run.json"))["criteria_snapshot"]
    term = snapshot["search_terms"][n]
    wanted = int(snapshot.get("results_wanted") or 100)
    progress_path = os.path.join(run_dir, "scrape", f"{n}.progress.json")

    def report(count: int, done: bool = False):
        _write_json(progress_path, {"term": term, "count": count, "wanted": wanted, "done": done})

    jobs = scrape(search_term=term, location=snapshot.get("location") or "",
                  is_remote=bool(snapshot.get("is_remote")), results_wanted=wanted,
                  hours_old=int(snapshot.get("hours_old") or 72),
                  exact_phrase=snapshot.get("exact_phrase_search", True) is not False,
                  on_batch=report)
    _write_json(os.path.join(run_dir, "scrape", f"{n}.json"), clean_jobs(jobs))
    report(len(jobs), done=True)
    return len(jobs)


def stage0(run_dir: str, project_dir: str = ".") -> dict:
    """Step 2: merge the scrapes (one entry per job_url) and run the code-only filter."""
    from ftja.filter_stage0 import filter_stage0

    merged: dict[str, dict] = {}
    scrape_dir = os.path.join(run_dir, "scrape")
    for name in sorted(os.listdir(scrape_dir), key=lambda s: (len(s), s)):
        if name.endswith(".json") and not name.endswith(".progress.json"):
            for job in _read_json(os.path.join(scrape_dir, name), []):
                if job.get("job_url"):
                    merged.setdefault(job["job_url"], job)
    jobs = list(merged.values())
    criteria = _read_json(os.path.join(project_dir, "criteria.json"), {})
    dropped_jobs: list[dict] = []
    passed, dropped = filter_stage0(jobs, criteria, dropped_jobs)
    _write_json(os.path.join(run_dir, "stage0.json"), passed)
    _write_json(os.path.join(run_dir, "stage0-dropped.json"), dropped_jobs)
    counts = {"scraped": len(jobs), "passed": len(passed), "dropped": dropped}
    _write_json(os.path.join(run_dir, "stage0-counts.json"), counts)
    return counts


def _rubric_excerpt(rubric_path: str) -> str:
    """rubric.md's Pass and Fail sections — all Stage 1 gets to see of the rubric."""
    with open(rubric_path) as f:
        text = f.read()
    sections = re.split(r"(?m)^(?=## )", text)
    wanted = [s.strip() for s in sections if re.match(r"## +(pass|fail)\b", s, re.I)]
    return "\n\n".join(wanted) if wanted else text.strip()


def _matched(job: dict) -> tuple[list[str], list[str]]:
    blocks = [b for b in job.get("_t1_blocks") or [] if b.get("matched")]
    keywords = list(dict.fromkeys(k for b in blocks for k in b.get("matched_keywords") or []))
    return keywords, [b["sentence"] for b in blocks]


def prepare_stage1(run_dir: str, project_dir: str = ".", db_path: str = "seen.db") -> dict:
    """Step 3 setup: drop jobs judged on an earlier run, then write one prompt
    file per remaining job and the manifest that maps file numbers to jobs."""
    jobs = _read_json(os.path.join(run_dir, "stage0.json"), [])
    with connect(db_path) as conn:
        fresh = [j for j in jobs if not is_seen(conn, j.get("job_url", ""))]
    template = _template(os.path.join(project_dir, "stage1_prompt.md"))
    excerpt = _rubric_excerpt(os.path.join(run_dir, "rubric.md"))
    os.makedirs(os.path.join(run_dir, "s1"), exist_ok=True)
    manifest = []
    for n, job in enumerate(fresh):
        blocks = job.get("_t1_blocks") or []
        rendered = "\n".join(f"{b['s_id']}[{'T1' if b.get('matched') else 'ctx'}] {b['sentence']}" for b in blocks)
        prompt = (template.replace("{rubric_pass_fail_excerpt}", excerpt)
                  .replace("{title}", str(job.get("title") or "")).replace("{company}", str(job.get("company") or ""))
                  .replace("{blocks}", rendered))
        with open(os.path.join(run_dir, "s1", f"{n}.txt"), "w") as f:
            f.write(prompt)
        manifest.append({"n": n, "job_url": job["job_url"], "title": job.get("title"), "company": job.get("company"),
                         "location": job.get("location"),
                         "blocks": [{"s_id": b["s_id"], "sentence": b["sentence"], "matched": bool(b.get("matched")),
                                     "keywords": b.get("matched_keywords") or []} for b in blocks]})
    _write_json(os.path.join(run_dir, "s1", "manifest.json"), manifest)
    _write_json(os.path.join(run_dir, "s1", "skipped.json"), {"already_seen_skipped": len(jobs) - len(fresh)})
    return {"jobs": len(manifest), "already_seen_skipped": len(jobs) - len(fresh),
            "prompt_files": os.path.join(run_dir, "s1", "<n>.txt"), "output_files": os.path.join(run_dir, "s1", "<n>.out.json")}


def _stage1_results(run_dir: str) -> tuple[list[dict], list[dict], list[int]]:
    """-> (passed, failed, numbers still without a usable verdict)."""
    manifest = _read_json(os.path.join(run_dir, "s1", "manifest.json"), []) or []
    passed, failed, pending = [], [], []
    for entry in manifest:
        path = os.path.join(run_dir, "s1", f"{entry['n']}.out.json")
        answer = _read_answer(path)
        verdict = str((answer or {}).get("verdict") or "").lower()
        if verdict not in ("pass", "fail"):
            pending.append(entry["n"])
            continue
        sids = {str(s) for s in answer.get("evidence_sids") or []}
        whys = {str(b.get("sid")): str(b.get("why") or "").strip()
                for b in answer.get("blocks") or [] if isinstance(b, dict)}
        entry = {**entry, "judged_at": _mtime(path), "reason": str(answer.get("reason") or "").strip(),
                 "evidence_sentences": [b["sentence"] for b in entry.get("blocks") or [] if b["s_id"] in sids],
                 # each keyword sentence with what Stage 1 made of it
                 "t1_blocks": [{"sentence": b["sentence"], "keywords": b.get("keywords") or [], "why": whys.get(b["s_id"], "")}
                               for b in entry.get("blocks") or [] if b["matched"]]}
        (passed if verdict == "pass" else failed).append(entry)
    return passed, failed, pending


def prepare_stage2(run_dir: str, project_dir: str = ".") -> dict:
    """Step 4 setup: one prompt file per Stage 1 pass. Refuses while Stage 1
    verdicts are missing, naming them, so none is silently dropped."""
    passed, _, pending = _stage1_results(run_dir)
    if pending:
        raise SystemExit(f"Stage 1 has no usable verdict yet for: {pending}. Rerun those jobs, then call this again.")
    by_url = {j["job_url"]: j for j in _read_json(os.path.join(run_dir, "stage0.json"), [])}
    definitions = load_criteria(os.path.join(run_dir, DEFAULT_CRITERIA_PATH))["criteria"]
    template = (_template(os.path.join(project_dir, "stage2_prompt.md"))
                .replace("{criteria_list}", render_criteria_list(definitions))
                .replace("Read `rubric.md`", f"Read `{os.path.join(run_dir, 'rubric.md')}`"))  # this run's copy
    os.makedirs(os.path.join(run_dir, "s2"), exist_ok=True)
    manifest, empty = [], []
    for entry in passed:
        job = by_url.get(entry["job_url"], {})
        if not str(job.get("description") or "").strip():
            empty.append(entry["n"])
            continue
        n = len(manifest)
        prompt = (template.replace("{title}", str(job.get("title") or "")).replace("{company}", str(job.get("company") or ""))
                  .replace("{location}", str(job.get("location") or "")).replace("{full_description}", job["description"]))
        with open(os.path.join(run_dir, "s2", f"{n}.txt"), "w") as f:
            f.write(prompt)
        manifest.append({"n": n, "s1_n": entry["n"], "job_url": entry["job_url"], "title": entry.get("title"),
                         "company": entry.get("company"), "location": entry.get("location")})
    _write_json(os.path.join(run_dir, "s2", "manifest.json"), manifest)
    _write_json(os.path.join(run_dir, "s2", "empty.json"), empty)
    return {"jobs": len(manifest), "skipped_empty_description": len(empty),
            "prompt_files": os.path.join(run_dir, "s2", "<n>.txt"), "output_files": os.path.join(run_dir, "s2", "<n>.out.json")}


def assemble_results(run_dir: str) -> dict:
    """Step 5 setup: results.json and stats.json for finalize. Refuses while
    any Stage 2 answer is missing."""
    by_url = {j["job_url"]: j for j in _read_json(os.path.join(run_dir, "stage0.json"), [])}
    passed, failed, pending = _stage1_results(run_dir)
    s2_manifest = _read_json(os.path.join(run_dir, "s2", "manifest.json"), []) or []
    answers = {e["job_url"]: _read_answer(os.path.join(run_dir, "s2", f"{e['n']}.out.json")) for e in s2_manifest}
    missing = [e["n"] for e in s2_manifest if not isinstance((answers[e["job_url"]] or {}).get("criteria"), list)]
    if pending or missing:
        raise SystemExit(f"Not complete — Stage 1 missing: {pending}; Stage 2 missing: {missing}. Rerun those, then call this again.")

    def base(entry: dict) -> dict:
        job = by_url.get(entry["job_url"], {})
        keywords, sentences = _matched(job)
        out = {"job_url": entry["job_url"], "title": entry.get("title") or "", "company": entry.get("company") or "",
               "location": entry.get("location") or ""}
        if job.get("_duplicate_job_urls"):
            out["duplicate_job_urls"] = job["_duplicate_job_urls"]
        if keywords:
            out.update(t1_matched_keywords=keywords, t1_matched_sentences=sentences)
        return out

    results = []
    for entry in failed:
        results.append({**base(entry), "status": "stage1_fail", "stage_reached": 1, "verdict": "fail",
                        "reasoning": entry["reason"], "evidence_sentences": entry["evidence_sentences"],
                        "t1_blocks": entry["t1_blocks"]})
    for entry in passed:
        if entry["job_url"] in answers:
            results.append({**base(entry), "stage_reached": 2, "stage2": answers[entry["job_url"]]})
        else:  # passed Stage 1 but had no JD text to judge
            results.append({**base(entry), "status": "stage1_fail", "stage_reached": 1, "verdict": "fail",
                            "reasoning": "Passed Stage 1, but the posting had no description to judge."})
    _write_json(os.path.join(run_dir, "results.json"), results)

    run = _read_json(os.path.join(run_dir, "run.json"), {})
    counts = _read_json(os.path.join(run_dir, "stage0-counts.json"), {}) or {}
    stats = {"ts_start": run.get("ts_start"), "criteria_snapshot": run.get("criteria_snapshot"),
             "scraped_count": counts.get("scraped"), "stage0_dropped": counts.get("dropped"),
             "scraped_by_term": [{"term": p["term"], "count": p["count"]} for p in _scrape_progress(run_dir)],
             "already_seen_skipped": (_read_json(os.path.join(run_dir, "s1", "skipped.json"), {}) or {}).get("already_seen_skipped")}
    _write_json(os.path.join(run_dir, "stats.json"), stats)
    return {"results": len(results), "stage1_fail": len(failed), "stage2": len(s2_manifest)}


# ---- the live view -----------------------------------------------------------

def _scrape_progress(run_dir: str) -> list[dict]:
    scrape_dir = os.path.join(run_dir, "scrape")
    if not os.path.isdir(scrape_dir):
        return []
    names = sorted((n for n in os.listdir(scrape_dir) if n.endswith(".progress.json")), key=lambda s: (len(s), s))
    return [p for p in (_read_json(os.path.join(scrape_dir, n)) for n in names) if p]


def _is_running(run_dir: str, run: dict, project_dir: str) -> bool:
    if os.path.exists(os.path.join(run_dir, "done.json")):
        return False
    if run.get("replay"):
        newest = max((_mtime(os.path.join(root, f)) for root, _, files in os.walk(run_dir) for f in files), default=0)
        return time.time() - newest < REPLAY_STALE_SECONDS
    return is_locked(os.path.join(project_dir, ".ftja.lock"))


def current_run_dir(project_dir: str = ".") -> str | None:
    """Only the newest directory can be the live run: an older unfinished
    one is a run that crashed, and the lock now belongs to its successor."""
    for run_dir in _run_dirs(project_dir)[:1]:
        run = _read_json(os.path.join(run_dir, "run.json"), {}) or {}
        if _is_running(run_dir, run, project_dir):
            return run_dir
    return None


_judged_cache: dict[tuple, dict] = {}


def _live_card(run_dir: str, entry: dict, definitions: list[dict], descriptions: dict) -> dict:
    card = {k: entry.get(k) for k in ("n", "job_url", "title", "company", "location")}
    path = os.path.join(run_dir, "s2", f"{entry['n']}.out.json")
    answer = _read_answer(path)
    if not isinstance((answer or {}).get("criteria"), list):
        return {**card, "status": "judging"}
    key = (path, _mtime(path))
    if key not in _judged_cache:
        if "_judged" in answer:  # a replay carries the stored judgment as is
            judged = answer["_judged"]
        else:
            description = descriptions.get(entry["job_url"], "")
            checked = "\n".join(filter(None, [entry.get("title"), entry.get("company"), entry.get("location"), description]))
            result = judge(definitions, answer["criteria"], checked if description else "")
            judged = {"status": STATUS_FOR_VERDICT[result["verdict"]], "criteria": result["criteria"],
                      **clean_extras(answer, checked if description else "")}
        _judged_cache[key] = judged
    return {**card, **_judged_cache[key], "criteria_feedback": {}}


def state(project_dir: str = ".") -> dict | None:
    """What the run looks like right now, or None when nothing is running."""
    run_dir = current_run_dir(project_dir)
    if not run_dir:
        return None
    run = _read_json(os.path.join(run_dir, "run.json"), {})
    terms = _scrape_progress(run_dir)
    counts = _read_json(os.path.join(run_dir, "stage0-counts.json"))
    out = {"run_id": run.get("run_id"), "ts_start": run.get("ts_start"), "replay": bool(run.get("replay")),
           "replay_of": run.get("replay_of"), "replay_sample": bool(run.get("replay_sample")), "criteria_snapshot": run.get("criteria_snapshot"),
           "scrape": {"terms": terms, "total": sum(t.get("count") or 0 for t in terms),
                      "done": bool(terms) and all(t.get("done") for t in terms)},
           "stage0": counts, "stage1": None, "stage2": None, "jobs": [],
           "already_seen_skipped": (_read_json(os.path.join(run_dir, "s1", "skipped.json"), {}) or {}).get("already_seen_skipped")}

    if os.path.exists(os.path.join(run_dir, "s1", "manifest.json")):
        passed, failed, pending = _stage1_results(run_dir)
        out["stage1"] = {"total": len(passed) + len(failed) + len(pending), "pass": len(passed),
                         "fail": len(failed), "pending": len(pending)}

    manifest = _read_json(os.path.join(run_dir, "s2", "manifest.json"))
    if manifest is not None:
        definitions = (_read_json(os.path.join(run_dir, DEFAULT_CRITERIA_PATH), {}) or {}).get("criteria") or []
        descriptions = {j["job_url"]: j.get("description") or "" for j in _read_json(os.path.join(run_dir, "stage0.json"), [])}
        jobs = [_live_card(run_dir, e, definitions, descriptions) for e in manifest]
        tally = {s: sum(j["status"] == s for j in jobs) for s in ("judging", "passed", "review", "stage2_fail")}
        out["stage2"] = {"total": len(jobs), "pending": tally["judging"], "passed": tally["passed"],
                         "review": tally["review"], "fail": tally["stage2_fail"]}
        out["jobs"] = jobs
    return out


def stage1_fails(project_dir: str, db_path: str, run_id: str, page: int = 1) -> dict:
    """Why each job stopped at Stage 1 — from the run directory while the run
    is live, from seen.db afterwards. Per job: the overall reason, the
    keywords that matched, and each keyword sentence with what Stage 1 made
    of it. Runs older than Stage 1 reasons have the sentences and keywords
    only."""
    run_dir = current_run_dir(project_dir)
    live = run_dir and (_read_json(os.path.join(run_dir, "run.json"), {}) or {}).get("run_id") == run_id
    if live:
        _, failed, _ = _stage1_results(run_dir)
        failed.sort(key=lambda e: e["judged_at"], reverse=True)  # newest verdict first, so page 1 is where they land
        rows = [{"job_url": e["job_url"], "title": e.get("title"), "company": e.get("company"), "location": e.get("location"),
                 "reason": _real_reason(e["reason"]), "blocks": e["t1_blocks"],
                 "keywords": list(dict.fromkeys(k for b in e["t1_blocks"] for k in b["keywords"]))} for e in failed]
    else:
        with connect(db_path) as conn:
            found = conn.execute(
                """SELECT job_url, title, company, location, reasoning, t1_blocks, t1_matched_sentences,
                          t1_matched_keywords
                   FROM seen_jobs WHERE run_id = ? AND status = 'stage1_fail' ORDER BY title""", (run_id,)).fetchall()
        rows, shown = [], set()
        for job_url, title, company, location, reasoning, blocks, matched, keywords in found:
            if (title, company, reasoning) in shown:  # a repost under another URL: same judgment, one row
                continue
            shown.add((title, company, reasoning))
            reason = _real_reason(reasoning)
            rows.append({"job_url": job_url, "title": title, "company": company, "location": location,
                         "reason": reason, "keywords": _loads_list(keywords),
                         "blocks": _loads_list(blocks)
                         or [{"sentence": s, "keywords": [], "why": ""} for s in _loads_list(matched)]})
    total = len(rows)
    page = max(1, page)
    return {"jobs": rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], "page": page, "total": total,
            "total_pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}


def _real_reason(reasoning) -> str:
    """Before Stage 1 gave reasons, runs wrote a filler line in their place."""
    return "" if (reasoning or "").startswith("Stage 1 (keyword excerpt)") else (reasoning or "")


def _loads_list(text) -> list:
    try:
        value = json.loads(text) if text else []
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


# ---- replay --------------------------------------------------------------------

def replay(project_dir: str, db_path: str, run_id: str | None = None, seconds: float = 75,
           per_term: int | None = None):
    """Play a finished run back through the live view, compressed into
    `seconds`. Every number, verdict and quote is that run's own, read from
    runs.jsonl and seen.db; only the timing is invented. Writes nothing to
    seen.db or runs.jsonl.

    per_term shortens it: as if each search term had been capped at that
    many results. Counts are scaled down by the same ratio and a matching
    share of the run's jobs is shown — a sample of the run, labelled so."""
    from ftja.runs_view import list_runs
    runs = [r for r in list_runs(project_dir) if r.get("run_id")]
    record = next((r for r in runs if r["run_id"] == run_id), None) if run_id else (runs[0] if runs else None)
    if not record:
        raise SystemExit("no such run in runs.jsonl")
    run_id = record["run_id"]
    with connect(db_path) as conn:
        cols = ["job_url", "title", "company", "location", "status", "stage_reached", "reasoning", "evidence_sentences",
                "t1_matched_sentences", "criteria", "company_line", "employer", "notes", "t1_blocks", "t1_matched_keywords"]
        rows = [dict(zip(cols, r)) for r in conn.execute(
            f"SELECT {', '.join(cols)} FROM seen_jobs WHERE run_id = ? AND stage_reached >= 1", (run_id,))]
    seen_keys, jobs = set(), []
    for r in rows:  # reposts share one judgment; show one
        key = (r["title"], r["company"], r["reasoning"])
        if key not in seen_keys:
            seen_keys.add(key)
            jobs.append(r)
    rng = random.Random(run_id)
    rng.shuffle(jobs)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_dir = run_dir_for(project_dir, f"replay-{now}")
    while os.path.exists(run_dir):  # two replays started within the same second
        run_dir += "-again"
    snapshot = record.get("criteria_snapshot") or {}
    terms = snapshot.get("search_terms") or ["(search)"]
    by_term = {t["term"]: t["count"] for t in record.get("scraped_by_term") or []}
    scraped = record.get("scraped_count") or 0
    weights = [rng.uniform(0.5, 1.5) for _ in terms]
    targets = [by_term.get(t, round(scraped * w / sum(weights))) for t, w in zip(terms, weights)]
    ratio = 1.0
    if per_term and scraped:
        targets = [min(t, per_term) for t in targets]
        ratio = min(1.0, sum(targets) / scraped)
        snapshot = {**snapshot, "results_wanted": per_term}

        def scale(n):
            return round(n * ratio) if isinstance(n, (int, float)) else n
        keep = lambda group: group[:max(1, round(len(group) * ratio))] if group else []
        jobs = keep([j for j in jobs if j["stage_reached"] == 1]) + keep([j for j in jobs if j["stage_reached"] == 2])
        rng.shuffle(jobs)
        record = {**record, "scraped_count": sum(targets), "stage0_passed": scale(record.get("stage0_passed")),
                  "already_seen_skipped": scale(record.get("already_seen_skipped")),
                  "stage0_dropped": {k: scale(v) for k, v in (record.get("stage0_dropped") or {}).items()}}
        scraped = record["scraped_count"]

    _write_json(os.path.join(run_dir, "run.json"), {"run_id": f"replay-{now}", "ts_start": now, "replay": True,
                                                   "replay_of": run_id, "replay_sample": ratio < 1,
                                                   "criteria_snapshot": snapshot})

    def pause(share: float, steps: int = 1):
        time.sleep(max(0.05, seconds * share / max(1, steps)))

    def progress(n, count, done=False):
        _write_json(os.path.join(run_dir, "scrape", f"{n}.progress.json"),
                    {"term": terms[n], "count": count, "wanted": snapshot.get("results_wanted"), "done": done})

    for n in range(len(terms)):
        progress(n, 0)
    steps = max(3, 24 // len(terms))
    for n, target in enumerate(targets):  # scraping: a quarter of the time, one search term after another
        for step in range(1, steps + 1):
            progress(n, round(target * step / steps), done=step == steps)
            pause(0.25, steps * len(terms))

    _write_json(os.path.join(run_dir, "stage0-counts.json"),
                {"scraped": scraped, "passed": record.get("stage0_passed"), "dropped": record.get("stage0_dropped") or {}})
    _write_json(os.path.join(run_dir, "stage0.json"), [])
    pause(0.06)

    def stored_blocks(j):
        # runs before per-sentence reasons: the sentences, with the job's keywords on the first
        return _loads_list(j["t1_blocks"]) or [
            {"sentence": s, "keywords": _loads_list(j["t1_matched_keywords"]) if i == 0 else [], "why": ""}
            for i, s in enumerate(_loads_list(j["t1_matched_sentences"]))]

    s1 = [{"n": n, "job_url": j["job_url"], "title": j["title"], "company": j["company"], "location": j["location"],
           "blocks": [{"s_id": f"S{i}", "sentence": b["sentence"], "matched": True, "keywords": b.get("keywords") or [],
                       "why": b.get("why") or ""} for i, b in enumerate(stored_blocks(j))]}
          for n, j in enumerate(jobs)]
    _write_json(os.path.join(run_dir, "s1", "manifest.json"), s1)
    _write_json(os.path.join(run_dir, "s1", "skipped.json"), {"already_seen_skipped": record.get("already_seen_skipped")})
    waves = 10
    for wave in range(waves):  # Stage 1: a quarter of the time
        for entry, job in list(zip(s1, jobs))[wave::waves]:
            fail = job["stage_reached"] == 1
            _write_json(os.path.join(run_dir, "s1", f"{entry['n']}.out.json"),
                        {"verdict": "fail" if fail else "pass", "reason": job["reasoning"] if fail else "",
                         "evidence_sids": [b["s_id"] for b in entry["blocks"]],
                         "blocks": [{"sid": b["s_id"], "why": b["why"]} for b in entry["blocks"]]})
        pause(0.25, waves)

    stage2 = [(e, j) for e, j in zip(s1, jobs) if j["stage_reached"] == 2]
    manifest = [{"n": n, "s1_n": e["n"], "job_url": j["job_url"], "title": j["title"], "company": j["company"],
                 "location": j["location"]} for n, (e, j) in enumerate(stage2)]
    _write_json(os.path.join(run_dir, "s2", "manifest.json"), manifest)
    pause(0.04)
    for entry, (_, job) in zip(manifest, stage2):  # Stage 2: the rest, one job at a time
        criteria = _loads_list(job["criteria"])
        judged = {"status": job["status"], "criteria": criteria or None, "company_line": job["company_line"],
                  "employer": _loads_list(job["employer"]), "notes": _loads_list(job["notes"]),
                  "reasoning": job["reasoning"], "evidence_sentences": _loads_list(job["evidence_sentences"])}
        _write_json(os.path.join(run_dir, "s2", f"{entry['n']}.out.json"), {"criteria": criteria, "_judged": judged})
        pause(0.36, len(manifest))
    time.sleep(2)
    _write_json(os.path.join(run_dir, "done.json"), {"replay_of": run_id})
    return run_dir


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project-dir", default=".")
    ap.add_argument("--db", default="seen.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start", help="create the run directory; prints run_id and run_dir")
    for name, help_ in (("scrape", "scrape one search term (--term N), reporting progress"),
                        ("stage0", "merge the scrapes and run the code-only filter"),
                        ("prepare-stage1", "write Stage 1 prompt files and the manifest"),
                        ("prepare-stage2", "write Stage 2 prompt files for the Stage 1 passes"),
                        ("results", "assemble results.json and stats.json for finalize")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--dir", required=True, help="the run directory printed by `start`")
        if name == "scrape":
            p.add_argument("--term", type=int, required=True, help="index into criteria.json's search_terms")
    rp = sub.add_parser("replay", help="play a finished run back through the live view (demo)")
    rp.add_argument("--run-id", default=None, help="default: the latest run")
    rp.add_argument("--seconds", type=float, default=75)
    rp.add_argument("--per-term", type=int, default=None,
                    help="shorter demo: show the run as if each search term were capped at this many results")
    args = ap.parse_args()

    if args.cmd == "start":
        out = start(args.project_dir)
    elif args.cmd == "scrape":
        out = {"term": args.term, "scraped": scrape_term(args.dir, args.term)}
    elif args.cmd == "stage0":
        out = stage0(args.dir, args.project_dir)
    elif args.cmd == "prepare-stage1":
        out = prepare_stage1(args.dir, args.project_dir, args.db)
    elif args.cmd == "prepare-stage2":
        out = prepare_stage2(args.dir, args.project_dir)
    elif args.cmd == "results":
        out = assemble_results(args.dir)
    else:
        out = {"replayed_into": replay(args.project_dir, args.db, args.run_id, args.seconds, args.per_term)}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
