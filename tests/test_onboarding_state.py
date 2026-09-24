import tempfile
import unittest
from pathlib import Path

from ftja.onboarding import OnboardingError, apply_action, write_json, STATE_FILE, DEFAULT_STATE


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
        state = self.act("set_profile_summary", summary="Built products and shipped experiments.")
        self.assertEqual(state["phase"], "title_proposal")
        state = self.act("confirm_profile", titles=["Product Manager"], location="Europe")
        self.assertEqual((state["stage"], state["phase"]), ("rubric", "stage0"))
        state = self.act("confirm_stage0", keywords=["AI builder"], languages=["en"], exclude_keywords=[])
        self.assertEqual(state["phase"], "stage1")
        self.assertEqual(self.act("confirm_stage1")["phase"], "stage2")
        with self.assertRaises(OnboardingError):
            self.act("confirm_stage2")
        state = self.act("confirm_stage2", rubric_md="Pass if the role is a clear product-building fit.")
        self.assertEqual((state["stage"], state["phase"], state["status"]), ("run", "ready", "ready"))
        self.assertTrue((self.root / "rubric.md").exists())

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

    def test_stale_revision_is_rejected(self):
        apply_action(str(self.root), {"type": "set_profile_sources", "expected_revision": 0, "sources": [{"path": str(self.root / "resume.txt")}]})
        with self.assertRaises(OnboardingError):
            apply_action(str(self.root), {"type": "set_profile_summary", "expected_revision": 0, "summary": "stale"})


if __name__ == "__main__":
    unittest.main()
