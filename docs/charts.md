# Chart Serializers and Numeric Safety

Chart typed IR never interpolates a value that OCR/VLM could not read. Numbers at the Structured VLM
boundary are strict finite JSON `int`/`float` values—booleans and numeric strings are not accepted—and an
invalid value is rejected during candidate validation. Direct serializer APIs for Pie, XY, Quadrant,
Sankey, Radar, and Treemap also accept `Decimal`, though it is not part of the provider-response contract.
The direct Venn API retains its existing `int`/`float` contract. Every API raises `SerializationError` for
NaN/Infinity, unknown endpoints, inconsistent series lengths, or invalid axis ranges.

| Type | Native conditions | Fallback |
| --- | --- | --- |
| Pie | Unique labels, nonnegative slices, positive total, at most 12 slices, zero-or-normal binary64 and equivalent 1% visibility/`showData` display | Edgeless exact-value Flowchart with at most 256 slices |
| XY | Matching category/value lengths, bounded exact numeric grid, visible line/bar, zero-or-normal binary64 axes/values, at most 10 series | Edgeless title/axis/category/exact-value Flowchart with at most 256 points |
| Quadrant | Low/high labels for both axes, exact `[0,1]` coordinates for at most 256 points, zero-or-normal binary64 and no point/text collision or clipping on the pinned 500×500 canvas | Edgeless Flowchart containing only title/axis/quadrant/`label · x X, y Y` exact cells |
| Sankey | Positive weighted DAG, every node participates, unique native-safe labels | Flowchart with exact weight labels |
| Radar | At least three dimensions, equal series lengths, consistent bounds, at most 12 series, nonnegative zero-or-normal binary64 domain and finite positive renderer span | Edgeless exact-value tabular Flowchart with at most 256 points |
| Treemap | Explicit positive value for every hierarchy leaf, no internal value, reproducible binary64/display totals | Value-label hierarchy for internal-node values, unsafe numbers, or native runtime failure |
| Venn | Every area is positive and normal-binary64-safe, largest-set/smallest-area ratio at most `200:1`, and every pair in a higher-order union is explicit | Set/intersection graph that invents no numbers for zero, unsafe, missing, exact-containment, visibility-risk, or missing-pair cases |

## Core Chart Structured Extraction

Pie, XY, and Quadrant use strict nested contracts shared by the provider prompt and response
post-validation rather than root-only JSON.

| Type | Nested contract | Semantic conditions decided by the serializer |
| --- | --- | --- |
| Pie | `label`, `value`, bbox/evidence in `slices[]`; strict boolean `show_data` | Nonempty slices, unique labels, nonnegative values, positive total |
| XY | `x_axis`/`y_axis`; `kind: line\|bar`, `values`, and `points` with point `x`/`y` in `series[]`; bbox/evidence on every record | Categorical and numeric-x modes are mutually exclusive; min < max; exactly one of values/points; category length or exact uniform numeric grid; every y lies within the declared y-axis range. `label`/`name` is rejected because Mermaid 11.16 has no strict-safe series-label syntax |
| Quadrant | Axis `low`/`high`; `quadrants: string[4]\|{quadrant-1:string,quadrant-2:string,quadrant-3:string,quadrant-4:string}`; point `label`, `x`, `y`, and bbox/evidence | Nonempty unique point labels and coordinates in `[0,1]`; quadrant lists contain exactly four entries, while objects accept a subset of canonical `quadrant-1`–`quadrant-4` or compatibility keys `1`–`4`, rejecting alias conflicts for the same slot |

The root container is required for all three contracts, but individual record fields are optional to allow
partial extraction. The serializer decides completeness and Mermaid representability; failure is isolated
to the candidate. If the native renderer cannot show source values/structure without loss, or native runtime
validation fails, Pie, XY, and Quadrant revalidate an exact-value Flowchart in the same candidate slot.

After strict validation, each record's bbox/evidence remains in typed IR and review sidecars. All three types
connect that evidence to generated Scene attribution and record-local label/value validation. Quadrant-slot
labels have no independent evidence field in the typed schema, so evidence is neither synthesized nor
inherited from axes or points. The common accessibility root and unregistered extra metadata are retained in
the original dictionary, but numbers there are not chart-data evidence that can fill a missing
slice/axis/point value.

### Pie Terminal Plan

The Pie serializer, generated Scene, and semantic OCR share a `PiePlan` validated once by
`plan_pie_records()`. The plan fixes each source slice record, `pie_slice_N` Scene identity, exact
fixed-decimal value, record-local evidence, and terminal-specific source/canvas labels. Slices must be
nonnegative, and the exact decimal total must be positive. Native `pie` accepts at most 12 slices. Every value
and JavaScript left-to-right total must round-trip exactly as zero-or-normal binary64 and produce a finite
positive total and finite centroid. Because Mermaid 11.16 hides sector/percentage output for a positive slice
below 1%, native is selected only when every positive slice is at least 1%. A zero slice is allowed as a
native record that has only a legend entry, without sector or percentage.

The native canvas shows every legend and the rounded percentage of every visible positive slice. With
`show_data=true`, each legend also receives `[value]` in JavaScript `String(value)` form, so native is allowed
only when that string equals the exact source decimal. If this condition, the 12-slice one-color-per-slice
palette cap, or the binary64/geometry conditions fail, the serializer uses disconnected `flowchart TB` with
at most 256 `label: exact-value` rectangles. The fallback invents neither sector size nor edges between
slices. If native `CandidateValidator` rejects at the parse/render/SVG/type gate, the same-slot Flowchart is
fully revalidated once without consuming another candidate. Both terminals pass preflight against 50,000
UTF-16 code units—the same measure as JavaScript `text.length` in Mermaid—and 5,000 lines.

