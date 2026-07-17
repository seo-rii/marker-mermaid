# Quality evaluation and availability

To avoid confusing the ability to produce a score with correctness, every metric returns a
`MetricResult` containing `value`, `available`, `warning`, and `evidence_ids`. If required structure
is absent, the metric uses `available=false` rather than inserting zero, and the aggregate
renormalizes only the weights that are actually present.

## Structural metrics

| metric | comparison | example unavailable condition |
| --- | --- | --- |
| `edge_agreement` | edge-multiset F1 between aligned nodes | no source relation or node alignment |
| `arrow_agreement` | multiset F1 of explicit arrow endpoints | no source arrowhead flag |
| `layout_similarity` | left/right and above/below ordering of node pairs | fewer than two alignments or no explicit generated position |
| `path_consistency` | multiset F1 of explicitly directed root-to-terminal simple paths | no root/terminal, a cycle, or path-budget overflow |

Node alignment tries the same ID first, then a collision-free portable emitted-ID alias, and then a
unique NFKC/casefold label. A normalized-ID collision is not force-aligned through an alias. A
collision cluster in which an emitted ID happens to equal another raw source ID receives no raw
exact-ID provenance; it aligns only with independent evidence such as a unique label or evidence
reference. Geometry is not used to align nodes, avoiding a circular layout metric that validates
its own assumption. Edge topology ignores direction; the arrow metric measures direction errors
separately.

Generated-Scene direction for Flowchart/Swimlane/BPMN comes from the connector actually emitted by
the serializer, not a raw arrow hint. Native Architecture ignores the IR direction and therefore
uses `unknown`; only the nested Flowchart retry uses a validated direction or `LR`. Native Sequence
uses the style plan's real operator: `solid` and `dotted` have arrowheads, `cross` has a crosshead,
and `open` and `dotted_open` have no end marker, so they are not all counted as
`arrow_at_end=true`. Native Mindmap rebuilds parent-child branches from the shared preorder plan as
`containment` relations, turns off both markers as the grammar does, and uses `radial`. It does not
copy raw logical IDs, direction, role, or shape into the evaluation Scene; it uses root-circle and
child-rectangle shapes with source-ordered `root`/`node_N` identities.

Typed IR is converted back into the node/edge structure actually emitted by its serializer. Layout
is not inferred when the IR has no explicit bbox. Scene IR portable fallbacks can therefore be
evaluated for deterministic serializer preservation. Raw/Direct Mermaid does not yet have a
general AST-to-Scene conversion, so structural scores can be unavailable. Evaluation Scene
adapters cover Sequence/ZenUML, hierarchy/Organization, planning/Event Modeling,
Packet/Pie/Radar/Treemap/Ishikawa/TreeView, Wardley/Cynefin, Data Lineage, Railroad, and Venn and
retain typed-record evidence IDs.

The Event Modeling generated Scene uses the fallback serializer's namespaced frame/relation IDs,
visible typed/time labels, lane subgraph membership, `LR`, and end arrows. ZenUML shares the
Sequence fallback's namespaced participant/message IDs, alias labels, endpoints, and end arrows.
Neither adapter copies raw bbox, direction, role, shape, style, or bidirectional metadata into the
emitted structure; both use zero geometry so they cannot fabricate a layout score. An unlabeled
Wardley component and unlabeled ZenUML participant display the safe source ID actually shown by the
serializer, not invented `text`.

The Organization generated Scene uses shared-plan logical `treeview_node_*` identities, actual
visible labels, parent-to-child containment, and `LR`. The pipeline gives the validated terminal
grammar to the adapter so native TreeView's marker-less connector and unspecified shape are distinct
from a Flowchart fallback's rectangle and end arrow. Child-record evidence attaches to both the
child element and its containment relation. Raw bbox, group, and style not reproduced by the
terminal are discarded.

The Data Lineage Scene uses `data_lineage_dataset_*` and `data_lineage_process_*` nodes,
cylinder/rectangle shapes, `data_lineage_relation_*` data-flow relations with end arrows, and only
validated `TB`/`BT`/`LR`/`RL` directions. Relation evidence attaches only to its relation. Both
Organization and Data Lineage adapters use zero geometry and an empty group list. Their OCR
projections count visible node/relation labels once per record.

The Railroad Scene uses shared-plan logical `railroad_rule_*` and `railroad_expression_N` elements
plus `railroad_relation_N` containment slots. Rule and leaf-expression evidence attaches to each
element; expression evidence also attaches to the incoming relation for that expression. Native
connectors have no markers, so both arrow flags are false. The Scene uses `LR`, zero geometry, and
no groups. Only terminal, nonterminal, and special expressions have grammar-visible labels;
structural operators are unlabeled, and a nonterminal reference is not counted as a separate
dependency edge. OCR likewise counts rule `native_name =`, leaf labels, and special `? text ?` once
and excludes accessibility/title and operator types from content recall.

Railroad rule text is the normalized safe source name, or the actual `native_name =` after a
scanner/preprocessor-active or native-grammar-reserved source name has been mapped to collision-safe
`rrmapped_N[_suffix]`. Source-active names include `style`/`classDef` substrings. Reserved names
include the case-folded expression-word namespace, `railroad-beta`, and a case-folded lowercase
`title*` prefix. Logical element IDs remain source-based `railroad_rule_*`. Mapping is disclosed by
a warning; raw source names remain in typed IR and normalized names in nonterminal labels. ASCII
angles in terminal/special/title/accessibility display as `〈`/`〉`, every ASCII `#` as `＃`, an
entity-like `&` prefix as `＆`, and NFKC quote/backslash hazards as `″`/`∖`, with warnings. Scene/OCR
share this exact compatibility text without emitted source's zero-width separators, while semantic
originals remain in typed IR. Direct Scene fails closed unless `evidence_ids` is null/omitted or a
string list. Raw source bbox, ID, label, role, shape, and style extras are not promoted into
Scene/OCR structure.

Wardley uses `(x, 1-y)` converted from native coordinates as its only explicit `normalized`
position instead of a raw record bbox. Horizontal/vertical IR `x`/`y` are emitted to native syntax
as `[y, x]`, and Scene values use the same token rounding. Because a `->` Wardley link has no marker
in the actual SVG, it is evaluated as an undirected relation and produces no arrow/path score.

After native rejection, a selected Wardley Flowchart fallback retains the shared plan's
source-ordered emitted IDs and explicit undirected topology but cannot represent coordinates,
axes, or anchors. Its generated Scene therefore uses zero-bbox rectangles, `pixels`, and `LR`.
`relative_layout_similarity` is unavailable because centers coincide. The native title, which is
not visible on the fallback canvas, is omitted from generated OCR labels.

