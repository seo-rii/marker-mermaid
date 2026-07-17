# MMX-001 v0.3 coverage

This matrix maps the experimental MMX-001 v0.3 specification to the current repository. It is a
statement of implemented behavior, not a claim that all specification-level quality targets have
been achieved.

Status terms:

- **Implemented**: an executable path is covered by automated tests.
- **Partially implemented**: the main path exists, but at least one specified fallback, extraction
  path, quality gate, or corpus requirement remains incomplete.
- **Foundation only**: models, protocols, or bounded seams exist, but the full automated feature
  does not.
- **Planned**: no supported implementation is currently provided.

## Pipeline and publication

| Specification area | Status | Current implementation |
| --- | --- | --- |
| `strict`, `extended`, `maximal` modes | Implemented | Mode-derived type, candidate, and repair budgets |
| Four publication policies | Implemented | Security/render hard gates plus grade and semantic-threshold policy tables |
| Original-image preservation | Implemented | Required in Marker rendering, Markdown, and sidecar output |
| Candidate failure isolation | Implemented | Engine, source, candidate, runtime, and page-detector failures are isolated |
| Direct Mermaid | Implemented | Enabled in extended/maximal and subject to the same hard gates |
| Publication receipts | Implemented | Mermaid, SVG, optional PNG, scores, policy, and status are digest-bound and process-sealed |
| Atomic sidecars | Implemented | Preflight, exclusive publication, manifests, hashes, alternatives, provenance, and affine maps |
| Render-and-compare repair | Foundation only | Deterministic evidence-backed text and limited flow-edge repair; broader visual patching is planned |
| Mermaid AST adapter | Foundation only | Bounded lexical/structural repair and adapter seam; `mermaid-ast` package integration is planned |

## Discovery, views, and evidence

| Specification area | Status | Current implementation |
| --- | --- | --- |
| Figure/Picture/ComplexRegion discovery | Implemented | Marker block and `current_children` traversal with source deduplication |
| Full-page candidates | Implemented | Coverage classification and original/full-page source output |
| Missed-diagram detector | Implemented | Bounded edge/component proposals, occupied-region exclusion, anchor or sidecar queue |
| Composite split | Implemented | Whitespace/separator proposals, raw crops, and virtual-source output |
| Adjacent and multi-page merge | Implemented | Caption/continued signals, bounded canvas assembly, first-fragment anchoring |
| Type-aware visual priors | Implemented | Edge, Hough, arrow, OCR, contour, threshold, grayscale, color, vector, and source-resolution tiles |
| Scene IR and provenance | Implemented | Pydantic integrity, bounded references, collision-safe generated-node attribution, and sidecars |
| Aggregate evidence budgets | Implemented | Hook-free snapshots; atomic item, reference, character, and whole-new-ID limits at every sink |
| Vector primitive extraction | Implemented | Duck-typed/PyMuPDF inputs, global raw-work budgets, bounded placement index, affine mapping |
| Geometry extraction | Implemented | Conservative contours, lines, endpoints, arrowheads, and unresolved relations |
| Evidence fusion | Implemented | Explicit-source precedence, atomic unions, conflict tracking, and limited flow-node ID reconciliation |
| Visual-entailment model score | Foundation only | Provenance-coverage proxy and publication gate; a dedicated model scorer is planned |

Detailed invariants and exact resource limits are documented in
[Architecture](architecture.md), [Vector extraction and fusion](vector-fusion.md),
[Visual priors](visual-priors.md), and [Security](security.md).

## Extraction and serialization

| Family or type | Status | Emission strategy |
| --- | --- | --- |
| Flowchart / Generic Network | Implemented | Typed Flowchart with validated flat/disjoint subgraphs and Scene round-trip |
| Sequence | Implemented | Native Sequence with shared participant/message/source-canvas plan |
| State | Implemented | Native State with terminal-text normalization and pseudo-state handling |
| Class | Implemented | Native Class with typed members and relations |
| ER | Implemented | Native ER with entity, attribute, role, cardinality, and terminal-text plan |
| Requirement / Block | Implemented | Native typed serializers |
| Architecture | Implemented | Native `architecture-beta`, then same-slot nested Flowchart fallback |
| C4 / Deployment / Component | Implemented | Architecture projection with preserved typed data, then Flowchart fallback |
| BPMN / Swimlane | Implemented | Portable Flowchart subgraph fallback |
| Use-case | Implemented | Explicit Flowchart projection with distinct actor/use-case shapes |
| Mindmap | Implemented | Native bounded recursive Mindmap with generated containment Scene |
| Timeline | Implemented | Native title/period/event plan and pinned runtime fixture |
| Gantt | Implemented | Native task/date/dependency plan with conservative date grammar |
| Journey / Kanban / GitGraph | Implemented | Strict extraction; native or planning projection with same-slot fallback |
| Pie / XY / Quadrant | Partially implemented | Native numeric plans with same-slot exact-value Flowchart fallback |
| Sankey / Radar / Treemap / Venn | Partially implemented | Native plans, evidence-bound numeric/set semantics, and same-slot Flowchart fallbacks |
| Packet / Ishikawa / TreeView | Implemented | Strict extraction, native/shared plans, and portable fallbacks |
| Wardley / Cynefin | Implemented | Experimental native plans and explicit loss-disclosed Flowchart fallbacks |
| Event Modeling / ZenUML | Implemented | Strict extraction with Flowchart/Sequence fallback |
| Organization / Data Lineage | Implemented | Recursive hierarchy or dataset/process contracts with TreeView/Flowchart fallback |
| Railroad | Implemented | Bounded recursive grammar AST and native Scene/OCR plan |