Native Scene creates one `sector` element per slice. A positive slice uses the normalized centroid at the
renderer percentage-label radius; a zero slice has a zero bbox. Direction is `radial`, with no relations or
groups. Element text is the actual legend text and cites record-local evidence unchanged. Native semantic
OCR counts the visible title, every legend, and percentages for visible positive slices, but not
accessibility metadata. Flowchart Scene contains only zero-geometry rectangular cells in `TB` order, with
no relation/group; OCR counts only exact `label: value` cells. A native-only canvas title is not copied into
fallback.

Quotes and backslashes in slice labels are escaped in native source but preserved on canvas. Scanner/entity-
active directives, URL schemes, callbacks, CSS/icon tokens, `%%`, `//`, `<`, `&`, `#`, and statement
separators receive a zero-width separator in source only. In a native title, Mermaid 11.16 cannot preserve
quotes, backslashes, angles, hashes, and semicolons, so they become visible compatibility glyphs. Flowchart
labels similarly use visible glyphs for quotes, backslashes, angles, and hashes, plus source-only separators.
Unicode whitespace is fixed to one ASCII space. Warnings disclose canvas-visible substitutions, while
semantic originals remain in typed IR/review metadata.

### XY Terminal Plan

The XY serializer, generated Scene, and semantic OCR share one bounded `XYPlan` from
`plan_xychart_records()`. It fixes source records for axes, series, and explicit points; deterministic Scene
IDs; fixed-decimal x/y values; record-local evidence; and terminal-specific source/canvas text. Categorical
mode binds every value to category text. Numeric mode preserves axis bounds and either ordered values or
explicit x/y. Valid nonuniform explicit x values are neither failed nor rebuilt as a uniform grid; exact
fallback cells retain the original x/y.

Native `xychart-beta` is used only when axis bounds, y values, and explicit x values round-trip exactly as
zero or normal binary64, and declared numeric-axis spans are positive, normal, and finite. Before runtime,
numeric x-axis iteration models Mermaid 11.16's `for (x = min; x <= max; x += step)` with an input-length+1
limit and verifies that every step strictly advances and produces the exact count, starting coordinate, and
ending coordinate. This closes both the case where the last point disappears when ten values are placed on
`[0,1]` and the infinite-loop risk where floating-point addition near `2^53` stops advancing.

A line additionally needs at least two points to form a visible segment. Fallback is selected when identical
line paths overlap completely, two or more bar series share the same x/width and obscure one another, or a
bar has zero height at the y-axis minimum. An eleventh series beyond the pinned palette also falls back. A
native-ineligible input uses disconnected `flowchart TB` with at most 256 points, emitting visible title,
both axes, categories, category-bound values, or exact x/y cells without inferred edges. Native
CandidateValidator rejection at the parse/render/SVG/type gate likewise revalidates this Flowchart once in
the same slot. Both terminals share 50,000-UTF-16-code-unit/5,000-line source preflight and strict security
scanning.

Native Scene creates normalized x/y axes, categorical tick anchors, and hidden-text data points/bars. A line
uses marker-free `series_line` associations between adjacent points; a bar's bbox runs from its y point to
the plot bottom. Semantic OCR counts only title, axis labels, and categories actually visible on canvas,
excluding hidden values and accessibility metadata. Flowchart Scene creates zero-geometry title, axis,
category, and data cells in source order, leaving relations/groups empty. Terminal-specific quote,
backslash, angle, and hash compatibility glyphs and source-only scanner separators are fixed in the plan,
and warnings disclose visible substitutions.

### Quadrant Terminal Plan

The Quadrant serializer, generated Scene, and semantic OCR share a bounded `QuadrantPlan` from
`plan_quadrant_records()`. The plan fixes both axis source records, supplied quadrant slots, point source
records, fixed-decimal x/y, deterministic Scene IDs, and terminal-specific source/canvas text once. Malformed
axis/point evidence is emptied only for that record. Slots keep empty evidence rather than inventing
provenance absent from the schema. There must be 1–256 points, and axis and point objects cannot be reused.

Native `quadrantChart` is used only when every coordinate in `[0,1]` round-trips exactly as zero-or-normal
binary64 and the `(x, 1-y)` canvas position is finite. It precomputes the pinned Mermaid 11.16 500×500
canvas, plot offset with or without title, point radius, and 12/16 px text layout. Native is rejected if
different source points collapse to the same pixel, or if points, labels, quadrants, axes, or title overlap
or clip. Comparisons are capped at 100,000 per candidate. Duplicate coordinates, subnormal differences,
float collapse, visually inseparable nearby points, and long canvas text therefore never reach native.

Pinned Mermaid 11.16 produces a `NaN%` component in native Quadrant HSL paint. SVG geometry and labels remain
finite and may still display through initial/inherited consumer paint, so native is not forcibly disabled;
instead every native candidate records a paint-compatibility warning. The portable Flowchart fallback does
not receive this renderer-specific warning.