The native Cynefin Scene rebuilds domain, item, transition, and domain-group membership from the
shared plan and adds the fixed domain/practice/response/disorder text always generated by the
runtime as unprovenanced template elements. A `confusion` domain projects only the first three
items plus `+N more`, matching native rendering. With no native placement, geometry is zero and
layout similarity is unavailable. A native result without a provenance contract for the fixed
template requires review regardless of aggregate score.

When terminal runtime type is Flowchart, the Cynefin fallback uses a separate projection. It creates
same-ID conceptual elements and groups only for source-supplied domains and includes every explicit
item and explicit directed transition without abbreviation. It adds neither the fixed template nor
membership relations, and a domain label is not double-counted through both its element and group.
Every bbox is zero and direction is the fallback's actual `LR`, so layout similarity is unavailable
and a warning records the loss of quadrant/Cynefin spatial meaning. If record-local provenance for
domains/items/transitions and the other semantic hard gates are sufficient, this fallback does not
receive the native-only review hold.

When Event Modeling, ZenUML, Wardley, or Cynefin originals use Mermaid 11.16 compatibility glyphs
for grammar/entity-like text, OCR projection uses the text actually visible in SVG. It does not use
the original and hide renderer loss. Grammar-fixed chrome such as Wardley axes or evolution stages
is not treated as general source labels.

The Packet Scene reuses reserved-safe emitted IDs, labels, bboxes, and evidence from the
serializer's field plan and does not invent edges between fields. Bit ranges are validated by the
same plan but remain a separate numeric projection/source gate rather than Scene elements. The
pipeline passes validated terminal grammar into semantic projection. Native Packet OCR alone
includes the normalized canvas title; a disconnected Flowchart fallback excludes the native-only
title. Entity-like title text uses the serializer's visible fullwidth glyphs, while invisible
source-security separators are removed so OCR tokens are not split.

Pie Scene and semantic OCR share the bounded `PiePlan` with serialization. Native output represents
each `pie_slice_N` as a `sector`, puts positive slices at normalized centroids on Mermaid's
percentage-label radius, and leaves zero slices as legend-only zero bboxes. It has no relations or
groups and uses `radial`. Element text is the real legend text including `showData`. Native OCR
counts visible title, every legend, and positive-slice percentages, excluding accessibility
metadata. Flowchart output uses `TB` zero-geometry `label: exact-value` rectangles with no
relation/group or native-only title. Slice evidence stays record-local on both terminals. A
malformed evidence list empties only that slice's provenance. Terminal compatibility glyphs and
source-only separator removal are shared by serializer, OCR, and Scene.

Pie participates in the Extended generated-node provenance gate. Each slice is a real Mermaid
element, so both native and fallback slices remain in the injective-attribution denominator. If
multiple slices claim the same eligible evidence, existing collision revocation applies.
Percentages and titles are not separate generated nodes and yield no provenance credit. Even with
correct slice-local numeric binding, output is not automatic when generated-slice attribution is
below 80% or cannot be computed.

Explicit Pie title/accessibility text is not a separate content node. Its dedicated gate requires
an independent, candidate-authorized exact OCR/vector observation or exact `user_edit` evidence
from initial reconstruction input. An engine-emitted `user_edit` cannot establish this trust.
Slice-owned observations, or the same text+bbox under a different ID, are not independent.
Deterministically derived default accessibility text and the experimental notice require no
separate source attribution.

Radar Scene and semantic OCR share `RadarPlan` with serialization. Native output places axes and
data points in normalized `[0,1]` radial coordinates and creates a `series_curve` association from
each point through the final point back to the first, without marker or label. A series bbox is the
normalized curve envelope, not a source bbox or arbitrary origin. Direction is `radial`, groups are
empty, and source bboxes are not copied to generated positions. Series text is visible only when
`showLegend=true`.

Radar's Flowchart terminal retains a visible title as an isolated zero-geometry node, creates one
zero-geometry group per series, labels it only when `showLegend=true`, emits rectangle
`dimension: exact-value` cells, and has no relations, matching the actual `flowchart TB`. Dimension
and series evidence attaches to axes and series, a bounded union attaches to point/cell projections,
and series evidence attaches to native curve relations. A malformed evidence list empties all
provenance for that record only. Scene/OCR share terminal-visible compatibility glyphs and warnings.
Native data points are derived series geometry rather than independent Mermaid nodes and are
excluded from the generated-node provenance denominator; only directly attributable axes and
series are evaluated injectively. A Flowchart cell projects two source records and may share
dimension/series evidence that passed record-local association, but receives no provenance credit
if no record evidence is known.

Treemap Scene consumes the serializer/OCR DFS-preorder `TreemapPlan`. Native output uses a unique,
bounded source ID or collision-safe `treemap_node_N[_suffix]` as section/leaf identity and models
parent-child relationships as arrowless logical containment. Since actual SVG has no connector path
and uses nested area layout, `reading_direction` is `unknown`. Source bboxes remain in typed
IR/provenance, but both native and fallback generated Scenes use zero bboxes to avoid mistaking
source positions for rendered layout.

Treemap Flowchart output uses the same plan with DFS-preorder `N1..Nn`, rectangles, `TB`,
parent-to-child end arrows, and explicit ` (value: x)` labels. Child-record evidence attaches to
both the element and containment relation. Malformed or oversized `evidence_ids` empties the whole
list for that record, never producing partial provenance. Visible compatibility glyphs for quote
and Flowchart angle/backslash/hash, plus native-title angles, are shared with Scene/OCR and disclosed
through candidate warnings. Scanner-only zero-width separators are not content tokens. Runs of
Unicode whitespace normalize to one ASCII space in terminal and semantic projection. Visible
substitution in resolved accessibility metadata also appears in candidate warnings.

Venn Scene shares `VennPlan` with serialization and semantic OCR. It reuses portable emitted set IDs
and collision-safe intersection IDs. Source bboxes remain only in typed IR/Review provenance;
generated bboxes are zero on both terminals. Native output uses set circles, shapeless
intersections, marker-less logical membership, and `unknown`. Native OCR counts visible title and
area labels, not geometry input values. Flowchart output uses set circles, rounded intersections,
exact value-suffix labels, `intersects` relation labels, end arrows, and `LR`. Set/intersection
evidence attaches to elements, and intersection evidence also attaches to membership relations. A
malformed evidence tuple empties provenance only for its record. Both terminals share compatibility
text and warnings.

