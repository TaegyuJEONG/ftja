"""macOS notification, best-effort. Never raises — a notification failure
must not stop a run from writing its digest and log."""
import subprocess


def _as_applescript_string(s: str) -> str:
    """Escape for embedding in an AppleScript double-quoted string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, message: str):
    try:
        script = (
            f"display notification {_as_applescript_string(message)} "
            f"with title {_as_applescript_string(title)}"
        )
        subprocess.run(["osascript", "-e", script], check=False, timeout=10)
    except Exception as e:
        print(f"[notify] failed (non-fatal): {e}")
