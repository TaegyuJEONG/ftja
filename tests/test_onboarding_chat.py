"""Setup runs in the chat: each step is a command that checks its input,
saves it and returns. Nothing waits for the viewer."""
import json
import os
import subprocess
import sys

import pytest

from ftja import live
from ftja.onboarding import (OnboardingError, draft_rubric_criteria, finish, next_step, read_state, reset,
                             save_profile, save_rubric, save_search, validate_stage0_draft, STATE_FILE)
from ftja.verdict import criteria_status, load_criteria

SEARCH = {
    "titles": ["Product Builder", " "],
    "locations": ["France", "Germany", " "],
    "results_wanted": 5000,   # settings from before every title had to match:
    "title_must_match": False,  # neither is kept
    "filter": {"keywords": ["prototype", "prototypage"], "languages": ["en", "FR"], "exclude_keywords": ["sales"],
               "keyword_language": {"prototypage": "fr", "gone": "fr"}},
}
ANSWERS = {"clear_yes": "- Builds AI products: owns discovery to launch\n- Entrepreneurial culture",
           "dealbreakers": "Internship; Pure sales", "preferences": "Salary above 80k"}


@pytest.fixture
def project(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "이력서.pdf").write_bytes(b"%PDF-1.4")
    return str(tmp_path)


def through_search(root):
    save_profile(root, ["profile/이력서.pdf"], "Builder with 7 years of product work.")
    save_search(root, SEARCH)


def test_each_answer_line_is_its_own_criterion_of_the_right_kind():
    draft = draft_rubric_criteria([{"id": k, "answer": v} for k, v in ANSWERS.items()])
    kinds = [c["kind"] for c in draft["criteria"]]
    assert kinds == ["pass", "pass", "fail", "fail", "preference"]
    assert draft["criteria"][0]["label"] == "Builds AI products"
    assert all("group" not in c for c in draft["criteria"])  # a job passes on any one
    assert len({c["id"] for c in draft["criteria"]}) == len(draft["criteria"])


def test_the_steps_run_in_order_and_finish_writes_what_a_run_reads(project):
    assert next_step(read_state(project)) == "profile"
    with pytest.raises(OnboardingError):
        save_search(project, SEARCH)  # no profile yet
    save_profile(project, ["profile/이력서.pdf"], "Builder with 7 years of product work.\n")
    with pytest.raises(OnboardingError):
        save_rubric(project, ANSWERS)  # no search settings yet
    state = save_search(project, SEARCH)
    criteria = state["search"]
    assert criteria["search_terms"] == ["Product Builder"]
    assert criteria["locations"] == ["France", "Germany"] and criteria["location"] == "France, Germany"
    assert criteria["hours_old"] == 24 and "results_wanted" not in criteria and "title_must_match" not in criteria
    assert criteria["languages"] == ["en", "fr"] and criteria["exclude_keywords"] == ["sales"]
    assert criteria["keywords"] == {"tier1": ["prototype", "prototypage"], "tier1_language": {"prototypage": "fr"}}
    assert [s["location"] for s in live.searches(criteria)] == ["France", "Germany"]  # a run searches each
    with pytest.raises(OnboardingError):
        finish(project)  # no rubric yet
    save_rubric(project, ANSWERS)
    assert next_step(read_state(project)) == "finish"
    finish(project)
    assert next_step(read_state(project)) == "done"

    with open(os.path.join(project, "criteria.json"), encoding="utf-8") as f:
        assert json.load(f)["search_terms"] == ["Product Builder"]
    path = os.path.join(project, "rubric-criteria.json")
    assert criteria_status(path, os.path.join(project, "rubric.md")) == "ok"
    assert len(load_criteria(path)["criteria"]) == 5
    with open(os.path.join(project, "profile", "summary.md"), encoding="utf-8") as f:
        assert f.read() == "Builder with 7 years of product work.\n"
    with open(os.path.join(project, "profile", "sources.json"), encoding="utf-8") as f:
        assert json.load(f)[0]["path"] == "profile/이력서.pdf"  # relative: the folder can move


