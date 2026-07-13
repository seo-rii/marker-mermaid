"""Dependency-free assets for the interactive local review workspace.

The HTTP layer intentionally lives elsewhere.  This module only produces a small
HTML shell and static CSS/JavaScript assets, which lets a server apply a strict
``script-src 'self'`` policy without enabling inline scripts.  Bootstrap data is
stored in an escaped ``data-*`` attribute rather than an executable script block.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReviewWorkspaceAssets:
    """A complete, self-contained review workspace asset bundle."""

    html: str
    css: str
    javascript: str


def build_review_workspace_assets(
    bootstrap: Mapping[str, Any],
    *,
    title: str = "Marker Mermaid Review",
    asset_base: str = "/assets",
) -> ReviewWorkspaceAssets:
    """Return an HTML shell and its external CSS and JavaScript assets.

    ``asset_base`` must be a same-origin absolute path.  JSON serialization is
    strict (NaN and Infinity are rejected), and the serialized value is escaped
    as an HTML attribute before insertion.
    """

    if not asset_base.startswith("/") or asset_base.startswith("//"):
        raise ValueError("asset_base must be a same-origin absolute path")
    if any(character in asset_base for character in "?#"):
        raise ValueError("asset_base must not contain a query or fragment")

    normalized_base = asset_base.rstrip("/")
    bootstrap_json = json.dumps(
        bootstrap,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    escaped_bootstrap = html.escape(bootstrap_json, quote=True)
    escaped_title = html.escape(title, quote=False)
    escaped_css = html.escape(f"{normalized_base}/review.css", quote=True)
    escaped_js = html.escape(f"{normalized_base}/review.js", quote=True)

    shell = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>{escaped_title}</title>
  <link rel="stylesheet" href="{escaped_css}">
  <script src="{escaped_js}" defer></script>
</head>
<body>
  <div id="review-app" data-bootstrap="{escaped_bootstrap}">
    <header class="topbar">
      <div>
        <h1>{escaped_title}</h1>
        <p id="workspace-summary" class="muted">Loading review data…</p>
      </div>
      <nav class="history-actions" aria-label="Edit history">
        <label for="revision-select">Revision</label>
        <select id="revision-select" disabled></select>
        <button id="checkout-revision" type="button" disabled>Restore revision</button>
        <button id="undo" type="button" disabled>Undo</button>
        <button id="redo" type="button" disabled>Redo</button>
      </nav>
    </header>

    <main>
      <section class="selection-bar" aria-label="Diagram selection">
        <label for="diagram-select">Diagram</label>
        <select id="diagram-select"></select>
        <button id="reload-latest" type="button" hidden>Reload latest</button>
        <span id="save-state" class="muted" role="status" aria-live="polite"></span>
      </section>

      <section class="visual-grid" aria-label="Visual comparison">
        <figure>
          <figcaption>Source image and provenance</figcaption>
          <div id="source-stage" class="image-stage">
            <div id="source-canvas" class="source-canvas" hidden>
              <img id="source-image" alt="Source diagram">
              <svg id="provenance-overlay" preserveAspectRatio="none"
                aria-label="Source evidence overlay"></svg>
            </div>
          </div>
        </figure>
        <figure>
          <figcaption>Mermaid render, advisory layout, and visual difference</figcaption>
          <p class="muted">Drag hints to record intent; Mermaid may choose a different layout.</p>
          <div class="diff-controls" aria-label="Visual difference controls">
            <label for="diff-enabled">
              <input id="diff-enabled" type="checkbox"> Difference blend
            </label>
            <label for="diff-opacity">Source strength</label>
            <input id="diff-opacity" type="range" min="0" max="10" step="1" value="5"
              disabled aria-describedby="diff-note">
          </div>
          <p id="diff-note" class="muted" role="status" aria-live="polite">
            Bounds-normalized visual aid only; no crop, rotation, feature, semantic, or pixel
            registration is claimed.
          </p>
          <div id="render-stage" class="image-stage">
            <img id="render-image" alt="Rendered Mermaid reconstruction">
            <div id="diff-layers" hidden aria-hidden="true"></div>
            <svg id="layout-overlay" viewBox="0 0 1 1"
              preserveAspectRatio="none" aria-label="Advisory node layout canvas"></svg>
          </div>
        </figure>
      </section>

      <section class="editor-grid" aria-label="Reconstruction editors">
        <label>Mermaid code
          <textarea id="mermaid-editor" spellcheck="false"></textarea>
        </label>
        <label>Scene IR (JSON)
          <textarea id="ir-editor" spellcheck="false"></textarea>
        </label>
      </section>
      <div class="editor-actions">
        <button id="save-editors" type="button">Save editors</button>
      </div>

      <section id="structure-operations" aria-labelledby="structure-heading">
        <h2 id="structure-heading">Validated structure operations</h2>
        <p class="muted">
          Select source-backed nodes and relations by stable ID. Drag nodes on the advisory layout
          canvas to record intent, or drag a selected relation endpoint onto a node to reconnect it.
          Neither gesture changes source bounding boxes or claims exact Mermaid placement.
        </p>
        <div class="structure-grid">
          <form id="add-node-form">
            <h3>Add source-anchored node</h3>
            <label for="add-node-id">Node ID</label>
            <input id="add-node-id" pattern="[A-Za-z][A-Za-z0-9_-]{{0,63}}" required>
            <label for="add-node-label">Label</label>
            <input id="add-node-label" maxlength="200" required>
            <fieldset class="bbox-fields">
              <legend>Source bbox (x0, y0, x1, y1)</legend>
              <input id="bbox-x0" type="number" min="0" step="any" aria-label="bbox x0" required>
              <input id="bbox-y0" type="number" min="0" step="any" aria-label="bbox y0" required>
              <input id="bbox-x1" type="number" min="0" step="any" aria-label="bbox x1" required>
              <input id="bbox-y1" type="number" min="0" step="any" aria-label="bbox y1" required>
            </fieldset>
            <p id="canvas-size" class="muted"></p>
            <label for="add-node-reason">Evidence note</label>
            <input id="add-node-reason" maxlength="4096" required>
            <button id="add-node" type="submit">Add node</button>
          </form>
          <form id="reconnect-form">
            <h3>Reconnect edge</h3>
            <label for="edge-select">Relation</label>
            <select id="edge-select" required></select>
            <label for="edge-source">New source</label>
            <select id="edge-source" required></select>
            <label for="edge-target">New target</label>
            <select id="edge-target" required></select>
            <button id="reconnect-edge" type="submit">Reconnect</button>
            <button id="delete-edge" class="reject" type="button">Delete relation</button>
          </form>
          <form id="add-edge-form">
            <h3>Add directed edge</h3>
            <label for="add-edge-source">Source</label>
            <select id="add-edge-source" required></select>
            <label for="add-edge-target">Target</label>
            <select id="add-edge-target" required></select>
            <label for="add-edge-reason">Evidence note</label>
            <input id="add-edge-reason" maxlength="4096" required>
            <button id="add-edge" type="submit">Add edge</button>
          </form>
          <form id="evidence-label-form">
            <h3>Use source-backed label</h3>
            <label for="evidence-label-node">Explicit node</label>
            <select id="evidence-label-node" required
              aria-describedby="evidence-label-help evidence-label-status"></select>
            <label for="evidence-label-select">Linked evidence</label>
            <select id="evidence-label-select" required
              aria-describedby="evidence-label-help evidence-label-status"></select>
            <p id="evidence-label-help" class="muted">
              Only a uniquely linked, single-line OCR or PDF vector-text observation can replace
              the node label. The server derives the label from provenance; browser text is never
              submitted as authority.
            </p>
            <p id="evidence-label-status" class="muted" role="status" aria-live="polite"
              tabindex="-1">No eligible source label selected.</p>
            <button id="apply-evidence-label" type="submit" disabled>Use source label</button>
          </form>
          <form id="delete-node-form">
            <h3>Delete node</h3>
            <label for="node-select">Explicit node</label>
            <select id="node-select" required></select>
            <p id="node-edge-count" class="muted"></p>
            <button id="delete-node" class="reject" type="submit">Delete node and edges</button>
          </form>
          <form id="group-nodes-form">
            <h3>Group nodes</h3>
            <label for="group-node-select">Explicit nodes</label>
            <select id="group-node-select" multiple size="6" required
              aria-describedby="group-node-help"></select>
            <p id="group-node-help" class="muted">Select at least two ungrouped node IDs.</p>
            <p id="group-selection-status" class="muted" role="status" aria-live="polite"
              tabindex="-1">0 nodes selected.</p>
            <label for="group-label">Group label</label>
            <input id="group-label" maxlength="200" placeholder="Services" required>
            <button id="group-nodes" type="submit" disabled>Create group</button>
          </form>
          <form id="delete-group-form">
            <h3>Delete group</h3>
            <label for="delete-group-select">Explicit group</label>
            <select id="delete-group-select" required
              aria-describedby="delete-group-help"></select>
            <p id="delete-group-help" class="muted">
              Removes only the group; member nodes and edges remain.
            </p>
            <button id="delete-group" class="reject" type="submit">Delete group</button>
          </form>
        </div>
      </section>

      <section class="review-grid">
        <div>
          <h2>Issues</h2>
          <ul id="issue-list" class="item-list"></ul>
        </div>
        <div>
          <h2>Alternative candidates</h2>
          <div id="alternative-list" class="item-list"></div>
        </div>
      </section>

      <form id="command-form">
        <label for="command-input">Natural-language correction</label>
        <div class="command-row">
          <input id="command-input" name="command" autocomplete="off"
            placeholder="Reverse the edge from DB to API" required>
          <button type="submit">Apply command</button>
        </div>
      </form>

      <section class="decision-actions" aria-label="Review decision">
        <label for="decision-reason">Decision note</label>
        <input id="decision-reason" autocomplete="off">
        <button id="approve" class="approve" type="button">Approve</button>
        <button id="reject" class="reject" type="button">Reject</button>
      </section>
      <p id="message" role="alert" aria-live="assertive"></p>
    </main>
  </div>
</body>
</html>
"""
    return ReviewWorkspaceAssets(html=shell, css=REVIEW_CSS, javascript=REVIEW_JAVASCRIPT)


