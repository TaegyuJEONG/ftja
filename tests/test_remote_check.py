"""The Remote check: an optional stage between the code filter and Stage 1.

The existing run tests (tests/test_live_run.py) are the proof that a run
with the check switched off behaves as it did before; they are not edited.
These cover the check itself and the stage switches."""
import json
import os
import shutil

import pytest

from ftja import finalize as finalize_mod
from ftja import live, remote, run_bridge
from ftja.filter_stage0 import filter_stage0
from ftja.lock import acquire
from ftja.runs_view import list_runs
from ftja.state import connect, is_seen
from ftja.verdict import save_criteria

REPO = os.path.dirname(os.path.dirname(__file__))
BUILD = "You will prototype features yourself with Cursor every day."
JOBS = [
    {"job_url": "https://x/remote", "title": "Product Builder", "company": "Alpha", "location": "Paris",
     "description": f"Alpha makes developer tools. {BUILD} This role is fully remote within the EU. We meet twice a year for a team gathering."},
    {"job_url": "https://x/hybrid", "title": "Product Builder", "company": "Beta", "location": "Lyon",
     "description": f"Beta sells insurance software. {BUILD} We work in a hybrid setup, three days a week in our Lyon office."},
    {"job_url": "https://x/silent", "title": "Product Builder", "company": "Gamma", "location": "Lille",
     "description": f"Gamma builds payroll tools for bakeries. {BUILD} You own the roadmap end to end."},
    {"job_url": "https://x/nokeyword", "title": "Account Manager (Remote)", "company": "Delta", "location": "Nice",
     "description": "Delta ships logistics dashboards. You manage key accounts and renewals for our largest customers."},
]


def config(**changes):
    data = remote.default_config()
    data.update(changes)
    return data


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(finalize_mod, "notify", lambda *a, **k: None)
    for name in ("stage1_prompt.md", "stage2_prompt.md", "remote_prompt.md"):
        shutil.copy(os.path.join(REPO, name), tmp_path / name)
    (tmp_path / "criteria.json").write_text(json.dumps({
        "search_terms": ["Product Builder"], "location": "France", "hours_old": 24, "results_wanted": 50,
        "languages": ["en"], "keywords": {"tier1": ["prototype"]}, "exclude_keywords": [],
        "stages": {"remote": True, "stage1": True, "stage2": True}}))
    save_criteria(str(tmp_path), [
        {"id": "builds", "kind": "pass", "label": "Builds personally", "text": "Builds it personally."},
        {"id": "intern", "kind": "fail", "label": "Internship", "text": "Internship."}])
    acquire(str(tmp_path / ".ftja.lock"))
    return str(tmp_path)


def set_stages(project, **stages):
    path = os.path.join(project, "criteria.json")
    criteria = json.load(open(path))
    criteria["stages"] = stages
    json.dump(criteria, open(path, "w"))


def scrape(run_dir, jobs=JOBS):
    live._write_json(os.path.join(run_dir, "scrape", "0.json"), jobs)


def finalize(run_dir, project, capsys):
    import sys
    argv = sys.argv
    sys.argv = ["finalize", "--run-dir", run_dir, "--db", os.path.join(project, "seen.db"), "--out-dir", project]
    try:
        finalize_mod.main()
    finally:
        sys.argv = argv
    capsys.readouterr()


def answer_remote(run_dir, by_company):
    """Write the Remote check's answer for each job, from {company: {category: (answer, detail, keyword)}}.
    The evidence is the sentence holding `keyword`."""
    for entry in live._read_json(os.path.join(run_dir, "rc", "manifest.json")):
        if entry.get("auto"):
            continue
        answers = []
        for category, (value, detail, keyword) in by_company[entry["company"]].items():
            sids = [b["s_id"] for b in entry["blocks"] if keyword and keyword in b["sentence"]]
            answers.append({"id": category, "answer": value, "detail": detail, "evidence_sids": sids})
        live._write_json(os.path.join(run_dir, "rc", f"{entry['n']}.out.json"), {"answers": answers})


