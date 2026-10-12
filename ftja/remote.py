"""Remote check — an optional stage between the code filter and Stage 1.

It answers a short list of questions about how remote a role is (the
"categories": how remote, from where, which hours, which on-site duties),
from the sentences around remote-related keywords only, with a cheap model.
The model does not say pass or fail. It picks one of each category's listed
answers, or "?" when the sentences do not say; code then applies the rule
the user set for that answer. So changing a rule never needs a model call,
and every verdict can be traced to an answer and the sentences behind it.

The setup lives in `remote-check.json` (the user's own file, like
criteria.json):

  {"drop_without_keywords": true,       # no remote keyword at all -> dropped by code
   "guidance": "...",                   # shown to the model above the questions
   "profile": {"residence", "work_countries", "time_zone", "work_permit"},
   "categories": [
     {"id": "remote_level", "label": "Remote level",
      "question": "How remote is the role itself?",
      "keywords": ["remote", "hybrid", ...],     # sentences with these are shown to the model
      "options": [{"id": "fully_remote", "label": "Fully remote", "rule": "pass"},
                  {"id": "hybrid", "label": "Hybrid", "rule": "fail"}],
      "unknown": "pass"}]}               # what "?" means for this category

A posting passes when no category's answer carries the rule "fail".

`/ftja-remote` drafts this file for the user: it works out where they
live, where they want to work, their time zone and their work permit (from
their profile, or from what they typed on the page), and writes the
categories for that. The draft (`remote-check.draft.json`) becomes the
setup only when the user has gone through it on the Pipeline tab and
confirmed. `remote-request.json` is how the tab asks the agent for a draft
and follows it: requested -> generating -> drafted.

Usage:
    python -m ftja.remote show | request | begin | fail | save --file F | coverage
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone

from ftja.text_blocks import find_matched_with_context, keyword_matcher, split_sentences, stem, tokenize

CONFIG_FILE = "remote-check.json"
REQUEST_FILE = "remote-request.json"
DRAFT_FILE = "remote-check.draft.json"
UNKNOWN = "?"
TITLE_SID = "T"
RULES = ("pass", "fail")
PROFILE_KEYS = ("residence", "work_countries", "time_zone", "work_permit")
# A request the agent took up and never finished stops showing as "generating" after this long.
GENERATING_STALE_SECONDS = 600

# The built-in setup, used until the user has their own. Its keywords were
# measured on 988 scraped European postings: matching is by word stem, so
# "globally" also hit "global", "countries" hit "country", "distributed" hit
# "distribution" and "gathering" hit "gather requirements" — together they
# sent 82% of all postings to the model. Without them it is 67%, and every
# posting a person had marked as remote in that set is still caught.
DEFAULT_CONFIG = {
    "drop_without_keywords": True,
    "guidance": ("Judge the role itself, not the company. Travel, an onboarding week and occasional team "
                 "gatherings are not office days. Steps of the hiring process, such as an on-site interview, say "
                 "nothing about the job: ignore them."),
    "profile": {},
    "categories": [
        {"id": "remote_level", "label": "Remote level", "question": "How remote is the role itself?",
         "keywords": ["remote", "remote-first", "work from home", "working from home", "WFH", "home office",
                      "home-based", "telework", "fully distributed", "distributed team", "distributed company",
                      "hybrid", "on-site", "onsite", "in-office", "in the office", "office days", "days per week",
                      "days a week", "work from", "anywhere", "relocate", "relocation"],
         "options": [{"id": "fully_remote", "label": "Fully remote", "rule": "pass"},
                     {"id": "hybrid", "label": "Hybrid: regular office days", "rule": "fail"},
                     {"id": "on_site", "label": "On-site", "rule": "fail"}],
         "unknown": "pass"},
        {"id": "work_location", "label": "Where you may work from",
         "question": "From where may the person work?",
         "keywords": ["based in", "located in", "reside", "residents", "residency", "eligible to work",
                      "authorized to work", "right to work", "work authorization", "work permit", "visa",
                      "sponsorship", "work from anywhere", "anywhere in the world", "EMEA", "EU"],
         "options": [{"id": "anywhere", "label": "Anywhere", "rule": "pass"},
                     {"id": "restricted", "label": "Only certain countries or regions", "rule": "pass"}],
         "unknown": "pass"},
        {"id": "time_zone", "label": "Time zone", "question": "Which working hours or time zone does it require?",
         "keywords": ["time zone", "timezone", "time zones", "timezones", "working hours", "core hours",
                      "overlap", "CET", "CEST", "GMT", "UTC", "EST", "PST"],
         "options": [{"id": "any", "label": "Any hours", "rule": "pass"},
                     {"id": "required", "label": "Requires certain hours or a time zone", "rule": "pass"}],
         "unknown": "pass"},
        {"id": "onsite_duties", "label": "Extra on-site duties",
         "question": "Besides regular office days, which on-site presence does it ask for?",
         "keywords": ["onboarding", "offsite", "off-site", "retreat", "team gathering", "company gathering",
                      "meetup", "meet-up", "travel", "in person", "in-person", "headquarters", "HQ"],
         "options": [{"id": "none", "label": "None", "rule": "pass"},
                     {"id": "occasional", "label": "Occasional: onboarding, gatherings or travel", "rule": "pass"}],
         "unknown": "pass"},
    ],
}


# ---- the setup file ----------------------------------------------------------

def _take_id(wanted, label: str, taken: set) -> str:
    """Keep an id that is already there (stored answers refer to it); make
    one from the name for a category or answer added in the viewer."""
    wanted = str(wanted or "").strip()
    base = wanted or re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")[:40] or "item"
    slug, n = base, 2
    while slug in taken or slug == UNKNOWN:
        slug, n = f"{base}_{n}", n + 1
    taken.add(slug)
    return slug


def _strings(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return list(dict.fromkeys(str(v).strip() for v in (value or []) if str(v).strip()))


def validate_config(data) -> dict:
    """The setup as it will be saved: ids filled in, lists cleaned. Raises
    ValueError, saying what to fix, for a setup the check could not run on."""
    if not isinstance(data, dict):
        raise ValueError("the remote check setup must be an object")
    categories = data.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("the remote check needs at least one category")
    clean, ids = [], set()
    for raw in categories:
        if not isinstance(raw, dict):
            raise ValueError("each category must be an object")
        label = str(raw.get("label") or "").strip()
        if not label:
            raise ValueError("every category needs a name")
        options, option_ids = [], set()
        for o in raw.get("options") or []:
            if not isinstance(o, dict) or not str(o.get("label") or "").strip():
                raise ValueError(f"category '{label}': every answer needs a name")
            rule = str(o.get("rule") or "pass").lower()
            if rule not in RULES:
                raise ValueError(f"category '{label}': an answer's rule is pass or fail")
            options.append({"id": _take_id(o.get("id"), o["label"], option_ids),
                            "label": str(o["label"]).strip(), "rule": rule})
        if not options:
            raise ValueError(f"category '{label}' needs at least one answer")
        unknown = str(raw.get("unknown") or "pass").lower()
        if unknown not in RULES:
            raise ValueError(f"category '{label}': the rule for '?' is pass or fail")
        clean.append({"id": _take_id(raw.get("id"), label, ids), "label": label, "question": str(raw.get("question") or label).strip(),
                      "keywords": _strings(raw.get("keywords")), "options": options, "unknown": unknown})
    if not any(c["keywords"] for c in clean):
        raise ValueError("the remote check needs at least one keyword: it reads only the sentences around them")
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
    return {"drop_without_keywords": data.get("drop_without_keywords", True) is not False,
            "guidance": str(data.get("guidance") or "").strip(),
            "profile": {k: str(profile.get(k) or "").strip() for k in PROFILE_KEYS if str(profile.get(k) or "").strip()},
            "categories": clean}


def default_config() -> dict:
    return validate_config(json.loads(json.dumps(DEFAULT_CONFIG)))


def _read(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write(path: str, value):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_config(path: str) -> dict | None:
    """The saved setup, or None when there is none (or it cannot be used)."""
    data = _read(path)
    try:
        return validate_config(data) if data is not None else None
    except ValueError:
        return None


def config_or_default(project_dir: str = ".") -> dict:
    return load_config(os.path.join(project_dir, CONFIG_FILE)) or default_config()


def save_config(project_dir: str, data) -> dict:
    config = validate_config(data)
    _write(os.path.join(project_dir, CONFIG_FILE), config)
    return config


# ---- which sentences the model sees --------------------------------------------

def all_keywords(config: dict) -> list[str]:
    return list(dict.fromkeys(k for c in config.get("categories") or [] for k in c.get("keywords") or []))


def blocks_for(job: dict, config: dict) -> list[dict]:
    """The sentences containing any category's keyword, each with the
    sentence before and after it, and the job's title on top as `T`. Empty
    when neither the title nor the description has a remote keyword."""
    keywords = all_keywords(config)
    blocks = find_matched_with_context(split_sentences(job.get("description") or ""), keywords)
    title = str(job.get("title") or "").strip()
    tokens = tokenize(title)
    stems = [stem(t) for t in tokens]
    in_title = [k for k in keywords for match in [keyword_matcher(k)] if match and match(title, tokens, stems)]
    if not blocks and not in_title:
        return []
    head = [{"index": -1, "s_id": TITLE_SID, "sentence": title, "matched": bool(in_title),
             "matched_keywords": in_title}] if title else []
    return head + blocks


def render_prompt(template: str, config: dict, job: dict, blocks: list[dict]) -> str:
    questions = []
    for c in config["categories"]:
        answers = "\n".join(f"    - `{o['id']}`: {o['label']}" for o in c["options"])
        questions.append(f"- `{c['id']}` — {c['question']}\n{answers}\n    - `{UNKNOWN}`: the sentences do not say")
    rendered = "\n".join(
        f"{b['s_id']}[{'title' if b['s_id'] == TITLE_SID else 'K' if b.get('matched') else 'ctx'}] {b['sentence']}"
        for b in blocks)
    return (template.replace("{guidance}", config.get("guidance") or "")
            .replace("{questions}", "\n".join(questions))
            .replace("{title}", str(job.get("title") or "")).replace("{company}", str(job.get("company") or ""))
            .replace("{location}", str(job.get("location") or "")).replace("{blocks}", rendered))


# ---- reading the model's answer ------------------------------------------------

def answer_problems(config: dict, shown_sids: set, answer: dict) -> list[str]:
    """What makes an answer unusable, so the job is run again: no list of
    answers, or an answer that is not one of the category's listed ones."""
    answers = answer.get("answers")
    if not isinstance(answers, list):
        return ["no `answers` list"]
    by_id = {str(a.get("id")): a for a in answers if isinstance(a, dict)}
    problems = []
    for c in config["categories"]:
        given = by_id.get(c["id"])
        value = str((given or {}).get("answer") or UNKNOWN).strip()
        if value != UNKNOWN and value not in {o["id"] for o in c["options"]}:
            problems.append(f"{c['id']}: '{value}' is not one of the listed answers")
    return problems


