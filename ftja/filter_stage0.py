"""Stage 0 — deterministic filter, no LLM call.

Location is NOT filtered here — it's already deterministic at scrape time
(jobspy's `location`/`is_remote` params in ftja/scrape.py), so a second
location check here would just be redundant with what the scrape already
guaranteed. (Caveat: if criteria.json's `location` is a single string like
"European Union", visa-sponsored locations mentioned in rubric.md but never
passed to scrape.py — e.g. Singapore, Dubai — are never scraped in the
first place. That's a scrape-config gap, not something this filter can fix.)

Keeps a scraped job only if, in order:
  1. the JD's own written language is one the candidate reads (criteria.languages)
  2. at least one tier1 keyword appears in the description
  3. none of criteria.exclude_keywords appear in the description
  4. the JD does not state that a language outside criteria.languages is required

Rule 1 exists because a JD can require no explicit language skill and still
be unusable — it's simply written in a language the candidate doesn't read.
Uses langdetect (offline, no network) on the description text itself.

Rule 2 mirrors JobSpyProject's Phase 1: a job with no tier1 hit fails
without ever reaching the LLM (job_evaluator.py:968-1010's "no tier1 hit ->
auto fail" rule) — it also means Stage 1 never needs to handle the
empty-blocks case, it just never sees those jobs.

Rule 3 is a deliberately blunt bare-word exclusion — no "fluent in X" or "X
required" phrase matching, just "does this word appear anywhere." Simpler
to reason about and per-user configurable for anything (a language name, a
tool, a company, whatever the candidate wants gated out).

Rule 4 is the phrase matching rule 3 leaves out, for one case only: a JD
written in a readable language that requires fluency in another one
("Fluent in German and English"). A bare language name in exclude_keywords
cannot do this job. Measured on 2,016 jobs that reached Stage 1, dropping
every JD that names another language would have lost 12 of 75 final passes
("the German market", "German helps but isn't required"). Rule 4 drops a job
only when a sentence names a language outside criteria.languages next to a
requirement cue and carries no softening word; anything ambiguous is kept,
because Stage 2 still checks language requirements on the full JD. Dropped
jobs are not written to seen.db, so changing criteria.languages takes effect
on the next run; --dropped-out saves them with the sentence that triggered
the drop, so the rule can be audited.

After the four rules, jobs are grouped by tier1-block content hash
(ftja.text_blocks.block_signature, ported from JobSpyProject's
text_index.t1_block_signature) — the same JD reposted under a different
LinkedIn URL hashes identically. Only one representative per group is kept
in the output; the rest are attached as `_duplicate_job_urls` so Stage1/2
judge once per group instead of once per URL, and finalize.py can still
mark every alias URL as seen. Measured impact in JobSpyProject was small
(~2% of jobs) — a cleanup, not a major cost lever.
"""
import argparse
import json
import re
import sys
from collections import OrderedDict

from langdetect import detect, LangDetectException

from ftja.text_blocks import blocks_for_job, block_signature


def passes_language(job: dict, languages: list[str]) -> bool:
    if not languages:
        return True
    desc = job.get("description") or ""
    if len(desc.strip()) < 20:
        return True  # too short to detect reliably -> don't drop on a guess
    try:
        detected = detect(desc)
    except LangDetectException:
        return True  # detector failure -> don't silently drop, let it through
    return detected in languages


def passes_exclude(job: dict, exclude_keywords: list[str]) -> bool:
    if not exclude_keywords:
        return True
    desc = (job.get("description") or "").lower()
    title = (job.get("title") or "").lower()
    haystack = f"{title}\n{desc}"
    return not any(kw.lower() in haystack for kw in exclude_keywords)

