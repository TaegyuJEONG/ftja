"""Setup run by a real agent, with a user on the web view, in a throwaway folder.

scripts/e2e_onboarding.py plays the agent from a script. This plays it with
Claude Code itself: `claude -p "/ftja-setup"` in an empty copy of this
folder, so what is tested is the skill as a user's agent reads it, from
creating the venv to writing the files. The user is a headless Chromium
(Playwright) that answers each card on the page as it appears, and a short
scripted reply in the chat ("yes" to start, the page's own "Stuck? Type ..."
phrase when the agent's turn ends early).

Measured, per run: what the agent cost (the cost Claude Code reports, which
is what the API would charge; on a subscription it is usage, not a bill),
tokens including cache, time and turns per chat turn, permission prompts it
hit, and, from the page, how long the user waited for each card after the
previous click. Results are content-agnostic: the agent writes its own
summary and proposals, so checks are on structure (files written, at least
one title, one pass criterion), not on wording.

Isolation from your machine: the agent gets no MCP servers
(--strict-mcp-config), only this project's settings (--setting-sources
project: no user plugins, hooks or memory), its own viewer port
(FTJA_PORT), and an `open` that only logs, so no browser window pops up.
Permissions are an allowlist (--permissions allowlist, the default) or
skipped entirely (--permissions skip), which is for a throwaway machine
such as a CI runner only.

Usage:
    venv/bin/python scripts/agent_e2e.py                       # Sonnet, artifacts in agent-e2e-out/
    venv/bin/python scripts/agent_e2e.py --model claude-haiku-5-5 --runs 3
    venv/bin/python scripts/agent_e2e.py --keep                # leave the temp folder

Needs `claude` logged in (or CLAUDE_CODE_OAUTH_TOKEN, from `claude
setup-token`), and requirements-dev.txt with `python -m playwright install
chromium`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_onboarding import FIXTURES, WINDOWS, copy_working_tree, free_port, http  # noqa: E402

DEFAULT_MODEL = "claude-sonnet-5-5"
CARD_TIMEOUT = 15 * 60   # the agent installing dependencies or writing a summary
CHAT_REPLIES = 6          # scripted replies before the run counts as stuck
ALLOWED_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    "Bash(venv/bin/python:*)", "Bash(venv/bin/pip:*)", "Bash(python3:*)", "Bash(python3.10:*)", "Bash(python3.11:*)",
    "Bash(python3.12:*)", "Bash(python3.13:*)", "Bash(python:*)", "Bash(/opt/homebrew/bin/python3:*)",
    "Bash(/usr/local/bin/python3:*)", "Bash(git:*)", "Bash(curl:*)", "Bash(lsof:*)", "Bash(nohup:*)",
    "Bash(open:*)", "Bash(xdg-open:*)", "Bash(cd:*)", "Bash(mkdir:*)", "Bash(cp:*)", "Bash(ls:*)", "Bash(pwd)",
    "Bash(cat:*)", "Bash(echo:*)", "Bash(test:*)", "Bash(sleep:*)", "Bash(which:*)", "Bash(command -v:*)",
]


class Failed(AssertionError):
    pass


def check(condition, message):
    if not condition:
        raise Failed(message)


# --- the agent: claude -p, one process per chat turn ----------------------------

class AgentTurn:
    """One `claude -p` call, its stream read on a thread."""

    def __init__(self, cmd: list[str], cwd: str, env: dict, log_path: str):
        self.started = time.monotonic()
        self.result: dict | None = None
        self.tool_uses = 0
        self.last_text = ""
        self.session_id = None
        self.log = open(log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     start_new_session=not WINDOWS)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace")
            self.log.write(line)
            self.log.flush()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.session_id = event.get("session_id") or self.session_id
            if event.get("type") == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        self.tool_uses += 1
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        self.last_text = block["text"].strip()
            elif event.get("type") == "result":
                self.result = event

    def done(self) -> bool:
        return self.proc.poll() is not None and not self.thread.is_alive()

    def wait(self, timeout: float):
        self.proc.wait(timeout=timeout)
        self.thread.join(timeout=10)

    def stop(self):
        if self.proc.poll() is None:
            try:
                if WINDOWS:
                    self.proc.kill()
                else:
                    os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def metrics(self) -> dict:
        r = self.result or {}
        usage = r.get("usage") or {}
        return {
            "seconds": round(time.monotonic() - self.started, 1),
            "duration_ms": r.get("duration_ms"),
            "num_turns": r.get("num_turns"),
            "cost_usd": r.get("total_cost_usd"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "tool_uses": self.tool_uses,
            "permission_denials": [d.get("tool_name") for d in r.get("permission_denials") or []],
            "is_error": r.get("is_error"),
            "subtype": r.get("subtype"),
            "last_message": self.last_text[-400:],
        }


class Agent:
    def __init__(self, folder: str, model: str, permissions: str, port: int, artifacts: str, budget: float):
        self.folder, self.model, self.permissions = folder, model, permissions
        self.artifacts, self.budget = artifacts, budget
        self.session_id = None
        self.turns: list[AgentTurn] = []
        shims = os.path.join(folder, ".e2e-shims")
        os.makedirs(shims)
        for name in ("open", "xdg-open"):  # the skill opens the page in the user's browser: log it instead
            path = os.path.join(shims, name)
            with open(path, "w") as f:
                f.write(f'#!/bin/sh\necho "$@" >> "{os.path.join(artifacts, "opened-urls.txt")}"\n')
            os.chmod(path, 0o755)
        self.env = {**os.environ, "FTJA_PORT": str(port), "FTJA_NO_UPDATE_CHECK": "1",
                    "PATH": shims + os.pathsep + os.environ.get("PATH", "")}
        self.env.pop("CLAUDECODE", None)  # started from inside a Claude Code session: still a fresh one

    def say(self, text: str) -> AgentTurn:
        cmd = [shutil.which("claude") or "claude", "-p", text,  # which: claude.cmd on Windows
                "--output-format", "stream-json", "--verbose", "--model", self.model,
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "project",
               "--max-budget-usd", str(self.budget)]
        if self.permissions == "skip":
            cmd.append("--dangerously-skip-permissions")
        else:
            # acceptEdits: a person approves the skill's file writes (.claude/settings.local.json
            # always asks); -p has no one to ask
            cmd += ["--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        turn = AgentTurn(cmd, self.folder, self.env, os.path.join(self.artifacts, "agent-stream.jsonl"))
        self.turns.append(turn)
        return turn

    def finished(self, turn: AgentTurn):
        self.session_id = turn.session_id or self.session_id

    def totals(self) -> dict:
        keys = ("cost_usd", "input_tokens", "output_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "num_turns", "tool_uses")
        per = [t.metrics() for t in self.turns]
        out = {k: round(sum((m.get(k) or 0) for m in per), 4) for k in keys}
        out["chat_turns"] = len(per)
        out["permission_denials"] = sorted({d for m in per for d in m["permission_denials"]})
        return out


# --- the user: a browser answering the cards -----------------------------------

class User:
    """Answers whatever card the page shows, the way a person would: reads
    it, then clicks its button once the button is enabled."""

    def __init__(self, base: str, resume: str, artifacts: str):
        from playwright.sync_api import sync_playwright
        self.base, self.resume, self.artifacts = base, resume, artifacts
        self.answers = json.load(open(os.path.join(FIXTURES, "answers.json"), encoding="utf-8"))
        self.errors: list[str] = []
        self.waits: list[dict] = []      # card -> seconds from the previous click until it could be clicked
        self.last_click = time.monotonic()
        self.shots = 0
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("dialog", lambda d: (self.errors.append(f"alert: {d.message}"), d.dismiss()))
        self.page.route("**/api/pick-profile-source", lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"path": resume, "label": os.path.basename(resume), "kind": "file", "enabled": True})))
        self.page.goto(base + "/")

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

    def shot(self, name):
        self.shots += 1
        self.page.screenshot(path=os.path.join(self.artifacts, f"{self.shots:02d}-{name}.png"), full_page=True)

    def _enabled(self, selector) -> bool:
        return self.page.evaluate("s => { const b = document.querySelector(s); return !!(b && !b.disabled && b.offsetParent); }", selector)

    def complete(self) -> bool:
        return self.page.evaluate("() => !document.body.classList.contains('onboarding-active') && !!document.querySelector('#view-pipeline.active')")

    def stuck_phrase(self) -> str | None:
        hint = self.page.locator(".onboarding-pending-hint code")
        return hint.first.inner_text() if hint.count() else None

    def step(self) -> str | None:
        """Act on the card on screen, if it can be acted on. -> its name, or None."""
        cards = [
            ("resume", "[data-add-onboarding-source]", self._add_resume),
            ("sources", "[data-confirm-profile-sources]", None),
            ("summary", "[data-confirm-profile-summary]", None),
            ("search-question", "[data-search-answer]", self._answer_search),
            ("search", "[data-confirm-profile]", None),
            ("filter", "[data-confirm-stage0]", None),
            ("question", "[data-answer-rubric-question]", self._answer_rubric),
            ("rubric", "[data-confirm-rubric]", None),
        ]
        for name, selector, act in cards:
            no_source = self.page.get_by_text("No source materials added yet").count() > 0
            if name == "resume" and not no_source:
                continue  # the resume is in; the next thing is its Confirm
            if name == "sources" and no_source:
                continue  # Confirm is enabled before anything is added; a person adds the resume first
            if not self._enabled(selector):
                continue
            self.waits.append({"card": name, "seconds": round(time.monotonic() - self.last_click, 1)})
            self.shot(name)
            (act or (lambda: self.page.click(selector)))()
            self.last_click = time.monotonic()
            self.page.wait_for_timeout(800)
            return name
        return None

    def _add_resume(self):
        self.page.click("[data-add-onboarding-source]")

    def _answer_search(self):
        self.page.fill("#search-answer", "Product manager roles building AI products, in France.")
        self.page.click("[data-search-answer]")

    def _answer_rubric(self):
        qid = self.page.get_attribute("[data-answer-rubric-question]", "data-question-id")
        self.page.fill("#rubric-answer", self.answers.get(qid, "No strong preference."))
        self.page.click("[data-answer-rubric-question]")


# --- one run -----------------------------------------------------------------------

def kill_port(port: int):
    """Stop the viewer the agent started on the test's own port, and nothing else."""
    try:
        if WINDOWS:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
                            "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"], timeout=20)
        else:
            pids = subprocess.run(["lsof", f"-tiTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True).stdout.split()
            for pid in pids:
                os.kill(int(pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def run_once(model: str, permissions: str, artifacts: str, keep: bool, budget: float) -> dict:
    os.makedirs(artifacts, exist_ok=True)
    started = time.monotonic()
    folder = tempfile.mkdtemp(prefix="ftja-agent-e2e-")
    outside = tempfile.mkdtemp(prefix="ftja-agent-e2e-docs-")  # the user's own resume lives outside FTJA
    resume = os.path.join(outside, "resume.md")
    shutil.copy(os.path.join(FIXTURES, "resume.md"), resume)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    report = {"model": model, "permissions": permissions, "platform": sys.platform, "folder": folder, "port": port,
              "phases": [], "ok": False}
    agent = Agent(folder, model, permissions, port, artifacts, budget)
    user = None
    turn = None

    def phase(name, t0, **extra):
        report["phases"].append({"phase": name, "seconds": round(time.monotonic() - t0, 1), **extra})

    try:
        copy_working_tree(folder)

        # Turn 1: install, start the viewer, ask one question
        t0 = time.monotonic()
        turn = agent.say("/ftja-setup")
        turn.wait(timeout=CARD_TIMEOUT)
        agent.finished(turn)
        phase("turn 1: install and open the viewer", t0, **turn.metrics())
        check(turn.result and not turn.result.get("is_error"), f"turn 1 ended in an error: {turn.metrics()}")
        try:
            status, _ = http("GET", base + "/api/version")
        except OSError:
            status = None
        check(status == 200, f"no viewer on the test's port {port} after turn 1: {turn.last_text[-300:]}")
        check(os.path.isdir(os.path.join(folder, "venv")), "turn 1 made no venv")

        # Turn 2 onward: the agent drives the page; the user answers it
        t0 = time.monotonic()
        user = User(base, resume, artifacts)
        turn = agent.say("yes")
        replies = 0
        deadline = time.monotonic() + 3 * CARD_TIMEOUT
        while not user.complete():
            check(time.monotonic() < deadline, "setup did not finish in time")
            check(not user.errors, f"the page reported errors: {user.errors}")
            if user.step():
                continue
            if turn.done():
                agent.finished(turn)
                report["phases"].append({"phase": f"chat turn {len(agent.turns)}", **turn.metrics()})
                if user.complete():
                    break
                replies += 1
                check(replies <= CHAT_REPLIES, f"the agent kept stopping: {turn.last_text[-300:]}")
                phrase = user.stuck_phrase()
                turn = agent.say(phrase or "Please continue the setup; I'll review everything in the web view.")
                continue
            if time.monotonic() - user.last_click > CARD_TIMEOUT:
                raise Failed(f"no card to answer for {CARD_TIMEOUT // 60} minutes")
            time.sleep(1)
        user.shot("pipeline")
        phase("turns 2+: setup on the web view", t0, cards=user.waits)
        if not turn.done():
            turn.wait(timeout=5 * 60)  # the agent's closing message
        agent.finished(turn)

        # what setup left behind
        for name in ("criteria.json", "rubric.md", "rubric-criteria.json", "profile/summary.md"):
            check(os.path.isfile(os.path.join(folder, name)), f"{name} was not written")
        criteria = json.load(open(os.path.join(folder, "criteria.json"), encoding="utf-8"))
        check(criteria.get("search_terms"), "criteria.json has no search terms")
        rubric = json.load(open(os.path.join(folder, "rubric-criteria.json"), encoding="utf-8"))
        check(any(c.get("kind") == "pass" for c in rubric.get("criteria", [])), "the rubric has no pass criterion")
        check(open(os.path.join(folder, "profile", "summary.md"), encoding="utf-8").read().strip(), "the summary is empty")
        report["result"] = {"search_terms": criteria["search_terms"], "locations": criteria.get("locations"),
                            "criteria": len(rubric.get("criteria", []))}
        report["ok"] = True
    except Failed as e:
        report["failed"] = str(e)
    except Exception as e:  # a crash in the harness is still a report
        report["failed"] = f"{type(e).__name__}: {e}"
    finally:
        if user:
            try:
                if not report["ok"]:
                    user.shot("failed")
            except Exception:
                pass
            report["user_waits"] = user.waits
            user.close()
        for t in agent.turns:
            t.stop()
        kill_port(port)
        report["agent"] = agent.totals()
        report["seconds"] = round(time.monotonic() - started, 1)
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
    return report


def summary_md(reports: list[dict]) -> str:
    passed = sum(r["ok"] for r in reports)
    lines = [f"### Agent setup E2E: {passed}/{len(reports)} passed ({reports[0]['model']}, {sys.platform})", "",
             "| run | result | minutes | cost (USD) | output tokens | cache read | chat turns | tool uses | denied |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(reports, 1):
        a = r["agent"]
        lines.append(f"| {i} | {'ok' if r['ok'] else 'FAILED: ' + r.get('failed', '')[:120].replace('|', '/')} | "
                     f"{r['seconds'] / 60:.1f} | {a['cost_usd']:.2f} | {int(a['output_tokens'])} | "
                     f"{int(a['cache_read_input_tokens'])} | {a['chat_turns']} | {int(a['tool_uses'])} | "
                     f"{', '.join(a['permission_denials']) or '-'} |")
    waits = [w for r in reports for w in r.get("user_waits", [])]
    if waits:
        lines += ["", "Waiting for each card (seconds after the previous click): " +
                  ", ".join(f"{w['card']} {w['seconds']}" for w in waits)]
    return "\n".join(lines) + "\n"


def ensure_logged_in():
    """Before spending a run: is `claude` signed in? If not, and someone is
    at the terminal, start the browser sign-in (`claude auth login`): it opens
    the sign-in page and prints its link, and returns once you have signed in."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
        return
    claude = shutil.which("claude") or "claude"

    def logged_in() -> bool:
        out = subprocess.run([claude, "auth", "status"], capture_output=True, text=True, timeout=30).stdout
        try:
            return bool(json.loads(out).get("loggedIn"))
        except json.JSONDecodeError:
            return False

    if logged_in():
        return
    check(sys.stdin.isatty(), "claude is not signed in: run `claude auth login`, or set CLAUDE_CODE_OAUTH_TOKEN")
    print("claude is not signed in. Opening the sign-in page in your browser; "
          "if it does not open, click the link below.", flush=True)
    subprocess.run([claude, "auth", "login", "--claudeai"])
    check(logged_in(), "the sign-in did not finish")
    print("Signed in.", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--runs", type=int, default=1, help="repeat to get a pass rate")
    ap.add_argument("--permissions", choices=("allowlist", "skip"), default="allowlist",
                    help="skip: --dangerously-skip-permissions, for a throwaway machine only")
    ap.add_argument("--budget", type=float, default=5.0, help="--max-budget-usd for each chat turn")
    ap.add_argument("--artifacts", default="agent-e2e-out")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    check(shutil.which("claude"), "the claude command is not on PATH")
    ensure_logged_in()
    reports = []
    for i in range(args.runs):
        out = os.path.abspath(os.path.join(args.artifacts, f"run-{i + 1}"))
        shutil.rmtree(out, ignore_errors=True)
        report = run_once(args.model, args.permissions, out, args.keep, args.budget)
        with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        reports.append(report)
        print(json.dumps({k: report.get(k) for k in ("ok", "failed", "seconds", "agent", "result")}, ensure_ascii=False))
    text = summary_md(reports)
    with open(os.path.join(args.artifacts, "summary.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.exit(0 if all(r["ok"] for r in reports) else 1)


if __name__ == "__main__":
    main()
