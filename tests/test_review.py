from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
from PIL import Image

from marker_mermaid.review import BoundedThreadingHTTPServer, ReviewHandler
from marker_mermaid.review_store import MAX_RENDER_BYTES, ReviewStore, ReviewValidationResult


def _png_bytes(size=(1, 1)):
    output = BytesIO()
    Image.new("RGB", size, "white").save(output, format="PNG")
    return output.getvalue()


def make_bundle(tmp_path, *, source_id="source-a", with_png=False):
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
    if with_png:
        (diagram / "final.png").write_bytes(_png_bytes())
    (diagram / "scene-ir.json").write_text(
        json.dumps(
            {
                "diagram_type": "flowchart",
                "elements": [
                    {"id": "A", "role": "node", "bbox": [0, 0, 10, 10]},
                    {"id": "B", "role": "node", "bbox": [20, 0, 30, 10]},
                ],
                "relations": [
                    {
                        "id": "E1",
                        "source_id": "A",
                        "target_id": "B",
                        "relation_type": "edge",
                    }
                ],
                "groups": [],
                "canvas_size": [100, 100],
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
                    "elements": [
                        {"id": "A", "role": "node", "bbox": [0, 0, 10, 10]},
                        {"id": "B", "role": "node", "bbox": [20, 0, 30, 10]},
                    ],
                    "relations": [
                        {
                            "id": "E1",
                            "source_id": "B",
                            "target_id": "A",
                            "relation_type": "edge",
                        }
                    ],
                    "groups": [],
                },
            }
        ),
        encoding="utf-8",
    )
    return diagram


@contextlib.contextmanager
def running_server(tmp_path, *, token="test-token", validator=None):
    effective_validator = validator or (
        lambda code: ReviewValidationResult(
            valid="INVALID" not in code,
            svg=f"<svg viewBox='0 0 1 1'><title>{len(code)}</title></svg>",
            png=_png_bytes(),
        )
    )
    store = ReviewStore(tmp_path, validator=effective_validator)
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
    with urllib.request.urlopen(request, timeout=10) as response:
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


def test_bounded_review_server_rejects_excess_concurrent_requests():
    started = threading.Event()
    release = threading.Event()

    class BlockingHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            started.set()
            release.wait(timeout=3)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass

    server = BoundedThreadingHTTPServer(
        ("127.0.0.1", 0),
        BlockingHandler,
        max_workers=1,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    first_result = []

    def first_request():
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/first", timeout=3
        ) as response:
            first_result.append(response.read())

    request_thread = threading.Thread(target=first_request, daemon=True)
    server_thread.start()
    request_thread.start()
    try:
        assert started.wait(timeout=2)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/second",
                timeout=2,
            )
        assert error.value.code == HTTPStatus.SERVICE_UNAVAILABLE
    finally:
        release.set()
        request_thread.join(timeout=3)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)

    assert first_result == [b"ok"]


def test_incomplete_http_headers_time_out_and_release_worker_slot():
    class ImmediateHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):
            pass

    server = BoundedThreadingHTTPServer(
        ("127.0.0.1", 0),
        ImmediateHandler,
        max_workers=1,
        request_timeout_seconds=0.2,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    slow = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
    try:
        slow.sendall(b"GET /slow HTTP/1.1\r\nHost: 127.0.0.1")
        time.sleep(0.4)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/healthy", timeout=2
        ) as response:
            assert response.read() == b"ok"
    finally:
        slow.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


