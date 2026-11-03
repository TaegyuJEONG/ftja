import json
import os
import stat
import urllib.error

import pytest

from ftja import judge, live


def _run(tmp_path, stage="s1", prompts=("Judge job zero", "Judge job one")):
    run_dir = tmp_path / ".ftja-run" / "r1"
    (run_dir / stage).mkdir(parents=True)
    for n, p in enumerate(prompts):
        (run_dir / stage / f"{n}.txt").write_text(p)
    return str(run_dir)


def _fake(monkeypatch, replies, seen=None):
    def provider(prompt, model, key, max_tokens):
        if seen is not None:
            seen.append((prompt, model, key))
        r = replies(prompt)
        if isinstance(r, Exception):
            raise r
        return r, 100, 20
    monkeypatch.setitem(judge.PROVIDERS, "anthropic", (provider, "ANTHROPIC_API_KEY"))
    monkeypatch.setattr(judge.time, "sleep", lambda s: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FTJA_JUDGE", raising=False)


def test_env_is_gitignored():
    root = os.path.dirname(os.path.dirname(__file__))
    with open(os.path.join(root, ".gitignore")) as f:
        assert ".env" in f.read().split()
    with open(os.path.join(root, ".env.example")) as f:
        assert "ANTHROPIC_API_KEY=\n" in f.read()


def test_status_without_env_is_agent_and_hides_key(tmp_path, monkeypatch):
    _fake(monkeypatch, lambda p: "{}")
    assert judge.status(str(tmp_path))["mode"] == "agent"
    assert judge.status(str(tmp_path))["key_set"] is False
    (tmp_path / ".env").write_text("FTJA_JUDGE=api\nANTHROPIC_API_KEY=sk-ant-secret-123456789012345\n")
    st = judge.status(str(tmp_path))
    assert st["mode"] == "api" and st["key_set"] is True
    assert "sk-ant-secret" not in json.dumps(st)


def test_choose_writes_env_privately_and_keeps_other_lines(tmp_path, monkeypatch):
    _fake(monkeypatch, lambda p: "{}")
    (tmp_path / ".env").write_text("# mine\nOTHER=1\n")
    st = judge.choose(str(tmp_path), mode="api", key="sk-ant-abcdefghijklmnopqrstuvwxyz")
    assert st["key_set"] and st["mode"] == "api" and "abcdefg" not in json.dumps(st)
    text = (tmp_path / ".env").read_text()
    assert "# mine" in text and "OTHER=1" in text and "ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz" in text
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    with pytest.raises(ValueError):
        judge.choose(str(tmp_path), key="not a key\nFTJA_JUDGE=agent")
    with pytest.raises(ValueError):
        judge.choose(str(tmp_path), mode="robot")


def test_run_writes_answers_and_records_real_tokens(tmp_path, monkeypatch):
    _fake(monkeypatch, lambda p: '```json\n{"verdict": "fail", "reason": "x", "evidence_sids": [], "blocks": []}\n```')
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz\n")
    run_dir = _run(tmp_path)
    out = judge.run(run_dir, "s1", str(tmp_path))
    assert out["judged"] == 2 and out["failed"] == {} and out["tokens"] == 240
    assert json.loads(open(os.path.join(run_dir, "s1", "1.out.json")).read())["verdict"] == "fail"
    assert live.tokens_total(run_dir) == 240
    # answered jobs are not judged again
    assert judge.run(run_dir, "s1", str(tmp_path))["judged"] == 0


def test_run_keeps_going_when_one_job_fails(tmp_path, monkeypatch):
    def replies(p):
        return urllib.error.HTTPError("u", 400, "bad", {}, None) if "zero" in p else "not json"
    _fake(monkeypatch, replies)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz\n")
    run_dir = _run(tmp_path)
    out = judge.run(run_dir, "s1", str(tmp_path))
    assert set(out["failed"]) == {0, 1} and out["judged"] == 0
    assert not os.path.exists(os.path.join(run_dir, "s1", "0.out.json"))


def test_retries_overloaded(tmp_path, monkeypatch):
    calls = []
    def replies(p):
        calls.append(1)
        return urllib.error.HTTPError("u", 529, "overloaded", {}, None) if len(calls) < 3 else '{"verdict": "pass"}'
    _fake(monkeypatch, replies)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz\n")
    run_dir = _run(tmp_path, prompts=("only",))
    assert judge.run(run_dir, "s1", str(tmp_path))["judged"] == 1 and len(calls) == 3


def test_stage2_inlines_files_the_prompt_asks_to_read(tmp_path, monkeypatch):
    seen = []
    _fake(monkeypatch, lambda p: '{"criteria": []}', seen)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz\nFTJA_STAGE2_MODEL=claude-test\n")
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "summary.md").write_text("CANDIDATE SUMMARY")
    (tmp_path / "rubric.md").write_text("THE RUBRIC")
    run_dir = _run(tmp_path, "s2", ("1. Read `rubric.md` now.\n2. Read `profile/summary.md` too.",))
    judge.run(run_dir, "s2", str(tmp_path))
    prompt, model, _ = seen[0]
    assert "THE RUBRIC" in prompt and "CANDIDATE SUMMARY" in prompt and model == "claude-test"


def test_run_without_key_stops_with_where_to_get_one(tmp_path, monkeypatch):
    _fake(monkeypatch, lambda p: "{}")
    run_dir = _run(tmp_path)
    with pytest.raises(SystemExit, match="console.anthropic.com"):
        judge.run(run_dir, "s1", str(tmp_path))


def test_anthropic_request_shape(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 7, "output_tokens": 3}}).encode()

    def urlopen(req, timeout):
        sent["url"], sent["headers"], sent["body"] = req.full_url, dict(req.headers), json.loads(req.data)
        return Resp()
    monkeypatch.setattr(judge.urllib.request, "urlopen", urlopen)
    assert judge.anthropic("hi", "claude-haiku-5-5", "k", 50) == ("{}", 7, 3)
    assert sent["url"].endswith("/v1/messages") and sent["headers"]["X-api-key"] == "k"
    assert sent["body"]["model"] == "claude-haiku-5-5" and sent["body"]["max_tokens"] == 50
