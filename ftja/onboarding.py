"""File-backed coordination between the FTJA skill chat and local web UI."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any

STATE_FILE = "onboarding-state.json"
ACTION_FILE = "onboarding-action.json"
BRIDGE_FILE = "onboarding-bridge.json"

DEFAULT_BRIDGE: dict[str, Any] = {
    "status": "offline",
    "action_types": [],
    "pid": None,
    "label": "",
    "updated_at": None,
}

# Bridge status values, from the web's point of view:
#   "listening" - a wait-for-action loop is polling; the web's decision
#                  buttons are enabled.
#   "working"   - the setup agent is between waits, doing something the web
#                  should narrate (writing a summary, deriving keywords).
#                  `label` carries the human-readable description.
#   "offline"   - no active bridge process (not started yet, crashed, or a
#                  wait timed out without a follow-up).
BRIDGE_STATUSES = {"listening", "working", "offline"}

DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "revision": 0,
    "stage": "profile",
    "phase": "sources",
    "status": "waiting",
    "allowed_actions": ["set_profile_sources"],
    "messages": [],
    "profile": {"sources": [], "summary": "", "titles": [], "criteria": {}, "sources_confirmed": False, "summary_confirmed": False},
    "cards": {},
    "rubric_questions": [],
}

# The checklist the chat and the web both render, grouped into four stages.
# Each entry's `done`/`current` is computed from onboarding-state.json (plus,
# for the last stage, whether a run has actually produced output) so the two
# surfaces can never drift apart — neither one owns progress on its own.
CHECKLIST_STAGES: list[dict[str, Any]] = [
    {
        "id": "profile",
        "label": "Profile",
        "items": [
            {"id": "add_background_files", "label": "Add your background files"},
            {"id": "review_profile_summary", "label": "Review your profile summary"},
        ],
    },
    {
        "id": "search_settings",
        "label": "Search settings",
        "items": [
            {"id": "review_search_settings", "label": "Review your search settings"},
            {"id": "review_code_filter", "label": "Review the code-based filter"},
        ],
    },
    {
        "id": "judgment",
        "label": "Judgment",
        "items": [
            {"id": "answer_role_questions", "label": "Answer three questions about your ideal role"},
            {"id": "confirm_rubric", "label": "Confirm your judgment rubric"},
        ],
    },
    {
        "id": "first_run",
        "label": "First run",
        "items": [
            {"id": "run_first_search", "label": "Run your first job search"},
            {"id": "review_first_results", "label": "Review your first results"},
        ],
    },
]


def build_checklist(state: dict[str, Any], project_dir: str | None = None) -> list[dict[str, Any]]:
    """Return CHECKLIST_STAGES annotated with each item's status: 'done',
    'current' (the one thing to do right now), or 'pending'."""
    profile = state.get("profile") or {}
    stage, phase = state.get("stage"), state.get("phase")
    questions = state.get("rubric_questions") or []
    questions_answered = sum(1 for q in questions if str(q.get("answer") or "").strip())

    has_run = False
    if project_dir is not None:
        try:
            from ftja.runs_view import list_runs
            has_run = bool(list_runs(project_dir))
        except Exception:
            has_run = False

    done = {
        "add_background_files": bool(profile.get("sources_confirmed")),
        "review_profile_summary": bool(profile.get("summary_confirmed")),
        "review_search_settings": stage in {"rubric", "run"},
        "review_code_filter": stage == "run" or (stage == "rubric" and phase not in {"stage0"}),
        "answer_role_questions": stage == "run" or (stage == "rubric" and phase not in {"stage0", "questions"}),
        "confirm_rubric": stage == "run",
        "run_first_search": has_run,
        "review_first_results": has_run,
    }
    current_id = next((item_id for item_id, is_done in done.items() if not is_done), None)

    stages = []
    for stage_def in CHECKLIST_STAGES:
        items = []
        for item in stage_def["items"]:
            item_id = item["id"]
            status = "done" if done[item_id] else ("current" if item_id == current_id else "pending")
            items.append({**item, "status": status})
        stages.append({"id": stage_def["id"], "label": stage_def["label"], "items": items})
    return stages


DEFAULT_RUBRIC_QUESTIONS = [
    {"id": "clear_yes", "label": "What makes a role a clear yes?", "hint": "Describe the work, team, and level that would make you want to apply."},
    {"id": "dealbreakers", "label": "What should rule a role out?", "hint": "Name non-negotiables or dealbreakers that need judgment beyond the code-based filter."},
    {"id": "preferences", "label": "What would make a good role even better?", "hint": "Add preferences FTJA should use to rank otherwise good roles."},
]



class OnboardingError(ValueError):
    """A requested transition is not valid for the current state."""


def _require(state: dict[str, Any], stage: str, phase: str) -> None:
    if state.get("stage") != stage or state.get("phase") != phase:
        raise OnboardingError(f"action is not allowed in {state.get('stage')}/{state.get('phase')}")


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def validate_stage0_draft(card: dict[str, Any]) -> dict[str, list[str]]:
    """Require a profile-derived, reviewable Stage 0 proposal."""
    if not isinstance(card, dict):
        raise OnboardingError("Stage 0 draft must be an object")
    keywords = _clean_list(card.get("keywords"))
    languages = _clean_list(card.get("languages"))
    excludes = _clean_list(card.get("exclude_keywords"))
    if not keywords:
        raise OnboardingError("Stage 0 draft needs at least one proposed keyword")
    if not languages:
        raise OnboardingError("Stage 0 draft needs at least one readable language")
    return {"keywords": keywords, "languages": languages, "exclude_keywords": excludes}


def _clean_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise OnboardingError("at least one profile source is required")
    result = []
    for source in value:
        if not isinstance(source, dict) or not source.get("path"):
            raise OnboardingError("each profile source needs a path")
        path = str(source["path"])
        kind = "folder" if source.get("kind") == "folder" else "file"
        result.append({"path": path, "label": str(source.get("label") or os.path.basename(os.path.normpath(path))), "kind": kind, "enabled": source.get("enabled", True) is not False})
    return result


def apply_action(project_dir: str, action: dict[str, Any]) -> dict[str, Any]:
    """Apply one legal transition; the browser cannot skip or reorder steps."""
    if not isinstance(action, dict) or not action.get("type"):
        raise OnboardingError("action type required")
    state = read_state(project_dir)
    expected = action.get("expected_revision")
    if expected is not None and int(expected) != int(state.get("revision", 0)):
        raise OnboardingError("stale onboarding revision; reload and try again")
    kind = action["type"]
    profile = state["profile"]
    cards = state.get("cards", {})
    if kind == "set_profile_sources":
        if state.get("stage") != "profile" or state.get("phase") not in {"sources", "profile_summary"}:
            raise OnboardingError(f"action is not allowed in {state.get('stage')}/{state.get('phase')}")
        if profile.get("sources_confirmed"):
            raise OnboardingError("profile sources are already confirmed")
        sources = _clean_sources(action.get("sources"))
        profile = {**profile, "sources": sources, "sources_confirmed": False, "summary_confirmed": False}
        state.update(
            phase="profile_summary",
            allowed_actions=["set_profile_sources", "confirm_profile_sources"],
            profile=profile,
        )
    elif kind == "confirm_profile_sources":
        if state.get("stage") != "profile" or state.get("phase") not in {"sources", "profile_summary"}:
            raise OnboardingError(f"action is not allowed in {state.get('stage')}/{state.get('phase')}")
        if not profile.get("sources"):
            raise OnboardingError("at least one profile source is required")
        profile = {**profile, "sources_confirmed": True}
        state.update(
            phase="profile_summary",
            allowed_actions=["set_profile_summary"],
            profile=profile,
        )
    elif kind == "set_profile_summary":
        if state.get("stage") != "profile" or state.get("phase") not in {"profile_summary", "profile_summary_review"}:
            raise OnboardingError(f"action is not allowed in {state.get('stage')}/{state.get('phase')}")
        if not profile.get("sources_confirmed"):
            raise OnboardingError("confirm the profile sources before writing a summary")
        summary = str(action.get("summary") or "").strip()
        if not summary:
            raise OnboardingError("profile summary is required")
        profile = {**profile, "summary": summary, "summary_confirmed": False}
        state.update(phase="profile_summary_review", allowed_actions=["set_profile_summary", "confirm_profile_summary"], profile=profile, cards={**cards, "profile": {"summary": summary}})
    elif kind == "confirm_profile_summary":
        _require(state, "profile", "profile_summary_review")
        if not profile.get("summary"):
            raise OnboardingError("profile summary is required")
        state.update(phase="title_proposal", allowed_actions=["set_profile_draft"], profile={**profile, "summary_confirmed": True})
    elif kind == "set_profile_draft":
        _require(state, "profile", "title_proposal")
        if not profile.get("summary_confirmed"):
            raise OnboardingError("confirm the profile summary before proposing search conditions")
        titles = _clean_list(action.get("titles"))
        location = str(action.get("location") or "").strip()
        if not titles or not location:
            raise OnboardingError("search settings draft needs at least one title and a location")
        stage0 = validate_stage0_draft(action.get("stage0") or {})
        criteria = {
            **profile.get("criteria", {}),
            "search_terms": titles,
            "location": location,
            "is_remote": bool(action.get("is_remote", False)),
            "hours_old": int(action.get("hours_old", 24)),
            "results_wanted": int(action.get("results_wanted", 100)),
            "exact_phrase_search": True,
            "languages": stage0["languages"],
            "exclude_keywords": stage0["exclude_keywords"],
            "keywords": {"tier1": stage0["keywords"]},
        }
        state.update(
            allowed_actions=["set_profile_draft", "confirm_profile"],
            profile={**profile, "titles": titles, "criteria": criteria},
            cards={**cards, "stage0": stage0},
        )
    elif kind == "confirm_profile":
        _require(state, "profile", "title_proposal")
        if not profile.get("summary_confirmed"):
            raise OnboardingError("confirm the profile summary before setting search conditions")
        stage0 = validate_stage0_draft(cards.get("stage0") or {})
        titles = _clean_list(action.get("titles"))
        location = str(action.get("location") or "").strip()
        if not titles or not location:
            raise OnboardingError("at least one title and a location are required")
        criteria = {
            **profile.get("criteria", {}),
            "search_terms": titles,
            "location": location,
            "is_remote": bool(action.get("is_remote", False)),
            "hours_old": int(action.get("hours_old", 24)),
            "results_wanted": int(profile.get("criteria", {}).get("results_wanted", 100)),
            "exact_phrase_search": True,
            "languages": stage0["languages"],
            "exclude_keywords": stage0["exclude_keywords"],
            "keywords": {"tier1": stage0["keywords"]},
        }
        profile = {**profile, "titles": titles, "criteria": criteria}
        state.update(stage="rubric", phase="stage0", allowed_actions=["confirm_stage0"], profile=profile, cards={**cards, "stage0": stage0})
    elif kind == "set_stage0_draft":
        _require(state, "rubric", "stage0")
        current = cards.get("stage0") or {}
        requested = action.get("card") or {}
        stage0 = validate_stage0_draft({
            "keywords": requested.get("keywords", current.get("keywords", [])),
            "languages": requested.get("languages", current.get("languages", [])),
            "exclude_keywords": requested.get("exclude_keywords", current.get("exclude_keywords", [])),
        })
        state.update(cards={**cards, "stage0": stage0}, allowed_actions=["confirm_stage0"])
    elif kind == "confirm_stage0":
        _require(state, "rubric", "stage0")
        stage0 = validate_stage0_draft({"keywords": action.get("keywords"), "languages": action.get("languages"), "exclude_keywords": action.get("exclude_keywords")})
        keywords = stage0["keywords"]; languages = stage0["languages"]; excludes = stage0["exclude_keywords"]
        criteria = {**profile.get("criteria", {}), "languages": languages, "exclude_keywords": excludes, "keywords": {"tier1": keywords}}
        questions = [{**question, "answer": ""} for question in DEFAULT_RUBRIC_QUESTIONS]
        state.update(phase="questions", allowed_actions=["answer_rubric_question"], profile={**profile, "criteria": criteria}, cards={**cards, "stage0": {"keywords": keywords, "languages": languages, "exclude_keywords": excludes}}, rubric_questions=questions)
    elif kind == "answer_rubric_question":
        _require(state, "rubric", "questions")
        questions = list(state.get("rubric_questions") or [])
        unanswered = next((question for question in questions if not str(question.get("answer") or "").strip()), None)
        question_id = str(action.get("question_id") or "")
        answer = str(action.get("answer") or "").strip()
        if not unanswered or question_id != unanswered.get("id"):
            raise OnboardingError("answer the current rubric question before moving on")
        if not answer:
            raise OnboardingError("an answer is required")
        for question in questions:
            if question.get("id") == question_id:
                question["answer"] = answer
        complete = all(str(question.get("answer") or "").strip() for question in questions)
        state.update(phase="rubric_review" if complete else "questions", allowed_actions=["confirm_rubric"] if complete else ["answer_rubric_question"], rubric_questions=questions)
    elif kind == "confirm_rubric":
        _require(state, "rubric", "rubric_review")
        questions = state.get("rubric_questions") or []
        if not questions or any(not str(question.get("answer") or "").strip() for question in questions):
            raise OnboardingError("answer every rubric question before confirmation")
        rubric = str(action.get("rubric_md") or "").strip()
        if not rubric:
            raise OnboardingError("rubric draft is required before confirmation")
        from ftja.pipeline_view import write_config
        write_config(project_dir, criteria=profile.get("criteria", {}), rubric_md=rubric, profile_md=profile.get("summary", ""), profile_sources=profile.get("sources", []))
        state.update(stage="run", phase="ready", status="ready", allowed_actions=[])

    else:
        raise OnboardingError(f"unknown onboarding action: {kind}")
    state["revision"] = int(state.get("revision", 0)) + 1
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_dir, STATE_FILE, state)
    return state

def _path(project_dir: str, name: str) -> str:
    return os.path.join(project_dir, name)


def read_json(project_dir: str, name: str, fallback: Any) -> Any:
    try:
        with open(_path(project_dir, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(project_dir: str, name: str, value: Any) -> None:
    os.makedirs(project_dir, exist_ok=True)
    target = _path(project_dir, name)
    fd, temp = tempfile.mkstemp(prefix=f".{name}.", dir=project_dir, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_state(project_dir: str) -> dict[str, Any]:
    state = read_json(project_dir, STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    merged = {**DEFAULT_STATE, **state}
    merged["profile"] = {**DEFAULT_STATE["profile"], **(state.get("profile") or {})}
    merged["cards"] = state.get("cards") or {}
    merged["messages"] = state.get("messages") or []
    merged["revision"] = int(state.get("revision", 0))
    merged["allowed_actions"] = state["allowed_actions"] if isinstance(state.get("allowed_actions"), list) else DEFAULT_STATE["allowed_actions"]
    return merged


def write_state(project_dir: str, **changes: Any) -> dict[str, Any]:
    state = read_state(project_dir)
    state.update({k: v for k, v in changes.items() if v is not None})
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project_dir, STATE_FILE, state)
    return state


def add_message(project_dir: str, role: str, text: str) -> dict[str, Any]:
    state = read_state(project_dir)
    messages = list(state.get("messages", []))
    messages.append({"role": role, "text": text, "at": datetime.now(timezone.utc).isoformat()})
    return write_state(project_dir, messages=messages)


def read_bridge(project_dir: str) -> dict[str, Any]:
    bridge = read_json(project_dir, BRIDGE_FILE, {})
    if not isinstance(bridge, dict):
        bridge = {}
    merged = {**DEFAULT_BRIDGE, **bridge}
    merged["action_types"] = [str(item) for item in merged.get("action_types", []) if str(item).strip()]
    return merged


def write_bridge(project_dir: str, status: str, action_types: list[str] | None = None, pid: int | None = None, label: str = "", **extra: Any) -> dict[str, Any]:
    if status not in BRIDGE_STATUSES:
        raise ValueError(f"unknown bridge status: {status}")
    bridge = {
        **DEFAULT_BRIDGE,
        **read_bridge(project_dir),
        "status": status,
        "action_types": [str(item) for item in (action_types or []) if str(item).strip()],
        "pid": pid,
        # Explicit default (not inherited from the previous write) so a
        # "working" label never survives into the next listening/offline
        # state and shows stale text in the web view.
        "label": str(label or ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    write_json(project_dir, BRIDGE_FILE, bridge)
    return bridge


def set_bridge_working(project_dir: str, label: str) -> dict[str, Any]:
    """Tell the web what the setup agent is doing right now, between two
    wait-for-action calls (writing a summary, deriving keywords, ...). The
    web shows `label` with a spinner instead of a plain "connecting" gate."""
    return write_bridge(project_dir, "working", pid=os.getpid(), label=label)


def bridge_is_ready(project_dir: str) -> bool:
    """True when a wait-for-action loop is actively polling for the web's
    next decision, i.e. the web's action buttons should be enabled."""
    bridge = read_bridge(project_dir)
    if bridge.get("status") != "listening":
        return False
    try:
        pid = int(bridge.get("pid"))
        os.kill(pid, 0)
    except (TypeError, ValueError, OSError):
        return False
    return True


