# Planning and Specialized Diagram Serializers

Planning and specialized types use the same `SerializationResult` contract as other typed serializers.
They do not invent IDs, numbers, coordinates, dates, branches, or relation endpoints absent from the input.
When a native grammar is incompatible with the strict parse/render/SVG gate, requested/emitted types and a
loss warning are recorded together.

## Planning Types

| Type | Output | Required evidence |
| --- | --- | --- |
| Journey | Timeline fallback | Section/task, integer score from 1 to 5, actor list |
| Kanban | Native `kanban`, then Flowchart on runtime rejection | Column/card ID and each card's explicit `column_id` |
| GitGraph | Native `gitGraph`, then Flowchart on runtime rejection | Initial `main` branch, ordered commits/branches/merges, globally unique commit IDs |

Mermaid 11.16 Journey generates `foreignObject`, which the strict SVG inspection rejects. Scores and actors
are therefore preserved in Timeline event text, with a warning for the loss of Journey scoring layout.
Journey scores must also match separate source OCR/vector numbers for automatic publication; duplicate task
IDs are sent to review instead of silently merging attribution. To avoid Timeline item delimiters and
renderer truncation, colons in section/task/actor text are displayed as `∶`, and entity-like prefixes use
the `＆`/`＃` compatibility glyphs with a warning. Angle brackets in a Journey title are displayed as `‹`/`›`,
while the original text remains in typed IR and sidecars.

The Kanban serializer and generated Scene use one bounded column/card plan. It first rejects collisions
between raw and Mermaid-normalized IDs and unknown `column_id` values, then places every emitted ID in the
reserved-word-safe `kanban_` namespace. Native and Flowchart runtime fallback use the same emitted IDs,
labels, and containment. A native runtime failure does not create a new candidate: in the same candidate
slot, column/card nodes and containment edges are revalidated once as Flowchart, with a warning for lost
lane/board layout. When a native Kanban Markdown label cannot preserve a literal quote or backtick, it uses
`″`/`ˋ` with a warning. When the portable Flowchart fallback cannot preserve a literal quote or backslash, it
uses `″`/`∖`.

GitGraph never lets Mermaid invent commit IDs, and a merge requires source, target, and merge-commit ID.
A shared branch-head replay plan determines commit/merge nodes, parent relations, branch membership, and the
Flowchart fallback together. `initial_branch` must be exactly the source value `main`, not merely normalize
to it. Branch-before-commit, same-head merge, self-merge, raw/normalized ID collisions, and record-budget
overflow fail closed. A GitGraph commit ID must remain unique after Mermaid encoding as well. The 2,000-record
limit for all three planning types is an absolute structure-traversal limit; before publication, every
native/fallback source is also checked against the validator's 50,000-character and 5,000-line hard budgets.

When a canonical field and compatibility alias are both present, their normalized meanings must match.
Conflicts in Journey `title/label` or `label/text`, Kanban `label/title` or `label/text`, and GitGraph
`name/id` or `commit_type/style` are rejected rather than selecting source evidence arbitrarily. The known
field sets for GitGraph commit, branch, and merge records are closed as well, so a field meaningful only to
another operation is not silently discarded.

Pinned Mermaid 11.16 does not reproduce ordinary HTML numeric entities exactly in GitGraph labels, so the
serializer does not use a broad entity encoder. Quotes and backslashes preserve their original glyphs
through grammar quoting; invisible separators are inserted only into URL/directive, callback, import, and
entity-like active tokens. Because native SVG does not preserve `<` and `>`, they become `‹` and `›` with a
compatibility warning. The same rules apply to commit IDs, tags, accessible titles, and descriptions, and
are verified by real SVG-text integration fixtures.

## Specialized Types

- Packet requires integer `start`/`end` values for every field under a strict nested `fields[]` contract.
  It rejects overlapping, reversed, or missing ranges. A non-contiguous range is preserved as a disconnected
  Flowchart fallback without inventing gap values or arrows between fields.
  Native Packet, the same-candidate-slot Flowchart fallback, and semantic-repair proposals share one field
  plan and field-local numeric association. Each field's label/range is verified only from directly cited
  `ocr_token`/`vector_text` evidence that the candidate is authorized to use for publication. Source-wide
  `ocr_texts` grants no field-binding authority. Field and evidence bboxes must have positive area within the
  actual image, and the entire evidence bbox must lie inside its field.
  When all labels and `start`/`end` values bind exactly, the score is `1.0`. A bound label with different
  range numbers or extra unnecessary numbers produces `0.0` and review. A field with `start == end` requires
  one occurrence of the endpoint number. Duplicate OCR/vector observations with the same normalized
  text+bbox within one field count once, while spatially distinct repetitions are preserved. Field overlap,
  broad evidence crossing multiple fields, shared evidence IDs, ambiguous observations at the same
  position, missing or invalid authority/bbox/image bounds, or an exhausted association budget makes the
  whole association unavailable/review without a partial result. Conflicting text at the same bbox within
  candidate authority remains ambiguous even if the field cites only one version.
