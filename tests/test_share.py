import json
import urllib.error
from datetime import datetime, timedelta, timezone

from ftja import ats, share


def _folder(tmp_path, local):
    (tmp_path / ats.LOCAL_FILE).write_text(json.dumps(local))
    shipped = tmp_path / "ats_boards.json"
    shipped.write_text(json.dumps({"boards": {"known": {"name": "Known", "vendor": "ashby", "slug": "known"}}}))
    return str(tmp_path), str(shipped)


LOCAL = {
    "acme": {"name": "Acme", "vendor": "ashby", "slug": "acme", "confirmed": True},
    "guess": {"name": "Guess", "vendor": "greenhouse", "slug": "guess"},              # never confirmed by a posting
    "known": {"name": "Known", "vendor": "ashby", "slug": "known", "confirmed": True},  # already shipped
    "none": {"name": "None", "checked_at": "2026-11-01"},                              # no board found
    "bad": {"name": "Bad", "vendor": "teamtailor", "slug": "evil.com", "confirmed": True},
}


def _calls(monkeypatch, fail=False):
    calls = []

    def call(install_id, boards):
        if fail:
            raise urllib.error.URLError("offline")
        calls.append((install_id, boards))
        return len(boards)

    monkeypatch.setattr(share, "_call", call)
    return calls


def test_only_confirmed_unshipped_boards_wait(tmp_path, monkeypatch):
    folder, shipped = _folder(tmp_path, LOCAL)
    monkeypatch.setattr(ats, "SHARED_PATH", shipped)
    assert share.unsent(folder, shipped) == [{"key": "acme", "name": "Acme", "vendor": "ashby", "slug": "acme"}]


def test_nothing_is_sent_without_a_yes(tmp_path, monkeypatch):
    folder, shipped = _folder(tmp_path, LOCAL)
    monkeypatch.setattr(ats, "SHARED_PATH", shipped)
    calls = _calls(monkeypatch)
    assert share.send(folder) == {"sent": 0, "skipped": "not shared"}
    share.choose(folder, "never")
    assert share.send(folder)["sent"] == 0
    assert calls == []


def test_yes_registers_then_sends_each_board_once(tmp_path, monkeypatch):
    folder, shipped = _folder(tmp_path, LOCAL)
    monkeypatch.setattr(ats, "SHARED_PATH", shipped)
    calls = _calls(monkeypatch)
    out = share.choose(folder, "yes")
    install = share.settings(folder)["install_id"]
    assert out["sent"] == 1
    assert calls == [(install, []), (install, [{"key": "acme", "name": "Acme", "vendor": "ashby", "slug": "acme"}])]
    assert share.send(folder) == {"sent": 0}  # already sent
    assert share.status(folder)["sent"] == 1


def test_a_failed_send_keeps_the_boards_for_next_time(tmp_path, monkeypatch):
    folder, shipped = _folder(tmp_path, LOCAL)
    monkeypatch.setattr(ats, "SHARED_PATH", shipped)
    _calls(monkeypatch, fail=True)
    assert "error" in share.choose(folder, "yes")
    out = share.send(folder)
    assert out["sent"] == 0 and "error" in out
    calls = _calls(monkeypatch)
    assert share.send(folder) == {"sent": 1} and len(calls) == 1


def test_not_now_asks_again_after_a_while(tmp_path, monkeypatch):
    folder, shipped = _folder(tmp_path, LOCAL)
    monkeypatch.setattr(ats, "SHARED_PATH", shipped)
    assert share.status(folder)["ask"]
    share.choose(folder, "no")
    assert not share.status(folder)["ask"]
    s = share.settings(folder)
    s["decided_at"] = (datetime.now(timezone.utc) - timedelta(days=share.ASK_AGAIN_DAYS + 1)).isoformat()
    share._save(folder, s)
    assert share.status(folder)["ask"]
    share.choose(folder, "never")
    assert not share.status(folder)["ask"]