Ishikawa and TreeView build containment from exact parent/emitted IDs in the serializer's shared DFS
plan. If the planner rejects a duplicate/normalized collision, missing-ID ambiguity, alias conflict,
cycle, object reuse, or resource overflow, the Scene adapter does not quietly drop colliding nodes
and shrink the attribution denominator; the entire metric becomes unavailable.

The generated Scene for an automatic C4 candidate does not reconstruct diagnostic native C4
macros. It follows the actual publication path—Architecture and, when needed, a nested Flowchart
fallback—by passing C4 elements, boundaries, and relations through the shared bounded Architecture
service/group/edge plan. Collision-safe emitted IDs, boundary membership, visible labels,
endpoints, and arrow semantics are therefore identical across both fallback grammars and OCR
projection. Element bbox/evidence, relation evidence, and boundary bbox remain from their records,
while relation polylines, technology, descriptions, relation labels, native boundary notation, and
other raw metadata not shown by the fallback are not promoted to structure or OCR labels.
`reading_direction` retains the IR value or `unknown` because the generated Scene cannot know in
advance whether runtime will select Architecture or Flowchart.

Malformed or over-reference-budget C4 `evidence_ids` do not block existing Mermaid publication;
they are omitted from generated-Scene attribution for that record. Architecture, Deployment, and
Component use the same record-local omission and shared Architecture projection, preventing scalar
strings from splitting into character evidence IDs and preventing serializer/Scene endpoint or
label identity divergence. Missing/non-string endpoints and falsey non-text labels are checked by
exact type before projection, so stringification cannot create quality-authorizing nodes or edges.
An accepted repair rebuilds the current label description from the raw accessibility snapshot,
keeping semantic-score improvement and SVG metadata on the same revision.

## Combination with existing metrics

- Syntax and render are both publication hard gates and score inputs. CandidateValidator's SVG
  inspection converts a Mermaid render success into render-invalid when any geometry attribute is
  `NaN` or `Infinity`.
- The pipeline seals final source, a non-empty post-security SVG, optional runtime-PNG SHA-256,
  security profile, and emitted/runtime type in a validation receipt. Installing that receipt
  requires a process-local certificate issued only after `CandidateValidator` completes exact
  source/SVG/PNG inspection; setting candidate-valid flags cannot create it. A separate publication
  receipt freezes a freshly recomputed publication policy, status, automatic `review_required`
  routing, and selected-candidate receipt digest. User approval/rejection is recorded in Review
  state/revision/history and does not alter the generation receipt. The Markdown renderer inserts a
  fence only when both receipts and process-private seals match current state, not merely when a
  boolean is true. JSON round trips preserve public digests for audit but not private trust, so a
  deserialized result cannot publish automatically without revalidation.
- A publication receipt's quality digest covers displayed aggregate score and grade, metric map,
  and generation warnings. The sealed Markdown snapshot also freezes serializer stability; an
  `experimental` candidate displays an `Experimental reconstruction` warning even at grade A. The
  pipeline deduplicates selected-candidate warnings and bounds them to 256 items of at most 4,096
  characters before deciding, preventing score or `scores.json` mutation from implying higher
  confidence. Evaluation warnings that explain a publication hold or policy limit, and pinned
  renderer-compatibility warnings, are retained before noisy engine diagnostics, which use only
  the remaining budget. Engine noise therefore cannot evict a required experimental warning from
  best-effort output. Probability values in the digest use exponent-free decimal strings and
  normalize negative zero to `"0"`, so Python and JavaScript verifiers reproduce identical bytes.
- Source/generated Scenes enter semantic scoring only when their current payload, including nested
  records, passes the Pydantic resource contract again. Fusion overflow canonicalizes to the winner
  record fallback. The pipeline's internal-fusion backstop also checks the evidence collection's
  exact-list and 20,000-item contract; if it fails, only the fused candidate is isolated and the
  original engine candidates remain.
- OCR recall is occurrence recall over the NFKC/casefold source-OCR token multiset. Identical text
  at distinct bboxes remains distinct; when context OCR overlaps OCR/vector evidence, the maximum
  count per token is used. Same-text evidence without a bbox cannot prove spatially distinct
  occurrences and is merged. Typed/Scene candidates compare generated-Scene node, relation, and
  group labels, including Gantt task and section semantic labels. Mermaid IDs, schedule fields,
  headers, `accTitle`, and `accDescr` cannot increase recall. A direct candidate without a Scene
  adapter uses quoted labels and a conservative grammar-specific label fallback.
- OCR/vector references and generated semantic labels each have evaluation budgets of 50,000
  observations, 1,000,000 characters, and 100,000 tokens. Exceeding any limit does not produce a
  truncated score; semantic evaluation becomes unavailable, blocking automatic publication. Token
  occurrences remain in `Counter` values instead of being expanded into repeated lists.
  Parse/render-invalid candidates skip expensive semantic work such as structure conversion and
  OCR. Typed-Scene conversion failures are isolated as candidate warnings.
- Structural Scenes do not turn class members or ER attributes into topology nodes. A separate lazy
  typed semantic projection adds only serializer-visible Class fields/methods/parameters/
  cardinalities, ER attribute type/name/key/comment, and Timeline-plan canvas title/period/every
  event label to OCR comparison. Timeline raw ID/role/shape/direction/hidden text, generated source
  sentinels, numeric source entities, and accessibility metadata receive no OCR credit. This
  projection also consumes the generated-label budget, preventing large typed IR from bypassing
  limits.
- Core Scenes use serializer-visible defaults exactly. Block shares collision-safe emitted IDs and
  `[unreadable]`. Ordinary State counts only the label/ID used by serialization; choice/fork/join
  retain topology but do not count invisible source labels. Sequence projects only participant and
  message canvas labels from the shared plan in source order, using `[unreadable]` for unlabeled
  messages. Source-only separators, raw participant/message IDs, hidden `text`, raw
  role/shape/direction, and accessibility `<title>/<desc>` receive no OCR credit. Participants share
  `mmx_sequence_participant_N`; messages use ordered `generated-relation-N` slots across serializer
  and Scene. Object-participant evidence attaches to its element and message evidence to its
  relation. String participants are legacy-compatible inputs without record provenance and can be
  routed to review by the normal Extended provenance gate. Any unknown/null endpoint fails the
  entire Scene closed instead of dropping one message.
- Mindmap counts each preorder terminal canvas label once and shares `root`/`node_N`, root/child
  shape, parent containment, and child record-local evidence between serializer and Scene. Quote,
  Markdown, and numeric-entity compatibility glyphs match actual SVG text. Semantic originals,
  source-only separators, raw ID/role/shape/direction, and accessibility metadata receive no OCR
  credit. Any alias conflict, malformed child, object reuse/cycle, or depth/node/source-budget
  error prevents a partial branch.
