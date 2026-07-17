# Vector extraction and fusion

## VectorPrimitiveEngine

To avoid coupling to one PDF provider implementation, the engine uses duck typing over:

- `get_drawings()` and `get_text("dict" | "words")`
- `vector_primitives` and `vector_texts`
- Block `page`, `document_page`, and `page_ref`

Only closed rectangles, ellipses, and polygons become nodes. An open line/path becomes a
relation only when its endpoints uniquely touch different nodes; direction comes only from an
explicit provider arrow flag. Vector text is attached as a label only when its center lies in
exactly one node. Scene IR preserves fill/stroke color and line style.

PyMuPDF's integer bold span flag `16` is retained on `vector_text` evidence. Node bold is restored
only when every contained span is bold; mixed or partial weight is omitted with a warning. A
weight conflict for the same text and bounding box omits emphasis without duplicating the label.
Combined labels, fonts, and provenance are revalidated as new `SceneElement` records. Including
the contour, one record may reference at most 256 evidence IDs. If combined text or references
overflow, the engine does not truncate IDs: it omits the entire text enrichment while preserving
the contour-only node, individual `vector_text` evidence, and an explicit warning for later
engines.

### Reconstruction-global resource budgets

Vector budgets are shared across every vector source in one reconstruction; they do not reset by
provider, page, or fragment.

| Resource | Default | Additional constraint |
| --- | ---: | --- |
| Raw primitive/command records | 2,048 | Configured maximum 5,000, matching the `SceneElement` cap |
| Raw vector-text records | 5,000 | Configured primitive + text maxima total at most 20,000 |
| Vector-text characters | 8,000,000 | Cannot exceed the reconstruction evidence-character cap |
| Provenance-reference fan-out | 20,000 | Sum of logical source-block references in planned evidence |
| Provenance-character fan-out | 8,000,000 | Python string lengths, including repeated copies per evidence record |
| Vector sources | 256 | Only a bounded prefix in source order is inspected |
| Total retained points | 100,000 | Polygon and polyline geometry across all sources |
| Vector metadata token | 256 characters | Kind, command, color, style, coordinate space, and similar fields |
| Warnings | 256 | Overflow/noncanonical warnings normalize to one terminal warning |

These limits charge raw provider work, not only nodes/spans that survive. Malformed records,
records mapped outside a crop, deduplicated records, and empty nested drawing containers consume
their respective budgets. Otherwise an input that never produces a valid Scene could force
unbounded traversal of later sources.

Provenance fan-out is a separate preflight after valid-record selection and deduplication. Before
allocating any Scene or evidence record, the engine multiplies canonical source-block IDs by the
planned shape/text/open-line `VisualEvidence` records. It counts both reference occurrences and
each repeated ID's Python string length. Exact boundaries pass. If either 20,000 references or
8,000,000 characters is exceeded, the complete vector result is isolated atomically as an unknown
prediction with `scene_ir=None`, empty evidence, and one budget warning. No partial provenance
prefix can gain evaluation or publication authority. The pipeline converts this payload-free
warning observation into a bounded generation failure recorded in results and the sidecar
manifest, while sibling engines may continue.

Source collections, raw-record iterables, and PyMuPDF drawing `items` stream on demand with at
most one lookahead to detect overflow. When primitive count, text count, or text characters close,
that dimension never reopens for a later source; reading continues only while another relevant
dimension has capacity. Polygon/closed-shape records allow at most 256 points and open polylines
512. An overflowing record is omitted whole, never partially reconstructed. The reconstruction
retains at most 100,000 points, but point-free records such as rectangles may still use remaining
primitive capacity after that point budget closes.

Computation within count limits is also bounded. Exact-key hashing runs before at most 250,000
approximate bounding-box deduplication comparisons. Text-to-node ownership and connector-endpoint
ownership each allow at most 1,000,000 comparisons; afterward labels stay unassigned or connectors
unresolved and a warning is recorded. Non-label metadata tokens such as kind, command, color, and
style are at most 256 characters and never invoke arbitrary string coercion. Direct text
attributes and duck-typed spans from `get_text("dict"/"words")` are read once into plain records,
then their exact-string lengths are charged before parsing. Huge integers in coordinates,
confidence, canvas, tolerance, or source IDs are rejected before float/decimal conversion.

