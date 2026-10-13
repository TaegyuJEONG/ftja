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
  remote-check.json        copy of the Remote check's setup, when that stage is on
  stage0.json, stage0-dropped.json, stage0-counts.json
  rc/manifest.json         jobs sent to the Remote check; rc/<n>.txt prompt, rc/<n>.out.json answers
  s1/manifest.json         jobs sent to Stage 1; s1/<n>.txt prompt, s1/<n>.out.json verdict
  s2/manifest.json         jobs sent to Stage 2; s2/<n>.txt prompt, s2/<n>.out.json answer
  results.json, stats.json what finalize reads
  done.json                written by finalize; the run is no longer live

Which judging stages a run does is fixed at its start (`stages` in its
snapshot: any of remote, stage1, stage2, in that order). Each stage judges
what the stage before it let through. A run whose last stage is not Stage 2
ends with its passes waiting, and `continue` reopens it for one more stage.

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

from ftja import remote as remote_check
from ftja import osutil
from ftja.lock import is_locked
from ftja.state import connect, is_seen
from ftja.verdict import (DEFAULT_CRITERIA_PATH, STATUS_FOR_VERDICT, clean_extras, judge, load_criteria,
                          render_criteria_list)

RUNS_DIR = ".ftja-run"
KEEP_RUNS = 5
SNAPSHOT_KEYS = ("search_terms", "location", "locations", "hours_old", "results_wanted", "title_must_match",
                 "stage1_only")
STAGES = ("remote", "stage1", "stage2")
STAGE_NAMES = {"remote": "the Remote check", "stage1": "Stage 1", "stage2": "Stage 2"}
NO_KEYWORD_REASON = "None of your Stage 1 keywords appears in this posting."
PAGE_SIZE = 10
# A replay has no lock to say it is alive; it counts as running while it keeps writing.
REPLAY_STALE_SECONDS = 120


# ---- files -----------------------------------------------------------------

def _read_json(path: str, default=None):
    """Missing, empty or half-written (a subagent is mid-write) all read as `default`."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: str, value):
    """Write-then-rename, so a poll never reads half a file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    osutil.replace(tmp, path)


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
        with open(path, encoding="utf-8-sig") as f:
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
    with open(path, encoding="utf-8-sig") as f:
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

def locations_of(criteria: dict) -> list[str]:
    """Where to search: criteria.json's `locations` list, or the single
    `location` it had before there could be several. One empty entry means
    "no location", so there is always at least one search per term."""
    listed = [str(x).strip() for x in (criteria.get("locations") or []) if str(x).strip()]
    return listed or [str(criteria.get("location") or "").strip()]


def searches(snapshot: dict) -> list[dict]:
    """Every search of a run, in order: each search term in each location
    (3 terms x 4 locations = 12 searches). scrape/<n>.json is search n."""
    return [{"term": term, "location": location}
            for term in (snapshot.get("search_terms") or []) for location in locations_of(snapshot)]


def stages_of(criteria: dict) -> list[str]:
    """The judging stages criteria.json asks for, in order. Its `stages`
    object switches each one on or off; a file from before there were
    switches means Stage 1 and Stage 2, or Stage 1 alone with `stage1_only`."""
    chosen = criteria.get("stages")
    if isinstance(chosen, dict):
        return [s for s in STAGES if chosen.get(s)]
    if isinstance(chosen, list):  # a run's own snapshot
        return [s for s in STAGES if s in chosen]
    return ["stage1"] + ([] if criteria.get("stage1_only") else ["stage2"])


def run_stages(run_dir: str) -> list[str]:
    """The stages this run does: the ones it started with, plus any it was
    continued into since."""
    run = _read_json(os.path.join(run_dir, "run.json"), {}) or {}
    stages = set(stages_of(run.get("criteria_snapshot") or {})) | set(run.get("added_stages") or [])
    if run.get("continued_at") and not run.get("added_stages"):
        stages.add("stage2")  # continued by a version that only knew "Continue to Stage 2"
    return [s for s in STAGES if s in stages]


def start(project_dir: str = ".") -> dict:
    """Step 0: fix the run id and create the directory. From this moment the
    run shows in the viewer's Results tab."""
    criteria = _read_json(os.path.join(project_dir, "criteria.json"), {}) or {}
    stages = stages_of(criteria)
    if not stages:
        raise SystemExit("Every stage is switched off. Turn on at least one of Remote check, Stage 1 and Stage 2 "
                         "on the Pipeline tab.")
    ts_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_dir = run_dir_for(project_dir, ts_start)
    if os.path.exists(run_dir):  # the id is the start time to the second; never reuse a directory
        time.sleep(1)
        return start(project_dir)
    for old in _run_dirs(project_dir)[KEEP_RUNS - 1:]:
        if not awaiting_stage2(old):  # its files are what "Continue" reads
            shutil.rmtree(old, ignore_errors=True)
    os.makedirs(run_dir, exist_ok=True)
    for name in (DEFAULT_CRITERIA_PATH, "rubric.md"):
        shutil.copyfile(os.path.join(project_dir, name), os.path.join(run_dir, name))
    snapshot = {k: criteria.get(k) for k in SNAPSHOT_KEYS if k in criteria}
    snapshot["locations"] = locations_of(criteria)
    snapshot["location"] = ", ".join(snapshot["locations"])  # as one line, for anything that shows it
    snapshot["stages"] = stages
    if "remote" in stages:  # judged against the setup as it is now, like the rubric
        from ftja.keywords import active_remote_config  # keywords of a language no longer read are switched off
        _write_json(os.path.join(run_dir, remote_check.CONFIG_FILE),
                    active_remote_config(remote_check.config_or_default(project_dir), criteria.get("languages")))
    run = {"run_id": ts_start, "ts_start": ts_start, "criteria_snapshot": snapshot}
    _write_json(os.path.join(run_dir, "run.json"), run)
    plan = searches(snapshot)
    for n, search in enumerate(plan):
        _write_json(os.path.join(run_dir, "scrape", f"{n}.progress.json"),
                    {**search, "count": 0, "wanted": snapshot.get("results_wanted"), "done": False})
    return {**run, "run_dir": run_dir, "searches": plan, "stages": stages}