- Timeline maps each source record to one collision-free `timeline_event_N` Scene slot, using the
  period as element text and title/period/all event labels in terminal OCR projection. Multiple
  event labels share the source record's bbox/evidence authority but do not create structures for
  raw event IDs or hidden aliases. Any alias collision, duplicate ID, or malformed nested label
  prevents a partial Scene. An unlabeled Gantt task projects as section-local `Task N`, and hidden
  task `text`/ID receives no OCR credit. State normalized IDs and transition endpoints share one
  serializer/Scene plan; malformed or unknown endpoints fail the whole Scene. The `[*]` boundary is
  not a structural relation, but a visible transition label remains in OCR projection.
- Ordinary State labels separate whitespace-normalized semantic, source, and canvas text in the
  plan. ASCII quotes in quoted nodes become `″`; only backslashes consumed at the beginning/end,
  in runs, or before CommonMark escapes in node/transition text become `∖`. Safe middle forms such
  as `C\path` and `A\ B` retain ASCII backslashes. A bounded linear scan works for 50,000-character
  input, fixing only active code span/link/emphasis/strike and entity-like literals as visible
  compatibility glyphs while retaining inactive punctuation. Bare email/`www` autolinks use
  source-only separators and preserve original canvas text after zero-width removal. Accessibility
  directives preserve raw quote/backslash/Markdown/named entities and display only Mermaid 11.16's
  lossy numeric entities and `<` as `＆＃…` and `‹`. Node/transition OCR removes source-only
  separators inserted around State grammar/scanner-active tokens. Raw labels never receive credit
  for glyphs actually deleted or interpreted by the renderer. Whitespace-only and non-whitespace
  control/format/surrogate labels fail before runtime and scoring. Accessibility title/description
  is checked as SVG `<title>`/`<desc>` metadata rather than Scene/OCR content. Hidden
  choice/fork/join labels do not reenter derived accessibility text and are replaced by IDs.
- State `title`/`description`/`acc_title`/`acc_description` passes exact built-in-string,
  raw/normalized-bound, Unicode, and UTF-8 gates before enrichment; exact `""` is omitted. The gate
  is shared by public/direct/initial/repair paths, and the pipeline stores a validated raw snapshot,
  not derived `acc_*`, for both initial and accepted-repair candidates. Compatibility warnings are
  reconciled with the accepted repair's canonical plan. A source ID colliding with State
  lexer/security-reserved terms or the strict remote-icon scanner's `iconify` substring is remapped
  after first reserving the entire normalized-ID set to a collision-free `mmx_state_id_…` without
  the dangerous token. Typed IR/evidence retain source identity; Scene relations and Mermaid
  transitions share emitted endpoints. The real SVG transition-count gate therefore detects silent
  renderer loss in which parse appears successful but a `state`-sourced edge disappears.
- ER structural Scene and semantic OCR share the serializer's record plan. Structure contains only
  emitted-ID entity elements and explicit relationships, never separate attribute nodes/edges.
  Entity/relationship elements and endpoints, relationship Scene slots, identifying status,
  direction, and canvas labels come from the plan and retain record evidence. Reserved ER IDs and
  `iconify` substrings map to collision-safe `mmx_er_id_…` while typed-IR source identity and
  provenance remain. Malformed records, unknown endpoints, missing cardinality/identifying flags,
  or resource overflow make metrics unavailable rather than producing a nodes-only partial Scene.
- ER OCR adds entity canvas labels and each attribute's actual canvas type/name/key/comment plus the
  quoted relationship role in record order. Only source-provided `PK`/`FK`/`UK` keys count.
  Internal IDs, cardinality tokens, connectors, and accessibility metadata cannot increase content
  recall. Source-only separators are removed. Visible compatibility glyphs for quote, percent,
  backslash, backtick, and active Markdown/entity text match Mermaid 11.16 SVG. Accessibility is
  checked separately as SVG `<title>`/`<desc>`. Raw metadata receives exact-string/bounds/
  Unicode/UTF-8 and exact-empty omission checks before enrichment. Accepted repair regenerates
  derived accessibility and reconciles warnings from the current semantic plan. Pinned runtime
  fixtures verify that a multiword role remains one edge label without increasing entity count and
  that canvas/accessibility text and reserved emitted identities match the plan.
- The Gantt record plan freezes title, section, and task semantic/source/canvas text. A separate
  accessibility plan derives descriptions from semantic section/task labels and follows its own
  accessibility grammar canvas rather than reusing task compatibility text. Explicit
  `description`/`acc_description` remains authoritative; an accepted repair rederives a description
  from current labels only when both fields are absent. OCR content contains visible title and
  section/task labels remaining after empty sections are removed, including exact `∶`/`％`/`‹`
  compatibility glyphs for task `:`/`%` and title `<`. Hidden task `text`, internal IDs,
  schedule/status fields, and SVG accessibility metadata receive no canvas recall. Missing or
  exactly empty tasks become section-local `Task N`; sections become `Tasks`. Zero-width separators
  leave normalized canvas/Scene/OCR comparison but can remain in raw SVG DOM text/title/desc.
  Section Scene identities are collision-free, duplicate terminal task IDs are rejected, empty
  sections disappear from Scene/OCR, and an all-empty candidate is rejected before scoring.
  Serialization first validates date/token shapes, ECMAScript timestamp range, resolved `x` ends
  after prior-only `after` chains, and millisecond-exact bounded durations, so renderer-zero-width
  tasks are never scored. Every Gantt SVG `class~=task` rectangle must then have finite positive
  dimensions; mixed-scale rounding that leaves zero width is render-invalid. An `after` target must
  precede the current task in source order, blocking forward/partial resolution and cycles.
  Because no `SceneRelation` is created yet, Gantt dependency-edge/path scores and relation
  provenance can remain unavailable.
- When a requested type emits a fallback, projection follows the actual emitted serializer rather
  than requested grammar. C4 Architecture or nested-Flowchart output counts only shared-plan
  boundary groups and service labels, excluding technology, relation labels, and descriptions.
  Architecture native and nested Flowchart evaluate the same frozen-plan service `label`/`name`
  aliases, group canvas labels, and unlabeled topology. Quote/Markdown/numeric-entity compatibility
  glyphs follow actual SVG; semantic originals and source-only separators receive no OCR credit.
  An unlabeled Architecture group displays the same portable emitted ID in both serializers.
  Service evidence attaches to elements and relation evidence to the corresponding
  `generated-relation-N`, while raw accessibility metadata and hidden relation labels are excluded
  from semantic OCR.
