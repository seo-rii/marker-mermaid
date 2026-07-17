# Typed serialization and fallback contract

`SerializationResult` records more than Mermaid source:

- `requested_type`: the semantic type requested by the classifier or typed IR
- `emitted_type`: the Mermaid grammar actually emitted by the serializer
- `fallback_chain`: the complete route from requested type to emitted type
- `warnings`: representation loss, compatibility behavior, and parser/runtime limitations
- `stability`: `stable`, `extended`, or `experimental`

A native result has a one-item fallback chain. A fallback chain must start with the requested type,
end with the emitted type, and include at least one warning. Dispatch rejects cycles, duplicate chain
entries, empty code, and serializers that report an inconsistent result. `SerializationRegistry`
wraps legacy string serializers in the same contract.

## Requested and emitted types

| Requested type | Emitted grammar | Contract and fallback |
| --- | --- | --- |
| Flowchart | `flowchart` | Phase 1 native; validated flat/disjoint groups may become subgraphs |
| Gantt | `gantt` | Phase 1 native with a strict schedule and terminal-text plan |
| Mindmap | `mindmap` | Phase 1 native with a bounded recursive terminal/emitted-ID plan |
| Sequence | `sequence` | Phase 1 native with a shared terminal/emitted-ID plan |
| Timeline | `timeline` | Phase 1 native with a shared title/period/event terminal plan |
| Architecture | `architecture → flowchart` | Prefer `architecture-beta`; retry once as a nested Flowchart when the runtime rejects it |
| State | `state` | Native; node and relation provenance is required |
| Class | `class` | Native; node, member, and relation provenance is required |
| ER | `er` | Native; entity, attribute, relationship, and explicit cardinality evidence is required |
| Requirement | `requirement` | Native Mermaid `requirementDiagram` |
| Block | `block` | Native; Mermaid 11.16 rejects this grammar's `accTitle`/`accDescr`, so resolved text remains in typed IR |
| Swimlane | `flowchart` | Portable subgraph fallback |
| BPMN | `bpmn → swimlane → flowchart` | Portable lane/subgraph fallback with explicit BPMN-notation loss |
| Generic Network | `flowchart` | Portable node/edge projection |
| C4 | `c4 → architecture → flowchart` | Automatic output uses Architecture because native C4 SVG conflicts with the strict data/xlink gate; Architecture rejection triggers one nested Flowchart retry |
| Deployment | `deployment → architecture → flowchart` | Flatten nodes and artifacts to services; preserve unsupported notation and relation labels in typed IR |
| Component | `component → architecture → flowchart` | Flatten components and interfaces to services; preserve unsupported notation and relation labels in typed IR |
| Use-case | `flowchart` | Stadium actor and round use-case proxies with typed relation labels; actor glyphs, system boundaries, groups, styles, and bidirectional metadata remain in typed IR |
| Pie | `pie` or `flowchart` | Native only within the 12-slice, binary64, 1% visibility, and `showData` exactness domain; otherwise use a same-slot exact-value fallback |
| XY | `xychart` or `flowchart` | Native only with a bounded exact renderer grid, visible line/bar geometry, and safe binary64 values; otherwise use a same-slot exact-value fallback |
| Quadrant | `quadrant` or `flowchart` | Native only for exact normalized coordinates that pass the pinned-canvas visibility gate; otherwise use a same-slot exact-cell fallback |
| Sankey | `sankey` or `flowchart` | Native only for a positive, native-safe weighted DAG; otherwise use a same-slot exact-weight fallback |
| Radar | `radar` or `flowchart` | Native for at most 12 series over a zero-or-normal binary64 domain with a finite positive span/radius; otherwise use a same-slot tabular fallback of at most 256 points |
| Treemap | `treemap` or `flowchart` | Leaf values are required; internal values, unsafe binary64/display totals, or runtime rejection select a same-slot exact-value fallback |
| Venn | `venn` or `flowchart` | Native only for positive normal binary64 areas, a `200:1` visibility bound, and a complete explicit-pair contract; otherwise use an exact set graph |
| Journey | `timeline` | Avoids `foreignObject`, which the strict SVG gate rejects; score and actors are retained as event text |
| Kanban | `kanban` or `flowchart` | Retry once in the same candidate slot with the shared planning plan when native runtime validation fails |
| GitGraph | `gitgraph` or `flowchart` | Retry once in the same candidate slot while retaining commit/parent topology and disclosing branch-lane and glyph loss |
| Packet | `packet` or `flowchart` | Native only for explicit contiguous bit ranges; fallback fields are disconnected and do not invent virtual gaps or edges |
| Ishikawa | `ishikawa` or `flowchart` | Native/fallback hierarchy after cycle, object-reuse, duplicate-ID, and depth checks |
| TreeView | `treeview` or `flowchart` | Native/fallback hierarchy under the same bounded tree plan |
| Event Modeling | `flowchart` | Lane-aware fallback because the Mermaid 11.16 renderer is not stable enough for automatic native publication |
| Wardley | `wardley` or `flowchart` | Native when accepted; fallback discloses loss of coordinates, axes, anchors, and the visible native title |
| Cynefin | `cynefin` or `flowchart` | A successful native render is review-only because Mermaid emits a fixed template; fallback contains only supplied domains/items/transitions |
| Railroad | `railroad` | Experimental native grammar over a strict bounded recursive rule AST |
| ZenUML | `sequence` | Explicit fallback because the pinned runtime has no ZenUML extension |
| Organization | `treeview` | Preserves reporting hierarchy without claiming organization-specific notation |
| Data Lineage | `flowchart` | Portable graph after validating every dataset/process endpoint |

