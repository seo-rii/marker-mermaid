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
        "reload-latest",
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
        "evidence-label-form",
        "evidence-label-node",
        "evidence-label-select",
        "evidence-label-help",
        "evidence-label-status",
        "apply-evidence-label",
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
    assert "editorBaseline" in assets.javascript
    assert "editorDraftDirty" in assets.javascript
    assert "detailReady" in assets.javascript
    assert "mutationLocked" in assets.javascript
    assert "preserveDraft" in assets.javascript
    assert "failure.status = response.status" in assets.javascript
    assert "Discard unsaved editor draft and continue?" in assets.javascript
    assert "Discard unsaved editor draft and switch diagrams?" in assets.javascript
    assert "Reload latest before saving." in assets.javascript
    assert 'state.editorConflict ? "Reload latest" : "Retry load"' in assets.javascript
    assert 'dirty ? "Discard draft"' in assets.javascript
    assert 'addEventListener("beforeunload"' in assets.javascript
    assert "eval(" not in assets.javascript
    assert "innerHTML" not in assets.javascript
    assert 'operation: "reconnect_edge"' in assets.javascript
    assert 'operation: "add_edge"' in assets.javascript
    assert 'operation: "relabel_node_from_evidence"' in assets.javascript
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
    assert "function evidenceLabelChoices(diagram)" in assets.javascript
    assert "node.evidence_ids" in assets.javascript
    assert "evidenceIdCounts.get(evidenceId) !== 1" in assets.javascript
    assert "links.length !== 1 || links[0] !== node" in assets.javascript
    assert "node[labelKey] === label" in assets.javascript
    assert '/^[A-Za-z][A-Za-z0-9_-]{0,63}$/' in assets.javascript
    assert "declarationCounts.get(node.id) !== 1" in assets.javascript
    assert "[\\p{Cc}\\p{Cf}\\p{Cs}\\p{Zl}\\p{Zp}]" in assets.javascript
    assert 'rect.setAttribute("aria-pressed", selected ? "true" : "false")' in assets.javascript
    assert 'rect.setAttribute("role", "button")' in assets.javascript
    assert "Use source-backed label" in assets.html
    assert (
        'id="evidence-label-status" class="muted" role="status" aria-live="polite"'
        in assets.html
    )
    assert ".evidence-box.selected" in assets.css
    relabel_submit = assets.javascript.split(
        'byId("evidence-label-form").addEventListener("submit"', 1
    )[1].split('byId("group-nodes-form").addEventListener("submit"', 1)[0]
    assert "node_id: nodeId, evidence_id: evidenceId" in relabel_submit
    for forbidden in ("label:", "kind:", "score:", "bbox:", "provenance:"):
        assert forbidden not in relabel_submit
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