- Deployment and Component omit relation labels preserved only in metadata. A Use-case Flowchart
  relation counts serializer-visible `type` first and `label` as fallback. These three software
  fallback Scenes share record planners with serializers, normalizing missing/colliding IDs,
  `label`/`name` aliases, and endpoints into the emitted namespace. The Use-case planner allocates
  Actor and UseCase identities together and suffixes secondary prefix collisions. Raw
  `text`/`role`/`shape`/style/semantic metadata and relation IDs that serializers ignore are not
  promoted. Node/relation resource overflow is rejected at the same serializer/projection boundary.
- Requirement counts serializer-identical normalized collision-safe IDs, requirement
  type/ID/text/risk/verification, element type/docref, and relation type, excluding accessibility
  metadata and ignored alternate labels. Event Modeling counts lane labels and the fallback's real
  time/frame-type/label combination and relation labels. Wardley counts native title, component,
  and link labels. Native Cynefin counts fixed templates, actual visible items (three plus `+N more`
  for `confusion`), and transition labels; Flowchart counts supplied domain labels once and every
  explicit item/transition label. ZenUML counts only Sequence-fallback participant aliases and
  message labels. Packet counts canvas title before fields only under native terminal. Native Pie
  counts visible title, every legend, and positive percentages, including `showData` values in
  legend text; Pie Flowchart counts exact `label: value` cells and excludes native-only title and
  accessibility metadata. Native Quadrant counts visible title, four axis endpoints, supplied
  quadrant labels, and point labels, excluding coordinates/accessibility; fallback counts title,
  axes, supplied slots, and exact `label · x X, y Y` cells. Native Sankey counts node labels and the
  renderer-visible `max(incoming, outgoing)` total but not individual flow weights; Flowchart counts
  node labels and exact edge-weight labels. Neither Sankey path counts title/description as canvas
  text. Native Radar counts visible title, axes, and `showLegend=true` legends but excludes values,
  bounds, ticks, graticule, and accessibility metadata. Radar Flowchart counts title,
  `showLegend=true` group labels, and `dimension: exact-value` cells.
- Native Treemap counts visible `title`, section/leaf labels, and values computed through d3
  hierarchy's reverse-order binary64 sum and d3 `format(",")`. Flowchart counts preorder exact
  value-suffix labels. `accTitle`/`accDescr` is SVG metadata, not content. Native rendering can hide
  small-cell text with `display:none`, a limitation to consider during rendered review. Native Venn
  counts visible title and set/intersection labels, not area values or marker-less membership;
  Flowchart counts exact value-suffix node labels and visible `intersects` on each membership.
  Accessibility metadata and native-only title are excluded. Internal endpoint IDs, coordinates,
  anchors, and accessibility text are not semantic OCR evidence. Deterministic helpers are shared
  between record planning, serialization, and projection for every type.
- If typed semantic projection raises on malformed data or an adapter defect, the candidate's OCR
  is not replaced by a direct-code fallback. The exception is isolated as a candidate warning and
  aggregate remains unavailable so other candidates and document conversion continue.
- General numeric consistency is precision/recall F1 over source/generated numeric-occurrence
  multisets. Record-local binding rules below override it for Pie, XY, Quadrant, Sankey, Radar,
  Treemap, and Packet. Same normalized text+bbox within bounded evidence is one observation;
  numeric Counters from OCR context and evidence channels merge by maximum occurrence per token,
  preserving spatially distinct repetitions while avoiding duplicate channel reports. Invented
  numbers or occurrence mismatches lower precision/recall. Generated projection excludes Mermaid
  `%%` comments and, only in supported detected grammars, chart metadata `title ...`/`title: ...`,
  `accTitle: ...`, one-line `accDescr: ...`, and block `accDescr { ... }`. Metadata-like Sankey CSV
  labels and weights remain data. Quadrant `quadrant-1` through `quadrant-4` slot indexes are grammar
  tokens, but numbers inside directive labels and point coordinates remain. Statements following
  block metadata on the same line are reevaluated. Exhausting the bounded suffix budget fails
  closed to `0.0`, not a partial score.
- Sankey structural metrics follow validated terminal grammar. Native uses source node identity,
  marker-less `data_flow` topology, fixed `LR`, and unlabeled relations; derived totals are not
  mixed into Scene node text. Flowchart fallback uses shared-planner collision-safe emitted IDs,
  exact weight relation labels, end arrows, and normalized requested direction. Only node/flow
  record evidence contributes to attribution. Unemitted role/shape/style/arrow/semantic hints and
  title/description cannot increase structure or OCR. Malformed/oversized evidence lists are not
  coerced character by character; only that record's provenance is emptied. Relation count/IDs
  share Scene resource validation with the serializer. Above the pinned runtime's 500-edge limit,
  Flowchart projection is unavailable rather than partial.
- Sankey numeric consistency binds each plan flow's exact `value_text` to candidate-authorized
  flow-local OCR/vector observations and also requires exact global source/generated numeric
  occurrences. Flow/evidence bboxes must be positive-area inside the source image; flow bboxes may
  not overlap with positive area; cited evidence must be fully contained by its flow. Evidence-ID
  or normalized-text+bbox reuse across flows, conflicting same-bbox observations, weight swaps,
  invalid/missing geometry or authority, and reference/text/token/spatial-budget overflow make the
  whole metric unavailable or mismatched and require review. Native, same-slot Flowchart, and
  semantic repair recompute the same gate with new typed IR and scoped evidence. Direct/untyped
  Sankey has no owner binding and is review-only.
- Sankey accessibility attribution follows the terminal. Native emits no title/description and is
  exempt. Same-slot Flowchart emits resolved accessibility metadata, so each non-derived title and
  description role needs candidate-authorized, non-data-record, spatially exact OCR/vector text or
  an approved exact initial-input `user_edit`. When `acc_title`/`acc_description` shadows a legacy
  field, hidden legacy text is exempt. Metadata is not a content OCR label. Deterministic defaults
  and experimental notices are exempt. Reuse across node/flow record evidence IDs, normalized
  text+bbox, or metadata roles; same-bbox ambiguity; metadata overlap with node/flow bboxes;
  missing/invalid required data-record geometry; budget exhaustion; and engine-emitted
  self-authorizing `user_edit` make all metadata association unavailable. Numeric tokens from
  selected OCR/vector metadata proof are removed from the flow-weight reference multiset; other
  source numbers remain extra occurrences. Repair proposals recompute the same terminal gate.