REMOTE_ANSWERS = {
    "Alpha": {"remote_level": ("fully_remote", "Remote within the EU", "fully remote"),
              "work_location": ("restricted", "EU", "fully remote"),
              "onsite_duties": ("occasional", "Two gatherings a year", "gathering")},
    "Beta": {"remote_level": ("hybrid", "3 days a week in the Lyon office", "hybrid")},
}


# ---- the setup file ----

def test_a_setup_is_checked_before_it_is_saved(tmp_path):
    saved = remote.save_config(str(tmp_path), {"categories": [
        {"label": "Remote level", "keywords": ["remote", "remote", " "],
         "options": [{"label": "Fully remote"}, {"label": "Hybrid", "rule": "fail"}]}]})
    category = saved["categories"][0]
    assert category["id"] == "remote_level" and category["keywords"] == ["remote"] and category["unknown"] == "pass"
    assert category["options"] == [{"id": "fully_remote", "label": "Fully remote", "rule": "pass"},
                                   {"id": "hybrid", "label": "Hybrid", "rule": "fail"}]
    assert saved["drop_without_keywords"] is True
    assert remote.load_config(str(tmp_path / remote.CONFIG_FILE)) == saved
    # an id that is already there is kept when the name changes: stored answers refer to it
    renamed = remote.validate_config({"categories": [{**category, "label": "How remote"}]})
    assert renamed["categories"][0]["id"] == "remote_level"
    for broken in ({}, {"categories": []}, {"categories": [{"label": "x", "keywords": ["a"], "options": []}]},
                   {"categories": [{"label": "x", "keywords": [], "options": [{"label": "y"}]}]},
                   {"categories": [{"label": "x", "keywords": ["a"], "options": [{"label": "y", "rule": "maybe"}]}]}):
        with pytest.raises(ValueError):
            remote.validate_config(broken)
    assert remote.load_config(str(tmp_path / "nothing.json")) is None
    assert remote.config_or_default(str(tmp_path / "elsewhere"))["categories"]  # nothing saved: the built-in setup


# ---- which sentences the model sees ----

def test_any_category_keyword_gets_a_posting_read_and_the_title_counts(tmp_path):
    cfg = config()
    blocks = remote.blocks_for(JOBS[0], cfg)
    assert [b["s_id"] for b in blocks][0] == "T" and blocks[0]["matched"] is False  # the title is shown, as context here
    hit = {k for b in blocks for k in b["matched_keywords"]}
    assert {"remote", "gathering", "EU"} <= hit  # keywords of three different categories
    assert remote.blocks_for(JOBS[2], cfg) == []  # nothing about remote work anywhere
    only_title = remote.blocks_for(JOBS[3], cfg)  # "(Remote)" in the title is enough to have it read
    assert [(b["s_id"], b["matched"]) for b in only_title] == [("T", True)]
    # an indirect phrase is caught by whatever keyword the setup lists for it
    indirect = {"title": "PM", "description": "We sell shoes. You can work where you feel at home. We ship worldwide."}
    cfg["categories"][0]["keywords"].append("feel at home")
    assert any("feel at home" in b["matched_keywords"] for b in remote.blocks_for(indirect, cfg))

    covered = remote.coverage(JOBS, config())
    assert (covered["postings"], covered["with_a_keyword"], covered["without"]) == (4, 3, 1)
    assert covered["by_keyword"]["remote"] == 2 and "WFH" in covered["never_matched"]


def test_the_prompt_lists_each_question_with_its_answers():
    cfg = config()
    prompt = remote.render_prompt(live._template(os.path.join(REPO, "remote_prompt.md")), cfg, JOBS[0],
                                  remote.blocks_for(JOBS[0], cfg))
    assert "- `remote_level` — How remote is the role itself?" in prompt and "- `fully_remote`: Fully remote" in prompt
    assert "- `?`: the sentences do not say" in prompt and "T[title] Product Builder" in prompt
    assert "[K] This role is fully remote within the EU." in prompt and "[ctx] " in prompt
    assert "{" not in prompt.split("Output ONLY")[0] and "Used by `/ftja-run`" not in prompt