def test_wildcard_listener_accepts_only_explicit_host_allowlist(tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    server = BoundedThreadingHTTPServer(
        ("0.0.0.0", 0),
        partial(
            ReviewHandler,
            directory=str(tmp_path),
            store=store,
            csrf_token="token",
        ),
        allowed_hosts={"127.0.0.1", "localhost"},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        accepted = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/diagrams",
            headers={"Host": f"127.0.0.1:{server.server_port}"},
        )
        with urllib.request.urlopen(accepted, timeout=2) as response:
            assert response.status == 200
        rejected = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/",
            headers={"Host": f"attacker.example:{server.server_port}"},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(rejected, timeout=2)
        assert error.value.code == HTTPStatus.MISDIRECTED_REQUEST
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_review_rejects_dns_rebinding_host_before_bootstrap_or_mutation(tmp_path):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        get_request = urllib.request.Request(f"{base}/", headers={"Host": "attacker.example"})
        with pytest.raises(urllib.error.HTTPError) as get_error:
            urllib.request.urlopen(get_request, timeout=3)
        current = store.load_bundle("diagram-a")
        post_request = urllib.request.Request(
            f"{base}/api/diagrams/diagram-a/decision",
            data=json.dumps({**expected(current), "decision": "approve"}).encode(),
            headers={
                "Content-Type": "application/json",
                "Host": "attacker.example",
                "Origin": "http://attacker.example",
                "X-CSRF-Token": "test-token",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as post_error:
            urllib.request.urlopen(post_request, timeout=3)

    assert get_error.value.code == HTTPStatus.MISDIRECTED_REQUEST
    assert post_error.value.code == HTTPStatus.MISDIRECTED_REQUEST


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
    assert diagram["diff_view"]["available"] is False
    assert diagram["scene_ir"]["relations"][0]["target_id"] == "B"
    assert diagram["alternatives"][0]["candidate_id"] == "candidate-b"
    assert diagram["issues"] == ["check edge direction"]
    assert error.value.code == 404


def test_diff_descriptor_requires_current_png_and_safe_source_url(tmp_path):
    diagram_path = make_bundle(tmp_path, with_png=True)
    with running_server(tmp_path) as (base, _):
        loaded, _ = read_json(f"{base}/api/diagrams/diagram-a")
        descriptor = loaded["diagram"]["diff_view"]

        manifest_path = diagram_path / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source_image"] = "../secret.png"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        unsafe, _ = read_json(f"{base}/api/diagrams/diagram-a")

    assert descriptor["available"] is True
    assert descriptor["source_url"] == "/images/source.png"
    assert descriptor["render_kind"] == "png"
    assert descriptor["render_dimensions"] == [1, 1]
    assert descriptor["alignment_profile"] == "bounds-contain-center-v1"
    assert descriptor["render_url"].startswith("/diagrams/diagram-a/final.png?digest=")
    assert unsafe["diagram"]["diff_view"]["available"] is False
    assert unsafe["diagram"]["diff_view"]["source_url"] is None


def test_diff_descriptor_rejects_oversized_png_dimensions(tmp_path):
    diagram_path = make_bundle(tmp_path, with_png=True)
    (diagram_path / "final.png").write_bytes(_png_bytes((8_193, 1)))

    with running_server(tmp_path) as (base, _):
        loaded, _ = read_json(f"{base}/api/diagrams/diagram-a")

    assert loaded["diagram"]["diff_view"]["available"] is False
    assert loaded["diagram"]["diff_view"]["render_dimensions"] is None


def test_diff_render_url_rejects_stale_digest_after_png_replacement(tmp_path):
    diagram_path = make_bundle(tmp_path, with_png=True)
    with running_server(tmp_path) as (base, _):
        loaded, _ = read_json(f"{base}/api/diagrams/diagram-a")
        render_url = loaded["diagram"]["diff_view"]["render_url"]
        with urllib.request.urlopen(f"{base}{render_url}", timeout=3) as response:
            initial = response.read()

        (diagram_path / "final.png").write_bytes(_png_bytes((2, 1)))
        with pytest.raises(urllib.error.HTTPError) as stale:
            urllib.request.urlopen(f"{base}{render_url}", timeout=3)

    assert initial == _png_bytes()
    assert stale.value.code == HTTPStatus.CONFLICT


def test_static_artifacts_never_follow_file_or_directory_symlinks(tmp_path):
    make_bundle(tmp_path)
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("do not expose", encoding="utf-8")
    (tmp_path / "images" / "leak").symlink_to(secret)
    outside_bundle = tmp_path / "outside-bundle"
    outside_bundle.mkdir()
    (outside_bundle / "final.svg").write_text("<svg><text>secret</text></svg>", encoding="utf-8")
    (tmp_path / "diagrams" / "leak").symlink_to(outside_bundle, target_is_directory=True)

    with running_server(tmp_path) as (base, _):
        for path in ("/images/leak", "/diagrams/leak/final.svg"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"{base}{path}", timeout=3)
            assert error.value.code == 404


def test_static_artifact_open_rejects_symlink_swapped_at_final_open(monkeypatch, tmp_path):
    make_bundle(tmp_path)
    source = tmp_path / "images" / "source.png"
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("do not expose", encoding="utf-8")
    real_open = os.open
    swapped = False

    def swap_before_final_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "source.png" and dir_fd is not None and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(secret)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_final_open)
    with (
        running_server(tmp_path) as (base, _),
        pytest.raises(urllib.error.HTTPError) as error,
    ):
        urllib.request.urlopen(f"{base}/images/source.png", timeout=3)

    assert swapped
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
                "elements": [
                    {"id": "A", "role": "node", "bbox": [0, 0, 10, 10]},
                    {"id": "C", "role": "node", "bbox": [20, 0, 30, 10]},
                ],
                "relations": [
                    {
                        "id": "E1",
                        "source_id": "A",
                        "target_id": "C",
                        "relation_type": "edge",
                    }
                ],
                "groups": [],
            },
        }
        with pytest.raises(urllib.error.HTTPError) as missing_token:
            post_json(f"{base}/api/diagrams/diagram-a/edits", payload, token="")
        result, _ = post_json(f"{base}/api/diagrams/diagram-a/edits", payload)

    assert missing_token.value.code == 403
    assert result["diagram"]["version"] == 1
    assert result["diagram"]["can_undo"]
    assert result["diagram"]["diff_view"]["available"] is True
    assert result["diagram"]["diff_view"]["render_url"].startswith(
        "/diagrams/diagram-a/final.png?digest="
    )
    assert (diagram_path / "final.mmd").read_text().endswith("A --> C\n")
    assert json.loads((diagram_path / "scene-ir.json").read_text())["elements"][1]["id"] == "C"
    assert "<title>" in (diagram_path / "final.svg").read_text()
    assert (diagram_path / "final.png").read_bytes() == _png_bytes()
    assert (
        json.loads((diagram_path / "review-history.json").read_text())[0]["operation"]
        == "edit_mermaid"
    )