def scrape_term(run_dir: str, n: int) -> int:
    """Step 1, one search (a search term in one location): scrape it,
    reporting the count after every batch."""
    from ftja.scrape import clean_jobs, scrape  # jobspy is slow to import; only here

    snapshot = _read_json(os.path.join(run_dir, "run.json"))["criteria_snapshot"]
    search = searches(snapshot)[n]
    term = search["term"]
    wanted = int(snapshot.get("results_wanted") or 100)
    progress_path = os.path.join(run_dir, "scrape", f"{n}.progress.json")

    def report(count: int, done: bool = False):
        _write_json(progress_path, {**search, "count": count, "wanted": wanted, "done": done})

    jobs = scrape(search_term=term, location=search["location"],
                  results_wanted=wanted,
                  hours_old=int(snapshot.get("hours_old") or 72),
                  on_batch=report)
    _write_json(os.path.join(run_dir, "scrape", f"{n}.json"), clean_jobs(jobs))
    report(len(jobs), done=True)
    return len(jobs)


def stage0(run_dir: str, project_dir: str = ".", db_path: str = "seen.db") -> dict:
    """Step 2: merge the scrapes (one entry per job_url) and run the code-only
    filter. Its last check drops jobs an earlier run already judged (they
    are in seen.db), so what passes Stage 0 is exactly what Stage 1 judges."""
    from ftja.filter_stage0 import filter_stage0

    merged: dict[str, dict] = {}
    scrape_dir = os.path.join(run_dir, "scrape")
    terms = [search["term"] for search in searches((_read_json(os.path.join(run_dir, "run.json"), {}) or {}).get("criteria_snapshot") or {})]
    for name in sorted(os.listdir(scrape_dir), key=lambda s: (len(s), s)):
        if name.endswith(".json") and not name.endswith(".progress.json"):
            n = name[:-len(".json")]
            term = terms[int(n)] if n.isdigit() and int(n) < len(terms) else None  # scrape/<n>.json is search n
            for job in _read_json(os.path.join(scrape_dir, name), []):
                if job.get("job_url"):
                    kept = merged.setdefault(job["job_url"], job)
                    if term and term not in kept.setdefault("_search_terms", []):
                        kept["_search_terms"].append(term)  # which searches returned it
    jobs = list(merged.values())
    criteria = _read_json(os.path.join(project_dir, "criteria.json"), {})
    dropped_jobs: list[dict] = []
    stages = run_stages(run_dir)
    if stages == ["stage1", "stage2"] or stages == ["stage1"]:  # no Remote check: the filter as it always was
        passed, dropped = filter_stage0(jobs, criteria, dropped_jobs)
    else:
        passed, dropped = filter_stage0(jobs, criteria, dropped_jobs, remote=_remote_config(run_dir),
                                        stage1="stage1" in stages)
    with connect(db_path) as conn:
        fresh = [j for j in passed if not is_seen(conn, j.get("job_url", ""))]
    dropped = {**dropped, "already_seen": len(passed) - len(fresh)}
    passed = fresh
    _write_json(os.path.join(run_dir, "stage0.json"), passed)
    _write_json(os.path.join(run_dir, "stage0-dropped.json"), dropped_jobs)
    counts = {"scraped": len(jobs), "passed": len(passed), "dropped": dropped}
    _write_json(os.path.join(run_dir, "stage0-counts.json"), counts)
    return counts


def _rubric_excerpt(rubric_path: str) -> str:
    """rubric.md's Pass and Fail sections — all Stage 1 gets to see of the rubric."""
    with open(rubric_path, encoding="utf-8-sig") as f:
        text = f.read()
    sections = re.split(r"(?m)^(?=## )", text)
    wanted = [s.strip() for s in sections if re.match(r"## +(pass|fail)\b", s, re.I)]
    return "\n\n".join(wanted) if wanted else text.strip()


def _matched(job: dict) -> tuple[list[str], list[str]]:
    blocks = [b for b in job.get("_t1_blocks") or [] if b.get("matched")]
    keywords = list(dict.fromkeys(k for b in blocks for k in b.get("matched_keywords") or []))
    return keywords, [b["sentence"] for b in blocks]


def _entry(job: dict) -> dict:
    return {"job_url": job["job_url"], "title": job.get("title"), "company": job.get("company"),
            # a repost listed for other places too names them all
            "location": " / ".join(filter(None, [job.get("location"), *(job.get("_duplicate_locations") or [])]))}


def _feeding(run_dir: str, stage: str) -> tuple[list[dict], list[int], str | None]:
    """What `stage` gets to judge in this run: the passes of the stage before
    it, or every Stage 0 survivor when it is the first one.
    -> (jobs, numbers the earlier stage has not settled yet, that stage)."""
    earlier = [s for s in run_stages(run_dir) if STAGES.index(s) < STAGES.index(stage)]
    if not earlier:
        jobs = _read_json(os.path.join(run_dir, "stage0.json"), []) or []
        return [{**_entry(job), "n": n} for n, job in enumerate(jobs)], [], None
    folder, command = ("s1", "prepare-stage1") if earlier[-1] == "stage1" else ("rc", "prepare-remote")
    if not os.path.exists(os.path.join(run_dir, folder, "manifest.json")):
        raise SystemExit(f"{STAGE_NAMES[earlier[-1]]} comes first in this run and has not been prepared. "
                         f"Run `{command}` and judge its jobs, then call this again.")
    passed, _, pending = _stage1_results(run_dir) if earlier[-1] == "stage1" else _remote_results(run_dir)
    return passed, pending, earlier[-1]


def _require(run_dir: str, stage: str):
    if stage not in run_stages(run_dir):
        raise SystemExit(f"{STAGE_NAMES[stage][0].upper()}{STAGE_NAMES[stage][1:]} is not part of this run "
                         f"(its stages: {', '.join(run_stages(run_dir))}). Skip this step.")


# ---- the Remote check ---------------------------------------------------------

def _remote_config(run_dir: str) -> dict | None:
    """This run's copy of the Remote check's setup; None when the run has no such stage."""
    if "remote" not in run_stages(run_dir):
        return None
    return remote_check.load_config(os.path.join(run_dir, remote_check.CONFIG_FILE)) or remote_check.default_config()


