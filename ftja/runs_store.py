"""Deleting a run, and what the project folder holds.

A run leaves four things behind: its rows in seen.db (one per job URL it
judged), its line in runs.jsonl, its working directory in .ftja-run/, and
its digest and rejected files. `delete_run` moves all four into
`.ftja-trash/<run>/`, so the run can be put back; nothing is destroyed
until the trash is emptied.

Deleting the rows is the point for a test run: "already judged" is decided
by a job URL having a row in seen.db, so with its rows gone the next run
judges those postings again. It also takes the user's own marks on those
jobs (applied / skipped, reasons, per-criterion feedback) with it, which is
why `delete_preview` counts them, and counts the ones /ftja-review has not
yet turned into a rubric or criteria change.

Usage:
    python -m ftja.runs_store storage
    python -m ftja.runs_store delete-preview --run-id <id>
    python -m ftja.runs_store delete --run-id <id>
    python -m ftja.runs_store trash
    python -m ftja.runs_store restore --name <trash folder>
    python -m ftja.runs_store empty-trash [--name <trash folder>]
"""
import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone

from ftja.lock import is_locked
from ftja.state import PRUNE_AFTER_DAYS, connect, pending_criterion_feedback

RUNS_DIR = ".ftja-run"
TRASH_DIR = ".ftja-trash"
RUNS_FILE = "runs.jsonl"


class RunStoreError(Exception):
    pass


def run_stamp(run_id: str) -> str:
    """A run id as a file name: the name of its directory in .ftja-run/ and
    the suffix of its digest and rejected files."""
    return re.sub(r"[^0-9A-Za-z._-]", "-", run_id or "")


