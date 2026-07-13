import html
import json
import os
import subprocess
from pathlib import Path

import pytest

from marker_mermaid.review_ui import build_review_workspace_assets


def _run_review_browser(node_script: str, payload: dict) -> object:
    runtime_dir = Path(__file__).parents[1] / "src" / "marker_mermaid" / "runtime"
    playwright = runtime_dir / "node_modules" / "playwright"
    if not playwright.is_dir():
        pytest.skip("packaged Playwright runtime is unavailable")
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "MMX_PLAYWRIGHT": str(playwright)},
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


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
        "source-canvas",
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
        "revision-select",
        "checkout-revision",
        "command-form",
        "structure-operations",
        "reconnect-form",
        "edge-select",
        "edge-source",
        "edge-target",
        "delete-edge",
        "add-edge-form",
        "add-edge-source",
        "add-edge-target",
        "add-edge-reason",
        "add-edge",
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
        "group-nodes-form",
        "group-node-select",
        "group-node-help",
        "group-selection-status",
        "group-label",
        "group-nodes",
        "delete-group-form",
        "delete-group-select",
        "delete-group-help",
        "delete-group",
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
    assert 'operation: "add_edge"' in assets.javascript
    assert 'operation: "delete_edge"' in assets.javascript
    assert 'operation: "delete_node"' in assets.javascript
    assert 'operation: "add_node"' in assets.javascript
    assert 'operation: "move_node"' in assets.javascript
    assert 'operation: "group_nodes"' in assets.javascript
    assert 'operation: "delete_group"' in assets.javascript
    assert "controls.groupNodeSelect.selectedOptions" in assets.javascript
    assert "Select at least two node IDs to create a group." in assets.javascript
    assert "Enter a group label." in assets.javascript
    assert "groupedNodeIds.has(option.value)" in assets.javascript
    assert "already grouped in ${groupedNodeIds.get(option.value)}" in assets.javascript
    assert 'role="status" aria-live="polite"' in assets.html
    assert 'id="group-node-select" multiple size="6" required' in assets.html
    assert 'id="group-label" maxlength="200" placeholder="Services" required' in assets.html
    assert "updateGroupSelectionState" in assets.javascript
    assert "clearGroupSelection" in assets.javascript
    assert "Member nodes and edges will remain." in assets.javascript
    assert "group_id: groupId" in assets.javascript
    assert "Source-anchored node added." in assets.javascript
    assert "An evidence note is required to add an edge." in assets.javascript
    assert "Delete relation ${edgeId}?" in assets.javascript
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
    assert 'action: "checkout"' in assets.javascript
    assert "A later edit will branch the active timeline" in assets.javascript
    assert "diagram.revision_navigation" in assets.javascript
    assert "navigation.current_revision" in assets.javascript
    assert "renderDifference" in assets.javascript
    assert "mix-blend-mode: difference" in assets.css
    assert "object-fit: contain" in assets.css
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
    assert 'const coordinateSpace = text(scene.coordinate_space || "pixels")' in assets.javascript
    assert 'coordinateSpace === "normalized"' in assets.javascript
    assert "canvasValid ? canvasSize[0] : state.sourceLoad.width" in assets.javascript
    assert "canvasValid ? canvasSize[1] : state.sourceLoad.height" in assets.javascript
    assert 'typeof value === "number"' in assets.javascript
    assert "diagram.source_width" not in assets.javascript
    assert "state.sourceRequest !== requested" in assets.javascript
    assert "const sourceImage = controls.source.cloneNode(false)" in assets.javascript
    assert "sourceImage !== controls.source" in assets.javascript
    assert 'preserveAspectRatio="none"' in assets.html
    assert 'id="source-canvas" class="source-canvas" hidden' in assets.html
    assert ".source-canvas" in assets.css


