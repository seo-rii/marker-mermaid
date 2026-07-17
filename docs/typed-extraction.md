# Typed extraction contract

Structured VLM output is not accepted as arbitrary JSON with a matching `diagram_type`.
Every enabled Mermaid type receives a `TypedIRContract` that fixes its root fields and container
kinds. The same registry validates the response again while Pydantic models are being built.
Serializer-specific semantic validation runs only after this structural boundary.

This separation is intentional:

- the extraction contract rejects cross-type root confusion, such as `nodes` in a `sequence`
  candidate;
- the serializer validates type-local meaning, including references, cardinality, numeric evidence,
  dates, resource limits, and whether Mermaid can represent the result.

`TYPED_IR_CONTRACTS` must have exactly the same key set as `ALL_TYPES`; missing or extra keys fail at
import time. Prompts include only current `enabled_types`, so disabled schemas consume no prompt
budget. Shared optional root fields are `title`, `description`, `acc_title`, `acc_description`, and
`direction`. Semantic node and relation records request `evidence_ids` from the supplied priors.
Every known record uses the same maximum of 256 evidence references in both the prompt and local
nested schema.

Validation models use `extra="allow"`. They validate registered containers and scalars without
replacing the original dictionary, stripping extension metadata, rewriting casing, or inserting
default collections. The original IR therefore continues into serialization, repair, canonical
hashing, and sidecars. A known scalar cannot be an object or list; record and child-container kinds
are fixed; `bbox` is exactly four finite numbers; and evidence and membership collections are
string lists. Root-list existence is structural. Non-emptiness, identity uniqueness, references,
placeholders, and representability remain serializer responsibilities unless stated otherwise.

## Contract coverage

All registered types have strict nested validation below their root. The provider-visible canonical
roots and record families are:

| Type | Required roots | Optional roots and canonical records |
| --- | --- | --- |
| Flowchart / Generic Network | `nodes: list` | `edges`, `groups`; node, edge, group member/evidence lists |
| Swimlane / BPMN | `lanes: list` | top-level `edges`; lane and nested node records |
| Sequence | `participants: list`, `messages: list` | participant and message records |
| Mindmap | `root: object` | recursive `children` hierarchy |
| Timeline | `events: list` | event aliases and ordered nested event labels |
| Gantt | `sections: list` | nested tasks and schedule/status fields |
| Architecture | `services: list` | `groups`, `edges`; service, group, port-aware edge records |
| State | `states: list` | transitions; state kind and transition endpoints/labels |
| Class | `classes: list` | class members and relations/cardinalities |
| ER | `entities: list` | `relationships`; attributes, keys, cardinalities |
| Requirement | `requirements: list` | `elements`, `relations` |
| Block | `blocks: list` | `edges`, `columns` |
| C4 fallback | `elements: list` | `level`, `boundaries`, `relations` |
| Deployment fallback | `nodes: list` | `artifacts`, `groups`, `links`; legacy `edges` is input-only |
| Component fallback | `components: list` | `interfaces`, `groups`, `dependencies`; legacy `edges` is input-only |
| Use-case fallback | `actors: list`, `use_cases: list` | `relations` |
| Pie | `slices: list` | `show_data` |
| XY | `x_axis: object`, `y_axis: object`, `series: list` | categorical/numeric axes, line/bar series, points |
| Quadrant | `x_axis: object`, `y_axis: object`, `points: list` | `quadrants` |
| Sankey | `nodes: list`, `flows: list` | node and weighted-flow records |
| Radar | `dimensions: list`, `series: list` | `min`, `max`, `ticks`, `show_legend`, `graticule` |
| Treemap | `root: object` | recursive nodes and optional explicit values |
| Venn | `sets: list`, `intersections: list` | set membership and optional explicit values |
| Journey | `sections: list` | nested scored tasks and actors |
| Kanban | `columns: list`, `cards: list` | explicit `column_id` references |
| GitGraph | `initial_branch: string`, `operations: list` | ordered commit/branch/merge operations |
| Packet | `fields: list` | integer bit ranges and labels |
| Ishikawa | `effect: object`, `categories: list` | leaf effect and recursive category/cause hierarchy |
| TreeView | `root: object` | recursive hierarchy |
| Wardley | `components: list` | `links`; positioned components and explicit links |
| Cynefin | `domains: list` | `transitions`; evidence-bearing items |
| Event Modeling fallback | `lanes: list` | lane frames and `relations` |
| ZenUML fallback | `participants: list`, `messages: list` | object participants and messages |
| Organization fallback | `root: object` | recursive reporting hierarchy |
| Data Lineage fallback | `datasets: list`, `relations: list` | optional `processes` |
| Railroad | `rules: list` | discriminated recursive expression AST |

### Common strict values

Provider numbers are finite JSON `int` or `float`; booleans, numeric strings, NaN, and Infinity are
rejected. A direct serializer may support `Decimal` internally without expanding the provider
contract. Strict integer fields reject booleans. Strict boolean fields do not coerce strings or
integers. Closed enums are checked case-insensitively when the serializer does so, but original
casing is preserved. State kinds, Class member visibility/kind/classifier and relation types, ER
keys/cardinalities, and every closed token listed below use the serializer's existing enum exactly.

Record fields remain optional where partial/legacy reconstruction requires it. This does not make
invalid semantics publishable: planners still fail closed for missing required values, conflicting
aliases, dangling endpoints, duplicate identities, cycles, or exhausted budgets. An unreadable or
missing label may become a documented placeholder only when the serializer explicitly supports it.

## Terminal and fallback contracts

### Sequence

Participants are legacy strings or objects with `id`, `label`, `bbox`, and `evidence_ids`. Messages
have `id`, `source`, `target`, `label`, `style`, `bbox`, and `evidence_ids`. `style` is exactly
`solid|dotted|open|dotted_open|cross`. Extra metadata remains in typed/review IR but participant
`text`, raw role/shape/direction, and message arrow hints never become emitted structure or OCR
labels.