def test_a_missing_source_or_empty_summary_is_refused(project):
    with pytest.raises(OnboardingError):
        save_profile(project, ["profile/missing.pdf"], "summary")
    with pytest.raises(OnboardingError):
        save_profile(project, ["profile/이력서.pdf"], "   ")
    with pytest.raises(OnboardingError):
        save_profile(project, [], "summary")


def test_search_settings_need_titles_a_location_keywords_and_languages(project):
    save_profile(project, ["profile/이력서.pdf"], "summary")
    for broken in ({**SEARCH, "titles": []}, {**SEARCH, "locations": []},
                   {**SEARCH, "filter": {**SEARCH["filter"], "keywords": []}},
                   {**SEARCH, "filter": {**SEARCH["filter"], "languages": []}}):
        with pytest.raises(OnboardingError):
            save_search(project, broken)
    one = save_search(project, {"titles": ["PM"], "location": "France", "filter": {"keywords": ["AI"], "languages": ["en"]}})
    assert one["search"]["locations"] == ["France"] and "title_must_match" not in one["search"]


def test_every_answer_is_required_and_a_rubric_must_be_able_to_pass(project):
    through_search(project)
    with pytest.raises(OnboardingError):
        save_rubric(project, {**ANSWERS, "dealbreakers": " "})
    with pytest.raises(OnboardingError):
        save_rubric(project, {**ANSWERS, "clear_yes": "- \n;"})


def test_a_changed_step_must_be_finished_again(project):
    through_search(project)
    save_rubric(project, ANSWERS)
    finish(project)
    save_search(project, SEARCH)
    assert next_step(read_state(project)) == "finish"


def test_an_old_web_setup_state_starts_over_and_reset_forgets(project):
    with open(os.path.join(project, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "stage": "rubric", "phase": "questions", "revision": 9}, f)
    assert next_step(read_state(project)) == "profile"
    through_search(project)
    reset(project)
    assert next_step(read_state(project)) == "profile"


def test_the_language_of_each_keyword_is_kept():
    card = validate_stage0_draft({"keywords": ["prototype", "prototypage"], "languages": ["en", "fr"],
                                  "keyword_language": {"prototype": "en", "prototypage": "FR", "gone": "fr"}})
    assert card["keyword_language"] == {"prototype": "en", "prototypage": "fr"}
    assert "keyword_language" not in validate_stage0_draft({"keywords": ["prototype"], "languages": ["en"]})


def run_cli(project, *args):
    return subprocess.run([sys.executable, "-m", "ftja.onboarding", project, *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=os.path.dirname(os.path.dirname(__file__)))


def test_the_command_line_reads_json_from_a_file_and_reports_errors(project, tmp_path_factory):
    work = tmp_path_factory.mktemp("work")
    summary = work / "summary.md"
    summary.write_text("Builder.", encoding="utf-8")
    first = run_cli(project, "search", "--json-file", str(work / "none.json"))
    assert first.returncode == 1 and "error" in json.loads(first.stdout)
    ok = run_cli(project, "profile", "--source", "profile/이력서.pdf", "--summary-file", str(summary))
    assert ok.returncode == 0 and json.loads(ok.stdout)["next"] == "search"
    (work / "search.json").write_text(json.dumps(SEARCH, ensure_ascii=False), encoding="utf-8-sig")  # as PowerShell writes it
    assert json.loads(run_cli(project, "search", "--json-file", str(work / "search.json")).stdout)["next"] == "rubric"
    (work / "answers.json").write_text(json.dumps(ANSWERS), encoding="utf-8")
    out = json.loads(run_cli(project, "rubric", "--json-file", str(work / "answers.json")).stdout)
    assert out["next"] == "finish" and len(out["rubric"]["criteria"]) == 5
    done = json.loads(run_cli(project, "finish").stdout)
    assert done["next"] == "done" and "criteria.json" in done["files"]
