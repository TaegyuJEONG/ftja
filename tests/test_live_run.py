import json
import os
import shutil

import pytest

from ftja import finalize as finalize_mod
from ftja import live
from ftja.lock import acquire, release
from ftja.runs_view import list_runs
from ftja.state import connect, is_seen
from ftja.verdict import save_criteria

REPO = os.path.dirname(os.path.dirname(__file__))
BUILD = "You will prototype features yourself with Cursor every day."
JOBS = [
    {"job_url": "https://x/1", "title": "Product Builder", "company": "Alpha", "location": "Paris",
     "description": f"Alpha makes developer tools for small teams. {BUILD} You own the roadmap end to end."},
    {"job_url": "https://x/2", "title": "Product Manager", "company": "Beta", "location": "Lyon",
     "description": "Beta sells insurance software. Engineers prototype features and you write the specifications for them."},
    {"job_url": "https://x/3", "title": "Chef de projet", "company": "Gamma", "location": "Lille",
     "description": "Nous cherchons un chef de projet pour piloter la feuille de route et animer les comités de pilotage."},
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(finalize_mod, "notify", lambda *a, **k: None)
    for name in ("stage1_prompt.md", "stage2_prompt.md"):
        shutil.copy(os.path.join(REPO, name), tmp_path / name)
    (tmp_path / "criteria.json").write_text(json.dumps({
        "search_terms": ["Product Builder", "Product Manager"], "location": "France", "is_remote": False,
        "hours_old": 24, "results_wanted": 50, "exact_phrase_search": True, "languages": ["en"],  # an old setting: ignored
        "keywords": {"tier1": ["prototype"]}, "exclude_keywords": []}))
    save_criteria(str(tmp_path), [
        {"id": "builds", "kind": "pass", "label": "Builds personally", "text": "Builds it personally."},
        {"id": "intern", "kind": "fail", "label": "Internship", "text": "Internship."}])
    acquire(str(tmp_path / ".ftja.lock"))
    return str(tmp_path)


def test_a_run_from_start_to_finish_as_the_viewer_sees_it(project, capsys):
    db = os.path.join(project, "seen.db")
    assert live.state(project) is None

    run = live.start(project)
    run_dir = run["run_dir"]
    state = live.state(project)
    assert state["run_id"] == run["run_id"] and state["criteria_snapshot"]["location"] == "France"
    assert "is_remote" not in state["criteria_snapshot"]  # LinkedIn ignores the filter; it is no longer sent or shown
    assert [t["term"] for t in state["scrape"]["terms"]] == ["Product Builder", "Product Manager"]
    assert state["scrape"]["total"] == 0 and state["stage0"] is None and state["jobs"] == []

    # what scrape_term writes, without calling LinkedIn
    for n, jobs in enumerate([JOBS[:2], JOBS[1:]]):  # job 2 comes back for both terms
        live._write_json(os.path.join(run_dir, "scrape", f"{n}.json"), jobs)
        live._write_json(os.path.join(run_dir, "scrape", f"{n}.skipped.json"), {"Strategy Lead": 3})  # titles left out: not jobs
        live._write_json(os.path.join(run_dir, "scrape", f"{n}.progress.json"),
                         {"term": state["scrape"]["terms"][n]["term"], "count": len(jobs), "read": 40, "done": True})
    assert live.state(project)["scrape"] == {**live.state(project)["scrape"], "total": 4, "done": True}

    counts = live.stage0(run_dir, project, db)
    assert (counts["scraped"], counts["passed"], counts["dropped"]["already_seen"]) == (3, 2, 0)  # merged by url; the French posting is dropped by code
    assert live.state(project)["stage0"]["passed"] == 2 and live.state(project)["stage1"] is None
    found_by = {j["job_url"]: j["_search_terms"] for j in live._read_json(os.path.join(run_dir, "stage0.json"))}
    assert found_by == {"https://x/1": ["Product Builder"], "https://x/2": ["Product Builder", "Product Manager"]}  # which searches returned each job

    assert live.prepare_stage1(run_dir, project)["jobs"] == 2
    prompt = open(os.path.join(run_dir, "s1", "0.txt"), encoding="utf-8").read()
    assert "TITLE: Product Builder | COMPANY: Alpha" in prompt and "[T1] You will prototype" in prompt
    assert "## Pass" in prompt and "{blocks}" not in prompt and "Used by `/ftja-run`" not in prompt
    assert live.state(project)["stage1"] == {"total": 2, "pass": 0, "fail": 0, "pending": 2}

    with pytest.raises(SystemExit):  # Stage 1 isn't finished
        live.prepare_stage2(run_dir, project)

    sid = next(b["s_id"] for b in live._read_json(os.path.join(run_dir, "s1", "manifest.json"))[1]["blocks"] if b["matched"])
    sid0 = next(b["s_id"] for b in live._read_json(os.path.join(run_dir, "s1", "manifest.json"))[0]["blocks"] if b["matched"])
    # a pass that cites nothing is not usable: the job is rerun, not counted as passed
    open(os.path.join(run_dir, "s1", "0.out.json"), "w", encoding="utf-8").write('{"verdict": "pass", "evidence_sids": []}')
    assert live.check_stage1(run_dir)["rerun"][0] == ["a pass with no evidence sentence"]
    open(os.path.join(run_dir, "s1", "0.out.json"), "w", encoding="utf-8").write(
        '```json\n{"verdict": "pass", "evidence_sids": ["%s"], "reason": "Builds it.", "blocks": [{"sid": "%s", "why": "Builds"}]}\n```' % (sid0, sid0))
    live._write_json(os.path.join(run_dir, "s1", "1.out.json"),
                     {"verdict": "fail", "evidence_sids": [sid], "reason": "Engineers do the prototyping.",
                      "blocks": [{"sid": sid, "why": "The engineers prototype, not this role."}]})
    assert live.state(project)["stage1"] == {"total": 2, "pass": 1, "fail": 1, "pending": 0}
    fails = live.stage1_fails(project, db, run["run_id"])
    assert fails["total"] == 1 and fails["jobs"][0]["reason"] == "Engineers do the prototyping."
    assert fails["jobs"][0]["keywords"] == ["prototype"]
    blocks = fails["jobs"][0]["blocks"]  # everything Stage 1 read: the keyword sentence and its neighbours
    hit = [b for b in blocks if b["matched"]]
    assert hit == [{"sid": sid, "sentence": hit[0]["sentence"], "matched": True,
                    "keywords": ["prototype"], "why": "The engineers prototype, not this role."}]
    assert "Engineers prototype features" in hit[0]["sentence"]
    context = [b for b in blocks if not b["matched"]]
    assert context and all(b["why"] == "" and b["keywords"] == [] for b in context)
    assert any("Beta sells insurance software" in b["sentence"] for b in context)

    live._write_json(os.path.join(run_dir, "s1", "1.out.json.keep"), live._read_json(os.path.join(run_dir, "s1", "1.out.json")))
    os.remove(os.path.join(run_dir, "s1", "1.out.json"))  # as if job 1's verdict hadn't landed yet
    early = live.prepare_stage2(run_dir, project, partial=True)  # Stage 2 can start on the passes so far
    assert (early["jobs"], early["new"], early["stage1_still_pending"]) == (1, [0], 1)
    os.rename(os.path.join(run_dir, "s1", "1.out.json.keep"), os.path.join(run_dir, "s1", "1.out.json"))
    done = live.prepare_stage2(run_dir, project)  # nothing is renumbered or repeated
    assert (done["jobs"], done["new"]) == (1, [])
    prompt = open(os.path.join(run_dir, "s2", "0.txt"), encoding="utf-8").read()
    assert "- builds (pass) Builds personally: Builds it personally." in prompt and BUILD in prompt
    assert f"Read `{os.path.join(run_dir, 'rubric.md')}`" in prompt  # the run's own copy of the rubric
    state = live.state(project)
    assert state["stage2"] == {"total": 1, "pending": 1, "passed": 0, "review": 0, "fail": 0}
    assert state["jobs"][0] == {**state["jobs"][0], "title": "Product Builder", "status": "judging"}

    with pytest.raises(SystemExit):  # Stage 2 isn't finished
        live.assemble_results(run_dir)

    live._write_json(os.path.join(run_dir, "s2", "0.out.json"), {
        "company_line": "Developer tools for small teams.",
        "criteria": [{"id": "builds", "result": "met", "quote": BUILD, "why": "Prototypes with Cursor."},
                     {"id": "intern", "result": "not_applicable", "quote": None, "why": ""}],
        "employer_requirements": [{"kind": "must", "label": "Owns the roadmap", "quote": "You own the roadmap end to end.",
                                   "candidate": "meets", "why": "Founder."}]})
    card = live.state(project)["jobs"][0]
    assert card["status"] == "passed" and card["criteria"][0]["quote_verified"] is True
    assert card["company_line"] and card["employer"][0]["label"] == "Owns the roadmap"

    # the rubric is edited in the viewer mid-run: this run still judges against its own copy
    save_criteria(project, [{"id": "other", "kind": "pass", "label": "Something else", "text": "x"}])

    assert live.assemble_results(run_dir) == {"results": 2, "stage1_fail": 1, "stage2": 1,
                                             "stopped_after_stage1": False, "waiting_for_stage2": 0}
    import sys
    argv = sys.argv
    sys.argv = ["finalize", "--run-dir", run_dir, "--total-tokens", "1234", "--db", db, "--out-dir", project]
    try:
        finalize_mod.main()
    finally:
        sys.argv = argv
    capsys.readouterr()

    assert live.state(project) is None  # finished: no longer live
    with connect(db) as conn:
        rows = {r[0]: r[1:] for r in conn.execute("SELECT company, status, reasoning, criteria FROM seen_jobs")}
    with connect(db) as conn:  # what Stage 1 read is kept for a job that went on to Stage 2 as well
        kept = conn.execute("SELECT t1_blocks, stage1_reason FROM seen_jobs WHERE company='Alpha'").fetchone()
    assert any(b["matched"] for b in json.loads(kept[0])) and kept[1] == "Builds it."
    assert rows["Alpha"][0] == "passed" and [c["id"] for c in json.loads(rows["Alpha"][2])] == ["builds", "intern"]
    assert rows["Beta"][:2] == ("stage1_fail", "Engineers do the prototyping.")
    record = json.loads(open(os.path.join(project, "runs.jsonl"), encoding="utf-8").read())
    assert record["run_id"] == run["run_id"] and record["total_tokens"] == 1234 and record["duration_seconds"] >= 0
    assert record["scraped_count"] == 3 and record["scraped_by_term"][0] == {"term": "Product Builder", "location": "", "count": 2, "read": 40}  # 2 scraped among 40 search results
    with connect(db) as conn:  # the same posting under a second URL, and a job from before reasons existed
        from ftja.state import mark_seen
        mark_seen(conn, job_url="https://x/2-repost", status="stage1_fail", stage_reached=1, title="Product Manager",
                  company="Beta", reasoning="Engineers do the prototyping.", run_id=run["run_id"])
        mark_seen(conn, job_url="https://x/9", status="stage1_fail", stage_reached=1, title="Old", company="Row",
                  reasoning="Stage 1 (keyword excerpt): no pass criterion evidenced", run_id=run["run_id"],
                  t1_matched_sentences=["A sentence."], t1_matched_keywords=["mvp"])
    after = live.stage1_fails(project, db, run["run_id"])  # now served from seen.db
    assert after["total"] == 2 and [j["reason"] for j in after["jobs"]] == ["", "Engineers do the prototyping."]
    assert after["jobs"][0]["blocks"] == [{"sentence": "A sentence.", "matched": True, "keywords": [], "why": ""}]
    assert after["jobs"][0]["keywords"] == ["mvp"]
    assert [b["why"] for b in after["jobs"][1]["blocks"] if b["matched"]] == ["The engineers prototype, not this role."]
    assert len(after["jobs"][1]["blocks"]) == len(blocks)  # context sentences are stored too
    assert after["jobs"][1]["keywords"] == ["prototype"]

    # the next run doesn't re-judge what this one recorded
    release(os.path.join(project, ".ftja.lock")); acquire(os.path.join(project, ".ftja.lock"))
    again = live.start(project)
    live._write_json(os.path.join(again["run_dir"], "scrape", "0.json"), JOBS)
    counts = live.stage0(again["run_dir"], project, db)
    assert (counts["passed"], counts["dropped"]["already_seen"]) == (0, 2)  # dropped at Stage 0, by code
    assert live.prepare_stage1(again["run_dir"], project)["jobs"] == 0
    assert live.state(project)["stage0"]["passed"] == live.state(project)["stage1"]["total"] == 0
    assert live.current_run_dir(project) == again["run_dir"]


def test_an_abandoned_run_is_not_shown_as_running(project):
    live.start(project)
    assert live.state(project) is not None
    release(os.path.join(project, ".ftja.lock"))
    assert live.state(project) is None


def test_replay_writes_nothing_to_the_run_history(project, monkeypatch):
    db = os.path.join(project, "seen.db")
    monkeypatch.setattr(live.time, "sleep", lambda s: None)
    finalize_mod.finalize(
        [{"job_url": "https://x/1", "title": "Product Builder", "company": "Alpha", "stage2": {"criteria": [
            {"id": "builds", "result": "met", "quote": BUILD, "why": "Builds."}]}},
         {"job_url": "https://x/2", "title": "PM", "company": "Beta", "status": "stage1_fail", "stage_reached": 1,
          "reasoning": "Engineers build.", "evidence_sentences": ["Engineers prototype features."]}],
        db_path=db, out_dir=project, stats={"ts_start": "2026-10-06T08:00:00Z", "scraped_count": 40,
                                             "criteria_snapshot": {"search_terms": ["Product Builder"], "results_wanted": 50}},
        definitions=[{"id": "builds", "kind": "pass", "label": "Builds personally"}],
        descriptions={"https://x/1": BUILD})
    release(os.path.join(project, ".ftja.lock"))  # a replay needs no lock
    history = open(os.path.join(project, "runs.jsonl"), encoding="utf-8").read()

    seen = {}
    real_write = live._write_json

    def spy(path, value):
        real_write(path, value)
        if path.endswith("0.out.json") and os.sep + "s2" + os.sep in path:
            seen["state"] = live.state(project)
    monkeypatch.setattr(live, "_write_json", spy)
    run_dir = live.replay(project, db, seconds=1)

    state = seen["state"]
    assert state["replay"] and state["replay_of"] == "2026-10-06T08:00:00Z"
    assert state["scrape"]["total"] == 40 and state["stage1"] == {"total": 2, "pass": 1, "fail": 1, "pending": 0}
    assert state["jobs"][0]["status"] == "passed" and state["jobs"][0]["criteria"][0]["quote"] == BUILD
    assert os.path.exists(os.path.join(run_dir, "done.json")) and live.state(project) is None
    assert open(os.path.join(project, "runs.jsonl"), encoding="utf-8").read() == history

    live.replay(project, db, seconds=1, per_term=10)  # a shorter sample of the same run
    sample = seen["state"]
    assert sample["replay_sample"] and sample["scrape"]["total"] == 10
    assert sample["criteria_snapshot"]["results_wanted"] == 10 and sample["stage1"]["total"] == 2


def test_stage1_answers_are_checked_against_the_sentences_the_prompt_had(tmp_path):
    entry = {"n": 0, "blocks": [{"s_id": "S1", "matched": True}, {"s_id": "S2", "matched": False}, {"s_id": "S3", "matched": True}]}
    ok = {"verdict": "pass", "evidence_sids": ["S1", "S2"], "blocks": [{"sid": "S1", "why": "a"}, {"sid": "S3", "why": "b"}]}
    assert live.stage1_problems(entry, ok) == ([], [])
    invented = {"verdict": "fail", "evidence_sids": ["S9"], "blocks": [{"sid": "S1", "why": "a"}, {"sid": "S3", "why": "b"}]}
    assert live.stage1_problems(entry, invented)[0] == ["cites sentences the prompt did not contain: S9"]
    invented_block = {"verdict": "fail", "evidence_sids": ["S1"], "blocks": [{"sid": "S1", "why": "a"}, {"sid": "S7", "why": "x"}]}
    assert "S7" in live.stage1_problems(entry, invented_block)[0][0]
    # a keyword sentence with no reading is kept but reported
    gap = {"verdict": "fail", "evidence_sids": ["S1"], "blocks": [{"sid": "S1", "why": "a"}]}
    assert live.stage1_problems(entry, gap) == ([], ["no reading given for keyword sentence(s): S3"])


def test_token_total_is_the_sum_of_what_was_recorded(tmp_path):
    run_dir = str(tmp_path)
    assert live.tokens_total(run_dir) == 0
    live.record_tokens(run_dir, 51000, "s1 batch 1")
    assert live.record_tokens(run_dir, 49000, "s1 batch 2") == {"recorded": 49000, "total": 100000}


def test_a_zero_token_total_is_never_recorded(tmp_path):
    run_dir = str(tmp_path)
    assert finalize_mod.resolve_total_tokens(0, run_dir) == 0   # not counted: the run record leaves it out
    assert finalize_mod.resolve_total_tokens(None, run_dir) == 0
    live.record_tokens(run_dir, 30000, "s2 job 0")
    assert finalize_mod.resolve_total_tokens(0, run_dir) == 30000  # the ledger fills in what was passed as 0
    assert finalize_mod.resolve_total_tokens(555, run_dir) == 555  # an explicit count still wins


def _finalize(run_dir, db, project, capsys):
    import sys
    argv = sys.argv
    sys.argv = ["finalize", "--run-dir", run_dir, "--db", db, "--out-dir", project]
    try:
        finalize_mod.main()
    finally:
        sys.argv = argv
    capsys.readouterr()


def test_a_run_can_stop_after_stage1_and_be_continued_later(project, capsys):
    db = os.path.join(project, "seen.db")
    criteria = json.load(open(os.path.join(project, "criteria.json"), encoding="utf-8"))
    json.dump({**criteria, "stage1_only": True}, open(os.path.join(project, "criteria.json"), "w", encoding="utf-8"))

    run = live.start(project)
    run_dir = run["run_dir"]
    assert run["criteria_snapshot"]["stage1_only"] is True
    live._write_json(os.path.join(run_dir, "scrape", "0.json"), JOBS[:2])
    live.stage0(run_dir, project, db)
    live.prepare_stage1(run_dir, project)
    manifest = live._read_json(os.path.join(run_dir, "s1", "manifest.json"))
    sids = [next(b["s_id"] for b in e["blocks"] if b["matched"]) for e in manifest]
    live._write_json(os.path.join(run_dir, "s1", "0.out.json"),
                     {"verdict": "pass", "evidence_sids": [sids[0]], "reason": "Builds it.", "blocks": [{"sid": sids[0], "why": "Builds"}]})
    live._write_json(os.path.join(run_dir, "s1", "1.out.json"),
                     {"verdict": "fail", "evidence_sids": [sids[1]], "reason": "Engineers prototype.", "blocks": [{"sid": sids[1], "why": "Not this role"}]})

    # Stage 2 is skipped: no prompts are prepared, and the pass is recorded as waiting
    assert live.assemble_results(run_dir) == {"results": 2, "stage1_fail": 1, "stage2": 0,
                                             "stopped_after_stage1": True, "waiting_for_stage2": 1}
    _finalize(run_dir, db, project, capsys)
    assert live.state(project) is None and live.awaiting_stage2(run_dir)
    record = list_runs(project)[0]
    assert (record["stage1_pass"], record["stage2_pending"], record["stage2_pass"]) == (1, 1, 0)
    with connect(db) as conn:
        assert dict(conn.execute("SELECT status, COUNT(*) FROM seen_jobs GROUP BY status")) == {"stage1_fail": 1, "stage1_pass": 1}
        assert all(is_seen(conn, j["job_url"]) for j in JOBS[:2])  # a later run does not judge the waiting job again
    passes = live.stage1_fails(project, db, run["run_id"], verdict="pass")
    assert passes["total"] == 1 and passes["jobs"][0]["reason"] == "Builds it."

    live.start(project)  # other runs come and go; the waiting run's files are kept
    for _ in range(live.KEEP_RUNS + 1):
        live.start(project)
    assert os.path.isdir(run_dir)

    # "Continue to Stage 2": the same run is live again and goes on from where it stopped
    again = live.continue_run(project, run["run_id"])
    assert again == {"run_id": run["run_id"], "run_dir": run_dir, "stage1_passes": 1}
    assert live.state(project)["run_id"] == run["run_id"]
    with pytest.raises(SystemExit):
        live.continue_run(project, run["run_id"])  # it is no longer waiting
    assert live.prepare_stage2(run_dir, project)["jobs"] == 1
    live._write_json(os.path.join(run_dir, "s2", "0.out.json"), {
        "company_line": None, "employer_requirements": [], "notes": [],
        "criteria": [{"id": "builds", "result": "met", "quote": BUILD, "why": "Prototypes with Cursor."},
                     {"id": "intern", "result": "not_applicable", "quote": None, "why": ""}]})
    assert live.assemble_results(run_dir)["stopped_after_stage1"] is False
    _finalize(run_dir, db, project, capsys)

    records = [r for r in list_runs(project) if r["run_id"] == run["run_id"]]
    assert len(records) == 1  # the same run, not a second entry
    assert (records[0]["stage2_pending"], records[0]["stage2_pass"], records[0]["stage1_pass"]) == (0, 1, 1)
    with connect(db) as conn:
        assert dict(conn.execute("SELECT status, COUNT(*) FROM seen_jobs GROUP BY status")) == {"stage1_fail": 1, "passed": 1}
    assert not live.awaiting_stage2(run_dir)


def test_the_continue_button_reaches_a_waiting_session_and_only_a_waiting_one(tmp_path):
    import threading
    import time
    from ftja import run_bridge
    root, run_id = str(tmp_path), "2026-10-11T07:00:00Z"
    assert run_bridge.listening(root)["listening"] is False
    assert run_bridge.request_continue(root, run_id) == {"listening": False}  # nobody waiting: the viewer shows the line to type

    result = {}
    waiter = threading.Thread(target=lambda: result.update(run_bridge.wait_for_continue(root, run_id, timeout_seconds=10)))
    waiter.start()
    for _ in range(50):
        if run_bridge.listening(root)["listening"]:
            break
        time.sleep(0.05)
    assert run_bridge.listening(root)["run_id"] == run_id
    # the request made before the wait began was dropped: an old click is not a go-ahead
    assert not os.path.exists(os.path.join(root, run_bridge.ACTION_FILE))
    assert run_bridge.request_continue(root, "another-run") == {"listening": False}
    time.sleep(0.7)
    assert waiter.is_alive()
    assert run_bridge.request_continue(root, run_id) == {"listening": True}
    waiter.join(timeout=5)
    assert result == {"status": "continue", "run_id": run_id}
    assert run_bridge.listening(root)["listening"] is False
    assert run_bridge.wait_for_continue(root, run_id, timeout_seconds=1)["status"] == "timeout"


def test_every_search_term_is_searched_in_every_location(project, monkeypatch):
    criteria = json.load(open(os.path.join(project, "criteria.json"), encoding="utf-8"))
    criteria.update(search_terms=["Product Manager", "Product Owner", "Product Lead"],
                    locations=["France", "Germany", "Switzerland"], location="ignored when there is a list")
    json.dump(criteria, open(os.path.join(project, "criteria.json"), "w", encoding="utf-8"))

    run = live.start(project)
    run_dir = run["run_dir"]
    assert [(s["term"], s["location"]) for s in run["searches"]] == [
        ("Product Manager", "France"), ("Product Manager", "Germany"), ("Product Manager", "Switzerland"),
        ("Product Owner", "France"), ("Product Owner", "Germany"), ("Product Owner", "Switzerland"),
        ("Product Lead", "France"), ("Product Lead", "Germany"), ("Product Lead", "Switzerland")]
    assert run["criteria_snapshot"]["locations"] == ["France", "Germany", "Switzerland"]
    shown = live.state(project)["scrape"]["terms"]
    assert len(shown) == 9 and shown[4] == {**shown[4], "term": "Product Owner", "location": "Germany", "count": 0}

    asked = []
    import ftja.scrape as scrape_mod
    monkeypatch.setattr(scrape_mod, "scrape", lambda **kw: asked.append((kw["search_term"], kw["location"])) or [
        {"job_url": f"https://x/{kw['search_term']}/{kw['location']}", "title": kw["search_term"],
         "company": f"{kw['search_term']} {kw['location']} Ltd", "location": kw["location"],
         "description": f"{BUILD} This team sits in {kw['location']} and hires a {kw['search_term']}."},
        {"job_url": "https://x/shared", "title": "Product Owner", "company": "Shared Co", "location": "Remote",
         "description": f"{BUILD} Every search returns this one."}])
    for n in range(len(run["searches"])):
        assert live.scrape_term(run_dir, n) == 2
    assert asked == [(s["term"], s["location"]) for s in run["searches"]]  # nine searches, each term in each place

    counts = live.stage0(run_dir, project, os.path.join(project, "seen.db"))
    assert counts["scraped"] == 10  # nine postings of their own, and one that every search returned, counted once
    shared = next(j for j in live._read_json(os.path.join(run_dir, "stage0.json")) if j["job_url"] == "https://x/shared")
    assert shared["_search_terms"] == ["Product Manager", "Product Owner", "Product Lead"]


def test_a_single_location_from_before_there_could_be_several_still_works(project):
    run = live.start(project)  # the fixture's criteria.json has only "location": "France"
    assert [(s["term"], s["location"]) for s in run["searches"]] == [("Product Builder", "France"), ("Product Manager", "France")]
    assert run["criteria_snapshot"]["locations"] == ["France"] and run["criteria_snapshot"]["location"] == "France"
    old_run = {"search_terms": ["Product Manager"], "location": "European Union"}  # a run.json written by an older version
    assert live.searches(old_run) == [{"term": "Product Manager", "location": "European Union"}]