def prepare_remote(run_dir: str, project_dir: str = ".") -> dict:
    """Remote check setup: one prompt file per Stage 0 survivor that has a
    remote keyword, and the manifest. A survivor without one (kept because
    the setup says so) gets no prompt: every category is "?" for it."""
    _require(run_dir, "remote")
    config = _remote_config(run_dir)
    jobs = _read_json(os.path.join(run_dir, "stage0.json"), []) or []
    jobs = sorted(jobs, key=lambda j: not j.get("_remote_blocks"))  # the ones a model reads are numbered first
    template = _template(os.path.join(project_dir, "remote_prompt.md"))
    os.makedirs(os.path.join(run_dir, "rc"), exist_ok=True)
    manifest = []
    for n, job in enumerate(jobs):
        blocks = job.get("_remote_blocks") or []
        entry = {"n": n, **_entry(job),
                 "blocks": [{"s_id": b["s_id"], "sentence": b["sentence"], "matched": bool(b.get("matched")),
                             "keywords": b.get("matched_keywords") or []} for b in blocks]}
        if blocks:
            with open(os.path.join(run_dir, "rc", f"{n}.txt"), "w", encoding="utf-8") as f:
                f.write(remote_check.render_prompt(template, config, job, blocks))
        else:
            entry["auto"] = True
        manifest.append(entry)
    _write_json(os.path.join(run_dir, "rc", "manifest.json"), manifest)
    read = sum(1 for e in manifest if not e.get("auto"))
    return {"jobs": read, "without_remote_keywords": len(manifest) - read,
            "prompt_files": os.path.join(run_dir, "rc", "<n>.txt"), "output_files": os.path.join(run_dir, "rc", "<n>.out.json")}


def check_remote(run_dir: str) -> dict:
    """Remote check answers that cannot be read (no file, no verdict, an
    answer that is not on a category's list), by job number, so the run
    can rerun them before moving on. A pass without evidence is not one of
    these: it is settled as a fail, not asked again."""
    config = _remote_config(run_dir)
    manifest = [e for e in _read_json(os.path.join(run_dir, "rc", "manifest.json"), []) or [] if not e.get("auto")]
    rerun = {}
    for entry in manifest:
        answer = _read_answer(os.path.join(run_dir, "rc", f"{entry['n']}.out.json"))
        if answer is None:
            rerun[entry["n"]] = ["no usable answer file"]
            continue
        problems = remote_check.answer_problems(config, {b["s_id"] for b in entry["blocks"]}, answer)
        if problems:
            rerun[entry["n"]] = problems
    return {"jobs": len(manifest), "rerun": rerun}


def _remote_results(run_dir: str) -> tuple[list[dict], list[dict], list[int]]:
    """-> (passed, failed, numbers still without a readable answer). The
    user's marks on the answers decide; where the posting gives none of
    them, the model's overall reading does (see `remote_check.judge`)."""
    config = _remote_config(run_dir)
    manifest_path = os.path.join(run_dir, "rc", "manifest.json")
    passed, failed, pending = [], [], []
    for entry in _read_json(manifest_path, []) or []:
        shown = {b["s_id"] for b in entry.get("blocks") or []}
        if entry.get("auto"):
            answer, judged_at = None, _mtime(manifest_path)
        else:
            path = os.path.join(run_dir, "rc", f"{entry['n']}.out.json")
            answer, judged_at = _read_answer(path), _mtime(path)
            if answer is None or remote_check.answer_problems(config, shown, answer):
                pending.append(entry["n"])
                continue
        judged = remote_check.judge(config, answer, shown)
        entry = {**entry, "judged_at": judged_at, "answers": judged["answers"], "reason": judged["reason"],
                 "evidence_sids": judged["evidence_sids"], "decided_by": judged["decided_by"],
                 "remote_blocks": [{"sid": b["s_id"], "sentence": b["sentence"], "matched": bool(b["matched"]),
                                    "keywords": b.get("keywords") or []} for b in entry.get("blocks") or []]}
        (passed if judged["verdict"] == "pass" else failed).append(entry)
    return passed, failed, pending


# ---- Stage 1 ---------------------------------------------------------------------

