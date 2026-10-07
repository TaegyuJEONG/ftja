"""Stage 2's final verdict, computed by code from per-criterion results.

The Stage 2 model does not return pass/fail. It returns one result per
rubric criterion, and this module turns those into pass / review / fail.
Two reasons:
  - the same JD judged twice could flip when the model picked the overall
    verdict itself; a fixed rule over per-criterion results cannot
  - each result carries a verbatim JD quote, and a quote can be checked
    against the JD text without spending a token

The criteria live in `rubric-criteria.json` — the rubric as a list, one
entry per criterion, in the rubric's own three categories:

  {"rubric_version": "<ftja.state.rubric_version()>",
   "criteria": [
     {"id": "direct_build", "kind": "pass", "group": "builder",
      "label": "Builds the product personally", "text": "<the rule>"},
     {"id": "internship", "kind": "fail", "label": "Internship or trainee", "text": "..."},
     {"id": "ownership", "kind": "preference", "label": "End-to-end ownership", "text": "..."}],
   "notes": {"pass": "<free text that applies to the whole section>", "fail": "", "preference": ""}}

`group` is optional and only meaningful on `pass` criteria: criteria that
share a group must ALL be met to count, and a job needs just one group (or
one ungrouped criterion) to pass. A rubric with no such structure simply
leaves it out.

The list and `rubric.md` hold the same rubric. Saving the list from the
viewer's Pipeline tab rewrites rubric.md from it (`save_criteria`); when
rubric.md is edited directly instead, the list is stale and /ftja-run
splits it again. Ids survive both, which is what lets per-criterion
feedback be counted across jobs and runs.

Results per kind:
  pass        met | unclear | not_met
  fail        triggered | unclear | not_applicable
  preference  met | unclear | not_met | not_applicable   (shown, never decides)

`unclear` means the JD says something ambiguous. A JD that says nothing
(no salary, no language requirement) is `not_applicable`, or nearly every
job would land in review.

Rule:
  fail    a fail criterion is triggered, or no pass group can still be met
  pass    every criterion of at least one pass group is met, and no fail
          criterion is unclear
  review  anything else
"""
import argparse
import json
import os
import re
import shutil
import sys

from ftja.state import rubric_version

DEFAULT_CRITERIA_PATH = "rubric-criteria.json"

KINDS = ("pass", "fail", "preference")
RESULTS = {
    "pass": ("met", "unclear", "not_met"),
    "fail": ("triggered", "unclear", "not_applicable"),
    "preference": ("met", "unclear", "not_met", "not_applicable"),
}
# A result that decides something has to point at JD text that exists.
_NEEDS_QUOTE = {"pass": ("met",), "fail": ("triggered",)}
EMPLOYER_KINDS = ("must", "preferred")
CANDIDATE_FITS = ("meets", "unclear", "gap")
MAX_NOTES = 3

_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!&|~<>])")
_MD_MARK_RE = re.compile(r"[*_`#>|~]+")
_WS_RE = re.compile(r"\s+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"',
                         "‐": "-", "‑": "-", "–": "-", "—": "-",
                         " ": " ", " ": " "})


def normalize_text(text: str) -> str:
    """JD text comes back from the scraper as markdown (`**bold**`, `\\-`,
    `\\&`) and a model quoting it may or may not keep those marks. Strip
    them from both sides before comparing, or genuine quotes fail."""
    text = _MD_ESCAPE_RE.sub(r"\1", text or "")
    text = _MD_MARK_RE.sub(" ", text).translate(_QUOTES)
    return _WS_RE.sub(" ", text).strip().casefold()


def quote_in_text(quote: str, description: str) -> bool:
    """A quote may skip the middle of a sentence with "..." or "…"; every
    piece has to appear in the JD, in order."""
    haystack = normalize_text(description)
    pos = 0
    pieces = [p for p in (normalize_text(part) for part in re.split(r"\.{3}|…", quote or "")) if p]
    if not pieces:
        return False
    for piece in pieces:
        found = haystack.find(piece, pos)
        if found < 0:
            return False
        pos = found + len(piece)
    return True


def _group_of(c: dict) -> str:
    """Ungrouped pass criteria are each their own alternative."""
    return c.get("group") or f"\x00{c['id']}"


def load_criteria(path: str = DEFAULT_CRITERIA_PATH) -> dict:
    with open(path) as f:
        data = json.load(f)
    problems = validate_criteria(data)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return data


def validate_criteria(data) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), list):
        return ["expected an object with a `criteria` list"]
    problems, seen = [], set()
    for c in data["criteria"]:
        cid = c.get("id") if isinstance(c, dict) else None
        if not cid or not str(c.get("label") or "").strip():
            problems.append(f"criterion needs id and label: {c!r}")
            continue
        if cid in seen:
            problems.append(f"duplicate id {cid}")
        seen.add(cid)
        if c.get("kind") not in KINDS:
            problems.append(f"{cid}: kind must be one of {KINDS}")
    if not any(isinstance(c, dict) and c.get("kind") == "pass" for c in data["criteria"]):
        problems.append("no pass criteria — nothing could ever pass")
    return problems