def test_source_overlay_matches_centered_image_and_scene_coordinate_spaces():
    node_script = r"""
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.MMX_PLAYWRIGHT);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const browser = await chromium.launch({ headless: true });
try {
  const results = [];
  for (const testCase of payload.cases) {
    const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
    await page.route("http://review.test/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/") return route.fulfill({ contentType: "text/html", body: testCase.html });
      if (path === "/assets/review.css") {
        return route.fulfill({ contentType: "text/css", body: testCase.css });
      }
      if (path === "/assets/review.js") {
        return route.fulfill({ contentType: "text/javascript", body: testCase.javascript });
      }
      if (path === "/images/source.svg") {
        return route.fulfill({
          contentType: "image/svg+xml",
          body: "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='100'>"
            + "<rect width='200' height='100' fill='white'/></svg>",
        });
      }
      if (path === `/api/diagrams/${testCase.diagram.id}`) {
        return route.fulfill({
          contentType: "application/json", body: JSON.stringify({ diagram: testCase.diagram }),
        });
      }
      return route.abort("blockedbyclient");
    });
    await page.goto("http://review.test/");
    await page.addStyleTag({ content: "#source-stage{width:600px;height:300px}" });
    await page.waitForFunction(() => {
      const image = document.getElementById("source-image");
      return image.complete && image.naturalWidth === 200
        && document.querySelectorAll("#provenance-overlay .node-box").length === 1
        && document.querySelectorAll("#provenance-overlay .evidence-box").length === 1;
    });
    results.push(await page.evaluate(() => {
      const stage = document.getElementById("source-stage").getBoundingClientRect();
      const image = document.getElementById("source-image").getBoundingClientRect();
      const overlay = document.getElementById("provenance-overlay").getBoundingClientRect();
      const node = document.querySelector("#provenance-overlay .node-box")
        .getBoundingClientRect();
      const box = (value) => ({
        left: value.left, top: value.top, width: value.width, height: value.height,
      });
      return { stage: box(stage), image: box(image), overlay: box(overlay), node: box(node) };
    }));
    await page.close();
  }
  process.stdout.write(JSON.stringify(results));
} finally {
  await browser.close();
}
"""
    cases = []
    for coordinate_space, canvas_size, bbox in (
        ("normalized", None, [0.25, 0.2, 0.75, 0.8]),
        ("pixels", [400, 200], [100, 40, 300, 160]),
    ):
        scene_ir = {
            "coordinate_space": coordinate_space,
            "elements": [{"id": "A", "text": "A", "bbox": bbox}],
            "relations": [],
            "groups": [],
        }
        if canvas_size is not None:
            scene_ir["canvas_size"] = canvas_size
        diagram = {
            "id": f"diagram-{coordinate_space}",
            "version": 0,
            "digest": "digest",
            "source_url": "/images/source.svg",
            "scene_ir": scene_ir,
            "provenance": [
                {"id": "inside", "kind": "ocr_token", "bbox": bbox},
                {
                    "id": "outside",
                    "kind": "ocr_token",
                    "bbox": [0, 0, 2, 2] if coordinate_space == "normalized" else [0, 0, 800, 400],
                },
            ],
        }
        assets = build_review_workspace_assets({"diagrams": [diagram]})
        cases.append(
            {
                "html": assets.html,
                "css": assets.css,
                "javascript": assets.javascript,
                "diagram": diagram,
            }
        )

    for measurement in _run_review_browser(node_script, {"cases": cases}):
        assert measurement["image"] == pytest.approx(measurement["overlay"], abs=0.5)
        assert measurement["image"]["width"] < measurement["stage"]["width"]
        assert measurement["image"]["height"] < measurement["stage"]["height"]
        assert measurement["node"]["left"] == pytest.approx(
            measurement["image"]["left"] + 50, abs=0.5
        )
        assert measurement["node"]["top"] == pytest.approx(
            measurement["image"]["top"] + 20, abs=0.5
        )
        assert measurement["node"]["width"] == pytest.approx(100, abs=0.5)
        assert measurement["node"]["height"] == pytest.approx(60, abs=0.5)


