# Accessibility generation

Successful reconstructions include Mermaid `accTitle` and `accDescr` directives whenever possible.
Explicit `acc_title`/`acc_description` values take precedence. When they are absent, values are
derived deterministically from the requested semantic type and labels observed in the typed IR.
Explicit title/description values are the next fallback.

A derived description lists at most five labels. A start and end are added only when an explicitly
directed graph has exactly one root and one terminal. The resolver does not infer increasing or
decreasing chart trends, relationship semantics, or missing numbers. Experimental candidates add a
single notice that review is required. For ordinary candidates, generated values are stored in both
the code and the enriched `typed_ir`, so they remain available as review metadata even for grammars
that do not support the directives. Diagram types with terminal-specific source/canvas plans are an
exception: the Architecture family (Architecture, C4, Deployment, and Component), as well as State,
Gantt, ER, Sequence, Mindmap, and Timeline. To prevent stale derived text, these types retain a
validated raw metadata snapshot in the candidate `typed_ir` and recompute accessibility values from
the current semantic records during initial serialization and after every accepted repair.

## Semantic type and emitted grammar

Content derivation uses the requested type. For example, a C4-to-Architecture fallback still uses
“C4 model reconstruction” as its title. Whether directives can actually be inserted is determined by
the emitted Mermaid grammar.

With pinned Mermaid 11.16, directives are not inserted into the following native grammars.

| Grammar | Reason |
| --- | --- |
| Mindmap | Interprets a directive as another root and fails to parse |
| Block | Rejects `accTitle`/`accDescr` during parsing |
| Sankey | Interprets a directive as a CSV flow row |
| Venn | Supports only a canvas `title` and rejects `accTitle`/`accDescr` during parsing |
| Ishikawa | Renders a directive as cause text and does not create SVG `<title>`/`<desc>` elements |
| Timeline/Journey | Accepts directives but does not create SVG accessibility elements |
| Kanban | Accepts directives but does not create SVG accessibility elements |

These types retain a limitation warning. An ordinary unsupported grammar preserves resolved text in
the enriched typed IR. Mindmap and Timeline terminals, which guard against stale derived text, are
exceptions: the candidate typed IR stores only the validated raw snapshot, and values are recomputed
from the current record plan during serialization or review. When a portable Flowchart fallback is
selected, the fallback grammar supports the directives and emits the same resolved text normally.

Native Architecture, the Architecture projection used by C4, Deployment, and Component, and the
nested Flowchart fallback all use the same raw-snapshot policy. Raw
`title`/`description`/`acc_title`/`acc_description` values are checked before enrichment for exact
string type, raw and normalized bounds, UTF-8 validity, and Unicode category; exact `""` is treated as
omitted. Candidates and accepted repairs store this raw snapshot rather than derived `acc_*` values,
then rebuild the description from the current requested type and service-like semantic labels. A
Phase 2 label repair therefore cannot leave an earlier derived description behind as explicit
metadata.

The Architecture accessibility lexer does not reuse the Markdown escaping used for labels. It turns
literal `&`, `#`, `<`, and `>`, plus one character from each scanner-active token, into `#NN;` source
entities interpreted by Architecture directives. Literal `&amp;`, `&#35;`, `<tag>`, quotes, a single
backslash, Markdown punctuation, and Korean text can therefore pass the strict scanner while the SVG
`<title>`/`<desc>` retains the normalized semantic text exactly. This codec is exclusive to
accessibility text terminals and is not used in service/group statements. Accessibility metadata is
not included in node/group OCR recall.

An explicit native Venn `title` is canvas content and is therefore included in Scene/OCR. Resolved
`accTitle`/`accDescr`, by contrast, are omitted from native source and retained only in the enriched
typed IR with a limitation warning. When Venn drops to its same-slot Flowchart fallback, the resolved
text is emitted as SVG accessibility metadata but is not counted as a canvas OCR label, and the
native-only title is not copied. If grammar-unsafe visible text is changed to a compatibility glyph,
the native candidate warning or fallback reason discloses the change.

Native ER emits pinned Mermaid 11.16 `accTitle`/`accDescr` directives as SVG `<title>`/`<desc>`
metadata. Its accessibility plan, which is separate from the record plan, prefers explicit
`acc_title`/`acc_description`, then `title`/`description`, and otherwise builds default text from the
current semantic entity labels. Before enrichment, all four raw metadata fields are checked for exact
built-in string type, bounds, UTF-8 validity, and Unicode category; an exact empty string is treated as
omitted. Unlike entity, attribute, and relationship canvas text, accessibility metadata is not counted
as semantic OCR content. Where directive rendering cannot preserve the source, such as numeric
entity-like text, the serializer uses a visible compatibility glyph and warning while retaining the
semantic source in the validated raw typed/review IR. An accepted repair rebuilds the accessibility
plan from the raw snapshot and reconciles the derived description and compatibility warning with the
current entity plan.