A valid but native-lossy input becomes disconnected `flowchart TB`. In source order, fallback emits optional
title, `X axis: low to high`, `Y axis: low to high`, the named position of each supplied slot, and a
`label · x exact-x, y exact-y` rectangle for every point, without inferring edges or quadrant geometry. A
native CandidateValidator failure at the security/parse/render/SVG/type gate revalidates Flowchart once in
the same slot. Both terminals pass strict security scan and source preflight at 50,000 UTF-16 code units and
5,000 lines. Point projection accumulates native/fallback line UTF-16 units per terminal first. If both
terminals exceed budget, it stops before duplicating source/canvas/fallback point strings; if only one does,
the valid output of the other is preserved.

Native Scene creates four visible axis endpoints and normalized point circles, plus four `SceneGroup`
objects: `q1=upper-right`, `q2=upper-left`, `q3=lower-left`, and `q4=lower-right`. Because it invents no axis
line, quadrant membership, or point connector, relations are empty and reading direction is `unknown`.
Fallback Scene contains zero-geometry rectangles in actual emitted order, no relations/groups, and uses
`TB`. Semantic OCR counts only native visible title, axis endpoints, supplied slots, and point labels, or
fallback exact cells. It does not count point coordinates or accessibility metadata as native canvas text.

Automatic publication requires, separately from global numeric completeness, candidate-authorized
OCR/vector inside each axis/point bbox to prove the complete low/high or label/x/y record. Record,
observation, or bbox reuse, axis/point swaps, invalid geometry, and exceeding the shared 100,000-comparison
association budget lead to review. Axis ownership also requires relative geometry matching a horizontal
bottom x bbox and vertical left y bbox, so swapping entire axis records is not approved. A supplied slot
label is accepted only through an independent exact OCR/vector observation inside that source quadrant, or
an exact initial `user_edit` with a valid source-quadrant bbox. Explicit title/accessibility text also needs
independent evidence that does not overlap data records.

Direct Quadrant has no typed plan and is review-only; a newly engine-created `user_edit` cannot approve
itself. Current `VisualEvidence` has no semantic target for title/description, so metadata validation proves
only exact content existence and cannot decide a role swap between the two. `best_effort_validated` warns
about this limitation and treats the candidate as experimental; `strict_validated` sends it to review.
Warnings disclose visible compatibility substitutions, and semantic originals remain in typed IR/review
metadata. Source quadrants for slots currently use the whole crop's horizontal/vertical midpoint rather than
a detected plot bbox. This conservative heuristic can send inset or off-center plots to review rather than
approving them automatically.

## Extended Chart Structured Extraction

Sankey, Radar, Treemap, and Venn also use strict nested contracts shared by provider prompts and response
post-validation.

| Type | Nested contract | Semantic conditions and fallback decided by the serializer |
| --- | --- | --- |
| Sankey | `id`/`label` in `nodes[]`; exact endpoint/`value` and bbox/evidence in `flows[]` | Decide nonempty IDs/endpoints, participation of every node, label safety, and positive DAG; a valid graph outside native conditions becomes exact-weight Flowchart |
| Radar | `id`/`label` in `dimensions[]`; ordered `values` in `series[]`; finite `min`/`max`; strict `ticks`/`show_legend`; `circle\|polygon` graticule; bbox/evidence | Decide at least three dimensions, IDs, series lengths, bounds/options, `ticks <= 100`, native 12-series and fallback 256-point caps; binary64/span incompatibility or a valid negative domain becomes edgeless exact-value Flowchart |
| Treemap | Recursive `root` node with `id`, `label`, `value`, `children`, and bbox/evidence | Decide root/internal/leaf roles, positive values, cycles, object reuse, depth, and size; internal values, binary64/display-total loss, or native runtime failure becomes a value-label hierarchy Flowchart |
| Venn | IDs, membership, labels, optional finite values, and bbox/evidence in `sets[]` and `intersections[]` | Decide nonnegative values, set/member and canonical-intersection uniqueness, and size containment; native requires positive normal-binary64-safe areas, a `200:1` visibility gate, and every explicit pair in higher-order unions; otherwise exact-value Flowchart |

Nested models validate JSON structure and the types of known scalars/containers. Individual semantic fields
remain optional to isolate partial/legacy candidates; serializers decide completeness and native/fallback
selection. Sankey `links`, Radar `axes`, and Treemap/Venn `name` can be validated and retained as direct
compatibility metadata but are not advertised in canonical prompts. They are not copied into canonical root
fields and missing collections are not filled, preserving serializer key-presence precedence.

Valid Sankey/Radar/Treemap/Venn evidence is connected to generated Scene attribution. Treemap source bboxes
remain only in typed IR/review provenance and are not copied into generated Scene. Radar source bboxes are
not presented as terminal layout either: native uses renderer-calculated normalized axis/data-point
positions, while fallback uses zero geometry. Radar fallback preserves every dimension label and series
value but expresses none of bounds, ticks, legend, graticule, or Radar geometry in Mermaid code. Treemap
retains unique bounded source IDs, isolating missing, duplicate, or invalid IDs into collision-safe
`treemap_node_N[_suffix]` attribution slots. Venn reserves portable emitted set IDs first; when an explicit
intersection ID normalizes to a collision, it assigns a deterministic `intersection_N[_suffix]` slot. Thus,
a set/intersection ID collision does not discard a Scene node. The independent source-evidence gate for
every numeric type still applies.

