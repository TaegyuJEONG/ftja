import json

from ftja import ats

GREENHOUSE = {"jobs": [{"title": "Senior Product Manager", "content": "&lt;p&gt;Work from anywhere in Europe.&lt;/p&gt;",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"},
                       {"title": "Designer", "content": "<p>Office in Paris.</p>", "absolute_url": "u2"}]}


def _boards(monkeypatch, answers):
    """The board APIs stand in: `answers` maps a requested URL to what it returns (anything else: no answer)."""
    asked = []

    def fetch(url, as_json=True):
        asked.append(url)
        return answers.get(url)

    monkeypatch.setattr(ats, "_fetch", fetch)
    return asked


def test_names_and_urls():
    assert ats.company_key("Acme GmbH") == ats.company_key("ACME") == "acme"
    assert ats.slugs("Acme Labs GmbH")[:2] == ["acme-labs-gmbh", "acmelabsgmbh"]
    assert ats.board_from_url("https://job-boards.greenhouse.io/openai/jobs/123") == ("greenhouse", "openai")
    assert ats.board_from_url("https://jobs.eu.lever.co/supercharge") == ("lever_eu", "supercharge")
    assert ats.board_from_url("https://acme.teamtailor.com/jobs") == ("teamtailor", "acme.teamtailor.com")
    assert ats.board_from_url("https://boards.greenhouse.io/embed/job_board") is None
    assert ats.board_from_url("https://acme.com/careers") is None
    assert not ats.valid_board("teamtailor", "evil.com/acme")  # a board name never leaves the vendor's host


def test_reads_a_greenhouse_board(monkeypatch):
    _boards(monkeypatch, {ats.LIST_URL["greenhouse"].format(s="acme"): GREENHOUSE})
    posts = ats.postings("greenhouse", "acme")
    assert [p["title"] for p in posts] == ["Senior Product Manager", "Designer"]
    assert posts[0]["description"] == "Work from anywhere in Europe."  # escaped HTML read as text


def test_smartrecruiters_text_is_one_more_request(monkeypatch):
    ref = "https://api.smartrecruiters.com/v1/companies/acme/postings/9"
    _boards(monkeypatch, {ats.LIST_URL["smartrecruiters"].format(s="acme"): {"content": [{"name": "PM", "ref": ref, "id": "9"}]},
                          ref: {"jobAd": {"sections": {"a": {"text": "<b>Remote</b> in Spain"}}}}})
    post = ats.postings("smartrecruiters", "acme")[0]
    assert ats.text_of(post) == "Remote\n in Spain"


def test_same_title_is_no_answer_when_postings_differ():
    board = [{"title": "Product Manager", "description": "Berlin"}, {"title": "Product manager", "description": "Remote"}]
    assert ats.same_title(board, "Product Manager") is None
    assert ats.same_title(board[:1], "product  manager")["description"] == "Berlin"


def test_details_come_from_the_board_of_a_known_company(monkeypatch, tmp_path):
    shared = tmp_path / "ats_boards.json"
    shared.write_text(json.dumps({"boards": {"acme": {"name": "Acme", "vendor": "greenhouse", "slug": "acme"}}}))
    asked = _boards(monkeypatch, {ats.LIST_URL["greenhouse"].format(s="acme"): GREENHOUSE})
    boards = ats.Boards(str(tmp_path), str(shared))
    got = boards.details({"company": "Acme Inc", "title": "Senior Product Manager"})
    assert got == {"description": "Work from anywhere in Europe.", "_source": "ats:greenhouse/acme",
                   "job_url_direct": "https://boards.greenhouse.io/acme/jobs/1"}
    assert boards.details({"company": "Acme", "title": "Engineer"}) == {}  # not on the board: LinkedIn instead
    assert boards.details({"company": "Other", "title": "Designer"}) == {}
    assert len(asked) == 1  # one request for the board, however many postings are looked up
    assert boards.stats["from_ats"] == 1


def test_discover_keeps_hits_and_misses_locally(monkeypatch, tmp_path):
    asked = _boards(monkeypatch, {ats.LIST_URL["ashby"].format(s="ramp"): {"jobs": [{"title": "PM"}]}})
    boards = ats.Boards(str(tmp_path), str(tmp_path / "none.json"))
    boards.discover(["Ramp", "Nowhere Ltd", "Ramp"])
    local = json.loads((tmp_path / ats.LOCAL_FILE).read_text())
    assert local["ramp"]["vendor"] == "ashby" and local["ramp"]["slug"] == "ramp"
    assert "vendor" not in local["nowhere"]
    before = len(asked)
    ats.Boards(str(tmp_path), str(tmp_path / "none.json")).discover(["Ramp", "Nowhere Ltd"])
    assert len(asked) == before  # both answered already: nothing asked again


def test_still_open(monkeypatch, tmp_path):
    _boards(monkeypatch, {ats.LIST_URL["greenhouse"].format(s="acme"): GREENHOUSE})
    boards = ats.Boards(str(tmp_path), str(tmp_path / "none.json"))
    assert boards.still_open("ats:greenhouse/acme", "Designer") is True
    assert boards.still_open("ats:greenhouse/acme", "Gone Role") is False
    assert boards.still_open("ats:lever/unknown", "Designer") is None  # no answer tells nothing


def test_export_leaves_out_what_is_shared_and_what_was_missed(monkeypatch, tmp_path):
    shared = tmp_path / "ats_boards.json"
    shared.write_text(json.dumps({"boards": {"acme": {"name": "Acme", "vendor": "greenhouse", "slug": "acme"}}}))
    (tmp_path / ats.LOCAL_FILE).write_text(json.dumps({
        "acme": {"name": "Acme", "vendor": "greenhouse", "slug": "acme", "checked_at": "x"},
        "ramp": {"name": "Ramp", "vendor": "ashby", "slug": "ramp", "checked_at": "x"},
        "nowhere": {"name": "Nowhere", "checked_at": "x"}}))
    out = ats.export(str(tmp_path), str(shared))
    assert out["boards"] == 1
    assert json.loads((tmp_path / ats.EXPORT_FILE).read_text()) == {
        "boards": {"ramp": {"name": "Ramp", "vendor": "ashby", "slug": "ramp"}}}


def test_verify_and_merge(monkeypatch, tmp_path):
    _boards(monkeypatch, {ats.LIST_URL["ashby"].format(s="ramp"): {"jobs": [{"title": "PM"}]}})
    shared = tmp_path / "ats_boards.json"
    shared.write_text(json.dumps({"boards": {}}))
    sent = tmp_path / "sent.json"
    sent.write_text(json.dumps({"boards": {"ramp": {"name": "Ramp", "vendor": "ashby", "slug": "ramp"},
                                           "dead": {"name": "Dead", "vendor": "ashby", "slug": "dead"},
                                           "wrong": {"name": "Ramp", "vendor": "ashby", "slug": "ramp"}}}))
    result = ats.merge(str(shared), str(sent), "test")
    assert result["added"] == 1 and set(result["rejected"]) == {"dead", "wrong"}
    assert json.loads(shared.read_text())["boards"]["ramp"]["source"] == "test"


def test_shipped_list_is_well_formed():
    boards = ats.shared_boards()
    assert boards, "ats_boards.json ships with boards"
    for key, entry in boards.items():
        assert key == ats.company_key(entry["name"]), key
        assert ats.valid_board(entry["vendor"], entry["slug"]), key
        assert entry.get("source"), key


def test_close_gone_marks_closed_postings_expired(monkeypatch, tmp_path):
    from ftja.state import connect, mark_seen

    _boards(monkeypatch, {ats.LIST_URL["greenhouse"].format(s="acme"): GREENHOUSE})
    jobs = {url: {"source": "ats:greenhouse/acme", "title": title} for url, title in
            [("u-open", "Designer"), ("u-gone", "Old Role"), ("u-decided", "Old Role 2"), ("u-failed", "Old Role 3")]}
    (tmp_path / ats.JOBS_FILE).write_text(json.dumps(jobs))
    db = str(tmp_path / "seen.db")
    with connect(db) as conn:
        for url in ("u-open", "u-gone", "u-decided"):
            mark_seen(conn, url, "passed", 2, "pass")
        mark_seen(conn, "u-failed", "stage2_fail", 2, "fail")
        conn.execute("UPDATE seen_jobs SET user_action='applied' WHERE job_url='u-decided'")
        conn.commit()
    assert ats.close_gone(str(tmp_path), db) == {"checked": 2, "closed": 1}
    with connect(db) as conn:
        actions = dict(conn.execute("SELECT job_url, user_action FROM seen_jobs").fetchall())
    assert actions == {"u-open": None, "u-gone": "expired", "u-decided": "applied", "u-failed": None}
    left = json.loads((tmp_path / ats.JOBS_FILE).read_text())
    assert set(left) == {"u-open", "u-failed"}  # the closed and the decided are never checked again


def test_titles_match_without_their_gender_note():
    assert ats.norm("Senior Product Manager (m/w/d)") == ats.norm("Senior Product Manager") == ats.norm("senior product manager (M/F/D)")
    assert ats.norm("Product Owner (all genders)") == "product owner"