# ---- the rules ----

def test_code_applies_the_rule_set_for_each_answer_and_for_not_stated():
    cfg = config()
    shown = {"S1", "S2"}
    hybrid = {"answers": [{"id": "remote_level", "answer": "hybrid", "detail": "3 days", "evidence_sids": ["S1"]}]}
    judged = remote.judge(cfg, hybrid, shown)
    assert judged["verdict"] == "fail" and judged["reason"] == "Remote level: Hybrid: regular office days (3 days)"
    by_id = {a["id"]: a for a in judged["answers"]}
    assert by_id["remote_level"]["rule"] == "fail" and by_id["time_zone"] == {
        "id": "time_zone", "label": "Time zone", "answer": "?", "answer_label": "Not stated", "detail": "",
        "rule": "pass", "evidence_sids": [], "note": ""}  # a category the model left out is "not stated"

    # the user decides, per category, what "not stated" means
    assert remote.judge(cfg, {"answers": []}, shown)["verdict"] == "pass"
    cfg["categories"][0]["unknown"] = "fail"
    silent = remote.judge(cfg, {"answers": []}, shown)
    assert silent["verdict"] == "fail" and silent["reason"] == "Remote level: Not stated"
    assert remote.judge(cfg, None)["verdict"] == "fail"  # a posting with no remote keyword: all "?", no model call

    # the user flips a rule: the same answer now passes, with no new model call
    cfg["categories"][0]["unknown"] = "pass"
    cfg["categories"][0]["options"][1]["rule"] = "pass"
    assert remote.judge(cfg, hybrid, shown)["verdict"] == "pass"


def test_an_answer_without_a_sentence_behind_it_is_read_as_not_stated():
    cfg = config()
    shown = {"S1"}
    unbacked = {"answers": [{"id": "remote_level", "answer": "on_site", "detail": "Office", "evidence_sids": []}]}
    judged = remote.judge(cfg, unbacked, shown)
    assert judged["verdict"] == "pass" and judged["answers"][0]["answer"] == "?" and "without pointing" in judged["answers"][0]["note"]
    invented = {"answers": [{"id": "remote_level", "answer": "on_site", "evidence_sids": ["S9"]}]}  # a sentence it was never shown
    assert remote.judge(cfg, invented, shown)["answers"][0]["answer"] == "?"
    # an answer that is not on the category's list makes the job run again
    assert remote.answer_problems(cfg, shown, {"answers": [{"id": "remote_level", "answer": "mostly"}]}) == [
        "remote_level: 'mostly' is not one of the listed answers"]
    assert remote.answer_problems(cfg, shown, {"verdict": "pass"}) == ["no `answers` list"]
    assert remote.answer_problems(cfg, shown, unbacked) == []


# ---- the code filter ----

def test_stage0_drops_a_posting_without_remote_keywords_only_when_told_to():
    criteria = {"languages": ["en"], "keywords": {"tier1": ["prototype"]}}
    plain, dropped = filter_stage0(JOBS, criteria)
    assert [j["company"] for j in plain] == ["Alpha", "Beta", "Gamma"] and "no_remote_keyword" not in dropped
    assert all("_remote_blocks" not in j for j in plain)  # without the check, a job carries nothing new

    kept, dropped = filter_stage0(JOBS, criteria, remote=config())
    assert [j["company"] for j in kept] == ["Alpha", "Beta"] and dropped["no_remote_keyword"] == 1 and dropped["no_tier1_hit"] == 1
    assert all(j["_remote_blocks"] for j in kept)

    kept, dropped = filter_stage0(JOBS, criteria, remote=config(drop_without_keywords=False))
    assert [j["company"] for j in kept] == ["Alpha", "Beta", "Gamma"] and "no_remote_keyword" not in dropped
    assert kept[2]["_remote_blocks"] == []

    # a run without Stage 1 does not ask for a Stage 1 keyword, but still works the sentences out
    kept, dropped = filter_stage0(JOBS, criteria, remote=config(), stage1=False)
    assert [j["company"] for j in kept] == ["Alpha", "Beta", "Delta"] and "no_tier1_hit" not in dropped
    assert kept[0]["_t1_blocks"] and kept[2]["_t1_blocks"] == [] and kept[2]["_content_hash"]