- Ishikawa and TreeView post-validate effect/category/cause or root/children through strict recursive
  contracts. A shared hierarchy planner decides IDs, normalized collisions, cycles, reuse of the same
  dictionary object, and maximum depth/node budgets exactly once.
- Event Modeling passes a strict nested lane/frame/relation contract before emitting a lane-aware Flowchart.
  The pinned renderer currently returns a native AST error, so the result is not presented as a native
  success. A shared frozen plan fixes emitted `eventmodeling_lane_*` and `eventmodeling_frame_*` IDs, lane
  membership, typed/time labels, and explicit relations. Because Mermaid edges have no ID syntax,
  `eventmodeling_relation_*` is assigned only as a Scene/provenance slot; topology, labels, and evidence are
  shared by fallback, Scene, and OCR. Keywords and URL-like tokens prohibited by the strict scanner are
  neutralized only in source with a zero-width separator. In compatibility labels visible in the SVG,
  quotes, backslashes, and entity-like literals become `″`, `∖`, and `＆`/`＃`; relation-label `|` and `;`
  additionally become `∣` and `⁏`, disclosing the loss. OCR projection uses these visible labels instead of
  pretending the originals rendered successfully.
- Wardley validates each component's `x`/`y` in the range 0–1 and strict boolean `anchor` under a strict
  nested component/link contract. It never infers missing coordinates with a layout algorithm. A shared
  plan applies component-ID/display-label collisions, endpoints, self/duplicate links, and record budgets
  identically to native output, Scene, and OCR. Character and line budgets receive a separate source
  preflight before serializer return.
- Cynefin permits only the five official domains and explicit domain transitions under a strict nested
  domain/item/transition contract. A canonical item is an object with `label`/bbox/evidence. Legacy scalar
  string items remain accepted for input compatibility but create no provenance. A shared plan fixes domain,
  item, and transition IDs, visible text, and membership. If native `cynefin-beta` is rejected at runtime,
  `flowchart LR` is revalidated once in the same candidate slot rather than creating a new candidate.
- Railroad serializes terminal/nonterminal/special/sequence/choice/optional/repetition ASTs with a strict
  nested rule/expression contract and frozen preorder plan. Rules use `railroad_rule_*`, expressions use
  `railroad_expression_N`, and containment without ID syntax in native source uses
  `railroad_relation_N` Scene/provenance slots. Every nonterminal reference must identify an existing rule,
  but no reference connector absent from native SVG is invented. A rule label is the actual runtime text
  `native_name =`; terminal/nonterminal nodes use runtime-visible labels, special nodes use `? text ?`, and
  operator nodes have no visible text. Canonical compatibility text displays ASCII `<`/`>` as `〈`/`〉`, every
  ASCII `#` as `＃`, entity-like `&` prefixes as `＆`, and NFKC quote/backslash hazards as `″`/`∖`, with a
  compatibility warning; original semantic text remains in typed IR/sidecars. Bare `#word;` and `#35;`,
  which global `encodeEntities` transforms, follow the same hash contract. Zero-width separators for active
  tokens are source-only and also split `style...:#...;`/`classDef...:#...;` preprocessor substrings; both raw
  and NFKC-normalized emitted source are strict-scanned. Scanner/preprocessor source-active rule names,
  case-folded expression-word namespaces, `railroad-beta`, and case-folded lowercase `title*` prefixes map to
  collision-safe `rrmapped_N[_suffix]` native names with a visible-change warning. All safe source names are
  reserved first to prevent collisions; raw source names remain in typed IR and normalized names remain in
  nonterminal labels. Scene/OCR use the same compatibility text without separators, and direct Scene fails
  closed when `evidence_ids` is neither null/omitted nor a string list.
- ZenUML uses a Sequence fallback because the pinned runtime has no extension. A strict nested
  participant/message contract and shared plan emit only `zenuml_participant_*` IDs, aliases, endpoints, and
  one-way messages. Mermaid messages have no ID syntax, so `zenuml_message_*` is assigned only as a
  Scene/provenance slot. To prevent statement or actor injection into Sequence grammar, visible `#`, `;`,
  and entity-like literals become `＃`, `⁏`, and `＆`/`＃` with a warning; active keyword and URL tokens retain
  visible text but are neutralized in source only. `<`/`>` values that double-escape only in Sequence
  accessibility are exposed as visible `〈`/`〉` glyphs, while angle brackets in participant/message text
  render as the originals.
