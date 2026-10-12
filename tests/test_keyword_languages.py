"""Keywords by language: gaps when the user starts reading a language, the
request the page makes for them, and keywords of a language no longer read."""
import json
import os

import pytest

from ftja import keywords, live, remote, run_bridge
from ftja.filter_stage0 import filter_stage0
from ftja.onboarding import validate_stage0_draft

JOBS = [
    {"job_url": "https://x/en", "title": "Product Builder", "company": "Alpha", "location": "Paris",
     "description": "Alpha makes developer tools for small teams. You will prototype features yourself every single day."},
    {"job_url": "https://x/fr", "title": "Chef de produit", "company": "Beta", "location": "Lyon",
     "description": "Beta édite un logiciel pour les assureurs. Vous réalisez vous-même le prototypage des nouvelles fonctionnalités chaque semaine avec les équipes."},
]


def criteria(tmp_path, languages, tier1, marked=None):
    block = {"tier1": tier1, **({"tier1_language": marked} if marked else {})}
    (tmp_path / "criteria.json").write_text(json.dumps({"languages": languages, "keywords": block}))
    return str(tmp_path)


def test_a_keyword_of_a_language_no_longer_read_is_switched_off_not_deleted():
    marked = {"prototypage": "fr"}
    assert keywords.active(["prototype", "prototypage"], marked, ["en", "fr"]) == ["prototype", "prototypage"]
    assert keywords.active(["prototype", "prototypage"], marked, ["en"]) == ["prototype"]  # French was removed
    assert keywords.active(["prototype", "prototypage"], marked, []) == ["prototype", "prototypage"]  # any language is read
    assert keywords.active(["prototype", "prototypage"], None, ["en"]) == ["prototype", "prototypage"]  # nothing recorded: as before

    both = {"languages": ["en", "fr"], "keywords": {"tier1": ["prototype", "prototypage"], "tier1_language": marked}}
    assert [j["company"] for j in filter_stage0(JOBS, both)[0]] == ["Alpha", "Beta"]
    french_only_keyword = {"languages": ["en", "fr"], "keywords": {"tier1": ["prototypage"], "tier1_language": marked}}
    assert [j["company"] for j in filter_stage0(JOBS, french_only_keyword)[0]] == ["Beta"]

    cfg = remote.default_config()
    cfg["categories"][0]["keywords"].append("télétravail")
    cfg["keyword_language"] = {"télétravail": "fr"}
    assert "télétravail" in remote.all_keywords(keywords.active_remote_config(cfg, ["en", "fr"]))
    assert "télétravail" not in remote.all_keywords(keywords.active_remote_config(cfg, ["en"]))
    assert "télétravail" in remote.all_keywords(cfg)  # the setup itself still holds it


def test_a_language_with_no_keywords_is_a_gap_in_each_list(tmp_path):
    root = criteria(tmp_path, ["en", "fr"], ["prototype", "MVP"])
    found = keywords.gaps(root)
    assert found["stage1"]["missing"] == ["fr"] and found["remote"]["missing"] == []  # no Remote check setup: nothing to fill
    remote.save_config(root, remote.default_config())
    assert keywords.gaps(root)["remote"]["missing"] == ["fr"]
    assert keywords.gaps(criteria(tmp_path, ["en"], ["prototype"]))["stage1"]["missing"] == []
    assert keywords.gaps(criteria(tmp_path, ["en", "fr"], []))["stage1"]["missing"] == []  # no keywords at all is not a language gap


def test_the_page_asks_the_agent_proposes_and_the_user_confirms(tmp_path):
    root = criteria(tmp_path, ["en", "fr"], ["prototype"])
    remote.save_config(root, remote.default_config())
    assert keywords.read_request(root) == {"status": "none", "items": []}
    with pytest.raises(ValueError):
        keywords.write_request(root, [{"target": "somewhere", "language": "fr"}])
    keywords.write_request(root, [{"target": "stage1", "language": "FR"}])
    assert keywords.pending(root) and keywords.read_request(root)["items"] == [{"target": "stage1", "language": "fr"}]
    assert keywords.begin(root)["status"] == "generating"
    with pytest.raises(ValueError):
        keywords.propose(root, [{"target": "stage1", "language": "fr", "keywords": []}])
    keywords.propose(root, [{"target": "stage1", "language": "fr", "keywords": ["prototypage", "maquette", "maquette"]}])
    request = keywords.read_request(root)
    assert request["status"] == "drafted" and request["items"][0]["keywords"] == ["prototypage", "maquette"]
    saved = json.load(open(os.path.join(root, "criteria.json")))
    assert saved["keywords"]["tier1"] == ["prototype"]  # nothing is added until the user confirms

    # the user drops one on the page and confirms
    keywords.add_keywords(root, [{"target": "stage1", "language": "fr", "keywords": ["prototypage"]}])
    saved = json.load(open(os.path.join(root, "criteria.json")))
    assert saved["keywords"] == {"tier1": ["prototype", "prototypage"], "tier1_language": {"prototypage": "fr"}}
    assert keywords.read_request(root)["status"] == "none" and keywords.gaps(root)["stage1"]["missing"] == []

    # typed in the chat instead (no request from the page): the agent covers every gap that is left
    taken = keywords.begin(root)
    assert taken["items"] == [{"target": "remote", "language": "fr"}]
    keywords.propose(root, [{"target": "remote", "language": "fr", "keywords": ["télétravail", "fuseau horaire"],
                             "category_of": {"fuseau horaire": "time_zone", "unknown": "x"}}])
    keywords.add_keywords(root, keywords.read_request(root)["items"])
    cfg = remote.load_config(os.path.join(root, remote.CONFIG_FILE))
    by_id = {c["id"]: c["keywords"] for c in cfg["categories"]}
    assert "fuseau horaire" in by_id["time_zone"] and "télétravail" in by_id["remote_level"]  # placed, or in the first category
    assert cfg["keyword_language"] == {"télétravail": "fr", "fuseau horaire": "fr"} and cfg["languages"] == ["en", "fr"]
    assert keywords.begin(root) == {"status": "none", "items": [], "nothing_missing": True}