Built-in work metadata is itself outside the trust boundary. `VectorPrimitiveEngine` re-bounds a
custom extractor observation and clamps reported work to at least retained counts and no more than
remaining limits. A direct `VectorObservation.to_engine_observation()` call repeats the same
primitive, text, character, point, warning, and aggregate-provenance preflight. Built-in, direct,
and custom extraction therefore share one final boundary. Detailed budgets are engine-constructor
and integration tuning points, not public Marker JSON settings; aggregate provenance is an
internal policy that adds no public configuration/API.

These controls limit consumption and normalization after a provider returns. Provider properties
and callables, custom extractor execution, and PyMuPDF's internal `get_text()`/`get_drawings()`
materialization are not isolated in a separate process and remain a trusted-local-integration
boundary.

Panel and merged sources use the same assembly `page_to_canvas` affine as `source-map.json`.
Before iterating sources, built-in `observe()` constructs one reconstruction-local bounded
placement index. It retains exact-dictionary placement references under all/page/block/page+block
candidate keys, allowing each source to select a unique placement with O(1) page/block lookup.
No transform is parsed during index construction. Only the selected placement's affine/bounding
box is parsed lazily, and the result is shared by that source's `page`, `document_page`, and
`page_ref` providers. Thus at most 256 placements and 256 sources do not cause repeated scans of
placement or source-ID lists. Standalone `extract_vector_observation()` builds the same one-call
index when page-coordinate mapping is needed; custom extraction does not build the built-in index.

Placement input must be an exact built-in list/tuple with no more than 256 entries. A 257th
lookahead invalidates the entire index. Each placement accepts at most 256 `source_block_ids`; a
257th removes all block/page+block keys for that placement atomically, although it remains in
all/page ambiguity. No ID prefix gains authority. Transform validity does not filter index
candidates, preventing a malformed placement from being removed early and creating false
uniqueness. Only after a unique match is chosen are affine/bounding-box values parsed. Zero or
multiple matches, or an invalid selected affine, produce a bounding-box-fallback warning.

Index keys accept only exact bounded strings. Exact bounded integer source identities become
decimal strings. Marker 1.10.2 `BlockId` values are reconstructed from
`page_id`/`block_type.name`/`block_id` without arbitrary `str()` calls; subclass hash, equality,
and coercion hooks never run. Invalid, empty, surrogate-containing, or longer-than-256-character
placement IDs are excluded, and duplicates within one placement register once. When a source
provides an exact page ID, only placements from that page are eligible even if a block ID matches
elsewhere. An explicitly noncanonical page identity fails closed despite an otherwise unique
placement. The index is internal to built-in integration. Cubic PyMuPDF curves are not guessed to
be ellipses, and raster lines are never treated as vectors.

## FusionEngine

Every engine declares `fusion_source`; origin is never guessed from evidence-ID text.

| Field | Precedence |
| --- | --- |
| Node/edge geometry | vector → geometry → other → VLM → OCR |
| Node label | vector text → OCR consensus → other → VLM |
| Font weight | Keep one consensus value; omit on bold/normal conflict |
| Semantic relation | VLM → other → vector → geometry → OCR |
| Type distribution | Deterministic weighted aggregation by source |

### Aggregate evidence ingress/output budget

Before serializing observation projections or deep-copying evidence winners, fusion freezes a
single cumulative snapshot of every `observation.evidence` record and every sorting-key
`FusionInput.prior_evidence` record. Duplicate-inclusive `VisualEvidence.source_block_ids` are
limited to 20,000 occurrences and 8,000,000 Python characters. An independent 8,000,000-character
full-evidence budget includes `id`, `kind`, `text`, and `font_weight`. Exact boundaries pass; one
extra unit rejects the complete fusion call before `_fuse_evidence` or live `model_copy`. Fused
evidence is detached and checked again under a new budget before constructing
`EngineObservation`.

The shared snapshot reads exact lists/model fields through built-in access, separates nested
source-block lists from scalar data, and invokes no live `model_dump`, iteration, equality, or
coercion hooks. The pipeline applies the same constants at initial/custom-engine collection,
reconstruction-global new-ID admission, final results, publication/Markdown, sidecars, and output.
Vector prospective fan-out is an earlier optimization but shares these limits. Marker OCR and
Review provenance read/replacement/structured-add paths also use the boundary. Evaluation applies
the source-block aggregate limit through raw-record snapshots while preserving its explicit
100,000-record/64 MiB artifact contract. No public config or sidecar schema/manifest version
changes.

Scene nodes cluster by identical ID or normalized-bounding-box IoU. Relation endpoints and group
members remap to fused Scene IDs, and provenance/source-block IDs merge. Competing values are
resolved by precedence with warnings. Typed and direct candidates deduplicate on canonical
JSON/code. Generic Scene clustering alone never authorizes typed-IR ID changes.

