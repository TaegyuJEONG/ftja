"""Judge a run's prompts with the user's own model API key instead of the
coding agent's subagents.

The agent path and this one read the same `<run_dir>/<stage>/<n>.txt`
prompts and write the same `<n>.out.json` answers, so `check-stage1`,
`prepare-stage2`, `results` and `finalize` do not know which one ran.

The key lives in `.env` in the FTJA folder (gitignored), next to the
choice of judge:

    FTJA_JUDGE=api            # "agent" (default) or "api"
    ANTHROPIC_API_KEY=sk-ant-...

A provider is one function `(prompt, model, key) -> (text, input_tokens,
output_tokens)` in PROVIDERS, so adding another model API is one function
and one entry.
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
# The agent path's haiku / sonnet, as API model ids. Overridable in .env.
DEFAULT_MODELS = {"rc": "claude-haiku-5-5", "s1": "claude-haiku-5-5", "s2": "claude-sonnet-5-5"}
MODEL_VARS = {"rc": "FTJA_REMOTE_MODEL", "s1": "FTJA_STAGE1_MODEL", "s2": "FTJA_STAGE2_MODEL"}
MAX_TOKENS = {"rc": 1024, "s1": 1024, "s2": 4096}
WORKERS = 8
KEY_PAGE = "https://console.anthropic.com/settings/keys"


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

def anthropic(prompt: str, model: str, key: str, max_tokens: int = 1024) -> tuple[str, int, int]:
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read())
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    usage = out.get("usage") or {}
    return text, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


PROVIDERS = {"anthropic": (anthropic, "ANTHROPIC_API_KEY")}


def status(project_dir: str = ".") -> dict:
    """What the viewer and the run skill need: which judge is chosen and
    whether a key is set. Never the key itself."""
    env = read_env(project_dir)
    provider = _setting(env, "FTJA_PROVIDER", "anthropic")
    key_var = PROVIDERS.get(provider, (None, ""))[1]
    mode = _setting(env, "FTJA_JUDGE", "agent")
    return {"mode": mode if mode in ("agent", "api") else "agent", "provider": provider,
            "key_var": key_var, "key_set": bool(key_var and _setting(env, key_var)),
            "models": {s: _setting(env, MODEL_VARS[s], DEFAULT_MODELS[s]) for s in STAGES},
            "key_page": KEY_PAGE}


def choose(project_dir: str, mode: str | None = None, key: str | None = None) -> dict:
    """The viewer's Pipeline tab: pick agent or API, and/or save a pasted
    key to .env. Returns status(), which never includes the key."""
    updates = {}
    if mode is not None:
        if mode not in ("agent", "api"):
            raise ValueError("mode must be agent or api")
        updates["FTJA_JUDGE"] = mode
    if key is not None:
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]{20,300}", key):
            raise ValueError("that does not look like an API key")
        updates[status(project_dir)["key_var"]] = key
    if updates:
        write_env(project_dir, updates)
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
    if st["provider"] not in PROVIDERS:
        raise SystemExit(f"unknown FTJA_PROVIDER {st['provider']!r}")
    fn, key_var = PROVIDERS[st["provider"]]
    key = _setting(env, key_var)
    if not key:
        raise SystemExit(f"no {key_var} in {ENV_FILE}: get a key at {KEY_PAGE} and paste it there")
    model = st["models"][stage]
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
    return {"stage": stage, "model": model, "judged": len(written), "failed": failed,
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
