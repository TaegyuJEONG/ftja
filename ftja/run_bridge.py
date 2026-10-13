"""How the viewer's buttons reach the agent: "Continue to Stage 1 / 2" on a
run that stopped early, and "Generate" on the Remote check's setup.

The viewer cannot start the agent. What it can do is leave a request in
the project folder, and an agent session that is waiting there picks it
up — the same arrangement the setup chat uses (ftja.onboarding's bridge).

After a run stops at Stage 1, `/ftja-run` keeps its session waiting for a
while with `wait`. While it waits, `run-bridge.json` says "listening" and
the button's request (`run-action.json`) is answered at once. When nobody
is listening, the viewer falls back to showing the line to type in the
agent: `/ftja-run continue <run id>`.

The Remote check's Generate button uses the same wait: its request is
answered with `{"status": "remote"}`, and the session follows `/ftja-remote`.
When nobody is listening the viewer shows `/ftja-remote` to type instead.

Usage:
    python -m ftja.run_bridge wait --run-id <id> [--timeout 540]
    python -m ftja.run_bridge status
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

from ftja import osutil

BRIDGE_FILE = "run-bridge.json"
ACTION_FILE = "run-action.json"
DEFAULT_TIMEOUT_SECONDS = 540  # under the 10 minutes an agent's shell command may run


def _read(project_dir: str, name: str) -> dict:
    try:
        with open(os.path.join(project_dir, name), encoding="utf-8-sig") as f:
            value = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(project_dir: str, name: str, value: dict):
    path = os.path.join(project_dir, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
    osutil.replace(tmp, path)


def _remove(project_dir: str, name: str):
    try:
        os.remove(os.path.join(project_dir, name))
    except OSError:
        pass


def listening(project_dir: str = ".") -> dict:
    """-> {"listening": bool, "run_id", "until"}: is an agent session waiting
    for the Continue button right now? A wait that was killed leaves its
    file behind, so the process is checked too."""
    bridge = _read(project_dir, BRIDGE_FILE)
    alive = False
    if bridge.get("status") == "listening" and float(bridge.get("until") or 0) > time.time():
        alive = osutil.pid_alive(bridge.get("pid"))
    return {"listening": alive, "run_id": bridge.get("run_id") if alive else None,
            "until": bridge.get("until") if alive else None}


def request_continue(project_dir: str, run_id: str, to: str | None = None) -> dict:
    """The button was pressed. -> {"listening": bool}: whether a waiting
    agent session will pick the request up, or the user has to type it.
    `to` is the stage to run next (Stage 2 when left out)."""
    state = listening(project_dir)
    heard = state["listening"] and state["run_id"] == run_id
    action = {"type": "continue", "run_id": run_id, "requested_at": time.time(),
              "at": datetime.now(timezone.utc).isoformat()}
    if to:
        action["to"] = to
    _write(project_dir, ACTION_FILE, action)
    return {"listening": heard}


def request_keywords(project_dir: str) -> dict:
    """The Pipeline tab asked for a new language's keywords (the request is
    in keywords-request.json). -> {"listening": bool}."""
    heard = listening(project_dir)["listening"]
    if heard:
        _write(project_dir, ACTION_FILE, {"type": "keywords", "requested_at": time.time(),
                                          "at": datetime.now(timezone.utc).isoformat()})
    return {"listening": heard}


def request_remote(project_dir: str) -> dict:
    """The Remote check's Generate button was pressed (its answers are in
    remote-request.json). -> {"listening": bool}. Any waiting session can
    take it, whichever run it is waiting on."""
    heard = listening(project_dir)["listening"]
    if heard:
        _write(project_dir, ACTION_FILE, {"type": "remote", "requested_at": time.time(),
                                          "at": datetime.now(timezone.utc).isoformat()})
    return {"listening": heard}


def wait_for_continue(project_dir: str, run_id: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Block until the viewer asks to continue this run, or the time is up.
    -> {"status": "continue" | "remote" | "keywords" | "timeout", "run_id"}, with `to`
    (the stage to run next) when the button named one. "remote" is the
    Remote check's Generate button. Only a request made while this wait is
    running counts: an old one is not a go-ahead for a step that spends
    this much."""
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError("timeout must be between 1 and 3600 seconds")
    started = time.time()
    _remove(project_dir, ACTION_FILE)
    _write(project_dir, BRIDGE_FILE, {"status": "listening", "run_id": run_id, "pid": os.getpid(),
                                      "since": started, "until": started + timeout_seconds})
    status, to = "timeout", None
    try:
        while time.time() < started + timeout_seconds:
            action = _read(project_dir, ACTION_FILE)
            if (action.get("type") == "continue" and action.get("run_id") == run_id
                    and float(action.get("requested_at") or 0) >= started):
                status, to = "continue", action.get("to")
                break
            if action.get("type") in ("remote", "keywords") and float(action.get("requested_at") or 0) >= started:
                status = action["type"]
                break
            time.sleep(0.5)
    finally:
        _remove(project_dir, ACTION_FILE)
        _write(project_dir, BRIDGE_FILE, {"status": "offline", "run_id": run_id, "last_status": status})
    return {"status": status, "run_id": run_id, **({"to": to} if to else {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wait", help="wait for the viewer's Continue button (or the Remote check's Generate button)")
    w.add_argument("--run-id", required=True)
    w.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    sub.add_parser("status", help="is an agent session waiting for the button?")
    args = ap.parse_args()
    if args.cmd == "wait":
        print(json.dumps(wait_for_continue(args.project_dir, args.run_id, args.timeout)))
    else:
        print(json.dumps(listening(args.project_dir)))


if __name__ == "__main__":
    main()