## Shared serialization invariants

### No coercion or invented structure

Known record/container/scalar fields are validated before serialization. IDs and relation endpoints
must be exact built-in strings where their grammar requires strings. Missing values, numbers, booleans,
string subclasses, and arbitrary objects are not converted with `str()`. In particular, a missing
endpoint cannot be laundered into an actual node named `"None"`, and a falsey non-string label cannot
fall back to its source ID. Unknown endpoints, normalized-ID collisions, duplicate records, invalid
membership, and unsupported nested intent fail the complete candidate rather than producing a partial
graph.

Serializer-owned semantic checks remain authoritative after the nested extraction contract. Examples
include non-empty collections, endpoint integrity, cardinality, schedule consistency, chart ranges,
hierarchy acyclicity, and native-versus-fallback selection. Validation models preserve allowed extra
metadata and do not rewrite the caller's input dictionary.

### Provenance and evidence

State, Class, and ER reject publishable-looking structures without the required provenance. Sequence
rejects the complete plan if any participant endpoint is unknown or null. Each known record may carry
at most 256 string `evidence_ids`. A structurally valid extended projection can isolate a malformed or
oversized evidence list by replacing that record's evidence tuple with an empty tuple; it must never
turn malformed metadata into partial or fabricated attribution. Publication still applies the
generated-node provenance and type-specific OCR/vector association gates described in
[quality evaluation](quality.md).

### Semantic, source, and canvas text

Terminal plans distinguish:

1. semantic text retained in typed/review IR;
2. Mermaid source text, including source-only neutralization; and
3. text actually visible in pinned Mermaid 11.16 SVG.

Source-only zero-width separators disable scanner, lexer, URL, directive, callback, entity, or
statement behavior without changing visible text. They do not create a compatibility warning.
Whenever the runtime cannot preserve a character literally, the plan uses an explicit visible
compatibility glyph and records a warning. Scene IR and semantic OCR consume the same terminal-visible
text as the selected native or fallback serializer. Raw role, style, direction, icon, geometry, or
relation metadata is not promoted into visible topology unless that terminal explicitly represents it.

### Accessibility snapshots

Terminal-planned types validate raw `title`, `description`, `acc_title`, and `acc_description` before
generic enrichment. Absent/`None` fields are omitted, and exact `""` is accepted only as an
omitted-value compatibility form. Other values must be exact built-in strings, within raw and
normalized `MAX_TEXT_CHARS`, non-empty after normalization, valid UTF-8, and free of the prohibited
Unicode control/format/surrogate/line-separator categories for that terminal. Initial candidates and
accepted repairs retain the validated raw snapshot rather than stale derived `acc_*` values, then
rebuild accessibility text from the current semantic plan. See [accessibility generation](accessibility.md)
for grammar-specific emission limits.

### Source and runtime budgets

Terminal serializers preflight source before handing it to Chromium. Plans that model JavaScript
string length use at most 50,000 UTF-16 code units; older portable/specialized plans use the documented
50,000-character cap. Every serializer is also limited to 5,000 source lines. Incremental planners
must stop before materializing an over-budget complete line list or source string. Runtime validation
applies security scan, parse, render, SVG inspection, and exact terminal-type agreement.

The runtime-reported type is stored as `runtime_diagram_type`. A deterministic typed serializer is not
render-valid when its declared emitted type differs from the runtime type. Direct Mermaid is
reclassified to its actual runtime type and receives type fitness `0`, preserving a review warning.

The principal serializer-specific hard limits are summarized here; lower shared Scene/model limits
still apply when reached first.

| Plan | Exact hard limit |
| --- | --- |
| Per-record evidence | 256 string references |
| Journey/Kanban/GitGraph structure | 2,000 records |
| Ishikawa/TreeView | depth 64, 2,000 nodes, 500 fallback edges |
| Railroad | depth 20, 500 rules, 500 expressions, 128-character rule/reference names, 500-character visible fields |
| Pie | 12 native slices, 256 fallback slices |
| XY | 10 native series, 256 fallback points |
| Quadrant | 256 points, 100,000 visibility comparisons |
| Sankey portable fallback | 500 edges |
| Radar | 12 native series, 256 fallback points, 100 ticks |
| Treemap portable fallback | 500 relations |
| Venn | `200:1` native area visibility ratio, 500 fallback membership edges |
| Treemap/Venn attribution | 20,000 references, 50,000 texts, 1,000,000 characters, 100,000 tokens, 100,000 spatial comparisons |