# ISO 639-1 code -> the English names a JD uses for that language.
_LANGUAGE_NAMES = {
    "en": ["English"], "fr": ["French"], "de": ["German"], "nl": ["Dutch", "Flemish"],
    "it": ["Italian"], "es": ["Spanish", "Castilian"], "pt": ["Portuguese"], "pl": ["Polish"],
    "cs": ["Czech"], "sk": ["Slovak"], "hr": ["Croatian"], "sr": ["Serbian"],
    "sl": ["Slovenian", "Slovene"], "sv": ["Swedish"], "da": ["Danish"], "no": ["Norwegian"],
    "fi": ["Finnish"], "el": ["Greek"], "ro": ["Romanian"], "hu": ["Hungarian"],
    "bg": ["Bulgarian"], "lt": ["Lithuanian"], "lv": ["Latvian"], "et": ["Estonian"],
    "tr": ["Turkish"], "uk": ["Ukrainian"], "ru": ["Russian"], "ca": ["Catalan"],
    "he": ["Hebrew"], "ar": ["Arabic"], "ja": ["Japanese"], "ko": ["Korean"],
    "zh": ["Chinese", "Mandarin", "Cantonese"], "hi": ["Hindi"],
}
_REQUIREMENT_CUE = (
    r"fluen(?:t|cy)|proficien(?:t|cy)|(?<!AI[- ])(?<!cloud[- ])(?<!digital[- ])\bnative\b|bilingual|mother tongue|\bC[12]\b"
    r"|(?:business|professional|working)[- ]level|written and (?:spoken|verbal)"
    r"|(?:spoken|verbal) and written|\bspeakers?\b|\bspeak\b|required|requirement"
    r"|\bmust\b|mandatory|essential|non\\?-negotiable|command of|(?:knowledge of|skills in) (?-i:[A-Z])"
)
_SOFTENER = (
    r"\bplus\b|bonus|nice[- ]to[- ]have|advantage|preferred|preferably|desirable|desired"
    r"|\basset\b|beneficial|ideally|appreciated|welcome|\bhelps?\b|helpful|optional"
    r"|n[o\u2019']t (?:be )?(?:required|necessary|needed|mandatory|essential|expected|a must|something)"
)
_NOT_A_LANGUAGE_NOUN = (
    r"markets?|sector|industry|customers?|clients?|users?|region|law|regulations?|legislation"
    r"|compan(?:y|ies)|teams?|office|government|authorit(?:y|ies)|entity|subsidiary"
)
# How far a requirement cue may sit from the language name it applies to.
_CUE_WINDOW = 40
_SENTENCE_RE = re.compile(r"[^.;\n]+")


def _language_codes(languages: list[str]) -> set[str]:
    """criteria.languages holds ISO codes, but the onboarding card also
    accepts names ("English"), so resolve both."""
    by_name = {name.lower(): code for code, names in _LANGUAGE_NAMES.items() for name in names}
    codes = set()
    for lang in languages:
        key = (lang or "").strip().lower()
        if key in _LANGUAGE_NAMES:
            codes.add(key)
        elif key in by_name:
            codes.add(by_name[key])
    return codes


def find_language_requirement(job: dict, languages: list[str]) -> str:
    """The first sentence stating that a language outside `languages` is
    required, or "" if there is none. Language names are matched
    case-sensitively, so "polish the roadmap" is not Polish."""
    known = _language_codes(languages)
    if not known:
        return ""  # nothing resolvable to compare against -> never drop on a guess
    own = [n for c in known for n in _LANGUAGE_NAMES[c]]
    other = [n for c, names in _LANGUAGE_NAMES.items() if c not in known for n in names]
    # "the Dutch accounting market" names a place to sell, not a language.
    other_re = re.compile(r"\b(?:%s)\b(?!(?: [\w-]+){0,2} (?:%s)\b)" % ("|".join(other), _NOT_A_LANGUAGE_NOUN))
    own_alt = "|".join(own)
    # "Dutch or French" with French readable -> the candidate qualifies.
    alternative_re = re.compile(rf"\b(?:{own_alt})\b,? (?:and/)?or\b|\bor (?:{own_alt})\b|\beither\b")
    cue_re = re.compile(_REQUIREMENT_CUE, re.I)
    soft_re = re.compile(_SOFTENER, re.I)

    title = job.get("title") or ""
    if re.search(r"\b(?:%s)\\?[- ][Ss]peak(?:ing|er)\b" % "|".join(other), title):
        return title.strip()

    for sentence in _SENTENCE_RE.findall(f"{title}\n{job.get('description') or ''}"):
        if soft_re.search(sentence) or alternative_re.search(sentence):
            continue
        cues = [c.span() for c in cue_re.finditer(sentence)]
        for m in other_re.finditer(sentence):
            if any(end > m.start() - _CUE_WINDOW and start < m.end() + _CUE_WINDOW for start, end in cues):
                return sentence.strip()
    return ""