The Sankey serializer and Scene/OCR adapter share one validated terminal plan. Native `sankey-beta` preserves
source node IDs and labels, but the Mermaid 11.16 canvas shows only each node's label and
`max(sum(incoming), sum(outgoing))`. Native is selected only when the total can safely reproduce runtime
binary-float addition and `Math.round(value * 100) / 100`. Native Scene flows are `data_flow`, but have no
individual weight labels or arrow markers, and direction is the runtime-fixed `LR`. Semantic OCR counts only
node labels and these displayed totals; exact relation values remain in typed IR and provenance. Native is
not used for exact values that become zero/infinity as JavaScript numbers, whose shortest decimal changes,
or whose totals exceed a safe cents-display range.

The Flowchart terminal uses the same plan's collision-safe emitted node IDs and shows each exact decimal
weight as a directed edge label. Scene uses the same endpoints, labels, end arrows, and requested direction
normalized to `TB`/`BT`/`LR`/`RL`. Only node/flow-record bboxes and evidence enter attribution; non-emitted
metadata such as raw `text`, role, shape, flow label/style/bidirectional/arrow hints is not promoted to Scene.
If native runtime rejects at the parse/render gate, the same-slot Flowchart is serialized once and passes the
complete security/parse/render/SVG/type gate again without creating another candidate.

Automatic publication also requires flow-local numeric attribution. Every flow must cite
candidate-authorized `ocr_token`/`vector_text` fully contained in its positive-area, source-image-contained,
non-overlapping bbox, proving the plan's exact `value_text`; complete source/generated numeric occurrences
must also match exactly. Reusing an evidence ID or normalized text+bbox between flows, hiding conflicting
observations at the same bbox, swapping weights, invalid geometry, or exceeding the bounded association
budget yields review without a partial score. Native, same-slot Flowchart, and semantic repair recompute this
gate with the same typed plan and scoped evidence. Direct/untyped Sankey is review-only because it cannot
prove flow owners.

Before terminal attribution, raw Sankey `title`, `description`, `acc_title`, and `acc_description` are
validated separately from accessibility enrichment. The reconstruction-pipeline candidate boundary and
public typed serializer apply the same rules. Any non-`None` value must be an exact built-in `str`, not a
subclass; numbers, containers, and custom string subclasses are rejected. Raw length is checked against
`MAX_TEXT_CHARS` before normalization. Except for compatibility exact `""`, strings must remain nonempty and
bounded after whitespace normalization, be UTF-8 encodable, and contain no Unicode category
`Cc`/`Cf`/`Zl`/`Zp` in normalized text. Overlong raw/normalized text, including huge whitespace,
whitespace-only or ZWSP/control-only input, and lone surrogates therefore fail before provider-specific
Mermaid serialization or runtime. JSON `null` is equivalent to absence. For Pie/XY compatibility, exact
`""` remains accepted but is treated as omitted rather than emitted empty metadata, allowing deterministic
accessibility text to derive.

Accessibility attribution is terminal-specific. Native Sankey emits no title/description, so metadata is
outside this gate. Same-slot Flowchart fallback emits resolved accessibility title/description into SVG
metadata, not content OCR labels. If `acc_title` shadows `title` or `acc_description` shadows `description`,
the non-emitted legacy text is exempt. Each actually emitted, non-derived title and description role must be
proved independently by a candidate-authorized spatial exact `ocr_token`/`vector_text` observation—or an
approved exact `user_edit` from initial reconstruction input—that is owned by no node/flow data record and
does not overlap those record bboxes. Deterministically structure-derived defaults and the experimental
notice are exempt. Reusing an ID or normalized text+bbox cited by a node/flow record, same-bbox ambiguity,
metadata overlap with node/flow bboxes, missing/invalid geometry for required data-record bboxes, exhaustion
of shared reference/text/token/spatial budget, or self-approval by engine-emitted `user_edit` closes to
review. Only numeric tokens from selected OCR/vector metadata evidence are removed from the global
flow-weight reference; unattributed extra numbers remain mismatches. Semantic repair recomputes this terminal
gate with the new typed IR and scoped evidence.

The Radar serializer, Scene, and semantic OCR share one bounded plan from `plan_radar_records()`. It fixes
dimension/series source records, terminal-wide collision-free emitted IDs, exact fixed-decimal values,
terminal-specific source/canvas labels, and per-point dimension+series evidence. Radar grammar reserved words
and Flowchart group/cell IDs share one namespace with collision-safe suffixes. A malformed evidence list is
atomically emptied only for that record; if a bounded union of point provenance cannot be made, all evidence
for that point is emptied. Dimensions are capped at 256; total points and Scene elements obey shared Scene
budgets; native and fallback source both pass 50,000-UTF-16-code-unit/5,000-line preflight.

Native `radar-beta` is selected only when values and explicit bounds round-trip exactly as zero or normal
binary64, the binary64 span between effective minimum and maximum is positive and finite, and the pinned
300 px renderer-radius calculation is finite. Negative domains, subnormal/overflow/precision loss, and zero
or non-finite spans use exact fallback. Native is limited to the 12 series for which the Mermaid theme
provides stable colors; the thirteenth falls back. Native Scene places perimeter axes and curve data points
in normalized `[0,1]` coordinates and uses marker/label-free `series_curve` associations, joining the last
point of each series to its first. A series-element bbox is the normalized envelope of those curve points,
so a logical series is not placed at the origin to distort layout score. Direction is `radial`; a series
label enters Scene/OCR only when `showLegend=true`. Native OCR counts visible title, axes, and legend only;
values, `min`/`max`, `ticks`, `graticule`, `accTitle`, and `accDescr` are geometry/metadata and excluded.

