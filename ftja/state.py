"""Local dedup state — seen.db (SQLite, stdlib only, no server).

Schema: seen_jobs(job_url_hash PK, job_url, title, company, location, status,
                   stage_reached, verdict, t1_matched_keywords,
                   t1_matched_sentences, reasoning, evidence_sentences,
                   rubric_version, user_action, user_reason, user_decided_at,
                   last_seen_at)
  status: 'stage0_fail' | 'stage1_fail' | 'stage2_fail' | 'passed'
  stage_reached: 0, 1, or 2
  t1_matched_keywords / t1_matched_sentences: JSON-encoded lists — which
  tier1 keyword(s) hit and the actual [T1] sentence(s) that matched, for a
  job that reached Stage 0 output. This is the audit trail: rather than a
  separate "preview" file, seen.db already IS the one-record-per-job ledger
  (user's own observation) — spot-check keyword-matching quality with e.g.
  `sqlite3 seen.db "SELECT title, t1_matched_keywords, t1_matched_sentences
  FROM seen_jobs WHERE status='passed'"` any time, no extra file needed.
  reasoning / evidence_sentences: Stage1/2's own free-text verdict reasoning,
  persisted here (not just in digest/rejected .md) so a later user decision
  can be joined back to *why the pipeline judged it that way* — without
  this, "why didn't this pass" only existed for the run's lifetime.
  rubric_version: md5 hash (first 12 hex chars) of rubric.md's content at
  the moment this job was judged — see `rubric_version()` below. Scopes a
  decision to the rubric era it was made under, so rubric.md edits later
  don't silently make old decisions look like they were judged against
  criteria they never saw (see JobSpyProject's gold_labels_inconsistent
  lesson: unstamped labels became untrustworthy once criteria drifted).
  user_action / user_reason / user_decided_at: the candidate's own outcome
  for a job that reached Stage 2 ('applied' / 'skipped_fit' /
  'skipped_circumstance' / 'expired') and why, captured via the review
  server (ftja.server) and recorded by /ftja-review. This is the ground
  truth /ftja-review uses to propose concrete criteria.json/rubric.md
  changes — NOT auto-applied, always proposed then confirmed.
  run_id: which /ftja-run invocation last touched this job — the same
  value finalize.py writes into that run's runs.jsonl record, so the two
  can be joined. Like status/reasoning (and unlike user_action), this gets
  overwritten on every mark_seen call: if a job resurfaces in a later run,
  it's "current" for that run now. Rows written before this column existed
  have run_id NULL — see scripts/backfill_run_id.py for the one-off,
  best-effort backfill from last_seen_at date buckets (approximate; a
  handful of jobs may be attributed to the wrong day if they were
  re-touched by a later run before this column existed).
  decision_review_status: NULL (not yet looked at) / 'applied' (this
  decision's reason led to a criteria.json/rubric.md change) / 'declined'
  (user was asked, said no — permanently excluded from future proposals).
  /ftja-run's preflight checks for `skipped_fit` decisions with a reason
  and NULL status here; that's what makes review "just happen" as part of
  the normal run cadence instead of needing a live watcher. Reset to NULL
  by record_decision whenever the reason changes, so an edited reason gets
  reconsidered.

Only title/company/location/match-evidence/reasoning are kept for future
dedup/analysis reference — the full JD text is never persisted here (it
lives only in /tmp for the duration of one run, then is discarded).
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    job_url_hash TEXT PRIMARY KEY,
    job_url TEXT NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    status TEXT NOT NULL,
    stage_reached INTEGER NOT NULL,
    verdict TEXT,
    t1_matched_keywords TEXT,
    t1_matched_sentences TEXT,
    reasoning TEXT,
    evidence_sentences TEXT,
    rubric_version TEXT,
    user_action TEXT,
    user_reason TEXT,
    user_decided_at TEXT,
    run_id TEXT,
    decision_review_status TEXT,
    last_seen_at TEXT NOT NULL
);
"""


def hash_url(job_url: str) -> str:
    return hashlib.sha256(job_url.strip().lower().encode("utf-8")).hexdigest()[:16]