A runtime portable fallback is attempted once in the same candidate slot. It does not consume another
type, candidate, or repair budget. The fallback must independently pass source security, parse, render,
SVG, and terminal-type gates. Success retains `requested_type`, updates `emitted_type` and
`runtime_diagram_type`, extends `fallback_chain`, adds a warning, and records
`runtime_portable_fallback` in repair history. Failure invalidates only that candidate.

## Core and software terminal plans

### Flowchart groups, Swimlane, and BPMN

Flowchart emits only explicit, flat, disjoint groups with an ID, label, and non-empty `member_ids` as
portable subgraphs. One shared plan normalizes node/group IDs and supplies identical emitted IDs,
membership, and relation endpoints to the serializer and generated Scene. Unknown members, duplicate
source nodes, overlapping membership, nested intent, normalized node/group collisions, non-scalar or
oversized labels, and Scene resource overflow raise `SerializationError`. Output without groups remains
byte-identical to the ungrouped serializer.

Swimlane and BPMN prepare lane membership for the same plan and serialize only once. Their generated
`SceneGroup` preserves actual subgraph membership, including missing-node-ID fallback and bidirectional
edges. Top-level groups are not mixed into lane topology, nested lanes are rejected, and this step does
not recover group styling.

### Sequence

`plan_sequence_records()` canonicalizes string/object participants and object messages once. A
missing, `None`, or exact-empty participant label falls back to the source ID; a missing message label
becomes `[unreadable]`. Every other label is bounded UTF-8 text without coercion. Source IDs remain
endpoint/provenance identities, while declarations, message endpoints, and Scene elements share
source-order IDs `mmx_sequence_participant_N`. Raw message IDs remain typed/review metadata because
Mermaid messages have no ID syntax; Scene/provenance relations use `generated-relation-N`.

Message style is a closed set:

| Style | Mermaid operator | Scene line/marker |
| --- | --- | --- |
| `solid` | `->>` | solid, end arrow |
| `dotted` | `-->>` | dotted, end arrow |
| `open` | `->` | solid, no end marker in Mermaid 11.16 SVG |
| `dotted_open` | `-->` | dotted, no end marker in Mermaid 11.16 SVG |
| `cross` | `-x` | solid, cross end marker |

Character-level `#35;`/`#59;` escaping preserves `#` and `;`. Source-only separators neutralize active
tokens. Accessibility alone displays literal angle brackets as `〈`/`〉` with a conditional warning.
Any malformed record, duplicate participant, unknown/null endpoint, or record/source-budget overflow
rejects the whole Sequence plan.

### Mindmap

`plan_mindmap_records()` freezes recursive `root/children` in iterative preorder. Raw logical IDs stay
in typed/review provenance; native source and generated Scene use source-order `root`, `node_N`, so a
duplicate logical ID cannot overwrite a node. `label` and `text` aliases may coexist only when their
normalized semantics agree. Exact empty means omitted, and a missing label becomes `[unreadable]`.
Malformed children, object reuse, invalid Unicode text, or depth/node/source overflow rejects the
complete hierarchy.

The root is a quoted circle and descendants are quoted rectangles. Source-only separators neutralize
Mindmap Markdown/shape-lexer behavior. Visible `″`, `＊`, `ˋ`, `～`, `＆`, or `＃` substitutions are used
only when Mermaid 11.16 cannot preserve quotes, active emphasis/code syntax, or numeric entity-like
literals. Generated Scene is radial, uses marker-less containment, and shares child evidence with its
containment relation. Native Mindmap treats accessibility directives as another root, so directives
are not emitted.

### Timeline

`plan_timeline_records()` canonicalizes one visible title, each period, and ordered event labels.
`time`/`period`, or `label` and the first `events[]` entry, may coexist only when normalized text agrees.
Empty aliases are omitted and missing event labels become `[unreadable]`. Invalid containers, nested
labels, aliases, IDs, duplicate IDs, or record/label/source overflow rejects the whole plan. Raw source
IDs remain provenance identities; generated Scene uses `timeline_event_N`.

Because Mermaid 11.16 treats `title`, `section`, `%`, `#`, and `:` in user periods as grammar tokens,
the plan appends a generated zero-width sentinel and emits ASCII code points as numeric entities. The
sentinel may remain in source/DOM but is removed from Scene/OCR semantic canvas text. Pinned Timeline
accepts accessibility directives without producing SVG accessibility elements, so directives are not
emitted and the raw snapshot remains review metadata.

