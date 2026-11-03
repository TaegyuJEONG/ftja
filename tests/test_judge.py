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
    def provider(prompt, model, key, max_tokens, base):
        if seen is not None:
            seen.append((prompt, model, key))
        r = replies(prompt)
        if isinstance(r, Exception):
            raise r
        return r, 100, 20
    for name in judge.PROVIDERS:
        monkeypatch.setitem(judge.PROVIDERS, name, {**judge.PROVIDERS[name], "call": provider})
    monkeypatch.setattr(judge.time, "sleep", lambda s: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for var in ("FTJA_JUDGE", "FTJA_PROVIDER", "FTJA_API_BASE", "DEEPSEEK_API_KEY", *judge.MODEL_VARS.values()):
        monkeypatch.delenv(var, raising=False)


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
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz\nFTJA_STAGE1_MODEL=m1  # cheap\n")
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
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz\nFTJA_STAGE1_MODEL=m1  # cheap\n")
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
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-abcdefghijklmnopqrstuvwxyz\nFTJA_STAGE1_MODEL=m1  # cheap\n")
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
    assert judge.anthropic("hi", "claude-haiku-5-5", "k", 50, "https://api.anthropic.com") == ("{}", 7, 3)
    assert sent["url"].endswith("/v1/messages") and sent["headers"]["X-api-key"] == "k"
    assert sent["body"]["model"] == "claude-haiku-5-5" and sent["body"]["max_tokens"] == 50


def test_openai_compatible_request_shape(monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}).encode()

    def urlopen(req, timeout):
        sent["url"], sent["headers"], sent["body"] = req.full_url, dict(req.headers), json.loads(req.data)
        return Resp()
    monkeypatch.setattr(judge.urllib.request, "urlopen", urlopen)
    assert judge.openai_compatible("hi", "deepseek-chat", "k", 50, "https://api.deepseek.com/") == ("{}", 9, 4)
    assert sent["url"] == "https://api.deepseek.com/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer k" and sent["body"]["model"] == "deepseek-chat"


def test_choosing_a_supplier_uses_its_key_models_and_address(tmp_path, monkeypatch):
    seen = []
    _fake(monkeypatch, lambda p: '{"verdict": "fail"}', seen)
    d = str(tmp_path)
    judge.choose(d, mode="api", provider="anthropic", models={"s1": "my-old-model"})
    st = judge.choose(d, provider="deepseek", key="sk-0123456789abcdef0123")
    assert st["key_var"] == "DEEPSEEK_API_KEY" and not st["ready"]
    assert st["models"] == {"rc": "", "s1": "", "s2": ""}  # no guessed names; the old supplier's model dropped
    assert "deepseek.com" in st["pricing_page"]
    st = judge.choose(d, models={"s1": "deepseek-flash", "s2": "deepseek-pro"})
    assert st["ready"] and st["models"]["rc"] == "deepseek-flash"
    assert "0123456789abcdef" not in json.dumps(st)
    run_dir = _run(tmp_path, prompts=("one",))
    out = judge.run(run_dir, "s1", d)
    assert out["provider"] == "deepseek" and seen[0][1:] == ("deepseek-flash", "sk-0123456789abcdef0123")


def test_custom_supplier_needs_address_and_models(tmp_path, monkeypatch):
    _fake(monkeypatch, lambda p: "{}")
    d = str(tmp_path)
    st = judge.choose(d, mode="api", provider="custom", key="local-key-0123456789")
    assert not st["ready"]
    with pytest.raises(SystemExit, match="Pipeline tab"):
        judge.run(_run(tmp_path), "s1", d)
    with pytest.raises(ValueError):
        judge.choose(d, base="file:///etc/passwd")
    st = judge.choose(d, base="http://localhost:11434/v1", models={"s1": "qwen3:8b", "s2": "qwen3:32b"})
    assert st["ready"] and st["models"]["rc"] == "qwen3:8b"


def test_example_env_with_empty_commented_values_reads_as_empty(tmp_path):
    (tmp_path / ".env").write_text("FTJA_STAGE1_MODEL=          # cheap\nFTJA_STAGE2_MODEL=big # strong\n")
    env = judge.read_env(str(tmp_path))
    assert env["FTJA_STAGE1_MODEL"] == "" and env["FTJA_STAGE2_MODEL"] == "big"


def test_list_models_asks_the_supplier(tmp_path, monkeypatch):
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({"data": [{"id": "deepseek-pro"}, {"id": "deepseek-flash"}]}).encode()

    def urlopen(req, timeout):
        sent["url"], sent["auth"] = req.full_url, req.headers.get("Authorization")
        return Resp()
    monkeypatch.setattr(judge.urllib.request, "urlopen", urlopen)
    for var in ("FTJA_PROVIDER", "FTJA_API_BASE", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".env").write_text("FTJA_PROVIDER=deepseek\nDEEPSEEK_API_KEY=sk-0123456789abcdef\n")
    assert judge.list_models(str(tmp_path)) == ["deepseek-flash", "deepseek-pro"]
    assert sent["url"] == "https://api.deepseek.com/models" and sent["auth"] == "Bearer sk-0123456789abcdef"
