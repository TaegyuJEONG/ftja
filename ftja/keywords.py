"""Keywords by language.

Two stages read a posting only where a keyword appears: Stage 1 (the
`keywords.tier1` list in criteria.json) and the Remote check (the keywords
of its categories in remote-check.json). A posting written in French is
only caught by French keywords, so each list records which language each
keyword is in:

  criteria.json      "keywords": {"tier1": [...], "tier1_language": {"prototypage": "fr"}}
  remote-check.json  "keyword_language": {"télétravail": "fr"}

A keyword with no language recorded counts for the first language the user
reads. A keyword of a language the user no longer reads (they removed it
from the readable languages) is kept but switched off: it is not matched,
and comes back when the language does.

When the user starts reading a new language, its keywords are missing
("a gap"). The Pipeline tab offers to have the agent write them;
`keywords-request.json` carries that request, the same way the Remote
check's setup is asked for: requested -> generating -> drafted, and the
user confirms on the page before anything is added.

Usage (for /ftja-keywords):
    python -m ftja.keywords gaps
    python -m ftja.keywords request
    python -m ftja.keywords begin [--all]
    python -m ftja.keywords propose --file F
    python -m ftja.keywords fail --error "..."
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone

from ftja import remote
from ftja import osutil

REQUEST_FILE = "keywords-request.json"
TARGETS = ("stage1", "remote")
GENERATING_STALE_SECONDS = 600


def _read(path: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write(path: str, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    osutil.replace(tmp, path)


def _codes(languages) -> list[str]:
    return list(dict.fromkeys(str(x).strip().lower() for x in languages or [] if str(x).strip()))


def active(keywords: list[str], language_of: dict | None, read_languages) -> list[str]:
    """The keywords that are matched: all of them, except those recorded as
    being in a language the user does not read (any more)."""
    read = set(_codes(read_languages))
    marked = language_of or {}
    if not read:
        return list(keywords or [])
    return [k for k in keywords or [] if str(marked.get(k) or "").lower() in read or k not in marked]


def without_keywords(keywords: list[str], language_of: dict | None, read_languages) -> list[str]:
    """The languages the user reads that this list has no keyword for."""
    read = _codes(read_languages)
    marked = language_of or {}
    covered = {str(v).lower() for k, v in marked.items() if k in set(keywords or [])}
    if read and any(k not in marked for k in keywords or []):
        covered.add(read[0])
    return [code for code in read if code not in covered] if keywords else []


def active_remote_config(config: dict, read_languages) -> dict:
    """The Remote check's setup as a run uses it: without the keywords of
    languages the user does not read."""
    marked = config.get("keyword_language") or {}
    return {**config, "categories": [{**c, "keywords": active(c.get("keywords"), marked, read_languages)}
                                     for c in config.get("categories") or []]}


def _remote_target(project_dir: str) -> tuple[dict | None, str]:
    """The Remote check setup that keywords are added to: the draft the user
    is going through when there is one, else the saved setup."""
    draft = remote.load_draft(project_dir)
    if draft:
        return draft, "draft"
    return remote.load_config(os.path.join(project_dir, remote.CONFIG_FILE)), "saved"


def gaps(project_dir: str = ".") -> dict:
    """For each keyword list, the languages the user reads that it has no
    keyword for, with what the agent needs to write them."""
    criteria = _read(os.path.join(project_dir, "criteria.json")) or {}
    read = _codes(criteria.get("languages"))
    tier1 = (criteria.get("keywords") or {}).get("tier1") or []
    tier1_language = (criteria.get("keywords") or {}).get("tier1_language") or {}
    remote_config, _ = _remote_target(project_dir)
    remote_keywords = remote.all_keywords(remote_config) if remote_config else []
    remote_language = (remote_config or {}).get("keyword_language") or {}
    return {"languages_the_user_reads": read,
            "stage1": {"missing": without_keywords(tier1, tier1_language, read),
                       "keywords": tier1, "keyword_language": tier1_language},
            "remote": {"missing": without_keywords(remote_keywords, remote_language, read) if remote_config else [],
                       "keywords": remote_keywords, "keyword_language": remote_language,
                       "categories": [{"id": c["id"], "label": c["label"], "keywords": c["keywords"]}
                                      for c in (remote_config or {}).get("categories") or []]}}


def read_request(project_dir: str = ".") -> dict:
    """-> {"status": "none" | "requested" | "generating" | "drafted" | "failed", "items": [{target, language, keywords}]}"""
    request = _read(os.path.join(project_dir, REQUEST_FILE))
    if not isinstance(request, dict) or not request.get("status"):
        return {"status": "none", "items": []}
    if request["status"] == "generating" and time.time() - float(request.get("began_at") or 0) > GENERATING_STALE_SECONDS:
        request = {**request, "status": "failed", "error": "The agent session stopped before it finished."}
    return request


def pending(project_dir: str = ".") -> bool:
    return read_request(project_dir)["status"] in ("requested", "generating")


def write_request(project_dir: str, items: list[dict]) -> dict:
    """The page asks the agent for the keywords of these (target, language) pairs."""
    clean = [{"target": i["target"], "language": str(i["language"]).strip().lower()} for i in items or []
             if isinstance(i, dict) and i.get("target") in TARGETS and str(i.get("language") or "").strip()]
    if not clean:
        raise ValueError("say which keyword list and which language")
    if remote.read_request(project_dir)["status"] in ("requested", "generating"):
        raise ValueError("your agent is writing the Remote check's setup; finish that first")
    request = {"status": "requested", "items": clean, "requested_at": time.time(),
               "at": datetime.now(timezone.utc).isoformat()}
    _write(os.path.join(project_dir, REQUEST_FILE), request)
    return request


def begin(project_dir: str, everything: bool = False) -> dict:
    """The agent takes the request up. With no request from the page (the
    user typed /ftja-keywords), or with `everything`, it covers every gap."""
    request = read_request(project_dir)
    if everything or request["status"] not in ("requested", "generating"):
        found = gaps(project_dir)
        items = [{"target": t, "language": code} for t in TARGETS for code in found[t]["missing"]]
        if not items:
            return {"status": "none", "items": [], "nothing_missing": True}
        request = {"items": items, "requested_at": time.time(), "at": datetime.now(timezone.utc).isoformat()}
    request.update(status="generating", began_at=time.time(), error="")
    _write(os.path.join(project_dir, REQUEST_FILE), request)
    return request


def propose(project_dir: str, proposals: list[dict]) -> dict:
    """The agent's keywords for each item of the request, for the user to
    confirm: [{"target", "language", "keywords": [...], "category_of": {keyword: category id}}]."""
    request = read_request(project_dir)
    if not request.get("items"):
        raise ValueError("there is no request to answer; run `begin` first")
    by_item = {(p.get("target"), str(p.get("language") or "").lower()): p for p in proposals or [] if isinstance(p, dict)}
    items = []
    for item in request["items"]:
        given = by_item.get((item["target"], item["language"]))
        words = remote._strings((given or {}).get("keywords"))
        if not words:
            raise ValueError(f"no keywords given for {item['target']} / {item['language']}")
        placed = (given.get("category_of") or {}) if isinstance(given.get("category_of"), dict) else {}
        items.append({**item, "keywords": words, "category_of": {k: str(v) for k, v in placed.items() if k in words}})
    request.update(status="drafted", items=items)
    _write(os.path.join(project_dir, REQUEST_FILE), request)
    return request


def fail(project_dir: str, error: str = "") -> dict:
    request = read_request(project_dir)
    request.update(status="failed", error=error)
    _write(os.path.join(project_dir, REQUEST_FILE), request)
    return request


def discard(project_dir: str):
    try:
        os.remove(os.path.join(project_dir, REQUEST_FILE))
    except OSError:
        pass


def add_keywords(project_dir: str, items: list[dict]) -> dict:
    """The user confirmed: add each item's keywords to its list, recorded
    under its language. Existing keywords are left as they are."""
    added = {}
    for item in items or []:
        target, code = item.get("target"), str(item.get("language") or "").strip().lower()
        words = remote._strings(item.get("keywords"))
        if target not in TARGETS or not code or not words:
            continue
        if target == "stage1":
            path = os.path.join(project_dir, "criteria.json")
            criteria = _read(path) or {}
            block = dict(criteria.get("keywords") or {})
            tier1 = list(block.get("tier1") or [])
            language_of = dict(block.get("tier1_language") or {})
            for word in words:
                if word not in tier1:
                    tier1.append(word)
                language_of[word] = code
            criteria["keywords"] = {**block, "tier1": tier1, "tier1_language": language_of}
            _write(path, criteria)
        else:
            config, where = _remote_target(project_dir)
            if not config:
                continue
            placed = item.get("category_of") or {}
            ids = [c["id"] for c in config["categories"]]
            have = set(remote.all_keywords(config))
            for word in words:
                if word not in have:
                    target_id = placed.get(word) if placed.get(word) in ids else ids[0]
                    next(c for c in config["categories"] if c["id"] == target_id)["keywords"].append(word)
                config.setdefault("keyword_language", {})[word] = code
            config["languages"] = list(dict.fromkeys([*(config.get("languages") or []), code]))
            if where == "draft":
                remote._write(os.path.join(project_dir, remote.DRAFT_FILE), remote.validate_config(config))
            else:
                remote.save_config(project_dir, config)
        added[f"{target}/{code}"] = len(words)
    discard(project_dir)
    return added


def main():
    ap = argparse.ArgumentParser(description="Keywords for a language the user started reading, for /ftja-keywords.")
    ap.add_argument("--project-dir", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gaps", help="which keyword lists lack which languages, and the keywords they have")
    sub.add_parser("request", help="what the Pipeline tab asked for, if anything")
    b = sub.add_parser("begin", help="take the request up (the page shows a spinner); with none, cover every gap")
    b.add_argument("--all", action="store_true", help="cover every gap, whatever the page asked for")
    p = sub.add_parser("propose", help="hand the keywords to the user to confirm on the page")
    p.add_argument("--file", required=True, help='a JSON list: [{"target", "language", "keywords": [...], "category_of": {...}}]')
    f = sub.add_parser("fail", help="tell the page the keywords could not be written")
    f.add_argument("--error", default="")
    args = ap.parse_args()
    if args.cmd == "gaps":
        out = gaps(args.project_dir)
    elif args.cmd == "request":
        out = read_request(args.project_dir)
    elif args.cmd == "begin":
        out = begin(args.project_dir, args.all)
    elif args.cmd == "propose":
        with open(args.file, encoding="utf-8-sig") as f:
            data = json.load(f)
        try:
            out = propose(args.project_dir, data)
        except ValueError as error:
            raise SystemExit(f"Not accepted: {error}")
        out = {"status": out["status"], "items": [{"target": i["target"], "language": i["language"], "keywords": len(i["keywords"])} for i in out["items"]],
               "next": "the user confirms them on the Pipeline tab"}
    else:
        out = fail(args.project_dir, args.error)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