- Organization uses a strict recursive `root/children` contract and frozen plan to fix the TreeView
  fallback's logical `treeview_node_*` identity, visible labels, and parent-to-child reporting relations. If
  TreeView fails runtime validation, it is revalidated in the same candidate slot through the
  `organization → treeview → flowchart` chain. Generated Scene uses `LR` to match native and nested-fallback
  depth layout, while distinguishing terminal native TreeView's marker-free, unspecified-shape connectors
  from Flowchart rectangles/end arrows. Because source bbox/group/style are not reconstructed, it reports
  zero geometry and no groups.
- Data Lineage uses a strict dataset/process/relation contract and frozen plan to create
  `data_lineage_dataset_*` and `data_lineage_process_*` nodes plus `data_lineage_relation_*` provenance slots.
  Its Flowchart fallback emits datasets as cylinders, processes as rectangles, and relations as one-way
  data-flow edges. It accepts only `TB`/`BT`/`LR`/`RL`, defaulting to `LR`.
- Both plans reject control/format/lone-surrogate characters in IDs/labels and normalization collisions. For
  legacy partial/direct IR, a missing Organization ID becomes preorder `node_N`; a missing Data Lineage
  label uses the validated source ID, preserving previous semantics. Organization relations derive only
  from validated `children`; Data Lineage rejects unresolved, self-referential, or duplicate explicit
  relation endpoints. Both paths enforce 500 aggregate records and output budgets of 50,000 characters and
  5,000 lines. Quotes, backslashes, entity-like literals, and edge `|`/`;`/`()[]{}@` are emitted as the
  SVG-visible compatibility glyphs `″`, `∖`, `＆`/`＃`, `∣`, `⁏`, `❨❩`, `⟦⟧`, `⦃⦄`, and `＠`; warnings, OCR,
  and generated Scene disclose the same loss. Fullwidth `＠` receives an additional source-only zero-width
  separator so NFKC cannot reactivate `@import`.

Packet, TreeView, and Ishikawa do not share one HTML-entity encoder. Each pinned native grammar uses quoting
that preserves actual SVG text. A label that the native renderer cannot preserve—such as a quote/backslash
in TreeView or ampersand/angle bracket in Ishikawa—switches to an explicit Flowchart fallback. When an
Ishikawa raw-line label starts with the reserved `ishikawa` or `ishikawa-beta` header, only the header token
is split and neutralized while visible text remains unchanged. When Flowchart itself cannot preserve a
literal quote/backslash, it uses `″`/`∖` with a compatibility warning. If an unsafe URL/HTML/control token
appears in accessibility text, the original remains in typed IR/review metadata while automatic SVG uses a
generic title/description and warning. Fallback IR does not pass the original type-specific root back into
accessibility derivation, preventing an unsafe label from being reintroduced.

If both `label` and `name` compatibility aliases are present for Packet, Ishikawa, or TreeView, they must
mean the same thing; a conflict is not resolved by arbitrary precedence. Ishikawa effect `children` likewise
cannot silently overwrite category roots. A shared plan validates identity and parent exactly once, and the
native serializer uses that label/range/depth. Only ID-expressing fallbacks and generated Scene share the
reserved-word-safe namespaces `packet_field_`, `ishikawa_node_`, and `treeview_node_`. Scene preserves each
original record's bbox/evidence in order. Packet invents no relations; hierarchies create containment only
from the shared parent. Packet is also subject to the 80% generated-node provenance gate and retains its
separate source OCR/vector numeric gate. That gate is the field-local association described above, not a
source-global numeric multiset, and does not vary between native and fallback grammar.

Organization's input-compatibility `name` must also match `label` when both are present, but the canonical
provider prompt exposes only `label`. Because Organization fallback does not reproduce source bboxes, it
does not share the hierarchy Scene bbox-preservation rule above.

Packet, Ishikawa, TreeView, Event Modeling, Wardley, Cynefin, ZenUML, Organization, Data Lineage, and
Railroad serializers implement strict source preflight. Regardless of native/fallback selection, they check
the 50,000-character and 5,000-line hard budgets before returning. In grammars where Mermaid 11.16 does not
preserve entity-like literals exactly, visible `＆`/`＃` compatibility glyphs and a warning disclose the loss,
while original text, geometry, and evidence remain unchanged in typed IR and sidecars.