def test_oversized_render_is_rejected_before_any_bundle_file_changes(tmp_path):
    diagram = make_bundle(tmp_path)
    store = ReviewStore(
        tmp_path,
        validator=lambda code: ReviewValidationResult(
            valid=True,
            svg="x" * (MAX_RENDER_BYTES + 1),
        ),
    )
    current = store.load_bundle("diagram-a")
    before = {
        path.relative_to(diagram): path.read_bytes()
        for path in diagram.rglob("*")
        if path.is_file()
    }

    with pytest.raises(Exception, match="artifact size limit"):
        store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\n  B --> A\n",
            **expected(current),
        )

    after = {
        path.relative_to(diagram): path.read_bytes()
        for path in diagram.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_http_edit_rejects_model_copy_bypassed_render_before_writing(tmp_path):
    diagram = make_bundle(tmp_path)
    valid_shape = ReviewValidationResult(
        valid=True,
        svg="<svg viewBox='0 0 1 1'/>",
        png=_png_bytes(),
    )
    bypassed = valid_shape.model_copy(update={"png": b"not-a-png"})
    before = {
        path.relative_to(diagram): path.read_bytes()
        for path in diagram.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }

    with running_server(tmp_path, validator=lambda code: bypassed) as (base, store):
        current = store.load_bundle("diagram-a")
        with pytest.raises(urllib.error.HTTPError) as rejected:
            post_json(
                f"{base}/api/diagrams/diagram-a/edits",
                {
                    **expected(current),
                    "mermaid_code": "flowchart LR\n  B --> A\n",
                    "scene_ir": current.scene_ir,
                },
            )

    after = {
        path.relative_to(diagram): path.read_bytes()
        for path in diagram.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    assert rejected.value.code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert after == before


def test_undo_atomically_restores_absent_optional_artifacts(tmp_path):
    diagram = make_bundle(tmp_path)
    (diagram / "scene-ir.json").unlink()
    (diagram / "final.svg").unlink()
    store = ReviewStore(
        tmp_path,
        validator=lambda code: ReviewValidationResult(
            valid=True,
            svg="<svg viewBox='0 0 1 1'/>",
            png=_png_bytes(),
        ),
    )
    current = store.load_bundle("diagram-a")
    edited = store.apply_edit(
        "diagram-a",
        "flowchart LR\n  B --> A\n",
        scene_ir={"elements": [], "relations": [], "groups": []},
        **expected(current),
    )
    assert (diagram / "scene-ir.json").exists()
    assert (diagram / "final.svg").exists()
    assert (diagram / "final.png").exists()

    undone = store.undo("diagram-a", **expected(edited))

    assert undone.scene_ir is None
    assert undone.svg is None
    assert undone.png is None
    assert not (diagram / "scene-ir.json").exists()
    assert not (diagram / "final.svg").exists()
    assert not (diagram / "final.png").exists()
    manifest_hashes = json.loads((diagram / "manifest.json").read_text())["files"]
    assert "scene-ir.json" not in manifest_hashes
    assert "final.svg" not in manifest_hashes
    assert "final.png" not in manifest_hashes


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
    diagram_path = make_bundle(tmp_path)
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
            {**expected(current), "action": "undo", "reason": "step back for comparison"},
        )

    assert selected["diagram"]["selected_candidate_id"] == "candidate-b"
    assert "A --> B" in patched["diagram"]["mermaid_code"]
    assert approved["diagram"]["decision"] == "approved"
    assert undone["diagram"]["decision"] == "pending"
    entries = json.loads((diagram_path / "review-history.json").read_text())
    reverse = next(entry for entry in entries if entry["operation"] == "reverse_edge")
    assert reverse["target"] == "E1"
    assert reverse["before"] == {"source": "B", "target": "A"}
    assert reverse["after"] == {"source": "A", "target": "B"}
    undo = next(entry for entry in entries if entry["operation"] == "undo")
    assert undo["reason"] == "step back for comparison"


def test_history_api_can_restore_an_active_timeline_revision(tmp_path):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        baseline = store.load_bundle("diagram-a")
        edited = store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\n  A --> C\n",
            expected_version=baseline.state.version,
            expected_digest=baseline.state.code_digest,
        )
        with pytest.raises(urllib.error.HTTPError) as extra_field:
            post_json(
                f"{base}/api/diagrams/diagram-a/history",
                {
                    **expected(edited),
                    "action": "checkout",
                    "revision": "r000000",
                    "artifact_path": "versions/r000000.mmd",
                },
            )
        restored, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(edited), "action": "checkout", "revision": "r000000"},
        )
        with pytest.raises(urllib.error.HTTPError) as stale_invalid:
            post_json(
                f"{base}/api/diagrams/diagram-a/history",
                {**expected(edited), "action": "checkout", "revision": "../../r000000"},
            )
        current = store.load_bundle("diagram-a")
        with pytest.raises(urllib.error.HTTPError) as invalid_revision:
            post_json(
                f"{base}/api/diagrams/diagram-a/history",
                {**expected(current), "action": "checkout", "revision": "../../r000000"},
            )

    diagram = restored["diagram"]
    assert diagram["revision_navigation"]["current_revision"] == "r000000"
    assert diagram["mermaid_code"] == baseline.mermaid_code
    assert diagram["revision_navigation"]["timeline"] == [
        "r000000",
        "r000001",
    ]
    assert diagram["revision_navigation"]["cursor"] == 0
    assert store.load_bundle("diagram-a").history[-1].operation == "checkout_revision"
    assert extra_field.value.code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert stale_invalid.value.code == HTTPStatus.CONFLICT
    assert invalid_revision.value.code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_group_command_persists_member_bbox_union_through_scene_schema(tmp_path):
    diagram_path = make_bundle(tmp_path)
    (diagram_path / "final.mmd").write_text(
        'flowchart LR\n  A["A"]\n  B["B"]\n  A --> B\n', encoding="utf-8"
    )
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        grouped, _ = post_json(
            f"{base}/api/diagrams/diagram-a/commands",
            {**expected(current), "command": "group nodes A, B as Pair"},
        )

    [group] = grouped["diagram"]["scene_ir"]["groups"]
    assert group["member_ids"] == ["A", "B"]
    assert group["bbox"] == [0.0, 0.0, 30.0, 10.0]