`plan_sequence_records()` freezes source identity, source-ordered
`mmx_sequence_participant_N` emitted identities, ordered `generated-relation-N` Scene slots,
semantic/source/Mermaid 11.16 canvas text, endpoints, and real line/marker semantics. A raw message
ID is metadata, not Mermaid identity. Participant declarations, message endpoints, and generated
Scene elements share one mapping; record evidence stays on its element or relation. A legacy string
participant cannot carry record evidence.

Any duplicate participant, malformed record, unknown/null endpoint, unknown style, or resource
overflow fails the whole plan rather than dropping messages. Missing/`None`/exact-empty participant
labels use the source ID; message labels use `[unreadable]`. Whitespace-only, non-string,
control/format/surrogate, and overlong labels are rejected. Character-wise `#35;`/`#59;` preserves
literal `#`, `;`, and entity-like text. Source-only separators disable active tokens. The generated
Scene is `LR` and follows each style's actual Mermaid marker behavior.

Raw `title`/`description`/`acc_title`/`acc_description` passes an exact bounded, nonblank UTF-8 gate
before enrichment. Only absent/`None` and exact-empty omission are accepted exceptions. Initial and
repair IR store this validated raw snapshot, not derived `acc_*`, so accepted repairs regenerate
descriptions and angle-bracket compatibility warnings from the current participant plan.

### Mindmap

The recursive root/child record has `id`, `label`, `text`, `bbox`, `evidence_ids`, and `children`.
When `label` and `text` coexist, their whitespace-normalized semantics must match; exact-empty aliases
are omitted. If neither exists, the preorder slot remains and displays `[unreadable]`. Logical IDs
must be bounded exact UTF-8 strings but may repeat because they are provenance, not native grammar
identity.

`plan_mindmap_records()` iteratively freezes preorder `root`/`node_N`, parent, depth,
semantic/source/canvas text, and source budget. Malformed children, object reuse, depth/node overflow,
invalid terminals, or output beyond 50,000 UTF-16 units or 5,000 lines fails the whole hierarchy.
The root is a quoted circle, children are quoted rectangles, and generated Scene uses the same
roles/shapes. Parent-child relations are marker-less `containment` with `radial` direction, and child
evidence attaches to both child and relation. Raw role/shape/direction is ignored.

Quoted Mindmap terminals neutralize Markdown escapes, links, shape delimiters, directives, URLs,
callbacks, and style/control words with source-only zero-width separators. Named entities and
literal angles retain exact canvas text. Quotes, active asterisks/backticks/tildes, and numeric
entity-like spellings that Mermaid 11.16 cannot preserve use visible compatibility glyphs and a
warning. Scene and semantic OCR consume the same separator-free canvas projection.

The four raw accessibility fields pass the exact gate before enrichment. Because a Mindmap
accessibility directive becomes another root, resolved text is not persisted or emitted in native
source; it is recalculated from the raw snapshot when needed, and a limitation warning discloses the
source/SVG omission.

### Timeline

Each item has `id`, `time`, `period`, `label`, `events`, `bbox`, and `evidence_ids`. `time` and
`period` are aliases and must normalize equally if both exist. `label` is a single-event alias for
the first ordered `events[]` value. Exact-empty aliases are omitted; an item with no label retains one
`[unreadable]` slot. Whitespace-only, non-string, control/format/surrogate, overlong, and malformed
nested values are rejected rather than coerced or skipped.

`plan_timeline_records()` freezes provenance IDs, source-ordered `timeline_event_N` Scene IDs,
title/period/event semantic/source/canvas text, and source budget. Duplicate IDs, alias conflicts,
and visible-label/record/source overflow fail the entire Timeline. Each generated Scene item is an
`event` containing period canvas text, the record bbox/evidence, and `reading_direction=timeline`.
OCR includes title, period, and all ordered event labels, but excludes raw IDs, hidden metadata,
sentinels/entities, and accessibility metadata.

A generated zero-width sentinel and numeric encoding of every ASCII code point prevent Timeline
grammar from consuming `title`, `section`, comments, delimiters, or entity-like text. Mermaid 11.16
decodes exact quote/backslash/colon/hash/semicolon/entity/whitespace canvas text. Expanded output is
preflighted at 50,000 UTF-16 units and 5,000 lines. Timeline SVG does not materialize accessibility
directives, so raw fields are validated and retained but resolved values are not persisted; the
limitation is warned.

### Gantt

Sections contain task IDs, labels, status, start, end, duration, bbox, and evidence. Raw
`title`/`description`/`acc_title`/`acc_description`/`date_format` is validated before generic
enrichment as an exact built-in string with raw/normalized `MAX_TEXT_CHARS`, valid UTF-8, and no
control/format/surrogate/line-separator characters. Only absent/`None` and exact-empty omission are
exceptions. The record and accessibility plans are separate: explicit description fields remain
authoritative, and a repair rederives a description only when both description fields are absent.

Empty sections are omitted, and a plan with no renderable task is rejected. Missing/exact-empty
section and task labels become `Tasks` and section-local `Task N`. Status is a unique subset of
`active|crit|done|milestone`; `active` and `done` cannot coexist. Every task has exactly one of `end`
or `duration`. IDs are unique ASCII Gantt identifiers and reject `active`, `done`, `crit`,
`milestone`, `vert`, `__proto__`, case-insensitive `iconify`, and schedule-injecting `,`, `#`, `;`.

The supported numeric Day.js format subset is parsed strictly. `h`/`hh` requires `A`/`a`;
zero-width `Z`/`ZZ` and `S`/`SS` are rejected; only `SSS` is accepted. Seconds timestamp `X` is
rejected because Mermaid 11.16 uses inconsistent units. Milliseconds `x` is a canonical decimal
without leading zero and remains within ECMAScript Date maximum, including resolved ends after
duration and prior-only `after` chains. End is after start, except a milestone may be equal.
Durations use exact decimal-plus-unit syntax: fractional `ms`/`d`/`w`/`M`/`y` is rejected;
fractional `h`/`m`/`s` must map to an exact positive integer millisecond within runtime bounds;
exact zero is milestone-only. `after` references only unique earlier tasks, blocking forward or
partial resolution, cycles, and multi-target ambiguity. `after` start permits duration only;
`until` fails closed until relation attribution exists.