If native cannot be used or CandidateValidator rejects it, one `flowchart TB` with at most 256 points is
revalidated in the same candidate slot. Each series is a subgraph and each point a zero-geometry
`dimension: exact-value` rectangle, with no edges. Visible title remains as an isolated title node; a series
subgraph label is emitted only when `showLegend=true`. Fallback Scene/OCR project exactly that title,
conditional group label, and cells, without pretending bounds/ticks/graticule are canvas content. A valid
native candidate whose required runtime fallback cannot fit the 256-point cap becomes unavailable rather
than yielding partial code/Scene. Source separators for strict scanning are distinct from visible
compatibility glyphs for angles/hashes and fallback quotes/backslashes; native/fallback warnings disclose
visible substitutions. CandidateValidator closes render as invalid if any geometry attribute contains
`NaN`/`Infinity`, even when Mermaid reports render success.

The native generated-node provenance gate evaluates directly attributable axes and series; data points
derived from a series are excluded from its denominator. A Flowchart point cell cites both dimension and
series records, so evidence is not made node-exclusive; the Radar-local association below validates both
owners. If no cell has any known record evidence, it also fails the separate generated-node provenance gate.

The Treemap serializer, Scene, and semantic OCR share the same DFS preorder plan from
`plan_treemap_records()`. It fixes source records, logical Scene IDs, actual Flowchart `N1..Nn` IDs,
parent/child relations, and terminal-specific labels/value text once. Original images and source bboxes
remain unchanged in typed IR/review provenance, but generated terminal Scene uses zero bboxes rather than
substituting source positions for generated SVG layout. Valid evidence IDs attach to elements; child
evidence also attaches to the corresponding containment relation. If any `evidence_ids` constraint fails—
exact string list, 256-item limit, or ID/Unicode boundary—only that record's complete evidence tuple is
emptied, preserving serialization, hierarchy, and other-record provenance.

Native `treemap-beta` renders internal nodes as sections and leaves as valued cells. An internal displayed
total follows Mermaid 11.16 d3-hierarchy: children are added in reverse order using binary64 `+=`. Every
section/leaf canvas value must match d3 `format(",")` comma-grouped 12-digit display. Native is not attempted
when a Decimal token underflows/overflows as JavaScript number, exceeds safe integer, changes after reading
its shortest decimal, or cannot reproduce these displayed totals safely. Native Scene contains section/leaf
text and logical containment only; actual SVG has no connector path or arrow marker, and nested-area layout
is not interpreted as flow direction, so `reading_direction=unknown`. With zero Scene geometry, neither
native nor fallback can prove generated layout similarity by copying original bboxes.

An explicit native `title` directive creates a canvas-visible title. Separate `accTitle`/`accDescr` values
are SVG `<title>`/`<desc>` accessibility metadata and do not count as content OCR. Native semantic projection
uses visible title when present, every section/leaf label, and d3 displayed totals. A tiny native cell may
have text hidden by the renderer with `display:none`, so visibility of every leaf label is not guaranteed.

If an internal node has an explicit value or the native numeric contract fails, the Flowchart terminal uses
DFS preorder `N1..Nn`, `flowchart TB`, rectangular nodes, and parent-to-child end arrows. It adds exact
fixed-decimal ` (value: x)` only for values actually supplied; no derived internal total is invented. Raw
direction and native-only visible title are not copied to fallback canvas; title/description remain only as
accessibility metadata. Native runtime rejection revalidates this same-slot fallback once without a new
candidate. Flowchart supports at most 500 relations. A valid native Treemap above that limit may remain
native, but becomes unavailable if runtime fallback is required.

Treemap text retains semantic originals in typed IR and uses terminal-visible compatibility text in
Scene/OCR. Scanner-active tokens are split by zero-width separators in emitted source only and rendered
without them; quotes display as `″`. Flowchart labels additionally display ASCII angles, backslash, and hash
as `＜`, `＞`, `∖`, and `＃`; native title displays angles as `＜`/`＞`. URL/directive-like tokens and entity-like
`&...;` are neutralized only in emitted source, and native `#` is source-split only. A literal already
preserved by native grammar is not changed arbitrarily. Unicode whitespace runs, including CR/LF and NBSP,
become one ASCII space as on the actual canvas. A native node/title or resolved `accTitle`/`accDescr` with a
visible compatibility glyph records a candidate warning; Flowchart records a fallback reason/warning. Both
terminal sources pass the 50,000-character/5,000-line budget before runtime.

Automatic Treemap publication additionally requires record-local source binding for every plan node. Every
node bbox must have positive area inside the source image. A child bbox must be fully and non-equally
contained in its parent; direct siblings under the same parent may touch edges but may not overlap in their
interiors. Parent/descendant overlap is inherent to the hierarchy and permitted, but text evidence cited by
an internal node cannot overlap a direct child's area. Each node must directly cite candidate-authorized
`ocr_token`/`vector_text` fully inside its bbox, proving the exact label; when an explicit value exists, the
same reading-order record must prove the following fixed-decimal value. A typed value or source-wide
`ocr_texts` alone cannot create ownership.

`native_total_text` computed and displayed by the native renderer is deterministic output rather than an
explicit source value in typed IR. The current local-owner record does not accept it as a substitute for
source citation. If source OCR/vector observes a separate numeric internal total, it remains an extra token
in global numeric occurrences and conservatively requires review. This behavior, along with the tiny-cell
visibility issue, remains until terminal-aware derived-total evaluation is introduced.