# ---- the stage switches ----

def test_stage_switches_and_the_setting_from_before_there_were_switches(project):
    assert live.stages_of({}) == ["stage1", "stage2"]
    assert live.stages_of({"stage1_only": True}) == ["stage1"]
    assert live.stages_of({"stages": {"remote": True, "stage1": False, "stage2": True}, "stage1_only": True}) == ["remote", "stage2"]
    set_stages(project, remote=False, stage1=False, stage2=False)
    with pytest.raises(SystemExit):
        live.start(project)
    set_stages(project, remote=False, stage1=True, stage2=True)
    run = live.start(project)
    assert run["stages"] == ["stage1", "stage2"] and not os.path.exists(os.path.join(run["run_dir"], remote.CONFIG_FILE))
    with pytest.raises(SystemExit):
        live.prepare_remote(run["run_dir"], project)  # not part of this run


# ---- whole runs ----

def test_a_run_with_the_remote_check_before_stage1_and_stage2(project, capsys):
    db = os.path.join(project, "seen.db")
    remote.save_config(project, config())
    run = live.start(project)
    run_dir = run["run_dir"]
    assert run["stages"] == ["remote", "stage1", "stage2"] and run["criteria_snapshot"]["stages"] == run["stages"]
    remote.save_config(project, config(drop_without_keywords=False))  # edited mid-run: the run keeps its own copy
    scrape(run_dir)
    counts = live.stage0(run_dir, project, db)
    assert counts["passed"] == 2 and counts["dropped"]["no_remote_keyword"] == 1 and counts["dropped"]["no_tier1_hit"] == 1

    with pytest.raises(SystemExit):
        live.prepare_stage1(run_dir, project)  # the Remote check comes first
    prepared = live.prepare_remote(run_dir, project)
    assert (prepared["jobs"], prepared["without_remote_keywords"]) == (2, 0)
    assert "[K] This role is fully remote within the EU." in open(os.path.join(run_dir, "rc", "0.txt")).read()
    assert live.state(project)["remote"] == {"total": 2, "pass": 0, "fail": 0, "pending": 2}
    assert live.check_remote(run_dir)["rerun"] == {0: ["no usable answer file"], 1: ["no usable answer file"]}
    with pytest.raises(SystemExit):
        live.prepare_stage1(run_dir, project)  # its answers are not in yet

    live._write_json(os.path.join(run_dir, "rc", "0.out.json"), {"answers": [{"id": "remote_level", "answer": "sometimes"}]})
    assert live.check_remote(run_dir)["rerun"][0] == ["remote_level: 'sometimes' is not one of the listed answers"]
    answer_remote(run_dir, REMOTE_ANSWERS)
    assert live.check_remote(run_dir) == {"jobs": 2, "rerun": {}}
    state = live.state(project)
    assert state["remote"] == {"total": 2, "pass": 1, "fail": 1, "pending": 0} and state["stages"] == run["stages"]
    failed = live.remote_jobs(project, db, run["run_id"])
    assert failed["total"] == 1 and failed["jobs"][0]["company"] == "Beta"
    assert failed["jobs"][0]["reason"] == "Remote level: Hybrid: regular office days (3 days a week in the Lyon office)"

    assert live.prepare_stage1(run_dir, project)["jobs"] == 1  # only what the Remote check let through
    manifest = live._read_json(os.path.join(run_dir, "s1", "manifest.json"))
    assert [e["company"] for e in manifest] == ["Alpha"]
    sid = next(b["s_id"] for b in manifest[0]["blocks"] if b["matched"])
    live._write_json(os.path.join(run_dir, "s1", "0.out.json"),
                     {"verdict": "pass", "evidence_sids": [sid], "reason": "Builds it.", "blocks": [{"sid": sid, "why": "Builds"}]})
    assert live.prepare_stage2(run_dir, project)["jobs"] == 1
    live._write_json(os.path.join(run_dir, "s2", "0.out.json"), {
        "criteria": [{"id": "builds", "result": "met", "quote": BUILD, "why": "Prototypes with Cursor."},
                     {"id": "intern", "result": "not_applicable", "quote": None, "why": ""}]})
    assert live.assemble_results(run_dir) == {
        "results": 2, "stage1_fail": 0, "stage2": 1, "stopped_after_stage1": False, "waiting_for_stage2": 0,
        "remote_pass": 1, "remote_fail": 1, "stopped_after": None, "waiting": 0}
    finalize(run_dir, project, capsys)

    with connect(db) as conn:
        rows = {r[0]: r[1:] for r in conn.execute("SELECT company, status, stage_reached, reasoning, remote FROM seen_jobs")}
        assert is_seen(conn, "https://x/hybrid") and not is_seen(conn, "https://x/silent")  # a code drop is checked again next time
    assert rows["Alpha"][0] == "passed" and rows["Beta"][:3] == ("remote_fail", 0, failed["jobs"][0]["reason"])
    stored = json.loads(rows["Alpha"][3])  # the check's reading stays with a job that went on
    assert [a["answer"] for a in stored["answers"]] == ["fully_remote", "restricted", "?", "occasional"]
    assert any(b["sid"] == "T" for b in stored["blocks"])
    record = list_runs(project)[0]
    assert (record["remote_pass"], record["remote_fail"], record["stage1_pass"], record["stage1_fail"],
            record["stage2_pass"], record["stage2_pending"]) == (1, 1, 1, 0, 1, 0)
    assert record["stages"] == ["remote", "stage1", "stage2"] and record["stopped_after"] is None
    assert "failed at the Remote check" in open(os.path.join(project, "rejected-" + live.run_dir_for(project, run["run_id"]).split("/")[-1] + ".md")).read()

    after = live.remote_jobs(project, db, run["run_id"])  # now from seen.db
    assert after["total"] == 1 and after["jobs"][0]["reason"] == "Remote level: Hybrid: regular office days"
    passed = live.remote_jobs(project, db, run["run_id"], verdict="pass", page_size=None)
    assert [j["company"] for j in passed["jobs"]] == ["Alpha"] and passed["jobs"][0]["answers"][0]["detail"] == "Remote within the EU"


