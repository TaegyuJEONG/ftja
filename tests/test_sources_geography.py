import pandas as pd
import pytest

from ftja.geography import EU, EUROPE, resolve_location, search_targets, unsupported_countries
from ftja import live, scrape as scraper
from ftja.filter_stage0 import dedupe_by_content
from ftja.server import _fetch_jobs, _location_warnings
from ftja.state import connect, mark_seen


def test_new_sources_route_only_supported_locations():
    eu = resolve_location("EU")
    assert search_targets(eu, "zip_recruiter") == []
    assert search_targets(eu, "naukri") == []
    assert search_targets(eu, "bdjobs") == []
    assert search_targets(eu, "hellowork") == [
        {"site": "hellowork", "location": "", "display_location": "France"}]
    bayt = search_targets(eu, "bayt")
    assert len(bayt) == 25
    assert set(unsupported_countries(eu, "bayt")) == {"Croatia", "Slovakia"}
    assert search_targets(resolve_location("New York, USA"), "zip_recruiter") == [
        {"site": "zip_recruiter", "location": "New York, USA"}]
    assert search_targets(resolve_location("Dubai, United Arab Emirates"), "bayt") == [
        {"site": "bayt", "location": "Dubai, United Arab Emirates"}]
    assert search_targets(resolve_location("Mumbai, India"), "naukri") == [
        {"site": "naukri", "location": "Mumbai", "display_location": "Mumbai, India"}]
    assert search_targets(resolve_location("Dhaka, Bangladesh"), "bdjobs") == [
        {"site": "bdjobs", "location": "Dhaka", "display_location": "Dhaka, Bangladesh"}]
    assert search_targets(resolve_location("Lyon, France"), "hellowork") == [
        {"site": "hellowork", "location": "Lyon", "display_location": "Lyon, France"}]
    assert "Israel" in unsupported_countries(resolve_location("MENA"), "bayt")


@pytest.mark.parametrize("site,location", [("zip_recruiter", "New York, USA"),
    ("bayt", "Dubai, United Arab Emirates"), ("naukri", "Mumbai"),
    ("bdjobs", "Dhaka"), ("hellowork", "Lyon")])
def test_new_sources_fetch_descriptions_and_keep_source(monkeypatch, site, location):
    calls = []
    def fake_scrape(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"job_url": f"https://{site}.example/1", "title": "Product Manager",
                              "company": "Acme", "location": location, "description": "Build products"}])
    monkeypatch.setattr(scraper, "scrape_jobs", fake_scrape)
    jobs = scraper.scrape("Product Manager", location, site=site)
    assert calls[0]["site_name"] == [site]
    assert calls[0]["location"] == location and calls[0]["fetch_description"] is True
    assert calls[0]["search_term"] == "Product Manager"
    assert "country_indeed" not in calls[0]
    assert jobs[0]["sources"] == [site]


def test_country_city_and_aliases_route_to_provider_arguments():
    assert resolve_location("European Union") == resolve_location("EU")
    assert search_targets(resolve_location("Paris, France"), "indeed") == [
        {"site": "indeed", "country_indeed": "France", "location": "Paris"}]
    assert search_targets(resolve_location("Paris, France"), "linkedin") == [
        {"site": "linkedin", "location": "Paris, France"}]
    assert search_targets(resolve_location("United States"), "indeed") == [
        {"site": "indeed", "country_indeed": "USA", "location": ""}]
    with pytest.raises(ValueError, match="Unknown location"):
        resolve_location("Pariss, Frnace")


def test_eu_and_mena_are_explicit_country_searches():
    eu = resolve_location("EU")
    linkedin = search_targets(eu, "linkedin")
    indeed = search_targets(eu, "indeed")
    assert linkedin == [{"site": "linkedin", "location": "European Union"}]
    assert all(t["country_indeed"] in EU and t["location"] == "" for t in indeed)
    assert "France" in {t["country_indeed"] for t in indeed}
    assert unsupported_countries(eu, "indeed") == []
    assert "Algeria" in unsupported_countries(resolve_location("MENA"), "indeed")


def test_plan_respects_source_selection_and_expands_regions():
    plan = live.searches({"search_terms": ["Product Manager"], "locations": ["EU", "Paris, France"],
                          "sites": ["linkedin", "indeed"]})
    assert len(plan) == 1 + len(search_targets(resolve_location("EU"), "indeed")) + 2
    assert plan[-1]["site"] == "indeed" and plan[-1]["location"] == "Paris"
    assert {p["site"] for p in plan} == {"linkedin", "indeed"}