def test_review_workspace_preserves_drafts_and_recovers_revision_conflicts():
    base_scene = {
        "coordinate_space": "normalized",
        "elements": [{"id": "A", "text": "Server zero", "bbox": [0.1, 0.1, 0.4, 0.3]}],
        "relations": [],
        "groups": [],
    }
    latest_scene = {
        "coordinate_space": "normalized",
        "elements": [{"id": "A", "text": "Server one", "bbox": [0.1, 0.1, 0.4, 0.3]}],
        "relations": [],
        "groups": [],
    }
    draft_scene = {
        "coordinate_space": "normalized",
        "elements": [{"id": "A", "text": "Local draft", "bbox": [0.1, 0.1, 0.4, 0.3]}],
        "relations": [],
        "groups": [],
    }
    retry_scene = {
        "coordinate_space": "normalized",
        "elements": [{"id": "A", "text": "Resolved edit", "bbox": [0.1, 0.1, 0.4, 0.3]}],
        "relations": [],
        "groups": [],
    }
    common = {
        "id": "diagram-a",
        "label": "Diagram A",
        "status": "review",
        "grade": "U",
        "issues": [],
        "alternatives": [],
        "provenance": [],
        "can_undo": False,
        "can_redo": False,
    }
    bootstrap_diagram = {
        **common,
        "version": 0,
        "digest": "bootstrap-digest",
        "mermaid_code": "bootstrap-code",
        "scene_ir": {},
        "revision_navigation": {"current_revision": "r0", "timeline": ["r0"]},
    }
    version_zero = {
        **common,
        "version": 0,
        "digest": "digest-0",
        "mermaid_code": "flowchart LR\n  A[Server zero]",
        "scene_ir": base_scene,
        "revision_navigation": {"current_revision": "r0", "timeline": ["r0"]},
    }
    version_one = {
        **common,
        "version": 1,
        "digest": "digest-1",
        "mermaid_code": "flowchart LR\n  A[Server one]",
        "scene_ir": latest_scene,
        "revision_navigation": {"current_revision": "r1", "timeline": ["r0", "r1"]},
    }
    version_two = {
        **common,
        "version": 2,
        "digest": "digest-2",
        "mermaid_code": "flowchart LR\n  A[Resolved edit]",
        "scene_ir": retry_scene,
        "revision_navigation": {
            "current_revision": "r2",
            "timeline": ["r0", "r1", "r2"],
        },
    }
    assets = build_review_workspace_assets(
        {"diagrams": [bootstrap_diagram], "csrf_token": "review-token"}
    )
    node_script = r"""
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.MMX_PLAYWRIGHT);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  let getCount = 0;
  let postCount = 0;
  let discardedActionPosts = 0;
  const postBodies = [];
  await page.route("http://review.test/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/") {
      return route.fulfill({ contentType: "text/html", body: payload.html });
    }
    if (path === "/assets/review.css") {
      return route.fulfill({ contentType: "text/css", body: payload.css });
    }
    if (path === "/assets/review.js") {
      return route.fulfill({ contentType: "text/javascript", body: payload.javascript });
    }
    if (path === "/api/diagrams/diagram-a" && request.method() === "GET") {
      getCount += 1;
      if (getCount === 3) {
        return route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({
            diagram: { ...payload.versionOne, id: "wrong-diagram" },
          }),
        });
      }
      if (getCount === 5) {
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "latest revision unavailable" }),
        });
      }
      const diagram = getCount === 1 ? payload.versionZero : payload.versionOne;
      return route.fulfill({
        contentType: "application/json", body: JSON.stringify({ diagram }),
      });
    }
    if (path === "/api/diagrams/diagram-a/edits" && request.method() === "POST") {
      postCount += 1;
      postBodies.push(JSON.parse(request.postData() || "{}"));
      if (postCount === 1) {
        return route.fulfill({
          status: 422,
          contentType: "application/json",
          body: JSON.stringify({ error: "invalid edit" }),
        });
      }
      if (postCount === 2) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "stale revision" }),
        });
      }
      if (postCount === 4) {
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ error: "new stale revision" }),
        });
      }
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ diagram: payload.versionTwo }),
      });
    }
    if (path === "/api/diagrams/diagram-a/decision" && request.method() === "POST") {
      discardedActionPosts += 1;
      return route.fulfill({ status: 500, body: "unexpected decision request" });
    }
    return route.abort("blockedbyclient");
  });
  await page.goto("http://review.test/", { waitUntil: "domcontentloaded" });
  await page.waitForFunction((expected) => {
    return document.getElementById("mermaid-editor").value === expected;
  }, payload.versionZero.mermaid_code);

  const draftIr = JSON.stringify(payload.draftScene, null, 2);
  await page.fill("#mermaid-editor", payload.draftCode);
  await page.fill("#ir-editor", draftIr);
  let discardPrompt = "";
  page.once("dialog", async (dialog) => {
    discardPrompt = dialog.message();
    await dialog.dismiss();
  });
  await page.click("#approve");
  const afterCancelledAction = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
  }));
  const invalidResponse = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a/edits"
      && response.status() === 422;
  });
  await page.click("#save-editors");
  await invalidResponse;
  await page.waitForFunction(() => {
    return document.getElementById("message").textContent.includes("invalid edit")
      && !document.getElementById("mermaid-editor").readOnly;
  });
  const afterValidation = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
  }));

  const conflictResponse = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a/edits"
      && response.status() === 409;
  });
  const latestResponse = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a"
      && response.request().method() === "GET";
  });
  await page.click("#save-editors");
  await Promise.all([conflictResponse, latestResponse]);
  await page.waitForFunction(() => {
    const reload = document.getElementById("reload-latest");
    return document.getElementById("message").textContent.includes("editor draft preserved")
      && !reload.hidden && !reload.disabled;
  });
  const afterConflict = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
    saveDisabled: document.getElementById("save-editors").disabled,
    status: document.getElementById("save-state").textContent,
  }));

  let reloadPrompt = "";
  page.once("dialog", async (dialog) => {
    reloadPrompt = dialog.message();
    await dialog.accept();
  });
  const mismatchedReload = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a"
      && response.status() === 200;
  });
  await page.click("#reload-latest");
  await mismatchedReload;
  await page.waitForFunction(() => {
    return document.getElementById("message").textContent.includes(
      "Latest revision response did not match the active diagram"
    ) && !document.getElementById("reload-latest").disabled;
  });
  const afterMismatchedReload = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
    saveDisabled: document.getElementById("save-editors").disabled,
    status: document.getElementById("save-state").textContent,
  }));
  page.once("dialog", (dialog) => dialog.accept());
  await page.click("#reload-latest");
  await page.waitForFunction((expected) => {
    return document.getElementById("mermaid-editor").value === expected
      && document.getElementById("reload-latest").hidden;
  }, payload.versionOne.mermaid_code);
  const afterReload = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
  }));

  const retryIr = JSON.stringify(payload.retryScene, null, 2);
  await page.fill("#mermaid-editor", payload.retryCode);
  await page.fill("#ir-editor", retryIr);
  const savedResponse = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a/edits"
      && response.status() === 200;
  });
  await page.click("#save-editors");
  await savedResponse;
  await page.waitForFunction(() => {
    return document.getElementById("message").textContent === "Edits saved."
      && !document.getElementById("mermaid-editor").readOnly;
  });
  const finalEditors = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
  }));

  const failedRefreshIr = JSON.stringify(payload.failedRefreshScene, null, 2);
  await page.fill("#mermaid-editor", payload.failedRefreshCode);
  await page.fill("#ir-editor", failedRefreshIr);
  const secondConflict = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a/edits"
      && response.status() === 409;
  });
  const failedRefresh = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a"
      && response.status() === 503;
  });
  await page.click("#save-editors");
  await Promise.all([secondConflict, failedRefresh]);
  await page.waitForFunction(() => {
    const reload = document.getElementById("reload-latest");
    return document.getElementById("message").textContent.includes("refresh failed")
      && reload.textContent === "Reload latest" && !reload.disabled;
  });
  const afterRefreshFailure = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
    saveDisabled: document.getElementById("save-editors").disabled,
    status: document.getElementById("save-state").textContent,
  }));
  process.stdout.write(JSON.stringify({
    afterValidation,
    afterCancelledAction,
    afterConflict,
    afterMismatchedReload,
    afterReload,
    finalEditors,
    afterRefreshFailure,
    postBodies,
    getCount,
    postCount,
    discardedActionPosts,
    discardPrompt,
    reloadPrompt,
  }));
} finally {
  await browser.close();
}
"""

    draft_code = "flowchart LR\n  A[Local draft]"
    retry_code = "flowchart LR\n  A[Resolved edit]"
    failed_refresh_code = "flowchart LR\n  A[Second local draft]"
    failed_refresh_scene = {
        "coordinate_space": "normalized",
        "elements": [{"id": "A", "text": "Second local draft", "bbox": [0.1, 0.1, 0.4, 0.3]}],
        "relations": [],
        "groups": [],
    }
    result = _run_review_browser(
        node_script,
        {
            "html": assets.html,
            "css": assets.css,
            "javascript": assets.javascript,
            "versionZero": version_zero,
            "versionOne": version_one,
            "versionTwo": version_two,
            "draftScene": draft_scene,
            "retryScene": retry_scene,
            "draftCode": draft_code,
            "retryCode": retry_code,
            "failedRefreshCode": failed_refresh_code,
            "failedRefreshScene": failed_refresh_scene,
        },
    )

    draft_ir = json.dumps(draft_scene, indent=2)
    latest_ir = json.dumps(latest_scene, indent=2)
    retry_ir = json.dumps(retry_scene, indent=2)
    assert result["afterValidation"] == {"mermaid": draft_code, "ir": draft_ir}
    assert result["afterCancelledAction"] == {"mermaid": draft_code, "ir": draft_ir}
    assert result["discardedActionPosts"] == 0
    assert result["discardPrompt"] == "Discard unsaved editor draft and continue?"
    assert result["reloadPrompt"] == "Discard editor draft and reload the latest revision?"
    assert result["afterConflict"] == {
        "mermaid": draft_code,
        "ir": draft_ir,
        "saveDisabled": True,
        "status": "review · grade U · conflicting editor draft preserved",
    }
    assert result["afterMismatchedReload"] == {
        "mermaid": draft_code,
        "ir": draft_ir,
        "saveDisabled": True,
        "status": "review · grade U · conflicting editor draft preserved",
    }
    assert result["afterReload"] == {
        "mermaid": version_one["mermaid_code"],
        "ir": latest_ir,
    }
    assert result["finalEditors"] == {"mermaid": retry_code, "ir": retry_ir}
    assert result["afterRefreshFailure"] == {
        "mermaid": failed_refresh_code,
        "ir": json.dumps(failed_refresh_scene, indent=2),
        "saveDisabled": True,
        "status": "review · grade U · conflict refresh required",
    }
    assert result["getCount"] == 5
    assert result["postCount"] == 4
    assert [body["expected_version"] for body in result["postBodies"]] == [0, 0, 1, 2]
    assert [body["expected_digest"] for body in result["postBodies"]] == [
        "digest-0",
        "digest-0",
        "digest-1",
        "digest-2",
    ]
    assert result["postBodies"][2]["mermaid_code"] == retry_code
    assert result["postBodies"][2]["scene_ir"] == retry_scene
    assert result["postBodies"][3]["mermaid_code"] == failed_refresh_code
    assert result["postBodies"][3]["scene_ir"] == failed_refresh_scene
    for body in result["postBodies"][:2]:
        assert body["mermaid_code"] == draft_code
        assert body["scene_ir"] == draft_scene


