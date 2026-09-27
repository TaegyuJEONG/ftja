import importlib.util
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "serve_landing_preview", ROOT / "scripts" / "serve_landing_preview.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the landing preview server")
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class LandingPreviewServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), preview.make_handler(ROOT))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_root_serves_the_canonical_landing_source(self):
        with urllib.request.urlopen(f"{self.base_url}/") as response:
            body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn('id="setup-toggle"', body)

    def test_assets_are_served_from_the_canonical_source(self):
        with urllib.request.urlopen(f"{self.base_url}/assets/value/your-background.svg") as response:
            body = response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
        self.assertIn(b"<svg", body)

    def test_old_public_entry_files_are_not_served(self):
        for path in ("/index.html", "/setup.html"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(f"{self.base_url}{path}")
                self.assertEqual(raised.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
