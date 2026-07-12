# Changelog

## Unreleased

### Added

- Deterministic composite-panel, fragment-merge, and full-page coverage proposal APIs.
- Page-aware `SourceFragment` and `DiscoveredSource` models for virtual diagram sources.
- Optional OpenCV geometry engine for contour, line, and arrowhead provenance.
- Geometry evidence enrichment before structured VLM extraction.
- Marker adapters for full-page, composite-panel, and adjacent/multi-page virtual sources.
- Deterministic fragment assembly with source/page-to-canvas affine provenance.
- `source-map.json` sidecars and multi-source Markdown rendering.
- Duck-typed PDF vector extraction with page-to-canvas mapping.
- Deterministic vector/geometry/OCR/VLM observation fusion.
- Conservative topology, arrow, relative-layout, and path quality metrics.
- Vector, detected-arrow, and color-cluster visual priors.
- Native State, Class, ER, Requirement, and Block typed serializers.
- Strict-safe C4, Deployment, Component, and Use-case portable fallbacks.
- Requested/emitted/runtime diagram type and fallback-chain metadata.
- Evidence-strict Pie, XY, Quadrant, Sankey, Radar, Treemap, and Venn serializers.
- Numeric multiset precision/recall scoring and no-evidence publication guard.
- Interactive source/render/provenance review workspace with external CSP-safe assets.
- Atomic Mermaid/Scene IR/render revisions, optimistic concurrency, and undo/redo.
- Alternative selection, approval/rejection audit history, and conservative Korean/English patches.
- Same-origin review API with CSRF, body limits, path confinement, and strict revalidation.
- Evidence-strict Journey, Kanban, GitGraph, Packet, Ishikawa, TreeView, and Event Modeling serializers.
- Native Wardley, Cynefin, and Railroad serializers plus explicit ZenUML, organization, and lineage fallbacks.
- Bounded pre-validation source repair with audit events, diagnostics, idempotence, and AST adapter seam.

### Changed

- Candidate budgets are distributed round-robin across successful engines.
- Visual priors are refreshed as earlier engines add structural evidence.
- Unlabeled geometry-only reconstructions are retained for review but cannot auto-publish.
- Marker OCR provenance now uses the exact, unexpanded block crop transform.
- Output saving now preflights source/image/artifact collisions and strict metadata JSON.

## 0.1.0

- Initial MMX-001 Phase 1 engineering baseline.
