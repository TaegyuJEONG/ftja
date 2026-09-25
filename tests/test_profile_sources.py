import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.request import Request, urlopen

from ftja.pipeline_view import read_config, write_config
from ftja.server import make_handler


class ProfileSourceTests(unittest.TestCase):
    def test_folder_picker_returns_folder_kind(self):
        with TemporaryDirectory() as tmp:
            selected = Path(tmp) / "portfolio"
            selected.mkdir()
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
                str(Path(tmp) / "seen.db"), tmp
            ))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                completed = __import__("subprocess").CompletedProcess(
                    ["osascript"], 0, f"{selected}\n", ""
                )
                body = json.dumps({"kind": "folder"}).encode()
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/pick-profile-source",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("ftja.server.sys.platform", "darwin"), patch(
                    "ftja.server.subprocess.run", return_value=completed
                ) as run:
                    with urlopen(request, timeout=3) as response:
                        payload = json.load(response)
                self.assertEqual(payload["kind"], "folder")
                self.assertEqual(payload["path"], str(selected))
                self.assertEqual(payload["label"], "portfolio")
                self.assertIn("choose folder", run.call_args.args[0][2])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_confirm_action_is_written_for_the_setup_helper(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "resume.txt"
            source.write_text("experience", encoding="utf-8")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
                str(Path(tmp) / "seen.db"), tmp
            ))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def post(payload):
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/onboarding-action",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=3) as response:
                        return json.load(response)

                first = post({
                    "type": "set_profile_sources",
                    "expected_revision": 0,
                    "sources": [{"path": str(source), "kind": "file"}],
                })
                self.assertEqual(first["state"]["revision"], 1)
                self.assertEqual(json.loads((Path(tmp) / "onboarding-action.json").read_text())["type"], "set_profile_sources")
                second = post({"type": "confirm_profile_sources", "expected_revision": 1})
                self.assertTrue(second["state"]["profile"]["sources_confirmed"])
                action = json.loads((Path(tmp) / "onboarding-action.json").read_text())
                self.assertEqual(action["type"], "confirm_profile_sources")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_pipeline_registry_preserves_folder_sources(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp) / "portfolio"
            folder.mkdir()
            write_config(tmp, profile_sources=[{
                "path": str(folder), "label": "My portfolio", "kind": "folder"
            }])
            sources = read_config(tmp)["profile_sources"]
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["kind"], "folder")
            self.assertTrue(sources[0]["exists"])

    def test_onboarding_ui_exposes_add_file_and_folder_actions(self):
        html = Path(__file__).parents[1].joinpath("ftja", "app.html").read_text()
        self.assertIn("Add your background.", html)
        self.assertIn("Fine-tune your job search.", html)
        self.assertIn("Your search settings", html)
        self.assertIn("Published within", html)
        self.assertIn("Past 24 hours (recommended)", html)
        self.assertIn("Confirm search settings", html)
        self.assertIn("Add any files or folders that help FTJA understand your background.", html)
        self.assertIn("Selected items stay on your computer. Your coding agent reads them locally.", html)
        self.assertIn("+ Add files", html)
        self.assertIn("+ Add a folder", html)
        self.assertIn("Confirm sources", html)
        self.assertIn("data-confirm-profile-sources", html)
        self.assertIn("I uploaded my background files, continue the setup", html)
        self.assertIn("sources: [...current, source]", html)
        self.assertNotIn("Choose resume or portfolio file", html)
        self.assertNotIn("You can add more than one. Nothing is uploaded;", html)
        self.assertNotIn("Start with what you know.", html)
        self.assertNotIn("Confirm profile and search draft", html)
        self.assertNotIn("Posting age", html)
        self.assertNotIn("<h3>Your source materials</h3><p>${esc(latestOnboardingMessage(state))}", html)

    def test_public_demo_uses_the_same_profile_language(self):
        index = Path(__file__).parents[1].joinpath("index.html").read_text()
        landing = Path(__file__).parents[1].joinpath("landing.html").read_text()
        self.assertEqual(index, landing)
        for text in ["Review your profile summary.", "Confirm profile summary", "Fine-tune your job search.", "Suggested job titles", "Published within", "Confirm search settings"]:
            self.assertIn(text, index)
        for text in ["Start with what you know.", "Confirm titles", "Posting age", "PROFILE SEARCH DRAFT CONFIRMED"]:
            self.assertNotIn(text, index)


if __name__ == "__main__":
    unittest.main()
