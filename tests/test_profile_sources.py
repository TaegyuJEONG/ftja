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
        self.assertIn("Add any files or folders that help FTJA understand your background.", html)
        self.assertIn("Selected items stay on your computer. Your coding agent reads them locally.", html)
        self.assertIn("+ Add files", html)
        self.assertIn("+ Add a folder", html)
        self.assertIn("sources: [...current, source]", html)
        self.assertNotIn("Choose resume or portfolio file", html)
        self.assertNotIn("You can add more than one. Nothing is uploaded;", html)
        self.assertNotIn("<h3>Your source materials</h3><p>${esc(latestOnboardingMessage(state))}", html)


if __name__ == "__main__":
    unittest.main()