def _size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _read_runs(project_dir: str) -> list[dict]:
    path = os.path.join(project_dir, RUNS_FILE)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _write_runs(project_dir: str, records: list[dict]):
    path = os.path.join(project_dir, RUNS_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _result_files(project_dir: str, run_id: str) -> list[str]:
    stamp = run_stamp(run_id)
    return [name for name in (f"digest-{stamp}.md", f"rejected-{stamp}.md")
            if os.path.isfile(os.path.join(project_dir, name))]


def _is_running(project_dir: str, run_id: str) -> bool:
    if not is_locked(os.path.join(project_dir, ".ftja.lock")):
        return False
    run_dir = os.path.join(project_dir, RUNS_DIR, run_stamp(run_id))
    return os.path.isdir(run_dir) and not os.path.exists(os.path.join(run_dir, "done.json"))


def storage(project_dir: str = ".", db_path: str = "seen.db") -> dict:
    """What FTJA's own data takes on disk, for the viewer's header."""
    parts = {
        "database": _size(db_path) if os.path.exists(db_path) else 0,
        "run_files": _size(os.path.join(project_dir, RUNS_DIR)),
        "trash": _size(os.path.join(project_dir, TRASH_DIR)),
    }
    return {"project_dir": os.path.realpath(project_dir), "total_bytes": sum(parts.values()), **parts,
            "prune_after_days": PRUNE_AFTER_DAYS, "trash_runs": list_trash(project_dir)}


def delete_preview(project_dir: str, db_path: str, run_id: str) -> dict:
    """What deleting this run would move to the trash, without doing it."""
    if not run_id:
        raise RunStoreError("this run has no id, so its jobs cannot be told apart from other runs'")
    record = next((r for r in _read_runs(project_dir) if r.get("run_id") == run_id), None)
    with connect(db_path) as conn:
        jobs, decisions, reasons, feedback = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(user_action IS NOT NULL), 0),
                      COALESCE(SUM(COALESCE(user_reason, '') != ''), 0),
                      COALESCE(SUM(criteria_feedback IS NOT NULL), 0)
               FROM seen_jobs WHERE run_id = ?""", (run_id,)).fetchone()
        by_status = dict(conn.execute("SELECT status, COUNT(*) FROM seen_jobs WHERE run_id = ? GROUP BY status", (run_id,)))
        applied = conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE run_id = ? AND user_action = 'applied'", (run_id,)).fetchone()[0]
        urls = {row[0] for row in conn.execute("SELECT job_url FROM seen_jobs WHERE run_id = ?", (run_id,))}
        unreviewed = conn.execute(
            """SELECT COUNT(*) FROM seen_jobs WHERE run_id = ? AND user_action = 'skipped_fit'
               AND COALESCE(user_reason, '') != '' AND decision_review_status IS NULL""", (run_id,)).fetchone()[0]
        unreviewed += sum(1 for item in pending_criterion_feedback(conn) if item.get("job_url") in urls)
    if record is None and not jobs:
        raise RunStoreError("no such run")
    run_dir = os.path.join(project_dir, RUNS_DIR, run_stamp(run_id))
    return {
        "run_id": run_id, "ts_end": (record or {}).get("ts_end"), "running": _is_running(project_dir, run_id),
        "jobs": jobs, "by_status": by_status,
        # the user's own marks on this run's jobs: they go to the trash with the rows
        "decisions": decisions, "applied": applied, "reasons": reasons, "criterion_feedback": feedback,
        # ... of which /ftja-review has not yet proposed a rubric or criteria change for
        "not_yet_reviewed": unreviewed,
        "files": _result_files(project_dir, run_id),
        "bytes": _size(run_dir) + sum(_size(os.path.join(project_dir, n)) for n in _result_files(project_dir, run_id)),
    }


def delete_run(project_dir: str, db_path: str, run_id: str) -> dict:
    """Move a run to the trash: its seen.db rows (so its jobs are judged
    again next time), its runs.jsonl line, its working directory and its
    digest and rejected files."""
    preview = delete_preview(project_dir, db_path, run_id)
    if preview["running"]:
        raise RunStoreError("this run is still in progress; wait for it to finish")
    stamp = run_stamp(run_id)
    trash = os.path.join(project_dir, TRASH_DIR, stamp)
    while os.path.exists(trash):  # the same run deleted, restored and deleted again
        trash += "-again"
    os.makedirs(trash)

    with connect(db_path) as conn:
        cursor = conn.execute("SELECT * FROM seen_jobs WHERE run_id = ?", (run_id,))
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    records = _read_runs(project_dir)
    kept = [r for r in records if r.get("run_id") != run_id]
    record = next((r for r in records if r.get("run_id") == run_id), None)
    # Written before anything is removed: a crash from here on loses nothing.
    with open(os.path.join(trash, "trash.json"), "w") as f:
        json.dump({"run_id": run_id, "deleted_at": datetime.now(timezone.utc).isoformat(),
                   "record": record, "rows": rows, "files": preview["files"],
                   "summary": {k: preview[k] for k in ("jobs", "by_status", "decisions", "ts_end")}},
                  f, ensure_ascii=False)

    run_dir = os.path.join(project_dir, RUNS_DIR, stamp)
    if os.path.isdir(run_dir):
        shutil.move(run_dir, os.path.join(trash, "run"))
    for name in preview["files"]:
        shutil.move(os.path.join(project_dir, name), os.path.join(trash, name))
    with connect(db_path) as conn:
        conn.execute("DELETE FROM seen_jobs WHERE run_id = ?", (run_id,))
        conn.commit()
        conn.execute("VACUUM")  # hand the space back to the file system
    _write_runs(project_dir, kept)
    _log(project_dir, f"run {run_id} moved to the trash ({len(rows)} jobs): {os.path.basename(trash)}")
    return {"deleted": run_id, "jobs": len(rows), "trash": os.path.basename(trash)}


def _trash_entry(project_dir: str, name: str) -> dict | None:
    path = os.path.join(project_dir, TRASH_DIR, name)
    try:
        with open(os.path.join(path, "trash.json")) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    summary = data.get("summary") or {}
    return {"name": name, "run_id": data.get("run_id"), "deleted_at": data.get("deleted_at"),
            "ts_end": summary.get("ts_end"), "jobs": summary.get("jobs", len(data.get("rows") or [])),
            "decisions": summary.get("decisions", 0), "bytes": _size(path)}


def list_trash(project_dir: str = ".") -> list[dict]:
    """Deleted runs, most recently deleted first."""
    root = os.path.join(project_dir, TRASH_DIR)
    if not os.path.isdir(root):
        return []
    entries = [e for e in (_trash_entry(project_dir, name) for name in os.listdir(root)) if e]
    return sorted(entries, key=lambda e: e.get("deleted_at") or "", reverse=True)


def _trash_path(project_dir: str, name: str) -> str:
    if not name or name != os.path.basename(name) or name.startswith("."):
        raise RunStoreError("not a trash entry")
    path = os.path.join(project_dir, TRASH_DIR, name)
    if not os.path.isfile(os.path.join(path, "trash.json")):
        raise RunStoreError("not a trash entry")
    return path


def restore_run(project_dir: str, db_path: str, name: str) -> dict:
    """Put a deleted run back. A job that a later run has judged since stays
    with that run: its old row is not restored over the newer judgment."""
    path = _trash_path(project_dir, name)
    with open(os.path.join(path, "trash.json")) as f:
        data = json.load(f)
    run_id, rows = data.get("run_id"), data.get("rows") or []
    restored = skipped = 0
    with connect(db_path) as conn:
        known = {row[1] for row in conn.execute("PRAGMA table_info(seen_jobs)")}
        for row in rows:
            row = {k: v for k, v in row.items() if k in known}
            if conn.execute("SELECT 1 FROM seen_jobs WHERE job_url_hash = ?", (row["job_url_hash"],)).fetchone():
                skipped += 1
                continue
            conn.execute(f"INSERT INTO seen_jobs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            restored += 1
        conn.commit()
    if data.get("record"):
        records = [r for r in _read_runs(project_dir) if r.get("run_id") != run_id] + [data["record"]]
        records.sort(key=lambda r: r.get("ts_end") or "")  # runs.jsonl is oldest first
        _write_runs(project_dir, records)
    run_dir = os.path.join(project_dir, RUNS_DIR, run_stamp(run_id))
    if os.path.isdir(os.path.join(path, "run")) and not os.path.exists(run_dir):
        os.makedirs(os.path.dirname(run_dir), exist_ok=True)
        shutil.move(os.path.join(path, "run"), run_dir)
    for file_name in data.get("files") or []:
        source = os.path.join(path, file_name)
        if os.path.isfile(source) and not os.path.exists(os.path.join(project_dir, file_name)):
            shutil.move(source, os.path.join(project_dir, file_name))
    shutil.rmtree(path)
    _log(project_dir, f"run {run_id} restored from the trash ({restored} jobs, {skipped} judged again since)")
    return {"restored": run_id, "jobs": restored, "judged_again_since": skipped}


def empty_trash(project_dir: str = ".", name: str = "") -> dict:
    """Remove deleted runs for good: one, or all of them. Cannot be undone."""
    paths = [_trash_path(project_dir, name)] if name else [
        os.path.join(project_dir, TRASH_DIR, e["name"]) for e in list_trash(project_dir)]
    freed = sum(_size(p) for p in paths)
    for path in paths:
        shutil.rmtree(path)
    if paths:
        _log(project_dir, f"trash emptied: {len(paths)} deleted run(s) removed for good")
    return {"removed": len(paths), "bytes_freed": freed}


def _log(project_dir: str, message: str):
    with open(os.path.join(project_dir, "run.log"), "a") as f:
        f.write(f"[{datetime.now(timezone.utc).isoformat()}] {message}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", default=".")
    ap.add_argument("--db", default="seen.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("storage", help="what FTJA's data takes on disk")
    for name, help_ in (("delete-preview", "what deleting a run would move to the trash; changes nothing"),
                        ("delete", "move a run to the trash; its jobs are judged again next time")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--run-id", required=True)
    sub.add_parser("trash", help="list deleted runs")
    r = sub.add_parser("restore", help="put a deleted run back")
    r.add_argument("--name", required=True, help="the trash folder, from `trash`")
    e = sub.add_parser("empty-trash", help="remove deleted runs for good; cannot be undone")
    e.add_argument("--name", default="", help="one trash folder; default is all of them")
    args = ap.parse_args()
    try:
        if args.cmd == "storage":
            out = storage(args.project_dir, args.db)
        elif args.cmd == "delete-preview":
            out = delete_preview(args.project_dir, args.db, args.run_id)
        elif args.cmd == "delete":
            out = delete_run(args.project_dir, args.db, args.run_id)
        elif args.cmd == "trash":
            out = list_trash(args.project_dir)
        elif args.cmd == "restore":
            out = restore_run(args.project_dir, args.db, args.name)
        else:
            out = empty_trash(args.project_dir, args.name)
    except RunStoreError as error:
        raise SystemExit(str(error))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