Native Sequence likewise emits pinned Mermaid 11.16 `accTitle`/`accDescr` directives as SVG
`<title>`/`<desc>` metadata. Resolution order is `acc_title > title` and
`acc_description > description`; when explicit values are absent, text is derived deterministically
from the current participant plan's semantic labels. Before enrichment, all four raw fields are
checked for exact built-in string type, raw and normalized bounds, UTF-8 validity, and Unicode
category; exact `""` is treated as omitted. Initial candidates and accepted repairs store the
validated raw snapshot rather than derived `acc_*` values, so participant changes cause the
description and compatibility warning to be recalculated from the current plan.

Native Sequence escaping preserves participant/message `#` and `;` characters on the canvas, but
Mermaid double-escapes literal `<`/`>` in accessibility text. They are therefore displayed as
`〈`/`〉`, with a conditional warning. Source-only separators disable scanner/lexer behavior without
changing the semantic source retained in typed/review IR. Because this metadata is not
participant/message Scene/OCR content, it is excluded from structural label recall.

Native Mindmap interprets `accTitle`/`accDescr` as an additional root, so the directives are not
inserted into source. Instead, the resolver prefers raw `acc_title > title` and
`acc_description > description`, then derives values deterministically from the current preorder node
plan when explicit values are absent. Before enrichment, the four raw fields are checked for exact
built-in string type, raw and normalized bounds, UTF-8 validity, and Unicode category; exact `""` is
treated as omitted. Initial candidates and accepted repairs store only the validated raw snapshot,
not derived `acc_*` values, allowing hierarchy-label changes to rebuild the description and its
conditional compatibility warning. Derived `acc_*` fields themselves are not persisted in the
candidate typed IR. These values are not Mindmap canvas OCR content, and a limitation warning
discloses that they are absent from source/SVG.

Native Timeline parses `accTitle`/`accDescr`, but pinned Mermaid 11.16 does not produce SVG
`<title>`/`<desc>` elements for them, so the directives are not inserted into source. The resolver
recomputes `acc_title > title` and `acc_description > description` from the current raw snapshot on
every pass and records a limitation warning. Before generic enrichment, all four raw metadata fields
pass exact built-in string, raw/normalized bound, UTF-8/Unicode category, and
exact-empty-as-omitted gates. Initial candidates and accepted repairs preserve the validated raw
snapshot rather than derived `acc_*` values, regenerate the description from the current semantic
period/event plan after event changes, and do not persist the derived fields themselves. These values
are not Timeline canvas OCR labels.

Native Pie supports `accTitle`/`accDescr` in pinned Mermaid 11.16, so resolved text is emitted as SVG
`<title>`/`<desc>` metadata. A separate explicit `title` is Pie canvas content and is included in native
semantic OCR, whereas accessibility directives are not counted as content labels. If native
conditions are not met, or runtime validation fails and the same-slot exact-value Flowchart is
selected, resolved title/description remain as Flowchart accessibility metadata and the description
adds that the result is an exact-value fallback rather than a proportional sector chart. The
native-only canvas title is not copied into fallback cells or OCR.

A source-only separator in a Pie slice label may remain zero-width in the SVG DOM without changing
canvas glyphs; semantic OCR uses the visible label with the separator removed. If native-title
quote/backslash/angle/hash/semicolon characters or Flowchart-cell quote/backslash/angle/hash characters
are changed to visible compatibility glyphs, a warning discloses the change. Resolved accessibility
text and semantic source remain in enriched typed IR/review metadata.

All four explicit Pie fields—`title`/`acc_title` and `description`/`acc_description`—must exactly match
independent candidate-authorized OCR/vector observations before automatic publication. Output
resolution still follows `acc_title > title` and `acc_description > description`, but shadowed explicit
text is also checked conservatively. The observations may neither reuse IDs or normalized text+bbox
pairs owned by slices nor overlap a slice bbox. Exact text from a `user_edit` supplied in the initial
reconstruction input, as in a reviewed result, is allowed; a `user_edit` newly emitted by an engine is
not authorization. Deterministically derived default accessibility text and the `experimental` notice
do not require separate source observations. Quotes and numeric entity-like text pass through
source-only separators and remain exact in `<title>`/`<desc>`.