def criteria_status(path: str = DEFAULT_CRITERIA_PATH, rubric_path: str = "rubric.md") -> str:
    """'ok' | 'missing' | 'stale' (rubric.md changed since the list was
    written) | 'invalid: ...'."""
    try:
        data = load_criteria(path)
    except FileNotFoundError:
        return "missing"
    except (ValueError, json.JSONDecodeError) as e:
        return f"invalid: {e}"
    return "ok" if data.get("rubric_version") == rubric_version(rubric_path) else "stale"


_SECTIONS = (
    ("pass", "Pass", "Pass a role that meets any one of the following. Criteria listed under \"All of\" "
                     "only count together: every one of them has to hold."),
    ("fail", "Fail", "Fail a role when any of the following applies, whatever else it offers."),
    ("preference", "Preferences", "These do not decide pass or fail. Report them so the candidate can weigh the role."),
)


def render_rubric_md(data: dict) -> str:
    """rubric.md written from the criteria list — what Stage 1 (Pass/Fail
    sections) and Stage 2 (whole file) read."""
    notes = data.get("notes") or {}

    def bullet(c: dict, indent: str = "") -> str:
        text = " ".join(str(c.get("text") or "").split())
        return f"{indent}- **{c['label'].strip()}**" + (f" — {text}" if text else "")

    lines = ["# FTJA judgment rubric", ""]
    for kind, title, intro in _SECTIONS:
        items = [c for c in data["criteria"] if c["kind"] == kind]
        lines += [f"## {title}", "", intro, ""]
        if kind == "pass":
            done = set()
            for c in items:
                group = _group_of(c)
                if group in done:
                    continue
                done.add(group)
                members = [m for m in items if _group_of(m) == group]
                if len(members) == 1:
                    lines.append(bullet(c))
                else:
                    lines.append("- All of:")
                    lines += [bullet(m, "  ") for m in members]
        else:
            lines += [bullet(c) for c in items]
        if not items:
            lines.append("- (none)")
        if str(notes.get(kind) or "").strip():
            lines += ["", str(notes[kind]).strip()]
        lines.append("")
    return "\n".join(lines)


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "criterion"