def dedupe_by_content(jobs: list[dict]) -> tuple[list[dict], int]:
    """Group jobs whose tier1 blocks hash identically (same JD, different
    LinkedIn URL). Keeps the first job per group as the representative and
    attaches `_duplicate_job_urls` (other URLs sharing that content)."""
    groups: "OrderedDict[str, list[dict]]" = OrderedDict()
    for job in jobs:
        sig = job.get("_content_hash") or ""
        groups.setdefault(sig, []).append(job)

    unique = []
    merged_count = 0
    for sig, group in groups.items():
        rep = dict(group[0])
        if len(group) > 1:
            rep["_duplicate_job_urls"] = [j["job_url"] for j in group[1:]]
            merged_count += len(group) - 1
        unique.append(rep)
    return unique, merged_count


def filter_stage0(jobs: list[dict], criteria: dict, dropped_jobs: list[dict] | None = None) -> tuple[list[dict], dict]:
    """`dropped_jobs`, when given, collects the rule-4 drops with the sentence
    that triggered each one (the other rules are plain counts)."""
    languages = criteria.get("languages") or []
    exclude_keywords = criteria.get("exclude_keywords") or []
    tier1 = (criteria.get("keywords") or {}).get("tier1") or []

    passed = []
    dropped = {"language": 0, "no_tier1_hit": 0, "exclude_keyword": 0, "language_requirement": 0}
    for job in jobs:
        if not passes_language(job, languages):
            dropped["language"] += 1
            continue
        blocks = blocks_for_job(job.get("description", ""), tier1)
        if not blocks:
            dropped["no_tier1_hit"] += 1
            continue  # no tier1 hit -> auto-fail, no LLM call
        if not passes_exclude(job, exclude_keywords):
            dropped["exclude_keyword"] += 1
            continue
        requirement = find_language_requirement(job, languages)
        if requirement:
            dropped["language_requirement"] += 1
            if dropped_jobs is not None:
                dropped_jobs.append({
                    "job_url": job.get("job_url"), "title": job.get("title"),
                    "company": job.get("company"), "location": job.get("location"),
                    "reason": "language_requirement", "evidence": requirement,
                })
            continue
        job = dict(job)
        job["_t1_blocks"] = blocks
        job["_content_hash"] = block_signature(blocks)
        passed.append(job)

    passed, merged_duplicates = dedupe_by_content(passed)
    dropped["duplicate_content"] = merged_duplicates
    return passed, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True, help="path to scraped jobs JSON")
    ap.add_argument("--criteria", required=True, help="path to criteria.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--dropped-out", default="",
                    help="optional path for the jobs dropped by the language-requirement rule, with evidence")
    args = ap.parse_args()

    with open(args.jobs) as f:
        jobs = json.load(f)
    with open(args.criteria) as f:
        criteria = json.load(f)

    dropped_jobs: list[dict] = []
    passed, dropped = filter_stage0(jobs, criteria, dropped_jobs)
    if args.dropped_out:
        with open(args.dropped_out, "w") as f:
            json.dump(dropped_jobs, f, ensure_ascii=False, indent=2)

    out = json.dumps(passed, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
    else:
        print(out)
    print(f"Stage0: {len(jobs)} -> {len(passed)} (dropped: {dropped})", file=sys.stderr)


if __name__ == "__main__":
    main()