### State, Class, and ER

State uses one emission plan for normalized/emitted IDs, endpoints, pseudo-state kinds, terminal text,
and deterministic Scene relation IDs. `[*]` boundary transitions are validated and retained in source,
but no fake boundary element is added to Scene. Boundary labels remain semantic OCR content. Reserved
State/security tokens and IDs containing `iconify` receive collision-safe `mmx_state_id_…` aliases;
source identity and evidence remain unchanged.

Visible State text requires exact bounded UTF-8 strings, collapsed Unicode whitespace, and no remaining
control/format/surrogate characters. Exact-empty ordinary labels fall back to the state ID; hidden
choice/fork/join labels do not. The bounded delimiter scanner handles code spans, links, emphasis, and
strike syntax without regex-tail amplification. Compatibility glyphs are used only for renderer-visible
loss; email/`www` autolinks and scanner-active grammar words use source-only neutralization. Transition
`:`/`;` restrictions apply to normalized semantic text. Accepted repair reconciles compatibility
warnings from the current record/accessibility plan.

Class requires provenance for nodes, members, and relations and rejects unknown relation endpoints.
It does not infer missing members, visibility, or relationship meaning.

ER shares one entity/attribute/relationship plan across serialization, Scene, and OCR. Relationship
roles always emit as one `: "..."` terminal so a multiword role cannot create phantom entities.
Entity aliases, attribute type/name/key/comment, and relationship roles use grammar-position-specific
encoders. Attribute keys are limited to source-provided `PK`, `FK`, and `UK`. Unsafe/reserved ER IDs,
including `iconify`, use collision-safe `mmx_er_id_N[_suffix]` aliases shared by declarations,
relationships, and Scene. Explicit cardinality and identifying flags remain mandatory; unknown
endpoints never produce a partial ER graph.

### Gantt

Gantt freezes semantic/source/canvas text for title, section, and task, while accessibility uses a
separate semantic plan. Missing or exact-empty section/task labels become `Tasks` and section-local
`Task N`. Empty sections are omitted, and an all-empty diagram is rejected. Section attribution IDs
are collision-safe; task IDs are real schedule keys and must be unique ASCII identifiers. Runtime tags
`active`, `done`, `crit`, `milestone`, `vert`, `__proto__`, and case-insensitive `iconify` conflicts are
rejected as IDs.

Task status is a duplicate-free subset of `active|crit|done|milestone`; `active` and `done` cannot
coexist. Every task has exactly one of `end` or `duration`. Schedule fields reject `,`, `#`, and `;`.
The supported numeric Day.js date-format subset requires year/month/day; `h`/`hh` requires `A`/`a` and
vice versa. `Z`, `ZZ`, `S`, `SS`, and seconds timestamp `X` are rejected; exactly three-digit `SSS` and
canonical millisecond timestamp `x` are supported. `x` and all resolved ends must remain within the
ECMAScript Date range. Non-milestone end must be after start; a milestone alone may use equality.

Duration grammar is exact decimal plus `ms|M|d|h|m|s|w|y`. Fractional `ms`, `d`, `w`, `M`, and `y` are
rejected because Mermaid rounds them. Fractional `h`, `m`, and `s` are accepted only when they convert
to an exact positive integer millisecond within the bounded runtime range. Exact zero is allowed only
for an explicit milestone. Other Mermaid-renderable formats such as `MMM`, time zones, and partial dates
remain direct/review-only.

An `after id...` start may reference only unique task IDs that appeared earlier in source order;
duplicate targets, forward references, cycles, `after` plus end date, and all `until` forms fail closed.
Validated dependencies are not yet promoted to attributed `SceneRelation`, so dependency path scoring
remains unavailable. Visible task `:`/`%` becomes `∶`/`％`; title/accessibility `<` becomes `‹`, with a
conditional warning. Final SVG inspection requires every `rect[class~="task"]` in a `gantt` runtime to
have finite positive width and height, catching renderer-rounded zero-width tasks even after successful
parse/render.

### Requirement and Block

Requirement records (`requirement`, `element`, `relation`) and Block records (`block`, `edge`) pass a
strict nested contract for known scalar fields, `bbox`, and `evidence_ids`. Requirement type/risk/verify
method/relation type and Block shape use the serializer's closed case-insensitive token sets without
rewriting source casing. Legacy `verifymethod` is validated against the same set, while prompts advertise
only `verify_method`. Block `columns` is advertised as `auto|integer`; serializer semantics determine
omitted/automatic/positive-integer behavior. Unknown endpoints and normalized collisions fail closed.

## Architecture-family and Phase 2 projections

### Architecture terminal plan

