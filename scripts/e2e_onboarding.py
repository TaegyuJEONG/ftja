"""End-to-end onboarding check in a throwaway copy of this folder.

Replaces the manual loop (delete the folder, stop the viewer, walk through
setup, look at the result): copies the working tree (tracked files plus new
untracked ones, never gitignored state such as criteria.json or profile/)
into an empty temporary folder, runs every `ftja.onboarding` step the
setup chat runs, with the answers in tests/fixtures/e2e/, then starts the
viewer on a free port and checks what it serves. Your own folder, its
files and a viewer on 8765 are never touched.

Each step is timed; the report is printed as JSON and exit code is 1 when
a check failed.

Usage:
    venv/bin/python scripts/e2e_onboarding.py               # reuse this Python
    venv/bin/python scripts/e2e_onboarding.py --fresh-venv  # also make a new venv and pip install
    venv/bin/python scripts/e2e_onboarding.py --keep        # leave the temp folder for a look
    venv/bin/python scripts/e2e_onboarding.py --browser on --artifacts e2e-out

The browser check opens the Pipeline tab in headless Chromium through
Playwright (requirements-dev.txt; then `python -m playwright install
chromium`). By default it runs when Playwright is installed and is
reported as skipped when it is not; `--browser on` makes a missing
Playwright a failure, as CI wants. `--artifacts` saves the report and the
screenshot there.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "e2e")
WINDOWS = os.name == "nt"


class CheckFailed(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def copy_working_tree(dest: str) -> int:
    """The files a fresh clone would have, as they are now in the working tree."""
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                         cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    count = 0
    for rel in filter(None, out.split("\0")):
        src = os.path.join(ROOT, rel)
        if not os.path.isfile(src):  # deleted in the working tree
            continue
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        count += 1
    return count


def venv_python(folder: str) -> str:
    return os.path.join(folder, "venv", "Scripts" if WINDOWS else "bin", "python.exe" if WINDOWS else "python")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


class Runner:
    def __init__(self, folder: str, python: str):
        self.folder = folder
        self.python = python
        self.steps: list[dict] = []

    def step(self, name, fn):
        start = time.monotonic()
        entry = {"step": name}
        try:
            detail = fn()
            entry["ok"] = True
            if detail is not None:
                entry["detail"] = detail
        except Exception as e:  # report every failure the same way, then stop
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
        entry["seconds"] = round(time.monotonic() - start, 2)
        self.steps.append(entry)
        if not entry["ok"]:
            raise CheckFailed(f"{name}: {entry['error']}")
        return entry.get("detail")

    def onboarding(self, *args, expect_error=False):
        proc = subprocess.run([self.python, "-m", "ftja.onboarding", ".", *args], cwd=self.folder,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise CheckFailed(f"onboarding {args[0]} printed no JSON: {proc.stdout!r} {proc.stderr!r}")
        if expect_error:
            check(proc.returncode == 1 and "error" in out, f"onboarding {args[0]} should have refused: {out}")
        else:
            check(proc.returncode == 0 and "error" not in out, f"onboarding {args[0]} failed: {out}")
        return out


def browser_check(base: str, artifacts: str | None) -> dict:
    """The page a user lands on after setup: the Pipeline tab shows the
    saved titles, the "finish setup" banner is gone, and no script failed."""
    from playwright.sync_api import sync_playwright
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
            page.goto(base + "/#pipeline")
            page.locator("#view-pipeline.active").wait_for(timeout=10000)
            page.locator("#pipeline-config").get_by_text("AI Product Manager").first.wait_for(timeout=10000)
            check(page.locator("#setup-banner").is_hidden(), "the finish-setup banner is still shown after setup")
            shot = None
            if artifacts:
                shot = os.path.join(artifacts, "pipeline.png")
                page.screenshot(path=shot, full_page=True)
        finally:
            browser.close()
    check(not errors, f"the page reported errors: {errors}")
    return {"screenshot": shot} if shot else None


def playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def run(fresh_venv: bool, keep: bool, browser: str = "auto", artifacts: str | None = None) -> dict:
    started = time.monotonic()
    folder = tempfile.mkdtemp(prefix="ftja-e2e-")
    r = Runner(folder, sys.executable)
    server = None
    report = {"folder": folder, "python": sys.version.split()[0], "platform": sys.platform, "steps": r.steps}
    try:
        r.step("copy working tree", lambda: {"files": copy_working_tree(folder)})
        r.step("starts without setup files", lambda: check(
            not any(os.path.exists(os.path.join(folder, n)) for n in ("criteria.json", "rubric.md", "profile")),
            "gitignored setup files leaked into the copy"))

        if fresh_venv:
            def make_venv():
                subprocess.run([sys.executable, "-m", "venv", "venv"], cwd=folder, check=True)
                r.python = venv_python(folder)
                subprocess.run([r.python, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                               cwd=folder, check=True, timeout=900)
            r.step("fresh venv + pip install", make_venv)

        def fresh_status():
            out = r.onboarding("status")
            check(out["next"] == "profile", f"a fresh folder should start at profile: {out}")
        r.step("status on a fresh folder", fresh_status)
        r.step("refuses search before profile", lambda: r.onboarding(
            "search", "--json-file", os.path.join(FIXTURES, "search.json"), expect_error=True) and None)

        def profile():
            os.makedirs(os.path.join(folder, "profile"))
            shutil.copy(os.path.join(FIXTURES, "resume.md"), os.path.join(folder, "profile", "resume.md"))
            shutil.copy(os.path.join(FIXTURES, "summary.md"), os.path.join(folder, "profile", "summary.draft.md"))
            out = r.onboarding("profile", "--source", "profile/resume.md", "--summary-file", "profile/summary.draft.md")
            check(out["next"] == "search", f"after profile: {out}")
            check(out["profile"]["sources"][0]["path"] == "profile/resume.md", f"source path: {out['profile']}")
        r.step("profile", profile)

        def search():
            out = r.onboarding("search", "--json-file", os.path.join(FIXTURES, "search.json"))
            check(out["next"] == "rubric" and len(out["questions"]) == 3, f"after search: {out}")
        r.step("search", search)
        r.step("interrupted setup resumes at rubric", lambda: check(
            r.onboarding("status")["next"] == "rubric", "status after search should say rubric"))

        def rubric():
            out = r.onboarding("rubric", "--json-file", os.path.join(FIXTURES, "answers.json"))
            kinds = [c["kind"] for c in out["rubric"]["criteria"]]
            check(kinds.count("pass") == 2 and kinds.count("fail") == 2 and kinds.count("preference") == 2,
                  f"one criterion per line expected, got {kinds}")
            return {"criteria": len(kinds)}
        r.step("rubric", rubric)

        def finish():
            out = r.onboarding("finish")
            check(out["next"] == "done", f"after finish: {out}")
            for name in out["files"]:
                check(os.path.isfile(os.path.join(folder, name)), f"{name} was not written")
            with open(os.path.join(folder, "criteria.json"), encoding="utf-8") as f:
                criteria = json.load(f)
            check(criteria["search_terms"] == ["AI Product Manager", "Chef de produit IA"], f"criteria: {criteria}")
            with open(os.path.join(folder, "profile", "summary.md"), encoding="utf-8") as f:
                check("0 to 1" in f.read(), "profile/summary.md is not the approved summary")
        r.step("finish writes the files", finish)

        port = free_port()
        base = f"http://127.0.0.1:{port}"

        def start_viewer():
            nonlocal server
            server = subprocess.Popen([r.python, "-m", "ftja.server", "--port", str(port)], cwd=folder,
                                      env={**os.environ, "FTJA_NO_UPDATE_CHECK": "1"},
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise CheckFailed(f"viewer exited: {server.stdout.read().decode(errors='replace')}")
                try:
                    status, body = get_json(base + "/api/version")
                    check(os.path.realpath(body["project_dir"]) == os.path.realpath(folder),
                          f"viewer shows another folder: {body['project_dir']}")
                    return {"port": port}
                except OSError:
                    time.sleep(0.2)
            raise CheckFailed("viewer did not answer within 15 s")
        r.step("viewer starts", start_viewer)

        def viewer_serves():
            with urllib.request.urlopen(base + "/", timeout=5) as resp:
                check(resp.status == 200 and b"<html" in resp.read().lower(), "/ is not the app page")
            _, setup = get_json(base + "/api/setup")
            check(setup == {"has_criteria": True, "has_rubric": True}, f"/api/setup: {setup}")
            _, pipeline = get_json(base + "/api/pipeline")
            check("AI Product Manager" in json.dumps(pipeline), "/api/pipeline does not show the saved titles")
        r.step("viewer serves the setup", viewer_serves)

        if browser == "on" or (browser == "auto" and playwright_installed()):
            r.step("browser: Pipeline tab after setup", lambda: browser_check(base, artifacts))
        else:
            r.steps.append({"step": "browser: Pipeline tab after setup", "ok": True, "skipped": True,
                            "detail": "Playwright not installed" if browser == "auto" else "--browser off"})
        report["ok"] = True
    except CheckFailed as e:
        report["ok"] = False
        report["failed"] = str(e)
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
        report["seconds"] = round(time.monotonic() - started, 2)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh-venv", action="store_true", help="make a new venv and install requirements.txt")
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder")
    ap.add_argument("--browser", choices=("auto", "on", "off"), default="auto",
                    help="the Playwright check: when installed (auto), required (on) or never (off)")
    ap.add_argument("--artifacts", help="a folder for report.json and the screenshot")
    args = ap.parse_args()
    if args.artifacts:
        os.makedirs(args.artifacts, exist_ok=True)
    report = run(args.fresh_venv, args.keep, args.browser, os.path.abspath(args.artifacts) if args.artifacts else None)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.artifacts:
        with open(os.path.join(args.artifacts, "report.json"), "w", encoding="utf-8") as f:
            f.write(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")  # a table on the GitHub Actions run page
    if summary:
        lines = [f"### Onboarding E2E: {sys.platform}, Python {report['python']}: "
                 f"{'passed' if report['ok'] else 'FAILED'} in {report['seconds']} s", "",
                 "| step | result | seconds |", "|---|---|---|"]
        for s in report["steps"]:
            result = "skipped" if s.get("skipped") else ("ok" if s["ok"] else f"failed: {s.get('error', '')}")
            lines.append(f"| {s['step']} | {result.replace('|', '/')} | {s.get('seconds', '')} |")
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
