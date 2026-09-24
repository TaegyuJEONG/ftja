import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from ftja.server import make_handler


class LandingAssetTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        handler = make_handler(str(root / "seen.db"), str(root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_value_section_svg_assets_are_served(self):
        names = (
            "your-background.svg",
            "agent-judgment.svg",
            "better-next-searches.svg",
        )
        for name in names:
            with self.subTest(name=name):
                with urllib.request.urlopen(
                    f"{self.base_url}/assets/value/{name}"
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
                    self.assertIn(b"<svg", response.read())


if __name__ == "__main__":
    unittest.main()
