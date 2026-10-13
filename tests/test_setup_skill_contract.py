"""Contract tests for the chat-driven setup skill."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILL = ROOT / ".claude/skills/ftja-setup/SKILL.md"
CODEX_SKILL = ROOT / ".agents/skills/ftja-setup/SKILL.md"
LANDING = ROOT / "landing.html"
APP = ROOT / "ftja/app.html"

CHECKLIST = [
    "Add your resume",
    "Review your profile summary",
    "Review your search settings",
    "Review the code-based filter",
    "Answer three questions about your ideal role",
    "Review everything in the viewer",
]


def normalized(text: str) -> str:
    return " ".join(text.split())


class SetupSkillContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = CLAUDE_SKILL.read_text(encoding="utf-8")
        self.text = normalized(self.raw)

    def test_both_clients_follow_the_same_skill(self) -> None:
        self.assertEqual(self.raw, CODEX_SKILL.read_text(encoding="utf-8"))

    def test_checklist_items_in_order(self) -> None:
        section = self.raw.split("### Checklist", 1)[1].split("###", 1)[0]
        self.assertEqual(re.findall(r"^\d+\. `([^`]+)`$", section, flags=re.MULTILINE), CHECKLIST)

    def test_nothing_waits_for_the_viewer(self) -> None:
        for gone in ("wait-for-action", "set-state", "run_bridge", "onboarding-action"):
            self.assertNotIn(gone, self.raw)
        self.assertIn("never ask the user to click anything in the viewer during setup", self.text)

    def test_every_step_uses_its_command_in_order(self) -> None:
        commands = re.findall(r"ftja\.onboarding \. (profile|search|rubric|finish)", self.raw)
        self.assertEqual(list(dict.fromkeys(commands)), ["profile", "search", "rubric", "finish"])

    def test_json_goes_through_a_file(self) -> None:
        self.assertIn("never use `--json`", self.text)
        self.assertIn("--json-file .ftja-run/setup-search.json", self.raw)

    def test_the_resume_is_kept_in_the_profile_folder(self) -> None:
        self.assertIn("save the file into `profile/` under its own file name", self.text)

    def test_the_rubric_list_is_shown_as_code_built_it(self) -> None:
        self.assertIn("do not rewrite, merge or reorder the criteria yourself", self.text)

    def test_the_viewer_opens_only_at_the_end_on_the_pipeline_tab(self) -> None:
        self.assertLess(self.raw.index("onboarding . finish"), self.raw.index("http://127.0.0.1:8765/#pipeline"))
        self.assertLess(self.raw.index("onboarding . finish"), self.raw.index("preview_start"))

    def test_turn_one_ends_with_the_resume_question(self) -> None:
        turn_one = self.raw.split("## Turn 1", 1)[1].split("## Turn 2", 1)[0]
        self.assertIn("CLAUDE_CODE_ENABLE_TODO_TOOLS", turn_one)
        self.assertIn("mcp__ccd_directory__change_directory", turn_one)
        self.assertIn("asking for the resume", turn_one)
        self.assertNotIn("preview_start", turn_one)

    def test_landing_prompt_is_short_and_points_at_the_skill(self) -> None:
        landing = LANDING.read_text(encoding="utf-8")
        self.assertIn(".claude/skills/ftja-setup/SKILL.md", landing)
        prompt = re.search(r'id="setup-prompt"[^>]*>([^<]*)<', landing)
        self.assertIsNotNone(prompt)
        self.assertLess(len(prompt.group(1)), 220)

    def test_the_viewer_has_no_setup_flow_left(self) -> None:
        app = APP.read_text(encoding="utf-8")
        for gone in ("/api/onboarding", "/api/continue-run", "/api/run-bridge", "/api/pick-profile-source", "view-onboarding"):
            self.assertNotIn(gone, app)
        self.assertIn("/api/setup", app)
        self.assertIn("/ftja-setup", app)


if __name__ == "__main__":
    unittest.main()
