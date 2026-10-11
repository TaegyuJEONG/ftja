import json
import os

import pytest

from ftja import runs_store
from ftja.finalize import finalize
from ftja.lock import acquire
from ftja.state import connect, is_seen, record_criterion_feedback, update_decision


def a_run(project, db, run_id, urls):
    """A finished run that judged `urls`, with its working directory."""
    os.makedirs(os.path.join(project, ".ftja-run", runs_store.run_stamp(run_id)))
    open(os.path.join(project, ".ftja-run", runs_store.run_stamp(run_id), "done.json"), "w").write("{}")
    results = [{"job_url": u, "title": "PM", "company": "Acme", "status": "stage1_fail", "stage_reached": 1,
                "reasoning": "Hybrid."} for u in urls]
    finalize(results, db_path=db, out_dir=project, stats={"ts_start": run_id})


@pytest.fixture
def project(tmp_path):
    root = str(tmp_path)
    open(os.path.join(root, "rubric.md"), "w").write("## Pass\nremote\n")
    return root, os.path.join(root, "seen.db")


def test_each_run_writes_its_own_digest_and_rejected_file(project):
    root, db = project
    a_run(root, db, "2026-10-11T07:00:00Z", ["u1"])
    a_run(root, db, "2026-10-11T09:30:15Z", ["u2"])  # the same day: used to overwrite the first
    names = sorted(n for n in os.listdir(root) if n.startswith(("digest-", "rejected-")))
    assert names == ["digest-2026-10-11T07-00-00Z.md", "digest-2026-10-11T09-30-15Z.md",
                     "rejected-2026-10-11T07-00-00Z.md", "rejected-2026-10-11T09-30-15Z.md"]


def test_a_deleted_run_is_judged_again_and_can_be_restored(project):
    root, db = project
    test_run, real_run = "2026-10-11T07:00:00Z", "2026-10-11T09:00:00Z"
    a_run(root, db, test_run, ["u1", "u2"])
    a_run(root, db, real_run, ["u3"])
    with connect(db) as conn:
        update_decision(conn, "u1", user_action="skipped_fit", user_reason="Too junior")

    preview = runs_store.delete_preview(root, db, test_run)
    assert (preview["jobs"], preview["decisions"], preview["reasons"], preview["not_yet_reviewed"]) == (2, 1, 1, 1)
    assert preview["files"] == ["digest-2026-10-11T07-00-00Z.md", "rejected-2026-10-11T07-00-00Z.md"]

    assert runs_store.delete_run(root, db, test_run)["jobs"] == 2
    with connect(db) as conn:  # its postings are new again; the other run is untouched
        assert [is_seen(conn, u) for u in ("u1", "u2", "u3")] == [False, False, True]
    assert [r["run_id"] for r in runs_store._read_runs(root)] == [real_run]
    assert not os.path.exists(os.path.join(root, ".ftja-run", "2026-10-11T07-00-00Z"))
    assert not os.path.exists(os.path.join(root, "digest-2026-10-11T07-00-00Z.md"))
    trash = runs_store.list_trash(root)
    assert [(t["run_id"], t["jobs"], t["decisions"]) for t in trash] == [(test_run, 2, 1)]
    assert runs_store.storage(root, db)["trash"] > 0

    a_run(root, db, "2026-10-11T10:00:00Z", ["u2"])  # a later run judges one of them again
    restored = runs_store.restore_run(root, db, trash[0]["name"])
    assert (restored["jobs"], restored["judged_again_since"]) == (1, 1)
    with connect(db) as conn:
        assert conn.execute("SELECT run_id, user_reason FROM seen_jobs WHERE job_url = 'u1'").fetchone() == (test_run, "Too junior")
        assert conn.execute("SELECT run_id FROM seen_jobs WHERE job_url = 'u2'").fetchone()[0] == "2026-10-11T10:00:00Z"
    assert [r["run_id"] for r in runs_store._read_runs(root)][0] == test_run  # back in its place, oldest first
    assert os.path.isdir(os.path.join(root, ".ftja-run", "2026-10-11T07-00-00Z"))
    assert os.path.isfile(os.path.join(root, "digest-2026-10-11T07-00-00Z.md"))
    assert runs_store.list_trash(root) == []


def test_the_trash_is_emptied_only_on_request_and_a_running_run_cannot_be_deleted(project):
    root, db = project
    a_run(root, db, "2026-10-11T07:00:00Z", ["u1"])
    a_run(root, db, "2026-10-11T08:00:00Z", ["u2"])
    runs_store.delete_run(root, db, "2026-10-11T07:00:00Z")
    assert runs_store.empty_trash(root)["removed"] == 1 and runs_store.list_trash(root) == []
    with pytest.raises(runs_store.RunStoreError):
        runs_store.restore_run(root, db, "../seen.db")

    os.remove(os.path.join(root, ".ftja-run", "2026-10-11T08-00-00Z", "done.json"))
    acquire(os.path.join(root, ".ftja.lock"))
    with pytest.raises(runs_store.RunStoreError):
        runs_store.delete_run(root, db, "2026-10-11T08:00:00Z")
    with pytest.raises(runs_store.RunStoreError):
        runs_store.delete_preview(root, db, "no-such-run")