Native Architecture and its nested Flowchart retry share one frozen service/group/relation plan. Label
resolution is `label → name → source ID`, plus group-label fallback. Unicode whitespace collapses to
one ASCII space; non-string, normalized-empty, control/format/surrogate, and oversized values are
rejected. Plain labels remain readable. Only labels containing quotes, active Markdown delimiters,
entity-like literals, or scanner/lexer-active spelling use quoted-Markdown source neutralization.
Mermaid 11.16 visible substitutions are disclosed for Architecture, C4, Deployment, and Component in
both initial and repaired candidates.

Serializers build source directly from this plan and never pass pre-escaped labels through generic
`_text()` or generic Flowchart serialization, preventing backslash duplication and `&quot;` re-encoding.
Scene/OCR shares canvas labels, emitted IDs, membership, endpoints, bidirectional markers, and
record-local evidence. Native Scene direction is `unknown`; retry Scene uses validated
`TB|BT|LR|RL`, defaulting to `LR`. `bidirectional` is an exact built-in boolean or omitted; a string
`"false"` is invalid. Missing/non-string endpoints are not coerced. Native and retry sources each use
incremental 50,000-UTF-16-unit and 5,000-line preflight.

### C4

C4 validates root `elements`, optional `boundaries`/`relations`, 14 canonical element kinds, and root
level `context|container|component` case-insensitively. Legacy `type` is checked against the same kind
set while preserving its original field and casing. Relation ports are uppercase `L|R|T|B` and
`bidirectional` is strict boolean. Boundary `type` is checked only as a string because automatic output
does not emit native boundary notation.

`serialize_c4_native` is a trusted diagnostic path, not the automatic publication or evaluation
baseline. Automatic C4 maps elements to Architecture services, boundaries to groups, and relations to
unlabeled service edges. The shared Architecture plan supplies collision-safe IDs, labels, membership,
ports, and topology to native output, retry output, Scene, and OCR. Technology, descriptions, relation
labels/polylines, and exact native boundary notation remain typed/review metadata. An empty C4 boundary
is retained by Architecture/Scene, but a nested Flowchart retry rejects the candidate because an empty
portable subgraph cannot be represented safely.

### Deployment and Component

Deployment requires root `nodes`; Component requires root `components`. Deployment artifacts and
Component interfaces are flattened to generic services after their primary records. Groups become
actual Architecture groups/membership. Artifact containment/stereotypes and provided/required interface
notation remain typed/review metadata.

Deployment prefers canonical `links` and Component canonical `dependencies`; when that key is present,
even as an empty list, legacy `edges` is not merged. `edges` is read only when the canonical key is
absent. Link/dependency labels, raw relation IDs, and relation bboxes are not emitted. Exact endpoints,
strict `bidirectional`, and uppercase `source_side`/`target_side` (`L|R|T|B`) determine unlabeled edges.
Known service icons `cloud|database|disk|internet|server` are normalized; unknown open-string icons
fall back to `server`. Flowchart retry retains IDs, labels, membership, and topology but loses icons and
port sides. Root direction affects generated Scene and Flowchart retry, not Architecture layout.

### Use-case

Use-case requires both `actors` and `use_cases`; `relations` is optional. Actor/use-case IDs share one
collision-safe namespace and use `label → name → source ID`. Actors emit as stadium proxies and use
cases as distinct round Flowchart nodes. Groups, system boundaries, actor glyphs, and raw role/shape
metadata are not inferred. Relation text prefers open-string `type`, then `label`, and otherwise emits
an unlabeled one-way edge. Raw relation ID, `bidirectional`, arrow/style, and semantic metadata stay in
typed IR. The default direction is `LR`; unsupported input direction falls back to `TB` in both source
and Scene.

## Planning and specialized projections

Journey, Kanban, and GitGraph use strict nested contracts and an absolute structural limit of 2,000
records, followed by the 50,000-character and 5,000-line source gates. Journey score must be in `1..5`.
Journey always emits `timeline`; sections become Scene groups and tasks become attributed elements.
Only task label, `Score N`, and actor lists become OCR text. Source numeric evidence must independently
support the score. Timeline delimiters are neutralized visibly (`:` → `∶`, entity prefixes → `＆`/`＃`,
title angle brackets → `‹`/`›`) with warnings.

Kanban freezes collision-safe `kanban_` IDs and column/card containment. GitGraph replays operations
from exact `main`, fixing commit/merge nodes, parent relations, branch membership, and tags. Canonical
fields and compatibility aliases may coexist only when semantically equal. Native GitGraph and Kanban
use grammar-specific quoting; same-slot Flowchart fallback uses portable label encoding and discloses
visible substitutions and native-only layout/glyph loss.

