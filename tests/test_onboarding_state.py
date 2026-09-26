import tempfile
import threading
import time
import unittest
from pathlib import Path

from ftja.onboarding import DEFAULT_STATE, STATE_FILE, OnboardingError, apply_action, read_state, wait_for_action, wait_for_source_confirm, write_action, write_json


class OnboardingStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_json(str(self.root), STATE_FILE, DEFAULT_STATE)
        (self.root / "resume.txt").write_text("experience", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def act(self, kind, **payload):
        from ftja.onboarding import read_state
        payload["type"] = kind
        payload["expected_revision"] = read_state(str(self.root))["revision"]
        return apply_action(str(self.root), payload)

    def test_cannot_skip_profile(self):
        with self.assertRaises(OnboardingError):
            self.act("confirm_profile", titles=["Product Manager"], location="Europe")

    def test_normal_order_is_strict(self):
        state = self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt")}])
        self.assertEqual((state["stage"], state["phase"]), ("profile", "profile_summary"))
        state = self.act("confirm_profile_sources")
        self.assertTrue(state["profile"]["sources_confirmed"])
        state = self.act("set_profile_summary", summary="Built products and shipped experiments.")
        state = self.act("confirm_profile_summary")
        self.assertEqual(state["phase"], "title_proposal")
        state = self.act("confirm_profile", titles=["Product Manager"], location="Europe")
        self.assertEqual((state["stage"], state["phase"]), ("rubric", "stage0"))
        state = self.act("set_stage0_draft", card={"keywords": ["AI builder"], "languages": ["en", "fr"], "exclude_keywords": ["pure sales"]})
        self.assertEqual(state["cards"]["stage0"]["keywords"], ["AI builder"])
        self.assertEqual(state["cards"]["stage0"]["languages"], ["en", "fr"])
        state = self.act("confirm_stage0", keywords=["AI builder"], languages=["en", "fr"], exclude_keywords=["pure sales"])
        self.assertEqual(state["phase"], "questions")
        with self.assertRaises(OnboardingError):
            self.act("confirm_rubric", rubric_md="Pass if the role is a clear product-building fit.")
        self.act("answer_rubric_question", question_id="clear_yes", answer="Product-building role")
        self.act("answer_rubric_question", question_id="dealbreakers", answer="Pure sales")
        self.act("answer_rubric_question", question_id="preferences", answer="Small team")
        state = self.act("confirm_rubric", rubric_md="Pass if the role is a clear product-building fit.")
        self.assertEqual((state["stage"], state["phase"], state["status"]), ("run", "ready", "ready"))
        self.assertTrue((self.root / "rubric.md").exists())

    def test_profile_summary_must_be_confirmed_before_search_settings(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt")}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        with self.assertRaises(OnboardingError):
            self.act("confirm_profile", titles=["Product Manager"], location="Europe")
        state = self.act("confirm_profile_summary")
        self.assertEqual(state["phase"], "title_proposal")

    def test_profile_sources_can_grow_after_first_selection(self):
        first = {"path": str(self.root / "resume.txt"), "kind": "file"}
        state = self.act("set_profile_sources", sources=[first])
        self.assertEqual(state["phase"], "profile_summary")
        second_dir = self.root / "portfolio"
        second_dir.mkdir()
        state = self.act("set_profile_sources", sources=[first, {"path": str(second_dir), "kind": "folder"}])
        self.assertEqual(state["phase"], "profile_summary")
        self.assertEqual([source["path"] for source in state["profile"]["sources"]], [first["path"], str(second_dir)])
        self.assertEqual(state["profile"]["sources"][1]["kind"], "folder")

    def test_wait_for_source_confirm_handles_multiple_source_updates(self):
        result = {}

        def wait():
            result.update(wait_for_source_confirm(str(self.root), timeout_seconds=2))

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.1)
        first = {"path": str(self.root / "resume.txt"), "kind": "file"}
        second_dir = self.root / "portfolio"
        second_dir.mkdir()
        self.act("set_profile_sources", sources=[first])
        self.act("set_profile_sources", sources=[first, {"path": str(second_dir), "kind": "folder"}])
        expected_revision = read_state(str(self.root))["revision"]
        action = {"type": "confirm_profile_sources", "expected_revision": expected_revision}
        apply_action(str(self.root), action)
        write_action(str(self.root), action)
        thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["state"]["profile"]["sources_confirmed"])

    def test_generic_wait_handles_later_profile_confirmation(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")
        result = {}

        def wait():
            result.update(wait_for_action(str(self.root), ["confirm_profile"], timeout_seconds=2))

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.1)
        action = {
            "type": "confirm_profile",
            "expected_revision": read_state(str(self.root))["revision"],
            "titles": ["Product Manager"],
            "location": "Europe",
            "is_remote": False,
            "hours_old": 24,
        }
        apply_action(str(self.root), action)
        write_action(str(self.root), action)
        thread.join(timeout=3)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["action"]["type"], "confirm_profile")
        self.assertEqual(result["state"]["phase"], "stage0")

    def test_wait_bridge_covers_every_confirm_action(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")

        def wait_then_confirm(kind, **payload):
            result = {}

            def wait():
                result.update(wait_for_action(str(self.root), [kind], timeout_seconds=2))

            thread = threading.Thread(target=wait)
            thread.start()
            time.sleep(0.1)
            action = {"type": kind, "expected_revision": read_state(str(self.root))["revision"], **payload}
            state = apply_action(str(self.root), action)
            write_action(str(self.root), action)
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result["status"], "confirmed")
            self.assertEqual(result["action"]["type"], kind)
            return state

        wait_then_confirm("confirm_profile", titles=["Product Manager"], location="Europe", is_remote=False, hours_old=24)
        wait_then_confirm("confirm_stage0", keywords=["AI builder"], languages=["en"], exclude_keywords=[])
        wait_then_confirm("answer_rubric_question", question_id="clear_yes", answer="Product-building role")
        wait_then_confirm("answer_rubric_question", question_id="dealbreakers", answer="Pure sales")
        wait_then_confirm("answer_rubric_question", question_id="preferences", answer="Small team")
        state = wait_then_confirm("confirm_rubric", rubric_md="Pass if the role is a clear product-building fit.")
        self.assertEqual((state["stage"], state["phase"]), ("run", "ready"))

    def test_stale_revision_is_rejected(self):
        apply_action(str(self.root), {"type": "set_profile_sources", "expected_revision": 0, "sources": [{"path": str(self.root / "resume.txt")}]})
        with self.assertRaises(OnboardingError):
            apply_action(str(self.root), {"type": "set_profile_summary", "expected_revision": 0, "summary": "stale"})


if __name__ == "__main__":
    unittest.main()