Generated Scene does not yet create dependency `SceneRelation` objects for validated `after`
schedules. Formats outside the numeric validation subset, including `MMM`, time zones, and partial
dates, require Direct Mermaid or review even if Mermaid itself supports them. Final SVG inspection
also requires finite positive width/height for every `class~=task` rectangle when runtime type is
`gantt`; zero-width rounding is render-invalid for typed and direct candidates.

Task canvas displays every `:`/`%` as `∶`/`％`; title/accessibility `<` displays as `‹`, with a
warning only for real visible substitution. Grammar/scanner-active tokens receive visually inert
separators. Scene/OCR removes them and includes only visible title/section/task canvas text, not
hidden task `text`, internal IDs, schedules, or accessibility metadata. Accessibility uses separate
SVG `<title>`/`<desc>` projection.

### State, Class, and ER

State validates state/kind records and transition endpoints/labels; Class validates classes,
members, relation types, classifiers, visibility, and cardinalities using the serializer's closed
sets. Their planners continue to enforce identities, references, and Mermaid semantics.

State's text plan revalidates exact strings, raw/normalized bounds, UTF-8, and Unicode categories.
Exact-empty node labels fall back to ID and exact-empty transition labels are omitted. Unicode
whitespace becomes one space. Quoted node quotes display as `″`; only renderer-active backslashes
become `∖`; safe punctuation/backslashes remain. A bounded linear scanner neutralizes active
Markdown/entities and bare email/`www` autolinks while preserving canvas text. Accessibility keeps
raw quote/backslash/Markdown/named entities but maps lossy numeric entities and `<` to `＆＃…`/`‹`.
Hidden pseudo-state labels are excluded from derived accessibility. Raw accessibility fields pass
the same gate before enrichment, and initial/repair candidates store a validated raw snapshot.

State source IDs in lexer/security-reserved namespaces or containing case-insensitive `iconify`
receive collision-free `mmx_state_id_…` emitted aliases after the full normalized namespace is
reserved. Typed identity/evidence remains unchanged; declarations, transitions, and Scene endpoints
share the alias.

ER requires `entities` and optionally `relationships`:

| Record | Validated fields |
| --- | --- |
| `entities[]` | `id`, `label`, `bbox`, `evidence_ids`, `attributes` |
| `attributes[]` | `type`, `name`, `keys`, `comment`, `bbox`, `evidence_ids` |
| `relationships[]` | `id`, `source`, `target`, `source_cardinality`, `target_cardinality`, `identifying`, `label`, `bbox`, `evidence_ids` |

`keys` is a list of `PK|FK|UK`; cardinality is
`one|only_one|zero_or_one|one_or_more|zero_or_more`; `identifying` is a strict boolean. Nested
scalars may be absent for partial reconstruction, but the serializer requires entity IDs,
attribute type/name/evidence, and complete relationship endpoints/cardinalities/identifying/
label/evidence. It never invents missing cardinality or emits a nodes-only partial ER.

`plan_er_records()` freezes source and collision-safe emitted identity, relation Scene slots, and
semantic/source/canvas text. Relationship roles are always one quoted terminal. ER grammar-specific
compatibility and source neutralization preserve semantic originals in typed/review IR. IDs
colliding with ER grammar/style/control/cardinality tokens, `__proto__`, or `iconify` map to
`mmx_er_id_N[_suffix]`, shared by relationship endpoints and generated Scene. Entity/relationship
evidence remains record-local; attribute evidence contributes only to actual canvas fields in
semantic OCR. Raw accessibility metadata passes an ER-specific exact gate before enrichment and is
stored as a raw snapshot so accepted repairs regenerate descriptions and warnings.

### Requirement and Block

Requirement canonical records expose:

- `requirements[]`: `id`, `requirement_id`, `text`, `label`, `type`, `risk`, `verify_method`,
  `bbox`, `evidence_ids`
- `elements[]`: `id`, `type`, `label`, `docref`, `bbox`, `evidence_ids`
- `relations[]`: `id`, `source`, `target`, `type`, `bbox`, `evidence_ids`

Closed Requirement tokens are:

- `type`: `requirement`, `functional`, `functional_requirement`, `interface`,
  `interface_requirement`, `performance`, `performance_requirement`, `physical`,
  `physical_requirement`, `design_constraint`
- `risk`: `low|medium|high`
- `verify_method`: `analysis|demonstration|inspection|test`
- relation `type`: `contains|copies|derives|satisfies|verifies|refines|traces`

Legacy `verifymethod` is validated post-response against the same set but is not prompted.
`relations[].label` remains typed compatibility metadata and is neither prompted nor emitted.

Block exposes `blocks[]` with `id`, `label`, `text`, `shape`, `bbox`, `evidence_ids` and `edges[]`
with `id`, `source`, `target`, `label`, `style`, `bidirectional`, `bbox`, `evidence_ids`. Closed
shapes are `rectangle|round|stadium|circle|diamond|hexagon|cylinder|subroutine`. `columns` is
prompted as `auto|integer`; nested validation accepts string/integer/null without coercion, while
the serializer resolves omission to `auto` and accepts only `auto` or a positive integer.

### Architecture, C4, Deployment, and Component

Architecture service records validate `id`, `label`, `name`, `icon`, `group`; groups validate
`id`, `label`, `icon`; edges validate `id`, `source`, `target`, `label`, `source_side`,
`target_side`, and strict `bidirectional`, plus shared bbox/evidence. The terminal plan then enforces
exact source IDs, collision-safe emitted IDs, flat membership, exact non-empty endpoints, and port
sides `L|R|T|B`. It never aliases a missing endpoint through `str(None)`.

