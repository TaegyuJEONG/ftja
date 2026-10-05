import unittest

from ftja.filter_stage0 import filter_stage0, find_language_requirement

READS = ["en", "fr", "ko"]


def job(description, title="Product Manager"):
    return {"job_url": "https://example.test/1", "title": title, "company": "Acme",
            "location": "Paris", "description": description}


class LanguageRequirementTests(unittest.TestCase):
    def assertDropped(self, description, title="Product Manager", languages=READS):
        self.assertTrue(find_language_requirement(job(description, title), languages), description)

    def assertKept(self, description, title="Product Manager", languages=READS):
        self.assertEqual(find_language_requirement(job(description, title), languages), "", description)

    def test_required_other_language_is_dropped(self):
        for sentence in [
            "* Fluency in German and English, and comfort working with engineers on APIs",
            "* English and Czech at C1 or better are required; it's how our tech unit communicates",
            "* Business level proficiency in Italian and English",
            "* Fluent English and Spanish",
            "* Excellent facilitation, teaching, and communication skills in Croatian and English",
            "* Fluent in English and Danish and/or Swedish",
            "* Business Fluent in Swedish and English; another Nordic language is a plus",
            "* Fluent English (minimum B2 level) and Polish (minimum C1 level)",
            "* German at native level and English fluency — both are non\\-negotiable",
            "* Good knowledge of English, both written and spoken; Italian is required",
            "* Native or professional\\-level proficiency in Dutch, paired with market knowledge",
        ]:
            self.assertDropped(sentence)

    def test_speaking_title_is_dropped(self):
        self.assertDropped("You will lead deployments.", title="Lead Deployment Strategist, Italian Speaking")
        self.assertDropped("You will lead deployments.", title="German\\-speaking Product Manager")

    def test_language_mentions_that_are_not_requirements_are_kept(self):
        for sentence in [
            "Planeco is the AI native architecture firm for the German market",
            "German helps but isn't required",
            "Fluent German is a big bonus, but not something we expect",
            "Our users are our own sales and operations teams, mostly German\\-speaking",
            "Spanish is a plus",
            "* Over time, you develop deep knowledge of our Dutch solution portfolio and become a point of contact",
            "* Keep your knowledge of the Dutch accounting market, relevant regulations and emerging technology up to date",
            "Knowledge of Dutch is an advantage",
            "Proactively builds a senior network in the Dutch banking sector; attends and speaks at industry forums",
            "Disclosing personal data in the scope specified by the Polish Labour Code from 26 June 1974 and executive acts are mandatory",
            "You will polish the roadmap, and you must ship weekly",
            "We also have support consultants covering Italy, Spain and Portugal, and you must coordinate with them",
        ]:
            self.assertKept(sentence)

    def test_alternative_with_a_readable_language_is_kept(self):
        self.assertKept("* Fluency in English and Dutch or French")
        self.assertKept("Fluent in German or English")
        self.assertKept("Fluent in either English or German")

    def test_requirement_for_a_readable_language_is_kept(self):
        self.assertKept("Fluent French and English are required")
        self.assertDropped("Fluent French and English are required", languages=["en"])

    def test_language_names_are_resolved_like_codes(self):
        self.assertKept("Fluent French and English are required", languages=["English", "French"])
        self.assertDropped("Fluent in German and English", languages=["English"])

    def test_langdetect_region_codes_are_resolved(self):
        self.assertKept("Fluent Mandarin and English required", languages=["zh-cn", "en"])

    def test_no_resolvable_language_never_drops(self):
        self.assertKept("Fluent in German and English", languages=[])
        self.assertKept("Fluent in German and English", languages=["klingon"])


class FilterStage0Tests(unittest.TestCase):
    def test_language_requirement_drop_is_counted_and_recorded_with_evidence(self):
        criteria = {"languages": ["en"], "exclude_keywords": [], "keywords": {"tier1": ["prototype"]}}
        jobs = [
            job("You will build a prototype every week with the product team.\nFluent in German and English."),
            {**job("You will build a prototype every week with the product team.\nGerman is a plus."),
             "job_url": "https://example.test/2"},
        ]
        dropped_jobs = []
        passed, dropped = filter_stage0(jobs, criteria, dropped_jobs)
        self.assertEqual([j["job_url"] for j in passed], ["https://example.test/2"])
        self.assertEqual(dropped["language_requirement"], 1)
        self.assertEqual(dropped_jobs[0]["job_url"], "https://example.test/1")
        self.assertEqual(dropped_jobs[0]["evidence"], "Fluent in German and English")


if __name__ == "__main__":
    unittest.main()
