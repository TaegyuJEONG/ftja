"""Contract tests for the native FTJA setup checklist."""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILL = ROOT / ".claude/skills/ftja-setup/SKILL.md"
CODEX_SKILL = ROOT / ".agents/skills/ftja-setup/SKILL.md"
LANDING = ROOT / "landing.html"

# These 8 items are things the *user* does or reviews. Turn-1 infrastructure
# (clone, connect, venv, server, browser) is not on this list — see
# ftja.onboarding.CHECKLIST_STAGES, which this list must match exactly.
CHECKLIST = [
    "Add your background files",
    "Review your profile summary",
    "Review your search settings",
    "Review the code-based filter",
    "Answer three questions about your ideal role",
    "Confirm your judgment rubric",
    "Run your first job search",
    "Review your first results",
]


def native_checklist_labels(text: str) -> list[str]:
    section = re.search(
        r"Create these 8 items, in this order\.(?P<body>.*?)\n### Narrate long operations",
        text,
        flags=re.DOTALL,
    )
    if not section:
        return []
    return re.findall(r"^\d+\. `([^`]+)`$", section.group("body"), flags=re.MULTILINE)


def normalized(text: str) -> str:
    return " ".join(text.split())


class SetupSkillContractTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.claude_raw = CLAUDE_SKILL.read_text(encoding="utf-8")
        self.codex_raw = CODEX_SKILL.read_text(encoding="utf-8")
        self.claude = normalized(self.claude_raw)
        self.codex = normalized(self.codex_raw)
        self.landing = LANDING.read_text(encoding="utf-8")

    def test_skills_share_the_same_checklist_in_order(self) -> None:
        self.assertEqual(native_checklist_labels(self.claude_raw), CHECKLIST)
        self.assertEqual(native_checklist_labels(self.codex_raw), CHECKLIST)

    def test_checklist_matches_the_ids_the_server_computes(self) -> None:
        from ftja.onboarding import CHECKLIST_STAGES
        labels = [item["label"] for stage in CHECKLIST_STAGES for item in stage["items"]]
        self.assertEqual(labels, CHECKLIST)

    def test_native_client_contract_and_plan_mode_rule_are_explicit(self) -> None:
        self.assertIn("TaskCreate", self.claude)
        self.assertIn("In Codex, use `update_plan`", self.codex)
        for skill in (self.claude, self.codex):
            self.assertIn("Do not enter Plan Mode", skill)
            self.assertIn("exactly one item `in_progress`", skill)
            self.assertIn("is unavailable", skill)

    def test_setup_is_split_into_two_turns(self) -> None:
        for skill in (self.claude, self.codex):
            self.assertIn("Turn 1: connect and open the web view", skill)
            self.assertIn("Turn 2 onward: the native checklist", skill)
            self.assertIn("Why two turns", skill)
            self.assertIn("only take effect once this turn ends", skill)
            self.assertIn("is expected to find nothing", skill)

    def test_landing_prompt_is_short_and_points_at_the_skill(self) -> None:
        self.assertIn(".claude/skills/ftja-setup/SKILL.md", self.landing)
        # The copy-paste prompt itself must stay short: the long bootstrap
        # detail (settings.local.json, TaskCreate/TaskUpdate) belongs in the
        # skill file, which the prompt just points at.
        prompt_match = re.search(r'id="setup-prompt"[^>]*>([^<]*)<', self.landing)
        self.assertIsNotNone(prompt_match)
        self.assertLess(len(prompt_match.group(1)), 220)
        self.assertNotIn("CLAUDE_CODE_ENABLE_TODO_TOOLS", prompt_match.group(1))

    def test_claude_bootstrap_enables_task_tools_in_the_current_session(self) -> None:
        expected = [
            ".claude/settings.local.json",
            "CLAUDE_CODE_ENABLE_TODO_TOOLS",
            "TaskCreate",
            "TaskUpdate",
        ]
        for text in (self.claude_raw, self.codex_raw):
            for phrase in expected:
                self.assertIn(phrase, text)

    def test_infrastructure_evidence_is_still_required_in_turn_one(self) -> None:
        for skill in (self.claude, self.codex):
            self.assertIn("Folder access granted", skill)
            self.assertIn("`http://127.0.0.1:8765/`", skill)
            self.assertIn("navigation/page-text check succeeds", skill)

    def test_web_confirmations_advance_only_after_durable_evidence(self) -> None:
        action_names = [
            "confirm_profile_sources",
            "confirm_profile_summary",
            "confirm_profile",
            "confirm_stage0",
            "answer_rubric_question",
            "confirm_rubric",
        ]
        for skill in (self.claude, self.codex):
            self.assertIn("`status: confirmed`", skill)
            self.assertIn("full durable state", skill)
            for action in action_names:
                self.assertIn(action, skill)

    def test_narrate_long_operations_uses_the_status_working_command(self) -> None:
        for skill in (self.claude, self.codex):
            self.assertIn("Narrate long operations", skill)
            self.assertIn("onboarding . status working", skill)

    def test_setup_does_not_message_its_own_session(self) -> None:
        # mcp__ccd_session_mgmt__send_message cannot target the current
        # session; the instruction belongs in this turn's response text
        # instead, followed by the blocking wait-for-action call.
        for skill in (self.claude_raw, self.codex_raw):
            self.assertNotIn("send_message", skill)

    def test_chat_copy_and_first_run_boundary_are_truthful(self) -> None:
        for skill in (self.claude, self.codex):
            self.assertIn("one or two short sentences", skill)
            self.assertIn("single next user action", skill)
            self.assertIn("Do not mark `Run your first job search`", skill)
            self.assertIn("Do not mark `Review your first results`", skill)


if __name__ == "__main__":
    unittest.main()
