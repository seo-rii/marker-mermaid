import html
import json

import pytest

from marker_mermaid.review_ui import build_review_workspace_assets


def test_review_workspace_escapes_bootstrap_json_and_uses_external_assets():
    attack = '</script><img src=x onerror="alert(1)">&'
    assets = build_review_workspace_assets(
        {"diagrams": [{"id": "d1", "label": attack}], "csrf_token": attack},
        title=f"Review {attack}",
    )

    assert attack not in assets.html
    assert "&lt;/script&gt;&lt;img" in assets.html
    assert "<img src=x onerror=" not in assets.html
    assert '<script src="/assets/review.js" defer></script>' in assets.html
    assert '<link rel="stylesheet" href="/assets/review.css">' in assets.html
    assert "<script>" not in assets.html
    assert "onclick=" not in assets.html

    marker = 'data-bootstrap="'
    encoded = assets.html.split(marker, 1)[1].split('"', 1)[0]
    assert json.loads(html.unescape(encoded))["diagrams"][0]["label"] == attack


def test_review_workspace_contains_required_controls_and_same_origin_api_routes():
    assets = build_review_workspace_assets({"diagrams": []})

    for control_id in (
        "source-stage",
        "source-image",
        "render-image",
        "render-stage",
        "diff-layers",
        "diff-enabled",
        "diff-opacity",
        "diff-note",
        "layout-overlay",
        "provenance-overlay",
        "mermaid-editor",
        "ir-editor",
        "issue-list",
        "alternative-list",
        "approve",
        "reject",
        "undo",
        "redo",
        "command-form",
        "structure-operations",
        "reconnect-form",
        "edge-select",
        "edge-source",
        "edge-target",
        "delete-node-form",
        "node-select",
        "add-node-form",
        "add-node-id",
        "add-node-label",
        "bbox-x0",
        "bbox-y0",
        "bbox-x1",
        "bbox-y1",
        "add-node-reason",
    ):
        assert f'id="{control_id}"' in assets.html

    for route in (
        "/edits",
        "/history",
        "/candidate",
        "/commands",
        "/operations",
        "/decision",
    ):
        assert f'route("{route}")' in assets.javascript
    assert "/api/diagrams/" in assets.javascript
    assert "target.origin !== window.location.origin" in assets.javascript
    assert 'credentials: "same-origin"' in assets.javascript
    assert 'redirect: "error"' in assets.javascript
    assert "expected_version" in assets.javascript
    assert "expected_digest" in assets.javascript
    assert "eval(" not in assets.javascript
    assert "innerHTML" not in assets.javascript
    assert 'operation: "reconnect_edge"' in assets.javascript
    assert 'operation: "delete_node"' in assets.javascript
    assert 'operation: "add_node"' in assets.javascript
    assert 'operation: "move_node"' in assets.javascript
    assert "Source-anchored node added." in assets.javascript
    assert "data-node-id" in assets.javascript
    assert 'addEventListener("keydown"' in assets.javascript
    assert "Drag nodes on the advisory" in assets.html
    assert "Mermaid may choose a different layout" in assets.html
    assert 'addEventListener("pointerdown"' in assets.javascript
    assert 'addEventListener("pointermove"' in assets.javascript
    assert 'addEventListener("pointerup"' in assets.javascript
    assert "setPointerCapture" in assets.javascript
    assert "event.button !== 0" in assets.javascript
    assert "completed.moved" in assets.javascript
    assert "saveLayoutPosition" in assets.javascript
    assert "node.focus()" in assets.javascript
    assert "touch-action: none" in assets.css
    assert "ArrowLeft" in assets.javascript
    assert "renderDifference" in assets.javascript
    assert 'mix-blend-mode: difference' in assets.css
    assert 'object-fit: contain' in assets.css
    assert "controls.diffLayers.hidden = !active" in assets.javascript
    assert "controls.diffLayers.replaceChildren(renderLayer, sourceLayer)" in assets.javascript
    assert "diagram.diff_view" in assets.javascript
    assert 'descriptor.render_kind === "png"' in assets.javascript
    assert 'descriptor.alignment_profile === "bounds-contain-center-v1"' in assets.javascript
    assert "Bounds-normalized visual aid only" in assets.html
    assert "semantic, or pixel" in assets.html
    assert 'id="diff-layers" hidden aria-hidden="true"' in assets.html
    assert 'id="diff-note" class="muted" role="status" aria-live="polite"' in assets.html
    assert "failDifference" in assets.javascript
    assert "sourceLayer.onerror" in assets.javascript
    assert "renderLayer.onerror" in assets.javascript
    assert "image.currentSrc || image.src" in assets.javascript
    assert "state.diffLoad?.key !== key" in assets.javascript
    assert "state.diffFailure?.key === descriptorKey" in assets.javascript
    assert "controls.diffNote.textContent !== diffStatus" in assets.javascript
    assert "getImageData" not in assets.javascript
    assert "DOMParser" not in assets.javascript
    assert "createObjectURL" not in assets.javascript


def test_asset_base_must_be_same_origin_path():
    with pytest.raises(ValueError, match="same-origin"):
        build_review_workspace_assets({}, asset_base="https://cdn.example/assets")
    with pytest.raises(ValueError, match="same-origin"):
        build_review_workspace_assets({}, asset_base="//cdn.example/assets")
    with pytest.raises(ValueError, match="query"):
        build_review_workspace_assets({}, asset_base="/assets?v=1")


def test_bootstrap_rejects_non_standard_json_numbers():
    with pytest.raises(ValueError):
        build_review_workspace_assets({"score": float("nan")})
