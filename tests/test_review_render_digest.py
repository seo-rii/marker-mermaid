from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from http import HTTPStatus

import pytest
from test_review import expected, make_bundle, post_json, read_json, running_server

from marker_mermaid.review import ReviewHandler


def test_svg_url_matches_json_snapshot_and_conflicts_after_edit(tmp_path):
    diagram = make_bundle(tmp_path)
    baseline_svg = (diagram / "final.svg").read_bytes()
    with running_server(tmp_path) as (base, store):
        loaded, _ = read_json(f"{base}/api/diagrams/diagram-a")
        url = loaded["diagram"]["rendered_url"]
        assert url.endswith(f"?digest={hashlib.sha256(baseline_svg).hexdigest()}")
        with urllib.request.urlopen(f"{base}{url}", timeout=3) as response:
            assert response.read() == baseline_svg

        current = store.load_bundle("diagram-a")
        changed, _ = post_json(
            f"{base}/api/diagrams/diagram-a/edits",
            {**expected(current), "mermaid_code": "flowchart LR\n A --> B\n B --> A\n"},
        )
        with pytest.raises(urllib.error.HTTPError) as conflict:
            urllib.request.urlopen(f"{base}{url}", timeout=3)
        assert conflict.value.code == HTTPStatus.CONFLICT
        with urllib.request.urlopen(
            f"{base}{changed['diagram']['rendered_url']}", timeout=3
        ) as response:
            assert response.read() == (diagram / "final.svg").read_bytes()

        current = store.load_bundle("diagram-a")
        restored, _ = post_json(
            f"{base}/api/diagrams/diagram-a/history",
            {**expected(current), "action": "undo"},
        )
        assert restored["diagram"]["rendered_url"] == url
        with urllib.request.urlopen(f"{base}{url}", timeout=3) as response:
            assert response.read() == baseline_svg


@pytest.mark.parametrize("query", ["digest=bad", "digest=", "digest=" + "0" * 64 + "&digest=x"])
def test_svg_url_rejects_malformed_digest(tmp_path, query):
    make_bundle(tmp_path)
    with running_server(tmp_path) as (base, _):
        with pytest.raises(urllib.error.HTTPError) as invalid:
            urllib.request.urlopen(f"{base}/diagrams/diagram-a/final.svg?{query}", timeout=3)
        assert invalid.value.code == HTTPStatus.BAD_REQUEST


def test_digest_response_sends_the_bytes_that_were_hashed(monkeypatch, tmp_path):
    diagram = make_bundle(tmp_path)
    svg_path = diagram / "final.svg"
    original_svg = svg_path.read_bytes()
    send_bytes = ReviewHandler._send_bytes

    def change_before_send(self, payload, content_type, **kwargs):
        if content_type == "image/svg+xml":
            svg_path.write_bytes(b"replacement after digest check")
        return send_bytes(self, payload, content_type, **kwargs)

    monkeypatch.setattr(ReviewHandler, "_send_bytes", change_before_send)
    with running_server(tmp_path) as (base, _):
        loaded, _ = read_json(f"{base}/api/diagrams/diagram-a")
        with urllib.request.urlopen(
            f"{base}{loaded['diagram']['rendered_url']}", timeout=3
        ) as response:
            assert response.read() == original_svg
    assert svg_path.read_bytes() != original_svg


def test_digest_response_closes_descriptor_if_stream_wrapping_fails(monkeypatch, tmp_path):
    make_bundle(tmp_path)
    descriptors = []
    pending_descriptors = set()
    failed_descriptors = []
    closed_descriptors = []
    open_artifact = ReviewHandler._open_static_artifact
    fdopen = os.fdopen
    close = os.close

    def capture_artifact(self, path):
        artifact = open_artifact(self, path)
        if artifact is not None:
            descriptors.append(artifact[0])
            pending_descriptors.add(artifact[0])
        return artifact

    def fail_fdopen(descriptor, *args, **kwargs):
        if descriptor in pending_descriptors:
            failed_descriptors.append(descriptor)
            raise OSError("stream wrapping failed")
        return fdopen(descriptor, *args, **kwargs)

    def record_close(descriptor):
        close(descriptor)
        if descriptor in pending_descriptors:
            pending_descriptors.remove(descriptor)
            closed_descriptors.append(descriptor)

    with running_server(tmp_path) as (base, _):
        loaded, _ = read_json(f"{base}/api/diagrams/diagram-a")
        monkeypatch.setattr(ReviewHandler, "_open_static_artifact", capture_artifact)
        monkeypatch.setattr(os, "fdopen", fail_fdopen)
        monkeypatch.setattr(os, "close", record_close)
        with pytest.raises(urllib.error.HTTPError) as unavailable:
            urllib.request.urlopen(f"{base}{loaded['diagram']['rendered_url']}", timeout=3)
        assert unavailable.value.code == HTTPStatus.NOT_FOUND
        assert len(descriptors) == 1
        assert failed_descriptors == descriptors
        assert closed_descriptors == descriptors
        assert not pending_descriptors