Evidence IDs and normalized text+bbox observations cannot be reused between nodes, and duplicate evidence
references inside one node are also forbidden. Conflicting text at one bbox, equal or crossing parent-child
bboxes, sibling overlap, and missing/invalid geometry make the complete binding unavailable/review.
Aggregate reference/text/character/token/spatial-comparison budgets are respectively
20,000/50,000/1,000,000/100,000/100,000. A mismatched bound label/value makes association aggregate
unavailable, though `numeric_consistency` can retain the global multiset diagnostic. Automatic publication
requires both local binding and exact global numeric occurrences. Native, same-slot Flowchart, and semantic
repair run the same gate; direct Treemap without a typed plan is not automatically published. Source bboxes
serve only this validation and review provenance; generated Scene keeps its zero-geometry contract.

Explicit Treemap metadata is attributed independently according to actual terminal output. Native requires
the explicit canvas-visible `title` and non-derived resolved `accTitle`/`accDescr`. If visible and
accessibility titles use the same text, one source observation proves the title role; otherwise each needs
proof. Flowchart fallback creates no visible native title, so only actual resolved `accTitle`/`accDescr` are
proved. Legacy `title`/`description` hidden by `acc_title`/`acc_description` are exempt, as are deterministic
structure-derived defaults and the pipeline's experimental-notice suffix. Conversely, a notice-only
explicit description override that erases the structural description leaves no source text to prove and
fails closed to review.

Evidence must be a candidate-authorized spatial exact OCR/vector observation outside every Treemap node
bbox, or an approved exact `user_edit` from initial reconstruction input. Node-owned IDs/observations,
positive overlap with node bboxes, same-bbox contradiction, ID or normalized text+bbox reuse between metadata
owners, a newly engine-created `user_edit`, invalid geometry, or exhaustion of bounded work shared with node
association causes review. Using identical text for title and description still requires separate evidence
for both roles. Numeric tokens from selected OCR/vector metadata evidence remove only their own occurrence
from data numeric references, so an independently proved title such as `Portfolio 2026` is not mistaken for
a hierarchy value. Native/fallback and semantic repair all recompute this gate.

Before attribution, raw `title`, `description`, `acc_title`, and `acc_description` are validated. Pipeline
typed candidates and public typed/runtime-fallback serializers inspect originals before accessibility
enrichment; direct typed `serialize_treemap()` inspects them before planning. Other than `None`/absence and
omission-compatible exact `""`, values must be exact built-in `str`, no longer than `MAX_TEXT_CHARS` before
normalization, free of raw `Cc`/`Cf`/`Zl`/`Zp`, nonempty and bounded after normalization, and valid UTF-8.
Containers, numbers, string subclasses, whitespace-only or huge-whitespace strings, newline/tab, zero-width
formats, and lone surrogates are rejected before native/fallback runtime. Semantic repair uses the same
snapshot with exact-empty fields removed for serialization, evaluation, and storage. Raw Direct Mermaid,
which has no typed metadata fields, uses security/parse/render checks and the review-only policy for a
missing typed plan instead.

The Venn serializer, Scene, and semantic OCR use one bounded plan from `plan_venn_records()`. It fixes source
and portable set IDs, collision-safe intersection Scene IDs, canonical membership order, exact fixed-decimal
value tokens, terminal-specific labels, and record-local evidence once. It never emits exponent notation.
Set/intersection object reuse, unknown or repeated members, duplicate intersections, containment violations,
and area/membership resource overflow are rejected before serialization. A malformed evidence list empties
only that record's complete evidence tuple, preserving code, topology, and all other provenance.

For automatic publication, presence of this provenance tuple alone does not authorize set/intersection
content. Every set and explicit intersection in the plan needs a finite positive bbox inside the source
image and a separately cited `contour` bbox under candidate publication authority that matches it exactly.
Cited `ocr_token`/`vector_text` under the same owner must prove the actually observed label and explicit
value exactly as one bounded record inside that bbox. An unlabeled intersection requires only its observed
value; a valueless record neither invents nor requires a number. An intersection with neither has no textual
owner proof and goes to review. Evidence IDs and normalized text+bbox observations are injective across
owners. Conflicting text at the same bbox, missing evidence, invalid geometry, or exhausted association
budget makes the entire binding unavailable/review.

Venn does not require record bboxes to be non-overlapping because overlap between set/intersection areas is
semantic. An intersection bbox must, however, be inclusively contained in every declared member set and not
fully contained in an undeclared set. A higher-order intersection must lie inside every strict-subset
intersection bbox present in input. Equal containment is allowed so the documented exact-containment
Flowchart fallback remains possible. Every set scan, intersection-pair scan, contour comparison, and text
containment consumes the same 100,000 spatial-work budget. Publication requires this membership geometry,
owner-local containment of each cited observation, no cross-owner reuse, and exact source/generated numeric
occurrences. Native, same-slot Flowchart fallback, and semantic repair rerun the same gate; runtime-fallback
repair canonically reserializes to that Flowchart terminal. Direct Venn without a typed owner plan is
review-only.

