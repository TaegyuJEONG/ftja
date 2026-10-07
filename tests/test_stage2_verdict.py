import json

import pytest

from ftja import finalize as finalize_mod
from ftja.server import _fetch_jobs
from ftja.state import (connect, mark_feedback_reviewed, mark_seen, pending_criterion_feedback,
                        record_criterion_feedback)
from ftja.verdict import (clean_extras, compute_verdict, criteria_status, judge, load_criteria, quote_in_text,
                          render_rubric_md, save_criteria, validate_criteria)

DEFS = [
    {"id": "A1", "kind": "pass", "group": "builder", "label": "Direct build", "text": "Builds it personally."},
    {"id": "A2", "kind": "pass", "group": "builder", "label": "AI use", "text": "Uses AI tools."},
    {"id": "B1", "kind": "pass", "label": "Founder role", "text": "Founder-level ownership."},
    {"id": "F_intern", "kind": "fail", "label": "Internship", "text": "Internship or trainee."},
    {"id": "F_salary", "kind": "fail", "label": "Salary below target", "text": "Below 60k."},
    {"id": "P_own", "kind": "preference", "label": "Ownership", "text": "End-to-end ownership."},
]
JD = ("You use **Cursor** daily to prototype features\\.\n\n"
      "Test \\& learn: you ship what you build. Salary: 45 \\- 65 K€. This is a permanent role.")
CURSOR = "You use Cursor daily to prototype features."


def results(**overrides):
    base = {
        "A1": {"result": "met", "quote": CURSOR, "why": "Builds with Cursor."},
        "A2": {"result": "met", "quote": CURSOR, "why": "Cursor is an AI tool."},
        "B1": {"result": "not_met", "quote": None, "why": "No founder ownership."},
        "F_intern": {"result": "not_applicable", "quote": None, "why": ""},
        "F_salary": {"result": "not_applicable", "quote": None, "why": ""},
        "P_own": {"result": "met", "quote": "you ship what you build", "why": ""},
    }
    base.update(overrides)
    return [{"id": cid, **r} for cid, r in base.items() if r is not None]


def test_quote_matching_ignores_markdown_and_escapes():
    assert quote_in_text(CURSOR, JD)
    assert quote_in_text("Test & learn: you ship what you build", JD)
    assert quote_in_text("Salary: 45 - 65 K€", JD)
    assert quote_in_text("You use Cursor ... prototype features", JD)
    assert not quote_in_text("prototype features ... You use Cursor", JD)  # pieces out of order
    assert not quote_in_text("You use Lovable daily", JD)
    assert not quote_in_text("", JD)
    # non-breaking hyphen in the JD, plain hyphen in the quote (seen in a real posting)
    assert quote_in_text("Prendre part aux avant-ventes", "Prendre part aux avant\u2011ventes et aux réponses")


def test_pass_needs_every_criterion_of_one_group():
    judged = judge(DEFS, results(), JD)
    assert judged["verdict"] == "pass"
    assert [c["id"] for c in judged["criteria"]] == [d["id"] for d in DEFS]
    assert judged["criteria"][0]["text"] == "Builds it personally."  # the rule as it was when judged


def test_an_ungrouped_pass_criterion_passes_on_its_own():
    raw = results(A1={"result": "not_met", "quote": None, "why": ""},
                  B1={"result": "met", "quote": "This is a permanent role.", "why": ""})
    assert judge(DEFS, raw, JD)["verdict"] == "pass"
    flat = [{k: v for k, v in d.items() if k != "group"} for d in DEFS]  # a rubric with no groups at all
    assert judge(flat, results(A1={"result": "not_met", "quote": None, "why": ""}), JD)["verdict"] == "pass"


def test_unclear_pass_criterion_is_review():
    judged = judge(DEFS, results(A1={"result": "unclear", "quote": CURSOR, "why": "May be PM practice."}), JD)
    assert judged["verdict"] == "review"


def test_triggered_fail_criterion_fails_even_with_a_met_group():
    judged = judge(DEFS, results(F_salary={"result": "triggered", "quote": "Salary: 45 - 65 K€", "why": "Below 60k."}), JD)
    assert judged["verdict"] == "fail"


def test_unclear_fail_criterion_is_review():
    judged = judge(DEFS, results(F_salary={"result": "unclear", "quote": "Salary: 45 - 65 K€", "why": "Overlaps."}), JD)
    assert judged["verdict"] == "review"


