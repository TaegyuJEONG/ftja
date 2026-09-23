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

After the three rules, jobs are grouped by tier1-block content hash
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


def filter_stage0(jobs: list[dict], criteria: dict) -> tuple[list[dict], dict]:
    languages = criteria.get("languages") or []
    exclude_keywords = criteria.get("exclude_keywords") or []
    tier1 = (criteria.get("keywords") or {}).get("tier1") or []

    passed = []
    dropped = {"language": 0, "no_tier1_hit": 0, "exclude_keyword": 0}
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
    args = ap.parse_args()

    with open(args.jobs) as f:
        jobs = json.load(f)
    with open(args.criteria) as f:
        criteria = json.load(f)

    passed, dropped = filter_stage0(jobs, criteria)

    out = json.dumps(passed, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
    else:
        print(out)
    print(f"Stage0: {len(jobs)} -> {len(passed)} (dropped: {dropped})", file=sys.stderr)


if __name__ == "__main__":
    main()
