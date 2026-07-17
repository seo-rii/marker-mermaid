# Evidence-backed semantic repair

The default Marker pipeline attempts deterministic semantic repair only for labels and clearly
directed relations in typed Flowchart/Generic Network candidates. This is not free-form LLM
self-correction; it is a narrow enrichment supported simultaneously by source evidence and the
shared quality evaluation.

A correction must satisfy every condition below:

- The candidate is a Flowchart or Generic Network generated from typed IR.
- The source Scene and typed node have the same exact ID.
- The source Scene label is non-empty and actually differs from the current typed label.
- `vector_text` referenced by the source element, or `ocr_token` text with a score of at least 0.8,
  exactly matches the source label after NFKC/casefold normalization.
- The text evidence came from initial Marker OCR or the exact built-in `VectorPrimitiveEngine`, and
  its ID does not collide.
- The center of the evidence bbox lies inside the source-node bbox and shares a current source
  block ID. OCR/vector evidence newly declared by a VLM has no label-repair authority.

Every qualifying label is corrected in one operation; node IDs and node count do not change.
Supporting evidence IDs are added to the typed node, and the accessible description is recomputed
only when it was generated text. Explicitly authored accessibility text is retained.

A conditional-relation label is added to or corrected on an **existing edge** only when all parts
of the following dual gate pass:

- The typed edge label is empty, or it is a likely typo with NFKC/casefold string similarity of at
  least 0.60 to the source label. A present label with different semantics is not overwritten
  automatically.
- A conditional/branch/decision/gateway relation in the source Scene and the typed-IR edge have the
  same exact source and target, and each unordered endpoint pair has exactly one source relation and
  one typed edge. The typed edge must already be a one-way edge in the same direction as the source.
- Exactly one built-in `GeometryEngine` relation, with neither an ID collision nor an inter-engine
  direction conflict, supports the same endpoints and direction. Trusted `line_segment` and
  `arrowhead` evidence with scores of at least 0.6 must be attached to the relation and share the
  same current-source block.
- Trusted `vector_text` that exactly matches the source-relation label after NFKC/casefold
  normalization, or trusted `ocr_token` with a score of at least 0.8, must be attached directly to
  the relation. Text evidence must be in the same block as the connector and may not share an
  evidence ID with another source relation. The center of a positive-area text bbox must not lie
  inside a node and must be close to both the source polyline and the expanded trusted-line bbox,
  within twice the bbox's shorter side. If the same center also falls inside the two corridors of
  another trusted connector, the repair is rejected instead of guessing which edge owns the label.

This repair updates only the typed edge's `label` and the associated text/connector evidence
attribution. It does not change nodes, endpoints, direction, relation count, or the source Scene.
The before/after labels and both evidence sets are recorded in repair history. When an existing edge
is reversed, parallel, or bidirectional, the system does not attempt to infer a label and direction
in one operation.

Reversing an edge or adding a missing edge must additionally satisfy every condition below:

- The source relation connects distinct exact node IDs, has confidence of at least 0.6, and is
  one-way.
- The endpoints and connector-evidence set of a directed relation produced by the built-in
  `GeometryEngine` match the source relation. Collision-free `line_segment` and `arrowhead` evidence
  must each have a bbox and a score of at least 0.6. This floor lets the default Hough-line 0.6 and
  arrowhead 0.65 signals participate in the path while keeping engine identity and relation
  geometry as separate hard gates. Connector evidence newly declared by a VLM has no repair
  authority.
- Both evidence records share the same current-source Marker block ID.
- The same unordered endpoint pair has no conflicting or parallel source relation.
- Engine observations before fusion have no direction/arrow-state conflict, and there is exactly
  one trusted Geometry pair.
- Reversal is performed only when typed IR contains exactly one unlabeled edge in the opposite
  direction and that edge is not bidirectional.
- A missing edge is added only when neither direction exists and the source-relation label is empty.

Reversal preserves the existing style and all other edge metadata and adds the connector evidence
IDs. A missing edge copies only the source relation and semantic type and uses the deterministic ID
`repair_edge_N`. The system does not automatically repair a missing labeled branch,
conditional/decision/gateway topology, a new outgoing edge from a decision/gateway/diamond source
node, an ambiguous or parallel relation, a self-loop, a dangling endpoint, or malformed IR.
Candidates that have already adopted style recovery are excluded from default semantic repair
because reserialization could discard the style.

## Acceptance gate

A repair proposal returns both code and updated typed IR. The pipeline first validates the typed IR
against the same depth/item/text budgets as the input, serializes it with the deterministic
serializer, and verifies that the result is byte-for-byte identical to the proposed code and
retains the emitted type. It then runs the repaired code through security scanning, parsing, and
rendering again. OCR, numeric, provenance, edge, arrow, layout, path, and type-fitness metrics are
all recomputed with the same evaluation functions used for the initial candidate. A repair is
accepted only when every condition below holds:

- The old and new aggregates are both available.
- The aggregate improves strictly by more than epsilon.
- The non-runtime semantic score does not decrease.
- The existing numeric/provenance publication gates still pass.

If a Packet proposal enters this evaluation path, field-local numeric association is recomputed
from the new typed IR regardless of whether the terminal is native or a fallback. Only OCR/vector
evidence cited by the field within candidate publication authority and positive-area,
image-bounded field/evidence bboxes are used; the entire evidence bbox must be inside its field.
Source-wide `ocr_texts` cannot establish field binding. An exact label and range produce `1.0`; an
associated but incorrect range or extra numbers produce `0.0` and review. When `start == end`, the
single-bit field requires one occurrence of the endpoint number. Duplicate OCR/vector observations
with the same normalized text and bbox count once, while spatially distinct repetitions are
preserved. Field overlap, broad boxes, shared or co-located ambiguous observations, and inadequate
authority, geometry, or budget make the entire metric unavailable. A proposal therefore cannot
pass merely by changing a field and range while preserving the global numeric multiset.

Consequently, semantic repair alone cannot make a held candidate with `aggregate_score=None`
publishable. The original baseline candidate remains unchanged as an alternative. The repair
candidate records structured corrections, before/after scores, and whether it was accepted.

The default repair currently covers node labels, label-only correction of an existing conditional
edge that passes both text and connector gates, reversed edges with strong line/arrow evidence, and
unlabeled missing edges. Missing nodes, conditional topology, arbitrary endpoint or direction
changes, new branch creation, Yes/No semantic inference, parallel relations, layout, and raw
Mermaid edits are not performed automatically until a broader AST/semantic patch layer can update
code and typed IR together safely. If a typed node ID does not exactly match a fused source-Scene
node ID, the repair is skipped rather than inferring an ID from geometry alone. Remapping node IDs
across engines remains separate fusion work.
