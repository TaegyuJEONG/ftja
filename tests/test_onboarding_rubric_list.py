"""The onboarding rubric is a list of one-line criteria, the same list the
Pipeline tab edits, so Pipeline shows it from the first visit."""
import json
import tempfile
import unittest
from pathlib import Path

from ftja.onboarding import OnboardingError, apply_action, draft_rubric_criteria, read_state, DEFAULT_STATE, STATE_FILE, write_json
from ftja.verdict import criteria_status, load_criteria


def questions(clear_yes, dealbreakers="", preferences=""):
    return [{"id": "clear_yes", "answer": clear_yes}, {"id": "dealbreakers", "answer": dealbreakers},
            {"id": "preferences", "answer": preferences}]


class DraftTests(unittest.TestCase):
    def test_each_line_is_its_own_criterion_of_the_right_kind(self):
        draft = draft_rubric_criteria(questions(
            "- Builds AI products: owns discovery to launch\n- Entrepreneurial culture",
            "Internship; Pure sales", "Salary above 80k"))
        by_kind = {k: [c for c in draft["criteria"] if c["kind"] == k] for k in ("pass", "fail", "preference")}
        self.assertEqual(len(by_kind["pass"]), 2)
        self.assertEqual(len(by_kind["fail"]), 2)
        self.assertEqual(len(by_kind["preference"]), 1)
        self.assertEqual(by_kind["pass"][0]["label"], "Builds AI products")
        # nothing is grouped, so a job passes on any one of them
        self.assertTrue(all("group" not in c for c in draft["criteria"]))
        self.assertEqual(len({c["id"] for c in draft["criteria"]}), len(draft["criteria"]))


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        state = {**DEFAULT_STATE, "stage": "rubric", "phase": "rubric_review", "revision": 5,
                 "rubric_questions": questions("Builds AI products", "Internship", "Remote")}
        write_json(str(self.root), STATE_FILE, state)

    def tearDown(self):
        self.tmp.cleanup()

    def confirm(self, **payload):
        return apply_action(str(self.root), {"type": "confirm_rubric", "expected_revision": 5, **payload})

    def test_confirming_the_list_writes_the_criteria_file_and_rubric_md(self):
        listed = draft_rubric_criteria(questions("Builds AI products", "Internship", "Remote"))["criteria"]
        self.confirm(criteria=listed, notes={})
        path = self.root / "rubric-criteria.json"
        self.assertTrue(path.exists())
        self.assertEqual(criteria_status(str(path), str(self.root / "rubric.md")), "ok")
        self.assertEqual(len(load_criteria(str(path))["criteria"]), 3)
        self.assertIn("Builds AI products", (self.root / "rubric.md").read_text())
        self.assertEqual(read_state(str(self.root))["stage"], "run")

    def test_a_list_with_nothing_that_can_pass_is_rejected(self):
        with self.assertRaises(OnboardingError):
            self.confirm(criteria=[{"id": "x", "kind": "fail", "label": "Internship", "text": "no"}])

    def test_the_text_only_path_still_works(self):
        self.confirm(rubric_md="# Rubric\n\nPass everything.")
        self.assertEqual((self.root / "rubric.md").read_text(), "# Rubric\n\nPass everything.")


if __name__ == "__main__":
    unittest.main()