def test_no_pass_group_left_is_fail():
    judged = judge(DEFS, results(A1={"result": "not_met", "quote": None, "why": "Engineers build."}), JD)
    assert judged["verdict"] == "fail"


def test_not_applicable_never_blocks_and_preferences_never_decide():
    judged = judge(DEFS, results(P_own={"result": "not_met", "quote": None, "why": ""}), JD)
    assert judged["verdict"] == "pass"


def test_met_with_a_quote_that_is_not_in_the_jd_is_downgraded():
    judged = judge(DEFS, results(A1={"result": "met", "quote": "You build agents with Claude Code.", "why": ""}), JD)
    a1 = judged["criteria"][0]
    assert (a1["result"], a1["downgraded_from"], a1["quote_verified"]) == ("unclear", "met", False)
    assert judged["verdict"] == "review"


def test_met_without_any_quote_is_downgraded():
    judged = judge(DEFS, results(A2={"result": "met", "quote": None, "why": "Obviously."}), JD)
    assert judged["criteria"][1]["result"] == "unclear"


def test_triggered_with_a_made_up_quote_does_not_fail_the_job():
    judged = judge(DEFS, results(F_intern={"result": "triggered", "quote": "This is an internship.", "why": ""}), JD)
    assert judged["verdict"] == "review"


def test_skipped_unknown_and_invented_criteria():
    raw = results(B1=None, F_intern={"result": "pass", "quote": None, "why": ""})
    raw.append({"id": "Z9", "result": "met", "quote": CURSOR, "why": "invented"})
    judged = judge(DEFS, raw, JD)
    by_id = {c["id"]: c for c in judged["criteria"]}
    assert "Z9" not in by_id
    assert by_id["B1"]["result"] == "unclear" and by_id["B1"]["why"] == "Not judged."
    assert by_id["F_intern"]["result"] == "unclear"
    assert judged["verdict"] == "review"  # the builder group is met, but a fail criterion is unsettled


def test_one_met_group_is_enough_while_another_is_unclear():
    criteria = [
        {"id": "a", "kind": "pass", "group": "x", "result": "unclear"},
        {"id": "b", "kind": "pass", "result": "met"},
    ]
    assert compute_verdict(criteria) == "pass"


def test_extras_keep_only_what_the_jd_says():
    extras = clean_extras({
        "company_line": "  Builds  dev tools.\n",
        "employer_requirements": [
            {"kind": "must", "label": "Uses Cursor", "quote": CURSOR, "candidate": "meets", "why": "Daily user."},
            {"kind": "must", "label": "Fluent German", "quote": "Fluent German required.", "candidate": "gap"},
            {"kind": "nice", "label": "Ships", "quote": "you ship what you build", "candidate": "maybe"},
            {"kind": "must", "label": "No quote", "candidate": "gap"},
        ],
        "notes": [{"text": "Permanent contract.", "quote": "This is a permanent role."},
                  {"text": "Starts in May.", "quote": "Start date: May."}, "Plain note.", {"text": "4"}, {"text": "5"}],
    }, JD)
    assert extras["company_line"] == "Builds dev tools."
    assert [(e["label"], e["kind"], e["candidate"]) for e in extras["employer"]] == [
        ("Uses Cursor", "must", "meets"), ("Ships", "preferred", "unclear")]
    assert [n["text"] for n in extras["notes"]] == ["Permanent contract.", "Plain note.", "4"]
    assert clean_extras({}, JD) == {"company_line": None, "employer": [], "notes": []}


