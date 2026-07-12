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
- Page-level missed-diagram proposals with occupied-region exclusion and virtual source crops.
- Profile-gated flowchart fill, border, and link style recovery with strict CSS allowlists.

### Changed

- Candidate budgets are distributed round-robin across successful engines.
- Visual priors are refreshed as earlier engines add structural evidence.
- Unlabeled geometry-only reconstructions are retained for review but cannot auto-publish.
- Marker OCR provenance now uses the exact, unexpanded block crop transform.
- Output saving now preflights source/image/artifact collisions and strict metadata JSON.
- Publication now requires an independent semantic threshold and generated-node attribution gate.
- Numeric diagrams with missing or sub-threshold source agreement are held for review.
- Review static serving rejects symlinks and DNS-rebinding Host values; render artifacts are bounded.
- Undo/redo transactionally removes optional artifacts absent from the target revision.
- Engine observations, typed IR, and per-engine serialization now have explicit resource budgets.
- Page proposals retain bounded crops instead of one full-page copy per proposal.
- Unanchored page proposals now flow through PageGroup metadata into sidecar output.
- Declared portable fallbacks are revalidated after supported native runtime failures.
- Marker now supplies real PyMuPDF page providers to vector extraction when available.
- Review edits validate full Scene IR integrity and invalidate stale automated quality metadata.
- Failed bundles without `final.mmd` can be repaired from retained alternatives.
- Natural-language review patches retain their structured operation, target, and delta history.
- Chromium worker responses use a nonblocking bounded protocol with partial-line deadlines.
- Review HTTP processing has a fixed in-flight worker budget and explicit busy responses.
- The Marker rendered-preview option now emits validated PNG previews when requested.
- SVG CSS inspection covers `<style>` text and edge styles require fully mapped Mermaid ordering.

## 0.1.0

- Initial MMX-001 Phase 1 engineering baseline.