def test_evidence_relabel_is_revisioned_audited_stale_safe_and_undoable(tmp_path):
    diagram_path = make_bundle(tmp_path)
    (diagram_path / "final.mmd").write_text(
        'flowchart LR\n  A["Old label"]\n  B["B"]\n  A --> B\n', encoding="utf-8"
    )
    scene = json.loads((diagram_path / "scene-ir.json").read_text(encoding="utf-8"))
    scene["elements"][0]["text"] = "Old label"
    scene["elements"][0]["evidence_ids"] = ["e1"]
    scene["elements"][1]["text"] = "B"
    scene["elements"][1]["evidence_ids"] = []
    (diagram_path / "scene-ir.json").write_text(json.dumps(scene), encoding="utf-8")
    (diagram_path / "provenance.json").write_text(
        json.dumps(
            [
                {
                    "id": "e1",
                    "kind": "ocr_token",
                    "bbox": [0, 0, 10, 10],
                    "text": "결제 승인입니다.",
                    "score": 0.93,
                    "source_block_ids": ["source-a"],
                }
            ]
        ),
        encoding="utf-8",
    )

    with running_server(tmp_path) as (base, store):
        baseline = store.load_bundle("diagram-a")
        provenance_before = [item.model_dump(mode="json") for item in baseline.provenance]
        payload = {
            **expected(baseline),
            "operation": {
                "operation": "relabel_node_from_evidence",
                "node_id": "A",
                "evidence_id": "e1",
            },
        }
        relabelled, _ = post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        with pytest.raises(urllib.error.HTTPError) as stale:
            post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        current = store.load_bundle("diagram-a")
        undone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )
        current = store.load_bundle("diagram-a")
        redone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "redo"},
        )

    diagram = relabelled["diagram"]
    assert diagram["scene_ir"]["elements"][0]["text"] == "결제 승인입니다."
    assert diagram["scene_ir"]["elements"][0]["evidence_ids"] == ["e1"]
    assert 'A["결제 승인입니다."]' in diagram["mermaid_code"]
    assert diagram["provenance"] == provenance_before
    assert stale.value.code == HTTPStatus.CONFLICT
    assert undone["diagram"]["scene_ir"]["elements"][0]["text"] == "Old label"
    assert 'A["Old label"]' in undone["diagram"]["mermaid_code"]
    assert undone["diagram"]["provenance"] == provenance_before
    assert redone["diagram"]["scene_ir"]["elements"][0]["text"] == "결제 승인입니다."
    assert redone["diagram"]["provenance"] == provenance_before
    entries = json.loads((diagram_path / "review-history.json").read_text(encoding="utf-8"))
    relabel = next(entry for entry in entries if entry["operation"] == "relabel_node_from_evidence")
    assert relabel["target"] == "A"
    assert relabel["before"] == {"text": "Old label"}
    assert relabel["after"] == {"text": "결제 승인입니다.", "evidence_id": "e1"}
    assert relabel["reason"] == "selected ocr_token evidence e1"


