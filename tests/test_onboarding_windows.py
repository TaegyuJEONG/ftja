"""What the first real setup on Windows ran into after the file chooser."""
import json
import os
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from ftja import osutil
from ftja.onboarding import live_bridge, read_state, write_bridge
from ftja.server import ViewerServer, make_handler


@pytest.fixture
def viewer(tmp_path):
    server = ViewerServer(("127.0.0.1", 0), make_handler(str(tmp_path / "seen.db"), str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def post(payload):
        request = Request(f"http://127.0.0.1:{server.server_port}/api/onboarding-action", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as e:
            return e.code, json.load(e)

    yield str(tmp_path), server, post
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def test_the_same_decision_sent_twice_is_done_once_and_is_not_an_error(viewer):
    project, _, post = viewer
    resume = os.path.join(project, "resume.txt")
    with open(resume, "w", encoding="utf-8") as f:
        f.write("experience")
    write_bridge(project, "listening", ["confirm_profile_sources"], os.getpid())
    assert post({"type": "set_profile_sources", "expected_revision": 0, "sources": [{"path": resume, "kind": "file"}]})[0] == 200
    confirm = {"type": "confirm_profile_sources", "expected_revision": 1}
    assert post(confirm)[0] == 200
    write_bridge(project, "offline", [], None)  # the wait heard it and left
    status, body = post(confirm)  # the double click
    assert status == 200 and body["repeat"] and body["state"]["revision"] == 2
    assert read_state(project)["revision"] == 2
    # a different decision from a page that is behind is still refused
    status, body = post({"type": "set_profile_sources", "expected_revision": 1, "sources": [{"path": resume, "kind": "file"}]})
    assert status == 409


def test_a_wait_that_was_killed_does_not_show_as_listening(tmp_path):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    write_bridge(str(tmp_path), "listening", ["confirm_profile_summary"], gone.pid)
    assert live_bridge(str(tmp_path))["status"] == "offline"
    write_bridge(str(tmp_path), "listening", ["confirm_profile_summary"], os.getpid())
    assert live_bridge(str(tmp_path))["status"] == "listening"


def test_set_state_reads_its_json_from_a_file(tmp_path):
    # PowerShell strips the quotes out of JSON passed on the command line.
    project = str(tmp_path)
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"profile": {"sources": [{"path": str(payload), "kind": "file"}]}}), encoding="utf-8")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    done = subprocess.run([sys.executable, "-m", "ftja.onboarding", project, "set-state", "--stage", "profile", "--phase", "sources",
                           "--json-file", str(payload)], cwd=root, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert read_state(project)["profile"]["sources"][0]["path"] == str(payload)


@pytest.mark.skipif(not osutil.WINDOWS, reason="only Windows lets two servers share a port")
def test_a_second_viewer_cannot_take_a_port_that_is_in_use(viewer):
    project, server, _ = viewer
    with pytest.raises(OSError):
        ViewerServer(("127.0.0.1", server.server_port), make_handler(os.path.join(project, "seen.db"), project))


def test_one_connection_serves_many_requests(viewer):
    # A new connection per request left thousands of closing ones on Windows.
    import http.client
    _, server, _ = viewer
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        for path in ("/api/onboarding", "/", "/api/onboarding", "/api/version", "/api/onboarding"):
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 200 and response.version == 11 and not response.will_close
            response.read()
        port = connection.sock.getsockname()[1]
        connection.request("GET", "/api/onboarding")
        connection.getresponse().read()
        assert connection.sock.getsockname()[1] == port  # still the same connection
        connection.request("POST", "/api/onboarding-action", body=b"{", headers={"Content-Type": "application/json"})
        refused = connection.getresponse()
        assert refused.status == 400 and refused.will_close
    finally:
        connection.close()