Packet binds every source field, raw/emitted `packet_field_` ID, label, and explicit start/end range.
Only contiguous ranges use native grammar. Gaps or runtime rejection produce disconnected `LR` field
nodes with no inferred relations. Native title is separate from field plans and counts as OCR only for
the `packet` terminal. Field-local numeric attribution is unchanged by fallback.
Automatic publication requires each field's label and explicit range to be proven by the field-local,
candidate-authorized OCR/vector observation and source bbox used by the shared plan. Missing or invalid
geometry, ambiguous or reused evidence, swapped ranges, numeric mismatch, and exhausted association
work remain review-only; source-wide OCR text cannot substitute for field ownership.

Ishikawa and TreeView share DFS/preorder plans for source/emitted ID, label, depth, parent, and source
record. Raw/normalized/emitted collisions, cycles, reuse of the same dictionary object, depth over 64,
more than 2,000 nodes, or more than 500 fallback edges reject the complete hierarchy. Ishikawa also
rejects an effect `children` field that would overwrite category roots. Entity-like visible
substitutions are disclosed; raw labels and evidence remain unchanged in typed/review IR.

Event Modeling uses `eventmodeling_lane_*` and `eventmodeling_frame_*` in a lane-aware Flowchart.
Relations have no Mermaid source ID, so `eventmodeling_relation_*` is Scene/provenance-only. ZenUML
uses `zenuml_participant_*` as Sequence participant IDs and `zenuml_message_*` only as Scene/provenance
slots. Unsupported role/shape/style/direction/bidirectional/relation-ID metadata is not promoted.

Organization shares logical `treeview_node_*` identities and parent-to-child topology across TreeView,
runtime Flowchart fallback, Scene, and OCR; both layouts use `LR`. Data Lineage freezes dataset/process
namespaces, cylinder/rectangle shapes, explicit endpoints, labels, and strict direction. Mermaid edges
have no source relation-ID syntax, so `organization_relation_*` and `data_lineage_relation_*` are
Scene/provenance-only.

Wardley freezes finite `x`/`y`, exact boolean `anchor`, links, labels, and record evidence. Native
`[visibility, evolution]` serializes IR `[x, y]` as `[y, x]`; normalized Scene position is `(x, 1-y)`
after applying the same emitted-token rounding. Native `->` has no SVG marker and becomes an undirected
Scene relation. Runtime fallback is marker-less `flowchart LR` over explicit links only and always
warns that coordinates, axes, anchors, and visible native title were lost. Its generated Scene uses
zero geometry so layout similarity cannot claim preservation.

Cynefin freezes the five official domains, reserved-safe group IDs, ordered item IDs, explicit
transition IDs, and terminal text. Native success includes Mermaid 11.16's fixed domain/practice/
response/disorder template in Scene/OCR without evidence; `confusion` exposes only the first three
items plus `+N more`. Native output is always review/sidecar-only. Runtime fallback emits one subgraph
for each supplied domain, all supplied items without abbreviation, and only explicit directed
transitions. It never creates omitted official domains, template nodes, `+N more`, or membership edges.

Railroad consumes only `plan_railroad_records()` output. It permits maximum depth 20, at most 500 rules,
and at most 500 expressions. Rule/nonterminal names normalize to ASCII identifiers of at most 128
characters; other visible fields normalize to at most 500 characters. Source-active names, expression
words, `railroad-beta`, lowercase `title*`, and names containing `style`/`classDef` are mapped to
collision-safe `rrmapped_N[_suffix]` native names. Scene IDs `railroad_expression_N` and
`railroad_relation_N` are not source syntax, and no nonterminal-to-rule edge is invented.

Railroad compatibility output maps ASCII angle brackets to `〈`/`〉`, every ASCII `#` to `＃`,
entity-like `&` prefixes to `＆`, and NFKC quote/backslash compatibility characters to `″`/`∖`.
Source-only separators neutralize remaining active tokens. Both emitted source and its NFKC-normalized
form must pass the strict scanner; production parse/render applies to raw source, while NFKC
parse/render is an integration safety probe for grammar injection. Generated Scene rejects the whole
candidate when record `evidence_ids` is neither null/omitted nor a string list.

## Chart terminal plans

### Pie, XY, and Quadrant

Structured extraction accepts finite JSON `int`/`float`, strict booleans, bbox/evidence, and closed XY
`line|bar`; direct serializers may additionally receive `Decimal`. Missing/unreadable chart-value
table/prose fallback is not implemented. Native and exact fallback source are planned separately and
must each pass 50,000 UTF-16 code units and 5,000 lines.

Pie requires unique non-empty labels, non-negative values, and a positive total. Native uses at most
12 slices, zero-or-normal round-trip-safe binary64 values, a finite left-to-right JavaScript total and
centroid, and at least 1% visibility for every positive slice. Zero slices are legend-only.
`show_data=true` additionally requires JavaScript `String(value)` to equal the fixed-decimal token.
Otherwise, at most 256 disconnected `label: exact-value` Flowchart cells are emitted. Native Scene is
radial with `pie_slice_N` sectors; fallback Scene is zero-geometry `TB`. Publication requires each
non-overlapping slice bbox to cite an authorized OCR/vector observation proving its full label and
exact value, plus exact global numeric occurrences. Swaps, omissions, extra numbers, ambiguity, invalid
geometry, or exhausted work remain review-only.

