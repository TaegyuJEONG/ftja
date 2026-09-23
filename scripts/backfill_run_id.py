"""One-off: assign run_id to the 3 runs that predate the run_id column.

Only needed once, on this specific project's existing runs.jsonl/seen.db —
not part of the regular /ftja-run flow (finalize() generates run_id itself
for every run from here on). Safe to delete after running.

Assigns:
  run_id="legacy-run1"                    -> M1, no ts_start/ts_end recorded
  run_id=<run2's ts_end>                   -> "Product Manager" test (2026-09-19)
  run_id=<run3's ts_start>                 -> "product" test (2026-09-20)

seen.db rows are bucketed by last_seen_at date, since no other join key
exists for rows written before run_id existed. Approximate: a job re-touched
by a later run before this column existed would already show the later
run's data anyway (mark_seen always overwrites status/reasoning/etc on
re-touch), so the date bucket here matches what's actually in the row.
"""
import json

from ftja.state import connect

RUNS_PATH = "runs.jsonl"
DB_PATH = "seen.db"

RUN2_ID = "2026-09-19T09:56:50.821836+00:00"  # run2's ts_end (no ts_start recorded)
RUN3_ID = "2026-09-20T14:10:05Z"              # run3's ts_start

DATE_TO_RUN_ID = {
    "2026-09-17": "legacy-run1",
    "2026-09-18": "legacy-run1",
    "2026-09-19": RUN2_ID,
    "2026-09-20": RUN3_ID,
}


def backfill_runs_jsonl():
    with open(RUNS_PATH) as f:
        lines = [json.loads(line) for line in f if line.strip()]

    assert len(lines) == 3, f"expected exactly 3 existing run records, found {len(lines)} - check before rerunning"
    lines[0]["run_id"] = "legacy-run1"
    lines[1]["run_id"] = RUN2_ID
    lines[2]["run_id"] = RUN3_ID

    with open(RUNS_PATH, "w") as f:
        for r in lines:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"runs.jsonl: assigned run_id to {len(lines)} records")


def backfill_seen_db():
    with connect(DB_PATH) as conn:
        total = 0
        for date_prefix, run_id in DATE_TO_RUN_ID.items():
            cur = conn.execute(
                "UPDATE seen_jobs SET run_id = ? WHERE run_id IS NULL AND substr(last_seen_at, 1, 10) = ?",
                (run_id, date_prefix),
            )
            total += cur.rowcount
        conn.commit()
        remaining = conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE run_id IS NULL").fetchone()[0]
    print(f"seen.db: assigned run_id to {total} rows, {remaining} still NULL (unexpected dates)")


if __name__ == "__main__":
    backfill_runs_jsonl()
    backfill_seen_db()