def judge(config: dict, answer: dict | None, shown_sids: set | None = None) -> dict:
    """Apply the user's rules to one posting's answers.

    -> {"verdict": "pass" | "fail", "answers": [{id, label, answer,
        answer_label, detail, rule, evidence_sids, note}], "reason": str}

    An answer other than "?" has to point at a sentence the model was
    shown; one that does not is read as "?" (with a `note` saying so), the
    same way Stage 2 treats a result without a real quote. A category the
    model left out is "?" too. `answer=None` is a posting with no remote
    keyword at all: every category is "?" without a model call."""
    by_id = {str(a.get("id")): a for a in (answer or {}).get("answers") or [] if isinstance(a, dict)}
    out = []
    for c in config["categories"]:
        given = by_id.get(c["id"]) or {}
        value = str(given.get("answer") or UNKNOWN).strip()
        option = next((o for o in c["options"] if o["id"] == value), None)
        sids = [str(s) for s in given.get("evidence_sids") or []]
        if shown_sids is not None:
            sids = [s for s in sids if s in shown_sids]
        note = ""
        if option and not sids:
            option, note = None, f"The model answered '{option['label']}' without pointing at a sentence; read as not stated."
        elif value != UNKNOWN and not option:
            note = f"The model gave an answer that is not listed ('{value}'); read as not stated."
        out.append({"id": c["id"], "label": c["label"], "answer": option["id"] if option else UNKNOWN,
                    "answer_label": option["label"] if option else "Not stated",
                    "detail": str(given.get("detail") or "").strip() if option else "",
                    "rule": option["rule"] if option else c["unknown"],
                    "evidence_sids": sids if option else [], "note": note})
    failed = [a for a in out if a["rule"] == "fail"]
    reason = "; ".join(f"{a['label']}: {a['answer_label']}" + (f" ({a['detail']})" if a["detail"] else "") for a in failed)
    return {"verdict": "fail" if failed else "pass", "answers": out, "reason": reason}