def test_a_remote_only_run_stops_and_can_go_on_to_stage1_then_stage2(project, capsys):
    db = os.path.join(project, "seen.db")
    set_stages(project, remote=True, stage1=False, stage2=False)
    run = live.start(project)
    run_dir = run["run_dir"]
    scrape(run_dir)
    counts = live.stage0(run_dir, project, db)
    assert counts["passed"] == 3 and "no_tier1_hit" not in counts["dropped"]  # Delta has no Stage 1 keyword and stays
    for step in (live.prepare_stage1, live.prepare_stage2):
        with pytest.raises(SystemExit):
            step(run_dir, project)  # not part of this run: nothing is spent by mistake
    live.prepare_remote(run_dir, project)
    answer_remote(run_dir, {**REMOTE_ANSWERS, "Delta": {"remote_level": ("fully_remote", "", "Remote")}})
    assert live.assemble_results(run_dir) == {
        "results": 3, "stage1_fail": 0, "stage2": 0, "stopped_after_stage1": False, "waiting_for_stage2": 0,
        "remote_pass": 2, "remote_fail": 1, "stopped_after": "remote", "waiting": 2}
    finalize(run_dir, project, capsys)

    assert live.state(project) is None and live.awaiting_stage2(run_dir)
    record = list_runs(project)[0]
    assert (record["stages"], record["stopped_after"], record["stage2_pending"], record["remote_pass"]) == (["remote"], "remote", 2, 2)
    with connect(db) as conn:
        assert dict(conn.execute("SELECT status, COUNT(*) FROM seen_jobs GROUP BY status")) == {"remote_fail": 1, "remote_pass": 2}
    digest = open(os.path.join(project, "digest-" + os.path.basename(run_dir) + ".md")).read()
    assert "# Passed the Remote check (2)" in digest and "- Remote level: Fully remote — Remote within the EU" in digest
    assert "  > This role is fully remote within the EU." in digest  # each answer with the sentence it rests on

    with pytest.raises(SystemExit):
        live.continue_run(project, run["run_id"], "remote")  # only a later stage
    again = live.continue_run(project, run["run_id"], "stage1")
    assert again == {"run_id": run["run_id"], "run_dir": run_dir, "to": "stage1", "remote_passes": 2}
    assert live.run_stages(run_dir) == ["remote", "stage1"]
    prepared = live.prepare_stage1(run_dir, project)
    assert prepared["jobs"] == 1 and prepared["failed_by_code_no_keyword"] == 1  # Delta: no keyword sentence, no model call
    manifest = live._read_json(os.path.join(run_dir, "s1", "manifest.json"))
    assert [(e["company"], bool(e.get("auto_fail"))) for e in manifest] == [("Alpha", False), ("Delta", True)]
    assert not os.path.exists(os.path.join(run_dir, "s1", "1.txt")) and live.check_stage1(run_dir)["jobs"] == 1
    sid = next(b["s_id"] for b in manifest[0]["blocks"] if b["matched"])
    live._write_json(os.path.join(run_dir, "s1", "0.out.json"),
                     {"verdict": "pass", "evidence_sids": [sid], "reason": "Builds it.", "blocks": [{"sid": sid, "why": "Builds"}]})
    assert live.state(project)["stage1"] == {"total": 2, "pass": 1, "fail": 1, "pending": 0}
    out = live.assemble_results(run_dir)
    assert (out["stopped_after"], out["waiting"], out["stage1_fail"], out["stopped_after_stage1"]) == ("stage1", 1, 1, True)
    finalize(run_dir, project, capsys)
    with connect(db) as conn:
        rows = dict(conn.execute("SELECT company, status FROM seen_jobs"))
        reason = conn.execute("SELECT reasoning FROM seen_jobs WHERE company = 'Delta'").fetchone()[0]
    assert rows == {"Alpha": "stage1_pass", "Beta": "remote_fail", "Delta": "stage1_fail"} and reason == live.NO_KEYWORD_REASON

    live.continue_run(project, run["run_id"])  # Stage 2 is the default next stage
    assert live.run_stages(run_dir) == ["remote", "stage1", "stage2"] and live.prepare_stage2(run_dir, project)["jobs"] == 1
    live._write_json(os.path.join(run_dir, "s2", "0.out.json"), {
        "criteria": [{"id": "builds", "result": "met", "quote": BUILD, "why": "Prototypes."},
                     {"id": "intern", "result": "not_applicable", "quote": None, "why": ""}]})
    assert live.assemble_results(run_dir)["waiting"] == 0
    finalize(run_dir, project, capsys)
    records = [r for r in list_runs(project) if r["run_id"] == run["run_id"]]
    assert len(records) == 1 and (records[0]["stage2_pass"], records[0]["stage2_pending"], records[0]["remote_fail"]) == (1, 0, 1)
    assert not live.awaiting_stage2(run_dir)