def test_glassdoor_city_country_and_region_use_country_autocomplete():
    assert search_targets(resolve_location("Paris, France"), "glassdoor") == [
        {"site": "glassdoor", "country_indeed": "France", "location": "Paris, France"}]
    assert search_targets(resolve_location("United States"), "glassdoor") == [
        {"site": "glassdoor", "country_indeed": "USA", "location": "USA"}]
    eu = search_targets(resolve_location("European Union"), "glassdoor")
    assert len(eu) == 27
    assert unsupported_countries(resolve_location("EU"), "glassdoor") == []
    assert {t["country_indeed"] for t in eu if t["location"] == "Finland"} == {"USA"}
    assert {t["country_indeed"] for t in eu if t["location"] == "Malta"} == {"France"}
    assert search_targets(resolve_location("Switzerland"), "glassdoor") == [
        {"site": "glassdoor", "country_indeed": "Switzerland", "location": "Switzerland"}]
    assert "Switzerland" not in EU and "Switzerland" in EUROPE
    europe = search_targets(resolve_location("Europe"), "glassdoor")
    assert len(europe) == 42
    assert any(t["location"] == "Switzerland" for t in europe)
    assert set(unsupported_countries(resolve_location("Europe"), "glassdoor")) == {
        "Kosovo", "Montenegro", "North Macedonia", "San Marino"}
    assert search_targets(resolve_location("San Marino"), "glassdoor") == []
    plan = live.searches({"search_terms": ["Product Manager"], "locations": ["EU"],
                          "sites": ["linkedin", "indeed", "glassdoor"]})
    assert {p["site"] for p in plan} == {"linkedin", "indeed", "glassdoor"}
    assert len(plan) == 1 + len(search_targets(resolve_location("EU"), "indeed")) + len(eu)


def test_glassdoor_eu_coverage_and_europe_warnings():
    criteria = {"search_terms": ["Product Manager"], "locations": ["EU"],
                "sites": ["linkedin", "indeed", "glassdoor"], "hours_old": 24}
    warnings = _location_warnings(criteria)
    assert not any("Glassdoor searches" in warning for warning in warnings)
    assert len(live.searches(criteria)) == 55
    europe_warnings = _location_warnings({**criteria, "locations": ["Europe"]})
    assert any("Glassdoor scraper searches" in warning for warning in europe_warnings)


def test_unconfigured_board_locations_are_grouped_without_claiming_site_absence():
    warnings = _location_warnings({"search_terms": ["Product Manager"],
                                   "locations": ["France", "Germany", "USA"],
                                   "sites": ["zip_recruiter", "naukri", "bdjobs", "hellowork"],
                                   "hours_old": 24})
    zip_warnings = [warning for warning in warnings if "ZipRecruiter" in warning]
    assert len(zip_warnings) == 1
    assert "France, Germany" in zip_warnings[0]
    assert all("does not cover" not in warning for warning in warnings)
    assert all("This does not mean the job board has no listings there" in warning
               for warning in warnings if "not configured" in warning)


def test_glassdoor_fetches_with_explicit_country_and_keeps_source(monkeypatch):
    calls = []
    def fake_scrape(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"job_url": "https://www.glassdoor.fr/job-listing/j?jl=123",
                              "title": "Product Manager", "company": "Acme",
                              "location": "Paris", "description": "Product strategy"}])
    monkeypatch.setattr(scraper, "scrape_jobs", fake_scrape)
    jobs = scraper.scrape("Product Manager", "Paris, France", site="glassdoor", country_indeed="France")
    assert calls[0]["site_name"] == ["glassdoor"]
    assert calls[0]["country_indeed"] == "France" and calls[0]["location"] == "Paris, France"
    assert calls[0]["results_wanted"] == 100 and calls[0]["search_term"] == '"Product Manager"'
    assert calls[0]["fetch_description"] is True
    assert jobs[0]["sources"] == ["glassdoor"]
    with pytest.raises(ValueError, match="explicit country"):
        scraper.scrape("Product Manager", "Paris, France", site="glassdoor")


