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


def quote_search_term(term: str) -> str:
    term = term.strip()
    if term and not (term.startswith('"') and term.endswith('"')):
        term = f'"{term}"'
    return term


def scrape(search_term: str, location: str, is_remote: bool = True,
           results_wanted: int = 100, hours_old: int = 72, job_type: str = "",
           exact_phrase: bool = True) -> list[dict]:
    """Scrape up to `results_wanted` LinkedIn listings for one search term.

    Batches in groups of BATCH_SIZE with an offset loop (throttle, not a
    jobspy requirement) and stops early if a batch comes back empty.

    exact_phrase controls whether search_term is quoted for LinkedIn (forces
    exact-phrase matching, e.g. "Founder in Residence" won't match a JD that
    only has the words scattered apart) vs sent as-is (broader, OR-ish
    matching). Controlled by criteria.json's exact_phrase_search.
    """
    if exact_phrase:
        search_term = quote_search_term(search_term)
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
            "is_remote": is_remote,
            "linkedin_fetch_description": True,
            "verbose": 1,
        }
        if job_type and job_type.strip():
            kwargs["job_type"] = job_type

        df = scrape_jobs(**kwargs)
        if df is None or df.empty:
            break

        jobs.extend(df.to_dict(orient="records"))

    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search-term", required=True)
    ap.add_argument("--location", default="")
    ap.add_argument("--is-remote", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--results-wanted", type=int, default=100)
    ap.add_argument("--hours-old", type=int, default=72)
    ap.add_argument("--job-type", default="")
    ap.add_argument("--exact-phrase", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    jobs = scrape(
        search_term=args.search_term,
        location=args.location,
        is_remote=args.is_remote,
        results_wanted=args.results_wanted,
        hours_old=args.hours_old,
        job_type=args.job_type,
        exact_phrase=args.exact_phrase,
    )

    # NaN/NaT from pandas isn't valid JSON — stringify anything non-primitive.
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

    clean_jobs = [{k: _clean(v) for k, v in job.items()} for job in jobs]

    out = json.dumps(clean_jobs, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out)
        print(f"Wrote {len(clean_jobs)} jobs to {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