Wardley generated Scene stores native vertical-axis coordinates as screen-space `(x, 1-y)` in the
`normalized` coordinate space. IR `x`/`y` are horizontal/vertical, but Mermaid Wardley source orders values
as `[visibility, evolution]`, so the serializer emits `[y, x]`. Decimal-token rounding is applied identically
to plan coordinates; a typed record's separate bbox or arbitrary extra geometry cannot contaminate layout
scoring. Mermaid 11.16 renders Wardley `->` as a plain link without an arrowhead, so generated Scene evaluates
it as an undirected relation as well.

If the native runtime rejects `wardley-beta`, the same plan's components become ordered
`wardley_component_N` rectangles and explicit links become undirected `---` in a `flowchart LR`, revalidated
once in the same candidate slot. This fallback Scene uses zero bboxes and `pixels` coordinate space, so loss
of coordinates, axes, and anchors is not mistaken for preserved layout. Warnings disclose those losses and
compatibility glyphs. Native title is not invented as a separate fallback-canvas node; it remains only in
accessibility metadata, with a separate warning for the visible-title loss. If an explicit, different
`acc_title` exists, only that accessibility value is retained in `accTitle`; the visible title remains in
typed IR/review metadata, and this distinction is documented.

Event Modeling and ZenUML generated Scene retain the requested type while reconstructing only the actual
Flowchart/Sequence fallback's namespaced IDs, `LR` direction, end-arrow topology, and visible labels. They use
zero geometry instead of pretending to reproduce source bbox, shape, style, direction, or bidirectional
extras. Frame/participant/relation/message evidence comes only from its own source record.

Cynefin native grammar provides no explicit item placement, so the layout metric remains unavailable. A
native success shows all five domains and a fixed practice/response and disorder template regardless of
input. Native Scene/OCR explicitly marks these elements as unsupported by evidence, and the fourth and later
`confusion` items collapse to `+N more` as they do at runtime. It does not create containment edges for input
membership. Because the fixed-template provenance contract is absent, every native candidate requires
review regardless of score.

The Flowchart fallback after native runtime rejection does not reproduce that template. It creates one
subgraph, in input order, only for each explicitly supplied domain; the optional fifth `confusion` domain is
created only when supplied. Every explicit item remains a separate node without `+N more` truncation, and
only explicit transitions become one-way edges between source/target domain subgraphs. It adds no separate
domain-item membership connector and no absent fixed domain/practice/response/disorder node. Fallback Scene
uses the same domain ID for the conceptual element and group, connecting domain/item/transition with actual
fallback visibility and record-local provenance. A domain label counts once in OCR. All geometry is zero
bbox, and direction is the fallback's actual `LR`. The projection does not claim to preserve original
quadrant layout or Cynefin spatial meaning and records a loss warning. If generated domain/item nodes meet
the 80% attribution threshold and pass security, parse, render, and semantic gates, the fallback may follow
the normal publication policy; the native result's review hold remains unchanged.

Representative native/fallback fixtures are pinned by integration tests that run real strict security
scanning, parse, render, and SVG inspection under Mermaid 11.16. Packet/Ishikawa/TreeView and Treemap/Venn
revalidate an evidence-preserving portable fallback once in the same candidate slot after native runtime
rejection. Kanban/GitGraph do the same with Flowchart from the shared planning plan. Organization pins both
a real TreeView runtime fixture and a simulated rejection-to-Flowchart pipeline fixture. The Data Lineage
Flowchart fallback is also checked for parse/render, visible labels, accessibility, and security in a real
strict runtime fixture. Experimental native output never bypasses the validation hard gate.

The Railroad native fixture pins recursive choices/sequences, compatibility text for
terminal/nonterminal/special nodes, accessibility, source-only active-token neutralization, strict scanning
of raw and NFKC-normalized source, the raw CandidateValidator parse/render hard gate, an NFKC grammar-injection
safety probe, scanner/preprocessor source-active and grammar-reserved rule-name mapping, bare
`#word;`/`#35;`, `style`/`classDef` substrings, NFKC quote-injection neutralization, and runtime termination.
Wardley likewise revalidates an undirected Flowchart from the shared plan in the same candidate slot after
native rejection, and verifies terminal type, visible compatibility text, and marker-free links in a real
runtime fixture. Cynefin revalidates a Flowchart retaining only explicit domains/items/transitions in the
same slot after native rejection. Its real runtime fixture verifies the absence of fixed templates,
`confusion` truncation, and membership connectors, along with terminal type and directed transitions. A
native success continues routing to review workspace/sidecars instead of automatic Markdown because of the
fixed-template boundary above. Both terminal paths retain candidate budgets, requested/emitted/runtime
metadata, strict security, and the requested-type accessibility contract.
