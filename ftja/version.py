"""Which FTJA this is, and whether a newer one has been published.

FTJA is installed by cloning the repository, so nothing updates it behind
the user's back: a clone stays at the commit it was made from until its
owner pulls. This module is how they find out there is something to pull.

`VERSION` at the repository root is the installed version. `check()`
compares it with the VERSION file on the default branch of the clone's own
`origin` (a fork checks the fork). The command line (`python -m
ftja.version check`, which is what `/ftja-run` and `/ftja-update` call) asks
every time, and asks the repository with `git fetch` rather than the cached
file, so a run never starts on a stale answer. The viewer, which polls, asks at most
once every CHECK_EVERY_HOURS; the last answer is kept in
`.ftja-update-check.json` and is also what an offline check falls back to.
The check is one unauthenticated GET of a small text file. It sends nothing about the user,
never raises, and is skipped entirely when FTJA_NO_UPDATE_CHECK is set.

`/ftja-run` offers the update at the start of an interactive run, the
viewer shows it as a banner, and `/ftja-update` does the pull.
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPO = "TaegyuJEONG/ftja"
CACHE_FILE = ".ftja-update-check.json"
CHECK_EVERY_HOURS = 12
TIMEOUT_SECONDS = 3
GIT_TIMEOUT_SECONDS = 20


def local_version(root: str = REPO_ROOT) -> str:
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3]) or (0,)


def _origin_repo(root: str) -> str:
    """owner/name of the clone's origin on GitHub, or the upstream repository."""
    try:
        url = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_REPO
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else DEFAULT_REPO


def _fetch_latest_with_git(root: str) -> str | None:
    """The VERSION on origin's default branch, asked of the repository
    itself. raw.githubusercontent.com is served through a cache that kept
    returning the previous VERSION for minutes after a release, so a check
    made right after one said "up to date". A fetch has no such cache. It
    downloads the new commits (which the update would need anyway) without
    touching the working tree or the current branch."""
    try:
        fetched = subprocess.run(["git", "-C", root, "fetch", "--quiet", "origin", "HEAD"],
                                 capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
        if fetched.returncode != 0:
            return None
        text = subprocess.run(["git", "-C", root, "show", "FETCH_HEAD:VERSION"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return text if re.fullmatch(r"\d+(\.\d+){0,2}", text) else None


def _fetch_latest(root: str) -> str | None:
    url = f"https://raw.githubusercontent.com/{_origin_repo(root)}/HEAD/VERSION"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            text = response.read(64).decode("utf-8", "replace").strip()
    except (OSError, ValueError):
        return None
    return text if re.fullmatch(r"\d+(\.\d+){0,2}", text) else None


def check(project_dir: str = ".", root: str = REPO_ROOT, force: bool = False) -> dict:
    """-> {"current", "latest", "update_available"}. `latest` is None when
    it could not be found out (offline, check disabled); that is never
    reported as an update."""
    current = local_version(root)
    result = {"current": current, "latest": None, "update_available": False}
    if os.environ.get("FTJA_NO_UPDATE_CHECK"):
        return result
    cache_path = os.path.join(project_dir, CACHE_FILE)
    cached = {}
    try:
        with open(cache_path, encoding="utf-8-sig") as f:
            cached = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    fresh = time.time() - float(cached.get("checked_at") or 0) < CHECK_EVERY_HOURS * 3600
    latest = cached.get("latest") if (fresh and not force) else None
    if latest is None:
        # the viewer polls, so it reads one small cached file; a run or an
        # update asks the repository, which is never stale
        latest = (_fetch_latest_with_git(root) if force else None) or _fetch_latest(root)
        if latest is not None:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"checked_at": time.time(), "latest": latest}, f)
            except OSError:
                pass
        else:
            latest = cached.get("latest")  # offline: the last answer is better than none
    if latest:
        result.update(latest=latest, update_available=parse(latest) > parse(current))
    return result


def changes_since(version: str, root: str = REPO_ROOT) -> str:
    """CHANGELOG.md's sections for every version newer than `version`."""
    try:
        with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8-sig") as f:
            sections = re.split(r"(?m)^(?=## )", f.read())
    except OSError:
        return ""
    newer = []
    for section in sections:
        heading = re.match(r"## +\[?(\d+(?:\.\d+){0,2})", section)
        if heading and parse(heading.group(1)) > parse(version):
            newer.append(section.strip())
    return "\n\n".join(newer)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="is a newer FTJA published? Always exits 0.")
    c.add_argument("--force", action="store_true", help="kept for older instructions; the command always asks now")
    s = sub.add_parser("changes", help="print the changelog for versions newer than --since")
    s.add_argument("--since", required=True)
    args = ap.parse_args()
    if args.cmd == "check":
        result = check(force=True)  # a run must not start on a cached "up to date"
        if result["update_available"]:
            print(f"update available: FTJA {result['latest']} (you have {result['current']})")
        elif result["latest"]:
            print(f"up to date: FTJA {result['current']}")
        else:
            print(f"FTJA {result['current']} (could not check for updates)")
    else:
        print(changes_since(args.since))


if __name__ == "__main__":
    main()
