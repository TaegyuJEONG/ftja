"""Concurrency guard for /ftja-run — an age-based lock file.

Not a PID-liveness lock on purpose: acquire/check/release run as separate
short-lived `python -m ftja.lock ...` CLI calls from the orchestrating
skill, not as one long-lived process, so a PID recorded by `acquire` is
already dead by the time anything else checks it (found via smoke test —
PID-liveness silently reported "free" during an active run). Age-based
staleness avoids that: a run should never take longer than STALE_SECONDS,
so a lock older than that is assumed abandoned (e.g. a crash) rather than
still in progress.

Usage:
    python -m ftja.lock check <path>    # exit 1 if locked
    python -m ftja.lock acquire <path>
    python -m ftja.lock release <path>
"""
import os
import sys
import time

STALE_SECONDS = 2 * 60 * 60  # 2h — generous for a daily run of a few hundred jobs


def is_locked(lock_path: str) -> bool:
    if not os.path.exists(lock_path):
        return False
    age = time.time() - os.path.getmtime(lock_path)
    if age > STALE_SECONDS:
        os.remove(lock_path)  # stale lock from a crashed/killed run
        return False
    return True


def acquire(lock_path: str):
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))  # informational only, not used for liveness


def release(lock_path: str):
    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass


def main():
    if len(sys.argv) < 3:
        print("usage: python -m ftja.lock {check|acquire|release} <path>", file=sys.stderr)
        sys.exit(2)
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "check":
        if is_locked(path):
            print("locked", file=sys.stderr)
            sys.exit(1)
        print("free")
    elif cmd == "acquire":
        acquire(path)
        print(f"acquired pid={os.getpid()}")
    elif cmd == "release":
        release(path)
        print("released")
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