def test_indeed_fetches_with_explicit_country_and_keeps_source(monkeypatch):
    calls = []
    def fake_scrape(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([{"job_url": "https://indeed.com/viewjob?jk=123",
                              "title": "Product Manager", "company": "Acme",
                              "location": "Paris", "description": "Product strategy"}])
    monkeypatch.setattr(scraper, "scrape_jobs", fake_scrape)
    monkeypatch.setattr(scraper, "SEARCH_PAGE_GAP", (0, 0))
    jobs = scraper.scrape("Product Manager", "Paris", site="indeed", country_indeed="France")
    assert calls[0]["site_name"] == ["indeed"] and calls[0]["country_indeed"] == "France"
    assert calls[0]["location"] == "Paris" and "linkedin_fetch_description" not in calls[0]
    assert calls[0]["search_term"] == '"Product Manager"' and calls[0]["results_wanted"] == 100
    assert len(calls) == 1
    assert jobs[0]["sources"] == ["indeed"]
    with pytest.raises(ValueError, match="explicit country"):
        scraper.scrape("Product Manager", "Paris", site="indeed")


def test_cross_source_duplicate_retains_both_sources_and_distinct_roles():
    same = " ".join(f"word{i}" for i in range(100))
    jobs = [
        {"job_url": "https://linkedin.com/jobs/view/1", "site": "linkedin", "sources": ["linkedin"],
         "title": "Sr. Product Manager", "company": "Acme", "location": "Paris", "description": same, "_content_hash": "a"},
        {"job_url": "https://indeed.com/viewjob?jk=1", "site": "indeed", "sources": ["indeed"],
         "title": "Senior Product Manager", "company": "Acme", "location": "Lyon", "description": same + " extra", "_content_hash": "b"},
        {"job_url": "https://indeed.com/viewjob?jk=2", "site": "indeed", "sources": ["indeed"],
         "title": "Senior Product Manager", "company": "Acme", "location": "Paris", "description": "a different role", "_content_hash": "c"},
    ]
    unique, merged = dedupe_by_content(jobs)
    assert merged == 1 and len(unique) == 2
    assert unique[0]["sources"] == ["linkedin", "indeed"]
    assert unique[0]["_duplicate_job_urls"] == [jobs[1]["job_url"]]
    assert unique[0]["_duplicate_locations"] == ["Lyon"]


def test_identical_short_excerpt_does_not_merge_different_cross_board_roles():
    jobs = [
        {"job_url": "linkedin/1", "site": "linkedin", "title": "Product Manager", "company": "Acme",
         "description": "Build payment software for merchants", "_content_hash": "shared"},
        {"job_url": "indeed/2", "site": "indeed", "title": "Product Manager", "company": "Acme",
         "description": "Build healthcare software for clinicians", "_content_hash": "shared"},
    ]
    unique, merged = dedupe_by_content(jobs)
    assert merged == 0 and len(unique) == 2


def test_all_board_duplicate_preserves_each_source():
    body = " ".join(f"responsibility{i}" for i in range(100))
    jobs = [{"job_url": f"https://{site}.example/{n}", "site": site, "sources": [site],
             "title": "Product Manager", "company": "Acme", "location": "Paris",
             "description": body, "_content_hash": str(n)}
            for n, site in enumerate(("linkedin", "indeed", "glassdoor", "zip_recruiter",
                                      "bayt", "naukri", "bdjobs", "hellowork"))]
    unique, merged = dedupe_by_content(jobs)
    assert len(unique) == 1 and merged == 7
    assert unique[0]["sources"] == ["linkedin", "indeed", "glassdoor", "zip_recruiter",
                                   "bayt", "naukri", "bdjobs", "hellowork"]


def test_result_card_gets_both_source_icons_from_saved_aliases(tmp_path):
    db = str(tmp_path / "seen.db")
    with connect(db) as conn:
        for url in ("https://www.linkedin.com/jobs/view/1", "https://www.indeed.com/viewjob?jk=2",
                    "https://www.glassdoor.fr/job-listing/j?jl=3", "https://www.ziprecruiter.com/jobs/4",
                    "https://www.bayt.com/en/jobs/5", "https://www.naukri.com/job/6",
                    "https://bdjobs.com/h/details/7", "https://www.hellowork.com/fr-fr/emplois/8.html"):
            mark_seen(conn, job_url=url, status="passed", stage_reached=2, title="Product Manager",
                      company="Acme", reasoning="Fits the role", run_id="run-1")
    result = _fetch_jobs(db, "run-1", "all", 1)
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["sources"] == ["linkedin", "indeed", "glassdoor", "zip_recruiter",
                                           "bayt", "naukri", "bdjobs", "hellowork"]
