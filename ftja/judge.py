"""Judge a run's prompts with the user's own model API key instead of the
coding agent's subagents.

The agent path and this one read the same `<run_dir>/<stage>/<n>.txt`
prompts and write the same `<n>.out.json` answers, so `check-stage1`,
`prepare-stage2`, `results` and `finalize` do not know which one ran.

The key lives in `.env` in the FTJA folder (gitignored), next to the
choice of judge and supplier:

    FTJA_JUDGE=api            # "agent" (default) or "api"
    FTJA_PROVIDER=deepseek    # any key of PROVIDERS
    DEEPSEEK_API_KEY=sk-...

Anthropic is called natively; every other supplier through the
OpenAI-compatible chat-completions API, so adding one is one PROVIDERS
entry.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from ftja import live

ENV_FILE = ".env"
STAGES = ("rc", "s1", "s2")
MODEL_VARS = {"rc": "FTJA_REMOTE_MODEL", "s1": "FTJA_STAGE1_MODEL", "s2": "FTJA_STAGE2_MODEL"}
MAX_TOKENS = {"rc": 1024, "s1": 1024, "s2": 4096}
WORKERS = 8


# ---------- .env ----------

def read_env(project_dir: str = ".") -> dict:
    env = {}
    try:
        with open(os.path.join(project_dir, ENV_FILE), encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def write_env(project_dir: str, updates: dict):
    """Set keys in .env, keeping every other line as it was. A value of None
    removes the key. The file is made readable by its owner only."""
    path = os.path.join(project_dir, ENV_FILE)
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    left = dict(updates)
    out = []
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in left:
            v = left.pop(k)
            if v is not None:
                out.append(f"{k}={v}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in left.items() if v is not None]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(path, 0o600)


def _setting(env: dict, name: str, default: str = "") -> str:
    return os.environ.get(name) or env.get(name) or default


# ---------- providers ----------

def _post(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def anthropic(prompt: str, model: str, key: str, max_tokens: int, base: str) -> tuple[str, int, int]:
    out = _post(base.rstrip("/") + "/v1/messages",
                {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
                {"x-api-key": key, "anthropic-version": "2023-06-01"})
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    usage = out.get("usage") or {}
    return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def openai_compatible(prompt: str, model: str, key: str, max_tokens: int, base: str) -> tuple[str, int, int]:
    """The chat-completions API most suppliers speak (OpenAI, DeepSeek,
    Gemini, Mistral, OpenRouter, Groq, a local Ollama...). No max_tokens:
    reasoning models count their thinking against it and some reject it."""
    out = _post(base.rstrip("/") + "/chat/completions",
                {"model": model, "messages": [{"role": "user", "content": prompt}]},
                {"authorization": f"Bearer {key}"})
    text = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = out.get("usage") or {}
    return text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


# One entry per supplier: how to call it, where its key goes and where to
# get one, and a cheap model (Remote check, Stage 1) and a stronger one
# (Stage 2). Every model can be changed in .env or the viewer; "custom" is
# any OpenAI-compatible address, with models the user names.
PROVIDERS = {
    "anthropic": {"label": "Anthropic (Claude)", "call": anthropic, "base": "https://api.anthropic.com",
                  "key_var": "ANTHROPIC_API_KEY", "key_page": "https://console.anthropic.com/settings/keys",
                  "models": ("claude-haiku-5-5", "claude-sonnet-5-5")},
    "openai": {"label": "OpenAI", "call": openai_compatible, "base": "https://api.openai.com/v1",
               "key_var": "OPENAI_API_KEY", "key_page": "https://platform.openai.com/api-keys",
               "models": ("gpt-5-mini", "gpt-5")},
    "deepseek": {"label": "DeepSeek", "call": openai_compatible, "base": "https://api.deepseek.com",
                 "key_var": "DEEPSEEK_API_KEY", "key_page": "https://platform.deepseek.com/api_keys",
                 "models": ("deepseek-chat", "deepseek-reasoner")},
    "gemini": {"label": "Google Gemini", "call": openai_compatible,
               "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "key_var": "GEMINI_API_KEY", "key_page": "https://aistudio.google.com/apikey",
               "models": ("gemini-2.5-flash", "gemini-2.5-pro")},
    "mistral": {"label": "Mistral", "call": openai_compatible, "base": "https://api.mistral.ai/v1",
                "key_var": "MISTRAL_API_KEY", "key_page": "https://console.mistral.ai/api-keys",
                "models": ("mistral-small-latest", "mistral-large-latest")},
    "openrouter": {"label": "OpenRouter (any model)", "call": openai_compatible, "base": "https://openrouter.ai/api/v1",
                   "key_var": "OPENROUTER_API_KEY", "key_page": "https://openrouter.ai/settings/keys",
                   "models": ("", "")},
    "custom": {"label": "Other (OpenAI-compatible)", "call": openai_compatible, "base": "",
               "key_var": "FTJA_API_KEY", "key_page": "", "models": ("", "")},
}


def status(project_dir: str = ".") -> dict:
    """What the viewer and the run skill need: which judge and supplier are
    chosen, the models, and whether a key is set. Never the key itself."""
    env = read_env(project_dir)
    provider = _setting(env, "FTJA_PROVIDER", "anthropic")
    p = PROVIDERS.get(provider) or PROVIDERS["anthropic"]
    provider = provider if provider in PROVIDERS else "anthropic"
    cheap, strong = p["models"]
    defaults = {"rc": cheap, "s1": cheap, "s2": strong}
    models = {s: _setting(env, MODEL_VARS[s], defaults[s]) for s in STAGES}
    base = _setting(env, "FTJA_API_BASE", p["base"])
    mode = _setting(env, "FTJA_JUDGE", "agent")
    key_set = bool(_setting(env, p["key_var"]))
    return {"mode": mode if mode in ("agent", "api") else "agent", "provider": provider,
            "key_var": p["key_var"], "key_set": key_set, "key_page": p["key_page"],
            "base": base, "models": models,
            "ready": key_set and bool(base) and all(models.values()),
            "providers": [{"id": k, "label": v["label"], "key_page": v["key_page"],
                           "models": {"s1": v["models"][0], "s2": v["models"][1]}} for k, v in PROVIDERS.items()]}


def choose(project_dir: str, mode: str | None = None, key: str | None = None, provider: str | None = None,
           base: str | None = None, models: dict | None = None) -> dict:
    """The viewer's Pipeline tab: pick agent or API, a supplier, its models,
    and/or save a pasted key to .env. Returns status(), which never
    includes the key."""
    updates = {}
    if mode is not None:
        if mode not in ("agent", "api"):
            raise ValueError("mode must be agent or api")
        updates["FTJA_JUDGE"] = mode
    if provider is not None:
        if provider not in PROVIDERS:
            raise ValueError("unknown supplier")
        if provider != status(project_dir)["provider"]:
            # the old supplier's models and address do not fit the new one
            updates.update({"FTJA_PROVIDER": provider, "FTJA_API_BASE": None, **{v: None for v in MODEL_VARS.values()}})
    if base is not None:
        base = base.strip()
        if base and not re.fullmatch(r"https?://[^\s]{3,300}", base):
            raise ValueError("the address must start with http:// or https://")
        updates["FTJA_API_BASE"] = base or None
    for stage, model in (models or {}).items():
        if stage in ("s1", "s2"):
            model = str(model).strip()
            if model and not re.fullmatch(r"[\w./:@-]{1,200}", model):
                raise ValueError("that does not look like a model name")
            for s in (("rc", "s1") if stage == "s1" else ("s2",)):
                updates[MODEL_VARS[s]] = model or None
    if updates:
        write_env(project_dir, updates)
    if key is not None:
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:\-]{16,400}", key):
            raise ValueError("that does not look like an API key")
        write_env(project_dir, {status(project_dir)["key_var"]: key})
    return status(project_dir)


# ---------- prompts and answers ----------

_READ = re.compile(r"Read `([^`]+)`")


def inline_files(prompt: str, project_dir: str) -> str:
    """Stage 2 prompts tell a subagent to Read rubric.md and
    profile/summary.md itself. A model API has no file tool, so append
    each file named that way to the prompt."""
    parts = [prompt]
    for rel in dict.fromkeys(_READ.findall(prompt)):
        path = rel if os.path.isabs(rel) else os.path.join(project_dir, rel)
        try:
            with open(path, encoding="utf-8-sig") as f:
                parts.append(f"\n\n===== Contents of `{rel}` =====\n{f.read()}")
        except OSError:
            pass
    if len(parts) > 1:
        parts.append("\n\nThe files above are the ones this prompt asks you to read. "
                     "Answer with ONLY the JSON object, no other text.")
    return "".join(parts)


def parse_answer(text: str) -> dict | None:
    """The first JSON object in a reply, fenced or not."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _call(fn, prompt, model, key, max_tokens, tries=4):
    for attempt in range(tries):
        try:
            return fn(prompt, model, key, max_tokens)
        except urllib.error.HTTPError as err:
            if err.code not in (408, 429, 500, 502, 503, 504, 529) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        time.sleep(2 ** attempt * 2)