- Pie structural metrics follow the `PiePlan` terminal. Native allows at most 12 slices and requires
  zero-or-normal binary64 round trips, a finite positive left-to-right total, at least 1% visibility
  for every positive slice, finite normalized centroids, and exact JavaScript `showData` strings.
  Positive slices are normalized `sector` elements; zero slices are legend-only zero bboxes, with
  no relation/group. Valid input outside these conditions and native runtime rejection are
  independently revalidated in the same candidate slot as at most 256 zero-geometry `TB`
  exact-value cells. Fallback creates no relations. Both terminals share source preflight of 50,000
  UTF-16 code units and 5,000 lines.
- Quadrant structural metrics follow `QuadrantPlan`. Native allows up to 256 zero-or-normal binary64
  points and requires finite, distinct point/text visibility on the pinned 500×500 renderer. It
  evaluates `(x, 1-y)` circles, four axis endpoints, and four quadrant groups, inventing no axis
  lines, connectors, or membership. Native-lossy input and runtime rejection are revalidated in the
  same slot as zero-geometry `TB` title/axis/slot/exact-point cells with no edges/groups. Pairwise
  collision and association each have 100,000 operations per candidate; source is limited to
  50,000 UTF-16 code units and 5,000 lines. Overflow never yields a partial score.
- Radar structural metrics follow `RadarPlan`. Native allows at most 12 series and requires
  zero-or-normal binary64 value/bound round trips, a positive finite effective span, and finite
  renderer radii, evaluating normalized radial points and closed marker-less curves. Flowchart uses
  at most 256 zero-geometry `TB` group/cells and no relations, so it cannot fabricate radial layout
  or edges. Native rejection gets one fresh same-slot fallback validation; fallback overflow makes
  the Scene unavailable. Native provenance evaluates axes/series rather than derived points.
  Flowchart cells share dimension/series evidence through Radar-local owner binding; a cell with no
  known record evidence receives no credit. Both terminals share reserved-safe IDs and the 50,000
  UTF-16-unit/5,000-line preflight.
