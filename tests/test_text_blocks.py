from ftja.filter_stage0 import dedupe_by_content
from ftja.text_blocks import blocks_for_job, clean_sentence, split_sentences


def hits(text, keywords):
    return {k for b in blocks_for_job(text, keywords, window=0) for k in b["matched_keywords"]}


def test_a_keyword_matches_whole_words_not_letters_inside_other_words():
    text = "We drive outcomes with frameworks.\nAncestry and telecom clients.\nThe allocation is fixed."
    assert hits(text, ["UTC", "work", "CEST", "tele", "location"]) == set()
    assert hits("Working hours follow UTC.", ["UTC", "work"]) == {"UTC", "work"}


def test_word_forms_and_phrases_match():
    assert hits("You can work remotely across time zones.", ["remote", "time zone"]) == {"remote", "time zone"}
    assert hits("Two days on site.\nFully onsite.", ["on-site"]) == {"on-site"}
    assert hits("Poste en télétravail partiel.", ["télétravail", "tele"]) == {"télétravail"}


def test_a_keyword_in_capitals_only_matches_capitals():
    assert hits("Cet emploi est à Paris.", ["CET"]) == set()
    assert hits("Core hours are 10-16 CET.", ["CET"]) == {"CET"}


def test_a_keyword_with_a_symbol_is_matched_literally():
    assert hits("One of the top 100 companies.", ["100%"]) == set()
    assert hits("This role is 100% remote.", ["100%"]) == {"100%"}


def test_markdown_from_the_scraper_is_cleaned_out_of_sentences():
    assert clean_sentence("* **3\\-5\\+ years** of experience") == "3-5+ years of experience"
    assert split_sentences("the **Nasdaq AI Platform**\n, an enterprise platform.\n## What You'll Do") == [
        "the Nasdaq AI Platform, an enterprise platform.", "What You'll Do"]


def test_reposts_of_one_posting_are_grouped_and_keep_their_places():
    jd = " ".join(f"word{i}" for i in range(100))
    jobs = [
        {"job_url": "u1", "title": "Sr. Product Manager", "company": "Acme", "location": "Porto", "description": jd, "_content_hash": "a"},
        {"job_url": "u2", "title": "Senior Product Manager", "company": "ACME", "location": "Madrid", "description": jd + " extra", "_content_hash": "b"},
        {"job_url": "u3", "title": "Senior Product Manager", "company": "Acme", "location": "Porto", "description": "another team entirely", "_content_hash": "c"},
        {"job_url": "u4", "title": "PM", "company": "Job Board", "location": "Porto", "description": "x", "_content_hash": "a"},
    ]
    unique, merged = dedupe_by_content(jobs)
    assert [j["job_url"] for j in unique] == ["u1", "u3"] and merged == 2
    assert unique[0]["_duplicate_job_urls"] == ["u2", "u4"] and unique[0]["_duplicate_locations"] == ["Madrid"]



def test_a_title_has_to_contain_the_search_term_that_found_the_job():
    from ftja.filter_stage0 import filter_stage0, title_matches_search
    terms = ["Product Manager", "AI Specialist"]
    job = lambda title, found_by: {"job_url": title, "title": title, "company": title, "_search_terms": found_by,
                                   "description": "This role is remote. " + title}
    jobs = [job("Lead Product Manager", ["Product Manager"]), job("Senior Product Managers (m/f/d)", ["Product Manager"]),
            job("Senior AI specialist", ["AI Specialist"]), job("AI Specialist Consultant", ["AI Specialist"]),
            job("Strategy Lead", ["Product Manager"]), job("Product Owner", ["Product Manager"]),
            job("AI Specialist", ["Product Manager"]),  # right title for another search, not for the one that found it
            job("Product Manager, AI", ["Product Manager", "AI Specialist"])]  # found by both: either term will do
    criteria = {"languages": [], "search_terms": terms, "keywords": {"tier1": ["remote"]}}
    passed, dropped = filter_stage0(jobs, criteria)
    assert len(passed) == 8 and "title_mismatch" not in dropped  # off unless asked for
    passed, dropped = filter_stage0(jobs, {**criteria, "title_must_match": True})
    assert [j["title"] for j in passed] == ["Lead Product Manager", "Senior Product Managers (m/f/d)", "Senior AI specialist",
                                            "AI Specialist Consultant", "Product Manager, AI"]
    assert dropped["title_mismatch"] == 3
    # quote marks typed around a term are not part of what the title has to contain
    assert title_matches_search({"title": "Entrepreneur in Residence (FD77540)"}, ['"Entrepreneur In Residence"'])
    assert title_matches_search({"title": "Entrepreneur in Residence"}, ["“Entrepreneur In Residence”"])
    assert not title_matches_search({"title": "Founder in Residence"}, ["Entrepreneur In Residence"])