def test_saving_the_list_rewrites_rubric_md_and_keeps_ids(tmp_path):
    (tmp_path / "rubric.md").write_text("# hand-written\n")
    edited = [dict(d) for d in DEFS] + [{"kind": "fail", "label": "Needs a car!", "text": "Driving licence required."}]
    edited[2]["label"] = "Founder or first hire"
    data = save_criteria(str(tmp_path), edited, {"pass": "Decide from the evidence.", "bogus": "x"})

    assert [c["id"] for c in data["criteria"]] == [d["id"] for d in DEFS] + ["needs_a_car"]
    assert (tmp_path / "rubric.md.bak").read_text() == "# hand-written\n"
    md = (tmp_path / "rubric.md").read_text()
    assert md == render_rubric_md(data)
    assert "- All of:\n  - **Direct build** — Builds it personally.\n  - **AI use**" in md
    assert "- **Founder or first hire** — Founder-level ownership." in md
    assert "## Fail" in md and "- **Needs a car!** — Driving licence required." in md
    assert "Decide from the evidence." in md and set(data["notes"]) == {"pass", "fail", "preference"}
    path = str(tmp_path / "rubric-criteria.json")
    assert criteria_status(path, str(tmp_path / "rubric.md")) == "ok"
    assert load_criteria(path)["criteria"][0]["group"] == "builder"

    (tmp_path / "rubric.md").write_text(md + "\nedited as text\n")
    assert criteria_status(path, str(tmp_path / "rubric.md")) == "stale"
    save_criteria(str(tmp_path), data["criteria"], data["notes"])  # the backup is only taken once
    assert (tmp_path / "rubric.md.bak").read_text() == "# hand-written\n"

    with pytest.raises(ValueError):
        save_criteria(str(tmp_path), [{"kind": "fail", "label": "Only a fail"}])
    with pytest.raises(ValueError):
        save_criteria(str(tmp_path), [{"kind": "pass", "label": " "}])


def test_validate_criteria():
    assert validate_criteria({"criteria": DEFS}) == []
    assert validate_criteria({"criteria": [d for d in DEFS if d["kind"] != "pass"]})
    assert validate_criteria({"criteria": DEFS + [DEFS[0]]})
    assert validate_criteria({"criteria": [{"id": "A1", "kind": "track", "label": "x"}]})
    assert validate_criteria([])


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(finalize_mod, "notify", lambda *a, **k: None)
    (tmp_path / "rubric.md").write_text("# rubric\n")
    return tmp_path


def run_finalize(project, entries):
    db = str(project / "seen.db")
    summary = finalize_mod.finalize(
        entries, db_path=db, out_dir=str(project), stats={"ts_start": "2026-10-06T08:00:00+00:00"},
        definitions=DEFS, descriptions={e["job_url"]: JD for e in entries})
    return db, summary


def test_finalize_sets_status_from_criteria_and_stores_the_jd(project):
    entries = [
        {"job_url": "https://x/1", "title": "PM", "company": "Passes", "status": "stage2_fail", "stage2": {
            "criteria": results(), "company_line": "Builds dev tools.",
            "employer_requirements": [{"kind": "must", "label": "Uses Cursor", "quote": CURSOR, "candidate": "meets", "why": "Daily."}],
            "notes": [{"text": "Permanent contract.", "quote": "This is a permanent role."}]}},
        {"job_url": "https://x/2", "title": "PM", "company": "Reviews",
         "stage2": {"criteria": results(A1={"result": "unclear", "quote": CURSOR, "why": "Ambiguous."})}},
        {"job_url": "https://x/3", "title": "PM", "company": "Fails",
         "stage2": {"criteria": results(A2={"result": "not_met", "quote": None, "why": "No AI."})}},
        {"job_url": "https://x/4", "title": "PM", "company": "Stage1", "status": "stage1_fail",
         "stage_reached": 1, "reasoning": "No builder signal."},
    ]
    db, summary = run_finalize(project, entries)
    assert (summary["passed"], summary["review"]) == (1, 1)

    with connect(db) as conn:
        rows = dict(conn.execute("SELECT company, status FROM seen_jobs"))
        stored = conn.execute("SELECT description, criteria, company_line, evidence_sentences, reasoning, employer, notes "
                              "FROM seen_jobs WHERE company='Passes'").fetchone()
        stage1_description = conn.execute("SELECT description FROM seen_jobs WHERE company='Stage1'").fetchone()[0]
    assert rows == {"Passes": "passed", "Reviews": "review", "Fails": "stage2_fail", "Stage1": "stage1_fail"}
    assert stored[0] == JD and stored[2] == "Builds dev tools."
    assert json.loads(stored[5])[0]["label"] == "Uses Cursor" and json.loads(stored[6])[0]["text"] == "Permanent contract."
    assert len(json.loads(stored[1])) == len(DEFS)
    assert CURSOR in json.loads(stored[3])
    assert "Direct build: met" in stored[4]
    assert stage1_description is None

    digest = (project / summary["digest_path"].split("/")[-1]).read_text()
    assert "1 passed, 1 to review" in digest and "# To review (1)" in digest
    assert "**Pass**\n- Met — Direct build: Builds with Cursor." in digest and "- 2 not applicable" in digest
    assert "*Builds dev tools.*" in digest and "- Meets — Must: Uses Cursor (Daily.)" in digest
    assert "**Notes**\n- Permanent contract." in digest
    rejected = (project / summary["rejected_path"].split("/")[-1]).read_text()
    assert "**Pass**\n- Met — Direct build" in rejected and "- 2 not met" in rejected and "**Reason**: No builder signal." in rejected

    run = json.loads((project / "runs.jsonl").read_text())
    assert (run["stage2_pass"], run["stage2_review"], run["stage2_fail"]) == (1, 1, 1)