The chart family remains **Partially implemented** because the specification's structured-table
plus-description fallback for unreadable or missing numeric values is not complete. The current
implementation does not invent numbers: candidates with missing, ambiguous, reused, or
geometrically inconsistent numeric evidence are routed to review or sidecars.

Structured Marker VLM extraction is implemented for the supported roots above. It uses isolated
source snapshots, type-specific nested schemas, exact JSON scalars, bounded depth/items/text,
request/view budgets, reconstruction-global evidence budgets, and prompt-selected prior-evidence
authority. The generic `ir` envelope still lacks a fully discriminated extraction schema.

See [Typed extraction](typed-extraction.md), [Serializer contracts](serialization.md),
[Charts](charts.md), and [Specialized diagrams](specialized-diagrams.md) for per-record semantics,
terminal grammar, runtime fallbacks, and provenance gates.

## Accessibility, quality, and repair

| Specification area | Status | Current implementation |
| --- | --- | --- |
| Accessible title/description | Implemented | Requested-type derivation, emitted-grammar support checks, explicit metadata provenance gates |
| OCR recall | Implemented | Bounded occurrence multisets projected from terminal-visible serializer plans |
| Numeric consistency | Implemented | Global occurrence checks plus record-local evidence binding for supported numeric diagrams |
| Edge agreement | Implemented | Aligned topology F1 with edge-map IoU fallback |
| Arrow agreement | Implemented | Explicit-arrow comparison; unavailable without source direction evidence |
| Layout similarity | Implemented | Relative node placement when both Scenes expose usable geometry |
| Path consistency | Implemented | Root-to-terminal path comparison for supported directed structures |
| Style recovery | Foundation only | Trusted vector-backed Flowchart node/group/edge style and bold; raster lanes and chart series are planned |
| Semantic repair | Partially implemented | Trusted label repair and limited conflict-free flow edges; topology inference and layout repair are planned |

Raw explicit accessibility metadata is checked before enrichment and serialization. Non-null
metadata must be an exact bounded string with valid UTF-8 and no prohibited control/format/line
separator characters. Exact empty strings retain documented accepted-as-omitted compatibility;
derived defaults and experimental notices do not masquerade as source-proven metadata. Chart and
fallback terminal plans separately decide which titles, descriptions, labels, values, and
percentages are actually visible and therefore eligible for OCR scoring.

## Review workspace

| Feature | Status | Current implementation |
| --- | --- | --- |
| Source/render/difference views | Implemented | Source-sized overlays, bounded difference blend, and candidate comparison |
| Code, IR, and provenance revisions | Implemented | Strict schemas, content-addressed provenance, immutable versions, undo/redo/rollback |
| Candidate approval/rejection | Implemented | Guarded drafts, optimistic locking, audit history, and active revision timeline |
| Natural-language patch | Implemented | Bounded explicit-ID commands with quality invalidation and structured history |
| Structured edit operations | Implemented | Relabel, node add/delete, evidence-backed edge add/delete/reconnect/relabel, group add/delete |
| Drag-and-drop layout | Implemented | Advisory normalized layout hints separate from source bounding boxes |

Natural-language patches do not bypass deterministic validation. Every stored revision must pass
the same source security, Mermaid parse/render, SVG inspection, and integrity checks as an
ordinary candidate.

## Release evaluation

| Specification area | Status | Current implementation |
| --- | --- | --- |
| Fixed manifest and artifact hashes | Implemented | Hash-bound JSON contract and deterministic report inputs |
| Hard and fixture gates | Implemented | Parse/render/original preservation, type fixtures, and quality summaries |
| Reference-free metrics | Implemented | OCR, numeric, provenance, topology, arrow, layout, and path availability |
| Production-scale corpus runner | Foundation only | Local aggregation exists; isolated trusted runner and required corpus scale remain planned |
| MMX-001 quality targets | Not yet demonstrated | The evaluator exists, but this repository does not ship the full required real-world corpus |

## Release interpretation

Version `0.1.0` is an experimental engineering baseline with Phase 1–5 serializer coverage. It is
not a production `extended` release that has satisfied every MMX-001 end-to-end functional and
quality gate. Remaining work includes the discriminated generic-IR extraction schema, adapters for
some experimental grammars, structured non-Mermaid fallbacks for unreadable charts, and measured
results on the specification-scale scientific, enterprise, specialized, multilingual, negative,
and hand-drawn corpora.

The repository does test the boundaries already relied upon for automatic output: source-image
preservation, security/parse/render hard gates, failure isolation, bounded resources, sidecar
integrity, provenance, and reviewability. The fixed evaluator can judge precision/recall targets
and minimum fixture counts once an appropriately licensed corpus and trusted runner are supplied.
