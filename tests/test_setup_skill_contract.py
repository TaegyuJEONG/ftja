"""Contract tests for the native FTJA setup checklist."""
from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLAUDE_SKILL = ROOT / ".claude/skills/ftja-setup/SKILL.md"
CODEX_SKILL = ROOT / ".agents/skills/ftja-setup/SKILL.md"

CHECKLIST = [
    "Clone the FTJA workspace",
    "Connect this session to the workspace",
    "Prepare the local environment",
    "Initialize the onboarding state",
    "Start the local FTJA server",
    "Verify the local server",
    "Open FTJA in the browser",
    "Connect this setup session to FTJA",
    "Add your background files",
    "Confirm your background files",
    "Build your profile summary",
    "Review and confirm your profile summary",
    "Prepare suggested roles and location",
    "Review and confirm your search settings",
    "Prepare the code-based filter",
    "Review and confirm the code-based filter",
    "Define what makes a role a clear yes",
    "Define your dealbreakers",
    "Define your preferences",
    "Review and confirm the judgment rubric",
    "Open the full pipeline",
    "Run your first job search",
    "Review your first results",
]


def native_checklist_labels(text: str) -> list[str]:
    section = re.search(
        r"## Native checklist\n(?P<body>.*?)(?=\n## )",
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

    def test_skills_share_the_same_checklist_in_order(self) -> None:
        self.assertEqual(native_checklist_labels(self.claude_raw), CHECKLIST)
        self.assertEqual(native_checklist_labels(self.codex_raw), CHECKLIST)

    def test_native_client_contract_and_plan_mode_rule_are_explicit(self) -> None:
        self.assertIn("Claude Code native task-list tools", self.claude)
        self.assertIn("In Codex, use `update_plan`", self.codex)
        for skill in (self.claude, self.codex):
            self.assertIn("Do not enter Plan Mode", skill)
            self.assertIn("exactly one checklist task `in_progress`", skill)
            self.assertIn("is unavailable", skill)

    def test_claude_bootstrap_enables_task_tools_in_the_current_session(self) -> None:
        landing = (ROOT / "landing.html").read_text(encoding="utf-8")
        expected = [
            ".claude/settings.local.json",
            "CLAUDE_CODE_ENABLE_TODO_TOOLS",
            "current session",
            "TaskCreate",
            "TaskUpdate",
        ]
        for text in (landing, self.claude_raw, self.codex_raw):
            for phrase in expected:
                self.assertIn(phrase, text)

    def test_infrastructure_tasks_require_observed_evidence(self) -> None:
        for skill in (self.claude, self.codex):
            self.assertIn("Create the complete native checklist before", skill)
            self.assertIn("`.git` exists", skill)
            self.assertIn("expected FTJA remote", skill)
            self.assertIn("A shell `cd` is insufficient", skill)
            self.assertIn("`http://127.0.0.1:8765/`", skill)
            self.assertIn("navigation and expected FTJA page text", skill)
            self.assertIn("bounded action wait/bridge", skill)
            self.assertNotIn("session detection", skill.lower())

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
            self.assertIn("A command invocation alone cannot complete", skill)
            for action in action_names:
                self.assertIn(action, skill)

    def test_chat_copy_and_first_run_boundary_are_truthful(self) -> None:
        for skill in (self.claude, self.codex):
            self.assertIn("one or two short sentences", skill)
            self.assertIn("single next user action", skill)
            self.assertIn("Do not mark `Run your first job search`", skill)
            self.assertIn("Do not mark `Review your first results`", skill)


if __name__ == "__main__":
    unittest.main()