def prepare_stage1(run_dir: str, project_dir: str = ".") -> dict:
    """Step 3 setup: write one prompt file per job Stage 1 judges (the Stage
    0 survivors, or the Remote check's passes when that stage ran first) and
    the manifest that maps file numbers to jobs."""
    _require(run_dir, "stage1")
    entries, unsettled, feeder = _feeding(run_dir, "stage1")
    if unsettled:
        raise SystemExit(f"{STAGE_NAMES[feeder]} has no usable answer yet for: {unsettled}. Rerun those jobs, then call this again.")
    by_url = {j["job_url"]: j for j in _read_json(os.path.join(run_dir, "stage0.json"), []) or []}
    fresh = [by_url[e["job_url"]] for e in entries if e["job_url"] in by_url]
    # Only a run that started without Stage 1 can hold a job with no keyword
    # sentence. Those are failed by code and numbered last.
    fresh.sort(key=lambda j: not any(b.get("matched") for b in j.get("_t1_blocks") or []))
    template = _template(os.path.join(project_dir, "stage1_prompt.md"))
    excerpt = _rubric_excerpt(os.path.join(run_dir, "rubric.md"))
    os.makedirs(os.path.join(run_dir, "s1"), exist_ok=True)
    manifest = []
    for n, job in enumerate(fresh):
        blocks = job.get("_t1_blocks") or []
        entry = {"n": n, **_entry(job),
                 "blocks": [{"s_id": b["s_id"], "sentence": b["sentence"], "matched": bool(b.get("matched")),
                             "keywords": b.get("matched_keywords") or []} for b in blocks]}
        if not any(b.get("matched") for b in blocks):
            manifest.append({**entry, "auto_fail": NO_KEYWORD_REASON})
            continue
        rendered = "\n".join(f"{b['s_id']}[{'T1' if b.get('matched') else 'ctx'}] {b['sentence']}" for b in blocks)
        prompt = (template.replace("{rubric_pass_fail_excerpt}", excerpt)
                  .replace("{title}", str(job.get("title") or "")).replace("{company}", str(job.get("company") or ""))
                  .replace("{blocks}", rendered))
        with open(os.path.join(run_dir, "s1", f"{n}.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)
        manifest.append(entry)
    _write_json(os.path.join(run_dir, "s1", "manifest.json"), manifest)
    by_code = sum(1 for e in manifest if e.get("auto_fail"))
    out = {"jobs": len(manifest) - by_code,
           "prompt_files": os.path.join(run_dir, "s1", "<n>.txt"), "output_files": os.path.join(run_dir, "s1", "<n>.out.json")}
    if by_code:
        out["failed_by_code_no_keyword"] = by_code
    return out


def stage1_problems(entry: dict, answer: dict) -> tuple[list[str], list[str]]:
    """Check one Stage 1 answer against the sentences that job's prompt
    actually contained. -> (problems, warnings).

    problems make the answer unusable, so the job is rerun:
      - an evidence or block S-id that is not one of the prompt's sentences
        (the model cited something it was never shown)
      - a pass that cites no sentence at all (nothing to show the candidate
        for why it got through)
    warnings are kept but reported: a keyword ([T1]) sentence the model
    gave no reading for, or evidence that is a keyword sentence with none."""
    shown = {b["s_id"]: b for b in entry.get("blocks") or []}
    evidence = [str(x) for x in answer.get("evidence_sids") or []]
    block_sids = [str(b.get("sid")) for b in answer.get("blocks") or [] if isinstance(b, dict)]
    problems, warnings = [], []
    unknown = sorted({x for x in evidence + block_sids if x not in shown})
    if unknown:
        problems.append(f"cites sentences the prompt did not contain: {', '.join(unknown)}")
    if str(answer.get("verdict")).lower() == "pass" and not [x for x in evidence if x in shown]:
        problems.append("a pass with no evidence sentence")
    matched = [sid for sid, b in shown.items() if b.get("matched")]
    unread = [sid for sid in matched if sid not in block_sids]
    if unread:
        warnings.append(f"no reading given for keyword sentence(s): {', '.join(unread)}")
    return problems, warnings


def check_stage1(run_dir: str) -> dict:
    """Every Stage 1 answer's problems and warnings, by job number, so the
    run can rerun the unusable ones before moving on."""
    manifest = [e for e in _read_json(os.path.join(run_dir, "s1", "manifest.json"), []) or [] if not e.get("auto_fail")]
    rerun, warn = {}, {}
    for entry in manifest:
        answer = _read_answer(os.path.join(run_dir, "s1", f"{entry['n']}.out.json"))
        if answer is None:
            rerun[entry["n"]] = ["no usable answer file"]
            continue
        problems, warnings = stage1_problems(entry, answer)
        if str(answer.get("verdict") or "").lower() not in ("pass", "fail"):
            problems.append("no pass/fail verdict")
        if problems:
            rerun[entry["n"]] = problems
        if warnings:
            warn[entry["n"]] = warnings
    return {"jobs": len(manifest), "rerun": rerun, "warnings": warn}


def _stage1_results(run_dir: str) -> tuple[list[dict], list[dict], list[int]]:
    """-> (passed, failed, numbers still without a usable verdict)."""
    manifest_path = os.path.join(run_dir, "s1", "manifest.json")
    manifest = _read_json(manifest_path, []) or []
    passed, failed, pending = [], [], []
    replay = bool((_read_json(os.path.join(run_dir, "run.json"), {}) or {}).get("replay"))
    for entry in manifest:
        if entry.get("auto_fail"):  # no keyword sentence to show a model: failed by code
            failed.append({**entry, "judged_at": _mtime(manifest_path), "reason": entry["auto_fail"],
                           "stage1_warnings": [], "evidence_sentences": [], "t1_blocks": []})
            continue
        path = os.path.join(run_dir, "s1", f"{entry['n']}.out.json")
        answer = _read_answer(path)
        verdict = str((answer or {}).get("verdict") or "").lower()
        if verdict not in ("pass", "fail"):
            pending.append(entry["n"])
            continue
        problems, warnings = ([], []) if replay else stage1_problems(entry, answer)  # a replay's answers are the stored verdicts
        if problems:  # an answer that points at sentences the model never saw is not usable: rerun the job
            pending.append(entry["n"])
            continue
        sids = {str(s) for s in answer.get("evidence_sids") or []}
        whys = {str(b.get("sid")): str(b.get("why") or "").strip()
                for b in answer.get("blocks") or [] if isinstance(b, dict)}
        entry = {**entry, "judged_at": _mtime(path), "reason": str(answer.get("reason") or "").strip(),
                 "stage1_warnings": warnings,
                 "evidence_sentences": [b["sentence"] for b in entry.get("blocks") or [] if b["s_id"] in sids],
                 # everything Stage 1 read, in order: each keyword sentence (matched) with what the
                 # model made of it, and the neighbouring sentences it saw as context
                 "t1_blocks": [{"sid": b["s_id"], "sentence": b["sentence"], "matched": bool(b["matched"]),
                                "keywords": b.get("keywords") or [], "why": whys.get(b["s_id"], "") if b["matched"] else ""}
                               for b in entry.get("blocks") or []]}
        (passed if verdict == "pass" else failed).append(entry)
    return passed, failed, pending


def record_tokens(run_dir: str, tokens: int, label: str = "") -> dict:
    """One subagent call's `usage.subagent_tokens`, appended as it arrives so
    the run's total is a sum of what was recorded, not a number someone
    has to add up and remember at the end."""
    with open(os.path.join(run_dir, "tokens.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"tokens": int(tokens), "label": label, "at": time.time()}) + "\n")
    return {"recorded": int(tokens), "total": tokens_total(run_dir)}


def tokens_total(run_dir: str) -> int:
    total = 0
    try:
        with open(os.path.join(run_dir, "tokens.jsonl"), encoding="utf-8-sig") as f:
            for line in f:
                try:
                    total += int(json.loads(line).get("tokens") or 0)
                except (ValueError, TypeError):
                    continue
    except OSError:
        pass
    return total


def prepare_stage2(run_dir: str, project_dir: str = ".", partial: bool = False) -> dict:
    """Step 4 setup: one prompt file per job Stage 2 judges — the Stage 1
    passes, or the Remote check's when the run has no Stage 1. It only ever
    adds: a job keeps the number it was first given, so it can be called
    again as more Stage 1 verdicts land (`partial`) and Stage 2 can start on
    the first passes while Stage 1 is still working through the rest.
    Without `partial` it refuses while verdicts are missing, naming them,
    so none is silently dropped."""
    _require(run_dir, "stage2")
    passed, pending, feeder = _feeding(run_dir, "stage2")
    if pending and not partial:
        name = "Stage 1" if feeder == "stage1" else "The Remote check"
        raise SystemExit(f"{name} has no usable verdict yet for: {pending}. Rerun those jobs, then call this again.")
    by_url = {j["job_url"]: j for j in _read_json(os.path.join(run_dir, "stage0.json"), [])}
    definitions = load_criteria(os.path.join(run_dir, DEFAULT_CRITERIA_PATH))["criteria"]
    template = (_template(os.path.join(project_dir, "stage2_prompt.md"))
                .replace("{criteria_list}", render_criteria_list(definitions))
                .replace("Read `rubric.md`", f"Read `{os.path.join(run_dir, 'rubric.md')}`"))  # this run's copy
    os.makedirs(os.path.join(run_dir, "s2"), exist_ok=True)
    manifest = _read_json(os.path.join(run_dir, "s2", "manifest.json"), []) or []
    known = {e["job_url"] for e in manifest}
    empty, new = [], []
    for entry in passed:
        job = by_url.get(entry["job_url"], {})
        if not str(job.get("description") or "").strip():
            empty.append(entry["n"])
            continue
        if entry["job_url"] in known:
            continue
        n = len(manifest)
        new.append(n)
        prompt = (template.replace("{title}", str(job.get("title") or "")).replace("{company}", str(job.get("company") or ""))
                  .replace("{location}", str(entry.get("location") or job.get("location") or "")).replace("{full_description}", job["description"]))
        with open(os.path.join(run_dir, "s2", f"{n}.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)
        manifest.append({"n": n, "s1_n": entry["n"], "job_url": entry["job_url"], "title": entry.get("title"),
                         "company": entry.get("company"), "location": entry.get("location")})
    _write_json(os.path.join(run_dir, "s2", "manifest.json"), manifest)
    _write_json(os.path.join(run_dir, "s2", "empty.json"), empty)
    return {"jobs": len(manifest), "new": new, "stage1_still_pending": len(pending),
            "skipped_empty_description": len(empty),
            "prompt_files": os.path.join(run_dir, "s2", "<n>.txt"), "output_files": os.path.join(run_dir, "s2", "<n>.out.json")}


def stops_after(run_dir: str) -> str | None:
    """The stage this run ends at, when that is not Stage 2: what passed it
    is recorded as waiting, and the run can be continued."""
    stages = run_stages(run_dir)
    return stages[-1] if stages and stages[-1] != "stage2" else None


def stops_after_stage1(run_dir: str) -> bool:
    """A run started with Stage 2 switched off (`stage1_only` before there
    were switches) stops once Stage 1 is done, unless it has since been
    continued."""
    return stops_after(run_dir) == "stage1"


def awaiting_stage2(run_dir: str) -> bool:
    """A finished run that stopped before Stage 2, with postings waiting,
    and can still be continued."""
    done = _read_json(os.path.join(run_dir, "done.json"))
    return bool(done and done.get("stage2_pending"))


def continue_run(project_dir: str, run_id: str, to: str | None = None) -> dict:
    """Reopen a run that stopped before Stage 2 so one more stage (`to`:
    Stage 2 unless said otherwise) can judge what is waiting. The run keeps
    its id, its answers so far and its copy of the rubric; it becomes the
    run in progress again (the caller holds the lock)."""
    run_dir = run_dir_for(project_dir, run_id)
    if not awaiting_stage2(run_dir):
        raise SystemExit("That run is not waiting for another stage (it ran to the end, "
                         "it has already been continued, or its working files are gone).")
    last, to = stops_after(run_dir), to or "stage2"
    if to not in STAGES or STAGES.index(to) <= STAGES.index(last):
        raise SystemExit(f"That run stopped after {STAGE_NAMES[last]}; it can go on to "
                         f"{' or '.join(STAGE_NAMES[s] for s in STAGES[STAGES.index(last) + 1:])}.")
    run = _read_json(os.path.join(run_dir, "run.json"), {}) or {}
    run["continued_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run["added_stages"] = [*(run.get("added_stages") or []), to]
    _write_json(os.path.join(run_dir, "run.json"), run)  # also makes it the newest run, which is the live one
    os.remove(os.path.join(run_dir, "done.json"))
    if last == "stage1":
        return {"run_id": run_id, "run_dir": run_dir, "stage1_passes": len(_stage1_results(run_dir)[0])}
    return {"run_id": run_id, "run_dir": run_dir, "to": to, "remote_passes": len(_remote_results(run_dir)[0])}


def assemble_results(run_dir: str) -> dict:
    """Step 5 setup: results.json and stats.json for finalize. Refuses while
    any answer of a stage this run does is missing. For a run that stops
    before Stage 2, what passed its last stage is recorded as waiting
    (`stage1_pass` or `remote_pass`) instead."""
    by_url = {j["job_url"]: j for j in _read_json(os.path.join(run_dir, "stage0.json"), [])}
    stages = run_stages(run_dir)
    for stage, folder, command in (("remote", "rc", "prepare-remote"), ("stage1", "s1", "prepare-stage1"),
                                   ("stage2", "s2", "prepare-stage2")):
        if stage in stages and not os.path.exists(os.path.join(run_dir, folder, "manifest.json")):
            raise SystemExit(f"Not complete — {STAGE_NAMES[stage]} has not been prepared. Run `{command}` and judge its jobs first.")
    remote_passed, remote_failed, remote_pending = _remote_results(run_dir) if "remote" in stages else ([], [], [])
    passed, failed, pending = _stage1_results(run_dir) if "stage1" in stages else ([], [], [])
    stop = stops_after(run_dir)
    s2_manifest = (_read_json(os.path.join(run_dir, "s2", "manifest.json"), []) or []) if "stage2" in stages else []
    answers = {e["job_url"]: _read_answer(os.path.join(run_dir, "s2", f"{e['n']}.out.json")) for e in s2_manifest}
    missing = [e["n"] for e in s2_manifest if not isinstance((answers[e["job_url"]] or {}).get("criteria"), list)]
    if remote_pending or pending or missing:
        remote_part = f"Remote check missing: {remote_pending}; " if "remote" in stages else ""
        raise SystemExit(f"Not complete — {remote_part}Stage 1 missing: {pending}; Stage 2 missing: {missing}. Rerun those, then call this again.")
    remote_by_url = {e["job_url"]: {"answers": e["answers"], "blocks": e["remote_blocks"], "reason": e["reason"],
                                    "evidence_sids": e["evidence_sids"], "decided_by": e["decided_by"]}
                     for e in remote_passed + remote_failed}

    def base(entry: dict) -> dict:
        job = by_url.get(entry["job_url"], {})
        keywords, sentences = _matched(job)
        out = {"job_url": entry["job_url"], "title": entry.get("title") or "", "company": entry.get("company") or "",
               "location": entry.get("location") or ""}
        if job.get("_duplicate_job_urls"):
            out["duplicate_job_urls"] = job["_duplicate_job_urls"]
        if keywords:
            out.update(t1_matched_keywords=keywords, t1_matched_sentences=sentences)
        if entry["job_url"] in remote_by_url:
            out["remote"] = remote_by_url[entry["job_url"]]
        return out

    results, waiting = [], 0
    for entry in remote_failed:
        results.append({**base(entry), "status": "remote_fail", "stage_reached": 0, "verdict": "fail",
                        "reasoning": entry["reason"]})
    for entry in failed:
        results.append({**base(entry), "status": "stage1_fail", "stage_reached": 1, "verdict": "fail",
                        "reasoning": entry["reason"], "evidence_sentences": entry["evidence_sentences"],
                        "t1_blocks": entry["t1_blocks"]})
    if "stage1" in stages:
        for entry in passed:
            if stop:  # waiting for Stage 2: recorded, so the next run does not judge it again
                waiting += 1
                results.append({**base(entry), "status": "stage1_pass", "stage_reached": 1, "verdict": "",
                                "reasoning": entry["reason"], "stage1_reason": entry["reason"],
                                "evidence_sentences": entry["evidence_sentences"], "t1_blocks": entry["t1_blocks"]})
            elif entry["job_url"] in answers:
                results.append({**base(entry), "stage_reached": 2, "stage2": answers[entry["job_url"]],
                                "stage1_reason": entry["reason"], "t1_blocks": entry["t1_blocks"]})
            else:  # passed Stage 1 but had no JD text to judge
                results.append({**base(entry), "status": "stage1_fail", "stage_reached": 1, "verdict": "fail",
                                "reasoning": "Passed Stage 1, but the posting had no description to judge."})
    else:  # a run without Stage 1: Stage 2, when it is on, reads what the Remote check (or Stage 0) let through
        for entry in (_feeding(run_dir, "stage2")[0] if "stage2" in stages else remote_passed):
            if stop:
                waiting += 1
                results.append({**base(entry), "status": "remote_pass", "stage_reached": 0, "verdict": "", "reasoning": ""})
            elif entry["job_url"] in answers:
                results.append({**base(entry), "stage_reached": 2, "stage2": answers[entry["job_url"]]})
            else:
                results.append({**base(entry), "status": "stage2_fail", "stage_reached": 2, "verdict": "fail",
                                "reasoning": "The posting had no description to judge."})
    _write_json(os.path.join(run_dir, "results.json"), results)

    run = _read_json(os.path.join(run_dir, "run.json"), {})
    counts = _read_json(os.path.join(run_dir, "stage0-counts.json"), {}) or {}
    stats = {"ts_start": run.get("ts_start"), "criteria_snapshot": run.get("criteria_snapshot"),
             "stage2_pending": waiting, "continued_at": run.get("continued_at"),
             "stages": stages, "stopped_after": stop,
             "scraped_count": counts.get("scraped"), "stage0_dropped": counts.get("dropped"),
             "scraped_by_term": [{"term": p["term"], "location": p.get("location") or "", "count": p["count"]}
                                 for p in _scrape_progress(run_dir)],
             "already_seen_skipped": (counts.get("dropped") or {}).get("already_seen")}
    _write_json(os.path.join(run_dir, "stats.json"), stats)
    out = {"results": len(results), "stage1_fail": len(failed), "stage2": len(s2_manifest),
           "stopped_after_stage1": stop == "stage1", "waiting_for_stage2": waiting if stop == "stage1" else 0}
    if "remote" in stages or "stage1" not in stages:
        out.update(remote_pass=len(remote_passed), remote_fail=len(remote_failed), stopped_after=stop, waiting=waiting)
    return out


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


def _live_card(run_dir: str, entry: dict, definitions: list[dict], descriptions: dict, stage1: dict | None = None) -> dict:
    card = {k: entry.get(k) for k in ("n", "job_url", "title", "company", "location")}
    # why Stage 1 let it through, so the card says so while the run is live too
    s1 = (stage1 or {}).get(entry.get("job_url")) or {}
    if s1:
        card.update(stage1_reason=s1.get("reason") or "", t1_blocks=s1.get("t1_blocks") or [])
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
           "continued_at": run.get("continued_at"),
           "scrape": {"terms": terms, "total": sum(t.get("count") or 0 for t in terms),
                      "done": bool(terms) and all(t.get("done") for t in terms)},
           "stages": run_stages(run_dir), "stage0": counts, "remote": None, "stage1": None, "stage2": None, "jobs": []}

    if os.path.exists(os.path.join(run_dir, "rc", "manifest.json")):
        passed, failed, pending = _remote_results(run_dir)
        out["remote"] = {"total": len(passed) + len(failed) + len(pending), "pass": len(passed),
                         "fail": len(failed), "pending": len(pending)}

    if os.path.exists(os.path.join(run_dir, "s1", "manifest.json")):
        passed, failed, pending = _stage1_results(run_dir)
        out["stage1"] = {"total": len(passed) + len(failed) + len(pending), "pass": len(passed),
                         "fail": len(failed), "pending": len(pending)}

    manifest = _read_json(os.path.join(run_dir, "s2", "manifest.json"))
    if manifest is not None:
        definitions = (_read_json(os.path.join(run_dir, DEFAULT_CRITERIA_PATH), {}) or {}).get("criteria") or []
        descriptions = {j["job_url"]: j.get("description") or "" for j in _read_json(os.path.join(run_dir, "stage0.json"), [])}
        s1_by_url = {e["job_url"]: e for e in _stage1_results(run_dir)[0]}
        jobs = [_live_card(run_dir, e, definitions, descriptions, s1_by_url) for e in manifest]
        if out["remote"]:  # what the Remote check read stays on the card
            remote_by_url = {e["job_url"]: {"answers": e["answers"], "blocks": e["remote_blocks"], "reason": e["reason"],
                                            "evidence_sids": e["evidence_sids"], "decided_by": e["decided_by"]}
                             for e in _remote_results(run_dir)[0]}
            jobs = [{**j, "remote": remote_by_url.get(j["job_url"])} for j in jobs]
        tally = {s: sum(j["status"] == s for j in jobs) for s in ("judging", "passed", "review", "stage2_fail")}
        out["stage2"] = {"total": len(jobs), "pending": tally["judging"], "passed": tally["passed"],
                         "review": tally["review"], "fail": tally["stage2_fail"]}
        out["jobs"] = jobs
    return out


def stage1_fails(project_dir: str, db_path: str, run_id: str, page: int = 1, verdict: str = "fail") -> dict:
    """Why each job stopped at Stage 1 (or, with verdict="pass", why each got
    through) — from the run directory while the run
    is live, from seen.db afterwards. Per job: the overall reason, the
    keywords that matched, and each keyword sentence with what Stage 1 made
    of it. Runs older than Stage 1 reasons have the sentences and keywords
    only."""
    run_dir = current_run_dir(project_dir)
    live = run_dir and (_read_json(os.path.join(run_dir, "run.json"), {}) or {}).get("run_id") == run_id
    if live:
        passed, failed, _ = _stage1_results(run_dir)
        if verdict == "pass":
            failed = passed
        failed.sort(key=lambda e: e["judged_at"], reverse=True)  # newest verdict first, so page 1 is where they land
        rows = [{"job_url": e["job_url"], "title": e.get("title"), "company": e.get("company"), "location": e.get("location"),
                 "reason": _real_reason(e["reason"]), "blocks": e["t1_blocks"],
                 "keywords": list(dict.fromkeys(k for b in e["t1_blocks"] for k in b["keywords"]))} for e in failed]
    else:
        with connect(db_path) as conn:
            found = conn.execute(
                f"""SELECT job_url, title, company, location, {'stage1_reason' if verdict == 'pass' else 'reasoning'},
                           t1_blocks, t1_matched_sentences, t1_matched_keywords, pruned_at
                    FROM seen_jobs WHERE run_id = ? AND {"(stage_reached = 2 OR status = 'stage1_pass')" if verdict == 'pass' else "status = 'stage1_fail'"}
                    ORDER BY title""", (run_id,)).fetchall()
        rows, shown = [], set()
        for job_url, title, company, location, reasoning, blocks, matched, keywords, pruned_at in found:
            if (title, company, reasoning) in shown:  # a repost under another URL: same judgment, one row
                continue
            shown.add((title, company, reasoning))
            reason = _real_reason(reasoning)
            rows.append({"job_url": job_url, "title": title, "company": company, "location": location,
                         "reason": reason, "keywords": _loads_list(keywords), "pruned_at": pruned_at,
                         "blocks": _loads_list(blocks)
                         or [{"sentence": s, "matched": True, "keywords": [], "why": ""} for s in _loads_list(matched)]})
    total = len(rows)
    page = max(1, page)
    return {"jobs": rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], "page": page, "total": total,
            "total_pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)}


def remote_jobs(project_dir: str, db_path: str, run_id: str, page: int = 1, verdict: str = "fail",
                page_size: int | None = PAGE_SIZE) -> dict:
    """What the Remote check made of each job it failed (or, with
    verdict="pass", each it let through): one answer per category with the
    rule that applied, and the sentences it read. From the run directory
    while the run is live, from seen.db afterwards. `page_size=None` is
    every job on one page (the table download)."""
    run_dir = current_run_dir(project_dir)
    live = run_dir and (_read_json(os.path.join(run_dir, "run.json"), {}) or {}).get("run_id") == run_id
    if live and os.path.exists(os.path.join(run_dir, "rc", "manifest.json")):
        passed, failed, _ = _remote_results(run_dir)
        chosen = sorted(passed if verdict == "pass" else failed, key=lambda e: e["judged_at"], reverse=True)
        rows = [{"job_url": e["job_url"], "title": e.get("title"), "company": e.get("company"), "location": e.get("location"),
                 "reason": e["reason"], "evidence_sids": e["evidence_sids"], "decided_by": e["decided_by"],
                 "answers": e["answers"],
                 "blocks": e["remote_blocks"]} for e in chosen]
    else:
        with connect(db_path) as conn:
            found = conn.execute(
                f"""SELECT job_url, title, company, location, remote, pruned_at FROM seen_jobs
                    WHERE run_id = ? AND {"status != 'remote_fail' AND (remote IS NOT NULL OR status = 'remote_pass')"
                                          if verdict == 'pass' else "status = 'remote_fail'"}
                    ORDER BY title""", (run_id,)).fetchall()
        rows, shown = [], set()
        for job_url, title, company, location, stored, pruned_at in found:
            try:
                stored = json.loads(stored) if stored else {}
            except json.JSONDecodeError:
                stored = {}
            answers = stored.get("answers") or []
            key = (title, company, json.dumps(answers, sort_keys=True))
            if key in shown:  # a repost under another URL: same reading, one row
                continue
            shown.add(key)
            failed_on = [a for a in answers if a.get("rule") == "fail"]
            rows.append({"job_url": job_url, "title": title, "company": company, "location": location,
                         # runs from before the model gave a verdict: the answers that failed
                         "reason": stored.get("reason") or "; ".join(f"{a['label']}: {a['answer_label']}" for a in failed_on),
                         "evidence_sids": stored.get("evidence_sids") or [], "decided_by": stored.get("decided_by") or "",
                         "answers": answers, "blocks": stored.get("blocks") or [], "pruned_at": pruned_at})
    total = len(rows)
    size = page_size or max(1, total)
    page = max(1, page)
    return {"jobs": rows[(page - 1) * size: page * size], "page": page, "total": total,
            "total_pages": max(1, (total + size - 1) // size)}


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
    terms = searches(snapshot) or [{"term": "(search)", "location": ""}]
    by_term = {(t["term"], t.get("location") or ""): t["count"] for t in record.get("scraped_by_term") or []}
    if len(locations_of(snapshot)) == 1:  # recorded before a run could have several locations
        by_term = {(term, terms[0]["location"]): count for (term, _), count in by_term.items()}
    scraped = record.get("scraped_count") or 0
    weights = [rng.uniform(0.5, 1.5) for _ in terms]
    targets = [by_term.get((t["term"], t["location"]), round(scraped * w / sum(weights))) for t, w in zip(terms, weights)]
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
                    {**terms[n], "count": count, "wanted": snapshot.get("results_wanted"), "done": done})

    for n in range(len(terms)):
        progress(n, 0)
    steps = max(3, 24 // len(terms))
    for n, target in enumerate(targets):  # scraping: a quarter of the time, one search term after another
        for step in range(1, steps + 1):
            progress(n, round(target * step / steps), done=step == steps)
            pause(0.25, steps * len(terms))

    dropped, stage0_passed = dict(record.get("stage0_dropped") or {}), record.get("stage0_passed")
    skipped = record.get("already_seen_skipped")
    if "already_seen" not in dropped and skipped is not None:  # recorded before this was a Stage 0 drop
        dropped["already_seen"] = skipped
        stage0_passed = stage0_passed - skipped if stage0_passed is not None else None
    _write_json(os.path.join(run_dir, "stage0-counts.json"), {"scraped": scraped, "passed": stage0_passed, "dropped": dropped})
    _write_json(os.path.join(run_dir, "stage0.json"), [])
    pause(0.06)

    def stored_blocks(j):
        # runs before per-sentence reasons: the sentences, with the job's keywords on the first
        return _loads_list(j["t1_blocks"]) or [
            {"sentence": s, "matched": True, "keywords": _loads_list(j["t1_matched_keywords"]) if i == 0 else [], "why": ""}
            for i, s in enumerate(_loads_list(j["t1_matched_sentences"]))]

    s1 = [{"n": n, "job_url": j["job_url"], "title": j["title"], "company": j["company"], "location": j["location"],
           "blocks": [{"s_id": b.get("sid") or f"S{i * 3}", "sentence": b["sentence"], "matched": b.get("matched", True),
                       "keywords": b.get("keywords") or [], "why": b.get("why") or ""}
                      for i, b in enumerate(stored_blocks(j))]}
          for n, j in enumerate(jobs)]
    _write_json(os.path.join(run_dir, "s1", "manifest.json"), s1)
    waves = 10
    for wave in range(waves):  # Stage 1: a quarter of the time
        for entry, job in list(zip(s1, jobs))[wave::waves]:
            fail = job["stage_reached"] == 1
            _write_json(os.path.join(run_dir, "s1", f"{entry['n']}.out.json"),
                        {"verdict": "fail" if fail else "pass", "reason": job["reasoning"] if fail else "",
                         "evidence_sids": [b["s_id"] for b in entry["blocks"] if b["matched"]],
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
                        ("prepare-remote", "write the Remote check's prompt files and the manifest"),
                        ("check-remote", "list Remote check answers that are not among a category's listed answers"),
                        ("prepare-stage1", "write Stage 1 prompt files and the manifest"),
                        ("prepare-stage2", "write Stage 2 prompt files for the Stage 1 passes"),
                        ("results", "assemble results.json and stats.json for finalize")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--dir", required=True, help="the run directory printed by `start`")
        if name == "prepare-stage2":
            p.add_argument("--partial", action="store_true",
                           help="add prompts for the Stage 1 passes so far, without waiting for all of Stage 1")
        if name == "scrape":
            p.add_argument("--term", "--search", dest="term", type=int, required=True,
                           help="which search, by its number in the `searches` list that `start` printed "
                                "(each search term in each location)")
    co = sub.add_parser("continue", help="reopen a run that stopped before Stage 2, so one more stage can judge what is waiting")
    co.add_argument("--run-id", default="", help="default: the latest run that is waiting")
    co.add_argument("--to", choices=("stage1", "stage2"), default="stage2",
                    help="the stage to run next (default: stage2; stage1 only for a run that stopped after the Remote check)")
    cp = sub.add_parser("check-stage1", help="list Stage 1 answers that cite sentences the prompt never had, or pass with no evidence")
    cp.add_argument("--dir", required=True)
    tp = sub.add_parser("record-tokens", help="add one subagent call's usage.subagent_tokens to the run's total")
    tp.add_argument("--dir", required=True)
    tp.add_argument("--tokens", type=int, required=True)
    tp.add_argument("--label", default="")
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
        out = stage0(args.dir, args.project_dir, args.db)
    elif args.cmd == "prepare-remote":
        out = prepare_remote(args.dir, args.project_dir)
    elif args.cmd == "check-remote":
        out = check_remote(args.dir)
    elif args.cmd == "prepare-stage1":
        out = prepare_stage1(args.dir, args.project_dir)
    elif args.cmd == "prepare-stage2":
        out = prepare_stage2(args.dir, args.project_dir, args.partial)
    elif args.cmd == "results":
        out = assemble_results(args.dir)
    elif args.cmd == "continue":
        run_id = args.run_id or next(((_read_json(os.path.join(d, "run.json"), {}) or {}).get("run_id")
                                      for d in _run_dirs(args.project_dir) if awaiting_stage2(d)), "")
        if not run_id:
            raise SystemExit("No run is waiting for another stage.")
        out = continue_run(args.project_dir, run_id, args.to)
    elif args.cmd == "check-stage1":
        out = check_stage1(args.dir)
    elif args.cmd == "record-tokens":
        out = record_tokens(args.dir, args.tokens, args.label)
    else:
        out = {"replayed_into": replay(args.project_dir, args.db, args.run_id, args.seconds, args.per_term)}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
