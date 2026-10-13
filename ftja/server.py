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
import csv
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ftja.state import connect, record_criterion_feedback, update_decision, VALID_FEEDBACK_VOTES, VALID_USER_ACTIONS
from ftja.pipeline_view import read_config, write_config, keyword_stats
from ftja import live, osutil, version
from ftja import remote as remote_check
from ftja import keywords as keyword_languages
from ftja.verdict import DEFAULT_CRITERIA_PATH, criteria_status, load_criteria, save_criteria
from ftja.runs_view import list_runs
from ftja import runs_store

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
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
def _rubric_criteria(project_dir: str) -> dict:
    """The rubric as a list of criteria, for the Pipeline tab's editor.
    `rubric_criteria` is null until /ftja-run has split rubric.md once
    (status 'missing'); 'stale' means rubric.md was edited as text since."""
    path = os.path.join(project_dir, DEFAULT_CRITERIA_PATH)
    status = criteria_status(path, os.path.join(project_dir, "rubric.md"))
    data = None
    if status in ("ok", "stale"):
        data = load_criteria(path)
    return {"rubric_criteria": data, "rubric_criteria_status": status}


def _fetch_jobs(db_path: str, run_id: str, status: str, page: int) -> dict:
    """Jobs from ONE run, filtered by status ('all': passed, then review,
    then failed), 10 per page. Only Stage2-
    reached jobs are ever returned (stage_reached=2) — Stage0/1 fails have
    no reasoning worth reviewing here. `criteria` is null for a job judged
    before Stage 2 returned per-criterion results; the viewer falls back to
    `reasoning`/`evidence_sentences` for those.

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
                      user_action, user_reason, last_seen_at,
                      criteria, criteria_feedback, company_line, employer, notes,
                      t1_blocks, stage1_reason, pruned_at, remote
               FROM seen_jobs
               WHERE stage_reached = 2 AND run_id = ? AND (status = ? OR ? = 'all')
               ORDER BY CASE status WHEN 'passed' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, last_seen_at DESC""",
            (run_id, status, status),
        ).fetchall()
    cols = ["job_url", "title", "company", "location", "status", "verdict",
            "reasoning", "evidence_sentences", "t1_matched_keywords",
            "user_action", "user_reason", "last_seen_at",
            "criteria", "criteria_feedback", "company_line", "employer", "notes",
            "t1_blocks", "stage1_reason", "pruned_at", "remote"]
    seen_groups: dict[tuple, dict] = {}
    for row in rows:
        job = dict(zip(cols, row))
        for key in ("evidence_sentences", "t1_matched_keywords"):
            try:
                job[key] = json.loads(job[key]) if job[key] else []
            except (TypeError, json.JSONDecodeError):
                job[key] = []
        for key, empty in (("criteria", None), ("criteria_feedback", {}), ("employer", []), ("notes", []),
                           ("t1_blocks", []), ("remote", None)):
            try:
                job[key] = json.loads(job[key]) if job[key] else empty
            except (TypeError, json.JSONDecodeError):
                job[key] = empty
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


def _remote_setup(project_dir: str) -> dict:
    """The Remote check's setup for the Pipeline tab: the saved one, or the
    built-in one until the user has their own; plus where the Generate
    button's request stands."""
    saved = remote_check.load_config(os.path.join(project_dir, remote_check.CONFIG_FILE))
    return {"config": saved or remote_check.default_config(), "saved": saved is not None,
            "draft": remote_check.load_draft(project_dir),  # the agent's, until the user confirms it
            "request": remote_check.read_request(project_dir)}


def _keyword_languages(project_dir: str) -> dict:
    """For the Pipeline tab: which keyword lists lack a language the user
    reads, and where a request to have the agent write them stands."""
    found = keyword_languages.gaps(project_dir)
    return {"read": found["languages_the_user_reads"],
            "missing": {"stage1": found["stage1"]["missing"], "remote": found["remote"]["missing"]},
            "request": keyword_languages.read_request(project_dir)}