Visible service text is non-empty `label`, then `name`, then source ID; group text is explicit label
or emitted group ID. Selected values pass exact-string, normalized-whitespace, bounds, and
control/format/surrogate gates. Native and Flowchart share semantic/source/canvas plans, and
Scene/OCR consumes only planned canvas text and record evidence—not raw `text`, relation labels, or
accessibility metadata. Raw accessibility is validated and stored as exact-empty-as-omitted.
Output is preflighted at 50,000 UTF-16 units and 5,000 lines. `bidirectional` is absent or exact
built-in boolean. Malformed/oversized/invalid-Unicode evidence is omitted atomically per record,
never iterated as a string or retained partially.

C4 requires `elements` and optionally `level`, `boundaries`, and `relations`. Closed `level` values
are `context|container|component`. Element `kind` values are:

`person`, `external_person`, `system`, `external_system`, `database`, `external_database`, `queue`,
`external_queue`, `container`, `container_database`, `container_queue`, `component`,
`component_database`, `component_queue`.

Legacy element `type` is validated against `kind` but not prompted. Relations expose endpoints,
labels, technology, strict boolean `bidirectional`, and `L|R|T|B` port sides. Boundary `type` is an
open string so an unknown diagnostic native-C4 boundary does not block the safe
Architecture/Flowchart fallback.

Automatic publication consumes element ID, `label`/`name`, kind-based icon, boundary membership,
boundary ID/label, and relation endpoints/ports/direction. Description, technology, relation label,
relation bbox, and exact boundary notation remain typed/review metadata and do not become fallback
labels or attribution geometry. `serialize_c4_native` may represent more metadata but is a trusted
diagnostic API, not an automatic publication or quality-evaluation path. Public boundaries also
require exact non-empty string endpoints and do not launder falsey non-text labels. The shared
C4-to-Architecture plan owns non-emptiness, collisions, references, and resource limits.

Deployment requires `nodes`; Component requires `components`. Canonical optional collections are
Deployment `artifacts`, `groups`, `links` and Component `interfaces`, `groups`, `dependencies`.
Legacy `edges` is validated but not prompted and is used only when the canonical relation key is
absent; an explicitly empty canonical collection wins.

Primary and secondary records flatten, in stable order, into one Architecture service list.
Service-like fields are `id`, `label`, `name`, `icon`, `group`, bbox, and evidence; relations use
exact endpoints, strict boolean `bidirectional`, and `L|R|T|B`. Containment stereotypes and
provided/required interface notation remain extra metadata. Relation labels, IDs, and bboxes are
not shown automatically. Icons accept strings; known `cloud|database|disk|internet|server` are
case-insensitive and other service icons degrade to `server`; group icons remain metadata/default.
Architecture may use icons and ports, while a runtime Flowchart retry preserves only IDs, labels,
membership, endpoints, and unlabeled bidirectional topology. Raw metadata drives regenerated
Deployment/Component accessibility after repair or fallback.

### Use-case

Actors and use cases expose `id`, `label`, `name`, bbox, and evidence; relations expose `id`, exact
`source`/`target`, open-string `type` and `label`, bbox, and evidence. One bounded planner allocates
a shared collision-safe namespace. Labels resolve as `label`, then `name`, then source ID. Actors
use a portable stadium proxy and use cases a round node; these are Mermaid 11.16 Flowchart proxies,
not UML actor glyphs or native Use-case notation. Groups/system boundaries remain extra metadata and
are deliberately suppressed.

Relation `type`, when non-empty, is the edge label; otherwise `label` is used; otherwise the edge is
unlabeled. Relation IDs, `bidirectional`, arrow hints, style, and semantic extras do not affect the
automatic one-way Flowchart. Generated Scene uses identical order, labels, endpoints, and evidence.
Node bboxes remain source positions, not Mermaid layout instructions; relation bboxes stay metadata.
The planner enforces non-empty roots, cross-family IDs, normalization and secondary `usecase_`
collisions, references, and caps. Default direction is `LR`; unsupported input becomes `TB`.

### Pie, XY, and Quadrant

Pie slices expose strict string `label`, finite numeric `value`, bbox, and evidence; `show_data` is
strict boolean. XY axes expose labels, categories or numeric bounds; series use closed `line|bar`,
ordered values or explicit numeric points, with bbox/evidence. Quadrant axes expose `low`/`high`;
`quadrants` is a four-string list or object with `quadrant-1` through `quadrant-4`; points expose
label, normalized x/y, bbox, and evidence.

Nested models validate types only. Serializers enforce Pie non-empty unique labels, nonnegative
values, and positive total; XY axis mode/bounds, y range, series form/length; and Quadrant unique
labels, `[0,1]`, aliases, and completeness. Valid but native-lossy binary64, geometry, or visibility
selects exact-value Flowchart instead of becoming a completeness error.

`PiePlan` supports at most 12 native slices and requires zero-or-normal binary64 round trips,
JavaScript left-to-right finite positive total, at least 1% visibility per positive slice, finite
centroids, and exact JavaScript `String(value)` under `show_data=true`. Zero slices are legend-only.
Valid non-native input and native runtime rejection receive one same-slot, independently validated,
edge-free `flowchart TB` with at most 256 exact-value cells. The structured table-and-description
fallback for missing/unreadable values remains future work.

Pie evidence is record-local to `pie_slice_N`. Native Scene uses sectors/centroids and zero bboxes
for zero slices; Flowchart uses zero-geometry cells; neither invents relations/groups. Automatic
numeric publication requires candidate-authorized slice-local OCR/vector full-record binding inside
non-overlapping slice bboxes plus global numeric exactness. Missing suffix/value/slice,
shared/ambiguous evidence, invalid geometry, or exhausted work requires review. Typed values or IDs
alone are insufficient. Explicit title/accessibility also needs independent exact spatial evidence
or an exact initial-input `user_edit`; an engine-emitted edit cannot self-authorize. Deterministic
default accessibility is exempt. Both terminals share 50,000 UTF-16 units and 5,000 lines.

