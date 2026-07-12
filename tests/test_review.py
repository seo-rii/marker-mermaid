from __future__ import annotations

import json
import threading
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

from marker_mermaid.review import ReviewHandler


def test_review_index_escapes_manifest_text(tmp_path):
    diagram = tmp_path / "diagrams" / "safe"
    diagram.mkdir(parents=True)
    (tmp_path / "images").mkdir()
    (diagram / "manifest.json").write_text(
        json.dumps(
            {
                "source_id": "<script>alert(1)</script>",
                "source_image": "images/source.png",
                "grade": "B",
                "status": "success",
            }
        ),
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(ReviewHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/", timeout=3
        ) as response:
            page = response.read().decode()
            csp = response.headers["Content-Security-Policy"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert "object-src 'none'" in csp
