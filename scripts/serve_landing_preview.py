#!/usr/bin/env python3
"""Serve the tracked FTJA landing source at a stable local preview URL."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class LandingPreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, landing_path: Path, **kwargs):
        self.landing_path = landing_path
        super().__init__(*args, directory=str(landing_path.parent), **kwargs)

    def _is_landing_route(self) -> bool:
        return urlparse(self.path).path in ("/", "/landing", "/landing.html")

    def _serve_landing(self, include_body: bool) -> None:
        body = self.landing_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        if self._is_landing_route():
            self._serve_landing(include_body=True)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if self._is_landing_route():
            self._serve_landing(include_body=False)
            return
        super().do_HEAD()


def make_handler(root: Path):
    landing_path = root / "landing.html"
    return partial(LandingPreviewHandler, landing_path=landing_path)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with ThreadingHTTPServer(("127.0.0.1", 8767), make_handler(root)) as server:
        print("Serving FTJA landing preview at http://127.0.0.1:8767/")
        server.serve_forever()


if __name__ == "__main__":
    main()