`XYPlan` freezes axes, series, points, fixed-decimal values, record evidence, terminal text, and
native geometry. Native requires exact zero-or-normal binary64, positive normal finite axis spans,
a bounded renderer x-loop with exact count/endpoints/progress, visible lines of at least two points,
positive-height bars, and at most 10 series. Non-uniform x, dropped/stalled loops, duplicate lines,
multiple overlapping bars, or bars at y minimum select a disconnected exact-value Flowchart with
at most 256 points and no inferred edges. Native runtime rejection receives the same complete retry.
Native Scene includes axes/categories and hidden-text geometry; OCR counts only visible title,
axes, categories. Fallback Scene/OCR follows emitted title/axis/category/cells exactly with no
relations/groups. Publication requires record-local full label/category/value/x-y evidence and
global numeric exactness. Swaps, shared observations, invalid/missing bboxes, or unproven explicit
metadata require review.

`QuadrantPlan` freezes two axes, supplied slots, up to 256 points, exact decimals, terminal text,
and geometry; reused axis/point objects are rejected. Native also checks finite, distinct,
non-occluded point/label/slot/axis/title placement on the pinned 500×500 canvas within 100,000
comparisons. Duplicate/near points, float collapse, subnormal values, clipping, or occlusion select
exact Flowchart title/axis/slot/`label · x X, y Y` cells without edges or inferred position.
Native Scene contains four axis endpoints, `(x, 1-y)` circles, and four region groups but no axis
line, membership, or connector; fallback uses zero geometry. X-axis source geometry must be
horizontal/bottom and y-axis vertical/left. Supplied slot labels require independent exact evidence
in the corresponding full-crop quadrant; off-center plots may conservatively require review.
Explicit metadata evidence proves content existence, not immutable target role, so best-effort
records a limitation warning and strict validation withholds automatic publication.

### Sankey, Radar, Treemap, and Venn

Sankey prompts canonical `flows`, not legacy `links`; Radar prompts `dimensions`, not legacy `axes`.
Aliases are input-only and do not satisfy required roots. Treemap and Venn `name` aliases are typed
compatibility metadata but are not prompted or copied into canonical keys.

Sankey records contain node IDs/labels and explicit weighted flows with endpoints, value, bbox, and
evidence. Radar dimensions contain ID/label and series contain ID/label/ordered values. `ticks` is a
strict integer, `show_legend` strict boolean, and `graticule` exactly `circle|polygon`. Treemap is a
recursive ID/label/value/children hierarchy. Venn contains set ID/label/value and intersection
ID/member sets/label/value. All numbers use the strict finite JSON contract. Serializers own minimum
counts, semantic requiredness, uniqueness, references, aligned series, bounds, positivity,
hierarchy cycles/depth, and Radar `ticks <= 100`. Negative Radar domains, nonpositive/cyclic Sankey,
internal-valued Treemap, and partially sized Venn can select documented Flowchart fallbacks instead
of being rejected structurally.

Radar requires 3–256 dimensions, aligned series, global Scene/point limits, at most 12 native series,
at most 256 fallback points, and 50,000 UTF-16 units/5,000 lines. Native values/bounds require exact
zero-or-normal binary64, positive finite effective span, and finite radii. Negative, subnormal,
overflowed, precision-losing, or zero/non-finite spans select exact Flowchart. Grammar-reserved IDs
receive reserved-safe suffixes across native and fallback namespaces. Native rejection gets only one
bounded same-slot fallback.

Radar native Scene uses normalized radial axes/points, point-derived series envelopes, and closed
curve relations; fallback uses zero-geometry `TB` title, conditionally labeled series groups, value
cells, and no relations. Native generated-node attribution covers axes/series, not derived points.
Record-owner OCR/vector binding must prove dimension labels and spatial order plus series labels and
ordered values, together with global numeric exactness. Cross-owner reuse, same-bbox contradictions,
overlap/invalid geometry, missing typed plan, or bounded work overflow requires review. Visible title
and non-derived accessibility require independent record-disjoint source evidence or approved
initial user edits.

Sankey automatic publication binds each planned exact `value_text` to a candidate-authorized
OCR/vector observation fully contained in non-overlapping, positive-area flow bboxes and also
requires global numeric exactness. Cross-flow evidence reuse, same-bbox conflict, weight swap,
invalid geometry, or bounded-work overflow keeps native, same-slot Flowchart, and repairs in review.
Direct/untyped Sankey has no flow-owner binding and is review-only. Raw accessibility metadata uses
exact built-in strings, `MAX_TEXT_CHARS`, UTF-8, and no `Cc`/`Cf`/`Zl`/`Zp`; `None` is absent and
exact `""` is omission-compatible. Native Sankey emits no title/description. Flowchart metadata
requires independent, non-data, non-overlapping source evidence or an approved initial edit; hidden
shadowed fields, deterministic defaults, and experimental notices are exempt.

Treemap planners allocate reserved-safe preorder identities and atomically omit malformed evidence
per record. Publication requires finite positive in-image nested bboxes, strict child containment,
non-overlapping sibling interiors, internal-owner text outside direct children, and owner-local
reading-order OCR/vector proof of exact label and optional fixed-decimal value. Cross-owner reuse,
same-bbox ambiguity, duplicate owner references, missing geometry, or bounded work makes the whole
binding unavailable. Generated Scene remains zero-geometry even when source geometry authorizes
attribution. Native fallback and repair recompute the same gate.

Treemap raw metadata is validated before enrichment at all typed public entries as exact built-in
strings under raw/normalized `MAX_TEXT_CHARS`, UTF-8, and no raw `Cc`/`Cf`/`Zl`/`Zp`; only
`None`/absent and exact-empty omission are exceptions. Terminal-effective non-derived title and
accessibility roles require independent node-disjoint evidence or approved initial edits.
Deterministic defaults, shadowed legacy fields, and pipeline-added notices are exempt; a notice-only
explicit description fails closed. Numeric tokens in selected metadata proof are excluded from data
occurrence matching. Raw Direct Mermaid lacks typed metadata and remains review-only without a typed
plan.