# ---- the Generate button's request ---------------------------------------------

def read_request(project_dir: str = ".") -> dict:
    """-> {"status": "none" | "requested" | "generating" | "drafted" | "failed", "answers": {...}, ...}"""
    request = _read(os.path.join(project_dir, REQUEST_FILE))
    if not isinstance(request, dict) or not request.get("status"):
        return {"status": "none", "answers": {}}
    if request["status"] == "generating" and time.time() - float(request.get("began_at") or 0) > GENERATING_STALE_SECONDS:
        request = {**request, "status": "failed", "error": "The agent session stopped before it finished."}
    return request


def write_request(project_dir: str, answers: dict) -> dict:
    """The page asks the agent for a draft. `answers` are the four answers
    as the user confirmed them; all empty means "work them out yourself"."""
    clean = {k: str((answers or {}).get(k) or "").strip() for k in PROFILE_KEYS}
    request = {"status": "requested", "answers": clean, "requested_at": time.time(),
               "at": datetime.now(timezone.utc).isoformat()}
    _write(os.path.join(project_dir, REQUEST_FILE), request)
    return request


def load_draft(project_dir: str = ".") -> dict | None:
    return load_config(os.path.join(project_dir, DRAFT_FILE))


def save_draft(project_dir: str, data) -> dict:
    """The agent's draft: checked like a setup, shown to the user to confirm."""
    config = validate_config(data)
    _write(os.path.join(project_dir, DRAFT_FILE), config)
    set_request_status(project_dir, "drafted")
    return config


