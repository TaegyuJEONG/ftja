"""Setup, driven entirely from the chat.

The setup chat interviews the user and calls these commands; the viewer is
opened only once setup has written criteria.json and rubric.md. Nothing
here waits for a click: every command checks its input, saves it, prints
the result and returns.

The order is fixed, and each command refuses to run before the one it
builds on:

    profile  the resume (a file inside the project, usually profile/...)
             and the profile summary the user approved
    search   search titles, locations, recency, results wanted, and the
             code filter: keywords per language, readable languages,
             exclude words
    rubric   the user's three answers (clear yes, dealbreakers,
             preferences); code turns them into the rubric list, one
             criterion per line, so the model never rewrites them
    finish   writes criteria.json, profile/summary.md,
             profile/sources.json, rubric-criteria.json and rubric.md

Progress is kept in onboarding-state.json, so a setup that was interrupted
picks up where it stopped (`status` says what comes next).

JSON goes in through --json-file: PowerShell strips the quotes out of JSON
given on the command line. --json works too, on macOS and Linux.

Usage:
    python -m ftja.onboarding . status
    python -m ftja.onboarding . profile --source profile/resume.pdf --summary-file profile/summary.draft.md
    python -m ftja.onboarding . search --json-file .ftja-run/search.json
    python -m ftja.onboarding . rubric --json-file .ftja-run/answers.json
    python -m ftja.onboarding . finish
    python -m ftja.onboarding . reset
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

from ftja import osutil

STATE_FILE = "onboarding-state.json"
STATE_VERSION = 2
STEPS = ("profile", "search", "rubric", "finish")

RUBRIC_QUESTIONS = [
    {"id": "clear_yes", "label": "What makes a role a clear yes?", "hint": "Describe the work, team, and level that would make you want to apply."},
    {"id": "dealbreakers", "label": "What should rule a role out?", "hint": "Name non-negotiables or dealbreakers that need judgment beyond the code-based filter."},
    {"id": "preferences", "label": "What would make a good role even better?", "hint": "Add preferences FTJA should use to rank otherwise good roles."},
]

_RUBRIC_KIND_BY_QUESTION = {"clear_yes": "pass", "dealbreakers": "fail", "preferences": "preference"}


class OnboardingError(ValueError):
    """The input is not valid, or a step it builds on is not done yet."""


# --- the rubric, built by code from the three answers -----------------------

def _criterion_label(text: str) -> str:
    """A short name for one criterion line: the part before a colon or dash
    when that is short, else the first few words."""
    for sep in (":", " — ", " - "):
        head = text.split(sep, 1)[0].strip()
        if sep in text and 2 <= len(head) <= 60:
            return head
    words = text.split()
    label = " ".join(words[:6])
    return label + ("…" if len(words) > 6 else "")


def draft_rubric_criteria(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """The three rubric answers as the rubric list the Pipeline tab edits:
    one criterion per line of an answer (bullets and semicolons also split).
    Each "clear yes" line stands alone (a job passes on any one of them),
    so nothing is silently required together; the candidate can group
    criteria afterwards in the Pipeline tab."""
    criteria: list[dict[str, Any]] = []
    used: set[str] = set()
    for question in questions:
        kind = _RUBRIC_KIND_BY_QUESTION.get(str(question.get("id")))
        if not kind:
            continue
        parts = re.split(r"[\n;]+", str(question.get("answer") or ""))
        for part in parts:
            text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", part).strip()
            if not text:
                continue
            base = cid = re.sub(r"[^a-z0-9]+", "_", _criterion_label(text).lower()).strip("_")[:40] or "criterion"
            n = 2
            while cid in used:
                cid, n = f"{base}_{n}", n + 1
            used.add(cid)
            criteria.append({"id": cid, "kind": kind, "label": _criterion_label(text), "text": text})
    return {"criteria": criteria, "notes": {"pass": "", "fail": "", "preference": ""}}


# --- checking what the chat hands over --------------------------------------

def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _clamp_results_wanted(value: Any, default: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(1000, parsed))


def validate_stage0_draft(card: dict[str, Any]) -> dict[str, Any]:
    """The code filter as proposed from the profile: keywords, the languages
    the user reads, exclude words, and which language each keyword is in."""
    if not isinstance(card, dict):
        raise OnboardingError("the code filter must be an object")
    keywords = _clean_list(card.get("keywords"))
    languages = [code.lower() for code in _clean_list(card.get("languages"))]
    excludes = _clean_list(card.get("exclude_keywords"))
    if not keywords:
        raise OnboardingError("the code filter needs at least one keyword")
    if not languages:
        raise OnboardingError("the code filter needs at least one language the user reads")
    out: dict[str, Any] = {"keywords": keywords, "languages": languages, "exclude_keywords": excludes}
    marked = card.get("keyword_language")
    if isinstance(marked, dict):  # which language each keyword is in (see ftja.keywords); optional
        kept = {str(k): str(v).strip().lower() for k, v in marked.items() if str(k) in keywords and str(v).strip()}
        if kept:
            out["keyword_language"] = kept
    return out


def validate_search(data: dict[str, Any]) -> dict[str, Any]:
    """Search settings plus the code filter -> the criteria.json content."""
    if not isinstance(data, dict):
        raise OnboardingError("search settings must be an object")
    titles = _clean_list(data.get("titles") or data.get("search_terms"))
    locations = _clean_list(data.get("locations")) if isinstance(data.get("locations"), list) else []
    if not locations and str(data.get("location") or "").strip():
        locations = [str(data["location"]).strip()]
    if not titles:
        raise OnboardingError("search settings need at least one job title")
    if not locations:
        raise OnboardingError("search settings need at least one location")
    stage0 = validate_stage0_draft(data.get("filter") or data.get("stage0") or {})
    try:
        hours_old = int(data.get("hours_old", 24))
    except (TypeError, ValueError):
        raise OnboardingError("hours_old must be a whole number of hours")
    keywords: dict[str, Any] = {"tier1": stage0["keywords"]}
    if stage0.get("keyword_language"):
        keywords["tier1_language"] = stage0["keyword_language"]
    criteria: dict[str, Any] = {
        "search_terms": titles,
        "location": ", ".join(locations),
        "locations": locations,
        "hours_old": max(1, hours_old),
        "results_wanted": _clamp_results_wanted(data.get("results_wanted"), 100),
        "languages": stage0["languages"],
        "exclude_keywords": stage0["exclude_keywords"],
        "keywords": keywords,
    }
    if "title_must_match" in data:
        criteria["title_must_match"] = bool(data["title_must_match"])
    return criteria


def validate_answers(data: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise OnboardingError("the rubric answers must be an object")
    questions = []
    for question in RUBRIC_QUESTIONS:
        answer = str(data.get(question["id"]) or "").strip()
        if not answer:
            raise OnboardingError(f"an answer to \"{question['label']}\" ({question['id']}) is required")
        questions.append({**question, "answer": answer})
    return questions


# --- the state file ---------------------------------------------------------

def _path(project_dir: str, name: str) -> str:
    return os.path.join(project_dir, name)


def read_json(project_dir: str, name: str, fallback: Any) -> Any:
    try:
        # Windows refuses to open a file in the instant another process is
        # renaming over it; wait for the rename to finish.
        for attempt in range(20):
            try:
                with open(_path(project_dir, name), encoding="utf-8-sig") as f:
                    return json.load(f)
            except PermissionError:
                if not osutil.WINDOWS or attempt == 19:
                    raise
                time.sleep(0.05)
    except (OSError, ValueError):
        return fallback


def write_json(project_dir: str, name: str, value: Any) -> None:
    os.makedirs(project_dir, exist_ok=True)
    target = _path(project_dir, name)
    fd, temp = tempfile.mkstemp(prefix=f".{name}.", dir=project_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
        osutil.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "profile": None, "search": None, "rubric": None, "finished_at": None}


def read_state(project_dir: str) -> dict[str, Any]:
    """The setup so far. A state file from the old web-driven setup
    (version 1) is not continued: setup starts over."""
    state = read_json(project_dir, STATE_FILE, {})
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return _empty_state()
    return {**_empty_state(), **state}


def _save(project_dir: str, state: dict[str, Any]) -> dict[str, Any]:
    state = {**state, "version": STATE_VERSION, "updated_at": datetime.now(timezone.utc).isoformat()}
    write_json(project_dir, STATE_FILE, state)
    return state


def next_step(state: dict[str, Any]) -> str:
    """The first step not done yet, or "done"."""
    if state.get("finished_at"):
        return "done"
    for step in STEPS[:-1]:
        if not state.get(step):
            return step
    return "finish"


def _require(state: dict[str, Any], step: str) -> None:
    missing = next_step(state)
    if missing == "done":
        missing = "finish"
    if STEPS.index(missing) < STEPS.index(step):
        raise OnboardingError(f"do the {missing} step first")


# --- the steps --------------------------------------------------------------

def save_profile(project_dir: str, sources: list[str], summary: str) -> dict[str, Any]:
    """The resume (and any other files) the profile is built from, and the
    summary the user approved. Sources must exist; paths inside the project
    are stored relative to it, so the folder can move."""
    if not sources:
        raise OnboardingError("at least one source file is required (the resume)")
    summary = str(summary or "").strip()
    if not summary:
        raise OnboardingError("the profile summary is empty")
    root = os.path.realpath(project_dir)
    cleaned = []
    for source in sources:
        full = source if os.path.isabs(source) else os.path.join(project_dir, source)
        if not os.path.exists(full):
            raise OnboardingError(f"source not found: {source}")
        real = os.path.realpath(full)
        stored = os.path.relpath(real, root) if real.startswith(root + os.sep) else real
        stored = stored.replace("\\", "/")
        cleaned.append({"path": stored, "label": os.path.basename(real),
                        "kind": "folder" if os.path.isdir(real) else "file", "enabled": True})
    state = read_state(project_dir)
    state.update(profile={"sources": cleaned, "summary": summary}, finished_at=None)
    return _save(project_dir, state)


def save_search(project_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    state = read_state(project_dir)
    _require(state, "search")
    state.update(search=validate_search(data), finished_at=None)
    return _save(project_dir, state)


def save_rubric(project_dir: str, data: dict[str, Any]) -> dict[str, Any]:
    state = read_state(project_dir)
    _require(state, "rubric")
    questions = validate_answers(data)
    draft = draft_rubric_criteria(questions)
    if not any(c["kind"] == "pass" for c in draft["criteria"]):
        raise OnboardingError("the clear-yes answer gave no criterion")
    state.update(rubric={"questions": questions, "criteria": draft}, finished_at=None)
    return _save(project_dir, state)


def finish(project_dir: str) -> dict[str, Any]:
    """Write the files a run reads, from the confirmed steps."""
    state = read_state(project_dir)
    _require(state, "finish")
    from ftja.pipeline_view import write_config
    from ftja.verdict import save_criteria
    profile = state["profile"]
    write_config(project_dir, criteria=state["search"], profile_md=profile["summary"].rstrip() + "\n",
                 profile_sources=profile["sources"])
    try:
        save_criteria(project_dir, state["rubric"]["criteria"]["criteria"], state["rubric"]["criteria"].get("notes") or {})
    except ValueError as e:
        raise OnboardingError(str(e))
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    return _save(project_dir, state)


def reset(project_dir: str) -> None:
    try:
        os.remove(_path(project_dir, STATE_FILE))
    except OSError:
        pass


# --- command line -----------------------------------------------------------

def _load_payload(args) -> dict[str, Any]:
    if args.json_file:
        with open(args.json_file, encoding="utf-8-sig") as f:
            return json.load(f)
    if args.json:
        return json.loads(args.json)
    raise OnboardingError("give the JSON with --json-file (or --json)")


def _report(project_dir: str, state: dict[str, Any]) -> dict[str, Any]:
    out = {"next": next_step(state)}
    for step in ("profile", "search"):
        if state.get(step):
            out[step] = state[step]
    if state.get("rubric"):
        out["rubric"] = state["rubric"]["criteria"]
    if out["next"] == "rubric":
        out["questions"] = RUBRIC_QUESTIONS
    if state.get("finished_at"):
        out["files"] = ["criteria.json", "rubric.md", "rubric-criteria.json", "profile/summary.md", "profile/sources.json"]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="FTJA setup, driven from the chat")
    parser.add_argument("project_dir", nargs="?", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="What is done and what comes next")
    profile = sub.add_parser("profile", help="Save the resume path(s) and the approved summary")
    profile.add_argument("--source", action="append", default=[], help="a resume or other file; repeat for several")
    profile.add_argument("--summary-file", required=True, help="a file holding the approved profile summary")
    for name, help_text in (("search", "Save search settings and the code filter"),
                            ("rubric", "Save the three answers; prints the rubric list code built from them")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--json-file")
        cmd.add_argument("--json")
    sub.add_parser("finish", help="Write criteria.json, rubric.md and the profile files")
    sub.add_parser("reset", help="Forget the setup so far (written files stay)")
    args = parser.parse_args()
    project_dir = args.project_dir
    try:
        if args.command == "status":
            state = read_state(project_dir)
        elif args.command == "profile":
            with open(args.summary_file, encoding="utf-8-sig") as f:
                summary = f.read()
            state = save_profile(project_dir, args.source, summary)
        elif args.command == "search":
            state = save_search(project_dir, _load_payload(args))
        elif args.command == "rubric":
            state = save_rubric(project_dir, _load_payload(args))
        elif args.command == "finish":
            state = finish(project_dir)
        else:
            reset(project_dir)
            state = read_state(project_dir)
    except (OnboardingError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(_report(project_dir, state), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
