from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from marker_mermaid.review import ReviewHandler
from marker_mermaid.review_store import ReviewStore, ReviewValidationResult


def make_bundle(tmp_path, *, source_id="source-a"):
    diagram = tmp_path / "diagrams" / "diagram-a"
    alternatives = diagram / "alternatives"
    alternatives.mkdir(parents=True)
    images = tmp_path / "images"
    images.mkdir()
    (images / "source.png").write_bytes(b"not-a-real-png")
    (diagram / "manifest.json").write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_image": "images/source.png",
                "grade": "C",
                "status": "review_required",
                "selected_candidate_id": "candidate-a",
                "files": {},
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    (diagram / "final.mmd").write_text("flowchart LR\n  A --> B\n", encoding="utf-8")
    (diagram / "final.svg").write_text("<svg viewBox='0 0 1 1'/>", encoding="utf-8")
    (diagram / "scene-ir.json").write_text(
        json.dumps(
            {
                "diagram_type": "flowchart",
                "elements": [{"id": "A"}, {"id": "B"}],
                "relations": [{"id": "E1", "source_id": "A", "target_id": "B"}],
                "groups": [],
            }
        ),
        encoding="utf-8",
    )
    (diagram / "provenance.json").write_text(
        json.dumps([{"id": "e1", "kind": "ocr_token", "bbox": [0, 0, 1, 1]}]),
        encoding="utf-8",
    )
    (diagram / "scores.json").write_text(
        json.dumps({"warnings": ["check edge direction"]}), encoding="utf-8"
    )
    (diagram / "review-history.json").write_text("[]\n", encoding="utf-8")
    (alternatives / "candidate-b.json").write_text(
        json.dumps(
            {
                "candidate_id": "candidate-b",
                "diagram_type": "flowchart",
                "aggregate_score": 0.7,
                "mermaid_code": "flowchart LR\n  B --> A\n",
                "scene_ir": {
                    "diagram_type": "flowchart",
                    "elements": [{"id": "A"}, {"id": "B"}],
                    "relations": [{"id": "E1", "source_id": "B", "target_id": "A"}],
                    "groups": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return diagram


@contextlib.contextmanager
def running_server(tmp_path, *, token="test-token"):
    validator = lambda code: ReviewValidationResult(  # noqa: E731
        valid="INVALID" not in code,
        svg=f"<svg viewBox='0 0 1 1'><title>{len(code)}</title></svg>",
        png=b"rendered-png",
    )
    store = ReviewStore(tmp_path, validator=validator)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(ReviewHandler, directory=str(tmp_path), store=store, csrf_token=token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def read_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read()), response.headers


def post_json(url, payload, *, token="test-token", origin=None):
    headers = {"Content-Type": "application/json", "X-CSRF-Token": token}
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read()), response.headers


def expected(bundle):
    return {
        "expected_version": bundle.state.version,
        "expected_digest": bundle.state.code_digest,
    }


def test_review_shell_escapes_bootstrap_and_uses_strict_external_assets(tmp_path):
    attack = '<script>alert("x")</script>'
    make_bundle(tmp_path, source_id=attack)
    with running_server(tmp_path) as (base, _):
        with urllib.request.urlopen(f"{base}/", timeout=3) as response:
            page = response.read().decode()
            csp = response.headers["Content-Security-Policy"]
        with urllib.request.urlopen(f"{base}/assets/review.js", timeout=3) as response:
            javascript = response.read().decode()

    assert attack not in page
    assert "&lt;script&gt;alert" in page
    assert '<script src="/assets/review.js" defer></script>' in page
    assert "'unsafe-inline'" not in csp
    assert "script-src 'self'" in csp
    assert "/api/diagrams/" in javascript


def test_api_loads_complete_bundle_without_exposing_revision_files(tmp_path):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, _):
        listing, _ = read_json(f"{base}/api/diagrams")
        loaded, _ = read_json(f"{base}/api/diagrams/diagram-a")
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/diagrams/diagram-a/review-state.json", timeout=3)

    assert listing["diagrams"][0]["id"] == "diagram-a"
    diagram = loaded["diagram"]
    assert diagram["source_url"] == "/images/source.png"
    assert diagram["scene_ir"]["relations"][0]["target_id"] == "B"
    assert diagram["alternatives"][0]["candidate_id"] == "candidate-b"
    assert diagram["issues"] == ["check edge direction"]
    assert error.value.code == 404


def test_edit_requires_csrf_and_atomically_updates_code_ir_render_and_history(tmp_path):
    diagram_path = make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        payload = {
            **expected(current),
            "mermaid_code": "flowchart LR\n  A --> C\n",
            "scene_ir": {
                "diagram_type": "flowchart",
                "elements": [{"id": "A"}, {"id": "C"}],
                "relations": [{"id": "E1", "source_id": "A", "target_id": "C"}],
                "groups": [],
            },
        }
        with pytest.raises(urllib.error.HTTPError) as missing_token:
            post_json(f"{base}/api/diagrams/diagram-a/edits", payload, token="")
        result, _ = post_json(f"{base}/api/diagrams/diagram-a/edits", payload)

    assert missing_token.value.code == 403
    assert result["diagram"]["version"] == 1
    assert result["diagram"]["can_undo"]
    assert (diagram_path / "final.mmd").read_text().endswith("A --> C\n")
    assert json.loads((diagram_path / "scene-ir.json").read_text())["elements"][1]["id"] == "C"
    assert "<title>" in (diagram_path / "final.svg").read_text()
    assert (diagram_path / "final.png").read_bytes() == b"rendered-png"
    assert (
        json.loads((diagram_path / "review-history.json").read_text())[0]["operation"]
        == "edit_mermaid"
    )


def test_stale_edit_and_cross_origin_mutation_are_rejected(tmp_path):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        payload = {
            **expected(current),
            "mermaid_code": current.mermaid_code,
            "scene_ir": current.scene_ir,
        }
        post_json(f"{base}/api/diagrams/diagram-a/edits", payload)
        with pytest.raises(urllib.error.HTTPError) as stale:
            post_json(f"{base}/api/diagrams/diagram-a/edits", payload)
        refreshed = store.load_bundle("diagram-a")
        with pytest.raises(urllib.error.HTTPError) as cross_origin:
            post_json(
                f"{base}/api/diagrams/diagram-a/decision",
                {**expected(refreshed), "decision": "approve"},
                origin="https://attacker.example",
            )

    assert stale.value.code == 409
    assert cross_origin.value.code == 403


def test_candidate_command_decision_and_undo_flow(tmp_path):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        selected, _ = post_json(
            f"{base}/api/diagrams/diagram-a/candidate",
            {**expected(current), "candidate_id": "candidate-b"},
        )
        current = store.load_bundle("diagram-a")
        patched, _ = post_json(
            f"{base}/api/diagrams/diagram-a/commands",
            {**expected(current), "command": "reverse edge B -> A"},
        )
        current = store.load_bundle("diagram-a")
        approved, _ = post_json(
            f"{base}/api/diagrams/diagram-a/decision",
            {**expected(current), "decision": "approve", "reason": "source checked"},
        )
        current = store.load_bundle("diagram-a")
        undone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )

    assert selected["diagram"]["selected_candidate_id"] == "candidate-b"
    assert "A --> B" in patched["diagram"]["mermaid_code"]
    assert approved["diagram"]["decision"] == "approved"
    assert undone["diagram"]["decision"] == "pending"