Explicit Venn metadata is also attributed only to actual terminal output. Native `venn-beta` proves only the
canvas-visible explicit `title`; `acc_title` does not shadow this visible title. Native-non-emitted
`description`, `acc_title`, `acc_description`, and derived accessibility are exempt. Intrinsic or runtime
Flowchart fallback evaluates actual resolved `accTitle`/`accDescr` instead of native visible title. When
effective `acc_*` shadows legacy `title`/`description`, hidden fields are exempt, as are deterministic
defaults such as a structure-only baseline and the pipeline-added experimental-notice suffix. A notice-only
explicit description override erases structural description in experimental mode and fails closed. In
`strict` mode, if a user explicitly supplied the same notice wording, it is not a pipeline suffix and must
be proved like ordinary source text.

Each required title/description role independently needs a candidate-authorized exact OCR/vector observation
outside all set/intersection bboxes, or an approved exact `user_edit` from initial reconstruction input.
Review is required for IDs or normalized text+bbox already used by a data contour/text owner, same-bbox
ambiguity, positive overlap with areas, engine-created `user_edit`, reuse between roles, or exhaustion of
reference/text/character/token/spatial budget shared with data association. Even identical fallback title
and description require separate proof for the two SVG roles. Only numeric occurrences from selected
OCR/vector metadata proof are removed from global Venn data references; numbers in `user_edit` are not
removed, regardless of bbox. If the same exact observation is proved by OCR/vector and an approved edit,
source observation takes precedence so evidence-ID ordering cannot change numeric results. Native/fallback
and semantic repair all recompute this gate.

Raw `title`, `description`, `acc_title`, and `acc_description` are validated before terminal attribution.
Pipeline typed candidates and public typed/runtime-fallback/chart-set serializers all inspect originals
before accessibility enrichment. Non-`None` values accept exact built-in `str` only and first enforce raw
`MAX_TEXT_CHARS`. Exact `""` retains omitted semantics. Every other string must normalize to nonempty,
bounded, valid UTF-8, with no raw Unicode category `Cc`/`Cf`/`Zl`/`Zp`. Whitespace-only, huge-whitespace,
newline/tab, zero-width format, string subclasses, containers/numbers, and lone surrogates are therefore
rejected before native/fallback Mermaid validation. Semantic repair passes through the public typed
serializer again and uses the same exact-empty-removed snapshot for serialization, evaluation, and storage.

Here, chart-set serializer means the typed `serialize_venn()` API. A Raw Direct Mermaid candidate with no
typed metadata fields uses security/parse/render checks and the review-only policy for a missing typed plan
instead of this gate.

Native `venn-beta` is selected only when every set/intersection value round-trips from source as positive
normal binary64, Python `int` input remains within JavaScript safe range, and the largest-set/smallest-positive-
area ratio is at most `200:1`. Exact containment—an intersection exactly as large as a member set or smaller
explicit intersection—also falls back due to renderer-budget risk. For a union of at least three sets,
every pairwise intersection within that union must be explicit; a missing pair size is never synthesized.
Zero, subnormal, overflow, precision-loss, and missing values all choose exact Flowchart. An intersection
exceeding observed containment is rejected as invalid IR rather than hidden through fallback.

Native Scene represents sets as circles, intersections as shapeless logical areas, and memberships as
label/marker-free `logical_membership` containment, with `reading_direction=unknown`. Native canvas OCR
counts visible `title` and actual set/intersection labels only; area values are geometry input, not screen
text. The Flowchart terminal shows observed values on set circles and rounded intersection nodes using exact
` (value: x)` suffixes, emits every set-to-intersection relation with label `intersects` and an end arrow,
and uses `LR`. Native-only title is not copied to fallback canvas; resolved accessibility text remains SVG
metadata only. Generated element bboxes are zero in both terminals. Set/intersection evidence attaches to
each element; intersection evidence also attaches to every membership relation.

Native runtime rejection revalidates Flowchart once in the same candidate slot without creating another
candidate. If membership exceeds the pinned worker's 500-edge limit, the Flowchart terminal closes both code
and Scene as unavailable, but a valid native Venn with at least 501 memberships is not prohibited. The 500
value is a cap, not a performance guarantee, so near-limit fallback still has runtime timeout and ordinary
render budgets. Native and fallback sources independently pass 50,000-character/5,000-line preflight.
Scanner-safe source separators and visible quote/angle/backslash/hash/semicolon compatibility glyphs are
shared with terminal Scene/OCR and disclosed in warnings.

The shared Sankey plan enforces the Scene-relation cap before serialization and assigns relation IDs to
bounded unique slots. Non-string or overlong IDs use deterministic `sankey_flow_N` slots; duplicates receive
suffixes. If a record's `evidence_ids` is not a string list or violates count, ID, or Unicode limits, Mermaid
and structure remain, while only that record's provenance becomes an empty list. Native can be evaluated up
to the Scene-relation cap. Flowchart closes serializer and Scene together before exceeding the pinned
worker's 500-edge limit.

Every representative native/fallback fixture passes Mermaid 11.16 strict `CandidateValidator`
parse/render/SVG inspection. Sankey grammar cannot express title/accTitle/accDescr, so those values remain in
typed IR with a warning. Flowchart fallback preserves accessibility metadata in SVG but does not count it as
a canvas OCR label. Native Venn supports only visible `title` and cannot parse `accTitle`/`accDescr`, so
resolved accessibility text remains in typed IR with a limitation warning. Experimental native
Treemap/Venn grammar also records runtime type in sidecars.