def test_structured_group_is_validated_rendered_audited_and_preserves_source_state(tmp_path):
    diagram_path = make_bundle(tmp_path)
    (diagram_path / "final.mmd").write_text(
        'flowchart LR\n  A["A"]\n  B["B"]\n  A --> B\n', encoding="utf-8"
    )
    source_before = (tmp_path / "images" / "source.png").read_bytes()
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        provenance_before = [item.model_dump(mode="json") for item in current.provenance]
        elements_before = current.scene_ir["elements"]
        relations_before = current.scene_ir["relations"]
        moved, _ = post_json(
            f"{base}/api/diagrams/diagram-a/operations",
            {
                **expected(current),
                "operation": {
                    "operation": "move_node",
                    "node_id": "A",
                    "position": [0.2, 0.8],
                },
            },
        )
        current = store.load_bundle("diagram-a")
        payload = {
            **expected(current),
            "operation": {
                "operation": "group_nodes",
                "node_ids": ["A", "B"],
                "label": "Pair",
            },
            "reason": "confirmed logical boundary",
        }
        result, _ = post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        with pytest.raises(urllib.error.HTTPError) as stale:
            post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        current = store.load_bundle("diagram-a")
        undone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )
        current = store.load_bundle("diagram-a")
        redone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "redo"},
        )

    diagram = result["diagram"]
    assert diagram["scene_ir"]["groups"][0]["member_ids"] == ["A", "B"]
    assert diagram["scene_ir"]["groups"][0]["bbox"] == [0.0, 0.0, 30.0, 10.0]
    assert diagram["scene_ir"]["elements"] == elements_before
    assert diagram["scene_ir"]["relations"] == relations_before
    assert 'subgraph group_A_B["Pair"]' in diagram["mermaid_code"]
    assert diagram["provenance"] == provenance_before
    assert diagram["layout_hints"] == moved["diagram"]["layout_hints"]
    assert (tmp_path / "images" / "source.png").read_bytes() == source_before
    assert stale.value.code == HTTPStatus.CONFLICT
    assert undone["diagram"]["scene_ir"]["groups"] == []
    assert 'subgraph group_A_B["Pair"]' not in undone["diagram"]["mermaid_code"]
    assert redone["diagram"]["scene_ir"]["groups"][0]["member_ids"] == ["A", "B"]
    assert redone["diagram"]["layout_hints"] == moved["diagram"]["layout_hints"]
    entries = json.loads((diagram_path / "review-history.json").read_text())
    grouped = next(entry for entry in entries if entry["operation"] == "group_nodes")
    assert grouped["target"] == "group_A_B"
    assert grouped["after"]["member_ids"] == ["A", "B"]
    assert grouped["reason"] == "confirmed logical boundary"