def save_criteria(project_dir: str, criteria: list[dict], notes: dict | None = None) -> dict:
    """The Pipeline tab's save: clean the list, give new entries an id,
    write it, and rewrite rubric.md from it so both stay the same rubric.
    The first time, the hand-written rubric.md is kept as rubric.md.bak."""
    clean, used = [], {str(c.get("id")) for c in criteria if isinstance(c, dict) and c.get("id")}
    for c in criteria:
        if not isinstance(c, dict):
            raise ValueError("each criterion must be an object")
        label = str(c.get("label") or "").strip()
        if not label:
            raise ValueError("every criterion needs a name")
        cid = str(c.get("id") or "").strip()
        if not cid:
            base = cid = _slug(label)
            n = 2
            while cid in used:
                cid, n = f"{base}_{n}", n + 1
            used.add(cid)
        entry = {"id": cid, "kind": c.get("kind"), "label": label, "text": str(c.get("text") or "").strip()}
        if c.get("kind") == "pass" and c.get("group"):
            entry["group"] = str(c["group"])
        clean.append(entry)
    data = {"criteria": clean,
            "notes": {k: str((notes or {}).get(k) or "").strip() for k in KINDS}}
    problems = validate_criteria(data)
    if problems:
        raise ValueError("; ".join(problems))

    rubric_path = os.path.join(project_dir, "rubric.md")
    backup = rubric_path + ".bak"
    if os.path.exists(rubric_path) and not os.path.exists(backup):
        shutil.copyfile(rubric_path, backup)
    with open(rubric_path, "w") as f:
        f.write(render_rubric_md(data))
    data = {"rubric_version": rubric_version(rubric_path), **data}
    with open(os.path.join(project_dir, DEFAULT_CRITERIA_PATH), "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return data


def judge(definitions: list[dict], raw_results: list[dict] | None, description: str = "") -> dict:
    """Merge the model's per-criterion results onto the criteria list,
    check quotes against the JD, and compute the verdict.

    The output always has exactly one entry per definition, in definition
    order: a criterion the model skipped or answered with an unknown
    result becomes `unclear`, and ids the model invented are dropped.
    Each entry keeps the criterion's label and text as they were when the
    job was judged — the rubric may be edited afterwards."""
    by_id = {}
    for r in raw_results or []:
        if isinstance(r, dict) and r.get("id"):
            by_id.setdefault(str(r["id"]), r)

    criteria = []
    for d in definitions:
        raw = by_id.get(d["id"])
        kind = d["kind"]
        entry = {"id": d["id"], "kind": kind, "label": d["label"], "text": d.get("text") or ""}
        if kind == "pass" and d.get("group"):
            entry["group"] = d["group"]
        if raw is None:
            entry.update(result="unclear", quote=None, why="Not judged.", quote_verified=None)
            criteria.append(entry)
            continue

        result = str(raw.get("result") or "").strip().lower().replace(" ", "_")
        quote = str(raw.get("quote") or "").strip() or None
        why = str(raw.get("why") or "").strip()
        if result not in RESULTS[kind]:
            result, why = "unclear", why or "Not judged."
        verified = quote_in_text(quote, description) if (quote and description) else None

        if result in _NEEDS_QUOTE.get(kind, ()) and (quote is None or verified is False):
            # No checkable quote behind a deciding result: don't let it decide.
            entry["downgraded_from"] = result
            result = "unclear"
        entry.update(result=result, quote=quote, why=why, quote_verified=verified)
        criteria.append(entry)

    return {"verdict": compute_verdict(criteria), "criteria": criteria}


def compute_verdict(criteria: list[dict]) -> str:
    groups: dict[str, list[str]] = {}
    for c in criteria:
        if c["kind"] == "pass":
            groups.setdefault(_group_of(c), []).append(c["result"])
    fails = [c["result"] for c in criteria if c["kind"] == "fail"]

    met = any(all(r == "met" for r in results) for results in groups.values())
    still_open = any("not_met" not in results for results in groups.values())

    if "triggered" in fails or not still_open:
        return "fail"
    if met and "unclear" not in fails:
        return "pass"
    return "review"


STATUS_FOR_VERDICT = {"pass": "passed", "review": "review", "fail": "stage2_fail"}


def clean_extras(raw: dict, description: str = "") -> dict:
    """The parts of the Stage 2 answer that never touch the verdict: a
    one-line company description, what the employer asks for (and whether
    the candidate has it), and at most a few notes on things no criterion
    covers. Anything claiming to quote the JD is dropped when the quote
    isn't there."""
    def quoted(item: dict) -> str | None | bool:
        quote = str(item.get("quote") or "").strip() or None
        if quote and description and not quote_in_text(quote, description):
            return False
        return quote

    employer = []
    for item in raw.get("employer_requirements") or []:
        if not isinstance(item, dict) or not str(item.get("label") or "").strip():
            continue
        quote = quoted(item)
        if not quote:  # what the employer asks for must be something the JD says
            continue
        fit = str(item.get("candidate") or "").strip().lower()
        employer.append({
            "kind": item.get("kind") if item.get("kind") in EMPLOYER_KINDS else "preferred",
            "label": str(item["label"]).strip(), "quote": quote,
            "candidate": fit if fit in CANDIDATE_FITS else "unclear",
            "why": str(item.get("why") or "").strip(),
        })

    notes = []
    for item in raw.get("notes") or []:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict) or not str(item.get("text") or "").strip():
            continue
        quote = quoted(item)
        if quote is False:
            continue
        notes.append({"text": str(item["text"]).strip(), "quote": quote})

    company_line = " ".join(str(raw.get("company_line") or "").split())
    return {"company_line": company_line or None, "employer": employer, "notes": notes[:MAX_NOTES]}


def summarize(judged: dict) -> str:
    """One line per deciding criterion — kept in seen.db's `reasoning`
    column so rejected-*.md and anything else that reads free text still
    has the gist."""
    lines = []
    for c in judged["criteria"]:
        decisive = (c["kind"] == "fail" and c["result"] in ("triggered", "unclear")) or \
                   (c["kind"] == "pass" and c["result"] in ("met", "unclear"))
        if decisive:
            lines.append(f"{c['label']}: {c['result'].replace('_', ' ')}" + (f" — {c['why']}" if c["why"] else ""))
    if judged["verdict"] == "fail" and not any(c["kind"] == "fail" and c["result"] == "triggered" for c in judged["criteria"]):
        lines.append("No pass criterion can be met.")
    return " | ".join(lines)


def render_criteria_list(definitions: list[dict]) -> str:
    """The `{criteria_list}` block of stage2_prompt.md."""
    lines = []
    for d in definitions:
        text = " ".join(str(d.get("text") or "").split())
        lines.append(f"- {d['id']} ({d['kind']}) {d['label']}" + (f": {text}" if text else ""))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("status", "is rubric-criteria.json present and current for rubric.md?"),
                        ("prompt-block", "print the criteria list for stage2_prompt.md's {criteria_list}")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--criteria", default=DEFAULT_CRITERIA_PATH)
        p.add_argument("--rubric", default="rubric.md")
    args = ap.parse_args()

    if args.cmd == "status":
        status = criteria_status(args.criteria, args.rubric)
        print(status)
        sys.exit(0 if status == "ok" else 1)
    elif args.cmd == "prompt-block":
        print(render_criteria_list(load_criteria(args.criteria)["criteria"]))


if __name__ == "__main__":
    main()