The pipeline's general numeric consistency is occurrence-multiset F1 between source and generated numbers.
Within bounded evidence, identical normalized text+bbox becomes one observation. Token counters from OCR
context and evidence channels merge by the maximum occurrence per token, preserving spatially distinct
repetitions without recounting duplicate cross-channel reports. A source-absent number or occurrence-count
mismatch lowers precision/recall. A typed chart value or its record's `evidence_ids` alone cannot substitute
for an observed source number. Typed/Scene candidates retain the gate by semantic type; only Direct
candidates use emitted/runtime type confirmed by parse/render validation. A result of type
Gantt/Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn with no source OCR/vector numeric evidence remains grade `U`
and review-only despite syntax/render success. A result below the consistency threshold is likewise never
published automatically.

Pie requires both slice-local association and global numeric completeness because a global numeric multiset
alone cannot detect a label/value swap. Every typed slice needs a positive-area, non-overlapping bbox inside
the source image and must directly cite candidate-publication-authorized `ocr_token`/`vector_text` evidence.
The evidence bbox must be fully inside its slice bbox. Reading-order combined observations must exactly match
a punctuation-preserving `label + allowed separator + value` record, and the numeric multiset inside the
slice label plus exact value must match. `numeric_consistency=1.0` additionally requires exact occurrence-
multiset equality between all bounded source OCR/vector numbers and generated data numbers. Candidate
publication authority is required only for slice-local ownership. A bound label with a changed value or an
unrelated extra source number produces `0.0` and review. Source-wide `ocr_texts` contributes to global
completeness but cannot establish slice ownership.

Overlapping slices, broad/shared evidence, cross-slice reuse of an evidence ID or normalized text+bbox,
conflicting text at one bbox, invalid geometry/authority, or association-budget exhaustion makes the entire
Pie binding unavailable/review. Native, exact-value Flowchart, and semantic repair reapply this check
identically. A candidate without typed slice slots, such as Direct Pie, is not automatically published.
Missing uncited slices or numbers also close to review through global completeness. Each generated Pie slice
element must separately pass the 80% provenance gate, so numeric binding alone cannot publish an
unattributed slice.

Explicit Pie `title`/`acc_title` and `description`/`acc_description` also require an independent,
candidate-authorized spatial exact OCR/vector observation or exact `user_edit` evidence passed in initial
reconstruction input; otherwise they require review. A newly engine-created `user_edit` cannot approve
itself. An observation owned by a slice cannot be reused merely by changing its ID, nor can an observation
overlapping a slice bbox. Deterministically structure-derived accessibility defaults and the experimental
notice are outside this gate.

Packet is another exception to the global occurrence multiset. Native Packet, Flowchart runtime fallback,
and semantic-repair proposals all recompute candidate-authorized field-local association. A label and bit
range bind only when the full bbox of directly cited OCR/vector evidence lies inside a positive-area field
bbox and both are within actual image bounds; source-wide `ocr_texts` cannot bind. An exact label+range gives
`1.0`; a bound but incorrect range or extra number gives `0.0` and review. Single-bit `start == end` requires
one endpoint-number occurrence. OCR/vector duplicates at identical normalized text+bbox count once, while
spatially distinct repetitions remain. Overlapping fields, broad/shared or same-location ambiguous
observations, missing/invalid authority, bbox, or image bounds, and exhausted association budget become
unavailable/review. Neither a global multiset nor publication threshold can bypass this.

Radar also requires dimension/series-local association in addition to the global numeric multiset. Every
dimension and series record needs a positive-area, non-overlapping bbox in the source image and direct
candidate-publication-authorized `ocr_token`/`vector_text`. A dimension observation must be the exact label;
a series observation must combine exact label and every fixed-decimal value in original order as one record.
Evidence bboxes must be fully contained in their owner bbox; only bbox-reading-order combined text is
compared with the allowed bounded notation. Reusing an evidence ID or normalized text+bbox across owners,
uncited conflicting text at one bbox, overlapping records, or inability to establish geometry/reference/
text/token/comparison budget makes the whole binding unavailable/review. A different bound label or value
order gives `0.0`; only exact local binding plus exact global occurrences gives `1.0`. Native, same-slot
Flowchart, and semantic repair share this check. Direct candidates without a typed Radar plan are never
automatically published. Multiset calculation remains unchanged for numeric types other than
Pie/XY/Quadrant/Sankey/Radar/Treemap and Packet.

Radar's visible `title` and non-derived explicit `acc_title`/`description`/`acc_description` also need
evidence independent of data records. A candidate-authorized OCR/vector observation must occupy a valid
source position outside every dimension/series bbox; an approved exact `user_edit` from initial
reconstruction input is also allowed. Record-owned evidence, same-text+bbox reuse, engine-created user edits,
or ambiguous/budget-exceeding comparisons do not approve metadata. Structure-derived accessibility defaults
and the experimental notice are exempt.

Generated numeric projection excludes Mermaid `%%` comments and, only when the detected grammar supports
them, native `title ...`, colon-form `title: ...`, `accTitle: ...`, single-line `accDescr: ...`, and block
`accDescr { ... }` metadata. In Sankey, a CSV label beginning with one of these strings is actual data, so its
row and weight numbers still count. Quadrant `quadrant-1`–`quadrant-4` directive indices are grammar tokens
and excluded, while real numbers inside directive labels or point coordinates remain. The source collector
does not distinguish title/accessibility regions; numbers observed there enter global completeness and can
conservatively cause review. Venn remains a numeric-mandatory type even when it can produce a sizeless
portable fallback, so it cannot bypass the automatic gate.