REVIEW_CSS = r"""
:root { color-scheme: light dark; font-family: system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; background: Canvas; color: CanvasText; }
button, input, select, textarea { font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .5; }
.topbar, main { width: min(1500px, calc(100% - 2rem)); margin-inline: auto; }
.topbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.topbar h1 { margin-bottom: 0; }
.muted { color: GrayText; }
.history-actions, .selection-bar, .editor-actions, .command-row, .decision-actions {
  display: flex; align-items: center; gap: .6rem;
}
.diff-controls { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.diff-controls label { font-weight: 500; }
.diff-controls input[type="range"] { width: min(16rem, 45%); }
.selection-bar { border-block: 1px solid GrayText; padding-block: .8rem; }
.selection-bar select { min-width: 18rem; }
.visual-grid, .editor-grid, .review-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;
}
figure { margin: 1rem 0 0; min-width: 0; }
figcaption, label { font-weight: 650; }
.image-stage { position: relative; display: grid; place-items: center; min-height: 18rem;
  border: 1px solid GrayText; overflow: auto; background: #fff; }
.image-stage img { display: block; max-width: 100%; max-height: 65vh; }
.source-canvas {
  position: relative; display: inline-block; width: max-content; max-width: 100%;
  max-height: 65vh; line-height: 0;
}
.source-canvas[hidden] { display: none; }
.source-canvas #source-image { width: auto; height: auto; }
#diff-layers {
  position: absolute; inset: 0; display: grid; background: #fff; pointer-events: none;
}
#diff-layers[hidden] { display: none; }
#diff-layers .diff-layer-image {
  grid-area: 1 / 1; display: block;
  width: 100%; height: 100%; max-width: none; max-height: none;
  object-fit: contain; object-position: center;
}
.diff-source-image { mix-blend-mode: difference; }
.diff-source-image.diff-opacity-0 { opacity: 0; }
.diff-source-image.diff-opacity-1 { opacity: .1; }
.diff-source-image.diff-opacity-2 { opacity: .2; }
.diff-source-image.diff-opacity-3 { opacity: .3; }
.diff-source-image.diff-opacity-4 { opacity: .4; }
.diff-source-image.diff-opacity-5 { opacity: .5; }
.diff-source-image.diff-opacity-6 { opacity: .6; }
.diff-source-image.diff-opacity-7 { opacity: .7; }
.diff-source-image.diff-opacity-8 { opacity: .8; }
.diff-source-image.diff-opacity-9 { opacity: .9; }
.diff-source-image.diff-opacity-10 { opacity: 1; }
#provenance-overlay, #layout-overlay {
  position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: auto;
}
#layout-overlay { touch-action: none; }
.evidence-box {
  fill: rgb(54 162 235 / .12); stroke: #087dbd; stroke-width: 2;
  vector-effect: non-scaling-stroke;
}
.evidence-box.eligible { cursor: pointer; }
.evidence-box:not(.eligible) { pointer-events: none; }
.evidence-box:hover, .evidence-box:focus, .evidence-box.selected {
  fill: rgb(255 159 64 / .25); stroke: #e26f00;
}
.evidence-box.selected { stroke-width: 3; }
.node-box {
  fill: transparent; stroke: #7453c6; stroke-width: 2; stroke-dasharray: 6 3;
  vector-effect: non-scaling-stroke; cursor: pointer;
}
.node-box:hover, .node-box:focus, .node-box.selected {
  fill: rgb(116 83 198 / .18); stroke: #4f2d9e;
}
.layout-node { cursor: grab; outline: none; }
.layout-node circle {
  fill: rgb(255 255 255 / .88); stroke: #8a3ffc; stroke-width: 2;
  vector-effect: non-scaling-stroke;
}
.layout-node.selected circle, .layout-node:hover circle, .layout-node:focus circle {
  fill: rgb(138 63 252 / .24); stroke: #5b21b6;
}
.layout-node text {
  fill: #24113d; font-size: .035px; font-weight: 700; pointer-events: none;
  text-anchor: middle; dominant-baseline: central;
}
.layout-edge {
  fill: none; stroke: #68707c; stroke-width: 2; vector-effect: non-scaling-stroke;
  cursor: pointer; outline: none; pointer-events: stroke;
}
.layout-edge.selected, .layout-edge:hover, .layout-edge:focus {
  stroke: #b1440e; stroke-width: 3;
}
.edge-handle {
  fill: #fff; stroke: #b1440e; stroke-width: 2; vector-effect: non-scaling-stroke;
  cursor: crosshair; outline: none;
}
.edge-handle:hover, .edge-handle:focus { fill: #ffd8c2; stroke: #7c2d0c; }
.edge-handle.source { stroke-dasharray: 3 2; }
.editor-grid { margin-top: 1rem; }
.editor-grid label { display: grid; gap: .4rem; }
textarea {
  width: 100%; min-height: 18rem; resize: vertical;
  font: .9rem ui-monospace, monospace; tab-size: 2;
}
.editor-actions { justify-content: end; margin-block: .6rem 1rem; }
#structure-operations { border: 1px solid GrayText; padding: 0 1rem 1rem; margin-block: 1rem; }
.structure-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; }
.structure-grid form {
  display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .6rem;
  align-items: center; min-width: 0;
}
.structure-grid input, .structure-grid select { min-width: 0; width: 100%; }
.structure-grid h3, .structure-grid p { grid-column: 1 / -1; }
.structure-grid button { justify-self: end; grid-column: 2; }
.bbox-fields {
  grid-column: 1 / -1; display: grid; grid-template-columns: repeat(4, 1fr); gap: .4rem;
}
.bbox-fields input { min-width: 0; width: 100%; }
.review-grid > div { border: 1px solid GrayText; padding: 0 1rem 1rem; }
.item-list { margin: 0; padding: 0; list-style: none; }
.item-list li, .candidate { padding: .55rem; border-top: 1px solid GrayText; }
.candidate { display: flex; justify-content: space-between; align-items: center; gap: .8rem; }
.candidate.selected { border-inline-start: .35rem solid #1b7f3b; }
#command-form { margin-block: 1rem; }
.command-row input { flex: 1; }
.decision-actions { padding-block: 1rem; border-top: 1px solid GrayText; }
.decision-actions input { flex: 1; }
.approve { background: #1b7f3b; color: #fff; border: 1px solid #0f5828; }
.reject { background: #a32d2d; color: #fff; border: 1px solid #711c1c; }
#message.error { color: #b42318; }
@media (max-width: 800px) {
  .visual-grid, .editor-grid, .review-grid, .structure-grid { grid-template-columns: 1fr; }
  .topbar, .decision-actions { align-items: stretch; flex-direction: column; }
}
""".strip()