def read_action(project_dir: str) -> dict[str, Any] | None:
    action = read_json(project_dir, ACTION_FILE, None)
    return action if isinstance(action, dict) else None


def write_action(project_dir: str, action: dict[str, Any]) -> None:
    write_json(project_dir, ACTION_FILE, {**action, "at": datetime.now(timezone.utc).isoformat()})


def wait_for_action(project_dir: str, action_types: list[str], timeout_seconds: int = 900) -> dict[str, Any]:
    """Wait for one of the web actions written in the connected project folder."""
    if not action_types:
        raise ValueError("at least one action type is required")
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 1 and 3600")
    expected_types = {str(action_type).strip() for action_type in action_types if str(action_type).strip()}
    initial_revision = int(read_state(project_dir).get("revision", 0))
    write_bridge(project_dir, "listening", sorted(expected_types), os.getpid())
    result: dict[str, Any] = {"status": "timeout", "state": read_state(project_dir)}
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            action = read_action(project_dir)
            if action and action.get("type") in expected_types:
                state = read_state(project_dir)
                action_revision = int(action.get("expected_revision", -1))
                current_revision = int(state.get("revision", 0))
                observed_after_wait_started = (
                    initial_revision <= action_revision < current_revision
                    and current_revision > initial_revision
                )
                just_completed_before_wait_started = (
                    current_revision > 0
                    and action_revision == current_revision - 1
                )
                if observed_after_wait_started or just_completed_before_wait_started:
                    result = {"status": "confirmed", "action": action, "state": state}
                    return result
            time.sleep(0.5)
        result = {"status": "timeout", "state": read_state(project_dir)}
        return result
    finally:
        write_bridge(project_dir, "offline", [], None, last_status=result.get("status"))


