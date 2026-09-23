"""File-backed coordination between the FTJA skill chat and local web UI."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

STATE_FILE = "onboarding-state.json"
ACTION_FILE = "onboarding-action.json"

DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "stage": "profile",
    "phase": "sources",
    "status": "waiting",
    "messages": [],
    "profile": {"sources": [], "summary": "", "titles": [], "criteria": {}},
    "cards": {},
}


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


def read_action(project_dir: str) -> dict[str, Any] | None:
    action = read_json(project_dir, ACTION_FILE, None)
    return action if isinstance(action, dict) else None


def write_action(project_dir: str, action: dict[str, Any]) -> None:
    write_json(project_dir, ACTION_FILE, {**action, "at": datetime.now(timezone.utc).isoformat()})


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
    add = sub.add_parser("message")
    add.add_argument("role", choices=["agent", "user", "system"])
    add.add_argument("text")
    args = parser.parse_args()
    if args.command == "init":
        write_state(args.project_dir, **({"messages": [{"role": "agent", "text": args.message}]} if args.message else {}))
    elif args.command == "message":
        add_message(args.project_dir, args.role, args.text)
    else:
        payload = json.loads(args.json)
        profile = payload.pop("profile", None)
        cards = payload.pop("cards", None)
        changes = {"stage": args.stage, "phase": args.phase, "status": args.status}
        if args.message:
            changes["messages"] = read_state(args.project_dir)["messages"] + [{"role": "agent", "text": args.message}]
        if profile is not None:
            changes["profile"] = {**read_state(args.project_dir)["profile"], **profile}
        if cards is not None:
            changes["cards"] = cards
        changes.update(payload)
        write_state(args.project_dir, **changes)


if __name__ == "__main__":
    main()