REVIEW_JAVASCRIPT = r"""
"use strict";

(() => {
  const root = document.getElementById("review-app");
  if (!root) return;

  let bootstrap;
  try {
    bootstrap = JSON.parse(root.dataset.bootstrap || "{}");
  } catch (error) {
    root.textContent = "Invalid review workspace bootstrap data.";
    return;
  }

  const state = {
    diagrams: Array.isArray(bootstrap.diagrams) ? bootstrap.diagrams : [],
    current: null,
    csrfToken: typeof bootstrap.csrf_token === "string" ? bootstrap.csrf_token : "",
    busy: false,
    diffFailure: null,
    diffLoad: null,
    sourceLoad: null,
    sourceRequest: null,
    diagramRequest: 0,
    diagramLoading: false,
    detailReady: false,
    editorBaseline: null,
    editorConflict: false,
  };
  const byId = (id) => document.getElementById(id);
  const controls = {
    diagram: byId("diagram-select"), source: byId("source-image"), render: byId("render-image"),
    overlay: byId("provenance-overlay"), sourceCanvas: byId("source-canvas"),
    mermaid: byId("mermaid-editor"), ir: byId("ir-editor"),
    layout: byId("layout-overlay"), renderStage: byId("render-stage"),
    diffLayers: byId("diff-layers"), diffEnabled: byId("diff-enabled"),
    diffOpacity: byId("diff-opacity"), diffNote: byId("diff-note"),
    issues: byId("issue-list"), alternatives: byId("alternative-list"), message: byId("message"),
    saveState: byId("save-state"), reloadLatest: byId("reload-latest"),
    saveEditors: byId("save-editors"), undo: byId("undo"), redo: byId("redo"),
    revision: byId("revision-select"), checkoutRevision: byId("checkout-revision"),
    node: byId("node-select"), edge: byId("edge-select"),
    edgeSource: byId("edge-source"), edgeTarget: byId("edge-target"),
    edgeCount: byId("node-edge-count"), reconnect: byId("reconnect-edge"),
    deleteEdge: byId("delete-edge"), deleteNode: byId("delete-node"),
    addEdge: byId("add-edge"), addEdgeSource: byId("add-edge-source"),
    addEdgeTarget: byId("add-edge-target"), addEdgeReason: byId("add-edge-reason"),
    evidenceLabelNode: byId("evidence-label-node"),
    evidenceLabel: byId("evidence-label-select"),
    evidenceLabelStatus: byId("evidence-label-status"),
    applyEvidenceLabel: byId("apply-evidence-label"),
    groupNodes: byId("group-nodes"), groupNodeSelect: byId("group-node-select"),
    groupLabel: byId("group-label"), groupStatus: byId("group-selection-status"),
    deleteGroup: byId("delete-group"), deleteGroupSelect: byId("delete-group-select"),
    addNode: byId("add-node"), addNodeId: byId("add-node-id"),
    addNodeLabel: byId("add-node-label"), addNodeReason: byId("add-node-reason"),
    canvasSize: byId("canvas-size"),
    commandInput: byId("command-input"),
    commandSubmit: byId("command-form").querySelector("button"),
    decisionReason: byId("decision-reason"), approve: byId("approve"), reject: byId("reject"),
    bbox: [byId("bbox-x0"), byId("bbox-y0"), byId("bbox-x1"), byId("bbox-y1")],
  };

  const diagramId = () => encodeURIComponent(
    String(state.current?.id || state.current?.source_id || ""),
  );
  const route = (suffix = "") => `/api/diagrams/${diagramId()}${suffix}`;

  async function sameOriginFetch(path, options = {}) {
    const target = new URL(path, window.location.origin);
    if (target.origin !== window.location.origin) {
      throw new Error("Cross-origin API request blocked");
    }
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body !== undefined) headers.set("Content-Type", "application/json");
    if (state.csrfToken) headers.set("X-CSRF-Token", state.csrfToken);
    const response = await fetch(target.pathname + target.search, {
      ...options, headers, credentials: "same-origin", redirect: "error",
    });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { detail = (await response.json()).error || detail; } catch (_) { /* non-JSON error */ }
      const failure = new Error(detail);
      failure.status = response.status;
      throw failure;
    }
    return response.status === 204 ? {} : response.json();
  }

  function showMessage(text, error = false) {
    controls.message.textContent = text || "";
    controls.message.classList.toggle("error", error);
  }

  function normalizeDiagram(payload) {
    return payload.diagram && typeof payload.diagram === "object" ? payload.diagram : payload;
  }

  function replaceCurrent(payload, { preserveDraft = false } = {}) {
    const next = normalizeDiagram(payload);
    if (!next || typeof next !== "object"
      || typeof next.mermaid_code !== "string"
      || !next.scene_ir || typeof next.scene_ir !== "object" || Array.isArray(next.scene_ir)
      || !Number.isInteger(Number(next.version)) || Number(next.version) < 0
      || !text(next.digest)) {
      throw new Error("Diagram response did not contain editable detail");
    }
    const keepDraft = preserveDraft && editorDraftDirty()
      && text(state.editorBaseline?.diagramId) === text(next.id);
    const changedRevision = String(next.id) !== String(state.current?.id)
      || Number(next.version) !== Number(state.current?.version)
      || text(next.digest) !== text(state.current?.digest);
    if (changedRevision) clearGroupSelection();
    const index = state.diagrams.findIndex((item) => String(item.id) === String(next.id));
    if (index >= 0) state.diagrams[index] = next;
    state.current = next;
    state.detailReady = true;
    syncEditors(next, { preserveDraft: keepDraft });
    renderCurrent();
  }

  function text(value) { return value === null || value === undefined ? "" : String(value); }

  function editorSnapshot(diagram) {
    return {
      diagramId: text(diagram?.id),
      version: Number(diagram?.version),
      digest: text(diagram?.digest),
      mermaid: text(diagram?.mermaid_code),
      ir: JSON.stringify(diagram?.scene_ir || {}, null, 2),
    };
  }

  function editorDraftDirty() {
    const baseline = state.editorBaseline;
    if (!baseline) return false;
    return controls.mermaid.value !== baseline.mermaid || controls.ir.value !== baseline.ir;
  }

  function mutationLocked() {
    return state.busy || state.diagramLoading || !state.detailReady;
  }

  function resetEditors(diagram) {
    state.editorBaseline = {
      diagramId: text(diagram?.id),
      version: Number(diagram?.version),
      digest: text(diagram?.digest),
      mermaid: "",
      ir: "",
    };
    controls.mermaid.value = "";
    controls.ir.value = "";
    state.editorConflict = false;
  }

  function syncEditors(diagram, { preserveDraft = false } = {}) {
    const snapshot = editorSnapshot(diagram);
    const keepDraft = preserveDraft && editorDraftDirty()
      && text(state.editorBaseline?.diagramId) === snapshot.diagramId;
    state.editorBaseline = snapshot;
    if (keepDraft) {
      state.editorConflict = true;
      return;
    }
    controls.mermaid.value = snapshot.mermaid;
    controls.ir.value = snapshot.ir;
    state.editorConflict = false;
  }

  function renderEditorState(diagram) {
    const dirty = editorDraftDirty();
    if (!dirty) state.editorConflict = false;
    const editorStatus = state.diagramLoading
      ? "loading diagram detail"
      : (!state.detailReady
        ? (state.editorConflict ? "conflict refresh required" : "diagram detail unavailable")
        : (state.editorConflict && dirty
          ? "conflicting editor draft preserved" : (dirty ? "unsaved editor draft" : "")));
    controls.saveState.textContent = [
      text(diagram.status || "review"),
      `grade ${text(diagram.grade || "U")}`,
      editorStatus,
    ].filter(Boolean).join(" · ");
    const recoveryLabel = !state.detailReady
      ? (state.editorConflict ? "Reload latest" : "Retry load")
      : (state.editorConflict && dirty ? "Reload latest" : (dirty ? "Discard draft" : ""));
    controls.reloadLatest.textContent = recoveryLabel;
    controls.reloadLatest.hidden = !recoveryLabel;
    const editorLocked = mutationLocked();
    controls.reloadLatest.disabled = state.busy || state.diagramLoading;
    controls.saveEditors.disabled = editorLocked || (state.editorConflict && dirty);
    controls.mermaid.readOnly = editorLocked;
    controls.ir.readOnly = editorLocked;
    controls.commandInput.disabled = editorLocked;
    controls.commandSubmit.disabled = editorLocked;
    controls.decisionReason.disabled = editorLocked;
    controls.approve.disabled = editorLocked;
    controls.reject.disabled = editorLocked;
  }

  function imageUrl(value) {
    if (!value) return "";
    const parsed = new URL(String(value), window.location.origin);
    return parsed.origin === window.location.origin ? parsed.pathname + parsed.search : "";
  }

  function sourceUrl(diagram) {
    return imageUrl(diagram?.source_url || diagram?.source_image);
  }

  function resetSourceCanvas() {
    state.sourceLoad = null;
    state.sourceRequest = null;
    controls.sourceCanvas.hidden = true;
    controls.overlay.replaceChildren();
  }

  function updateSourceImage(diagram) {
    const expected = sourceUrl(diagram);
    if (!expected) {
      resetSourceCanvas(); controls.source.removeAttribute("src"); return;
    }
    if (state.sourceLoad?.url === expected) {
      controls.sourceCanvas.hidden = false; return;
    }
    if (state.sourceRequest === expected) return;
    resetSourceCanvas(); state.sourceRequest = expected;
    const requested = expected;
    const sourceImage = controls.source.cloneNode(false);
    controls.source.replaceWith(sourceImage); controls.source = sourceImage;
    sourceImage.addEventListener("load", () => {
      const loaded = imageUrl(sourceImage.currentSrc || sourceImage.src);
      const width = sourceImage.naturalWidth; const height = sourceImage.naturalHeight;
      if (sourceImage !== controls.source || state.sourceRequest !== requested
        || sourceUrl(state.current || {}) !== requested
        || loaded !== requested || !Number.isFinite(width) || !Number.isFinite(height)
        || width <= 0 || height <= 0) return;
      state.sourceLoad = { url: requested, width, height };
      controls.sourceCanvas.hidden = false; renderOverlay(state.current || {});
    }, { once: true });
    sourceImage.addEventListener("error", () => {
      if (sourceImage === controls.source && state.sourceRequest === requested
        && sourceUrl(state.current || {}) === requested) {
        resetSourceCanvas();
      }
    }, { once: true });
    sourceImage.src = requested;
  }

  function renderDifference(diagram) {
    const descriptor = diagram.diff_view && typeof diagram.diff_view === "object"
      ? diagram.diff_view : {};
    const sourceUrl = imageUrl(descriptor.source_url);
    const diffRenderUrl = imageUrl(descriptor.render_url);
    const available = descriptor.available === true
      && descriptor.alignment_profile === "bounds-contain-center-v1"
      && descriptor.render_kind === "png" && Boolean(sourceUrl && diffRenderUrl);
    const descriptorKey = `${sourceUrl}|${diffRenderUrl}`;
    const failed = state.diffFailure?.key === descriptorKey;
    if ((!available || failed) && controls.diffEnabled.checked) {
      controls.diffEnabled.checked = false;
    }
    const requested = available && !failed && controls.diffEnabled.checked;
    if (requested && state.diffLoad?.key !== descriptorKey) {
      const sourceLayer = new Image(); const renderLayer = new Image();
      sourceLayer.alt = ""; sourceLayer.setAttribute("aria-hidden", "true");
      renderLayer.alt = ""; renderLayer.setAttribute("aria-hidden", "true");
      sourceLayer.className = "diff-layer-image diff-source-image";
      renderLayer.className = "diff-layer-image diff-render-image";
      state.diffLoad = {
        key: descriptorKey, sourceUrl, renderUrl: diffRenderUrl,
        source: false, render: false, sourceLayer, renderLayer,
      };
      sourceLayer.onload = () => markDifferenceLoaded(
        descriptorKey, "source", sourceLayer, sourceUrl,
      );
      renderLayer.onload = () => markDifferenceLoaded(
        descriptorKey, "render", renderLayer, diffRenderUrl,
      );
      sourceLayer.onerror = () => failDifference(
        descriptorKey,
        "Difference blend unavailable: the source comparison image failed to load.",
      );
      renderLayer.onerror = () => failDifference(
        descriptorKey,
        "Difference blend unavailable: the current PNG render failed to load.",
      );
      controls.diffLayers.replaceChildren(renderLayer, sourceLayer);
      controls.diffLayers.hidden = true;
      sourceLayer.src = sourceUrl; renderLayer.src = diffRenderUrl;
    } else if (!requested) {
      state.diffLoad = null;
      controls.diffLayers.replaceChildren();
    }
    const active = requested && state.diffLoad?.source === true
      && state.diffLoad?.render === true;
    const opacity = Math.min(10, Math.max(0, Math.round(Number(controls.diffOpacity.value))));
    const sourceLayer = state.diffLoad?.sourceLayer;
    for (const className of [...(sourceLayer?.classList || [])]) {
      if (className.startsWith("diff-opacity-")) sourceLayer.classList.remove(className);
    }
    if (sourceLayer) sourceLayer.classList.add(`diff-opacity-${opacity}`);
    controls.diffLayers.hidden = !active;
    controls.renderStage.classList.toggle("diff-active", active);
    controls.diffEnabled.disabled = state.busy || !available || failed;
    controls.diffOpacity.disabled = state.busy || !active;
    const diffStatus = failed
      ? state.diffFailure.message
      : (requested && !active
        ? "Loading source and PNG layers for the bounds-normalized preview…"
        : (available
        ? "Bounds-normalized preview; no node, feature, rotation, or pixel registration is applied."
        : "Difference blend unavailable: this revision has no safe bounded PNG comparison."));
    if (controls.diffNote.textContent !== diffStatus) {
      controls.diffNote.textContent = diffStatus;
    }
  }

  function failDifference(key, message) {
    if (!controls.diffEnabled.checked || state.diffLoad?.key !== key) return;
    state.diffFailure = { key, message };
    controls.diffEnabled.checked = false;
    renderDifference(state.current || {});
  }

  function markDifferenceLoaded(key, layer, image, expectedUrl) {
    if (!controls.diffEnabled.checked || state.diffLoad?.key !== key) return;
    if (imageUrl(image.currentSrc || image.src) !== expectedUrl) return;
    const width = Number(image.naturalWidth); const height = Number(image.naturalHeight);
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0
      || width > 8192 || height > 8192 || width * height > 50000000) {
      failDifference(
        key, `Difference blend unavailable: the ${layer} image exceeds decode bounds.`,
      );
      return;
    }
    state.diffLoad[layer] = true;
    renderDifference(state.current || {});
  }

  function evidenceLabelChoices(diagram) {
    const scene = diagram.scene_ir && typeof diagram.scene_ir === "object"
      ? diagram.scene_ir : {};
    const nodes = Array.isArray(scene.elements)
      ? scene.elements.filter((item) => item && typeof item === "object") : [];
    const evidence = Array.isArray(diagram.provenance)
      ? diagram.provenance : Object.values(diagram.provenance?.evidence || {});
    const code = typeof diagram.mermaid_code === "string" ? diagram.mermaid_code : "";
    const declarationCounts = new Map();
    if (code.length <= 1000000
      && /^\s*(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b/.test(code)) {
      for (const line of code.split(/\r?\n/)) {
        const match = line.match(
          /^\s*([A-Za-z][A-Za-z0-9_-]{0,63})\s*\[\s*"(?:[^"\\]|\\.)*"\s*\]\s*$/,
        );
        if (match) declarationCounts.set(match[1], (declarationCounts.get(match[1]) || 0) + 1);
      }
    }
    const evidenceIdCounts = new Map(); const evidenceById = new Map();
    const nodeIdCounts = new Map();
    const linkedNodes = new Map();
    for (const item of evidence) {
      if (typeof item?.id !== "string" || !item.id) continue;
      evidenceIdCounts.set(item.id, (evidenceIdCounts.get(item.id) || 0) + 1);
      if (!evidenceById.has(item.id)) evidenceById.set(item.id, item);
    }
    for (const node of nodes) {
      if (typeof node.id !== "string" || !node.id) continue;
      nodeIdCounts.set(node.id, (nodeIdCounts.get(node.id) || 0) + 1);
      for (const evidenceId of Array.isArray(node.evidence_ids) ? node.evidence_ids : []) {
        if (typeof evidenceId !== "string" || !evidenceId) continue;
        const links = linkedNodes.get(evidenceId) || [];
        links.push(node); linkedNodes.set(evidenceId, links);
      }
    }
    const choices = [];
    for (const node of nodes) {
      if (typeof node.id !== "string" || nodeIdCounts.get(node.id) !== 1
        || !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(node.id)
        || declarationCounts.get(node.id) !== 1) continue;
      const references = Array.isArray(node.evidence_ids) ? node.evidence_ids : [];
      for (const evidenceId of references) {
        const item = evidenceById.get(evidenceId);
        if (!item || evidenceIdCounts.get(evidenceId) !== 1
          || !["ocr_token", "vector_text"].includes(item.kind)
          || typeof item.text !== "string") continue;
        const hasControl = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(item.text);
        const label = item.text.trim();
        const links = linkedNodes.get(evidenceId) || [];
        if (!label || [...label].length > 200 || hasControl
          || links.length !== 1 || links[0] !== node) continue;
        const labelKey = ["text", "label", "name"].find(
          (key) => Object.prototype.hasOwnProperty.call(node, key),
        );
        if (labelKey && node[labelKey] === label) continue;
        choices.push({ id: evidenceId, kind: item.kind, label, nodeId: node.id });
      }
    }
    return choices;
  }

  function renderOverlay(diagram) {
    controls.overlay.replaceChildren();
    const expectedSource = sourceUrl(diagram);
    if (!expectedSource || state.sourceLoad?.url !== expectedSource) {
      controls.sourceCanvas.hidden = true; return;
    }
    const evidence = Array.isArray(diagram.provenance)
      ? diagram.provenance : Object.values(diagram.provenance?.evidence || {});
    const labelChoices = new Map(
      evidenceLabelChoices(diagram).map((item) => [item.id, item]),
    );
    const scene = diagram.scene_ir && typeof diagram.scene_ir === "object"
      ? diagram.scene_ir : {};
    const coordinateSpace = text(scene.coordinate_space || "pixels");
    const canvasSize = Array.isArray(scene.canvas_size) ? scene.canvas_size : [];
    const canvasValid = canvasSize.length === 2 && canvasSize.every(
      (value) => typeof value === "number" && Number.isFinite(value) && value > 0,
    );
    const width = coordinateSpace === "normalized"
      ? 1 : (canvasValid ? canvasSize[0] : state.sourceLoad.width);
    const height = coordinateSpace === "normalized"
      ? 1 : (canvasValid ? canvasSize[1] : state.sourceLoad.height);
    if (![width, height].every((value) => Number.isFinite(value) && value > 0)) {
      controls.sourceCanvas.hidden = true; return;
    }
    controls.overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const evidenceRects = [];
    for (const item of evidence) {
      if (!Array.isArray(item?.bbox) || item.bbox.length !== 4) continue;
      const [x0, y0, x1, y1] = item.bbox.map(Number);
      if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0
        || x0 < 0 || y0 < 0 || x1 > width || y1 > height) continue;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(x0)); rect.setAttribute("y", String(y0));
      rect.setAttribute("width", String(x1 - x0)); rect.setAttribute("height", String(y1 - y0));
      rect.setAttribute("class", "evidence-box");
      const choice = labelChoices.get(text(item.id));
      if (choice) {
        rect.classList.add("eligible");
        rect.setAttribute("tabindex", mutationLocked() ? "-1" : "0");
        rect.setAttribute("role", "button");
        rect.setAttribute("aria-disabled", mutationLocked() ? "true" : "false");
        rect.setAttribute(
          "aria-label", `Select ${choice.kind} ${choice.id} as the label for node `
            + `${choice.nodeId}: ${choice.label}`,
        );
        const selected = choice.id === controls.evidenceLabel.value
          && choice.nodeId === controls.evidenceLabelNode.value;
        rect.setAttribute("aria-pressed", selected ? "true" : "false");
        if (selected) {
          rect.classList.add("selected");
        }
      } else {
        rect.setAttribute("tabindex", "0");
        rect.setAttribute(
          "aria-label", `${text(item.kind || "evidence")}: ${text(item.text || item.id)}`,
        );
      }
      rect.dataset.evidenceId = text(item.id);
      evidenceRects.push(rect);
    }
    const elements = Array.isArray(scene.elements) ? scene.elements : [];
    for (const item of elements) {
      if (!Array.isArray(item?.bbox) || item.bbox.length !== 4) continue;
      const [x0, y0, x1, y1] = item.bbox.map(Number);
      if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0
        || x0 < 0 || y0 < 0 || x1 > width || y1 > height) continue;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(x0)); rect.setAttribute("y", String(y0));
      rect.setAttribute("width", String(x1 - x0)); rect.setAttribute("height", String(y1 - y0));
      rect.setAttribute("class", "node-box"); rect.setAttribute("tabindex", "0");
      rect.setAttribute("role", "button");
      rect.setAttribute("aria-label", `node ${text(item.id)}: ${text(item.text || "unlabelled")}`);
      rect.dataset.nodeId = text(item.id);
      const selected = text(item.id) === controls.node.value;
      rect.setAttribute("aria-pressed", selected ? "true" : "false");
      if (selected) rect.classList.add("selected");
      controls.overlay.append(rect);
    }
    for (const rect of evidenceRects) controls.overlay.append(rect);
    controls.sourceCanvas.hidden = false;
  }

  function layoutPositions(diagram) {
    const nodes = Array.isArray(diagram.scene_ir?.elements)
      ? [...diagram.scene_ir.elements].filter((item) => item?.id) : [];
    nodes.sort((left, right) => text(left.id).localeCompare(text(right.id)));
    const saved = new Map(
      (Array.isArray(diagram.layout_hints?.nodes) ? diagram.layout_hints.nodes : [])
        .map((item) => [text(item.node_id), [Number(item.x), Number(item.y)]]),
    );
    const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length)));
    return nodes.map((node, index) => {
      const fallback = [
        ((index % columns) + 1) / (columns + 1),
        (Math.floor(index / columns) + 1) / (Math.ceil(nodes.length / columns) + 1),
      ];
      const position = saved.get(text(node.id));
      const valid = position?.length === 2 && position.every(
        (value) => Number.isFinite(value) && value >= 0 && value <= 1,
      );
      return { node, position: valid ? position : fallback };
    });
  }

  function renderLayout(diagram) {
    controls.layout.replaceChildren();
    const positionedNodes = layoutPositions(diagram);
    const positionById = new Map(
      positionedNodes.map(({ node, position }) => [text(node.id), position]),
    );
    const relations = Array.isArray(diagram.scene_ir?.relations)
      ? diagram.scene_ir.relations.filter(
        (item) => item?.id && positionById.has(text(item.source_id))
          && positionById.has(text(item.target_id)),
      ) : [];
    for (const relation of relations) {
      const source = positionById.get(text(relation.source_id));
      const target = positionById.get(text(relation.target_id));
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("class", "layout-edge");
      line.setAttribute("x1", String(source[0])); line.setAttribute("y1", String(source[1]));
      line.setAttribute("x2", String(target[0])); line.setAttribute("y2", String(target[1]));
      line.setAttribute("tabindex", "0"); line.setAttribute("role", "button");
      line.setAttribute(
        "aria-label",
        `Select relation ${text(relation.id)}: ${text(relation.source_id)}`
          + ` to ${text(relation.target_id)}`,
      );
      line.dataset.edgeId = text(relation.id);
      if (text(relation.id) === controls.edge.value) line.classList.add("selected");
      controls.layout.append(line);
    }
    for (const { node, position } of positionedNodes) {
      const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.setAttribute("class", "layout-node");
      group.setAttribute("transform", `translate(${position[0]} ${position[1]})`);
      group.setAttribute("tabindex", "0");
      group.setAttribute("role", "button");
      group.setAttribute("aria-label", `Move layout hint for node ${text(node.id)}`);
      group.dataset.nodeId = text(node.id);
      group.dataset.x = String(position[0]); group.dataset.y = String(position[1]);
      if (text(node.id) === controls.node.value) group.classList.add("selected");
      const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      circle.setAttribute("r", ".035");
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.textContent = text(node.id).slice(0, 12);
      group.append(circle, label); controls.layout.append(group);
    }
    const selected = relations.find((item) => text(item.id) === controls.edge.value);
    if (!selected) return;
    for (const endpoint of ["source", "target"]) {
      const nodeId = text(selected[`${endpoint}_id`]);
      const position = positionById.get(nodeId);
      const handle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      handle.setAttribute("class", `edge-handle ${endpoint}`);
      handle.setAttribute("cx", String(position[0]));
      handle.setAttribute("cy", String(position[1]));
      handle.setAttribute("r", ".018"); handle.setAttribute("tabindex", "-1");
      handle.setAttribute(
        "aria-label",
        `Drag ${endpoint} endpoint of relation ${text(selected.id)} from node ${nodeId}`,
      );
      handle.dataset.edgeId = text(selected.id); handle.dataset.endpoint = endpoint;
      controls.layout.append(handle);
    }
  }

  function replaceOptions(select, values, selectedValue, labelFor) {
    select.replaceChildren();
    for (const value of values) {
      const option = document.createElement("option");
      option.value = text(value.id); option.textContent = labelFor(value);
      select.append(option);
    }
    if (values.some((value) => text(value.id) === text(selectedValue))) {
      select.value = text(selectedValue);
    }
  }

  function renderStructure(diagram) {
    const ir = diagram.scene_ir && typeof diagram.scene_ir === "object" ? diagram.scene_ir : {};
    const nodes = Array.isArray(ir.elements) ? ir.elements.filter((item) => item?.id) : [];
    const relations = Array.isArray(ir.relations)
      ? ir.relations.filter((item) => item?.id && item?.source_id && item?.target_id) : [];
    const groups = Array.isArray(ir.groups)
      ? ir.groups.filter((item) => item?.id && Array.isArray(item?.member_ids)) : [];
    const selectedNode = controls.node.value;
    const selectedEdge = controls.edge.value;
    const selectedEvidence = controls.evidenceLabel.value;
    const selectedGroupNodes = new Set(
      [...controls.groupNodeSelect.selectedOptions].map((option) => option.value),
    );
    replaceOptions(
      controls.node, nodes, selectedNode,
      (item) => `${text(item.id)} · ${text(item.text || item.role || "node")}`,
    );
    replaceOptions(
      controls.evidenceLabelNode, nodes, controls.node.value,
      (item) => `${text(item.id)} · ${text(item.text || item.role || "node")}`,
    );
    const labelChoices = evidenceLabelChoices(diagram).filter(
      (item) => item.nodeId === controls.evidenceLabelNode.value,
    );
    replaceOptions(
      controls.evidenceLabel, labelChoices, selectedEvidence,
      (item) => `${item.kind} · ${item.id} · ${item.label}`,
    );
    const selectedLabelChoice = labelChoices.find(
      (item) => item.id === controls.evidenceLabel.value,
    );
    controls.evidenceLabelStatus.textContent = selectedLabelChoice
      ? `Ready to use ${selectedLabelChoice.kind} evidence ${selectedLabelChoice.id}: `
        + selectedLabelChoice.label
      : (controls.evidenceLabelNode.value
        ? `No eligible linked OCR or vector-text label for node `
          + `${controls.evidenceLabelNode.value}.`
        : "No explicit node is available for source-backed relabelling.");
    for (const select of [
      controls.edgeSource, controls.edgeTarget, controls.addEdgeSource, controls.addEdgeTarget,
    ]) {
      const selected = select.value;
      replaceOptions(select, nodes, selected, (item) => text(item.id));
    }
    replaceOptions(
      controls.groupNodeSelect, nodes, "",
      (item) => `${text(item.id)} · ${text(item.text || item.role || "node")}`,
    );
    const groupedNodeIds = new Map();
    for (const group of Array.isArray(ir.groups) ? ir.groups : []) {
      for (const nodeId of Array.isArray(group?.member_ids) ? group.member_ids : []) {
        groupedNodeIds.set(text(nodeId), text(group.id || "group"));
      }
    }
    for (const option of controls.groupNodeSelect.options) {
      option.disabled = groupedNodeIds.has(option.value);
      if (option.disabled) {
        option.textContent += ` · already grouped in ${groupedNodeIds.get(option.value)}`;
      }
      option.selected = !option.disabled && selectedGroupNodes.has(option.value);
    }
    replaceOptions(
      controls.edge, relations, selectedEdge,
      (item) => `${text(item.id)} · ${text(item.source_id)} → ${text(item.target_id)}`,
    );
    replaceOptions(
      controls.deleteGroupSelect, groups, controls.deleteGroupSelect.value,
      (item) => `${text(item.id)} · ${text(item.label || "unlabelled")} · `
        + `${item.member_ids.length} node(s)`,
    );
    const selectedRelation = relations.find((item) => text(item.id) === controls.edge.value);
    if (selectedRelation) {
      controls.edgeSource.value = text(selectedRelation.source_id);
      controls.edgeTarget.value = text(selectedRelation.target_id);
    }
    if (controls.addEdgeSource.value === controls.addEdgeTarget.value && nodes.length > 1) {
      controls.addEdgeTarget.value = text(nodes[1].id);
    }
    const incident = relations.filter(
      (item) => controls.node.value
        && [item.source_id, item.target_id].map(text).includes(controls.node.value),
    ).length;
    controls.edgeCount.textContent = controls.node.value
      ? `${incident} incident relation(s) will also be deleted.` : "No selectable explicit node.";
    const locked = mutationLocked();
    const unavailable = locked || !nodes.length;
    controls.node.disabled = unavailable; controls.deleteNode.disabled = unavailable;
    controls.evidenceLabelNode.disabled = unavailable;
    controls.evidenceLabel.disabled = locked || !labelChoices.length;
    controls.applyEvidenceLabel.disabled = locked || !selectedLabelChoice;
    controls.edge.disabled = locked || !relations.length;
    controls.edgeSource.disabled = unavailable; controls.edgeTarget.disabled = unavailable;
    controls.reconnect.disabled = locked || !relations.length || !nodes.length;
    controls.deleteEdge.disabled = locked || !relations.length;
    const edgeAdditionUnavailable = locked || nodes.length < 2;
    controls.addEdgeSource.disabled = edgeAdditionUnavailable;
    controls.addEdgeTarget.disabled = edgeAdditionUnavailable;
    controls.addEdgeReason.disabled = edgeAdditionUnavailable;
    controls.addEdge.disabled = edgeAdditionUnavailable;
    controls.groupNodeSelect.disabled = locked || nodes.length < 2;
    controls.groupLabel.disabled = locked || nodes.length < 2;
    updateGroupSelectionState();
    controls.deleteGroupSelect.disabled = locked || !groups.length;
    controls.deleteGroup.disabled = locked || !groups.length;
    const canvas = ir.coordinate_space === "normalized"
      ? [1, 1] : (Array.isArray(ir.canvas_size) ? ir.canvas_size.map(Number) : []);
    const sourceAnchoringAvailable = canvas.length === 2 && canvas.every(Number.isFinite);
    controls.canvasSize.textContent = sourceAnchoringAvailable
      ? `Scene canvas: ${canvas[0]} × ${canvas[1]}`
      : "Scene canvas size is unavailable; source-anchored addition is disabled.";
    controls.addNode.disabled = locked || !sourceAnchoringAvailable;
  }

  function renderIssues(diagram) {
    controls.issues.replaceChildren();
    const issues = Array.isArray(diagram.issues) ? diagram.issues : [];
    for (const issue of issues) {
      const item = document.createElement("li");
      item.textContent = typeof issue === "string"
        ? issue : text(issue.message || issue.code || issue.type);
      controls.issues.append(item);
    }
    if (!issues.length) {
      const item = document.createElement("li");
      item.textContent = "No reported issues.";
      controls.issues.append(item);
    }
  }

  function renderAlternatives(diagram) {
    controls.alternatives.replaceChildren();
    const alternatives = Array.isArray(diagram.alternatives) ? diagram.alternatives : [];
    for (const candidate of alternatives) {
      const candidateId = text(candidate.candidate_id || candidate.id);
      const row = document.createElement("div");
      row.className = "candidate" + (
        candidateId === text(diagram.selected_candidate_id) ? " selected" : ""
      );
      const description = document.createElement("span");
      description.textContent = [
        candidateId,
        text(candidate.diagram_type || "unknown"),
        text(candidate.aggregate_score ?? "unscored"),
      ].join(" · ");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Select";
      button.dataset.candidateId = candidateId;
      button.disabled = mutationLocked() || candidateId === text(diagram.selected_candidate_id);
      row.append(description, button); controls.alternatives.append(row);
    }
    if (!alternatives.length) controls.alternatives.textContent = "No alternative candidates.";
  }

  function renderCurrent() {
    const diagram = state.current;
    if (!diagram) return;
    controls.diagram.value = text(diagram.id);
    controls.diagram.disabled = state.busy;
    if (!state.editorBaseline
      || text(state.editorBaseline.diagramId) !== text(diagram.id)) {
      if (state.detailReady) syncEditors(diagram);
      else resetEditors(diagram);
    }
    updateSourceImage(diagram);
    const regularRenderUrl = imageUrl(
      diagram.rendered_url || diagram.render_url || diagram.final_svg,
    );
    if (regularRenderUrl) controls.render.setAttribute("src", regularRenderUrl);
    else controls.render.removeAttribute("src");
    renderDifference(diagram);
    controls.undo.disabled = !diagram.can_undo || mutationLocked();
    controls.redo.disabled = !diagram.can_redo || mutationLocked();
    const navigation = diagram.revision_navigation
      && typeof diagram.revision_navigation === "object" ? diagram.revision_navigation : {};
    const timeline = Array.isArray(navigation.timeline)
      ? navigation.timeline.map(text).filter(Boolean) : [];
    const selectedRevision = controls.revision.value;
    controls.revision.replaceChildren();
    for (const [index, revision] of timeline.entries()) {
      const option = document.createElement("option");
      option.value = revision;
      option.textContent = index === 0
        ? `${revision} · automated baseline` : revision;
      controls.revision.append(option);
    }
    const currentRevision = text(navigation.current_revision);
    controls.revision.value = timeline.some(
      (revision) => revision === selectedRevision,
    ) ? selectedRevision : currentRevision;
    controls.revision.disabled = mutationLocked() || !timeline.length;
    controls.checkoutRevision.disabled = mutationLocked() || !timeline.length
      || controls.revision.value === currentRevision;
    renderEditorState(diagram);
    renderStructure(diagram); renderOverlay(diagram); renderLayout(diagram);
    renderIssues(diagram); renderAlternatives(diagram);
  }

  function errorText(error) {
    return error instanceof Error ? error.message : String(error);
  }

  async function perform(path, body, successMessage, { keepEditorDraft = false } = {}) {
    if (mutationLocked() || !state.current) return false;
    if (editorDraftDirty() && !keepEditorDraft
      && !window.confirm("Discard unsaved editor draft and continue?")) return false;
    const requestDiagramId = text(state.current.id);
    const expectedVersion = Number(state.current.version);
    const expectedDigest = text(state.current.digest);
    state.diagramRequest += 1;
    state.busy = true; showMessage(""); renderCurrent();
    let succeeded = false;
    try {
      const request = {
        ...body,
        expected_version: expectedVersion,
        expected_digest: expectedDigest,
      };
      const payload = await sameOriginFetch(
        path, { method: "POST", body: JSON.stringify(request) },
      );
      const next = normalizeDiagram(payload);
      if (text(next?.id) !== requestDiagramId || text(state.current?.id) !== requestDiagramId) {
        throw new Error("Mutation response did not match the active diagram");
      }
      replaceCurrent(payload); showMessage(successMessage);
      succeeded = true;
    } catch (error) {
      if (error?.status === 409 && text(state.current?.id) === requestDiagramId) {
        const originalError = errorText(error);
        const preserveDraft = editorDraftDirty();
        if (preserveDraft) state.editorConflict = true;
        state.detailReady = false;
        renderCurrent();
        try {
          const latest = await sameOriginFetch(
            `/api/diagrams/${encodeURIComponent(requestDiagramId)}`,
          );
          const latestDiagram = normalizeDiagram(latest);
          if (text(latestDiagram?.id) !== requestDiagramId
            || text(state.current?.id) !== requestDiagramId) {
            throw new Error("Latest revision response did not match the active diagram");
          }
          replaceCurrent(latest, { preserveDraft });
          showMessage(
            preserveDraft
              ? "Revision conflict: latest server revision loaded; editor draft preserved. "
                + "Reload latest before saving."
              : "Revision conflict: latest server revision loaded; retry the action.",
            true,
          );
        } catch (refreshError) {
          showMessage(
            `${originalError}. Latest revision refresh failed: ${errorText(refreshError)}`,
            true,
          );
        }
      } else {
        showMessage(errorText(error), true);
      }
    } finally {
      state.busy = false; renderCurrent();
    }
    return succeeded;
  }

  async function loadSelectedDiagram() {
    const selected = state.diagrams.find((item) => String(item.id) === controls.diagram.value);
    if (!selected) return;
    if (state.busy) {
      controls.diagram.value = text(state.current?.id);
      return;
    }
    const selectedId = text(selected.id);
    const switching = selectedId !== text(state.current?.id);
    if (switching && editorDraftDirty()
      && !window.confirm("Discard unsaved editor draft and switch diagrams?")) {
      controls.diagram.value = text(state.current?.id);
      return;
    }
    const request = ++state.diagramRequest;
    state.diagramLoading = true;
    state.detailReady = false;
    clearGroupSelection();
    if (switching) {
      state.current = selected;
      resetEditors(selected);
    }
    renderCurrent();
    try {
      const id = encodeURIComponent(selectedId);
      const payload = await sameOriginFetch(`/api/diagrams/${id}`);
      if (request !== state.diagramRequest || controls.diagram.value !== selectedId) return;
      const next = normalizeDiagram(payload);
      if (text(next?.id) !== selectedId) {
        throw new Error("Diagram response did not match the requested diagram");
      }
      state.diagramLoading = false;
      replaceCurrent(payload, { preserveDraft: editorDraftDirty() });
    } catch (error) {
      if (request === state.diagramRequest) {
        state.diagramLoading = false; renderCurrent(); showMessage(errorText(error), true);
      }
    }
  }

  controls.diagram.addEventListener("change", loadSelectedDiagram);
  for (const editor of [controls.mermaid, controls.ir]) {
    editor.addEventListener("input", () => renderEditorState(state.current || {}));
  }
  controls.reloadLatest.addEventListener("click", async () => {
    if (state.busy || state.diagramLoading || !state.current) return;
    const dirty = editorDraftDirty();
    const requiresFetch = !state.detailReady || state.editorConflict;
    if (!requiresFetch) {
      if (!dirty
        || !window.confirm("Discard editor draft and restore the loaded revision?")) return;
      syncEditors(state.current); renderCurrent();
      showMessage("Editor draft discarded.");
      return;
    }
    if (dirty && !window.confirm("Discard editor draft and reload the latest revision?")) return;
    const requestedId = text(state.current.id);
    const request = ++state.diagramRequest;
    state.diagramLoading = true; renderCurrent();
    try {
      const payload = await sameOriginFetch(
        `/api/diagrams/${encodeURIComponent(requestedId)}`,
      );
      if (request !== state.diagramRequest || text(state.current?.id) !== requestedId) return;
      const next = normalizeDiagram(payload);
      if (text(next?.id) !== requestedId) {
        throw new Error("Latest revision response did not match the active diagram");
      }
      state.diagramLoading = false;
      replaceCurrent(payload);
      showMessage(dirty
        ? "Latest revision loaded; editor draft discarded." : "Diagram detail loaded.");
    } catch (error) {
      if (request === state.diagramRequest) {
        state.diagramLoading = false; renderCurrent(); showMessage(errorText(error), true);
      }
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (!editorDraftDirty()) return;
    event.preventDefault(); event.returnValue = "";
  });

  controls.render.addEventListener("load", () => renderLayout(state.current || {}));
  controls.diffEnabled.addEventListener("change", () => renderDifference(state.current || {}));
  controls.diffOpacity.addEventListener("input", () => renderDifference(state.current || {}));
  function selectOverlayNode(nodeId) {
    controls.node.value = text(nodeId);
    controls.evidenceLabelNode.value = text(nodeId);
    renderStructure(state.current || {}); renderOverlay(state.current || {});
  }
  function selectOverlayEvidence(evidenceId) {
    if (mutationLocked()) return;
    const choice = evidenceLabelChoices(state.current || {}).find(
      (item) => item.id === text(evidenceId),
    );
    if (!choice) {
      showMessage(
        "This observation is not a uniquely linked, safe OCR or vector-text label.", true,
      );
      return;
    }
    controls.node.value = choice.nodeId;
    controls.evidenceLabelNode.value = choice.nodeId;
    renderStructure(state.current || {});
    controls.evidenceLabel.value = choice.id;
    renderStructure(state.current || {}); renderOverlay(state.current || {});
    renderLayout(state.current || {}); controls.evidenceLabel.focus();
  }
  controls.overlay.addEventListener("click", (event) => {
    const evidence = event.target.closest("[data-evidence-id]");
    if (evidence) { selectOverlayEvidence(evidence.dataset.evidenceId); return; }
    const node = event.target.closest("[data-node-id]");
    if (!node) return;
    selectOverlayNode(node.dataset.nodeId);
  });
  controls.overlay.addEventListener("keydown", (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    const evidence = event.target.closest("[data-evidence-id]");
    if (evidence) {
      event.preventDefault(); selectOverlayEvidence(evidence.dataset.evidenceId); return;
    }
    const node = event.target.closest("[data-node-id]");
    if (!node) return;
    event.preventDefault(); selectOverlayNode(node.dataset.nodeId);
  });
  controls.node.addEventListener("change", () => {
    controls.evidenceLabelNode.value = controls.node.value;
    renderStructure(state.current || {}); renderOverlay(state.current || {});
    renderLayout(state.current || {});
  });
  controls.evidenceLabelNode.addEventListener("change", () => {
    controls.node.value = controls.evidenceLabelNode.value;
    renderStructure(state.current || {}); renderOverlay(state.current || {});
    renderLayout(state.current || {});
  });
  controls.evidenceLabel.addEventListener("change", () => {
    renderStructure(state.current || {}); renderOverlay(state.current || {});
  });
  controls.edge.addEventListener("change", () => {
    renderStructure(state.current || {}); renderLayout(state.current || {});
  });
  function clearGroupSelection() {
    for (const option of controls.groupNodeSelect.options) option.selected = false;
    controls.groupLabel.value = "";
    updateGroupSelectionState();
  }
  function updateGroupSelectionState() {
    const count = controls.groupNodeSelect.selectedOptions.length;
    controls.groupStatus.textContent = `${count} node(s) selected.`;
    controls.groupNodes.disabled = mutationLocked() || count < 2
      || controls.groupLabel.value.trim().length === 0;
  }
  controls.groupNodeSelect.addEventListener("change", updateGroupSelectionState);
  controls.groupLabel.addEventListener("input", updateGroupSelectionState);
  let layoutDrag = null; let edgeDrag = null;
  function normalizedPointer(event) {
    const bounds = controls.layout.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return null;
    return [
      Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width)),
      Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height)),
    ];
  }
  controls.layout.addEventListener("pointerdown", (event) => {
    const node = event.target.closest(".layout-node[data-node-id]");
    if (!node || layoutDrag || edgeDrag || mutationLocked()
      || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault(); controls.layout.setPointerCapture(event.pointerId);
    layoutDrag = {
      node, nodeId: node.dataset.nodeId, pointerId: event.pointerId,
      startX: Number(node.dataset.x), startY: Number(node.dataset.y), moved: false,
    };
    selectOverlayNode(layoutDrag.nodeId);
  });
  controls.layout.addEventListener("pointermove", (event) => {
    if (!layoutDrag || event.pointerId !== layoutDrag.pointerId) return;
    const position = normalizedPointer(event);
    if (!position) return;
    if (Math.hypot(position[0] - layoutDrag.startX, position[1] - layoutDrag.startY) > .002) {
      layoutDrag.moved = true;
    }
    layoutDrag.node.dataset.x = String(position[0]);
    layoutDrag.node.dataset.y = String(position[1]);
    layoutDrag.node.setAttribute("transform", `translate(${position[0]} ${position[1]})`);
  });
  async function saveLayoutPosition(nodeId, position) {
    const saved = await perform(
      route("/operations"),
      { operation: { operation: "move_node", node_id: nodeId, position } },
      "Advisory layout hint saved.",
    );
    if (!saved) { renderLayout(state.current || {}); return; }
    const node = [...controls.layout.querySelectorAll(".layout-node[data-node-id]")]
      .find((item) => item.dataset.nodeId === nodeId);
    if (node) node.focus();
  }
  controls.layout.addEventListener("pointerup", (event) => {
    if (!layoutDrag || event.pointerId !== layoutDrag.pointerId) return;
    const completed = layoutDrag; layoutDrag = null;
    if (controls.layout.hasPointerCapture(event.pointerId)) {
      controls.layout.releasePointerCapture(event.pointerId);
    }
    if (!completed.moved) { renderLayout(state.current || {}); return; }
    saveLayoutPosition(
      completed.nodeId,
      [Number(completed.node.dataset.x), Number(completed.node.dataset.y)],
    );
  });
  controls.layout.addEventListener("pointercancel", (event) => {
    if (!layoutDrag || event.pointerId !== layoutDrag.pointerId) return;
    layoutDrag = null; renderLayout(state.current || {});
  });
  controls.layout.addEventListener("keydown", (event) => {
    const node = event.target.closest(".layout-node[data-node-id]");
    const delta = { ArrowLeft: [-.025, 0], ArrowRight: [.025, 0],
      ArrowUp: [0, -.025], ArrowDown: [0, .025] }[event.key];
    if (!node || !delta || mutationLocked()) return;
    event.preventDefault();
    const position = [
      Math.min(1, Math.max(0, Number(node.dataset.x) + delta[0])),
      Math.min(1, Math.max(0, Number(node.dataset.y) + delta[1])),
    ];
    saveLayoutPosition(node.dataset.nodeId, position);
  });
  function selectOverlayEdge(edgeId) {
    controls.edge.value = text(edgeId);
    renderStructure(state.current || {}); renderLayout(state.current || {});
  }
  controls.layout.addEventListener("click", (event) => {
    const edge = event.target.closest(".layout-edge[data-edge-id]");
    if (edge && !edgeDrag) selectOverlayEdge(edge.dataset.edgeId);
  });
  function nearestLayoutNode(clientX, clientY) {
    const bounds = controls.layout.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return null;
    const candidates = layoutPositions(state.current || {}).map(({ node, position }) => ({
      nodeId: text(node.id),
      distance: Math.hypot(
        clientX - (bounds.left + position[0] * bounds.width),
        clientY - (bounds.top + position[1] * bounds.height),
      ),
    })).sort((left, right) => left.distance - right.distance);
    const radius = Math.min(48, Math.max(24, Math.min(bounds.width, bounds.height) * .08));
    if (!candidates.length || candidates[0].distance > radius) return null;
    if (candidates.length > 1 && candidates[1].distance - candidates[0].distance <= .5) return null;
    return candidates[0].nodeId;
  }
  function cancelEdgeDrag() {
    if (edgeDrag?.frame) cancelAnimationFrame(edgeDrag.frame);
    edgeDrag = null; renderLayout(state.current || {});
  }
  function previewEdgeEndpoint(position) {
    if (!edgeDrag) return;
    const axis = edgeDrag.endpoint === "source" ? ["x1", "y1"] : ["x2", "y2"];
    edgeDrag.line.setAttribute(axis[0], String(position[0]));
    edgeDrag.line.setAttribute(axis[1], String(position[1]));
    edgeDrag.handle.setAttribute("cx", String(position[0]));
    edgeDrag.handle.setAttribute("cy", String(position[1]));
  }
  controls.layout.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest(".edge-handle[data-edge-id][data-endpoint]");
    if (!handle || edgeDrag || layoutDrag || mutationLocked()
      || event.button !== 0 || event.isPrimary === false) return;
    const relations = Array.isArray(state.current?.scene_ir?.relations)
      ? state.current.scene_ir.relations : [];
    const relation = relations.find((item) => text(item.id) === handle.dataset.edgeId);
    const line = [...controls.layout.querySelectorAll(".layout-edge[data-edge-id]")]
      .find((item) => item.dataset.edgeId === handle.dataset.edgeId);
    if (!relation || !line || !["source", "target"].includes(handle.dataset.endpoint)) return;
    event.preventDefault(); controls.layout.setPointerCapture(event.pointerId);
    handle.focus();
    edgeDrag = {
      handle, line, endpoint: handle.dataset.endpoint, edgeId: text(relation.id),
      sourceId: text(relation.source_id), targetId: text(relation.target_id),
      pointerId: event.pointerId, startClientX: event.clientX, startClientY: event.clientY,
      moved: false, frame: 0, pendingPosition: null,
      diagramId: diagramId(), version: Number(state.current.version),
      digest: text(state.current.digest),
    };
  });
  controls.layout.addEventListener("pointermove", (event) => {
    if (!edgeDrag || event.pointerId !== edgeDrag.pointerId) return;
    const position = normalizedPointer(event);
    if (!position) return;
    const movement = Math.hypot(
      event.clientX - edgeDrag.startClientX, event.clientY - edgeDrag.startClientY,
    );
    if (movement > 3) {
      edgeDrag.moved = true;
    }
    edgeDrag.pendingPosition = position;
    if (!edgeDrag.frame) edgeDrag.frame = requestAnimationFrame(() => {
      if (!edgeDrag) return;
      edgeDrag.frame = 0;
      if (edgeDrag.pendingPosition) previewEdgeEndpoint(edgeDrag.pendingPosition);
    });
  });
  async function saveEdgeReconnect(edgeId, sourceId, targetId, focusEndpoint = "") {
    const saved = await perform(
      route("/operations"),
      { operation: { operation: "reconnect_edge", edge_id: edgeId, source_id: sourceId,
        target_id: targetId } },
      "Edge reconnected.",
    );
    if (!saved) { renderLayout(state.current || {}); return; }
    const handle = [...controls.layout.querySelectorAll(".edge-handle[data-endpoint]")]
      .find((item) => item.dataset.edgeId === edgeId && item.dataset.endpoint === focusEndpoint);
    if (handle) handle.focus();
  }
  controls.layout.addEventListener("pointerup", (event) => {
    if (!edgeDrag || event.pointerId !== edgeDrag.pointerId) return;
    const completed = edgeDrag; edgeDrag = null;
    if (completed.frame) cancelAnimationFrame(completed.frame);
    if (controls.layout.hasPointerCapture(event.pointerId)) {
      controls.layout.releasePointerCapture(event.pointerId);
    }
    const relation = (Array.isArray(state.current?.scene_ir?.relations)
      ? state.current.scene_ir.relations : [])
      .find((item) => text(item.id) === completed.edgeId);
    const unchangedRevision = completed.diagramId === diagramId()
      && completed.version === Number(state.current?.version)
      && completed.digest === text(state.current?.digest);
    const unchangedRelation = relation && text(relation.source_id) === completed.sourceId
      && text(relation.target_id) === completed.targetId;
    const nodeId = nearestLayoutNode(event.clientX, event.clientY);
    if (!completed.moved || !unchangedRevision || !unchangedRelation || !nodeId) {
      renderLayout(state.current || {}); return;
    }
    const sourceId = completed.endpoint === "source" ? nodeId : completed.sourceId;
    const targetId = completed.endpoint === "target" ? nodeId : completed.targetId;
    if (sourceId === targetId
      || (sourceId === completed.sourceId && targetId === completed.targetId)) {
      renderLayout(state.current || {}); return;
    }
    saveEdgeReconnect(completed.edgeId, sourceId, targetId, completed.endpoint);
  });
  controls.layout.addEventListener("pointercancel", (event) => {
    if (edgeDrag && event.pointerId === edgeDrag.pointerId) cancelEdgeDrag();
  });
  controls.layout.addEventListener("lostpointercapture", (event) => {
    if (edgeDrag && event.pointerId === edgeDrag.pointerId) cancelEdgeDrag();
  });
  controls.layout.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && edgeDrag) {
      event.preventDefault(); cancelEdgeDrag(); return;
    }
    const edge = event.target.closest(".layout-edge[data-edge-id]");
    if (edge && ["Enter", " "].includes(event.key)) {
      event.preventDefault(); selectOverlayEdge(edge.dataset.edgeId);
    }
  });
  controls.saveEditors.addEventListener("click", async () => {
    if (state.editorConflict && editorDraftDirty()) {
      showMessage("Reload the latest revision before saving this conflicting draft.", true);
      return;
    }
    let sceneIr;
    try { sceneIr = JSON.parse(controls.ir.value); }
    catch (_) { showMessage("Scene IR must be valid JSON.", true); return; }
    await perform(
      route("/edits"),
      { mermaid_code: controls.mermaid.value, scene_ir: sceneIr },
      "Edits saved.",
      { keepEditorDraft: true },
    );
  });
  controls.undo.addEventListener("click", () => {
    perform(route("/history"), { action: "undo" }, "Undid edit.");
  });
  controls.redo.addEventListener("click", () => {
    perform(route("/history"), { action: "redo" }, "Redid edit.");
  });
  controls.revision.addEventListener("change", () => {
    const navigation = state.current?.revision_navigation || {};
    controls.checkoutRevision.disabled = mutationLocked()
      || controls.revision.value === text(navigation.current_revision);
  });
  controls.checkoutRevision.addEventListener("click", () => {
    const revision = controls.revision.value;
    const currentRevision = text(state.current?.revision_navigation?.current_revision);
    if (!revision || revision === currentRevision) return;
    if (!window.confirm(
      `Restore ${revision}? A later edit will branch the active timeline from this point.`,
    )) return;
    perform(
      route("/history"),
      { action: "checkout", revision, reason: `restored revision ${revision}` },
      `Restored ${revision}.`,
    );
  });
  controls.alternatives.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-candidate-id]");
    if (button) {
      perform(
        route("/candidate"),
        { candidate_id: button.dataset.candidateId },
        "Candidate selected.",
      );
    }
  });
  byId("command-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = controls.commandInput; const command = input.value.trim();
    if (!command) return;
    if (await perform(route("/commands"), { command }, "Command applied.")
      && input.value.trim() === command) input.value = "";
  });
  byId("reconnect-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveEdgeReconnect(
      controls.edge.value, controls.edgeSource.value, controls.edgeTarget.value,
    );
  });
  controls.deleteEdge.addEventListener("click", async () => {
    const edgeId = controls.edge.value;
    if (!edgeId || !window.confirm(`Delete relation ${edgeId}?`)) return;
    await perform(
      route("/operations"),
      { operation: { operation: "delete_edge", edge_id: edgeId } },
      "Edge deleted.",
    );
  });
  byId("add-edge-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const reason = controls.addEdgeReason.value.trim();
    if (!reason) {
      showMessage("An evidence note is required to add an edge.", true); return;
    }
    await perform(
      route("/operations"),
      { operation: { operation: "add_edge", source_id: controls.addEdgeSource.value,
        target_id: controls.addEdgeTarget.value }, reason },
      "Edge added.",
    );
  });
  byId("evidence-label-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const nodeId = controls.evidenceLabelNode.value;
    const evidenceId = controls.evidenceLabel.value;
    const choice = evidenceLabelChoices(state.current || {}).find(
      (item) => item.nodeId === nodeId && item.id === evidenceId,
    );
    if (!choice) {
      showMessage("Select an eligible source-backed label for this node.", true); return;
    }
    const saved = await perform(
      route("/operations"),
      { operation: {
        operation: "relabel_node_from_evidence", node_id: nodeId, evidence_id: evidenceId,
      } },
      "Node relabelled from source evidence.",
    );
    if (saved) controls.evidenceLabelStatus.focus();
  });
  byId("group-nodes-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const nodeIds = [...controls.groupNodeSelect.selectedOptions]
      .map((option) => option.value);
    if (nodeIds.length < 2) {
      showMessage("Select at least two node IDs to create a group.", true); return;
    }
    const label = controls.groupLabel.value.trim();
    if (!label) {
      showMessage("Enter a group label.", true); return;
    }
    await perform(
      route("/operations"),
      { operation: { operation: "group_nodes", node_ids: nodeIds,
        ...(label ? { label } : {}) } },
      "Nodes grouped.",
    );
    controls.groupStatus.focus();
  });
  byId("delete-group-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const groupId = controls.deleteGroupSelect.value;
    const selected = (Array.isArray(state.current?.scene_ir?.groups)
      ? state.current.scene_ir.groups : []).find((item) => text(item.id) === groupId);
    if (!selected) return;
    const memberCount = Array.isArray(selected.member_ids) ? selected.member_ids.length : 0;
    if (!window.confirm(
      `Delete group ${groupId} (${text(selected.label || "unlabelled")}, ${memberCount} nodes)? `
        + "Member nodes and edges will remain.",
    )) return;
    await perform(
      route("/operations"),
      { operation: { operation: "delete_group", group_id: groupId } },
      "Group deleted.",
    );
    if (!controls.deleteGroupSelect.disabled) controls.deleteGroupSelect.focus();
  });
  byId("add-node-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const bbox = controls.bbox.map((input) => Number(input.value));
    if (!bbox.every(Number.isFinite)) {
      showMessage("Source bbox must contain four finite numbers.", true); return;
    }
    await perform(
      route("/operations"),
      {
        operation: {
          operation: "add_node", node_id: controls.addNodeId.value.trim(),
          label: controls.addNodeLabel.value.trim(), bbox,
        },
        reason: controls.addNodeReason.value.trim(),
      },
      "Source-anchored node added.",
    );
  });
  byId("delete-node-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!controls.node.value || !window.confirm(
      `Delete node ${controls.node.value} and all incident relations?`,
    )) return;
    await perform(
      route("/operations"),
      { operation: { operation: "delete_node", node_id: controls.node.value } },
      "Node deleted.",
    );
  });
  for (const decision of ["approve", "reject"]) {
    byId(decision).addEventListener("click", () => perform(
      route("/decision"), { decision, reason: controls.decisionReason.value.trim() },
      decision === "approve" ? "Reconstruction approved." : "Reconstruction rejected.",
    ));
  }

  for (const diagram of state.diagrams) {
    const option = document.createElement("option");
    option.value = text(diagram.id);
    option.textContent = text(diagram.label || diagram.source_id || diagram.id);
    controls.diagram.append(option);
  }
  byId("workspace-summary").textContent = `${state.diagrams.length} reconstruction(s) available`;
  state.current = state.diagrams[0] || null;
  if (state.current) { renderCurrent(); loadSelectedDiagram(); }
  else showMessage("No reconstruction sidecars were found.");
})();
""".strip()