def test_keywords_go_into_the_remote_draft_while_the_user_is_going_through_one(tmp_path):
    root = criteria(tmp_path, ["en", "fr"], ["prototype"])
    remote.save_draft(root, remote.default_config())
    assert keywords.gaps(root)["remote"]["missing"] == ["fr"]
    keywords.add_keywords(root, [{"target": "remote", "language": "fr", "keywords": ["télétravail"]}])
    assert "télétravail" in remote.all_keywords(remote.load_draft(root))
    assert remote.load_config(os.path.join(root, remote.CONFIG_FILE)) is None  # still only a draft


def test_one_request_at_a_time_and_the_button_reaches_a_waiting_session(tmp_path):
    import threading
    import time
    root = criteria(tmp_path, ["en", "fr"], ["prototype"])
    remote.write_request(root, {})  # the Remote check's setup is being asked for
    with pytest.raises(ValueError):
        keywords.write_request(root, [{"target": "stage1", "language": "fr"}])
    remote.discard_draft(root)
    assert run_bridge.request_keywords(root) == {"listening": False}

    result = {}
    waiter = threading.Thread(target=lambda: result.update(run_bridge.wait_for_continue(root, "run", timeout_seconds=10)))
    waiter.start()
    for _ in range(50):
        if run_bridge.listening(root)["listening"]:
            break
        time.sleep(0.05)
    assert run_bridge.request_keywords(root) == {"listening": True}
    waiter.join(timeout=5)
    assert result == {"status": "keywords", "run_id": "run"}


def test_onboarding_keeps_the_language_of_each_proposed_keyword():
    card = validate_stage0_draft({"keywords": ["prototype", "prototypage"], "languages": ["en", "fr"],
                                  "keyword_language": {"prototype": "en", "prototypage": "FR", "gone": "fr"}})
    assert card["keyword_language"] == {"prototype": "en", "prototypage": "fr"}
    assert "keyword_language" not in validate_stage0_draft({"keywords": ["prototype"], "languages": ["en"]})  # as before


def test_a_run_judges_with_the_keywords_of_the_languages_read_at_its_start(tmp_path, monkeypatch):
    import shutil
    from ftja.lock import acquire
    from ftja.verdict import save_criteria
    repo = os.path.dirname(os.path.dirname(__file__))
    for name in ("stage1_prompt.md", "stage2_prompt.md", "remote_prompt.md"):
        shutil.copy(os.path.join(repo, name), tmp_path / name)
    (tmp_path / "criteria.json").write_text(json.dumps({
        "search_terms": ["x"], "location": "France", "languages": ["en"], "keywords": {"tier1": ["prototype"]},
        "stages": {"remote": True, "stage1": False, "stage2": False}}))
    save_criteria(str(tmp_path), [{"id": "a", "kind": "pass", "label": "A", "text": "A."}])
    cfg = remote.default_config()
    cfg["categories"][0]["keywords"].append("télétravail")
    cfg["keyword_language"] = {"télétravail": "fr"}
    remote.save_config(str(tmp_path), cfg)
    acquire(str(tmp_path / ".ftja.lock"))
    run_dir = live.start(str(tmp_path))["run_dir"]
    assert "télétravail" not in remote.all_keywords(live._remote_config(run_dir))  # French is not read: switched off for this run


def test_onboarding_search_settings_take_several_locations_and_keep_keyword_languages(tmp_path):
    from ftja.onboarding import apply_action, read_state, write_state
    root = str(tmp_path)
    write_state(root, stage="profile", phase="title_proposal", allowed_actions=["set_profile_draft", "confirm_profile"],
                profile={"summary_confirmed": True, "criteria": {}}, cards={})
    stage0 = {"keywords": ["prototype", "prototypage"], "languages": ["en", "fr"], "exclude_keywords": [],
              "keyword_language": {"prototypage": "fr"}}
    apply_action(root, {"type": "set_profile_draft", "titles": ["Product Builder"], "location": "France", "stage0": stage0})
    assert "locations" not in read_state(root)["profile"]["criteria"]  # one location, as before
    after = apply_action(root, {"type": "confirm_profile", "titles": ["Product Builder"],
                                "locations": ["France", "Germany", " "], "location": "ignored", "title_must_match": True})
    criteria = after["profile"]["criteria"]
    assert criteria["locations"] == ["France", "Germany"] and criteria["location"] == "France, Germany"
    assert criteria["title_must_match"] is True
    assert criteria["keywords"] == {"tier1": ["prototype", "prototypage"], "tier1_language": {"prototypage": "fr"}}
    assert [s["location"] for s in live.searches(criteria)] == ["France", "Germany"]  # a run searches each
