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
        <button id="undo" type="button" disabled>Undo</button>
        <button id="redo" type="button" disabled>Redo</button>
      </nav>
    </header>

    <main>
      <section class="selection-bar" aria-label="Diagram selection">
        <label for="diagram-select">Diagram</label>
        <select id="diagram-select"></select>
        <span id="save-state" class="muted" role="status" aria-live="polite"></span>
      </section>

      <section class="visual-grid" aria-label="Visual comparison">
        <figure>
          <figcaption>Source image and provenance</figcaption>
          <div id="source-stage" class="image-stage">
            <img id="source-image" alt="Source diagram">
            <svg id="provenance-overlay" aria-label="Source evidence overlay"></svg>
          </div>
        </figure>
        <figure>
          <figcaption>Mermaid render</figcaption>
          <div class="image-stage">
            <img id="render-image" alt="Rendered Mermaid reconstruction">
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
          Select source-backed nodes and relations by stable ID. Node movement and insertion
          remain unavailable until layout hints and user-edit provenance are revisioned safely.
        </p>
        <div class="structure-grid">
          <form id="reconnect-form">
            <h3>Reconnect edge</h3>
            <label for="edge-select">Relation</label>
            <select id="edge-select" required></select>
            <label for="edge-source">New source</label>
            <select id="edge-source" required></select>
            <label for="edge-target">New target</label>
            <select id="edge-target" required></select>
            <button id="reconnect-edge" type="submit">Reconnect</button>
          </form>
          <form id="delete-node-form">
            <h3>Delete node</h3>
            <label for="node-select">Explicit node</label>
            <select id="node-select" required></select>
            <p id="node-edge-count" class="muted"></p>
            <button id="delete-node" class="reject" type="submit">Delete node and edges</button>
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
#provenance-overlay {
  position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: auto;
}
.evidence-box {
  fill: rgb(54 162 235 / .12); stroke: #087dbd; stroke-width: 2;
  vector-effect: non-scaling-stroke;
}
.evidence-box:hover, .evidence-box:focus { fill: rgb(255 159 64 / .25); stroke: #e26f00; }
.node-box {
  fill: transparent; stroke: #7453c6; stroke-width: 2; stroke-dasharray: 6 3;
  vector-effect: non-scaling-stroke; cursor: pointer;
}
.node-box:hover, .node-box:focus, .node-box.selected {
  fill: rgb(116 83 198 / .18); stroke: #4f2d9e;
}
.editor-grid { margin-top: 1rem; }
.editor-grid label { display: grid; gap: .4rem; }
textarea {
  width: 100%; min-height: 18rem; resize: vertical;
  font: .9rem ui-monospace, monospace; tab-size: 2;
}
.editor-actions { justify-content: end; margin-block: .6rem 1rem; }
#structure-operations { border: 1px solid GrayText; padding: 0 1rem 1rem; margin-block: 1rem; }
.structure-grid { display: grid; grid-template-columns: 2fr 1fr; gap: 1rem; }
.structure-grid form {
  display: grid; grid-template-columns: max-content 1fr; gap: .6rem; align-items: center;
}
.structure-grid h3, .structure-grid p { grid-column: 1 / -1; }
.structure-grid button { justify-self: end; grid-column: 2; }
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
  };
  const byId = (id) => document.getElementById(id);
  const controls = {
    diagram: byId("diagram-select"), source: byId("source-image"), render: byId("render-image"),
    overlay: byId("provenance-overlay"), mermaid: byId("mermaid-editor"), ir: byId("ir-editor"),
    issues: byId("issue-list"), alternatives: byId("alternative-list"), message: byId("message"),
    saveState: byId("save-state"), undo: byId("undo"), redo: byId("redo"),
    node: byId("node-select"), edge: byId("edge-select"),
    edgeSource: byId("edge-source"), edgeTarget: byId("edge-target"),
    edgeCount: byId("node-edge-count"), reconnect: byId("reconnect-edge"),
    deleteNode: byId("delete-node"),
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
      throw new Error(detail);
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

  function replaceCurrent(payload) {
    const next = normalizeDiagram(payload);
    if (!next || typeof next !== "object") return;
    const index = state.diagrams.findIndex((item) => String(item.id) === String(next.id));
    if (index >= 0) state.diagrams[index] = next;
    state.current = next;
    renderCurrent();
  }

  function text(value) { return value === null || value === undefined ? "" : String(value); }

  function imageUrl(value) {
    if (!value) return "";
    const parsed = new URL(String(value), window.location.origin);
    return parsed.origin === window.location.origin ? parsed.pathname + parsed.search : "";
  }

  function renderOverlay(diagram) {
    controls.overlay.replaceChildren();
    const evidence = Array.isArray(diagram.provenance)
      ? diagram.provenance : Object.values(diagram.provenance?.evidence || {});
    const width = Number(diagram.source_width || controls.source.naturalWidth || 1);
    const height = Number(diagram.source_height || controls.source.naturalHeight || 1);
    controls.overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
    for (const item of evidence) {
      if (!Array.isArray(item?.bbox) || item.bbox.length !== 4) continue;
      const [x0, y0, x1, y1] = item.bbox.map(Number);
      if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0) continue;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(x0)); rect.setAttribute("y", String(y0));
      rect.setAttribute("width", String(x1 - x0)); rect.setAttribute("height", String(y1 - y0));
      rect.setAttribute("class", "evidence-box"); rect.setAttribute("tabindex", "0");
      rect.setAttribute(
        "aria-label", `${text(item.kind || "evidence")}: ${text(item.text || item.id)}`,
      );
      rect.dataset.evidenceId = text(item.id);
      controls.overlay.append(rect);
    }
    const elements = Array.isArray(diagram.scene_ir?.elements)
      ? diagram.scene_ir.elements : [];
    for (const item of elements) {
      if (!Array.isArray(item?.bbox) || item.bbox.length !== 4) continue;
      const [x0, y0, x1, y1] = item.bbox.map(Number);
      if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0) continue;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(x0)); rect.setAttribute("y", String(y0));
      rect.setAttribute("width", String(x1 - x0)); rect.setAttribute("height", String(y1 - y0));
      rect.setAttribute("class", "node-box"); rect.setAttribute("tabindex", "0");
      rect.setAttribute("aria-label", `node ${text(item.id)}: ${text(item.text || "unlabelled")}`);
      rect.dataset.nodeId = text(item.id);
      if (text(item.id) === controls.node.value) rect.classList.add("selected");
      controls.overlay.append(rect);
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
    const selectedNode = controls.node.value;
    const selectedEdge = controls.edge.value;
    replaceOptions(
      controls.node, nodes, selectedNode,
      (item) => `${text(item.id)} · ${text(item.text || item.role || "node")}`,
    );
    for (const select of [controls.edgeSource, controls.edgeTarget]) {
      const selected = select.value;
      replaceOptions(select, nodes, selected, (item) => text(item.id));
    }
    replaceOptions(
      controls.edge, relations, selectedEdge,
      (item) => `${text(item.id)} · ${text(item.source_id)} → ${text(item.target_id)}`,
    );
    const selectedRelation = relations.find((item) => text(item.id) === controls.edge.value);
    if (selectedRelation) {
      controls.edgeSource.value = text(selectedRelation.source_id);
      controls.edgeTarget.value = text(selectedRelation.target_id);
    }
    const incident = relations.filter(
      (item) => controls.node.value
        && [item.source_id, item.target_id].map(text).includes(controls.node.value),
    ).length;
    controls.edgeCount.textContent = controls.node.value
      ? `${incident} incident relation(s) will also be deleted.` : "No selectable explicit node.";
    const unavailable = state.busy || !nodes.length;
    controls.node.disabled = unavailable; controls.deleteNode.disabled = unavailable;
    controls.edge.disabled = state.busy || !relations.length;
    controls.edgeSource.disabled = unavailable; controls.edgeTarget.disabled = unavailable;
    controls.reconnect.disabled = state.busy || !relations.length || !nodes.length;
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
      button.disabled = candidateId === text(diagram.selected_candidate_id);
      row.append(description, button); controls.alternatives.append(row);
    }
    if (!alternatives.length) controls.alternatives.textContent = "No alternative candidates.";
  }

  function renderCurrent() {
    const diagram = state.current;
    if (!diagram) return;
    controls.diagram.value = text(diagram.id);
    controls.source.src = imageUrl(diagram.source_url || diagram.source_image);
    controls.render.src = imageUrl(diagram.rendered_url || diagram.render_url || diagram.final_svg);
    controls.mermaid.value = text(diagram.mermaid_code);
    controls.ir.value = JSON.stringify(diagram.scene_ir || {}, null, 2);
    controls.undo.disabled = !diagram.can_undo || state.busy;
    controls.redo.disabled = !diagram.can_redo || state.busy;
    controls.saveState.textContent = [
      text(diagram.status || "review"), `grade ${text(diagram.grade || "U")}`,
    ].join(" · ");
    renderStructure(diagram); renderOverlay(diagram);
    renderIssues(diagram); renderAlternatives(diagram);
  }

  async function perform(path, body, successMessage) {
    if (state.busy || !state.current) return;
    state.busy = true; showMessage(""); renderCurrent();
    try {
      const request = {
        ...body,
        expected_version: Number(state.current.version),
        expected_digest: text(state.current.digest),
      };
      const payload = await sameOriginFetch(
        path, { method: "POST", body: JSON.stringify(request) },
      );
      replaceCurrent(payload); showMessage(successMessage);
    } catch (error) {
      showMessage(error instanceof Error ? error.message : String(error), true);
    } finally {
      state.busy = false; renderCurrent();
    }
  }

  async function loadSelectedDiagram() {
    const selected = state.diagrams.find((item) => String(item.id) === controls.diagram.value);
    if (!selected) return;
    state.current = selected; renderCurrent();
    try {
      const id = encodeURIComponent(String(selected.id));
      const payload = await sameOriginFetch(`/api/diagrams/${id}`);
      replaceCurrent(payload);
    } catch (error) { showMessage(error instanceof Error ? error.message : String(error), true); }
  }

  controls.diagram.addEventListener("change", loadSelectedDiagram);

  controls.source.addEventListener("load", () => renderOverlay(state.current || {}));
  function selectOverlayNode(nodeId) {
    controls.node.value = text(nodeId);
    renderStructure(state.current || {}); renderOverlay(state.current || {});
  }
  controls.overlay.addEventListener("click", (event) => {
    const node = event.target.closest("[data-node-id]");
    if (!node) return;
    selectOverlayNode(node.dataset.nodeId);
  });
  controls.overlay.addEventListener("keydown", (event) => {
    if (!['Enter', ' '].includes(event.key)) return;
    const node = event.target.closest("[data-node-id]");
    if (!node) return;
    event.preventDefault(); selectOverlayNode(node.dataset.nodeId);
  });
  controls.node.addEventListener("change", () => {
    renderStructure(state.current || {}); renderOverlay(state.current || {});
  });
  controls.edge.addEventListener("change", () => renderStructure(state.current || {}));
  byId("save-editors").addEventListener("click", async () => {
    let sceneIr;
    try { sceneIr = JSON.parse(controls.ir.value); }
    catch (_) { showMessage("Scene IR must be valid JSON.", true); return; }
    await perform(
      route("/edits"),
      { mermaid_code: controls.mermaid.value, scene_ir: sceneIr },
      "Edits saved.",
    );
  });
  controls.undo.addEventListener("click", () => {
    perform(route("/history"), { action: "undo" }, "Undid edit.");
  });
  controls.redo.addEventListener("click", () => {
    perform(route("/history"), { action: "redo" }, "Redid edit.");
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
    const input = byId("command-input"); const command = input.value.trim();
    if (!command) return;
    await perform(route("/commands"), { command }, "Command applied."); input.value = "";
  });
  byId("reconnect-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await perform(
      route("/operations"),
      { operation: {
        operation: "reconnect_edge", edge_id: controls.edge.value,
        source_id: controls.edgeSource.value, target_id: controls.edgeTarget.value,
      } },
      "Edge reconnected.",
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
      route("/decision"), { decision, reason: byId("decision-reason").value.trim() },
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
