"""Local review server — serves app.html (Pipeline / Results tabs) and a
JSON API over seen.db + runs.jsonl + criteria.json/rubric.md, so the
candidate can review config, browse past runs, and record apply/skip
decisions interactively in a real browser — no hand-edited files, no
regeneration step (everything below is read live on each request).

Why a server instead of the File System Access API: the FSA route only
works in Chromium browsers and is a dead end if FTJA ever needs to support
other users over the web — this server's request/response shape (HTTP JSON
API over a persistent store) is the same shape a hosted multi-user version
would use, so extending it later means adding auth/multi-tenancy, not
rewriting the interaction model.

Binds to 127.0.0.1 only — never exposed beyond the local machine.

Run: venv/bin/python -m ftja.server [--port 8765] [--db seen.db] [--project-dir .]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ftja.state import connect, update_decision, VALID_USER_ACTIONS
from ftja.pipeline_view import read_config, write_config, keyword_stats
from ftja.runs_view import list_runs
from ftja.onboarding import OnboardingError, apply_action, read_state, write_action, write_state

DEFAULT_PORT = 8765
APP_HTML_PATH = os.path.join(os.path.dirname(__file__), "app.html")
REPO_ROOT = os.path.dirname(os.path.dirname(__file__))
LANDING_HTML_PATH = os.path.join(REPO_ROOT, "landing.html")
VALUE_ASSETS = {
    f"/assets/value/{name}": os.path.join(REPO_ROOT, "assets", "value", name)
    for name in (
        "your-background.svg",
        "agent-judgment.svg",
        "better-next-searches.svg",
    )
}
PAGE_SIZE = 10
ONBOARDING_STATE = "onboarding-state.json"


def _onboarding_state(project_dir: str) -> dict:
    path = os.path.join(project_dir, ONBOARDING_STATE)
    if not os.path.exists(path):
        return {"step": "background", "status": "not_started"}
    try:
        with open(path) as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {"step": "background", "status": "not_started"}
    except (OSError, json.JSONDecodeError):
        return {"step": "background", "status": "error"}


def _write_onboarding_state(project_dir: str, state: dict):
    path = os.path.join(project_dir, ONBOARDING_STATE)
    with open(path, "w") as f:
        json.dump({**state, "updated_at": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _fetch_jobs(db_path: str, run_id: str, status: str, page: int) -> dict:
    """Jobs from ONE run, filtered by status, 10 per page. Only Stage2-
    reached jobs are ever returned (stage_reached=2) — Stage0/1 fails have
    no reasoning worth reviewing here.

    Content-hash dedup (ftja.filter_stage0.dedupe_by_content) makes
    finalize() write one seen.db row per URL alias of the same repost —
    same title/company/reasoning, different job_url. There's no stored
    "is representative" flag, so dedupe here the same way digest.md does:
    group by (title, company, reasoning) and keep one row per group,
    noting how many aliases it stood in for."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT job_url, title, company, location, status, verdict,
                      reasoning, evidence_sentences, t1_matched_keywords,
                      user_action, user_reason, last_seen_at
               FROM seen_jobs
               WHERE stage_reached = 2 AND run_id = ? AND status = ?
               ORDER BY last_seen_at DESC""",
            (run_id, status),
        ).fetchall()
    cols = ["job_url", "title", "company", "location", "status", "verdict",
            "reasoning", "evidence_sentences", "t1_matched_keywords",
            "user_action", "user_reason", "last_seen_at"]
    seen_groups: dict[tuple, dict] = {}
    for row in rows:
        job = dict(zip(cols, row))
        for key in ("evidence_sentences", "t1_matched_keywords"):
            try:
                job[key] = json.loads(job[key]) if job[key] else []
            except (TypeError, json.JSONDecodeError):
                job[key] = []
        group_key = (job["title"], job["company"], job["reasoning"])
        if group_key not in seen_groups:
            job["repost_count"] = 1
            seen_groups[group_key] = job
        else:
            seen_groups[group_key]["repost_count"] += 1
    jobs = list(seen_groups.values())

    total = len(jobs)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    return {"jobs": jobs[start:start + PAGE_SIZE], "page": page, "total_pages": total_pages, "total": total}


def make_handler(db_path: str, project_dir: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet; this is a local single-user tool

        def _send_json(self, status: int, payload):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _pick_profile_source(self, kind="file"):
            if kind not in {"file", "folder"}:
                self._send_json(400, {"error": "source kind must be file or folder"})
                return
            if sys.platform != "darwin":
                self._send_json(501, {"error": "native source picking is currently supported on macOS only"})
                return
            if kind == "folder":
                script = 'POSIX path of (choose folder with prompt "Select a source folder")'
            else:
                script = 'POSIX path of (choose file with prompt "Select a source file")'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if result.returncode != 0:
                self._send_json(204, {})
                return
            path = result.stdout.strip()
            if not path or not os.path.exists(path):
                self._send_json(400, {"error": "the selected source no longer exists"})
                return
            self._send_json(200, {
                "path": path,
                "label": os.path.basename(os.path.normpath(path)),
                "kind": kind,
            })

        def do_GET(self):
            parsed = urlparse(self.path)
            path, qs = parsed.path, parse_qs(parsed.query)

            if path in VALUE_ASSETS:
                with open(VALUE_ASSETS[path], "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif path in ("/landing", "/landing.html"):
                with open(LANDING_HTML_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif path in ("/", "/index.html"):
                # no-store: app.html changes across sessions as this tool is
                # developed, and a stale cached copy silently running old JS
                # against the current API is a confusing failure mode (looks
                # like a broken tab/click, not a cache issue) — always serve fresh.
                with open(APP_HTML_PATH, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/runs":
                self._send_json(200, list_runs(project_dir))
            elif path == "/api/jobs":
                run_id = (qs.get("run_id") or [""])[0]
                status = (qs.get("status") or ["passed"])[0]
                page = int((qs.get("page") or ["1"])[0])
                if not run_id:
                    self._send_json(400, {"error": "run_id required"})
                    return
                self._send_json(200, _fetch_jobs(db_path, run_id, status, page))
            elif path == "/api/onboarding":
                config = read_config(project_dir)
                self._send_json(200, {
                    "state": read_state(project_dir),
                    "has_onboarding_state": os.path.isfile(os.path.join(project_dir, ONBOARDING_STATE)),
                    "has_criteria": os.path.isfile(os.path.join(project_dir, "criteria.json")),
                    "has_rubric": os.path.isfile(os.path.join(project_dir, "rubric.md")),
                    "has_profile": bool(config.get("profile_sources")) or bool(config.get("profile_md")),
                    "config": config,
                })
            elif path == "/api/pipeline":
                self._send_json(200, {
                    **read_config(project_dir),
                    "keyword_stats": keyword_stats(db_path),
                })
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON body"})
                return

            if self.path == "/api/pick-profile-source":
                kind = str(data.get("kind") or "file")
                self._pick_profile_source(kind)
            elif self.path == "/api/decide":
                job_url = data.get("job_url", "")
                user_action = data.get("user_action")
                user_reason = data.get("user_reason")
                if not job_url or (user_action is None and user_reason is None):
                    self._send_json(400, {"error": "job_url required, plus at least one of user_action/user_reason"})
                    return
                if user_action is not None and user_action not in VALID_USER_ACTIONS:
                    self._send_json(400, {"error": f"user_action must be one of {VALID_USER_ACTIONS}"})
                    return
                with connect(db_path) as conn:
                    update_decision(conn, job_url, user_action=user_action, user_reason=user_reason)
                self._send_json(200, {"ok": True})
            elif self.path == "/api/onboarding-action":
                try:
                    state = apply_action(project_dir, data)
                    write_action(project_dir, data)
                except (OnboardingError, TypeError, ValueError, OSError) as e:
                    self._send_json(409, {"error": str(e), "state": read_state(project_dir)})
                    return
                self._send_json(200, {"ok": True, "state": state})
            elif self.path == "/api/onboarding":
                criteria = data.get("criteria")
                rubric_md = data.get("rubric_md")
                profile_md = data.get("profile_md")
                profile_sources = data.get("profile_sources")
                state = data.get("state")
                if state is not None:
                    self._send_json(409, {"error": "onboarding state changes must use /api/onboarding-action"})
                    return
                if criteria is None and rubric_md is None and profile_md is None and profile_sources is None:
                    self._send_json(400, {"error": "at least one onboarding field required"})
                    return
                try:
                    write_config(project_dir, criteria=criteria, rubric_md=rubric_md,
                                 profile_md=profile_md, profile_sources=profile_sources)
                except (TypeError, ValueError, OSError) as e:
                    self._send_json(400, {"error": f"invalid onboarding data: {e}"})
                    return
                self._send_json(200, {"ok": True, "state": _onboarding_state(project_dir)})
            elif self.path == "/api/config":
                # Pipeline tab's Save buttons — writes whichever of
                # criteria/rubric_md/profile_md was included, same as
                # hand-editing those files (no auto git-commit).
                criteria = data.get("criteria")
                rubric_md = data.get("rubric_md")
                profile_md = data.get("profile_md")
                profile_sources = data.get("profile_sources")
                if criteria is None and rubric_md is None and profile_md is None and profile_sources is None:
                    self._send_json(400, {"error": "at least one config field required"})
                    return
                try:
                    write_config(project_dir, criteria=criteria, rubric_md=rubric_md, profile_md=profile_md, profile_sources=profile_sources)
                except (TypeError, ValueError) as e:
                    self._send_json(400, {"error": f"invalid config: {e}"})
                    return
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"error": "not found"})

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default="seen.db")
    ap.add_argument("--project-dir", default=".")
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.db, args.project_dir))
    url = f"http://127.0.0.1:{args.port}"
    print(f"FTJA review server running at {url} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