def run(run_dir: str, stage: str, project_dir: str = ".", jobs: list[int] | None = None,
        force: bool = False, workers: int = WORKERS) -> dict:
    """Judge every `<stage>/<n>.txt` that has no answer yet (or `jobs`),
    write `<n>.out.json`, and record the real tokens used."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}")
    env = read_env(project_dir)
    st = status(project_dir)
    p = PROVIDERS[st["provider"]]
    key = _setting(env, p["key_var"])
    if not key:
        where = f"get a key at {p['key_page']} and " if p["key_page"] else ""
        raise SystemExit(f"no {p['key_var']} in {ENV_FILE}: {where}paste it on the Pipeline tab or into {ENV_FILE}")
    model, base = st["models"][stage], st["base"]
    if not model or not base:
        raise SystemExit(f"set the API address and the {stage} model for {p['label']} on the Pipeline tab")
    fn = lambda prompt, model, key, max_tokens: p["call"](prompt, model, key, max_tokens, base)
    folder = os.path.join(run_dir, stage)
    numbers = sorted(int(n[:-4]) for n in os.listdir(folder) if re.fullmatch(r"\d+\.txt", n))
    if jobs is not None:
        numbers = [n for n in numbers if n in set(jobs)]
    elif not force:
        numbers = [n for n in numbers if not os.path.exists(os.path.join(folder, f"{n}.out.json"))]

    def one(n):
        with open(os.path.join(folder, f"{n}.txt"), encoding="utf-8") as f:
            prompt = inline_files(f.read(), project_dir)
        try:
            text, tin, tout = _call(fn, prompt, model, key, MAX_TOKENS[stage])
        except Exception as err:  # one job's failure must not lose the others
            return n, None, 0, f"{type(err).__name__}: {getattr(err, 'code', '') or err}"
        answer = parse_answer(text)
        if answer is not None:
            tmp = os.path.join(folder, f"{n}.out.json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(answer, f, ensure_ascii=False)
            os.replace(tmp, os.path.join(folder, f"{n}.out.json"))
        live.record_tokens(run_dir, tin + tout, f"{stage} {n} api {model}")
        return n, answer, tin + tout, None if answer is not None else "reply was not JSON"

    written, failed, tokens = [], {}, 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for n, answer, used, error in pool.map(one, numbers):
            tokens += used
            if error:
                failed[n] = error
            else:
                written.append(n)
    return {"stage": stage, "provider": st["provider"], "model": model, "judged": len(written), "failed": failed,
            "tokens": tokens, "total": live.tokens_total(run_dir)}


def main():
    ap = argparse.ArgumentParser(prog="ftja.judge", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="which judge is chosen and whether a key is set (never prints the key)")
    rp = sub.add_parser("run", help="judge a stage's prompts with the model API")
    rp.add_argument("--dir", required=True)
    rp.add_argument("--stage", required=True, choices=STAGES)
    rp.add_argument("--jobs", help="comma-separated job numbers (default: every one without an answer)")
    rp.add_argument("--force", action="store_true", help="judge again even where an answer exists")
    rp.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()
    if args.cmd == "status":
        out = status(".")
    else:
        jobs = [int(j) for j in args.jobs.split(",") if j.strip()] if args.jobs else None
        out = run(args.dir, args.stage, ".", jobs, args.force, args.workers)
    print(json.dumps(out, indent=2))
    if args.cmd == "run" and out["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