- Radar numeric consistency binds exact dimension-label records and each series' `label + ordered
  values` record to candidate-authorized OCR/vector evidence, then requires global numeric
  occurrence exactness. Every owner bbox must have positive area within the source image and not
  overlap another; cited evidence must be fully inside its owner. Evidence IDs and normalized
  text+bbox cannot be reused across owners, and contradictory uncited text at the same bbox cannot
  be cherry-picked away. Missing typed plans, invalid geometry/authority, empty owner observations,
  or reference/text/token/100,000-spatial-comparison overflow make the entire metric unavailable;
  a different combined label/value order produces `0.0`. Native, same-slot Flowchart, and semantic
  repair all recompute this gate from new typed IR and the same scoped evidence.
- A Radar visible title and non-derived explicit accessibility title/description require separate
  candidate-authorized spatially exact OCR/vector text, disjoint from record-owned observations,
  or an approved exact `user_edit` from initial reconstruction input. Reusing evidence/normalized
  text+bbox across metadata owners, engine-emitted self-approval, or bounded
  metadata-to-record/matching overflow requires review on both terminals. Deterministically derived
  accessibility defaults and experimental notices are exempt.
- Treemap structural metrics follow the shared preorder plan's fixed terminal. Native uses
  section/leaf identity, arrowless logical containment, and `unknown`; Flowchart uses `N1..Nn`,
  `TB`, rectangles, and end arrows. An incompatible explicit value or binary64/renderer display
  selects exact-value Flowchart, and native runtime rejection gets one same-slot fallback
  validation. Flowchart projection above 500 relations is unavailable, but a hierarchy satisfying
  native resource contracts is not prohibited solely by that fallback limit.
- Treemap numeric publication requires a finite, in-image source bbox and directly cited
  candidate-authorized OCR/vector text for every planned node. A child must be fully but not equally
  contained in its parent. Direct siblings may touch edges but their interiors may not overlap.
  Internal-node text evidence must lie outside direct child areas. Each owner proves its exact
  label; an owner with explicit value proves the fixed-decimal value after its label in reading
  order. Evidence IDs and normalized text+bbox are injective across owners. Same-bbox conflicts,
  equal/crossing hierarchies, invalid geometry, and bounded-work overflow make association
  unavailable. Aggregate reference/text/character/token/spatial budgets are
  20,000/50,000/1,000,000/100,000/100,000. A local-record mismatch empties aggregate association,
  though `numeric_consistency` can retain its global-multiset diagnostic. Automatic publication
  requires both local association and global occurrence exactness. Same-slot fallback and repair
  recompute the binding; Direct/untyped Treemap remains review-only. Source geometry authorizes
  attribution but does not resolve generated Scene zero bboxes or the native small-cell
  `display:none` risk. A native-computed internal total is not source-explicit; if OCR/vector
  separately observes it, the current global occurrence gate conservatively requires review.
- The Treemap metadata gate evaluates only roles emitted by the terminal: native visible title and
  non-derived resolved accessibility title/description, or Flowchart non-derived resolved
  accessibility title/description. Each requires candidate-authorized exact OCR/vector observation
  outside data nodes or an approved initial `user_edit`. An identical native visible/accessibility
  title is merged into one title proof, while title and description remain separate owners even
  when text matches. Shadowed legacy metadata, deterministic defaults, and experimental notices are
  exempt, but a notice-only explicit description override erases structural description and fails
  closed. Node-owned/reused/ambiguous/overlapping evidence, engine-emitted edits, and budget
  exhaustion shared with node association make aggregate unavailable. Numeric occurrences from
  selected OCR/vector metadata proof are removed from global Treemap data reference before exactness
  is combined with local node binding.
- The Treemap raw-metadata gate runs before attribution. Pipeline typed candidates and public
  typed/runtime serializers validate before enrichment; direct typed `serialize_treemap()` validates
  before planning. Except for absent/`None` and omission-compatible exact empty values, each of four
  explicit fields must be an exact built-in string, obey raw/normalized length limits, normalize
  non-empty, encode as UTF-8, and contain no raw `Cc`/`Cf`/`Zl`/`Zp`. Newline/tab normalization,
  zero-width formats, string subclasses, non-text containers/numbers, enormous whitespace, and
  surrogates fail serialization before runtime. Repair serializes, evaluates, and stores one
  canonical IR with exact-empty removed. Raw Direct Mermaid has no typed metadata and remains
  security/parse/render validated but review-only when no typed plan exists.
- Venn structural metrics follow the shared plan's terminal. Native requires positive normal
  binary64-safe areas, a `200:1` visibility gate, no exact containment, and complete explicit pairs
  for each higher-order union; it invents no missing areas or pairs. Native Scene uses marker-less
  logical membership and `unknown`; fallback uses labeled end-arrow membership and `LR`. Runtime
  native rejection gets one same-slot exact-value Flowchart validation. The 500-edge hard cap
  applies only to Flowchart projection; near-limit performance still relies on runtime timeout.
- Venn numeric publication does not rely only on a document-wide occurrence multiset. Every planned
  set/intersection finite positive in-image source bbox must exactly match a candidate-authorized
  cited contour, and a separate cited OCR/vector observation must bind actual label and explicit
  value per record. Label/value swaps, evidence-ID or normalized-text+bbox cross-owner reuse,
  same-bbox conflicts, missing authority/geometry, and bounded-association overflow make aggregate
  unavailable. An intersection without a source label/value does not invent one; with neither, it
  lacks textual owner proof and requires review. Set/intersection bboxes may overlap as Venn
  semantics require, but an intersection must be inclusively contained in every declared set and
  not fully contained in an undeclared set. A higher-order intersection must lie inside every
  explicit strict-subset intersection; equal containment is allowed. All set scans,
  intersection-pair scans, contour comparisons, and text containment share one bounded spatial
  counter. Membership geometry, owner-local cited-observation containment, local content binding,
  and global numeric exactness are reevaluated together for native, same-slot Flowchart, and repair;
  runtime-fallback repair retains canonical serialization for the terminal. Direct/untyped Venn is
  review-only.
- The Venn metadata gate evaluates only terminal-emitted roles. Native requires only an explicit
  visible title. Intrinsic/runtime Flowchart requires non-derived resolved accessibility
  title/description. Native-unsupported accessibility/description fields, fallback legacy fields
  shadowed by effective `acc_*`, deterministic defaults, and pipeline-added notice suffixes are
  exempt; a notice-only explicit description fails closed. Each role requires an independent
  candidate-authorized exact OCR/vector observation outside every data area or an approved exact
  initial `user_edit`. Data/metadata ID or normalized-observation reuse, same-bbox ambiguity, area
  overlap, engine edits, and bounded work shared with data gates make aggregate unavailable. Only
  proven OCR/vector metadata numeric occurrences are removed from global data reference;
  `user_edit` occurrences are not. For identical exact proof, source observation wins over edit so
  results do not depend on ID order. Under `strict`, explicit notice text is not a pipeline suffix
  and must be proven. Equal fallback title/description text still needs separate proof.
- The Venn raw-metadata gate precedes attribution. Pipeline and public typed/runtime fallback
  serializers share validation of four explicit fields before enrichment; the direct chart-set
  serializer validates when planning begins. Except for absent/`None` and omission-compatible exact
  `""`, values must be exact built-in strings, obey raw/normalized bounds, normalize non-empty,
  encode as UTF-8, and contain no raw `Cc`/`Cf`/`Zl`/`Zp`. Newline/tab normalization, zero-width
  formats, string subclasses, non-text containers/numbers, enormous whitespace, and surrogates fail
  before runtime; repair cannot bypass the same public serializer. Repair shares exact-empty-removed
  canonical IR across serialization, evaluation, and storage. Here the direct chart-set serializer
  is the typed `serialize_venn()` API; Raw Direct Mermaid without typed metadata retains its
  security/parse/render gates and review-only hold when no typed plan exists.
- Pie combines slice-local association with global occurrence completeness. Native Pie, same-slot
  exact-value Flowchart, and semantic repair require every typed slice to directly reference
  candidate-authorized `ocr_token` or `vector_text`. Slice/evidence bboxes must have positive area
  inside the source image, slice bboxes must not overlap, and each evidence bbox must be fully inside
  its slice. Source-wide `ocr_texts` cannot assign a label or value to a slice.
- For every slice, cited observations in bbox reading order must bind a punctuation-preserving full
  label plus allowed separators to exactly one value record, and the numeric multiset of numbers in
  the label plus exact value must match. Pie numeric consistency is `1.0` only when both this local
  result and the full source/generated numeric occurrence multisets are exact. Swapped values in
  otherwise associated records or extra source-wide numbers produce `0.0`. A missing label suffix
  or malformed/cited-extra record that prevents full-record binding makes the metric unavailable.
  Both cases require review regardless of threshold. Duplicate same-slice observations with equal
  normalized text+bbox count once; spatially distinct repetitions remain.
- Overlapping slice bboxes, broad/shared evidence, cross-slice reuse of an evidence ID or normalized
  text+bbox, conflicting text at one bbox, invalid authority/geometry/image bounds, or exhausted
  association budget makes the entire Pie metric unavailable/review-only, never a partial
  per-slice score. Direct Pie without typed slice slots cannot prove this binding.
- Packet replaces the global numeric occurrence multiset with field-local association. Native
  Packet, same-slot Flowchart fallback, and semantic repair use the same field plan and evaluation.
  A field binds label and `start`/`end` only when it explicitly cites candidate-authorized
  `ocr_token`/`vector_text`, field/evidence bboxes have positive area inside the source image, and
  the full evidence bbox lies within the field. Source-wide `ocr_texts` authorizes no field label or
  number.
- When all field labels and ranges bind exactly to field-local evidence, Packet numeric consistency
  is `1.0`. A bound label with wrong range or unrelated extra numbers produces `0.0` and review
  regardless of threshold. A single-bit `start == end` field requires one range-endpoint number.
  Duplicate same-field OCR/vector observations with equal normalized text+bbox count once;
  spatially distinct repetitions do not merge.
- Overlapping field bboxes, a broad evidence bbox spanning fields, shared evidence IDs or co-located
  ambiguous observations claimed by multiple fields, or unverifiable authority, bbox, image bounds,
  or association budget makes the entire Packet metric unavailable, not a partial field score.
  Conflicting normalized OCR/vector text at the same candidate-authorized bbox also makes it
  unavailable even if the field cites only the favorable observation. Packet binding replaces the
  global multiset; Pie, XY, Quadrant, and Radar binding is additional to global exactness. Other
  numeric calculations are unchanged.
- Visual-entailment precision is a collision-free evidence-coverage proxy aligning generated nodes
  by source node ID, collision-free portable ID alias, or unique normalized label. Eligible node
  evidence is limited to `ocr_token`, `vector_text`, `contour`, `vlm_observation`, and `user_edit`.
  `source_crop`, `line_segment`, and `arrowhead` yield no node credit even when registered. If two
  generated nodes directly reference or inherit the same eligible evidence ID through source
  alignment, the ID is revoked from every claimant. Duplicate references within one node are one
  claim. A node is supported if any collision-free eligible ID remains. Relation/group references
  do not participate in node-claim collisions, preserving legitimate connector/containment sharing.
  The source Scene itself is never reused as candidate precision. A model scorer is future work.
- If structural edges cannot be evaluated and a rendered PNG exists, raster edge IoU is used as a
  fallback.

Path enumeration stops by default at 10,000 completed paths or 100,000 explored states/stack
entries. A cyclic dead branch that never reaches a terminal still consumes state budget, preventing
simple-path combinatorial growth from bypassing the completed-path cap. If source or generated graph
exceeds path/state/depth budgets, no partial score is produced; the entire metric is unavailable.

A non-runtime semantic score is calculated separately from the displayed total. Syntax/render
participates in the hard gate and total score but cannot dilute a zero semantic score into a
publishable grade. Under `extended`/`maximal`, structural candidates go to review when
collision-free generated-node provenance under the eligible-kind and conservative-revocation rules
is below 80% or unavailable. Packet and Pie are included, so matching bit ranges or slice values do
not automatically publish unattributed fields/slices.

When multiple parse/render-valid candidates exist under `best_effort_validated` or
`strict_validated`, the same aggregate/semantic thresholds and provenance/numeric holds are applied
to each candidate, after which a publishable class is selected first. Within a class, ordering is
aggregate, OCR recall, generation method, then candidate ID. A high-total candidate with fewer
available metrics therefore cannot hide an evidence-rich publishable alternative and force the
whole document to review. Forced-review and sidecar policies do not use this class priority. A
typed/Scene numeric hold retains the semantic type regardless of fallback grammar. A Direct
candidate lacks a typed semantic contract and is judged by the emitted/runtime grammar type proven
by parse/render validation rather than predicted/requested type.

Semantic-repair candidates use the same reference-text sets and evaluation functions as initial
candidates. OCR/vector, provenance, edge, arrow, layout, path, and numeric gates are recomputed from
new typed IR, and acceptance requires both strict aggregate improvement and a non-decreasing
semantic score. Repair cannot arbitrarily release a held aggregate. Reversal and unlabeled
missing-edge proposals require source-relation confidence 0.6, exact endpoint/relation ownership
from the built-in Geometry engine, collision-free line/arrow evidence with bbox and score of at
least 0.6, and same-source-block attribution. This includes the default detector's line 0.6 and
arrow 0.65 range while retaining engine identity and geometry-relation matching as separate gates.
VLM-declared connector evidence, inter-engine direction conflicts including weak evidence,
conflicting/parallel/labeled/conditional relations, and outgoing edges from
decision/gateway/diamond source nodes are excluded from automatic topology repair.

Label repair likewise requires trusted Marker OCR or built-in Vector origin, source block, bbox
containment, and no ID collision. Proposal typed IR must pass the input's resource budgets again and
its code must exactly equal deterministic reserialization before evaluation.

If a Typed/Scene semantic type—or a Direct candidate's validated emitted/runtime type—is
Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn, aggregate is set to `None` and automatic publication
is blocked when no OCR/vector numeric evidence exists or numeric consistency is below publication
threshold. Pie does not fall through to the configured threshold when any of candidate-authorized
slice-local association, global numeric completeness, or explicit accessibility attribution is
unavailable/mismatched.

XY binds candidate-authorized OCR/vector observations to every axis, series, and explicit point in
the typed plan. Cited text within each finite in-image source-record bbox must match an allowed full
form of axis label plus category/bounds, series kind plus ordered values, or point x plus y.
Candidates remain unavailable/mismatched and in review if two records share an evidence ID or
normalized text+bbox, if categories/values/x values are swapped while the global numeric multiset
still matches, if a bbox is invalid/missing, or if budgets overflow. Without explicit metadata no
extra bbox-overlap scan runs; when it does run, spatial evidence-to-record checks are limited to
100,000 per candidate and fail closed on overflow. Explicit `title`/`acc_title` and
`description`/`acc_description` require exact OCR/vector evidence or an initial exact `user_edit`
separate from data-owned observations/bboxes. Engine-emitted edits and Direct-Mermaid-only XY cannot
self-create typed record association. A same-slot downgrade to Flowchart retains semantic type XY
and the same publication gate.

Quadrant similarly binds complete low/high axis records or label/x/y point records to
candidate-authorized OCR/vector evidence and also checks the global numeric multiset. Reused
evidence IDs, normalized text+bbox, or source records, and swapped axes/points/coordinates, are
mismatches even when numeric multisets agree. Bbox geometry must place the x axis horizontally at
the bottom and the y axis vertically at the left, so whole-record swaps and nonstandard axes require
review. A supplied quadrant label requires an independent exact observation in the corresponding
quadrant of the full source canvas, or an exact initial `user_edit` with a valid source-quadrant
bbox. It does not inherit nonexistent slot evidence from axes/points.

Explicit Quadrant metadata is also checked separately from data-owned observations. Direct
Mermaid-only Quadrant, invalid/missing bboxes, engine-emitted edits, or overflow of the shared
100,000-operation budget across all spatial/matching phases remains review-only. Same-slot
Flowchart retains semantic type Quadrant. Source quadrants use the full-crop midpoint rather than a
detected plot bbox, so inset or off-center plots can be false-reviewed; position is not adjusted
automatically until an axis/vector plot bbox exists.

Independent observations for explicit title/description/accessibility metadata currently prove
only content existence because the evidence schema has no immutable target role. Best-effort policy
therefore records a role-attribution limitation warning and publishes only as experimental, while
`strict_validated` makes aggregate unavailable and requires review. A semantic-repair proposal that
adds explicit metadata recomputes the same limitation. Under strict policy, a limited proposal is
not adopted merely for score improvement, while the previously validated candidate's code, IR,
score, and publishability remain intact.

Packet sets aggregate to `None` instead of falling through to a global numeric multiset or configured
threshold when candidate-authorized field-local association is unavailable or `0.0`.

Treemap likewise sets aggregate to `None` unless every typed-plan node binds nested source geometry
and exact label/explicit-value observations. It does not fall through to a configured threshold if
any parent-child containment, sibling-interior non-overlap, internal-node cited-text exclusivity,
injective evidence/observation ownership, or global numeric completeness cannot be verified. Native
runtime fallback and semantic repair retain semantic type Treemap and rerun the same gate. Direct
Mermaid-only Treemap cannot self-create owner binding. If terminal-effective explicit metadata
proof is unavailable, aggregate is `None` even when node/value and global numbers are exact. A
repair that adds or changes metadata recomputes the gate with newly resolved output roles and
scoped evidence.