XY requires exactly one categorical or numeric x-axis mode, valid bounds, in-range y values, exactly
one of values/points per series, and matching category lengths. Named series are rejected because
Mermaid 11.16 lacks strict-safe series-label syntax. Native requires zero-or-normal exact binary64
axes/values, a positive normal span, and a bounded simulation of the renderer loop with exact count,
endpoints, and progress. Lines need at least two points; bars need positive height from y-minimum.
Overlaid bars/paths, more than 10 series, non-uniform explicit x, or point drop/stall risk choose an
exact fallback of at most 256 points. Native data values are geometry, not OCR text. Axis, series, and
point records require owner-local authorized observations and exact global numeric occurrences.

Quadrant requires unique non-empty labels and exact `[0,1]` coordinates. Native supports at most 256
points whose fixed decimals round-trip as zero-or-normal binary64 and remain finite, distinct,
unclipped, and non-overlapping with point/label/quadrant/axis/title text on the pinned 500×500 canvas.
Pairwise visibility work is capped at 100,000 comparisons. Otherwise it emits disconnected `TB` cells
for title, axes, supplied slots, and `label · x X, y Y`. Native Scene uses `(x, 1-y)` normalized points,
four axis endpoint elements, and four quadrant groups but invents no axis lines or membership edges.
Axis ownership includes horizontal/bottom and vertical/left spatial checks. Slot labels need independent
source-quadrant evidence; the midpoint heuristic does not infer an off-center plot bbox. Metadata-role
evidence proves content existence only, so best effort warns about role attribution and strict policy
requires review.

### Sankey

Sankey freezes native IDs, collision-safe Flowchart IDs, exact decimal weights, relation Scene IDs, and
record evidence. Native requires a positive weighted DAG, unique native-safe labels, participation of
every node, safe `parseFloat` behavior, and exact reproduction of Mermaid's displayed node total:
`Math.round(max(incoming, outgoing) * 100) / 100`. Native Scene uses marker-less unlabeled `data_flow`
relations in `LR`; OCR includes node labels and displayed totals, not individual weights. Native can use
the common Scene relation limit, but Flowchart fallback is unavailable above Mermaid worker
`maxEdges=500`; 501 or more valid native flows may therefore remain native but cannot retry.

Fallback emits every exact weight as a directed edge label using the shared IDs, requested direction,
and end arrows. Native emits no title/description; fallback emits resolved values only as SVG
accessibility metadata. Each flow must own a positive, non-overlapping in-image bbox and directly cite
an authorized contained OCR/vector observation proving exact `value_text`; global numeric occurrences
must also match. Cross-flow reuse, ambiguity, swaps, invalid geometry, or work exhaustion fail closed.
Direct/untyped Sankey remains review-only.

Raw Sankey metadata is validated before enrichment as exact strings within pre-normalization
`MAX_TEXT_CHARS`, valid UTF-8, non-empty/bounded after normalization, and without normalized
`Cc|Cf|Zl|Zp`. Exact empty is omitted. Native is exempt from metadata attribution because it does not
emit it. Effective non-derived fallback title and description each require independent authorized
spatial proof outside node/flow records, or an approved exact initial `user_edit`; an engine-created
edit cannot authorize itself.

### Radar

Radar freezes dimensions, series, exact fixed decimals, collision-safe axis/series/cell IDs, visible
text, and evidence. Native requires at most 12 series; values and explicit bounds must round-trip as
zero or normal binary64; Decimal/binary64 span and renderer radius must be positive finite. Negative,
subnormal, overflow, precision-loss, or zero/non-finite span selects a fallback of at most 256 points.
`ticks` is capped at 100.

Native Scene contains normalized axes/data points, series elements, and a marker-less closed
`series_curve`; source bbox is not copied into generated geometry. OCR includes visible title, axes,
and legends only when `showLegend=true`. Fallback is an edge-free `flowchart TB` with an isolated title,
conditional series groups, and `dimension: exact-value` cells. Runtime SVG containing `NaN` or
`Infinity` geometry is render-invalid. Dimension records must prove exact labels; series records must
prove label plus all values in source order. Owner overlap, evidence reuse, same-bbox contradiction,
invalid geometry, global numeric mismatch, or work exhaustion remains review-only.

### Treemap

Treemap uses one bounded DFS preorder plan for logical IDs, collision-safe
`treemap_node_N[_suffix]`, Flowchart `N1..Nn`, parent/child topology, terminal text, exact fixed values,
and evidence. Cycles, object reuse, and depth/node/relation overflow fail before serialization. Source
bboxes remain typed/review provenance; both generated Scenes use zero geometry.