def discard_draft(project_dir: str):
    for name in (DRAFT_FILE, REQUEST_FILE):
        try:
            os.remove(os.path.join(project_dir, name))
        except OSError:
            pass


def set_request_status(project_dir: str, status: str, error: str = "") -> dict:
    request = _read(os.path.join(project_dir, REQUEST_FILE))
    request = request if isinstance(request, dict) else {"answers": {}}
    request.update(status=status, error=error)
    if status == "generating":
        request["began_at"] = time.time()
    _write(os.path.join(project_dir, REQUEST_FILE), request)
    return request


# ---- how much of a scrape the keywords catch -----------------------------------

def coverage(jobs: list[dict], config: dict) -> dict:
    """With no model call: how many of these postings have a remote keyword
    (those are the ones the model would read), and which keywords catch the
    most. A keyword that hits nearly every posting filters nothing."""
    hits: dict[str, int] = {}
    caught = 0
    for job in jobs:
        blocks = blocks_for(job, config)
        caught += bool(blocks)
        for keyword in {k for b in blocks for k in b.get("matched_keywords") or []}:
            hits[keyword] = hits.get(keyword, 0) + 1
    unused = [k for k in all_keywords(config) if k not in hits]
    return {"postings": len(jobs), "with_a_keyword": caught, "without": len(jobs) - caught,
            "by_keyword": dict(sorted(hits.items(), key=lambda kv: -kv[1])), "never_matched": unused}


def _latest_scrape(project_dir: str) -> list[dict]:
    root = os.path.join(project_dir, ".ftja-run")
    dirs = sorted((os.path.join(root, d) for d in os.listdir(root)), key=os.path.getmtime, reverse=True) \
        if os.path.isdir(root) else []
    for run_dir in dirs:
        scrape_dir = os.path.join(run_dir, "scrape")
        jobs: dict[str, dict] = {}
        for name in os.listdir(scrape_dir) if os.path.isdir(scrape_dir) else []:
            if name.endswith(".json") and not name.endswith(".progress.json"):
                for job in _read(os.path.join(scrape_dir, name)) or []:
                    if job.get("job_url"):
                        jobs.setdefault(job["job_url"], job)
        if jobs:
            return list(jobs.values())
    return []


def main():
    ap = argparse.ArgumentParser(description="The Remote check's setup, for /ftja-remote.")
    ap.add_argument("--project-dir", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print the current setup (the built-in one when nothing is saved yet)")
    sub.add_parser("request", help="print what the Pipeline tab's Generate button asked for, if anything")
    sub.add_parser("begin", help="tell the viewer the setup is being written (it shows a spinner)")
    fail = sub.add_parser("fail", help="tell the viewer the setup could not be written")
    fail.add_argument("--error", default="")
    save = sub.add_parser("save", help="check a setup and hand it to the user as a draft to confirm on the Pipeline tab")
    save.add_argument("--file", required=True, help="a JSON file with the setup")
    cov = sub.add_parser("coverage", help="how many postings the keywords catch, with no model call")
    cov.add_argument("--jobs", default="", help="a JSON list of scraped jobs; default: the newest run's scrape")
    cov.add_argument("--file", default="", help="a setup to try instead of the saved one")
    args = ap.parse_args()

    if args.cmd == "show":
        saved = load_config(os.path.join(args.project_dir, CONFIG_FILE))
        out = {"saved": saved is not None, "config": saved or default_config()}
    elif args.cmd == "request":
        out = read_request(args.project_dir)
    elif args.cmd == "begin":
        out = set_request_status(args.project_dir, "generating")
    elif args.cmd == "fail":
        out = set_request_status(args.project_dir, "failed", args.error)
    elif args.cmd == "save":
        with open(args.file) as f:
            data = json.load(f)
        try:
            config = save_draft(args.project_dir, data)
        except ValueError as error:
            raise SystemExit(f"Not saved: {error}")
        out = {"draft": os.path.join(args.project_dir, DRAFT_FILE), "categories": [c["id"] for c in config["categories"]],
               "keywords": len(all_keywords(config)),
               "next": "the user reviews and confirms it on the Pipeline tab, under Remote check"}
    else:
        config = config_or_default(args.project_dir)
        if args.file:
            with open(args.file) as f:
                config = validate_config(json.load(f))
        jobs = _read(args.jobs) if args.jobs else _latest_scrape(args.project_dir)
        out = coverage(jobs or [], config)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