def test_source_overlay_rejects_stale_failed_and_empty_images_and_keeps_selection():
    diagrams = [
        {
            "id": "old",
            "version": 0,
            "digest": "old-digest",
            "source_url": "/images/old.svg",
            "scene_ir": {
                "coordinate_space": "normalized",
                "elements": [{"id": "OLD", "text": "Old", "bbox": [0.1, 0.1, 0.4, 0.4]}],
                "relations": [],
                "groups": [],
            },
        },
        {
            "id": "new",
            "version": 0,
            "digest": "new-digest",
            "source_url": "/images/new.svg",
            "scene_ir": {
                "coordinate_space": "normalized",
                "elements": [
                    {"id": "N1", "text": "One", "bbox": [0.1, 0.1, 0.3, 0.3]},
                    {"id": "N2", "text": "Two", "bbox": [0.6, 0.5, 0.9, 0.8]},
                ],
                "relations": [],
                "groups": [],
            },
        },
        {
            "id": "missing",
            "version": 0,
            "digest": "missing-digest",
            "source_url": "/images/missing.svg",
            "scene_ir": {
                "coordinate_space": "normalized",
                "elements": [{"id": "MISSING", "bbox": [0.1, 0.1, 0.2, 0.2]}],
                "relations": [],
                "groups": [],
            },
        },
        {
            "id": "empty",
            "version": 0,
            "digest": "empty-digest",
            "source_url": None,
            "scene_ir": {
                "coordinate_space": "normalized",
                "elements": [{"id": "EMPTY", "bbox": [0.1, 0.1, 0.2, 0.2]}],
                "relations": [],
                "groups": [],
            },
        },
    ]
    assets = build_review_workspace_assets({"diagrams": diagrams})
    node_script = r"""
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.MMX_PLAYWRIGHT);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  await page.route("http://review.test/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/") return route.fulfill({ contentType: "text/html", body: payload.html });
    if (path === "/assets/review.css") {
      return route.fulfill({ contentType: "text/css", body: payload.css });
    }
    if (path === "/assets/review.js") {
      return route.fulfill({ contentType: "text/javascript", body: payload.javascript });
    }
    if (path.startsWith("/api/diagrams/")) {
      const id = decodeURIComponent(path.split("/").at(-1));
      const diagram = payload.diagrams.find((item) => item.id === id);
      return route.fulfill({
        contentType: "application/json", body: JSON.stringify({ diagram }),
      });
    }
    if (path === "/images/old.svg") {
      await new Promise((resolve) => setTimeout(resolve, 500));
      return route.fulfill({
        contentType: "image/svg+xml",
        body: "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='50'/>",
      });
    }
    if (path === "/images/new.svg") {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: "<svg xmlns='http://www.w3.org/2000/svg' width='240' height='120'/>",
      });
    }
    if (path === "/images/missing.svg") return route.abort("failed");
    return route.abort("blockedbyclient");
  });
  await page.goto("http://review.test/", { waitUntil: "domcontentloaded" });
  await page.addStyleTag({ content: "#source-stage{width:600px;height:300px}" });
  await page.selectOption("#diagram-select", "new");
  await page.waitForFunction(() => {
    const image = document.getElementById("source-image");
    return image.naturalWidth === 240
      && document.querySelectorAll("#provenance-overlay .node-box").length === 2
      && !document.getElementById("source-canvas").hidden;
  });
  await page.waitForTimeout(650);
  const staleSafe = await page.evaluate(() => {
    const image = document.getElementById("source-image");
    return image.naturalWidth === 240
      && image.getAttribute("src") === "/images/new.svg"
      && !document.querySelector("[data-node-id='OLD']");
  });
  await page.locator("#provenance-overlay [data-node-id='N2']").click();
  const clickSelection = await page.locator("#node-select").inputValue();
  await page.selectOption("#node-select", "N1");
  await page.locator("#provenance-overlay [data-node-id='N2']").focus();
  await page.keyboard.press("Enter");
  const keyboardSelection = await page.locator("#node-select").inputValue();
  await page.setViewportSize({ width: 520, height: 500 });
  const resized = await page.evaluate(() => {
    const image = document.getElementById("source-image").getBoundingClientRect();
    const overlay = document.getElementById("provenance-overlay").getBoundingClientRect();
    return Math.abs(image.left - overlay.left) <= .5
      && Math.abs(image.top - overlay.top) <= .5
      && Math.abs(image.width - overlay.width) <= .5
      && Math.abs(image.height - overlay.height) <= .5;
  });
  await page.selectOption("#diagram-select", "missing");
  await page.waitForFunction(() => document.getElementById("source-canvas").hidden);
  const failedSafe = await page.locator("#provenance-overlay > *").count() === 0;
  await page.selectOption("#diagram-select", "empty");
  await page.waitForFunction(() => document.getElementById("source-canvas").hidden
    && !document.getElementById("source-image").hasAttribute("src"));
  const emptySafe = await page.locator("#provenance-overlay > *").count() === 0;
  process.stdout.write(JSON.stringify({
    staleSafe, clickSelection, keyboardSelection, resized, failedSafe, emptySafe,
  }));
} finally {
  await browser.close();
}
"""

    result = _run_review_browser(
        node_script,
        {
            "html": assets.html,
            "css": assets.css,
            "javascript": assets.javascript,
            "diagrams": diagrams,
        },
    )

    assert result == {
        "staleSafe": True,
        "clickSelection": "N2",
        "keyboardSelection": "N2",
        "resized": True,
        "failedSafe": True,
        "emptySafe": True,
    }


def test_review_workspace_edge_drag_reuses_validated_reconnect_operation():
    assets = build_review_workspace_assets({"diagrams": []})

    assert 'class", "layout-edge"' in assets.javascript
    assert "`edge-handle ${endpoint}`" in assets.javascript
    assert "function nearestLayoutNode(clientX, clientY)" in assets.javascript
    assert "bounds.left + position[0] * bounds.width" in assets.javascript
    assert "bounds.top + position[1] * bounds.height" in assets.javascript
    assert "candidates[1].distance - candidates[0].distance <= .5" in assets.javascript
    assert "completed.diagramId === diagramId()" in assets.javascript
    assert "completed.version === Number(state.current?.version)" in assets.javascript
    assert "completed.digest === text(state.current?.digest)" in assets.javascript
    assert 'completed.endpoint === "source" ? nodeId' in assets.javascript
    assert 'completed.endpoint === "target" ? nodeId' in assets.javascript
    assert "sourceId === targetId" in assets.javascript
    assert 'addEventListener("lostpointercapture"' in assets.javascript
    assert "event.pointerId === edgeDrag.pointerId" in assets.javascript
    assert 'event.key === "Escape" && edgeDrag' in assets.javascript
    assert "handle.focus()" in assets.javascript
    assert (
        'handle.setAttribute("r", ".018"); handle.setAttribute("tabindex", "-1")'
        in assets.javascript
    )
    assert "requestAnimationFrame" in assets.javascript
    assert "cancelAnimationFrame" in assets.javascript
    reconnect = assets.javascript.split("async function saveEdgeReconnect", 1)[1].split(
        "controls.layout.addEventListener", 1
    )[0]
    assert 'operation: "reconnect_edge"' in reconnect
    assert "position" not in reconnect
    assert "bbox" not in reconnect
    assert "provenance" not in reconnect
    assert ".layout-edge" in assets.css
    assert ".edge-handle" in assets.css
    assert "drag a selected relation endpoint onto a node" in assets.html


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
