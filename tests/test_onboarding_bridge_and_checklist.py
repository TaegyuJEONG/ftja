"""Bridge status labels ('listening'/'working'/'offline') and the shared
8-item checklist that both the chat and the web view render from the same
onboarding-state.json — see ftja.onboarding.build_checklist."""
import tempfile
import unittest
from pathlib import Path

from ftja.onboarding import (
    DEFAULT_STATE,
    STATE_FILE,
    apply_action,
    bridge_is_ready,
    build_checklist,
    read_bridge,
    read_state,
    set_bridge_working,
    write_bridge,
    write_json,
)


def flat_status(checklist):
    """{item_id: status} across all stages, for easy assertions."""
    return {item["id"]: item["status"] for stage in checklist for item in stage["items"]}


class BridgeStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_json(str(self.root), STATE_FILE, DEFAULT_STATE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_bridge_is_offline(self):
        self.assertEqual(read_bridge(str(self.root))["status"], "offline")
        self.assertFalse(bridge_is_ready(str(self.root)))

    def test_working_sets_label_and_is_not_ready_for_actions(self):
        bridge = set_bridge_working(str(self.root), "Writing your profile summary")
        self.assertEqual(bridge["status"], "working")
        self.assertEqual(bridge["label"], "Writing your profile summary")
        self.assertFalse(bridge_is_ready(str(self.root)))

    def test_listening_is_ready_and_working_label_does_not_leak_into_it(self):
        set_bridge_working(str(self.root), "Deriving keywords")
        write_bridge(str(self.root), "listening", ["confirm_profile_sources"], __import__("os").getpid())
        bridge = read_bridge(str(self.root))
        self.assertEqual(bridge["status"], "listening")
        self.assertEqual(bridge["label"], "")
        self.assertTrue(bridge_is_ready(str(self.root)))

    def test_offline_does_not_keep_a_stale_pid_or_label(self):
        set_bridge_working(str(self.root), "Writing your profile summary")
        write_bridge(str(self.root), "offline")
        bridge = read_bridge(str(self.root))
        self.assertEqual(bridge["status"], "offline")
        self.assertEqual(bridge["label"], "")
        self.assertIsNone(bridge["pid"])
        self.assertFalse(bridge_is_ready(str(self.root)))

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):
            write_bridge(str(self.root), "ready")


class ChecklistTests(unittest.TestCase):
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

    def test_fresh_onboarding_has_first_item_current_and_rest_pending(self):
        checklist = build_checklist(read_state(str(self.root)), str(self.root))
        self.assertEqual(len(checklist), 4)
        self.assertEqual([stage["id"] for stage in checklist], ["profile", "search_settings", "judgment", "first_run"])
        status = flat_status(checklist)
        self.assertEqual(status["add_background_files"], "current")
        self.assertEqual(status["review_profile_summary"], "pending")
        self.assertEqual(status["review_first_results"], "pending")

    def test_items_complete_in_order_as_the_state_machine_advances(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        status = flat_status(build_checklist(read_state(str(self.root)), str(self.root)))
        self.assertEqual(status["add_background_files"], "done")
        self.assertEqual(status["review_profile_summary"], "current")

        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")
        status = flat_status(build_checklist(read_state(str(self.root)), str(self.root)))
        self.assertEqual(status["review_profile_summary"], "done")
        self.assertEqual(status["review_search_settings"], "current")

        self.act(
            "set_profile_draft",
            titles=["Product Manager"],
            location="Europe",
            stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []},
        )
        self.act(
            "confirm_profile",
            titles=["Product Manager"],
            location="Europe",
            is_remote=False,
            hours_old=24,
        )
        status = flat_status(build_checklist(read_state(str(self.root)), str(self.root)))
        self.assertEqual(status["review_search_settings"], "done")
        self.assertEqual(status["review_code_filter"], "current")

        self.act("confirm_stage0", keywords=["AI builder"], languages=["en"], exclude_keywords=[])
        status = flat_status(build_checklist(read_state(str(self.root)), str(self.root)))
        self.assertEqual(status["review_code_filter"], "done")
        self.assertEqual(status["answer_role_questions"], "current")

        state = read_state(str(self.root))
        for question in state["rubric_questions"]:
            self.act("answer_rubric_question", question_id=question["id"], answer="An answer.")
        status = flat_status(build_checklist(read_state(str(self.root)), str(self.root)))
        self.assertEqual(status["answer_role_questions"], "done")
        self.assertEqual(status["confirm_rubric"], "current")

        self.act("confirm_rubric", rubric_md="# Rubric\n\nPass everything.")
        status = flat_status(build_checklist(read_state(str(self.root)), str(self.root)))
        self.assertEqual(status["confirm_rubric"], "done")
        # No run has produced output yet, even though the state machine reached "run".
        self.assertEqual(status["run_first_search"], "current")
        self.assertEqual(status["review_first_results"], "pending")

    def test_run_items_require_an_actual_run_not_just_reaching_the_run_stage(self):
        self.act("set_profile_sources", sources=[{"path": str(self.root / "resume.txt"), "kind": "file"}])
        self.act("confirm_profile_sources")
        self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.act("confirm_profile_summary")
        self.act(
            "set_profile_draft",
            titles=["Product Manager"],
            location="Europe",
            stage0={"keywords": ["AI builder"], "languages": ["en"], "exclude_keywords": []},
        )
        self.act("confirm_profile", titles=["Product Manager"], location="Europe", is_remote=False, hours_old=24)
        self.act("confirm_stage0", keywords=["AI builder"], languages=["en"], exclude_keywords=[])
        for question in read_state(str(self.root))["rubric_questions"]:
            self.act("answer_rubric_question", question_id=question["id"], answer="An answer.")
        self.act("confirm_rubric", rubric_md="# Rubric\n\nPass everything.")

        state = read_state(str(self.root))
        self.assertEqual(state["stage"], "run")
        status = flat_status(build_checklist(state, str(self.root)))
        self.assertEqual(status["run_first_search"], "current")
        self.assertEqual(status["review_first_results"], "pending")

        (self.root / "runs.jsonl").write_text(
            '{"run_id": "r1", "scraped_count": 10, "passed": 1, "total_evaluated": 5, "by_status": {}}\n',
            encoding="utf-8",
        )
        status = flat_status(build_checklist(state, str(self.root)))
        self.assertEqual(status["run_first_search"], "done")
        self.assertEqual(status["review_first_results"], "done")


if __name__ == "__main__":
    unittest.main()
