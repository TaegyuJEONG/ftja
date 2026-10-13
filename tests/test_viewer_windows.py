"""What the first real setup on Windows ran into in the viewer."""
import os
import threading

import pytest

from ftja import osutil
from ftja.server import ViewerServer, make_handler


@pytest.fixture
def viewer(tmp_path):
    server = ViewerServer(("127.0.0.1", 0), make_handler(str(tmp_path / "seen.db"), str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield str(tmp_path), server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.mark.skipif(not osutil.WINDOWS, reason="only Windows lets two servers share a port")
def test_a_second_viewer_cannot_take_a_port_that_is_in_use(viewer):
    project, server = viewer
    with pytest.raises(OSError):
        ViewerServer(("127.0.0.1", server.server_port), make_handler(os.path.join(project, "seen.db"), project))


def test_one_connection_serves_many_requests(viewer):
    # A new connection per request left thousands of closing ones on Windows.
    import http.client
    _, server = viewer
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        for path in ("/api/setup", "/", "/api/setup", "/api/version", "/api/live"):
            connection.request("GET", path)
            response = connection.getresponse()
            assert response.status == 200 and response.version == 11 and not response.will_close
            response.read()
        port = connection.sock.getsockname()[1]
        connection.request("GET", "/api/setup")
        connection.getresponse().read()
        assert connection.sock.getsockname()[1] == port  # still the same connection
        connection.request("POST", "/api/config", body=b"{", headers={"Content-Type": "application/json"})
        refused = connection.getresponse()
        assert refused.status == 400 and refused.will_close
    finally:
        connection.close()


def test_the_page_says_whether_setup_has_written_its_files(viewer):
    import http.client
    import json
    project, server = viewer

    def setup():
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/api/setup")
        body = json.loads(connection.getresponse().read())
        connection.close()
        return body

    assert setup() == {"has_criteria": False, "has_rubric": False}
    for name in ("criteria.json", "rubric.md"):
        with open(os.path.join(project, name), "w", encoding="utf-8") as f:
            f.write("{}")
    assert setup() == {"has_criteria": True, "has_rubric": True}


def test_the_viewer_no_longer_offers_bridge_or_setup_endpoints(viewer):
    import http.client
    _, server = viewer
    for path in ("/api/onboarding-action", "/api/continue-run", "/api/pick-profile-source"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
        assert connection.getresponse().status == 404
        connection.close()