def _migrate(conn: sqlite3.Connection):
    """Idempotent: add columns introduced after the first M1 run, if missing."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(seen_jobs)")}
    for col in ("title", "company", "location", "t1_matched_keywords", "t1_matched_sentences",
                "reasoning", "evidence_sentences", "rubric_version",
                "user_action", "user_reason", "user_decided_at", "run_id",
                "decision_review_status"):
        if col not in cols:
            conn.execute(f"ALTER TABLE seen_jobs ADD COLUMN {col} TEXT")
    conn.commit()


def rubric_version(rubric_path: str = "rubric.md") -> str:
    """md5 of rubric.md's current content, first 12 hex chars — stamped onto
    every job judged under it so a later decision can be scoped to the
    rubric era it was made under. Empty string if the file doesn't exist
    (e.g. called before /ftja-setup has run)."""
    try:
        with open(rubric_path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except FileNotFoundError:
        return ""


@contextmanager
def connect(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(DB_SCHEMA)
        conn.commit()
        _migrate(conn)
        yield conn
    finally:
        conn.close()


def is_seen(conn: sqlite3.Connection, job_url: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_jobs WHERE job_url_hash = ?", (hash_url(job_url),)
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, job_url: str, status: str, stage_reached: int,
              verdict: str = "", title: str = "", company: str = "", location: str = "",
              t1_matched_keywords: list[str] | None = None,
              t1_matched_sentences: list[str] | None = None,
              reasoning: str = "", evidence_sentences: list[str] | None = None,
              rubric_version: str = "", run_id: str = ""):
    """Note: the ON CONFLICT clause intentionally does NOT touch
    user_action/user_reason/user_decided_at — a re-scrape or an alias URL
    from content dedup must never overwrite a decision the user already
    recorded via /ftja-review."""
    conn.execute(
        """INSERT INTO seen_jobs (job_url_hash, job_url, title, company, location, status,
                                  stage_reached, verdict, t1_matched_keywords, t1_matched_sentences,
                                  reasoning, evidence_sentences, rubric_version, run_id, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(job_url_hash) DO UPDATE SET
             title=excluded.title, company=excluded.company, location=excluded.location,
             status=excluded.status, stage_reached=excluded.stage_reached,
             verdict=excluded.verdict, t1_matched_keywords=excluded.t1_matched_keywords,
             t1_matched_sentences=excluded.t1_matched_sentences,
             reasoning=excluded.reasoning, evidence_sentences=excluded.evidence_sentences,
             rubric_version=excluded.rubric_version, run_id=excluded.run_id,
             last_seen_at=excluded.last_seen_at""",
        (hash_url(job_url), job_url, title, company, location, status, stage_reached, verdict,
         json.dumps(t1_matched_keywords or [], ensure_ascii=False),
         json.dumps(t1_matched_sentences or [], ensure_ascii=False),
         reasoning, json.dumps(evidence_sentences or [], ensure_ascii=False), rubric_version, run_id,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


VALID_USER_ACTIONS = ("applied", "skipped_fit", "skipped_circumstance", "expired")


def record_decision(conn: sqlite3.Connection, job_url: str, user_action: str, user_reason: str = ""):
    """Records the candidate's own outcome for a job, keyed off the same
    job_url_hash mark_seen uses. Does nothing if the job was never seen
    (record it via mark_seen / a normal run first). Resets
    decision_review_status to NULL — any recorded/edited decision is
    "pending" again until /ftja-run's preflight (or a manual /ftja-review)
    looks at it."""
    if user_action not in VALID_USER_ACTIONS:
        raise ValueError(f"user_action must be one of {VALID_USER_ACTIONS}, got {user_action!r}")
    conn.execute(
        "UPDATE seen_jobs SET user_action=?, user_reason=?, user_decided_at=?, decision_review_status=NULL WHERE job_url_hash=?",
        (user_action, user_reason, datetime.now(timezone.utc).isoformat(), hash_url(job_url)),
    )
    conn.commit()


def update_decision(conn: sqlite3.Connection, job_url: str,
                     user_action: str | None = None, user_reason: str | None = None):
    """Partial version of record_decision, for the review server's UI: a
    click on an action button saves just the action; a separate save on the
    reason textarea saves just the reason. Only the fields actually passed
    are written — the other stays whatever it already was. Also resets
    decision_review_status to NULL, same as record_decision."""
    if user_action is not None and user_action not in VALID_USER_ACTIONS:
        raise ValueError(f"user_action must be one of {VALID_USER_ACTIONS}, got {user_action!r}")
    sets, params = [], []
    if user_action is not None:
        sets.append("user_action=?")
        params.append(user_action)
    if user_reason is not None:
        sets.append("user_reason=?")
        params.append(user_reason)
    if not sets:
        return
    sets.append("user_decided_at=?")
    params.append(datetime.now(timezone.utc).isoformat())
    sets.append("decision_review_status=NULL")
    params.append(hash_url(job_url))
    conn.execute(f"UPDATE seen_jobs SET {', '.join(sets)} WHERE job_url_hash=?", params)
    conn.commit()


VALID_REVIEW_STATUSES = ("applied", "declined")


def pending_review_decisions(conn: sqlite3.Connection) -> list[dict]:
    """skipped_fit decisions with a reason that /ftja-run's preflight (or a
    manual /ftja-review) hasn't proposed a criteria.json/rubric.md change
    for yet."""
    rows = conn.execute(
        """SELECT job_url, title, company, user_reason, user_decided_at
           FROM seen_jobs
           WHERE user_action = 'skipped_fit' AND user_reason != '' AND decision_review_status IS NULL"""
    ).fetchall()
    cols = ["job_url", "title", "company", "user_reason", "user_decided_at"]
    return [dict(zip(cols, row)) for row in rows]


def mark_reviewed(conn: sqlite3.Connection, job_url: str, status: str):
    """status='applied' if this decision's reason led to a criteria.json/
    rubric.md edit, 'declined' if the user was asked and said no — either
    way it's permanently excluded from future proposals unless the
    decision itself changes (record_decision resets this to NULL)."""
    if status not in VALID_REVIEW_STATUSES:
        raise ValueError(f"status must be one of {VALID_REVIEW_STATUSES}, got {status!r}")
    conn.execute(
        "UPDATE seen_jobs SET decision_review_status=? WHERE job_url_hash=?",
        (status, hash_url(job_url)),
    )
    conn.commit()


def unseen_only(conn: sqlite3.Connection, jobs: list[dict], url_key: str = "job_url") -> list[dict]:
    return [j for j in jobs if not is_seen(conn, j.get(url_key, ""))]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    rd = sub.add_parser("record-decision", help="record the candidate's apply/skip outcome for one job")
    rd.add_argument("--job-url", required=True)
    rd.add_argument("--action", required=True, choices=VALID_USER_ACTIONS)
    rd.add_argument("--reason", default="")
    rd.add_argument("--db", default="seen.db")

    pr = sub.add_parser("pending-review", help="list skipped_fit decisions not yet reviewed for a criteria/rubric proposal")
    pr.add_argument("--db", default="seen.db")

    mr = sub.add_parser("mark-reviewed", help="mark one decision's reason as applied/declined so it's never re-proposed")
    mr.add_argument("--job-url", required=True)
    mr.add_argument("--status", required=True, choices=VALID_REVIEW_STATUSES)
    mr.add_argument("--db", default="seen.db")

    args = ap.parse_args()
    if args.cmd == "record-decision":
        with connect(args.db) as conn:
            record_decision(conn, args.job_url, args.action, args.reason)
        print(f"recorded: {args.job_url} -> {args.action}", file=sys.stderr)
    elif args.cmd == "pending-review":
        with connect(args.db) as conn:
            pending = pending_review_decisions(conn)
        print(json.dumps(pending, ensure_ascii=False, indent=2))
    elif args.cmd == "mark-reviewed":
        with connect(args.db) as conn:
            mark_reviewed(conn, args.job_url, args.status)
        print(f"marked: {args.job_url} -> {args.status}", file=sys.stderr)


if __name__ == "__main__":
    main()