def test_finalize_reports_stage2_jobs_without_jd_text(project, capsys):
    summary = finalize_mod.finalize([{"job_url": "https://x/1", "title": "PM", "stage2": {"criteria": results()}}],
                                    db_path=str(project / "seen.db"), out_dir=str(project), definitions=DEFS)
    assert summary["quotes_unchecked"] == 1
    assert "no JD text" in capsys.readouterr().err
    assert "without JD text" in (project / "run.log").read_text()


def test_a_quote_may_come_from_the_title_line(project):
    entry = {"job_url": "https://x/1", "title": "Founding PM - CDI Paris", "company": "Co",
             "stage2": {"criteria": results(B1={"result": "met", "quote": "Founding PM", "why": "First product hire."})}}
    db, _ = run_finalize(project, [entry])
    b1 = next(c for c in entry["criteria"] if c["id"] == "B1")
    assert (b1["result"], b1["quote_verified"]) == ("met", True)


def test_finalize_refuses_criteria_without_a_criteria_list(project):
    with pytest.raises(ValueError):
        finalize_mod.finalize([{"job_url": "https://x/1", "stage2": {"criteria": results()}}],
                              db_path=str(project / "seen.db"), out_dir=str(project))


def test_criterion_feedback_survives_a_rejudge_and_gets_resolved(project):
    db, _ = run_finalize(project, [{"job_url": "https://x/1", "title": "PM", "company": "Co", "stage2": {"criteria": results()}}])
    with connect(db) as conn:
        record_criterion_feedback(conn, "https://x/1", "A1", "disagree", " Engineers build here. ")
        mark_seen(conn, job_url="https://x/1", status="passed", stage_reached=2, criteria=[])  # a later run
        pending = pending_criterion_feedback(conn)
        assert [(p["criterion_id"], p["comment"]) for p in pending] == [("A1", "Engineers build here.")]

        mark_feedback_reviewed(conn, "https://x/1", "A1", "applied")
        assert pending_criterion_feedback(conn) == []

        record_criterion_feedback(conn, "https://x/1", "A1", "disagree", "Edited.")  # editing reopens it
        assert len(pending_criterion_feedback(conn)) == 1
        assert record_criterion_feedback(conn, "https://x/1", "A1", None) == {}  # withdrawn

        with pytest.raises(KeyError):
            record_criterion_feedback(conn, "https://x/unknown", "A1", "disagree")
        with pytest.raises(ValueError):
            record_criterion_feedback(conn, "https://x/1", "A1", "agree")


def test_fetch_jobs_returns_structured_judgment_and_old_rows(project):
    db, summary = run_finalize(project, [
        {"job_url": "https://x/1", "title": "PM", "company": "Co",
         "stage2": {"criteria": results(A1={"result": "unclear", "quote": CURSOR, "why": "Ambiguous."})}}])
    with connect(db) as conn:
        mark_seen(conn, job_url="https://x/old", status="review", stage_reached=2, title="Old", company="Row",
                  reasoning="Prose only.", evidence_sentences=["q"], run_id=summary["run_id"])
    jobs = {j["company"]: j for j in _fetch_jobs(db, summary["run_id"], "review", 1)["jobs"]}
    assert jobs["Co"]["criteria"][0]["result"] == "unclear" and jobs["Co"]["criteria"][0]["text"]
    assert "description" not in jobs["Co"] and jobs["Co"]["criteria_feedback"] == {}
    assert jobs["Co"]["employer"] == [] and jobs["Row"]["notes"] == []
    assert jobs["Row"]["criteria"] is None and jobs["Row"]["reasoning"] == "Prose only."