def test_the_remote_check_can_feed_stage2_directly_and_keep_postings_without_keywords(project, capsys):
    db = os.path.join(project, "seen.db")
    set_stages(project, remote=True, stage1=False, stage2=True)
    cfg = config(drop_without_keywords=False)
    cfg["categories"][0]["unknown"] = "fail"  # not stated how remote: out
    remote.save_config(project, cfg)
    run_dir = live.start(project)["run_dir"]
    scrape(run_dir)
    assert live.stage0(run_dir, project, db)["passed"] == 4  # nothing is dropped for lacking a remote keyword
    prepared = live.prepare_remote(run_dir, project)
    assert (prepared["jobs"], prepared["without_remote_keywords"]) == (3, 1)
    manifest = live._read_json(os.path.join(run_dir, "rc", "manifest.json"))
    assert manifest[-1]["company"] == "Gamma" and manifest[-1]["auto"] and not os.path.exists(os.path.join(run_dir, "rc", "3.txt"))
    answer_remote(run_dir, {**REMOTE_ANSWERS, "Delta": {"remote_level": ("fully_remote", "", "Remote")}})
    failed = {e["company"]: e["reason"] for e in live._remote_results(run_dir)[1]}
    assert failed == {"Beta": "Remote level: Hybrid: regular office days (3 days a week in the Lyon office)",
                      "Gamma": "Remote level: Not stated"}  # no keyword: every category "?", judged by the rule, no model call
    assert live.prepare_stage2(run_dir, project)["jobs"] == 2  # the Remote check's passes, straight to Stage 2
    for n in (0, 1):
        live._write_json(os.path.join(run_dir, "s2", f"{n}.out.json"), {
            "criteria": [{"id": "builds", "result": "not_met", "quote": None, "why": ""},
                         {"id": "intern", "result": "not_applicable", "quote": None, "why": ""}]})
    out = live.assemble_results(run_dir)
    assert (out["results"], out["stage2"], out["remote_fail"], out["waiting"]) == (4, 2, 2, 0)
    finalize(run_dir, project, capsys)
    with connect(db) as conn:
        assert dict(conn.execute("SELECT status, COUNT(*) FROM seen_jobs GROUP BY status")) == {"remote_fail": 2, "stage2_fail": 2}


