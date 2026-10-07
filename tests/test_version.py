import json
import time

from ftja import version


def setup(tmp_path, local="0.2.0"):
    (tmp_path / "VERSION").write_text(local + "\n")
    return str(tmp_path)


def test_newer_published_version_is_an_update_and_is_cached(tmp_path, monkeypatch):
    root = setup(tmp_path)
    calls = []
    monkeypatch.delenv("FTJA_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(version, "_fetch_latest", lambda r: calls.append(r) or "0.10.0")
    assert version.check(root, root) == {"current": "0.2.0", "latest": "0.10.0", "update_available": True}
    assert version.check(root, root)["update_available"] and len(calls) == 1  # second answer came from the cache
    assert version.check(root, root, force=True) and len(calls) == 2


def test_same_or_older_published_version_is_not_an_update(tmp_path, monkeypatch):
    root = setup(tmp_path, "0.3.0")
    monkeypatch.delenv("FTJA_NO_UPDATE_CHECK", raising=False)
    for published in ("0.3.0", "0.2.9"):
        monkeypatch.setattr(version, "_fetch_latest", lambda r: published)
        assert version.check(root, root, force=True)["update_available"] is False


def test_offline_or_disabled_never_reports_an_update(tmp_path, monkeypatch):
    root = setup(tmp_path)
    monkeypatch.delenv("FTJA_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(version, "_fetch_latest", lambda r: None)
    assert version.check(root, root) == {"current": "0.2.0", "latest": None, "update_available": False}

    # offline now, but an old answer is on disk: use it rather than nothing
    (tmp_path / version.CACHE_FILE).write_text(json.dumps({"checked_at": time.time() - 10 ** 6, "latest": "0.5.0"}))
    assert version.check(root, root)["latest"] == "0.5.0"

    monkeypatch.setenv("FTJA_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(version, "_fetch_latest", lambda r: 1 / 0)
    assert version.check(root, root)["latest"] is None


def test_changes_since_returns_only_newer_sections(tmp_path):
    root = setup(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\nintro\n\n## 0.3.0 — today\n\nthree\n\n## 0.2.0\n\ntwo\n\n## 0.1.0\n\none\n")
    assert version.changes_since("0.1.0", root) == "## 0.3.0 — today\n\nthree\n\n## 0.2.0\n\ntwo"
    assert version.changes_since("0.3.0", root) == ""


def test_repository_changelog_covers_the_repository_version():
    assert f"## {version.local_version()}" in open(version.REPO_ROOT + "/CHANGELOG.md").read()