def test_review_workspace_locks_failed_summary_load_until_explicit_retry():
    summary = {
        "id": "diagram-a",
        "source_id": "page-1-figure-1",
        "label": "page-1-figure-1",
        "status": "review",
        "grade": "U",
        "decision": None,
        "version": 4,
        "digest": "digest-4",
    }
    detail = {
        **summary,
        "mermaid_code": "flowchart LR\n  A[Loaded]",
        "scene_ir": {
            "coordinate_space": "normalized",
            "elements": [{"id": "A", "text": "Loaded", "bbox": [0.1, 0.1, 0.4, 0.3]}],
            "relations": [],
            "groups": [],
        },
        "issues": [],
        "alternatives": [],
        "provenance": [],
        "can_undo": False,
        "can_redo": False,
        "revision_navigation": {"current_revision": "r4", "timeline": ["r0", "r4"]},
    }
    assets = build_review_workspace_assets({"diagrams": [summary]})
    node_script = r"""
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.MMX_PLAYWRIGHT);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
  let getCount = 0;
  await page.route("http://review.test/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/") return route.fulfill({ contentType: "text/html", body: payload.html });
    if (path === "/assets/review.css") {
      return route.fulfill({ contentType: "text/css", body: payload.css });
    }
    if (path === "/assets/review.js") {
      return route.fulfill({ contentType: "text/javascript", body: payload.javascript });
    }
    if (path === "/api/diagrams/diagram-a") {
      getCount += 1;
      if (getCount === 1) {
        return route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "detail unavailable" }),
        });
      }
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ diagram: payload.detail }),
      });
    }
    return route.abort("blockedbyclient");
  });
  await page.goto("http://review.test/", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => {
    return document.getElementById("message").textContent.includes("detail unavailable")
      && document.getElementById("reload-latest").textContent === "Retry load";
  });
  const locked = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
    mermaidReadOnly: document.getElementById("mermaid-editor").readOnly,
    irReadOnly: document.getElementById("ir-editor").readOnly,
    saveDisabled: document.getElementById("save-editors").disabled,
    approveDisabled: document.getElementById("approve").disabled,
    rejectDisabled: document.getElementById("reject").disabled,
    commandDisabled: document.getElementById("command-input").disabled,
    retryHidden: document.getElementById("reload-latest").hidden,
  }));
  const retryResponse = page.waitForResponse((response) => {
    return new URL(response.url()).pathname === "/api/diagrams/diagram-a"
      && response.status() === 200;
  });
  await page.click("#reload-latest");
  await retryResponse;
  await page.waitForFunction((expected) => {
    return document.getElementById("mermaid-editor").value === expected
      && !document.getElementById("mermaid-editor").readOnly;
  }, payload.detail.mermaid_code);
  const ready = await page.evaluate(() => ({
    mermaid: document.getElementById("mermaid-editor").value,
    ir: document.getElementById("ir-editor").value,
    saveDisabled: document.getElementById("save-editors").disabled,
    approveDisabled: document.getElementById("approve").disabled,
    commandDisabled: document.getElementById("command-input").disabled,
    recoveryHidden: document.getElementById("reload-latest").hidden,
    message: document.getElementById("message").textContent,
  }));
  process.stdout.write(JSON.stringify({ locked, ready, getCount }));
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
            "detail": detail,
        },
    )

    assert result["locked"] == {
        "mermaid": "",
        "ir": "",
        "mermaidReadOnly": True,
        "irReadOnly": True,
        "saveDisabled": True,
        "approveDisabled": True,
        "rejectDisabled": True,
        "commandDisabled": True,
        "retryHidden": False,
    }
    assert result["ready"] == {
        "mermaid": detail["mermaid_code"],
        "ir": json.dumps(detail["scene_ir"], indent=2),
        "saveDisabled": False,
        "approveDisabled": False,
        "commandDisabled": False,
        "recoveryHidden": True,
        "message": "Diagram detail loaded.",
    }
    assert result["getCount"] == 2


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
            "mermaid_code": "flowchart LR\n  A[A]",
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
            "mermaid_code": "flowchart LR\n  OLD[Old]",
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
            "mermaid_code": "flowchart LR\n  N1[One] --> N2[Two]",
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
            "mermaid_code": "flowchart LR\n  MISSING[Missing]",
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
            "mermaid_code": "flowchart LR\n  EMPTY[Empty]",
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
  let resolveOldDetailStarted;
  let resolveOldImageStarted;
  let releaseOldDetail;
  let releaseOldImage;
  let resolveOldDetail;
  let resolveOldImage;
  const oldDetailStarted = new Promise((resolve) => { resolveOldDetailStarted = resolve; });
  const oldImageStarted = new Promise((resolve) => { resolveOldImageStarted = resolve; });
  const oldDetailGate = new Promise((resolve) => { releaseOldDetail = resolve; });
  const oldImageGate = new Promise((resolve) => { releaseOldImage = resolve; });
  const oldDetailCompleted = new Promise((resolve) => { resolveOldDetail = resolve; });
  const oldImageCompleted = new Promise((resolve) => { resolveOldImage = resolve; });
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
      if (id === "old") {
        resolveOldDetailStarted();
        await oldDetailGate;
        await route.fulfill({
          contentType: "application/json", body: JSON.stringify({ diagram }),
        });
        resolveOldDetail();
        return;
      }
      return route.fulfill({
        contentType: "application/json", body: JSON.stringify({ diagram }),
      });
    }
    if (path === "/images/old.svg") {
      resolveOldImageStarted();
      await oldImageGate;
      await route.fulfill({
        contentType: "image/svg+xml",
        body: "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='50'/>",
      });
      resolveOldImage();
      return;
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
  await Promise.all([oldDetailStarted, oldImageStarted]);
  await page.selectOption("#diagram-select", "new");
  await page.waitForFunction(() => {
    const image = document.getElementById("source-image");
    return image.naturalWidth === 240
      && document.querySelectorAll("#provenance-overlay .node-box").length === 2
      && !document.getElementById("source-canvas").hidden;
  });
  releaseOldDetail();
  releaseOldImage();
  await Promise.all([oldDetailCompleted, oldImageCompleted]);
  await page.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  const staleSafe = await page.evaluate((expected) => {
    const image = document.getElementById("source-image");
    return image.naturalWidth === 240
      && image.getAttribute("src") === "/images/new.svg"
      && document.getElementById("diagram-select").value === "new"
      && document.getElementById("mermaid-editor").value === expected.mermaid
      && JSON.stringify(JSON.parse(document.getElementById("ir-editor").value))
        === JSON.stringify(expected.scene)
      && !document.querySelector("[data-node-id='OLD']");
  }, { mermaid: payload.diagrams[1].mermaid_code, scene: payload.diagrams[1].scene_ir });
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


def test_review_workspace_selects_and_submits_only_linked_source_label_ids():
    summary = {
        "id": "diagram-a",
        "source_id": "page-1-figure-1",
        "label": "page-1-figure-1",
        "status": "review",
        "grade": "C",
        "decision": None,
        "version": 0,
        "digest": "digest-0",
    }
    scene = {
        "coordinate_space": "normalized",
        "elements": [
            {
                "id": "A",
                "role": "node",
                "text": "Old label",
                "bbox": [0.05, 0.1, 0.45, 0.4],
                "evidence_ids": ["ocr-a", "vector-a", "vector-no-box", "same", "shared", "bad"],
            },
            {
                "id": "B",
                "role": "node",
                "text": "B",
                "bbox": [0.55, 0.1, 0.95, 0.4],
                "evidence_ids": ["shared", "b-label"],
            },
        ],
        "relations": [],
        "groups": [],
    }
    provenance = [
        {
            "id": "vector-a",
            "kind": "vector_text",
            "bbox": [0.1, 0.22, 0.4, 0.3],
            "text": "Observed label",
            "score": 0.99,
        },
        {"id": "bad", "kind": "vlm_observation", "text": "Generated label"},
        {
            "id": "shared",
            "kind": "ocr_token",
            "bbox": [0.4, 0.45, 0.6, 0.55],
            "text": "Shared label",
        },
        {"id": "same", "kind": "ocr_token", "text": "Old label"},
        {
            "id": "ocr-a",
            "kind": "ocr_token",
            "bbox": [0.1, 0.12, 0.4, 0.2],
            "text": "  Observed label  ",
            "score": 0.91,
        },
        {"id": "vector-no-box", "kind": "vector_text", "text": "Observed label"},
        {"id": "b-label", "kind": "ocr_token", "text": "Observed B"},
    ]
    detail = {
        **summary,
        "source_url": "/images/source.svg",
        "mermaid_code": 'flowchart LR\n  A["Old label"]\n  B\n',
        "scene_ir": scene,
        "provenance": provenance,
        "issues": [],
        "alternatives": [],
        "can_undo": False,
        "can_redo": False,
        "revision_navigation": {"current_revision": "r000000", "timeline": ["r000000"]},
    }
    updated_scene = json.loads(json.dumps(scene))
    updated_scene["elements"][0]["text"] = "Observed label"
    updated = {
        **detail,
        "version": 1,
        "digest": "digest-1",
        "mermaid_code": 'flowchart LR\n  A["Observed label"]\n  B\n',
        "scene_ir": updated_scene,
        "can_undo": True,
        "revision_navigation": {
            "current_revision": "r000001",
            "timeline": ["r000000", "r000001"],
        },
    }
    assets = build_review_workspace_assets({"diagrams": [summary]})
    node_script = r"""
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.MMX_PLAYWRIGHT);
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1000, height: 900 } });
  const postBodies = [];
  let releaseOperation;
  let operationStarted;
  const operationGate = new Promise((resolve) => { releaseOperation = resolve; });
  const operationStart = new Promise((resolve) => { operationStarted = resolve; });
  await page.route("http://review.test/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/") return route.fulfill({ contentType: "text/html", body: payload.html });
    if (path === "/assets/review.css") {
      return route.fulfill({ contentType: "text/css", body: payload.css });
    }
    if (path === "/assets/review.js") {
      return route.fulfill({ contentType: "text/javascript", body: payload.javascript });
    }
    if (path === "/api/diagrams/diagram-a" && route.request().method() === "GET") {
      return route.fulfill({
        contentType: "application/json", body: JSON.stringify({ diagram: payload.detail }),
      });
    }
    if (path === "/api/diagrams/diagram-a/operations") {
      postBodies.push(route.request().postDataJSON());
      operationStarted();
      await operationGate;
      return route.fulfill({
        contentType: "application/json", body: JSON.stringify({ diagram: payload.updated }),
      });
    }
    if (path === "/images/source.svg") {
      return route.fulfill({
        contentType: "image/svg+xml",
        body: "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='200'/>",
      });
    }
    return route.abort("blockedbyclient");
  });
  await page.goto("http://review.test/", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => {
    return !document.getElementById("evidence-label-node").disabled
      && document.querySelectorAll("#provenance-overlay .evidence-box.eligible").length === 2;
  });
  const choices = await page.locator("#evidence-label-select option").evaluateAll(
    (options) => options.map((option) => option.value),
  );
  await page.selectOption("#evidence-label-node", "B");
  const emptyForB = await page.evaluate(() => ({
    evidenceDisabled: document.getElementById("evidence-label-select").disabled,
    applyDisabled: document.getElementById("apply-evidence-label").disabled,
  }));
  await page.locator("#provenance-overlay [data-evidence-id='ocr-a']").click();
  const clickSelection = await page.evaluate(() => ({
    node: document.getElementById("evidence-label-node").value,
    evidence: document.getElementById("evidence-label-select").value,
    pressed: document.querySelector("[data-evidence-id='ocr-a']").getAttribute("aria-pressed"),
    selected: document.querySelector("[data-evidence-id='ocr-a']").classList.contains("selected"),
    applyDisabled: document.getElementById("apply-evidence-label").disabled,
  }));
  await page.selectOption("#evidence-label-node", "B");
  await page.locator("#provenance-overlay [data-evidence-id='vector-a']").focus();
  await page.keyboard.press("Space");
  const keyboardSelection = await page.evaluate(() => ({
    node: document.getElementById("evidence-label-node").value,
    evidence: document.getElementById("evidence-label-select").value,
    pressed: document.querySelector("[data-evidence-id='vector-a']").getAttribute("aria-pressed"),
  }));
  await page.locator("#provenance-overlay [data-evidence-id='ocr-a']").click();
  await page.click("#apply-evidence-label");
  await operationStart;
  const pending = await page.evaluate(() => ({
    nodeDisabled: document.getElementById("evidence-label-node").disabled,
    evidenceDisabled: document.getElementById("evidence-label-select").disabled,
    applyDisabled: document.getElementById("apply-evidence-label").disabled,
    overlayDisabled: document.querySelector("[data-evidence-id='ocr-a']")
      .getAttribute("aria-disabled"),
  }));
  releaseOperation();
  await page.waitForFunction(() => document.getElementById("mermaid-editor").value
    .includes("Observed label"));
  const after = await page.evaluate(() => ({
    irLabel: JSON.parse(document.getElementById("ir-editor").value).elements[0].text,
    options: [...document.querySelectorAll("#evidence-label-select option")]
      .map((option) => option.value),
    evidenceDisabled: document.getElementById("evidence-label-select").disabled,
    applyDisabled: document.getElementById("apply-evidence-label").disabled,
    status: document.getElementById("evidence-label-status").textContent,
  }));
  process.stdout.write(JSON.stringify({
    choices, emptyForB, clickSelection, keyboardSelection, pending, after, postBodies,
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
            "detail": detail,
            "updated": updated,
        },
    )

    assert result["choices"] == ["ocr-a", "vector-a", "vector-no-box"]
    assert result["emptyForB"] == {"evidenceDisabled": True, "applyDisabled": True}
    assert result["clickSelection"] == {
        "node": "A",
        "evidence": "ocr-a",
        "pressed": "true",
        "selected": True,
        "applyDisabled": False,
    }
    assert result["keyboardSelection"] == {
        "node": "A",
        "evidence": "vector-a",
        "pressed": "true",
    }
    assert result["pending"] == {
        "nodeDisabled": True,
        "evidenceDisabled": True,
        "applyDisabled": True,
        "overlayDisabled": "true",
    }
    assert result["after"] == {
        "irLabel": "Observed label",
        "options": ["same"],
        "evidenceDisabled": False,
        "applyDisabled": False,
        "status": "Ready to use ocr_token evidence same: Old label",
    }
    assert result["postBodies"] == [
        {
            "operation": {
                "operation": "relabel_node_from_evidence",
                "node_id": "A",
                "evidence_id": "ocr-a",
            },
            "expected_version": 0,
            "expected_digest": "digest-0",
        }
    ]


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