# ---- the buttons ----

def test_the_continue_button_names_the_next_stage_and_generate_reaches_a_waiting_session(tmp_path):
    import threading
    import time
    root, run_id = str(tmp_path), "2026-10-12T07:00:00Z"
    assert run_bridge.request_remote(root) == {"listening": False}  # nobody waiting: the viewer shows the line to type
    assert not os.path.exists(os.path.join(root, run_bridge.ACTION_FILE))

    def wait_and(press):
        result = {}
        waiter = threading.Thread(target=lambda: result.update(run_bridge.wait_for_continue(root, run_id, timeout_seconds=10)))
        waiter.start()
        for _ in range(50):
            if run_bridge.listening(root)["listening"]:
                break
            time.sleep(0.05)
        assert press() == {"listening": True}
        waiter.join(timeout=5)
        return result

    assert wait_and(lambda: run_bridge.request_continue(root, run_id, "stage1")) == {"status": "continue", "run_id": run_id, "to": "stage1"}
    assert wait_and(lambda: run_bridge.request_remote(root)) == {"status": "remote", "run_id": run_id}


def test_generate_keeps_the_four_answers_for_the_agent(tmp_path):
    root = str(tmp_path)
    assert remote.read_request(root) == {"status": "none", "answers": {}}
    with pytest.raises(ValueError):
        remote.write_request(root, {"time_zone": "CET"})  # where you live is what the setup is written for
    remote.write_request(root, {"residence": "Paris, France", "work_countries": "France, EU", "time_zone": "CET",
                                "work_permit": "France only", "something_else": "ignored"})
    request = remote.read_request(root)
    assert request["status"] == "requested" and request["answers"] == {
        "residence": "Paris, France", "work_countries": "France, EU", "time_zone": "CET", "work_permit": "France only"}
    assert remote.set_request_status(root, "generating")["status"] == "generating"
    assert remote.read_request(root)["status"] == "generating"
    stale = json.load(open(os.path.join(root, remote.REQUEST_FILE)))
    stale["began_at"] -= remote.GENERATING_STALE_SECONDS + 1
    json.dump(stale, open(os.path.join(root, remote.REQUEST_FILE), "w"))
    assert remote.read_request(root)["status"] == "failed"  # the session died: the spinner does not turn forever
