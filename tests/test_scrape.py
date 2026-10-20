import pandas as pd
import logging
import pytest

from ftja import scrape as scrape_mod

TITLES = ["Senior Product Manager", "Strategy Lead", "Product Managers (m/f/d)", "Co-Founder"]


def test_clean_jobs_preserves_source_lists():
    assert scrape_mod.clean_jobs([{"site": "linkedin", "sources": ["linkedin", "indeed"]}])[0]["sources"] == ["linkedin", "indeed"]


def test_linkedin_rate_limit_is_not_treated_as_end_of_results(monkeypatch):
    def limited(**kwargs):
        logging.getLogger("JobSpy:LinkedIn").error("LinkedIn response status code 429")
        return pd.DataFrame()

    monkeypatch.setattr(scrape_mod, "scrape_jobs", limited)
    monkeypatch.setattr(scrape_mod, "_details_fetcher", lambda: None)
    with pytest.raises(scrape_mod.ScrapeRateLimited, match="incomplete"):
        scrape_mod.scrape("Product Manager", "USA")


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


def test_linkedin_details_adapter_uses_jobspy_130_fetcher(monkeypatch):
    from jobspy import linkedin
    class FakeLinkedIn:
        def _fetch_details(self, job_id):
            assert job_id == "123"
            return {"description": "Build the product", "emails": ["hiring@example.com"],
                    "job_type": None, "job_level": "senior"}
    monkeypatch.setattr(linkedin, "LinkedIn", FakeLinkedIn)
    details = scrape_mod._details_fetcher()({"job_url": "https://www.linkedin.com/jobs/view/123"})
    assert details["description"] == "Build the product"
    assert details["emails"] == "hiring@example.com"
