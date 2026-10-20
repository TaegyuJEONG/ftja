"""Job board scraping via JobSpy. LinkedIn needs no login or session — ported from
JobSpyProject's app_db.run_db_scraper throttle pattern (batch_size=10 with an
offset loop), which is the config that was already proven not to trip
LinkedIn's rate limiting in production. Do not raise batch_size without
re-validating against a live run.
"""
import argparse
import json
import logging
import random
import sys
import time

from jobspy import scrape_jobs
from ftja.geography import SOURCES

BATCH_SIZE = 10
# Seconds between two search requests, jobspy's own wait between search
# pages (its delay and band_delay). With 2s, a search whose titles all
# missed got a 429 on its third page (measured 2026-10-13).
SEARCH_PAGE_GAP = (3.0, 7.0)
SEARCH_RESULTS_CAP = 1000  # LinkedIn returns no search results past this; jobspy stops there too
# This many batches in a row without a matching title end the search.
# Matches are not all at the front: measured 2026-10-13 over the first 40
# batches, "AI Specialist" (European Union, a week) had 9 matching titles
# with up to 12 batches between two of them, and "Product Manager" (24
# hours) still had 4 in the 40th. A search with no match at all spends
# about a minute and a half finding that out.
NO_MATCH_PAGES = 15


class ScrapeRateLimited(RuntimeError):
    """The provider stopped returning search pages because it throttled us."""