def test_structured_group_delete_preserves_members_and_undoes_atomically(tmp_path):
    diagram_path = make_bundle(tmp_path)
    scene_path = diagram_path / "scene-ir.json"
    scene = json.loads(scene_path.read_text())
    scene["groups"] = [
        {
            "id": "group_A_B",
            "role": "subgraph",
            "label": "Pair",
            "bbox": [0, 0, 30, 10],
            "member_ids": ["A", "B"],
        }
    ]
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    (diagram_path / "final.mmd").write_text(
        'flowchart LR\n  A["A"]\n  B["B"]\n  A --> B\n'
        '  subgraph group_A_B["Pair"]\n    A\n    B\n  end\n',
        encoding="utf-8",
    )
    source_before = (tmp_path / "images" / "source.png").read_bytes()
    with running_server(tmp_path) as (base, store):
        initial = store.load_bundle("diagram-a")
        current = store.apply_layout_hint(
            "diagram-a",
            node_id="A",
            x=0.2,
            y=0.8,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )
        provenance_before = [item.model_dump(mode="json") for item in current.provenance]
        payload = {
            **expected(current),
            "operation": {"operation": "delete_group", "group_id": "group_A_B"},
            "reason": "group boundary removed",
        }
        deleted, _ = post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        with pytest.raises(urllib.error.HTTPError) as stale:
            post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        current = store.load_bundle("diagram-a")
        undone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )

    assert deleted["diagram"]["scene_ir"]["groups"] == []
    assert [item["id"] for item in deleted["diagram"]["scene_ir"]["elements"]] == ["A", "B"]
    assert [item["id"] for item in deleted["diagram"]["scene_ir"]["relations"]] == ["E1"]
    assert "subgraph group_A_B" not in deleted["diagram"]["mermaid_code"]
    assert 'A["A"]' in deleted["diagram"]["mermaid_code"]
    assert deleted["diagram"]["provenance"] == provenance_before
    assert deleted["diagram"]["layout_hints"]["nodes"][0]["node_id"] == "A"
    assert undone["diagram"]["scene_ir"]["groups"][0]["id"] == "group_A_B"
    assert "subgraph group_A_B" in undone["diagram"]["mermaid_code"]
    assert stale.value.code == HTTPStatus.CONFLICT
    assert (tmp_path / "images" / "source.png").read_bytes() == source_before
    entries = json.loads((diagram_path / "review-history.json").read_text())
    deletion = next(entry for entry in entries if entry["operation"] == "delete_group")
    assert deletion["before"]["member_ids"] == ["A", "B"]
    assert deletion["after"] == {"deleted": "group_A_B"}


def test_structured_edge_add_delete_revisions_evidence_and_undoes_atomically(tmp_path):
    diagram_path = make_bundle(tmp_path)
    (diagram_path / "final.mmd").write_text(
        'flowchart LR\n  A["A"]\n  B["B"]\n  A --> B\n', encoding="utf-8"
    )
    source_before = (tmp_path / "images" / "source.png").read_bytes()
    with running_server(tmp_path) as (base, store):
        initial = store.load_bundle("diagram-a")
        current = store.apply_layout_hint(
            "diagram-a",
            node_id="A",
            x=0.2,
            y=0.8,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )
        provenance_before = [item.model_dump(mode="json") for item in current.provenance]
        add_payload = {
            **expected(current),
            "operation": {
                "operation": "add_edge",
                "source_id": "B",
                "target_id": "A",
            },
            "reason": "confirmed connector on source",
        }
        added, _ = post_json(f"{base}/api/diagrams/diagram-a/operations", add_payload)
        with pytest.raises(urllib.error.HTTPError) as stale:
            post_json(f"{base}/api/diagrams/diagram-a/operations", add_payload)
        current = store.load_bundle("diagram-a")
        added_relation = next(
            item
            for item in added["diagram"]["scene_ir"]["relations"]
            if item["source_id"] == "B" and item["target_id"] == "A"
        )
        deleted, _ = post_json(
            f"{base}/api/diagrams/diagram-a/operations",
            {
                **expected(current),
                "operation": {
                    "operation": "delete_edge",
                    "edge_id": added_relation["id"],
                },
                "reason": "connector removed after review",
            },
        )
        current = store.load_bundle("diagram-a")
        undo_delete, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )
        current = store.load_bundle("diagram-a")
        undo_add, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )

    evidence_id = added_relation["evidence_ids"][0]
    assert added_relation["id"] == "user-edge-r000002"
    assert evidence_id == "user-edit-r000002-edge"
    assert "B --> A" in added["diagram"]["mermaid_code"]
    assert any(item["id"] == evidence_id for item in added["diagram"]["provenance"])
    assert [item["id"] for item in deleted["diagram"]["scene_ir"]["relations"]] == ["E1"]
    assert "B --> A" not in deleted["diagram"]["mermaid_code"]
    assert any(item["id"] == evidence_id for item in deleted["diagram"]["provenance"])
    assert any(
        item["id"] == added_relation["id"]
        for item in undo_delete["diagram"]["scene_ir"]["relations"]
    )
    assert undo_add["diagram"]["provenance"] == provenance_before
    assert [item["id"] for item in undo_add["diagram"]["scene_ir"]["relations"]] == ["E1"]
    assert undo_add["diagram"]["layout_hints"]["nodes"] == [{"node_id": "A", "x": 0.2, "y": 0.8}]
    assert stale.value.code == HTTPStatus.CONFLICT
    assert (tmp_path / "images" / "source.png").read_bytes() == source_before
    entries = json.loads((diagram_path / "review-history.json").read_text())
    assert any(entry["operation"] == "add_edge" for entry in entries)
    assert any(entry["operation"] == "delete_edge" for entry in entries)