Venn native requires positive normal binary64-safe observed areas, maximum/minimum positive ratio at
most `200:1`, no exact containment, and all pairwise intersections for every union of at least three
sets. Unsafe/missing areas or pairs select exact-value Flowchart without inventing values; values
beyond observed containment are invalid. Flowchart membership is capped at 500 edges; native area
notation does not inherit that fallback-only cap. Both terminals use 50,000 characters and 5,000
lines.

Every Venn set/intersection needs a finite positive in-image bbox that exactly matches a cited,
candidate-authorized contour and separate in-area OCR/vector full-record proof of existing label and
explicit value. An intersection with neither text field has no owner proof and requires review.
Owners are injective by evidence ID and normalized text+bbox. Membership geometry requires each
intersection inside all declared sets, not fully inside undeclared sets, and every higher-order
intersection inside explicit strict subsets; equal containment is allowed. Limits are 20,000
references, 50,000 text records, 1,000,000 characters, 100,000 OCR tokens, and 100,000 shared
spatial operations. Native, same-slot Flowchart, and repair recompute local binding, membership, and
global exactness. Direct/untyped Venn is review-only.

Venn raw metadata uses the same early exact-string/Unicode gate as Treemap. Native requires proof
only for explicit visible title; Flowchart requires terminal-effective non-derived accessibility
roles. Unsupported or shadowed fields, defaults, and pipeline-added notice suffixes are exempt, but
explicit matching text under `strict` is ordinary source text. Metadata owners need source evidence
outside all data areas or approved initial edits; reused/ambiguous/overlapping evidence or combined
budget exhaustion requires review. OCR/vector metadata numbers, but not `user_edit` numbers, are
removed from data occurrence matching. Typed `serialize_venn()` receives this gate; Raw Direct
Mermaid does not and remains review-only without typed ownership.

### Journey, Kanban, and GitGraph

Journey sections expose title and nested tasks with ID, label, strict integer score, actors, bbox,
and evidence. Kanban columns/cards expose ID, label, and explicit `column_id`. GitGraph requires
exact `initial_branch: main` and ordered operations. Direction is `LR|TB|BT`; operation type and
commit type `NORMAL|REVERSE|HIGHLIGHT` are closed case-insensitive values; `order` is a strict
integer. Legacy aliases (`label`/`text`, `title`, branch `id`, commit `style`) are typed but not
prompted.

Planners enforce non-empty Journey sections/tasks, score `1..5`, unique actors; Kanban ID
normalization and column references; and GitGraph branch-head replay, commit/merge uniqueness, and
merge validity. Each family has an absolute 2,000-record traversal cap, plus the normal
50,000-character/5,000-line output cap.

Journey uses a Timeline fallback and preserves score/actors in visible event text and OCR; numeric
publication still needs independent source OCR/vector evidence. Kanban shares emitted IDs and
containment across native, fallback, and Scene. GitGraph shares branch-head plans across native and
Flowchart retry. GitGraph preserves quote/backslash and punctuation, substituting `‹`/`›` only when
the runtime loses angle brackets. Journey substitutes `∶` and `＆`/`＃` for Timeline-active colon and
entities; Kanban native uses `″`/`ˋ`; planning Flowchart fallbacks use `″`/`∖`. Conflicting canonical
and compatibility aliases fail closed. Kanban uses a strict-reserved-safe `kanban_` namespace.
GitGraph rejects irrelevant known fields per operation instead of silently discarding them.

### Packet, Ishikawa, and TreeView

Packet fields expose `id`, strict integer `start`/`end`, label, bbox, and evidence. Ishikawa exposes
a childless effect plus recursive categories/causes. TreeView is recursive root/children. `name` is
an input-only label alias; coexisting aliases must normalize equally. An Ishikawa effect with
children is rejected. Planners enforce non-empty collections, bit-range ordering/overlap/gaps,
identities, cycles/object reuse, required children, and depth/node/source budgets.

Native, Flowchart fallback, and Scene share one source record, label, identity, and parent plan.
Fallback/Scene IDs use reserved-safe `packet_field_`, `ishikawa_node_`, and `treeview_node_`.
Packet Scene shows independent `LR` fields and invents no relation. Ishikawa/TreeView use planned
containment and original bbox/evidence. Planner failure makes evaluation unavailable rather than
producing partial attribution.

### Wardley and Cynefin

Wardley components expose `id`, `label`, strict finite non-boolean `x`/`y`, strict boolean `anchor`,
bbox, and evidence; links expose endpoints, label, bbox, and evidence. Canonical roots cannot be
replaced by aliases. The planner enforces `[0,1]`, IDs/labels, endpoints, self/duplicates, at most
500 components and 500 links, and 50,000 characters/5,000 lines. It freezes source-ordered
`wardley_component_N`, `wardley_link_N`, fallback labels, and endpoints. Native emits `[y, x]` for
Mermaid `[visibility, evolution]`; Scene uses `(x, 1-y)`. Runtime `->` is marker-less and therefore
undirected in Scene. A same-slot `flowchart LR` fallback preserves nodes and undirected links but
warns that coordinates, axes, and anchor semantics were lost.

Cynefin canonical domain names are exactly
`complex|complicated|clear|chaotic|confusion`, checked case-insensitively without rewriting. Items
are prompted as evidence-bearing objects; legacy strings remain input-compatible but cannot carry
record evidence. The planner enforces non-empty and unique domains/items, transition references,
self/duplicates, at most 500 items and 500 transitions, and source budgets.