Native `treemap-beta` renders internal nodes as sections and leaves as value cells. The plan reproduces
Mermaid 11.16 reverse-order binary64 child accumulation and d3 `format(",")` 12-digit display totals.
Underflow, overflow, unsafe range, shortest-decimal loss, or non-reproducible totals select Flowchart.
Tiny native cells may hide text with `display:none`; native OCR therefore cannot assume every label is
visible. Any explicit internal-node value also selects Flowchart. Fallback uses parent-to-child end
arrows, adds exact `(value: x)` only for supplied values, does not derive internal totals, and is
unavailable above 500 relations.

Automatic publication binds every planned node to a finite positive in-image bbox. A child must be
strictly contained in its parent; direct sibling interiors cannot overlap, although touching edges are
allowed. Internal-owner cited text cannot overlap direct-child regions. Owner-local OCR/vector reading
order must prove label and any explicit value. Shared budgets are exactly 20,000 references, 50,000
texts, 1,000,000 characters, 100,000 tokens, and 100,000 spatial comparisons. Owner reuse,
same-bbox contradiction, invalid hierarchy, or exhausted work is unavailable. Direct Treemap has no
owner plan and remains review-only.

Terminal-effective metadata is attributed only when emitted. Native requires proof for its visible
title and non-derived accessibility title/description; fallback requires only its emitted accessibility
metadata. Shadowed legacy fields, deterministic derived text, and the experimental notice are exempt;
a notice-only explicit description is unavailable. Proof must lie outside all node bboxes, and title
and description remain separate roles even with equal text. Raw metadata accepts only omitted/`None`/
exact-empty-as-omitted or exact strings within pre-normalization `MAX_TEXT_CHARS`, valid UTF-8,
non-empty/bounded after normalization, and without raw `Cc|Cf|Zl|Zp`.

### Venn

Venn freezes collision-safe set/intersection IDs, canonical membership, exact non-exponent fixed
values, terminal text, and evidence. Reused objects, unknown/repeated members, duplicate canonical
intersections, intersections larger than observed sets/sub-intersections, and area/membership overflow
fail before serialization. Every record must cite an exact authorized contour bbox plus separate
contained OCR/vector text proving supplied label/value. Intersections with neither field cannot prove
text ownership and require review.

Owner observations are injective. Record bboxes may overlap by definition, but an intersection must be
inclusively contained in every declared set, not fully contained in undeclared sets, and every
higher-order intersection must lie inside all explicit strict-subset intersections. Equal containment
is allowed. Set scans, intersection-pair scans, contour comparisons, and text containment share one
100,000-spatial-work budget; the same 20,000/50,000/1,000,000/100,000 reference/text/character/token
limits apply. Direct/untyped Venn remains review-only.

Native `venn-beta` requires every size to be observed, positive normal binary64, safe for Python integer
input, and within `largest set / smallest positive area <= 200`. Exact containment, zero, subnormal,
overflow, or precision loss selects Flowchart. For three or more sets, every pairwise intersection in
the union must be explicit; missing pairs and higher-order areas are never synthesized. Native Scene
uses circles/areas and marker-less logical membership with `unknown` direction. Fallback uses set
circles, round intersection nodes, exact `(value: x)` suffixes, `intersects` end-arrow relations, and
`LR`; it is unavailable above 500 membership edges.

Native Venn attributes only its visible explicit title; unsupported description/accessibility fields
are not emitted and are exempt. Fallback attributes only effective emitted accessibility roles.
Shadowed legacy fields, structure-only deterministic text, and the pipeline experimental suffix are
exempt, but notice-only explicit description is unavailable. Metadata proof must be outside all data
areas and cannot reuse data or another metadata role. Raw metadata follows the same exact-string,
pre-normalization `MAX_TEXT_CHARS`, raw `Cc|Cf|Zl|Zp`, normalized non-empty/bounded, and UTF-8 gate as
Treemap; newline/tab cannot be laundered by whitespace normalization.

## Compatibility inputs and remaining limitations

Direct serializers retain legacy Sankey `links`, Radar `axes`, and Treemap/Venn `name` aliases only
when the canonical key is absent; structured prompts do not advertise them. Raw Direct Mermaid does
not receive typed-metadata validation because it has no typed metadata fields; it remains subject to
security scan, parse/render/SVG validation, runtime-type checking, and review-only policy whenever a
typed ownership plan is required.

The project does not currently synthesize structured table/prose output for missing or unreadable
chart values. Gantt supports only its documented conservative date subset and does not yet attribute
dependency relations. Native Cynefin is not automatically publishable. C4 native output is diagnostic.
Deployment/Component do not recover dedicated native notation. Event Modeling and ZenUML remain
portable fallbacks. Raster-derived group/lane styling and chart-series styling are outside this
serialization contract.