def test_structured_edge_add_render_failure_leaves_every_bundle_file_unchanged(tmp_path):
    diagram_path = make_bundle(tmp_path)
    (diagram_path / "final.mmd").write_text(
        'flowchart LR\n  A["A"]\n  B["B"]\n  A --> B\n', encoding="utf-8"
    )
    before = {
        path.relative_to(diagram_path): path.read_bytes()
        for path in diagram_path.rglob("*")
        if path.is_file()
    }
    validator = lambda code: ReviewValidationResult(  # noqa: E731
        valid="B --> A" not in code,
        svg="<svg viewBox='0 0 1 1'/>",
        error="injected render rejection",
    )
    with running_server(tmp_path, validator=validator) as (base, store):
        current = store.load_bundle("diagram-a")
        with pytest.raises(urllib.error.HTTPError) as rejected:
            post_json(
                f"{base}/api/diagrams/diagram-a/operations",
                {
                    **expected(current),
                    "operation": {
                        "operation": "add_edge",
                        "source_id": "B",
                        "target_id": "A",
                    },
                    "reason": "confirmed connector on source",
                },
            )

    after = {
        path.relative_to(diagram_path): path.read_bytes()
        for path in diagram_path.rglob("*")
        if path.is_file()
    }
    assert rejected.value.code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert after == before


def test_structured_edge_operation_is_validated_rendered_and_audited(tmp_path):
    diagram_path = make_bundle(tmp_path)
    source_before = (tmp_path / "images" / "source.png").read_bytes()
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        provenance_before = [item.model_dump(mode="json") for item in current.provenance]
        moved, _ = post_json(
            f"{base}/api/diagrams/diagram-a/operations",
            {
                **expected(current),
                "operation": {
                    "operation": "move_node",
                    "node_id": "A",
                    "position": [0.2, 0.8],
                },
            },
        )
        current = store.load_bundle("diagram-a")
        payload = {
            **expected(current),
            "operation": {
                "operation": "reconnect_edge",
                "edge_id": "E1",
                "source_id": "B",
                "target_id": "A",
            },
            "reason": "confirmed against source",
        }
        result, _ = post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        with pytest.raises(urllib.error.HTTPError) as stale:
            post_json(f"{base}/api/diagrams/diagram-a/operations", payload)

    diagram = result["diagram"]
    assert diagram["scene_ir"]["relations"][0]["source_id"] == "B"
    assert diagram["scene_ir"]["relations"][0]["target_id"] == "A"
    assert "B --> A" in diagram["mermaid_code"]
    assert diagram["provenance"] == provenance_before
    assert diagram["layout_hints"] == moved["diagram"]["layout_hints"]
    assert (tmp_path / "images" / "source.png").read_bytes() == source_before
    assert stale.value.code == HTTPStatus.CONFLICT
    entries = json.loads((diagram_path / "review-history.json").read_text())
    reconnect = next(entry for entry in entries if entry["operation"] == "reconnect_edge")
    assert reconnect["target"] == "E1"
    assert reconnect["before"] == {"source": "A", "target": "B"}
    assert reconnect["after"] == {"source": "B", "target": "A"}
    assert reconnect["reason"] == "confirmed against source"


