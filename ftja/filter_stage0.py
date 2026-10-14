"""Stage 0 — deterministic filter, no LLM call.

Location is NOT filtered here — it's already deterministic at scrape time
(jobspy's `location` param in ftja/scrape.py), so a second
location check here would just be redundant with what the scrape already
guaranteed. (Caveat: if criteria.json's `location` is a single string like
"European Union", visa-sponsored locations mentioned in rubric.md but never
passed to scrape.py — e.g. Singapore, Dubai — are never scraped in the
first place. That's a scrape-config gap, not something this filter can fix.)

Titles are not filtered here either: the scrape keeps only postings whose
title contains the search term (title_matches_search, used by ftja/scrape.py).

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

When the Remote check is part of the run, a fifth rule follows: a job with
no remote keyword in its title or description is dropped (the check reads
only the sentences around those keywords), unless its setup says to keep
such jobs. And a run without Stage 1 skips rule 2.

After the rules, reposts are grouped the way JobSpyProject's
job_dedup.find_duplicate_groups does it, on two signals:
  - same company, same normalized title, and JD word overlap (Jaccard) of
    at least 0.85 — the same posting listed again, often for another city
  - identical tier1 blocks (ftja.text_blocks.block_signature), whatever
    the company — a job board reposting someone else's JD
Only one representative per group is kept in the output; the rest are
attached as `_duplicate_job_urls` (and `_duplicate_locations`) so Stage1/2
judge once per group instead of once per URL, and finalize.py can still
mark every alias URL as seen.
"""
import argparse
import json
import re
import sys
from collections import OrderedDict

from langdetect import detect, LangDetectException

from ftja.text_blocks import blocks_for_job, block_signature, keyword_matcher, stem, tokenize


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
        if key.split("-")[0] in _LANGUAGE_NAMES:
            key = key.split("-")[0]  # langdetect's zh-cn / zh-tw
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


JACCARD_THRESHOLD = 0.85
_TITLE_ABBREVIATIONS = ((r"\bsr\b", "senior"), (r"\bjr\b", "junior"), (r"\bmgr\b", "manager"))


def _normalize_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"[^\w\s]|_", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _normalize_title(title: str) -> str:
    t = _normalize_text(title)
    for pattern, full in _TITLE_ABBREVIATIONS:
        t = re.sub(pattern, full, t)
    return t