def _remote_table(rows: list[dict]) -> str:
    """The Remote check's readings as a CSV table: one row per posting, one
    pair of columns per category (the answer, and the specifics), then the
    sentences the answers rest on."""
    labels = list(dict.fromkeys(a["label"] for row in rows for a in row.get("answers") or []))
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Title", "Company", "Location", "URL", "Why", *[x for label in labels for x in (label, f"{label}: details")],
                     "Evidence sentences"])
    for row in rows:
        by_label = {a["label"]: a for a in row.get("answers") or []}
        sentences = {b.get("sid"): b.get("sentence") for b in row.get("blocks") or []}
        cited = list(dict.fromkeys(sentences[sid] for sid in [*(row.get("evidence_sids") or []),
                                                              *(sid for a in row.get("answers") or [] for sid in a.get("evidence_sids") or [])]
                                   if sentences.get(sid)))
        writer.writerow([row.get("title") or "", row.get("company") or "", row.get("location") or "", row.get("job_url") or "",
                         row.get("reason") or "", *[x for label in labels for x in ((by_label.get(label) or {}).get("answer_label", ""),
                                                           (by_label.get(label) or {}).get("detail", ""))],
                         " | ".join(cited)])
    return out.getvalue()


def make_handler(db_path: str, project_dir: str):
    class Handler(BaseHTTPRequestHandler):
        # Keep connections open between requests. The page asks something
        # every second or so; a new connection each time left thousands of
        # closing ones behind on Windows, and new ones then failed to open.
        protocol_version = "HTTP/1.1"
        timeout = 60  # an idle connection is dropped; the browser opens another

        def log_message(self, fmt, *args):
            pass  # quiet; this is a local single-user tool

        def _send_json(self, status: int, payload):
            body = b"" if status == 204 else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if status >= 400:
                self.close_connection = True  # the request's body may not have been read
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            if status >= 400:
                self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _local_request(self, write: bool = False) -> bool:
            """Refuse requests that did not come from this viewer's own page.
            The server is bound to 127.0.0.1, but a web page the user visits
            can still reach it from their browser: by a cross-site POST, or by
            DNS rebinding, where another site's name resolves to 127.0.0.1.
            Both show up as a Host or Origin that is not this machine."""
            host = urlparse("//" + (self.headers.get("Host") or "")).hostname
            if host not in LOCAL_HOSTS:
                self._send_json(403, {"error": "this viewer only answers on 127.0.0.1"})
                return False
            if write:
                origin = self.headers.get("Origin")
                if origin is not None and urlparse(origin).netloc != self.headers.get("Host"):
                    self._send_json(403, {"error": "cross-site requests are not accepted"})
                    return False
                content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if content_type != "application/json":
                    self._send_json(415, {"error": "Content-Type must be application/json"})
                    return False
            return True

        def do_GET(self):
            if not self._local_request():
                return
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
                records = list_runs(project_dir)
                for record in records:  # stopped before Stage 2, and its working files are still there to go on from
                    record["can_continue"] = bool(record.get("stage2_pending")) and live.awaiting_stage2(
                        live.run_dir_for(project_dir, record.get("run_id") or ""))
                    last = record.get("stopped_after")
                    record["continue_to"] = list(live.STAGES[live.STAGES.index(last) + 1:]) \
                        if record["can_continue"] and last in live.STAGES else []
                self._send_json(200, records)
            elif path == "/api/version":
                # project_dir: which FTJA folder this viewer is showing. /ftja-run
                # compares it with its own folder, since two clones share the port.
                self._send_json(200, {**version.check(project_dir), "project_dir": os.path.realpath(project_dir)})
            elif path == "/api/storage":
                self._send_json(200, runs_store.storage(project_dir, db_path))
            elif path == "/api/delete-run-preview":
                try:
                    self._send_json(200, runs_store.delete_preview(project_dir, db_path, (qs.get("run_id") or [""])[0]))
                except runs_store.RunStoreError as error:
                    self._send_json(400, {"error": str(error)})
            elif path == "/api/live":
                # the run in progress, read from its working directory; polled by the Results tab
                self._send_json(200, {"run": live.state(project_dir)})
            elif path == "/api/stage1-fails":
                run_id = (qs.get("run_id") or [""])[0]
                page = int((qs.get("page") or ["1"])[0])
                if not run_id:
                    self._send_json(400, {"error": "run_id required"})
                    return
                verdict = "pass" if (qs.get("verdict") or ["fail"])[0] == "pass" else "fail"
                self._send_json(200, live.stage1_fails(project_dir, db_path, run_id, page, verdict))
            elif path in ("/api/remote-jobs", "/api/remote-jobs.csv"):
                run_id = (qs.get("run_id") or [""])[0]
                if not run_id:
                    self._send_json(400, {"error": "run_id required"})
                    return
                verdict = "pass" if (qs.get("verdict") or ["fail"])[0] == "pass" else "fail"
                if path.endswith(".csv"):
                    rows = live.remote_jobs(project_dir, db_path, run_id, 1, verdict, page_size=None)["jobs"]
                    body = ("\ufeff" + _remote_table(rows)).encode("utf-8")  # the mark makes Excel read it as UTF-8
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition",
                                     f'attachment; filename="remote-{verdict}-{runs_store.run_stamp(run_id)}.csv"')
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                page = int((qs.get("page") or ["1"])[0])
                self._send_json(200, live.remote_jobs(project_dir, db_path, run_id, page, verdict))
            elif path == "/api/remote-check":
                self._send_json(200, _remote_setup(project_dir))
            elif path == "/api/keyword-languages":
                self._send_json(200, _keyword_languages(project_dir))
            elif path == "/api/jobs":
                run_id = (qs.get("run_id") or [""])[0]
                status = (qs.get("status") or ["passed"])[0]
                page = int((qs.get("page") or ["1"])[0])
                if not run_id:
                    self._send_json(400, {"error": "run_id required"})
                    return
                self._send_json(200, _fetch_jobs(db_path, run_id, status, page))
            elif path == "/api/setup":
                # Has setup written the files a run needs? Until it has, the
                # page says to finish setup in the chat.
                self._send_json(200, {
                    "has_criteria": os.path.isfile(os.path.join(project_dir, "criteria.json")),
                    "has_rubric": os.path.isfile(os.path.join(project_dir, "rubric.md")),
                })
            elif path == "/api/pipeline":
                self._send_json(200, {
                    **read_config(project_dir),
                    **_rubric_criteria(project_dir),
                    "keyword_stats": keyword_stats(db_path),
                })
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            if not self._local_request(write=True):
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON body"})
                return

            if self.path == "/api/remote-check":
                # Pipeline tab's Remote check editor: the whole setup at once.
                try:
                    remote_check.save_config(project_dir, data.get("config"))
                except (TypeError, ValueError, OSError) as e:
                    self._send_json(400, {"error": str(e)})
                    return
                remote_check.discard_draft(project_dir)  # confirmed: the draft has become the setup
                self._send_json(200, {"ok": True, **_remote_setup(project_dir)})
            elif self.path == "/api/keywords-request":
                # "Add French keywords": kept in the project folder for
                # /ftja-keywords, which the user types in the chat.
                try:
                    keyword_languages.write_request(project_dir, data.get("items") or [])
                except (TypeError, ValueError, KeyError, OSError) as e:
                    self._send_json(409, {"error": str(e)})
                    return
                self._send_json(200, {"ok": True, **_keyword_languages(project_dir)})
            elif self.path == "/api/keywords-confirm":
                # the user went through the agent's keywords: add them
                added = keyword_languages.add_keywords(project_dir, data.get("items") or [])
                self._send_json(200, {"ok": True, "added": added, **_keyword_languages(project_dir)})
            elif self.path == "/api/keywords-cancel":
                if keyword_languages.read_request(project_dir)["status"] != "generating":
                    keyword_languages.discard(project_dir)
                self._send_json(200, {"ok": True, **_keyword_languages(project_dir)})
            elif self.path == "/api/remote-generate-cancel":
                if remote_check.read_request(project_dir)["status"] != "generating":  # not one the agent is writing right now
                    remote_check.discard_draft(project_dir)
                self._send_json(200, {"ok": True, **_remote_setup(project_dir)})
            elif self.path == "/api/remote-generate":
                # The Generate button: keep the four answers in the project
                # folder for /ftja-remote, which the user types in the chat.
                try:
                    if keyword_languages.pending(project_dir):
                        raise ValueError("your agent is writing keywords for a new language; finish that first")
                    remote_check.write_request(project_dir, data.get("answers") or {})
                except (TypeError, ValueError, OSError) as e:
                    self._send_json(400, {"error": str(e)})
                    return
                self._send_json(200, {"ok": True, **_remote_setup(project_dir)})
            elif self.path == "/api/open-folder":
                # Opens this viewer's own folder in Finder or Explorer. The path
                # is never taken from the request, so nothing else can be opened.
                try:
                    error = osutil.open_folder(os.path.realpath(project_dir))
                except OSError as e:
                    error = str(e)
                if error:
                    self._send_json(500, {"error": error})
                    return
                self._send_json(200, {"ok": True})
            elif self.path in ("/api/delete-run", "/api/restore-run", "/api/empty-trash"):
                try:
                    if self.path == "/api/delete-run":
                        result = runs_store.delete_run(project_dir, db_path, str(data.get("run_id") or ""))
                    elif self.path == "/api/restore-run":
                        result = runs_store.restore_run(project_dir, db_path, str(data.get("name") or ""))
                    else:
                        result = runs_store.empty_trash(project_dir, str(data.get("name") or ""))
                    self._send_json(200, {"ok": True, **result})
                except runs_store.RunStoreError as error:
                    self._send_json(400, {"error": str(error)})
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
            elif self.path == "/api/criterion-feedback":
                job_url = data.get("job_url", "")
                criterion_id = data.get("criterion_id", "")
                vote = data.get("vote")
                if not job_url or not criterion_id:
                    self._send_json(400, {"error": "job_url and criterion_id required"})
                    return
                if vote is not None and vote not in VALID_FEEDBACK_VOTES:
                    self._send_json(400, {"error": f"vote must be one of {VALID_FEEDBACK_VOTES} or null"})
                    return
                try:
                    with connect(db_path) as conn:
                        feedback = record_criterion_feedback(conn, job_url, criterion_id, vote,
                                                             str(data.get("comment") or ""))
                except KeyError:
                    self._send_json(404, {"error": "job not found"})
                    return
                self._send_json(200, {"ok": True, "criteria_feedback": feedback})
            elif self.path == "/api/rubric-criteria":
                # Pipeline tab's rubric editor: one criterion at a time. Saving
                # rewrites rubric.md from the list, so both stay the same rubric.
                if not isinstance(data.get("criteria"), list):
                    self._send_json(400, {"error": "criteria list required"})
                    return
                try:
                    save_criteria(project_dir, data["criteria"], data.get("notes"))
                except (TypeError, ValueError, OSError) as e:
                    self._send_json(400, {"error": str(e)})
                    return
                self._send_json(200, {"ok": True, **_rubric_criteria(project_dir),
                                      "rubric_md": read_config(project_dir)["rubric_md"]})
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


class ViewerServer(ThreadingHTTPServer):
    """One viewer per port. http.server asks for SO_REUSEADDR, and on Windows
    that lets a second viewer listen on a port that is already taken: both
    then answer, each request going to either one."""
    allow_reuse_address = not osutil.WINDOWS
    request_queue_size = 128  # the default of 5 turns a burst of requests away

    def server_bind(self):
        if osutil.WINDOWS:
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--db", default="seen.db")
    ap.add_argument("--project-dir", default=".")
    args = ap.parse_args()

    server = ViewerServer(("127.0.0.1", args.port), make_handler(args.db, args.project_dir))
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