def test_layout_move_operation_is_advisory_versioned_and_stale_safe(tmp_path):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        original_code = current.mermaid_code
        original_bbox = current.scene_ir["elements"][0]["bbox"]
        payload = {
            **expected(current),
            "operation": {
                "operation": "move_node",
                "node_id": "A",
                "position": [0.2, 0.8],
            },
            "reason": "advisory placement",
        }
        moved, _ = post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        with pytest.raises(urllib.error.HTTPError) as stale:
            post_json(f"{base}/api/diagrams/diagram-a/operations", payload)
        latest = store.load_bundle("diagram-a")
        with pytest.raises(urllib.error.HTTPError) as extra_field:
            post_json(
                f"{base}/api/diagrams/diagram-a/operations",
                {
                    **expected(latest),
                    "operation": {
                        "operation": "move_node",
                        "node_id": "B",
                        "position": [0.4, 0.4],
                        "url": "https://example.invalid",
                    },
                },
            )

    diagram = moved["diagram"]
    assert diagram["mermaid_code"] == original_code
    assert diagram["scene_ir"]["elements"][0]["bbox"] == original_bbox
    assert diagram["layout_hints"]["coordinate_space"] == "normalized"
    assert diagram["layout_hints"]["nodes"] == [{"node_id": "A", "x": 0.2, "y": 0.8}]
    assert stale.value.code == HTTPStatus.CONFLICT
    assert extra_field.value.code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_candidate_selection_clears_layout_and_undo_restores_it(tmp_path):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        initial = store.load_bundle("diagram-a")
        moved = store.apply_layout_hint(
            "diagram-a",
            node_id="A",
            x=0.25,
            y=0.5,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )
        selected, _ = post_json(
            f"{base}/api/diagrams/diagram-a/candidate",
            {**expected(moved), "candidate_id": "candidate-b"},
        )
        current = store.load_bundle("diagram-a")
        undone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )

    assert selected["diagram"]["layout_hints"] is None
    assert undone["diagram"]["layout_hints"]["nodes"][0]["node_id"] == "A"


def test_structured_operation_schema_failure_leaves_bundle_unchanged(tmp_path):
    diagram_path = make_bundle(tmp_path)
    before = {
        path.relative_to(diagram_path): path.read_bytes()
        for path in diagram_path.rglob("*")
        if path.is_file()
    }
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        with pytest.raises(urllib.error.HTTPError) as invalid:
            post_json(
                f"{base}/api/diagrams/diagram-a/operations",
                {
                    **expected(current),
                    "operation": {
                        "operation": "reconnect_edge",
                        "edge_id": "E1",
                        "source_id": "B",
                    },
                },
            )

    after = {
        path.relative_to(diagram_path): path.read_bytes()
        for path in diagram_path.rglob("*")
        if path.is_file()
    }
    assert invalid.value.code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert after == before


def test_structured_node_delete_persists_validated_ir_code_and_render(tmp_path):
    diagram_path = make_bundle(tmp_path)
    (diagram_path / "final.mmd").write_text(
        'flowchart LR\n  A["Start"]\n  B["End"]\n  A --> B\n',
        encoding="utf-8",
    )
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        result, _ = post_json(
            f"{base}/api/diagrams/diagram-a/operations",
            {
                **expected(current),
                "operation": {"operation": "delete_node", "node_id": "A"},
            },
        )

    diagram = result["diagram"]
    assert [item["id"] for item in diagram["scene_ir"]["elements"]] == ["B"]
    assert diagram["scene_ir"]["relations"] == []
    assert diagram["mermaid_code"] == 'flowchart LR\n  B["End"]\n'
    assert "<title>" in (diagram_path / "final.svg").read_text()
    entries = json.loads((diagram_path / "review-history.json").read_text())
    deletion = next(entry for entry in entries if entry["operation"] == "delete_node")
    assert deletion["target"] == "A"
    assert deletion["after"] == {"deleted": "A"}


def test_source_anchored_add_revisions_user_evidence_and_undo_restores_both(tmp_path):
    diagram_path = make_bundle(tmp_path)
    with running_server(tmp_path) as (base, store):
        current = store.load_bundle("diagram-a")
        added, _ = post_json(
            f"{base}/api/diagrams/diagram-a/operations",
            {
                **expected(current),
                "operation": {
                    "operation": "add_node",
                    "node_id": "Review",
                    "label": "Manual review",
                    "bbox": [40, 20, 70, 40],
                },
                "reason": "confirmed on source image",
            },
        )
        current = store.load_bundle("diagram-a")
        undone, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )

    added_diagram = added["diagram"]
    node = added_diagram["scene_ir"]["elements"][-1]
    evidence_id = node["evidence_ids"][0]
    assert node["id"] == "Review"
    assert evidence_id.startswith("user-edit-r000001-Review")
    assert 'Review["Manual review"]' in added_diagram["mermaid_code"]
    user_evidence = next(item for item in added_diagram["provenance"] if item["id"] == evidence_id)
    assert user_evidence["kind"] == "user_edit"
    assert user_evidence["bbox"] == [40.0, 20.0, 70.0, 40.0]
    assert user_evidence["source_block_ids"] == ["source-a"]
    assert all(item["id"] != "Review" for item in undone["diagram"]["scene_ir"]["elements"])
    assert all(item["id"] != evidence_id for item in undone["diagram"]["provenance"])
    manifest = json.loads((diagram_path / "manifest.json").read_text())
    assert manifest["files"]["provenance.json"]