def dedupe_by_content(jobs: list[dict]) -> tuple[list[dict], int]:
    """Group reposts of the same JD (see the module docstring for the two
    signals). Keeps the first job per group as the representative and
    attaches `_duplicate_job_urls` (the other URLs) and, when a repost names
    another place, `_duplicate_locations`."""
    parent = list(range(len(jobs)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # the earliest job stays the representative

    by_hash: dict[str, int] = {}
    by_title: dict[tuple, list[int]] = {}
    for i, job in enumerate(jobs):
        sig = job.get("_content_hash") or ""
        if sig:
            union(by_hash.setdefault(sig, i), i)
        key = (_normalize_text(job.get("company") or ""), _normalize_title(job.get("title") or ""))
        if all(key):
            by_title.setdefault(key, []).append(i)
    for members in by_title.values():
        if len(members) < 2:
            continue
        words = {i: set(_normalize_text(jobs[i].get("description") or "").split()) for i in members}
        for n, i in enumerate(members):
            for j in members[n + 1:]:
                if find(i) != find(j) and words[i] and words[j] \
                        and len(words[i] & words[j]) / len(words[i] | words[j]) >= JACCARD_THRESHOLD:
                    union(i, j)

    groups: "OrderedDict[int, list[dict]]" = OrderedDict()
    for i, job in enumerate(jobs):
        groups.setdefault(find(i), []).append(job)

    unique = []
    merged_count = 0
    for group in groups.values():
        rep = dict(group[0])
        if len(group) > 1:
            rep["_duplicate_job_urls"] = [j["job_url"] for j in group[1:]]
            others = list(dict.fromkeys(j.get("location") for j in group[1:]
                                        if j.get("location") and j.get("location") != rep.get("location")))
            if others:
                rep["_duplicate_locations"] = others
            merged_count += len(group) - 1
        unique.append(rep)
    return unique, merged_count


def title_matches_search(job: dict, search_terms: list[str]) -> bool:
    """Does the job's title contain the search term that found it?

    LinkedIn's search matches by meaning, so a search for "Entrepreneur In
    Residence" also returns "Co-Founder" and "Strategy Lead". This is the
    check LinkedIn does not do. A job is held to the term or terms whose
    search returned it (`_search_terms`, set when the scrapes are merged):
    one found by "AI Specialist" has to say that in its title, however well
    it would have matched "Product Manager". Matching is the tier1 keywords'
    (whole words, word forms, any case), so "Product Manager" accepts
    "Senior Product Managers"; quote marks around a term are ignored."""
    terms = job.get("_search_terms") or search_terms
    title = job.get("title") or ""
    tokens = tokenize(title)
    stems = [stem(t) for t in tokens]
    for term in terms:
        match = keyword_matcher(str(term).strip().strip('"\u201c\u201d\u2018\u2019\''))
        if match and match(title, tokens, stems):
            return True
    return False


def filter_stage0(jobs: list[dict], criteria: dict, dropped_jobs: list[dict] | None = None,
                  remote: dict | None = None, stage1: bool = True) -> tuple[list[dict], dict]:
    """`dropped_jobs`, when given, collects the rule-4 drops with the sentence
    that triggered each one (the other rules are plain counts).

    `remote` is the Remote check's setup when that stage is part of the run
    (see ftja.remote): each kept job then carries `_remote_blocks`, the
    sentences around its remote keywords, and a job with none is dropped
    here unless the setup says to keep it. `stage1=False` is a run without
    Stage 1: a job no longer needs a tier-1 keyword (rule 2), though its
    tier-1 sentences are still worked out, for a run that is continued into
    Stage 1 later. With neither argument this is the filter as it always was."""
    languages = criteria.get("languages") or []
    exclude_keywords = criteria.get("exclude_keywords") or []
    tier1 = (criteria.get("keywords") or {}).get("tier1") or []
    marked = (criteria.get("keywords") or {}).get("tier1_language")
    if marked:  # a keyword of a language the candidate no longer reads is switched off (see ftja.keywords)
        from ftja.keywords import active
        tier1 = active(tier1, marked, languages)

    passed = []
    dropped = {"language": 0, "no_tier1_hit": 0, "exclude_keyword": 0, "language_requirement": 0}
    if not stage1:
        del dropped["no_tier1_hit"]
    if remote is not None:
        from ftja.remote import blocks_for as remote_blocks_for
        if remote.get("drop_without_keywords", True):
            dropped["no_remote_keyword"] = 0
    for job in jobs:
        if not passes_language(job, languages):
            dropped["language"] += 1
            continue
        blocks = blocks_for_job(job.get("description", ""), tier1)
        if stage1 and not blocks:
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
        remote_blocks = remote_blocks_for(job, remote) if remote is not None else None
        if remote is not None and not remote_blocks and "no_remote_keyword" in dropped:
            dropped["no_remote_keyword"] += 1
            continue  # nothing about remote work anywhere in it: not sent to the model
        job = dict(job)
        job["_t1_blocks"] = blocks
        job["_content_hash"] = block_signature(blocks)
        if remote is not None:
            job["_remote_blocks"] = remote_blocks
            if not blocks:  # a run without Stage 1 keywords still groups reposts by what the model will read
                job["_content_hash"] = block_signature(remote_blocks)
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

    with open(args.jobs, encoding="utf-8-sig") as f:
        jobs = json.load(f)
    with open(args.criteria, encoding="utf-8-sig") as f:
        criteria = json.load(f)

    dropped_jobs: list[dict] = []
    passed, dropped = filter_stage0(jobs, criteria, dropped_jobs)
    if args.dropped_out:
        with open(args.dropped_out, "w", encoding="utf-8") as f:
            json.dump(dropped_jobs, f, ensure_ascii=False, indent=2)

    out = json.dumps(passed, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)
    print(f"Stage0: {len(jobs)} -> {len(passed)} (dropped: {dropped})", file=sys.stderr)


if __name__ == "__main__":
    main()
