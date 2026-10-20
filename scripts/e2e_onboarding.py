"""End-to-end onboarding check in a throwaway copy of this folder.

Replaces the manual loop (delete the folder, stop the viewer, walk through
setup, look at the result): copies the working tree (tracked files plus new
untracked ones, never gitignored state such as criteria.json or profile/)
into an empty temporary folder, starts the viewer there on a free port,
and plays both sides of setup:

- the agent, as the setup skill drives it: `ftja.onboarding` commands, with
  a `wait-for-action` running whenever the page has a decision to make;
- the user, on the page: in headless Chromium (Playwright) clicking the
  same buttons a person clicks, or, without Playwright, sending the same
  requests the page sends.

The answers come from tests/fixtures/e2e/. Your own folder, its files and
a viewer on 8765 are never touched. Each step is timed; the report is
printed as JSON and the exit code is 1 when a check failed.

Usage:
    venv/bin/python scripts/e2e_onboarding.py               # reuse this Python
    venv/bin/python scripts/e2e_onboarding.py --fresh-venv  # also make a new venv and pip install
    venv/bin/python scripts/e2e_onboarding.py --keep        # leave the temp folder for a look
    venv/bin/python scripts/e2e_onboarding.py --browser on --artifacts e2e-out

The browser needs requirements-dev.txt, then `python -m playwright install
chromium`. By default (`--browser auto`) it is used when Playwright is
installed; `--browser on` makes a missing Playwright a failure, as CI
wants; `--browser off` always sends the requests directly. `--artifacts`
saves the report and a screenshot of each card there.
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
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "e2e")
WINDOWS = os.name == "nt"
WAIT_SECONDS = 60  # one decision on the page; a cold CI runner is slow


class CheckFailed(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def fixture(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read() if not name.endswith(".json") else json.load(f)


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


def http(method: str, url: str, body=None):
    """-> (status, JSON body); an error status is returned, not raised."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def playwright_installed() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class Agent:
    """The setup skill's side: onboarding commands, and a wait for each
    decision the page makes."""

    def __init__(self, folder: str, python: str):
        self.folder = folder
        self.python = python

    def command(self, *args, json_file: dict | None = None):
        if json_file is not None:
            path = os.path.join(self.folder, ".ftja-run", "e2e-payload.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(json_file, f)
            args = (*args, "--json-file", path)
        proc = subprocess.run([self.python, "-m", "ftja.onboarding", ".", *args], cwd=self.folder,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        check(proc.returncode == 0, f"onboarding {args[0]} failed: {proc.stdout} {proc.stderr}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def wait(self, *types: str) -> subprocess.Popen:
        """Start `wait-for-action` and return once the page can see it listening."""
        cmd = [self.python, "-m", "ftja.onboarding", ".", "wait-for-action", "--timeout", str(WAIT_SECONDS)]
        for t in types:
            cmd += ["--type", t]
        proc = subprocess.Popen(cmd, cwd=self.folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        bridge_file = os.path.join(self.folder, "onboarding-bridge.json")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise CheckFailed(f"wait-for-action exited early: {proc.communicate()}")
            try:
                with open(bridge_file, encoding="utf-8-sig") as f:
                    bridge = json.load(f)
                if bridge.get("status") == "listening" and bridge.get("pid") == proc.pid:
                    return proc
            except (OSError, json.JSONDecodeError):
                pass
            time.sleep(0.1)
        proc.kill()
        raise CheckFailed("wait-for-action never started listening")

    @staticmethod
    def confirmed(proc: subprocess.Popen, expected_type: str) -> dict:
        try:
            out, err = proc.communicate(timeout=WAIT_SECONDS + 10)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise CheckFailed(f"wait for {expected_type} never returned")
        result = json.loads(out.decode("utf-8"))
        check(result.get("status") == "confirmed" and result["action"]["type"] == expected_type,
              f"wait for {expected_type} returned {result.get('status')}: {result.get('action')}")
        return result


class HttpUser:
    """The user without a browser: the requests the page sends for each click."""

    def __init__(self, base: str):
        self.base = base

    def state(self) -> dict:
        return http("GET", self.base + "/api/onboarding")[1]["state"]

    def send(self, type_: str, **payload) -> dict:
        status, body = http("POST", self.base + "/api/onboarding-action",
                            {"type": type_, "expected_revision": self.state().get("revision", 0), **payload})
        check(status == 200, f"{type_} was refused ({status}): {body.get('error')}")
        return body

    def add_resume(self, path):
        self.send("set_profile_sources", sources=[{"path": path, "label": os.path.basename(path), "kind": "file", "enabled": True}])

    def confirm_sources(self):
        self.send("confirm_profile_sources")

    def confirm_summary(self):
        self.send("confirm_profile_summary")

    def confirm_search(self):
        p = fixture("search.json")["profile"]
        self.send("confirm_profile", titles=p["titles"], locations=p["criteria"]["locations"], hours_old=24)

    def confirm_filter(self):
        card = fixture("search.json")["cards"]["stage0"]
        self.send("confirm_stage0", **card)

    def answer(self, question_id, text):
        self.send("answer_rubric_question", question_id=question_id, answer=text)

    def confirm_rubric(self):
        draft = self.state()["rubric_criteria_draft"]
        self.send("confirm_rubric", criteria=draft["criteria"], notes={})

    def screenshot(self, name):
        pass


class BrowserUser:
    """The user in a real browser, clicking the page's own buttons. Only the
    operating system's file chooser is stood in for: /api/pick-profile-source
    answers with the resume's path."""

    def __init__(self, base: str, artifacts: str | None):
        from playwright.sync_api import sync_playwright
        self.base, self.artifacts = base, artifacts
        self.errors: list[str] = []
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        self.page.on("dialog", lambda d: (self.errors.append(f"alert: {d.message}"), d.dismiss()))
        self.picked = None
        self.page.route("**/api/pick-profile-source", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(self.picked)))
        self.page.goto(base + "/")

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def click(self, selector):
        button = self.page.locator(selector).first
        button.wait_for(state="visible", timeout=WAIT_SECONDS * 1000)
        self.page.wait_for_function("s => { const b = document.querySelector(s); return b && !b.disabled; }",
                                    arg=selector, timeout=WAIT_SECONDS * 1000)
        button.click()
        check(not self.errors, f"the page reported errors: {self.errors}")

    def add_resume(self, path):
        self.picked = {"path": path, "label": os.path.basename(path), "kind": "file", "enabled": True}
        self.click("[data-add-onboarding-source]")
        self.page.get_by_text(os.path.basename(path)).first.wait_for(timeout=WAIT_SECONDS * 1000)

    def confirm_sources(self):
        self.click("[data-confirm-profile-sources]")

    def confirm_summary(self):
        self.page.locator("[data-profile-summary-preview]", has_text="0 to 1").wait_for(timeout=WAIT_SECONDS * 1000)  # the agent's summary is shown
        self.screenshot("1-summary")
        self.click("[data-confirm-profile-summary]")

    def confirm_search(self):
        self.page.locator(".profile-search-card").get_by_text("AI Product Manager").first.wait_for(timeout=WAIT_SECONDS * 1000)
        self.screenshot("2-search")
        self.click("[data-confirm-profile]")

    def confirm_filter(self):
        self.page.get_by_text("produit IA").first.wait_for(timeout=WAIT_SECONDS * 1000)
        self.screenshot("3-filter")
        self.click("[data-confirm-stage0]")

    def answer(self, question_id, text):
        self.page.locator(f'[data-answer-rubric-question][data-question-id="{question_id}"]').wait_for(timeout=WAIT_SECONDS * 1000)
        self.page.fill("#rubric-answer", text)
        self.click("[data-answer-rubric-question]")

    def confirm_rubric(self):
        self.page.locator("#onboarding-rubric").wait_for(timeout=WAIT_SECONDS * 1000)
        self.screenshot("4-rubric")
        self.click("[data-confirm-rubric]")

    def screenshot(self, name):
        if self.artifacts:
            self.page.screenshot(path=os.path.join(self.artifacts, f"{name}.png"), full_page=True)


class Runner:
    def __init__(self):
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


def run(fresh_venv: bool, keep: bool, browser: str = "auto", artifacts: str | None = None) -> dict:
    started = time.monotonic()
    folder = tempfile.mkdtemp(prefix="ftja-e2e-")
    r = Runner()
    agent = Agent(folder, sys.executable)
    use_browser = browser == "on" or (browser == "auto" and playwright_installed())
    report = {"folder": folder, "python": sys.version.split()[0], "platform": sys.platform,
              "user": "browser" if use_browser else "http", "steps": r.steps}
    server = user = None
    waits: list[subprocess.Popen] = []
    try:
        r.step("copy working tree", lambda: {"files": copy_working_tree(folder)})
        r.step("starts without setup files", lambda: check(
            not any(os.path.exists(os.path.join(folder, n)) for n in ("criteria.json", "rubric.md", "profile", "onboarding-state.json")),
            "gitignored setup files leaked into the copy"))

        if fresh_venv:
            def make_venv():
                subprocess.run([sys.executable, "-m", "venv", "venv"], cwd=folder, check=True)
                agent.python = venv_python(folder)
                subprocess.run([agent.python, "-m", "pip", "install", "-q", "-r", "requirements.txt"],
                               cwd=folder, check=True, timeout=900)
            r.step("fresh venv + pip install", make_venv)

        port = free_port()
        base = f"http://127.0.0.1:{port}"

        def start_viewer():
            nonlocal server
            server = subprocess.Popen([agent.python, "-m", "ftja.server", "--port", str(port)], cwd=folder,
                                      env={**os.environ, "FTJA_NO_UPDATE_CHECK": "1"},
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 60  # a cold macOS CI runner takes well over 15 s to import
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise CheckFailed(f"viewer exited: {server.stdout.read().decode(errors='replace')}")
                try:
                    _, body = http("GET", base + "/api/version")
                    check(os.path.realpath(body["project_dir"]) == os.path.realpath(folder),
                          f"viewer shows another folder: {body['project_dir']}")
                    return {"port": port}
                except OSError:
                    time.sleep(0.2)
            server.kill()
            raise CheckFailed(f"viewer did not answer within 60 s: {server.communicate()[0].decode(errors='replace')}")
        r.step("viewer starts", start_viewer)

        def init():
            agent.command("init")
            check(agent.command("next")["next_action"]["actions"] == ["confirm_profile_sources"], "a new setup should wait for the resume")
        r.step("agent: init", init)

        def refused_without_agent():
            status, body = http("POST", base + "/api/onboarding-action", {"type": "confirm_profile_sources", "expected_revision": 0})
            check(status == 409 and "not listening" in body.get("error", ""), f"expected a refusal, got {status} {body}")
        r.step("a click with no agent waiting is refused", refused_without_agent)

        resume = os.path.join(folder, "profile", "resume.md")
        os.makedirs(os.path.dirname(resume))
        shutil.copy(os.path.join(FIXTURES, "resume.md"), resume)

        def open_page():
            nonlocal user
            user = BrowserUser(base, artifacts) if use_browser else HttpUser(base)
        r.step("user opens the page" + (" (browser)" if use_browser else " (requests)"), open_page)

        def decide(agent_waits_for, user_does):
            """One decision: the agent waits for it, the user makes it on the page."""
            def go():
                proc = agent.wait(agent_waits_for)
                waits.append(proc)
                user_does()
                return {"revision": Agent.confirmed(proc, agent_waits_for)["state"]["revision"]}
            return go

        r.step("user adds the resume and confirms", decide("confirm_profile_sources",
                                                           lambda: (user.add_resume(resume), user.confirm_sources())))

        def write_summary():
            agent.command("status", "working", "Reading your resume")
            out = agent.command("set-state", "--stage", "profile", "--phase", "profile_summary",
                                json_file={"profile": {"summary": fixture("summary.md")}})
            check(out["phase"] == "profile_summary_review", f"after the summary: {out}")
        r.step("agent writes the profile summary", write_summary)
        r.step("user confirms the summary", decide("confirm_profile_summary", lambda: user.confirm_summary()))

        def propose_search():
            out = agent.command("set-state", "--stage", "profile", "--phase", "title_proposal", json_file=fixture("search.json"))
            check("confirm_profile" in out["allowed_actions"], f"after the search draft: {out}")
        r.step("agent proposes search settings", propose_search)
        r.step("user confirms search settings", decide("confirm_profile", lambda: user.confirm_search()))
        r.step("user confirms the code filter", decide("confirm_stage0", lambda: user.confirm_filter()))

        answers = fixture("answers.json")
        for qid in ("clear_yes", "dealbreakers", "preferences"):
            r.step(f"user answers {qid}", decide("answer_rubric_question", lambda qid=qid: user.answer(qid, answers[qid])))

        def rubric_draft():
            kinds = [c["kind"] for c in agent.command("next")["state"]["rubric_criteria_draft"]["criteria"]]
            check(kinds.count("pass") == 2 and kinds.count("fail") == 2 and kinds.count("preference") == 2,
                  f"one criterion per line expected, got {kinds}")
            return {"criteria": len(kinds)}
        r.step("rubric built one criterion per line", rubric_draft)
        r.step("user confirms the rubric", decide("confirm_rubric", lambda: user.confirm_rubric()))

        def files_written():
            for name in ("criteria.json", "rubric.md", "rubric-criteria.json", "profile/summary.md", "profile/sources.json"):
                check(os.path.isfile(os.path.join(folder, name)), f"{name} was not written")
            with open(os.path.join(folder, "criteria.json"), encoding="utf-8") as f:
                criteria = json.load(f)
            check(criteria["search_terms"] == ["AI Product Manager", "Chef de produit IA"], f"criteria: {criteria}")
            check("results_wanted" not in criteria and "title_must_match" not in criteria, f"dead settings written: {criteria}")
            with open(os.path.join(folder, "profile", "summary.md"), encoding="utf-8") as f:
                check("0 to 1" in f.read(), "profile/summary.md is not the approved summary")
            check(agent.command("next")["state"]["phase"] == "ready", "setup did not end ready")
        r.step("setup files written", files_written)

        def pipeline_after_setup():
            _, onboarding = http("GET", base + "/api/onboarding")
            check(onboarding["has_criteria"] and onboarding["has_rubric"], "/api/onboarding does not see the written files")
            if isinstance(user, BrowserUser):
                page = user.page
                page.locator("#view-pipeline.active").wait_for(timeout=WAIT_SECONDS * 1000)
                page.locator("#pipeline-config").get_by_text("AI Product Manager").first.wait_for(timeout=WAIT_SECONDS * 1000)
                user.screenshot("5-pipeline")
                check(not user.errors, f"the page reported errors: {user.errors}")
            else:
                _, pipeline = http("GET", base + "/api/pipeline")
                check("AI Product Manager" in json.dumps(pipeline), "/api/pipeline does not show the saved titles")
        r.step("page shows the Pipeline after setup", pipeline_after_setup)
        report["ok"] = True
    except CheckFailed as e:
        report["ok"] = False
        report["failed"] = str(e)
        if isinstance(user, BrowserUser):
            try:
                user.screenshot("failed")
            except Exception:
                pass
    finally:
        for proc in waits:
            if proc.poll() is None:
                proc.kill()
        if isinstance(user, BrowserUser):
            user.close()
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
                    help="the user in Chromium: when Playwright is installed (auto), required (on) or never (off)")
    ap.add_argument("--artifacts", help="a folder for report.json and the screenshots")
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
        lines = [f"### Onboarding E2E: {sys.platform}, Python {report['python']}, {report['user']}: "
                 f"{'passed' if report['ok'] else 'FAILED'} in {report['seconds']} s", "",
                 "| step | result | seconds |", "|---|---|---|"]
        for s in report["steps"]:
            result = "ok" if s["ok"] else f"failed: {s.get('error', '')}"
            lines.append(f"| {s['step']} | {result.replace('|', '/')} | {s.get('seconds', '')} |")
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
