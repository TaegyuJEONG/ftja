"""Tokens a Codex sub-agent used, read from its own session log.

Codex writes every thread, sub-agents included, to
`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl`
(`CODEX_HOME` defaults to `~/.codex`). Its last `token_count` event holds
the thread's running total. Prints the sum for the given thread ids, or
nothing and exit code 1 when a thread's log or count cannot be found, so
the caller records no count rather than a wrong one.

Run: venv/bin/python -m ftja.codex_tokens <agent id> [<agent id> ...]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def sessions_dir() -> Path:
    home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(home) / "sessions"


def thread_tokens(thread_id: str, root: Path | None = None) -> int | None:
    root = root or sessions_dir()
    logs = sorted(root.rglob(f"rollout-*{thread_id}.jsonl"))
    if not logs:
        return None
    total = None
    with open(logs[-1], encoding="utf-8") as f:
        for line in f:
            if '"token_count"' not in line:
                continue
            try:
                payload = json.loads(line).get("payload") or {}
                usage = (payload.get("info") or {}).get("total_token_usage") or {}
            except (ValueError, AttributeError):
                continue
            if isinstance(usage.get("total_tokens"), int):
                total = usage["total_tokens"]
    return total


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m ftja.codex_tokens <agent id> [...]", file=sys.stderr)
        return 2
    total = 0
    for thread_id in argv:
        tokens = thread_tokens(thread_id)
        if tokens is None:
            print(f"no token count found for {thread_id}", file=sys.stderr)
            return 1
        total += tokens
    print(total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