class _LinkedInSearchErrors(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.rate_limited = False

    def emit(self, record):
        message = record.getMessage()
        if "LinkedIn response status code 429" in message:
            self.rate_limited = True


def scrape(search_term: str, location: str, hours_old: int = 72, job_type: str = "",
           on_batch=None, skipped: list | None = None, site: str = "linkedin",
           country_indeed: str = "") -> list[dict]:
    """Scrape the LinkedIn listings whose title contains `search_term`.

    LinkedIn's logged-out search matches by meaning: a search for
    "Entrepreneur In Residence" also returns "Co-Founder" and "Strategy
    Lead", and quoting the term changes nothing (measured on 20 postings per
    search, `"venture builder"` in quotes returned none that contain that
    phrase). So the title is checked here, on the search results, which
    already carry it (see title_matches_search): a posting whose title does
    not contain the term is left out as if the search had not returned it,
    and its page is never fetched. jobspy has no such option. It fetches
    every posting's page, one request each, which is most of a scrape's
    time.

    There is no number of results to ask for. The search is read in batches
    of BATCH_SIZE (the throttle, not a jobspy requirement) until a batch
    comes back empty, NO_MATCH_PAGES batches in a row hold no matching
    title, or SEARCH_RESULTS_CAP is reached. The pages that are skipped no
    longer space the search requests out, so those are kept SEARCH_PAGE_GAP
    apart.

    There is no remote-only option. LinkedIn ignores its workplace filter
    (f_WT) for requests that are not logged in, which is what this scraper
    sends: measured, the same search returned the same postings with the
    filter set to remote, hybrid, on-site or left out. Whether a role is
    remote is judged from the posting's text, by the keywords and the rubric.

    on_batch(count_so_far, read_so_far), if given, is called after every
    batch — the live view's "scraped so far" number, and how many search
    results it was found among. `skipped`, if given, collects the titles
    that were left out.
    """
    from ftja.filter_stage0 import title_matches_search

    search_term = search_term.strip()
    jobs: list[dict] = []
    if site not in SOURCES:
        raise ValueError(f"Unsupported source: {site}")
    if site in ("indeed", "glassdoor") and not country_indeed:
        raise ValueError(f"{site.title()} requires an explicit country_indeed")
    if site != "linkedin":
        # A bounded request per source/location avoids LinkedIn's slow page
        # throttle. Other boards page internally up to this limit.
        query = (search_term if search_term.startswith('"') and search_term.endswith('"')
                 else f'"{search_term}"') if site in ("indeed", "glassdoor") else search_term
        kwargs = {"site_name": [site], "search_term": query, "location": location,
                  "hours_old": hours_old, "verbose": 1}
        kwargs["results_wanted"] = 100
        if site in ("indeed", "glassdoor"):
            kwargs["country_indeed"] = country_indeed
        if site in ("glassdoor", "zip_recruiter", "bayt", "naukri", "bdjobs", "hellowork"):
            kwargs["fetch_description"] = True
        if job_type and job_type.strip():
            kwargs["job_type"] = job_type
        df = scrape_jobs(**kwargs)
        batch = [] if df is None or df.empty else df.to_dict(orient="records")
        matched = [job for job in batch if title_matches_search({"title": job.get("title")}, [search_term])]
        if skipped is not None:
            skipped.extend(str(job.get("title") or "") for job in batch if job not in matched)
        for job in matched:
            job["site"] = site
            job["sources"] = [site]
        if on_batch:
            on_batch(len(matched), len(batch))
        return matched
    details = _details_fetcher() if site == "linkedin" else None
    seen_urls: set = set()
    pages_without_match = 0

    for offset in range(0, SEARCH_RESULTS_CAP, BATCH_SIZE):
        started = time.monotonic()
        kwargs = {
            "site_name": [site],
            "search_term": search_term,
            "location": location,
            "results_wanted": BATCH_SIZE,
            "hours_old": hours_old,
            "offset": offset,
            "verbose": 1,
        }
        if site == "linkedin":
            kwargs["linkedin_fetch_description"] = False
        else:
            kwargs["country_indeed"] = country_indeed
        if job_type and job_type.strip():
            kwargs["job_type"] = job_type

        logger = logging.getLogger("JobSpy:LinkedIn")
        errors = _LinkedInSearchErrors()
        logger.addHandler(errors)
        try:
            df = scrape_jobs(**kwargs)
        finally:
            logger.removeHandler(errors)
        if errors.rate_limited:
            raise ScrapeRateLimited(
                f"LinkedIn limited the search at offset {offset}; this search is incomplete. "
                "Retry after the limit clears."
            )
        if df is None or df.empty:
            break

        batch = [job for job in df.to_dict(orient="records") if job.get("job_url") not in seen_urls]
        seen_urls.update(job.get("job_url") for job in batch)
        matched = [job for job in batch if title_matches_search({"title": job.get("title")}, [search_term])]
        if skipped is not None:
            skipped.extend(str(job.get("title") or "") for job in batch if job not in matched)
        for job in matched:
            job["site"] = site
            job["sources"] = [site]
            if details:
                job.update(details(job))
        jobs.extend(matched)
        if on_batch:
            on_batch(len(jobs), len(seen_urls))
        pages_without_match = 0 if matched else pages_without_match + 1
        if pages_without_match >= NO_MATCH_PAGES:
            break
        if offset + BATCH_SIZE < SEARCH_RESULTS_CAP:
            time.sleep(max(0.0, random.uniform(*SEARCH_PAGE_GAP) - (time.monotonic() - started)))

    return jobs


def _details_fetcher():
    """Fetch details only for LinkedIn titles we kept, using pinned JobSpy."""
    from jobspy.linkedin import LinkedIn
    from jobspy.model import ScraperInput

    scraper = LinkedIn()
    scraper.scraper_input = ScraperInput()  # markdown descriptions, as scrape_jobs asks for

    def details(job: dict) -> dict:
        job_id = str(job.get("job_url") or "").rstrip("/").split("/")[-1]
        found = scraper._fetch_details(job_id) if job_id else {}
        if not found:
            return {}
        return {**found,
                "job_type": ", ".join(t.value[0] for t in found["job_type"]) if found.get("job_type") else None,
                "emails": ", ".join(found["emails"]) if found.get("emails") else None}

    return details


def clean_jobs(jobs: list[dict]) -> list[dict]:
    """NaN/NaT from pandas isn't valid JSON — stringify anything non-primitive."""
    def _clean(v):
        if v is None:
            return None
        try:
            if v != v:  # NaN check
                return None
        except Exception:
            pass
        if isinstance(v, (str, int, float, bool)):
            return v
        if isinstance(v, (list, tuple)):
            return [_clean(item) for item in v]
        if isinstance(v, dict):
            return {str(key): _clean(item) for key, item in v.items()}
        return str(v)

    return [{k: _clean(v) for k, v in job.items()} for job in jobs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search-term", required=True)
    ap.add_argument("--location", default="")
    ap.add_argument("--hours-old", type=int, default=72)
    ap.add_argument("--job-type", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    jobs = scrape(
        search_term=args.search_term,
        location=args.location,
        hours_old=args.hours_old,
        job_type=args.job_type,
    )

    cleaned = clean_jobs(jobs)
    out = json.dumps(cleaned, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Wrote {len(cleaned)} jobs to {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