def wait_for_source_confirm(project_dir: str, timeout_seconds: int = 900) -> dict[str, Any]:
    """Backward-compatible wrapper for the first source-confirm handoff."""
    return wait_for_action(project_dir, ["confirm_profile_sources"], timeout_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Coordinate FTJA chat and web onboarding")
    parser.add_argument("project_dir", nargs="?", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--message", default="")
    set_state = sub.add_parser("set-state")
    set_state.add_argument("--stage", required=True)
    set_state.add_argument("--phase", required=True)
    set_state.add_argument("--status", default="waiting")
    set_state.add_argument("--message", default="")
    set_state.add_argument("--json", default="{}")
    wait = sub.add_parser("wait-for-source-confirm", help="Wait for the web source Confirm action")
    wait.add_argument("--timeout", type=int, default=900)
    wait_action = sub.add_parser("wait-for-action", help="Wait for a web action")
    wait_action.add_argument("--type", dest="action_types", action="append", required=True)
    wait_action.add_argument("--timeout", type=int, default=900)
    add = sub.add_parser("message")
    add.add_argument("role", choices=["agent", "user", "system"])
    add.add_argument("text")
    status_cmd = sub.add_parser("status", help="Tell the web what the setup agent is doing right now")
    status_cmd.add_argument("state", choices=["working", "offline"])
    status_cmd.add_argument("label", nargs="?", default="")
    checklist_cmd = sub.add_parser("checklist", help="Print the current onboarding checklist")
    args = parser.parse_args()
    if args.command == "init":
        write_state(args.project_dir, **({"messages": [{"role": "agent", "text": args.message}]} if args.message else {}))
    elif args.command == "wait-for-source-confirm":
        print(json.dumps(wait_for_source_confirm(args.project_dir, args.timeout), ensure_ascii=False, indent=2))
    elif args.command == "wait-for-action":
        print(json.dumps(wait_for_action(args.project_dir, args.action_types, args.timeout), ensure_ascii=False, indent=2))
    elif args.command == "message":
        add_message(args.project_dir, args.role, args.text)
    elif args.command == "status":
        if args.state == "working":
            print(json.dumps(set_bridge_working(args.project_dir, args.label), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(write_bridge(args.project_dir, "offline", [], None), ensure_ascii=False, indent=2))
    elif args.command == "checklist":
        print(json.dumps(build_checklist(read_state(args.project_dir), args.project_dir), ensure_ascii=False, indent=2))
    else:
        payload = json.loads(args.json)
        state = read_state(args.project_dir)
        profile = payload.get("profile") or {}
        if (state["stage"], state["phase"]) == ("profile", "sources"):
            apply_action(args.project_dir, {"type": "set_profile_sources", "sources": profile.get("sources")})
        elif (state["stage"], state["phase"]) == ("profile", "profile_summary"):
            apply_action(args.project_dir, {"type": "set_profile_summary", "summary": profile.get("summary")})
        elif (state["stage"], state["phase"]) == ("profile", "profile_summary_review"):
            apply_action(args.project_dir, {"type": "confirm_profile_summary"})
        elif (state["stage"], state["phase"]) == ("profile", "title_proposal"):
            criteria = profile.get("criteria") or {}
            stage0_card = (payload.get("cards") or {}).get("stage0", {})
            stage0 = validate_stage0_draft({
                "keywords": stage0_card.get("keywords", criteria.get("keywords", {}).get("tier1", [])),
                "languages": stage0_card.get("languages", criteria.get("languages", [])),
                "exclude_keywords": stage0_card.get("exclude_keywords", criteria.get("exclude_keywords", [])),
            })
            apply_action(args.project_dir, {
                "type": "set_profile_draft",
                "titles": profile.get("titles") or criteria.get("search_terms"),
                "location": criteria.get("location"),
                "is_remote": criteria.get("is_remote", False),
                "hours_old": criteria.get("hours_old", 24),
                "results_wanted": criteria.get("results_wanted", 100),
                "stage0": stage0,
            })
        elif (state["stage"], state["phase"]) == ("rubric", "stage0"):
            card = (payload.get("cards") or {}).get("stage0", {})
            criteria = profile.get("criteria") or {}
            draft = validate_stage0_draft({
                "keywords": card.get("keywords", criteria.get("keywords", {}).get("tier1", [])),
                "languages": card.get("languages", criteria.get("languages", [])),
                "exclude_keywords": card.get("exclude_keywords", criteria.get("exclude_keywords", [])),
            })
            apply_action(args.project_dir, {"type": "set_stage0_draft", "card": draft})
        else:
            raise OnboardingError("set-state cannot bypass the onboarding state machine")
        if args.message:
            add_message(args.project_dir, "agent", args.message)


if __name__ == "__main__":
    main()
