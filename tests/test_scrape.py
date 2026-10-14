import pandas as pd

from ftja import scrape as scrape_mod

TITLES = ["Senior Product Manager", "Strategy Lead", "Product Managers (m/f/d)", "Co-Founder"]


def _linkedin(monkeypatch, pages):
    """jobspy stands in for LinkedIn: `pages(offset)` -> the titles of that page of search results."""
    calls, fetched = [], []

    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"job_url": f"https://www.linkedin.com/jobs/view/{kwargs['offset']}-{n}", "title": title,
                              "description": None} for n, title in enumerate(pages(kwargs["offset"]))])

    def fetcher():
        def details(job):
            fetched.append(job["title"])
            return {"description": "the posting", "job_level": "director"}
        return details
    monkeypatch.setattr(scrape_mod, "scrape_jobs", scrape_jobs)
    monkeypatch.setattr(scrape_mod, "_details_fetcher", fetcher)
    monkeypatch.setattr(scrape_mod, "SEARCH_PAGE_GAP", (0, 0))
    return calls, fetched


def test_only_postings_whose_title_contains_the_search_term_are_scraped(monkeypatch):
    calls, fetched = _linkedin(monkeypatch, lambda offset: [] if offset else TITLES)
    skipped, counts = [], []
    jobs = scrape_mod.scrape('"Product Manager"', "France", skipped=skipped, on_batch=lambda count, read: counts.append((count, read)))
    assert calls[0]["linkedin_fetch_description"] is False  # jobspy would read every posting's page
    assert [j["title"] for j in jobs] == [TITLES[0], TITLES[2]] == fetched  # only these pages were read
    assert [j["description"] for j in jobs] == ["the posting", "the posting"] and jobs[0]["job_level"] == "director"
    assert skipped == ["Strategy Lead", "Co-Founder"] and counts == [(2, 4)]  # 2 scraped, among 4 search results


def test_a_search_is_read_until_its_titles_stop_matching(monkeypatch):
    monkeypatch.setattr(scrape_mod, "NO_MATCH_PAGES", 3)
    # two pages with matches, one without, one with again, then nothing that matches
    calls, _ = _linkedin(monkeypatch, lambda offset: TITLES if offset in (0, 10, 30) else ["Strategy Lead"])
    jobs = scrape_mod.scrape("Product Manager", "France")
    assert len(jobs) == 6  # there is no number of results to ask for
    assert [c["offset"] for c in calls] == [0, 10, 20, 30, 40, 50, 60]  # one page without a match is not the end; three in a row are

    monkeypatch.setattr(scrape_mod, "SEARCH_RESULTS_CAP", 40)
    calls, _ = _linkedin(monkeypatch, lambda offset: TITLES)
    assert len(scrape_mod.scrape("Product Manager", "France")) == 8
    assert [c["offset"] for c in calls] == [0, 10, 20, 30]  # LinkedIn returns nothing past its cap