Mermaid 11.16 native Cynefin always adds five domains and practice/response template text.
Scene/OCR identifies these as unprovenanced runtime templates; `confusion` shows only three items
plus `+N more`. Native Cynefin always requires review because no source-provenance contract exists
for the template. On native rejection, one same-slot `flowchart LR` emits only supplied domains,
all explicit items, and explicit directed transitions—never fixed templates, `+N more`, or
membership connectors. Fallback Scene uses zero geometry and `LR`, counts domain labels once, keeps
record-local evidence, and warns about lost spatial meaning. It may publish through ordinary gates,
but does not release the native review hold.

### Event Modeling, ZenUML, Organization, Data Lineage, and Railroad

Event Modeling lane records expose `id`, `label`, bbox/evidence, and frames. Frames expose `id`,
`type`, `label`, `time`, bbox/evidence. Relations expose endpoints and label. Frame `type` is
`command|event|readmodel|processor|ui|unknown`. Aliases such as `swimlanes`, `nodes`, `name`,
`timestamp`, `cmd`, and `evt` are not prompted.

ZenUML object participants expose ID/label/bbox/evidence and messages expose endpoints/label/
bbox/evidence. Legacy string participants remain input-compatible but are not prompted and have no
record evidence. Raw IDs/styles/text extras do not become fallback structure. Shared planners own
non-empty labels, collisions, endpoints, and 50,000-character/5,000-line limits.

Organization requires recursive `root`; legacy `name` is input-only. Data Lineage requires
`datasets` and `relations`, optionally `processes`; dataset/process records expose ID/label and
relation records endpoints/label, all with bbox/evidence. Organization missing IDs receive preorder
`node_N`; Data Lineage missing labels use validated source IDs. Planners own collisions,
relationships, self/duplicates, and source budgets. Organization relations derive only from
children. Data Lineage edge-label `|`, `;`, `()`, `[]`, `{}`, and `@` use visible compatibility
glyphs shared by warnings, Scene, and OCR.

Railroad rules expose `name`, `definition`, bbox, and evidence. `definition` is a recursively
discriminated object:

| `type` | Payload |
| --- | --- |
| `terminal` | `value: string` |
| `nonterminal` | `name: string` |
| `special` | `text: string` |
| `sequence` | `elements: expression[]` |
| `choice` | `alternatives: expression[]` |
| `optional`, `one_or_more`, `zero_or_more` | `element: expression` |

Wrong scalar/container kinds fail nested validation. Variant-foreign fields remain extra metadata.
The serializer requires non-empty rules and expression containers, unique names, resolved
nonterminal references, maximum depth 20, at most 500 rules and 500 expressions, and 50,000
characters/5,000 lines. Names normalize to `[A-Za-z_][A-Za-z0-9_-]{0,127}`; visible text is at most
500 characters per field.

Canonical Railroad canvas maps ASCII angles to `〈`/`〉`, every ASCII `#` to `＃`, entity-like `&`
prefixes to `＆`, and NFKC quote/backslash hazards to `″`/`∖`, with warnings and semantic originals
retained. Source-only separators disable active/preprocessor tokens, including
`style...:#...;`/`classDef...:#...;`; raw and NFKC source are both strictly scanned. Unsafe names,
case-folded expression-word names (`terminal`, `nonterminal`, `special`, `sequence`, `choice`,
`optional`, `oneOrMore`, `zeroOrMore`), `railroad-beta`, lowercase case-folded `title*`, and names
containing active `style`/`classDef` substrings map to collision-safe `rrmapped_N[_suffix]`.
Original names remain in typed IR and nonterminal labels.

## Prompt selection boundary

Provider-visible text has its own character budget. If the system instruction, enabled contracts,
view manifest, empty selection section, and Marker 1.10.2 canonical response-schema reserve already
exceed it, the provider is not called. After user-edit and trusted-connector evidence, at least 25%
of remaining evidence slots are reserved round-robin for arrow, line, contour, and vector evidence;
trusted labels and global priority backfill the rest.

Evidence and OCR roots must be exact plain lists. One bounded shallow snapshot is reused for
preflight and canonical selection. Evidence strings have an 8,000,000-character pre-copy hard cap.
Oversized records are skipped by allocation-free JSON-escape length calculation. The configured OCR
prefix is preflighted for plain strings and an 8,000,000-character aggregate; raw strings whose
lower bound already exceeds remaining prompt space are skipped without escape scanning. Included
records are complete compact-JSON items only.

Every exact `VisualEvidence` scalar and nested source-block list is treated as mutable. The snapshot
reads at most one reference beyond the limit, validates bbox/score shape/type/finiteness and string
type/length/UTF-8 before `model_dump()`, and canonicalizes only the detached payload. Trusted
label/connector sets become bounded immutable snapshots reused throughout selection.

The selection manifest records input, inspected, and included counts, schema reserve, and selection
profile. Candidate warnings mention omissions, but `ReconstructionResult.prompt_budget_notices` is
the source of truth even for prediction-only responses with no candidate. Sidecar `manifest.json`
and Marker internal metadata preserve the same notice. `SourceContext` evidence/OCR order and data
are not mutated. Provider response-token limits, image encoding, and SDK wire overhead are outside
this bounded text-request contract.

For `flowchart` and `generic_network`, typed `nodes[].id` must reuse the corresponding same-response
`scene_ir.elements[].id` byte-for-byte. Renaming, normalization, or new IDs are prohibited.
Typed-node `evidence_ids` must cite supplied Prior evidence and share at least one with the
same-response Scene element; response-created IDs are not authority. The pipeline intersects
non-conflicting call-time evidence with the private prompt-selected ID set and excludes late or
colliding IDs. The provider cannot set this private set or prompt notice. Fusion still verifies
prior bbox/text, same-owner Scene links, unique vector/geometry IoU mappings, and spatially aligned
contour provenance; prompt compliance alone is not trusted.

Marker 1.10.2 stock Ollama copies only top-level schema `properties` and `required`, dropping
`$defs`. When detected, the adapter passes a schema-only `EngineObservation` subclass with local
`#/$defs/*` references recursively inlined. External, recursive, sibling references and schemas
above 65,536 characters are rejected. Every response is then validated as the original
`EngineObservation`, independent of provider.

