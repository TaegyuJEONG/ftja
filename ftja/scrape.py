"""LinkedIn guest scraping via jobspy. No login, no session — ported from
JobSpyProject's app_db.run_db_scraper throttle pattern (batch_size=10 with an
offset loop), which is the config that was already proven not to trip
LinkedIn's rate limiting in production. Do not raise batch_size without
re-validating against a live run.
"""
import argparse
import json
import sys

from jobspy import scrape_jobs

SITES = ["linkedin"]
BATCH_SIZE = 10


def scrape(search_term: str, location: str,
           results_wanted: int = 100, hours_old: int = 72, job_type: str = "",
           on_batch=None) -> list[dict]:
    """Scrape up to `results_wanted` LinkedIn listings for one search term.

    Batches in groups of BATCH_SIZE with an offset loop (throttle, not a
    jobspy requirement) and stops early if a batch comes back empty.

    The search term is sent as typed. There is no exact-phrase option:
    quoting a term does not make LinkedIn's logged-out search match the
    phrase. Measured on 20 postings per search, `"venture builder"` in
    quotes returned none that contain that phrase, and `"founder in
    residence"` returned 3, next to titles like "Entrepreneur In Residence".
    Its search matches by meaning. A phrase that has to be in the posting is
    enforced by Stage 0's tier1 keywords, which read the text.

    There is no remote-only option. LinkedIn ignores its workplace filter
    (f_WT) for requests that are not logged in, which is what this scraper
    sends: measured, the same search returned the same postings with the
    filter set to remote, hybrid, on-site or left out. Whether a role is
    remote is judged from the posting's text, by the keywords and the rubric.

    on_batch(count_so_far), if given, is called after every batch — the
    live view's "scraped so far" number.
    """
    search_term = search_term.strip()
    jobs: list[dict] = []

    for offset in range(0, results_wanted, BATCH_SIZE):
        num_to_fetch = min(BATCH_SIZE, results_wanted - offset)
        kwargs = {
            "site_name": SITES,
            "search_term": search_term,
            "location": location,
            "results_wanted": num_to_fetch,
            "hours_old": hours_old,
            "offset": offset,
            "linkedin_fetch_description": True,
            "verbose": 1,
        }
        if job_type and job_type.strip():
            kwargs["job_type"] = job_type

        df = scrape_jobs(**kwargs)
        if df is None or df.empty:
            break

        jobs.extend(df.to_dict(orient="records"))
        if on_batch:
            on_batch(len(jobs))

    return jobs


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
        return str(v)

    return [{k: _clean(v) for k, v in job.items()} for job in jobs]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search-term", required=True)
    ap.add_argument("--location", default="")
    ap.add_argument("--results-wanted", type=int, default=100)
    ap.add_argument("--hours-old", type=int, default=72)
    ap.add_argument("--job-type", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    jobs = scrape(
        search_term=args.search_term,
        location=args.location,
        results_wanted=args.results_wanted,
        hours_old=args.hours_old,
        job_type=args.job_type,
    )

    cleaned = clean_jobs(jobs)
    out = json.dumps(cleaned, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Wrote {len(cleaned)} jobs to {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
