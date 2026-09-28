import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ftja.onboarding import DEFAULT_STATE, STATE_FILE, OnboardingError, apply_action, read_state, read_bridge, wait_for_action, wait_for_source_confirm, write_action, write_json


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

    def test_stage0_draft_rejects_blank_proposal(self):
        from ftja.onboarding import validate_stage0_draft
        with self.assertRaises(OnboardingError):
            validate_stage0_draft({"keywords": [], "languages": [], "exclude_keywords": []})
        validate_stage0_draft({"keywords": ["AI product"], "languages": ["English"], "exclude_keywords": []})

    def test_search_and_stage0_proposals_are_saved_together_without_skipping_confirmation(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt")}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="AI Product Builder. English and French fluent.")
        self.act("confirm_profile_summary")
        proposal = {
            "profile": {
                "titles": ["Product Builder", "AI Product Manager"],
                "criteria": {
                    "search_terms": ["Product Builder", "AI Product Manager"],
                    "location": "France",
                    "is_remote": False,
                    "hours_old": 24,
                    "results_wanted": 100,
                    "languages": ["en", "fr"],
                    "exclude_keywords": ["pure sales"],
                    "keywords": {"tier1": ["AI product", "LLM"]},
                },
            },
            "cards": {
                "stage0": {
                    "keywords": ["AI product", "LLM"],
                    "languages": ["en", "fr"],
                    "exclude_keywords": ["pure sales"],
                }
            },
        }
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ftja.onboarding",
                str(self.root),
                "set-state",
                "--stage",
                "profile",
                "--phase",
                "title_proposal",
                "--status",
                "waiting",
                "--json",
                json.dumps(proposal),
            ],
            cwd=Path(__file__).parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        state = read_state(str(self.root))
        self.assertEqual((state["stage"], state["phase"]), ("profile", "title_proposal"))
        self.assertEqual(state["profile"]["titles"], ["Product Builder", "AI Product Manager"])
        self.assertEqual(state["profile"]["criteria"]["location"], "France")
        self.assertEqual(state["cards"]["stage0"]["keywords"], ["AI product", "LLM"])
        self.assertEqual(state["cards"]["stage0"]["languages"], ["en", "fr"])

        state = self.act(
            "confirm_profile",
            titles=["Product Builder", "AI Product Manager"],
            location="France",
            is_remote=False,
            hours_old=24,
        )
        self.assertEqual((state["stage"], state["phase"]), ("rubric", "stage0"))
        self.assertEqual(state["cards"]["stage0"]["keywords"], ["AI product", "LLM"])
        self.assertEqual(state["cards"]["stage0"]["languages"], ["en", "fr"])

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
        self.act("set_profile_draft", titles=["Product Manager"], location="Europe", stage0={"keywords": ["AI builder"], "languages": ["en", "fr"], "exclude_keywords": ["pure sales"]})
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

    def test_wait_marks_the_bridge_listening_while_it_is_waiting(self):
        result = {}

        def wait():
            result.update(wait_for_action(str(self.root), ["confirm_profile_sources"], timeout_seconds=1))

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.1)
        bridge = read_bridge(str(self.root))
        self.assertEqual(bridge["status"], "listening")
        self.assertEqual(bridge["action_types"], ["confirm_profile_sources"])
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(read_bridge(str(self.root))["status"], "offline")

    def test_wait_recovers_a_web_action_that_finished_before_the_waiter_started(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        expected_revision = read_state(str(self.root))["revision"]
        action = {"type": "confirm_profile_sources", "expected_revision": expected_revision}
        apply_action(str(self.root), action)
        write_action(str(self.root), action)

        result = wait_for_action(str(self.root), ["confirm_profile_sources"], timeout_seconds=1)

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["action"]["type"], "confirm_profile_sources")
        self.assertTrue(result["state"]["profile"]["sources_confirmed"])

    def test_wait_does_not_replay_an_older_action_file(self):
        source = {"path": str(self.root / "resume.txt"), "kind": "file"}
        self.act("set_profile_sources", sources=[source])
        self.act("set_profile_sources", sources=[source])
        action = {"type": "set_profile_sources", "expected_revision": 0}
        write_action(str(self.root), action)

        result = wait_for_action(str(self.root), ["set_profile_sources"], timeout_seconds=1)

        self.assertEqual(result["status"], "timeout")

    def test_generic_wait_handles_later_profile_confirmation(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")
        self.act("set_profile_draft", titles=["Product Manager"], location="Europe", stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []})
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
        self.act("set_profile_draft", titles=["Product Manager"], location="Europe", stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []})

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

    def test_second_wait_for_the_same_action_type_does_not_replay_the_first_answer(self):
        # Regression test for a live-session bug: the three rubric questions
        # all wait for the identical action type ("answer_rubric_question").
        # If the second wait starts while the action file still holds the
        # already-consumed first answer, its own revision-delta recovery
        # check must not mistake that stale answer for a fresh one.
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")
        self.act("set_profile_draft", titles=["Product Manager"], location="Europe", stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []})
        self.act("confirm_profile", titles=["Product Manager"], location="Europe", is_remote=False, hours_old=24)
        self.act("confirm_stage0", keywords=["AI builder"], languages=["en"], exclude_keywords=[])

        first_action = {
            "type": "answer_rubric_question",
            "expected_revision": read_state(str(self.root))["revision"],
            "question_id": "clear_yes",
            "answer": "Product-building role",
        }
        apply_action(str(self.root), first_action)
        write_action(str(self.root), first_action)
        first_result = wait_for_action(str(self.root), ["answer_rubric_question"], timeout_seconds=1)
        self.assertEqual(first_result["status"], "confirmed")
        self.assertEqual(first_result["action"]["question_id"], "clear_yes")

        # Nothing new is written this time: the action file still holds the
        # first answer. A second wait for the same type must time out, not
        # immediately "confirm" that stale answer again.
        second_result = wait_for_action(str(self.root), ["answer_rubric_question"], timeout_seconds=1)
        self.assertEqual(second_result["status"], "timeout")

        # A genuinely new second answer, written while this third wait is
        # polling, must still be picked up correctly.
        result = {}

        def wait():
            result.update(wait_for_action(str(self.root), ["answer_rubric_question"], timeout_seconds=2))

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.1)
        second_action = {
            "type": "answer_rubric_question",
            "expected_revision": read_state(str(self.root))["revision"],
            "question_id": "dealbreakers",
            "answer": "Pure sales",
        }
        apply_action(str(self.root), second_action)
        write_action(str(self.root), second_action)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["action"]["question_id"], "dealbreakers")

    def test_confirm_profile_uses_the_confirm_actions_own_results_wanted(self):
        # Regression test: confirm_profile used to read results_wanted only
        # from the earlier draft, silently discarding whatever the user
        # edited on the search-settings card at confirm time.
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")
        self.act(
            "set_profile_draft",
            titles=["Product Manager"],
            location="Europe",
            results_wanted=100,
            stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []},
        )
        state = self.act(
            "confirm_profile",
            titles=["Product Manager"],
            location="Europe",
            is_remote=False,
            hours_old=24,
            results_wanted=500,
        )
        self.assertEqual(state["profile"]["criteria"]["results_wanted"], 500)

    def test_results_wanted_is_clamped_to_a_sane_range(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")
        state = self.act(
            "set_profile_draft",
            titles=["Product Manager"],
            location="Europe",
            results_wanted=5000,
            stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []},
        )
        self.assertEqual(state["profile"]["criteria"]["results_wanted"], 1000)

    def test_stale_revision_is_rejected(self):
        apply_action(str(self.root), {"type": "set_profile_sources", "expected_revision": 0, "sources": [{"path": str(self.root / "resume.txt")}]})
        with self.assertRaises(OnboardingError):
            apply_action(str(self.root), {"type": "set_profile_summary", "expected_revision": 0, "summary": "stale"})


if __name__ == "__main__":
    unittest.main()