An element/relation `evidence_ids` union and a `VisualEvidence.source_block_ids` union are applied
only within the per-record limit of 256. Overflow does not retain a bounded prefix or delete the
record; every engine input for that evidence ID is discarded together, the entire cross-input
enrichment is omitted, and the precedence winner remains with a warning. Endpoint remapping and
direction-conflict tracking still apply. Every modified record is rebuilt through Pydantic, and
the pipeline rechecks fused Scene/evidence, exact-list/20,000-item collections, aggregate
source-block references/characters, and full-evidence characters before generation. Exactly 256
therefore merges without loss, while a 257th cannot create different scoring, publication, and
sidecar contracts.

### Flow-node ID reconciliation

Typed-ID reconciliation currently supports only flat `flowchart` and `generic_network` flows.
The owner's `nodes[].id` must exactly reuse an ID from the same response's
`scene_ir.elements[].id`. That Scene element must uniquely correspond at IoU 0.45 or higher to a
fused node cluster from a separate, explicitly `vector` or `geometry` input. Both sides need
nonempty, noncolliding provenance. Label agreement, a VLM-only bounding box, or a self-declared
evidence-kind string is not authority.

Source evidence must have existed in pipeline context before the semantic-engine call, with a
noncolliding ID and matching payload. For Marker Structured VLM, it must also be among the private
IDs actually selected into the bounded prompt, and the owner Scene element and typed node must
share at least one such ID. Evidence centers must fall inside the node; OCR/vector text must equal
or contain the node text after NFKC, case-fold, and whitespace normalization. Authority accepts
only a `contour` produced directly by that vector/geometry observation, with its bounding box at
least IoU 0.45 against the authority node. A later duplicate declaration by another owner revokes
mapping authority. A VLM therefore cannot cite a predictable omitted ID, create an evidence record
and ID in its own response as if it were prior evidence, or substitute a geometry reference. The
fused typed candidate adds only independently certified source/authority mapping IDs to the
selected owner's closed publication-evidence set.

Legacy `FusionInput.publication_evidence_ids=None` retains unrestricted legacy semantics; an
explicit empty set closes authority completely on both source-prior and authority-contour sides.
Mapping uses only records that pass both inputs' boundaries. If identical direct Mermaid code
deduplicates across inputs, only the confidence/source-precedence winner's publication authority
is retained; authorities are never unioned, and an explicit empty set remains empty.

A pixel Scene's self-declared `canvas_size` is trusted for mapping only when exactly equal to the
current reconstruction source dimensions. Shared evidence block IDs must intersect the current
trusted source blocks. Fake small canvases or matching fake block IDs cannot make distant boxes
appear aligned. Because evidence-coordinate metadata is not yet preserved separately, only a
trusted pixel Scene can use this authorization path; normalized Scenes may participate in generic
fusion but not typed-ID mapping.

Only when every node passes and targets are distinct does one atomic operation rewrite:

- `nodes[].id`
- `edges[].source` and `edges[].target`
- `groups[].member_ids`

No other string or nested reference is replaced recursively. Duplicate/missing IDs, dangling
endpoints or members, ambiguous IoU, collisions, many-to-one targets, or partial coverage leave
the complete candidate unchanged. A candidate can never mix original and fused ID spaces. Only a
successful mapping writes the `node-id-map.json` audit record. When mapping-backed and unmapped
candidates of the same type share canonical IR/code, the mapping-backed candidate is budgeted
first, preserving its audit sidecar even at lower confidence.

Later automatic semantic repair cannot add, remove, or replace nodes in a mapped set; such a
proposal conflicts with the audit and typed IR and is rejected before validation. Direction
conflicts are transferred from owner IDs to fused endpoint pairs before rewriting, so independent
opposite directions remain conflicted and ineligible for repair. ID changes never hide or resolve
disagreement.

Nested flow containers such as Swimlane/BPMN, other typed diagrams, direct Mermaid, and generic
Scene fallback are outside reconciliation and remain unchanged. Bold emission likewise does not
trust only the fused Scene value: it rechecks actual vector origin, noncolliding provenance, and
evidence text/bounding-box mapping to the generated node. VLM or fixture-supplied `font_weight`
and self-declared `vector_text` cannot become automatic style.

Fusion itself is failure-isolated. The fused observation forms the first candidate group, while
raw engine observations remain available under code-hash deduplication and round-robin budgeting.
Independent candidates therefore remain reviewable if fusion fails or merges too aggressively.
