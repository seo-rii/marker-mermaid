"""Read-only local review workspace for source/render/provenance inspection."""

from __future__ import annotations

import html as html_lib
import json
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        self.review_root = Path(directory)
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self):  # noqa: N802 - stdlib handler API
        if self.path.rstrip("/") == "":
            self._index()
            return
        super().do_GET()

    def end_headers(self):
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; object-src 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def _index(self):
        diagrams = []
        for manifest_path in sorted((self.review_root / "diagrams").glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            folder = manifest_path.parent.name
            source = "/" + html_lib.escape(manifest.get("source_image", ""), quote=True)
            source_id = html_lib.escape(str(manifest.get("source_id", folder)))
            grade = html_lib.escape(str(manifest.get("grade", "U")))
            status = html_lib.escape(str(manifest.get("status", "unknown")))
            diagrams.append(
                f"""
                <article>
                  <h2>{source_id}</h2>
                  <p>Grade {grade} · {status}</p>
                  <div class="panes">
                    <figure><figcaption>Original</figcaption><img src="{source}" /></figure>
                    <figure><figcaption>Rendered</figcaption>
                      <img src="/diagrams/{folder}/final.svg" />
                    </figure>
                  </div>
                  <p><a href="/diagrams/{folder}/final.mmd">Mermaid</a> ·
                     <a href="/diagrams/{folder}/scene-ir.json">Scene IR</a> ·
                     <a href="/diagrams/{folder}/provenance.json">Provenance</a> ·
                     <a href="/diagrams/{folder}/scores.json">Scores</a></p>
                </article>
                """
            )
        body = "".join(diagrams) or "<p>No sidecar bundles were found.</p>"
        html = f"""<!doctype html>
        <html><head><meta charset="utf-8"><title>Marker Mermaid Review</title>
        <style>
          body{{font:16px system-ui;margin:2rem;max-width:1400px}}
          article{{border-top:1px solid #ccc;padding:1rem 0}}
          .panes{{display:grid;grid-template-columns:1fr 1fr;gap:1rem}}
          figure{{margin:0}} img{{max-width:100%;max-height:65vh}}
        </style></head><body><h1>Marker Mermaid Review</h1>
        <p>This v0.1 workspace is read-only.</p>{body}</body></html>"""
        payload = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve_review(output_dir: str | Path, *, host: str, port: int, open_browser: bool) -> None:
    root = Path(output_dir).resolve()
    if not (root / "diagrams").is_dir():
        raise FileNotFoundError(f"no diagrams directory below {root}")
    handler = partial(ReviewHandler, directory=str(root))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_port}/"
    print(f"Review workspace: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
