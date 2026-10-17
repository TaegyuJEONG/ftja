import json
import tempfile
import unittest
from pathlib import Path

from ftja.codex_tokens import thread_tokens


def event(total):
    return json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": total}}}})


class CodexTokensTests(unittest.TestCase):
    def test_reads_the_last_running_total_of_the_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            day = Path(tmp) / "2026" / "10" / "16"
            day.mkdir(parents=True)
            (day / "rollout-2026-10-16T10-55-49-abc-123.jsonl").write_text(
                "\n".join([json.dumps({"type": "session_meta"}), event(1000), event(4500)]) + "\n", encoding="utf-8")
            self.assertEqual(thread_tokens("abc-123", Path(tmp)), 4500)

    def test_unknown_thread_gives_no_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(thread_tokens("missing", Path(tmp)))


if __name__ == "__main__":
    unittest.main()