Native XY Chart also uses pinned Mermaid 11.16 `accTitle`/`accDescr` directives to create SVG
accessibility metadata. A separate `title` is visible on the canvas and is included in semantic OCR,
but axis bounds, series values, automatic ticks, and accessibility metadata are not counted as
visible content text. If native binary64/grid/visibility conditions are not met or runtime validation
fails, the candidate falls back in the same slot to an exact-value Flowchart. This terminal preserves
resolved accessibility metadata, adds that it is an exact-value rather than proportional-plot
fallback to the description, and displays the explicit canvas title in a separate rectangle.

Explicit XY `title`/`acc_title` and `description`/`acc_description` fields also require independent,
candidate-authorized exact OCR/vector text or exact initial-reconstruction `user_edit` evidence before
automatic publication. They may not overlap observations/bboxes owned by axis, series, or point
records, nor reuse their evidence IDs. A `user_edit` newly created by an engine is not authorization.
Only structurally derived default title/description text and the experimental notice are allowed
without a separate source observation. Native/fallback quote, backslash, angle, and hash substitutions
are disclosed by compatibility warnings while semantic source remains in enriched typed IR and review
metadata.

Native Quadrant also emits `accTitle`/`accDescr` as SVG accessibility metadata. Its separate explicit
`title` is canvas content and is included in semantic OCR together with both axis endpoints, supplied
quadrant labels, and point labels. Coordinate numbers are point geometry rather than native canvas
text, and accessibility directives are not counted as content labels. Without an explicit
description, derived `accDescr` lists at most five point labels in source order without inferring
coordinate trends or quadrant membership. If the binary64/pixel/text visibility gate fails or native
runtime validation fails, the same candidate slot uses a disconnected exact-value Flowchart. This
terminal preserves resolved accessibility metadata and counts its title, axes, slots, and
`label · x X, y Y` cells as actual canvas text.

Explicit `title`/`description`/`acc_title`/`acc_description` values are checked for non-empty bounded
text before accessibility enrichment. An empty directive therefore cannot trigger the Mermaid grammar
error in which the next axis directive is consumed as title/description. Automatic publication
requires exact OCR/vector evidence independent of axis/point observations, or an exact `user_edit`
from the initial reconstruction; each supplied slot label must likewise have independent evidence in
the corresponding source quadrant. A `user_edit` newly created by an engine, an edit without a bbox
that cannot prove slot position, or reuse of axis/point evidence is not authorization. Visible native
or fallback compatibility glyphs are disclosed by warnings while semantic source remains in enriched
typed IR/review metadata. The current evidence model has no immutable target role that distinguishes
title from description, so independent observations prove only exact content existence. Best-effort
policy does not silently claim that swapped roles are correct and emits a limitation warning; strict
validated policy requires review until role-bound provenance is available.

Native Radar supports `accTitle`/`accDescr` in pinned Mermaid 11.16 and emits both directives as SVG
`<title>`/`<desc>` metadata. Only the separate explicit `title` is visible on the radar canvas.
Semantic OCR contains that visible title, axis labels, and series legends when `showLegend=true`.
Values, `min`/`max`, `ticks`, `graticule`, and accessibility metadata are geometry or hidden options and
are not content OCR text. The same-slot Flowchart fallback also preserves resolved accessibility text
as metadata, but does not copy the native-only canvas title; its OCR consists only of series-subgraph
labels and `dimension: exact-value` cells. Whenever native/fallback visible text changes to a
compatibility glyph to avoid Mermaid grammar, the corresponding candidate warning discloses it and
the source remains in typed IR and review metadata.

## Direct Mermaid

Raw/direct candidates are not modified solely on the basis of their predicted type. The original
candidate is first security-scanned, parsed, and rendered. Missing directives are inserted only after
the canonical runtime type is confirmed as a supported grammar. The augmented source is accepted only
if it passes full validation again without runtime type drift. Otherwise, the original valid candidate
is retained with a warning.

Numbers inside accessibility metadata are not chart data. Accordingly, only when the detected grammar
supports the relevant directive does `numeric_consistency` exclude `accTitle: ...`, one-line
`accDescr: ...`, or the complete body of block-form `accDescr { ... }` from its generated multiset.
Native `title ...` and colon-form `title: ...` are likewise excluded only at metadata boundaries
supported by the grammar; actual numbers in chart labels, coordinates, and values remain. In a grammar
such as Sankey that does not support these directives, a metadata-like CSV label is real data and is
not removed. OCR recall continues to evaluate source-label coverage.