## Input budgets

VLM and fixture inputs are untrusted. Typed IR is detached by a hook-free iterative walker that
accepts only exact built-in `dict`, `list`, `tuple`, `str`, number, boolean, and null. Limits apply
simultaneously:

- depth: 64
- total items: 100,000
- one string field: 50,000 characters
- aggregate UTF-8 text, including keys and repeated aliases: 1,000,000 bytes
- compact escaped JSON: 4,000,000 bytes per candidate
- all typed-candidate JSON in one observation: 8,000,000 bytes
- fusion: 64 unique candidates and 8,000,000 bytes globally

Tuples normalize to lists. Cycles, container subclasses, non-finite numbers, and values outside the
JavaScript safe-integer range fail before serialization. Candidate envelopes have exactly the three
public fields `diagram_type`, `ir`, and `confidence`; field count is checked before exact-string
names are copied. Validation errors never interpolate hostile input.

Known semantic records allow exactly 256 evidence references. Boundary values survive into Scene
and publication; post-construction mutation to 257 or more is rejected at canonical-key, fusion,
pipeline, and sidecar boundaries. Observation candidate/evidence/warning counts, Scene
element/relation/group counts, polygon/polyline points, IDs, bboxes, and finite confidence have
separate model limits. `NaN`, infinity, and out-of-range confidence never reach sidecars, which use
`allow_nan=false`.

Across each retained `VisualEvidence` collection, `source_block_ids` is limited to 20,000 logical
occurrences and 8,000,000 Python string characters, counting duplicates. An independent
8,000,000-character cap covers all evidence `id`, `kind`, `text`, `font_weight`, and source-block
IDs. Exact boundaries pass; `+1` atomically isolates an initial/custom-engine collection,
reconstruction-global new-ID batch, or fusion input/output. Snapshots use exact public fields and
exact nested lists through built-in access, not live `model_dump()` or subclass hooks. Final result,
publication/Markdown, sidecar, output, Marker OCR, Review provenance, standalone Structured VLM,
and evaluation prediction ingress share the boundary. Evaluation preserves prediction `0.1`'s
100,000-record/64 MiB artifact capacity while retaining the same source-block occurrence and
character limits.

Canonical candidate keys are SHA-256 digests of bounded snapshots, not multi-megabyte IR values.
Non-JSON values such as sets and bytes fail before deduplication or private lookup. Current payloads
are re-snapshotted and revalidated after mutable plug-in/repair changes, at fusion, after every engine
response, and before sinks. One invalid component becomes `CandidateFailure`; other candidates and
the document continue. Sidecars replace live selected/alternative IR with safe shallow snapshots
before model dump, JSON encoding, or whole-result deep copy.

## Evaluation Scene adapters

`candidate_scene.py` converts serializer output into the `DiagramSceneIR` structure actually
emitted for provenance and structural scoring. Adapters cover Flow/UML/Architecture/charts,
Sequence/ZenUML, Mindmap/Treemap/TreeView/Organization, Timeline/Journey/Kanban, Event Modeling,
Ishikawa, Wardley/Cynefin, Data Lineage, Railroad, and Venn. A missing adapter never guesses
structure; metrics become `unavailable`.

Serializer-implied relationships such as hierarchy children, Kanban column-card membership, and
Venn set-intersection membership become deterministic containment relations. Typed evidence IDs
remain attached to emitted nodes and relations, so Extended generated-node attribution evaluates
candidate structure rather than reusing the source Scene. Fallbacks that cannot reproduce source
layout use zero geometry and do not fabricate layout similarity. Requested semantic type remains
while emitted topology follows the real fallback.

Unlabeled Flowchart/Generic Network, Swimlane/BPMN, and Mindmap nodes display `[unreadable]`, not
internal IDs. Sequence shares `mmx_sequence_participant_N`, planned messages, canvas text, markers,
and record-local evidence exactly; unresolved messages fail the whole Scene. Event Modeling and
ZenUML retain requested type while using real Flowchart/Sequence identities, `LR`, end-arrow
relations, zero geometry, and no invented lane evidence. Organization/Data Lineage use frozen
`treeview_node_*`, `data_lineage_dataset_*`, `data_lineage_process_*`,
`organization_relation_N`, and `data_lineage_relation_N`; Organization child evidence attaches to
child and containment, Lineage relation evidence only to its `data_flow`. They create no groups or
source-layout claims.

Railroad uses frozen `railroad_rule_*`, preorder `railroad_expression_N`, actual
`native_name =`/leaf compatibility text, marker-less containment only, `LR`, zero geometry, and no
invented nonterminal-reference connector. Direct Railroad Scene accepts only null/omitted or string
list evidence. Raw IDs, roles, shapes, styles, source bboxes, and unsupported extras remain sidecar
IR rather than Scene/OCR structure.

Flowchart/Generic Network ID harmonization happens before the adapter and atomically rewrites only
`nodes[].id`, `edges[].source`/`target`, and `groups[].member_ids` when a full injective mapping to
fused Scene IDs exists. Any ambiguity, dangling reference, or collision leaves every field
unchanged. `candidate_scene.py` consumes this authority but never creates it. Candidates already in
the fused namespace create no remap sidecar, and prompt compliance alone grants no authority.

Nested Swimlane/BPMN lanes, hierarchy children, software/chart/planning/special IR, and Direct
Mermaid do not support this ID harmonization. The existence of a Scene adapter does not imply that
nested references can be rewritten safely.

The public Marker response envelope remains `TypedIRCandidate.ir: dict`. Nested contracts therefore
cover all registered roots and records but are not one provider-visible, diagram-discriminated JSON
Schema, and the generic envelope reserve is unchanged. This is only a partial mitigation of
`ARCH-001`. Fully streaming JSON ingestion is also future isolation work.

When adding a type, update `ALL_TYPES`, `TYPED_IR_CONTRACTS`, its serializer, its evaluation Scene
adapter, and contract/serialization tests together.
