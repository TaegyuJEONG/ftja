import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from ftja.server import make_handler


class ServerLocalOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(root / "seen.db"), str(root)))
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.putrequest(method, path, skip_host=True)
        headers = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if body is not None:
            headers["Content-Length"] = str(len(body))
        for name, value in headers.items():
            conn.putheader(name, value)
        conn.endheaders(body)
        response = conn.getresponse()
        response.read()
        conn.close()
        return response.status

    def save_rubric(self, **headers):
        body = json.dumps({"rubric_md": "# written by a request\n"}).encode()
        return self.request("POST", "/api/config", {"Content-Type": "application/json", **headers}, body)

    def test_the_viewers_own_requests_are_served(self):
        self.assertEqual(self.request("GET", "/api/onboarding"), 200)
        self.assertEqual(self.request("GET", "/api/onboarding", {"Host": f"localhost:{self.port}"}), 200)
        self.assertEqual(self.save_rubric(Origin=f"http://127.0.0.1:{self.port}"), 200)
        self.assertEqual(self.save_rubric(), 200)  # an agent's own curl sends no Origin

    def test_a_rebound_host_name_cannot_read_the_profile(self):
        self.assertEqual(self.request("GET", "/api/onboarding", {"Host": f"attacker.example:{self.port}"}), 403)
        self.assertEqual(self.request("GET", "/api/pipeline", {"Host": "attacker.example"}), 403)

    def test_another_site_cannot_rewrite_the_rubric(self):
        self.assertEqual(self.save_rubric(Origin="https://attacker.example"), 403)
        self.assertEqual(self.save_rubric(Origin="null"), 403)
        # a cross-site form or text/plain post needs no preflight, so it must be refused here
        self.assertEqual(self.save_rubric(**{"Content-Type": "text/plain"}), 415)
        self.assertFalse((self.root / "rubric.md").exists())


if __name__ == "__main__":
    unittest.main()
