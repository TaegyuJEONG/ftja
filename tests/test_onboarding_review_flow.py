import tempfile
import unittest
from pathlib import Path

from ftja.onboarding import DEFAULT_STATE, STATE_FILE, OnboardingError, apply_action, read_state, write_json


class OnboardingReviewFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_json(str(self.root), STATE_FILE, DEFAULT_STATE)
        (self.root / "resume.txt").write_text("experience", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def act(self, kind, **payload):
        payload["type"] = kind
        payload["expected_revision"] = read_state(str(self.root))["revision"]
        return apply_action(str(self.root), payload)

    def reach_rubric_questions(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt")}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Initial draft")
        state = self.act("set_profile_summary", summary="Edited summary")
        self.assertEqual(state["profile"]["summary"], "Edited summary")
        self.act("confirm_profile_summary")
        self.act("set_profile_draft", titles=["Product Builder"], location="Europe", stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []})
        self.act("confirm_profile", titles=["Product Builder"], location="Europe")
        return self.act("confirm_stage0", keywords=["AI builder"], languages=["en"], exclude_keywords=[])

    def test_summary_can_be_edited_before_confirmation(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt")}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Initial draft")
        state = self.act("set_profile_summary", summary="Edited summary")
        self.assertEqual(state["phase"], "profile_summary_review")
        self.assertEqual(state["profile"]["summary"], "Edited summary")

    def test_rubric_questions_advance_one_at_a_time_then_require_final_confirmation(self):
        state = self.reach_rubric_questions()
        self.assertEqual((state["stage"], state["phase"]), ("rubric", "questions"))
        self.assertEqual(state["rubric_questions"][0]["id"], "clear_yes")

        state = self.act("answer_rubric_question", question_id="clear_yes", answer="0 to 1 product building")
        self.assertEqual(state["rubric_questions"][0]["answer"], "0 to 1 product building")
        self.assertEqual(state["rubric_questions"][1]["id"], "dealbreakers")
        with self.assertRaises(OnboardingError):
            self.act("confirm_rubric", rubric_md="Pass if it fits.")

        self.act("answer_rubric_question", question_id="dealbreakers", answer="Pure sales")
        state = self.act("answer_rubric_question", question_id="preferences", answer="Small teams")
        self.assertEqual(state["phase"], "rubric_review")
        state = self.act("confirm_rubric", rubric_md="Pass if it is a 0 to 1 product-building role.")
        self.assertEqual((state["stage"], state["phase"], state["status"]), ("run", "ready", "ready"))
        self.assertEqual(read_state(str(self.root))["allowed_actions"], [])
        self.assertTrue((self.root / "rubric.md").exists())

    def test_web_contract_has_editable_summary_and_one_question_rubric_flow(self):
        html = Path(__file__).parents[1].joinpath("ftja", "app.html").read_text(encoding="utf-8")
        self.assertIn('data-profile-summary', html)
        self.assertIn('data-save-profile-summary', html)
        self.assertIn('data-answer-rubric-question', html)
        self.assertIn('data-confirm-rubric', html)
        self.assertIn('Answer ${currentIndex + 1} of ${questions.length}', html)
        self.assertIn('Confirm rubric and open Pipeline', html)
        self.assertNotIn('Confirm Stage 1', html)
        self.assertNotIn('Confirm Stage 2', html)


if __name__ == "__main__":
    unittest.main()
