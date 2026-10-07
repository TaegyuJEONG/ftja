import json
import os

from ftja.server import _fetch_jobs
from ftja.state import connect, is_seen, mark_seen, prune, prune_preview, record_decision
from ftja.pipeline_view import keyword_stats

BIG = "x" * 20000


def add(conn, n, days_old, **extra):
    mark_seen(conn, job_url=f"https://x/{n}", status="passed", stage_reached=2, title=f"Job {n}", company="Co",
              reasoning="Builds: met", description=BIG, criteria=[{"id": "a", "result": "met"}],
              t1_matched_keywords=["prototype"], t1_matched_sentences=["A sentence."], run_id="r", **extra)
    conn.execute("UPDATE seen_jobs SET last_seen_at = datetime('now', ?) WHERE job_url = ?", (f"-{days_old} days", f"https://x/{n}"))
    conn.commit()


def test_prune_removes_old_details_and_keeps_the_ledger(tmp_path):
    db = str(tmp_path / "seen.db")
    with connect(db) as conn:
        for n in range(60):
            add(conn, n, 90)
        add(conn, "applied", 90)
        record_decision(conn, "https://x/applied", "applied", "sent")
        add(conn, "recent", 10)
        record_decision(conn, "https://x/0", "skipped_fit", "too corporate")

        preview = prune_preview(conn, db)
        assert preview["jobs"] == 60 and preview["by_status"] == {"passed": 60} and preview["worth_asking"]
        assert preview["bytes_freed_estimate"] > 60 * 20000 and preview["database_bytes"] == os.path.getsize(db)
        assert conn.execute("SELECT COUNT(*) FROM seen_jobs WHERE description IS NULL").fetchone()[0] == 0  # preview changed nothing

        result = prune(conn, db)
        assert result["jobs"] == 60 and result["database_bytes_after"] < result["database_bytes_before"] - 1_000_000

        row = conn.execute("SELECT description, criteria, t1_matched_sentences, reasoning, status, t1_matched_keywords, "
                           "user_action, user_reason, pruned_at FROM seen_jobs WHERE job_url='https://x/0'").fetchone()
        assert row[:3] == (None, None, None) and row[3:8] == ("Builds: met", "passed", '["prototype"]', "skipped_fit", "too corporate")
        assert row[8]
        assert is_seen(conn, "https://x/0")  # still never re-judged
        for kept in ("applied", "recent"):
            assert conn.execute("SELECT description FROM seen_jobs WHERE job_url=?", (f"https://x/{kept}",)).fetchone()[0] == BIG
        assert prune_preview(conn, db)["jobs"] == 0  # nothing left to prune; a second pass is a no-op

    assert keyword_stats(db)[0] == {"keyword": "prototype", "matched": 62, "passed": 62, "review": 0, "stage2_fail": 0, "stage1_fail": 0}
    card = next(j for j in _fetch_jobs(db, "r", "passed", 1)["jobs"] if j["pruned_at"])
    assert card["criteria"] is None and card["reasoning"] == "Builds: met"


def test_a_job_judged_again_is_no_longer_marked_pruned(tmp_path):
    db = str(tmp_path / "seen.db")
    with connect(db) as conn:
        add(conn, 1, 90)
        prune(conn, db)
        add(conn, 1, 0)
        assert conn.execute("SELECT pruned_at, description FROM seen_jobs").fetchone() == (None, BIG)
