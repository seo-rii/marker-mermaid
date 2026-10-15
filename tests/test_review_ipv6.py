from __future__ import annotations

import errno
import http.client
import json
import socket
import threading
from functools import partial
from http import HTTPStatus

import pytest
from test_review import make_bundle

from marker_mermaid import review
from marker_mermaid.review import BoundedThreadingHTTPServer, ReviewHandler


@pytest.mark.skipif(not socket.has_ipv6, reason="requires IPv6")
@pytest.mark.parametrize("host", ["::1", "::"])
def test_review_ipv6_listener_accepts_bracketed_loopback_host(tmp_path, host):
    make_bundle(tmp_path)
    try:
        server = BoundedThreadingHTTPServer(
            (host, 0),
            partial(ReviewHandler, directory=str(tmp_path)),
            allowed_hosts={"::1", "localhost"},
        )
    except OSError as exc:
        if exc.errno in {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT}:
            pytest.skip("IPv6 loopback unavailable on this host")
        raise
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection("::1", server.server_port, timeout=3)
    try:
        assert server.address_family == socket.AF_INET6
        connection.request("GET", "/api/diagrams")
        response = connection.getresponse()
        assert response.status == HTTPStatus.OK
        assert json.loads(response.read())["diagrams"][0]["id"] == "diagram-a"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(
    ("host", "url_host"),
    [("::1", "[::1]"), ("::", "[::1]"), ("127.0.0.1", "127.0.0.1")],
)
def test_serve_review_opens_a_valid_listener_url(monkeypatch, tmp_path, capsys, host, url_host):
    make_bundle(tmp_path)
    opened = []
    closed = []

    class Runtime:
        def close(self):
            closed.append("runtime")

    class Server:
        server_port = 12345

        def __init__(self, address, *args, **kwargs):
            assert address == (host, 0)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            closed.append("server")

    monkeypatch.setattr(review, "NodeMermaidRuntime", Runtime)
    monkeypatch.setattr(review, "BoundedThreadingHTTPServer", Server)
    monkeypatch.setattr(review.webbrowser, "open", opened.append)
    review.serve_review(tmp_path, host=host, port=0, open_browser=True)
    expected_url = f"http://{url_host}:12345/"
    assert opened == [expected_url]
    assert expected_url in capsys.readouterr().out
    assert closed == ["server", "runtime"]
