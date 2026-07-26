# Security model

## Threat scope

Mermaid input is untrusted. LLM output, OCR, fixtures, and user edits all pass through the same
validation path. Mermaid's own `securityLevel: strict` is not the product allowlist. Because
external links can remain in SVG, the system uses layered defenses:

1. byte/candidate budgets and the product source scanner
2. a Chromium context with network access blocked
3. `mermaid.parse()`
4. `mermaid.render()`
5. SVG XML reinspection
6. publication-policy evaluation

Native C4 parses and renders in Mermaid 11.16, but its generated SVG contains a `data:` image that
uses an undeclared `xlink` prefix. The strict SVG/XML gate is not relaxed; an `architecture-beta`
fallback is preferred. If the runtime rejects that Architecture grammar as well, one nested
Flowchart is created in the same candidate slot and is independently subjected to source security
scanning, parse/render, SVG inspection, and terminal runtime-type validation. Architecture,
Deployment, and Component use the same revalidation boundary as C4. If the fallback fails, only
that candidate remains invalid. Every loss and transition is recorded in candidate warnings, the
fallback chain, and repair history without increasing the candidate budget or security allowlist.

Architecture service/group labels use a quoted-Markdown terminal plan that separates directives,
callbacks, URLs, remote icons, CSS imports, and statement-like tokens with source-only separators.
Visible compatibility glyphs are used, and a warning is recorded, only when Mermaid 11.16 cannot
display quote, Markdown delimiter, or numeric-entity-like spelling literally. `accTitle` and
`accDescr` use a separate grammar-scoped `#NN;` codec that separates active spellings while
preserving the original SVG metadata text. This numeric codec may not be used to obfuscate an
executable statement or Direct Mermaid; the final source scanner, parse/render, and SVG inspection
still apply. C4, Deployment, and Component also retain a raw accessibility snapshot and rebuild the
codec input after every accepted repair. Their Phase 2 projection forbids endpoint `str()` coercion
and falsey-label fallback, preventing invented edges through real `"None"`/`"1"` IDs and preventing
malformed labels from being laundered.

## Input prohibited under `strict`

- `click`, callbacks, and JavaScript
- `http:`, `https:`, `ftp:`, `file:`, `data:`, and protocol-relative external references
- init/config directives (`%%{...}%%`)
- script, iframe, object, embed, link, style, img, and svg HTML tags
- `@import` and external CSS `url()`
- remote icon packs
- `style`, `classDef`, and `linkStyle`

`style-only` permits only the last three Mermaid style statements and continues to reject external
URLs and CSS. `trusted-local` output cannot be published to automatic Markdown.

Runtime-produced SVG and PNG artifacts are each limited to 16,000,000 bytes, measured as UTF-8 or
raw bytes respectively. SVG must pass XML and external-resource inspection. PNG must have a real
PNG signature/format, no dimension greater than 8,192 pixels, and no more than 50,000,000 pixels in
total. Invalid or oversized SVG fails the render hard gate. If only an optional PNG is invalid,
Mermaid/SVG publication remains valid but the preview bytes are discarded. Newly returned Review
validator PNGs use the same checks, while compatibility for reading existing bundles is retained
separately.

Validation/publication HMAC seals are process-local capabilities that prevent public model
constructors, JSON round trips, and ordinary post-validation mutation from impersonating a
validated result. Engines and plug-ins running in the same Python process are trusted code. The
seal does not sandbox hostile Python that can access underscore/private APIs or module memory.
Untrusted extractors and plug-ins must run in a separate OS process or container and pass only
validated IR or images into this process.

The source scanner treats a semicolon as a possible Mermaid statement boundary in addition to the
start of a line. In `flowchart` and `graph`, quoted-label state spanning LF or CRLF starts only when
a double quote follows a real node opener or bracketed subgraph-label opener. An arbitrary double
quote in `class`, `direction`, a Gantt title, or accessibility text cannot hide the next statement.
Apostrophes, backticks, and backslashes are not quote delimiters or escapes, so same-line `click`,
`style`, `classDef`, and `linkStyle` still follow the active profile's rules. Only a quoted node
label, `accTitle`/`accDescr` text, or a `%%` comment after optional leading whitespace prevents
semicolons and keywords within it from being mistaken for statements.

State's `state ID <<choice|fork|join>>` is Mermaid pseudo-state syntax, not HTML. The source scanner
allows this angle syntax only when the first statement is exactly a `stateDiagram` or
`stateDiagram-v2` header and the entire declaration line consists solely of a normalized identifier
plus one of the three stereotypes. The same text in a Flowchart, an unknown stereotype, and HTML
suffixes remain `html` findings. Bare email and `www` autolinks in State labels are disabled with
source-only separators after `@` and inside `www.`, preserving visible text. Source-only separators
also disable behavior for ordinary `<b>` text and the `<` in formulas.

Accessibility-prefix inspection in the statement scanner is limited to 128 characters and does
not repeatedly compare later `:`/`{` characters with the full prefix. HTML-like source is also
found with a linear per-line scan over complete tag, closing-tag, comment, doctype, or processing
instruction candidates. The scanner does not skip whitespace after `<` and does not borrow an
unrelated later `>` from `-->`; comparison labels such as `x < y`, `0 < x < 10`, and
`x < y > z` therefore remain text. Repeated punctuation in a maximum-size label cannot amplify
regex/statement work beyond the candidate budget. Abnormal indentation past the limit is not
relaxed into accessibility-text state; it fails closed as an ordinary statement.

If a State node ID collides with lexer/security-reserved tokens such as `state`, `class`, `click`,
`accTitle`, or `as`, or its normalized ID contains the strict remote-icon rule's `iconify`
substring, the serializer retains source identity and evidence while allocating a collision-free
`mmx_state_id_…` emitted ID that contains no dangerous token. All normalized IDs are reserved first,
so a user-provided matching alias cannot overwrite another node. This mapping is an inactive
identifier substitution, not a scanner bypass, and is shared by Scene endpoints and Mermaid edges.

An ER relationship role is always emitted as one double-quoted terminal. Mermaid 11.16 is not
allowed to parse unquoted multiword text as a short role followed by phantom entity statements, and
a semicolon inside the role cannot open a new statement. Entity aliases, attribute types/names,
comments, and roles must pass exact built-in-string, bounded normalized-text, UTF-8, and
non-whitespace control/format/surrogate restrictions for their specific ER grammar positions.
Attribute keys are limited to `PK`, `FK`, and `UK`; when a type/name conflicts with a key token or
plain-word grammar, it is isolated in a backtick terminal.

If an ER source ID collides with the lexer/security namespace, including `erDiagram`, `style`,
`classDef`, `class`, `one`, `many`, `to`, `click`, `linkStyle`, or `__proto__`, or contains the
`iconify` substring, the serializer emits a collision-safe `mmx_er_id_N[_suffix]`. Source identity
and evidence remain in typed IR, while entity declarations, relationship endpoints, and the
generated Scene share the alias. This is not an allowlist exception for prohibited statements; it
avoids emitting active tokens as source identifiers.

ER terminals that Mermaid 11.16 cannot preserve literally use position-specific visible
compatibility glyphs. Entity quotes, percent signs, grammar-active backslashes, attribute
backticks, active Markdown/entity-like text in comments and roles, and accessibility numeric-entity
substitutions are disclosed by a warning, while semantic originals remain in typed IR. URLs,
callbacks, directives, style/control words, and remote-icon patterns receive source-only
zero-width separators that disable lexer/scanner behavior. Those separators are removed from the
Scene/OCR canvas and do not change visible text, so they do not by themselves produce a
compatibility warning. Raw source must still pass the ordinary strict source scan, Mermaid
parse/render, and SVG reinspection.

Raw ER `title`/`description`/`acc_title`/`acc_description` fields are checked before accessibility
enrichment and semantic-repair serialization for exact built-in string type, raw and normalized
length, UTF-8, and Unicode category. Only absent/`None` and exactly empty omitted values are
accepted. Whitespace-only values, subclasses/hooks, and control/format/surrogate/line-separator text
are isolated at candidate scope before they can be laundered into derived accessibility text.
Initial candidates and repairs each rebuild record/accessibility plans from a validated raw
snapshot. An accepted repair reconciles derived text and visible-substitution warnings with the
current plan.

Raw Gantt `title`/`description`/`acc_title`/`acc_description`/`date_format` fields undergo the same
exact built-in-string, raw/normalized-length, UTF-8, and Unicode-category checks before generic
enrichment and semantic-repair serialization. Only absent/`None` and exactly empty omitted values
are accepted. Whitespace-only values, subclasses/hooks, and control/format/surrogate/line-separator
text are isolated before they can be laundered into derived accessibility text. The separate
accessibility plan uses only semantic section/task labels. Explicit `description` and
`acc_description` remain authoritative after repair; a description is rederived from the repaired
IR only when both are absent.

Task status is a closed token set and may not contain both `active` and `done`. Each task requires
exactly one end or duration and bounded task-ID/start/end/duration/date-format syntax. Task IDs must
be unique across the diagram and reject runtime tags `active`, `done`, `crit`, `milestone`, `vert`,
`__proto__`, and the `iconify` substring. `,`, `#`, and `;`, which could append schedule fields, are
also rejected. Only supported numeric date-format tokens are converted to parsing formats, blocking
invalid calendar dates. End must be after start, except that a milestone may be equal. `h`/`hh`
must be paired with `A`/`a`. Zero-width end-date forms `Z`/`ZZ` and `S`/`SS` are rejected; only `SSS`
is supported. Seconds timestamp `X` is rejected because of Mermaid 11.16's unit mismatch.
Milliseconds `x` requires canonical decimal without a leading zero and must remain within the
ECMAScript Date maximum. Resolved ends after duration and prior-only `after` chains must also stay
within that maximum. Durations must match exact decimal-plus-unit grammar; Mermaid-rounded
fractional `ms`/`d`/`w`/`M`/`y` values are rejected. Fractional `h`/`m`/`s` must convert to an exact
positive integer number of milliseconds, and the total magnitude must remain inside the bounded
runtime range. Exact zero is allowed only for a milestone. An `after` target must be an existing
unique ID that precedes the current task in source order. This backward-only gate prevents forward
or partial resolution and cycles. Combining `after` with an end date, and every `until`, fails
closed.

For runtime type `gantt`, every rectangle in the final SVG with whitespace class token `task` must
have finite, positive width and height. This pinned-runtime shape gate does not apply to a `task`
class in other diagram types. It isolates a mixed-scale, zero-width task as render-invalid before
certification and publication even if it appears parse/render-valid. Standalone/Review SVG
inspection where runtime type is unknown applies the same gate only when the root has
`aria-roledescription="gantt"`.

Gantt label source neutralizes directive openers, `//`, URL/data/JavaScript schemes, `@import`,
callbacks, remote icons, config and Gantt control words, numeric entities, `---`, and a task-leading
ISO date with visually inert zero-width separators. Normalized canvas text and Scene/OCR remove the
separators, although raw SVG DOM text/title/description can retain them. When Mermaid 11.16 cannot
preserve a task's `:`/`%` or a title/accessibility `<` literally, the serializer uses visible
`∶`/`％`/`‹` and records a compatibility warning. Every task `%` becomes fullwidth, so
directive-like task text also uses visible substitution; plain `%%` in a title/section may remain
literal when it is not an active directive opener. Initial and repair candidates revalidate their
raw snapshots and record/accessibility plans, and an accepted repair reconciles warnings with the
current plan.

Observing the prohibited tokens in a specialized typed-serializer label never emits them as active
statements. Zero-width separators inside keywords and URL-like tokens disable behavior in both the
scanner and parser. Source `&` in a Flowchart label is separated the same way to prevent entity
reinterpretation. Event Modeling edge `|` and `;` display as `∣` and `⁏`, whose NFKC forms do not
restore delimiter/statement behavior. Quotes, backslashes, and entity-like literals use the
`″`, `∖`, and `＆`/`＃` glyphs that pinned Flowchart SVG actually preserves, with a warning. Source
control/format characters and line/paragraph separators are rejected before whitespace
normalization, preventing invisible-character bypasses. Packet, TreeView, and Ishikawa use encoders
matched to the actual SVG behavior of each native grammar; characters that cannot be preserved
switch to a verifiable Flowchart fallback with a compatibility warning. Unsafe original
accessibility text remains only in typed IR/Review metadata, while automatic SVG receives generic
wording. Native/fallback labels and SVG titles/descriptions are covered by pinned Mermaid
integration tests.

Native Sequence never emits a participant's source ID directly as a Mermaid identifier. A
source-ordered `mmx_sequence_participant_N` namespace is shared by declarations, every message
endpoint, and the generated Scene, separating it from lexer/scanner tokens such as `participant`,
`end`, and `style`, from `__proto__`, from the `iconify` substring, and from future reserved words.
The source ID remains in typed IR and provenance. Because literal `;` in participant/message text
can pass strict scanning and parse/render while injecting a statement, every `#` and `;` is encoded
character by character as native `#35;`/`#59;`. Compound entity-like literals retain exact SVG text
under this order.

Directive openers, comments, URL/data/JavaScript schemes, callbacks, remote icons, config, and
style/control words receive source-only zero-width separators before raw and NFKC strict scans and
the pinned runtime. Quotes, backslashes, colons, and Markdown punctuation are not replaced because
the native Sequence canvas preserves them. Only accessibility angle brackets display as `〈`/`〉` to
avoid Mermaid 11.16 double-escaping, with a warning. Raw accessibility fields pass exact-string,
Unicode, and bounds gates before generic enrichment and repair. The candidate stores a validated
raw snapshot so a malformed or empty directive cannot swallow the next source line or be laundered
into derived text.

Native Mindmap does not emit a user logical ID as a source identifier; it uses the preorder
`root`/`node_N` namespace. Every root/child label sits inside a quoted shape terminal. A generated
leading sentinel and source-only zero-width separators disable directive openers, comments,
URL/data/JavaScript schemes, callbacks, remote icons, config, and click/style/control words.
Backslash, underscore, bracket, and parenthesis separators disable only Mindmap Markdown
escape/link and shape-delimiter interpretation and disappear from the visible canvas. A
source-authored named-entity prefix is separated, while actual literal `<`/`>` characters alone are
XML-entity encoded, distinguishing and recovering `&amp;` spelling and angle text. Ordinary spaces
are placed on both sides of separators so adjacent words do not join in SVG text.

Where pinned Mermaid 11.16 cannot retain a quote, active asterisk/backtick/tilde, or
numeric-entity-like spelling literally, visible `″`, `＊`, `ˋ`, `～`, and `＆`/`＃` glyphs are used
and disclosed in candidate warnings. Semantic originals remain in raw typed IR. Node labels/IDs and
top-level accessibility metadata pass bounded exact-string, UTF-8, and Unicode-category gates
before coercion; only exactly empty aliases/metadata are omitted. Initial and repair paths both
rebuild plans from validated raw snapshots, preflight expanded source at 50,000 UTF-16 units and
5,000 lines, and still apply the ordinary strict scan, parse/render, and SVG inspection. An
accessibility directive would become another root, so native source does not emit it.

Native Timeline grammar can reinterpret a period beginning with `title` or `section`, or containing
`%`, `#`, or a literal colon, as a title, section, comment, or additional event on the same line.
The renderer can also decode entity-like spellings while losing surrounding spaces. The serializer
therefore prefixes normalized title/period/event terminals with one generated zero-width sentinel
and emits every ASCII code point as a Mermaid numeric entity. The lexer and strict scanner no
longer see user directives, URLs, callbacks, HTML, click/style, comments, or delimiters as active
source, while the Mermaid 11.16 renderer restores quotes, backslashes, colons, semicolons, `#`,
literal entity spellings, and whitespace exactly on the canvas. The sentinel may remain in source
or the SVG DOM, but it is visually inert and excluded from Scene/OCR canvas text. Unicode
control/format/surrogate input fails the semantic gate.

Raw `title`/`description`/`acc_title`/`acc_description` fields pass exact-string, bounds, and Unicode
gates before enrichment and repair, with only exact `""` omitted. Timeline runtime does not turn
accessibility directives into real SVG metadata, so automatic source omits them. Candidates store
only the raw snapshot and resolve values from the current record plan when needed. Final code,
including source-encoding expansion, is preflighted at 50,000 UTF-16 units and 5,000 lines so
numeric expansion cannot bypass runtime budgets.

The ZenUML Sequence fallback emits participant source IDs through a reserved
`zenuml_participant_*` namespace and uses only those namespaced endpoints in messages. Because
Mermaid messages have no ID syntax, `zenuml_message_*` exists only as a Scene/provenance slot.
Alias/message `#`, `;`, and entity-like literals display as `＃`, `⁏`, and `＆`/`＃`; active
keyword/URL/callback/config tokens are split only in source with invisible separators. `accTitle`
and `accDescr` share active-token, entity, and `#` rules, but display `<`/`>` as NFKC-stable
`〈`/`〉` because Sequence accessibility SVG double-escapes angle brackets. A semicolon proven to be
text in the one-line accessibility grammar retains its original glyph. Substitutions are recorded
as compatibility warnings.

Wardley and Cynefin native serializers also reject control/format/line-separator characters before
normalization and use visible `＆`/`＃` compatibility glyphs where the renderer would lose
entity-like literals. Warnings record the substitutions, while originals remain in typed IR and
sidecars. Their strict nested contracts reject booleans, NaN, and infinity for Wardley `x`/`y`,
integer/string coercion for `anchor`, and any Cynefin domain outside the closed official token set.
If runtime rejects native `cynefin-beta`, one explicit-domain `flowchart LR` is generated in the
same candidate slot. The fallback independently receives raw/NFKC source scans, strict Flowchart
label neutralization, parse/render, SVG inspection, and terminal-type checking, without increasing
the candidate budget or allowlist. It invents neither the fixed runtime template nor membership
connectors and retains requested/emitted/runtime metadata, fallback history, and requested-type
accessibility sanitization. Only a validated fallback that meets the generated-node attribution
threshold can proceed through normal publication gates; success of the security fallback does not
remove the native result's template-provenance review hold.

Organization and Data Lineage fallbacks do not emit raw IDs directly as Mermaid identifiers; they
use type-specific namespaces and normalization-collision checks. Labels reject control/format/lone
surrogates before normalization and replace quote, backslash, entity-like literals, relation
delimiters, edge-grammar `()[]{}@`, and accessibility angle brackets with compatibility glyphs
visible in the pinned runtime. Edge `@` displays as `＠`, with an additional source-only zero-width
separator that also keeps NFKC `@import` inactive. Active keywords and URL tokens are disabled only
in source, while originals remain in typed IR and sidecars. A Data Lineage relation reaches
Flowchart only when its endpoints are resolved, non-self, and non-duplicate.

Railroad first bounds expression variants and payload containers with a strict discriminated
contract, then a shared plan checks rule-name collisions, references, depth, and record budgets.
Because the native grammar cannot safely preserve ASCII angle brackets, any ASCII `#`, an
entity-like `&` prefix, or an NFKC quote/backslash hazard, it converts them respectively to canonical
visible `〈`/`〉`, `＃`, `＆`, and `″`/`∖`. Mermaid's global `encodeEntities` also mutates bare
`#word;` and `#35;`, so ASCII `#` has no exception and always produces a warning. Original semantic
fields remain in typed IR and sidecars. Source-only zero-width separators disable active
URL/directive/callback/HTML-like tokens and compatibility-normalized hazards in rule, terminal,
nonterminal, special, and accessibility text. `style...:#...;` and `classDef...:#...;` substrings
are also split only in source so Mermaid's preprocessor cannot promote them to statements. Both
the emitted source and its NFKC-normalized form pass the strict scanner independently; the
candidate is rejected if either retains an active rule.

Scanner/preprocessor-active names that are unsafe under Railroad identifier grammar, the case-folded
expression-word namespace, `railroad-beta`, and a case-folded lowercase `title*` prefix are mapped
from logical `railroad_rule_*` IDs to collision-safe `rrmapped_N[_suffix]` native names, with a
visible-change warning. Logical `railroad_rule_*` IDs remain only in Scene/provenance, while
Scene/OCR records the actual mapped `native_name =` text. A normalized safe rule name remains
unchanged. The allocator reserves every safe native name before using suffixes to avoid collisions.
For a mapped rule, the source name remains in typed IR/sidecars in raw form and in nonterminal
labels in normalized form. Other generated Scene/OCR labels use exact canonical compatibility text
without separators; the original AST remains in typed IR/sidecars. Direct Scene projection also
fails closed unless `evidence_ids` is null/omitted or a string list.

Production applies strict source scans to raw and NFKC-normalized emitted source, then sends only
raw source to the CandidateValidator parse/render hard gate. The pinned integration fixture's NFKC
parse/render is a safety probe that confirms bare hashes and `style`/`classDef` substrings cannot
create grammar injection; it does not require normalized SVG to display the same compatibility
glyphs as raw SVG. Rule names containing those substrings reach the runtime only after
source-active mapping.

Generated source from Wardley, Cynefin, Event Modeling, ZenUML, Organization, Data Lineage, and
Railroad serializers is rejected at whole-candidate scope before security scanning if it exceeds
50,000 characters or 5,000 lines.

A per-record limit of 256 source-block references in the `VisualEvidence` model does not bound
list/object fan-out across an entire collection. Every retained collection therefore limits
`source_block_ids` to 20,000 occurrences and the sum of their Python string lengths to 8,000,000
characters. Duplicate IDs count on every occurrence. The separate 8,000,000-character evidence
limit across `id`, `kind`, `text`, `font_weight`, and source-block IDs also remains. Exact boundaries
pass; `+1` in any dimension atomically isolates the collection or reconstruction-global new-ID
batch.

The shared snapshot reads an exact plain list and exact public `VisualEvidence` fields once through
hook-free built-in access and creates detached records from in-budget scalar/nested-list values. It
does not call live `model_dump`, deep copy, or JSON serialization before validation. Mutable `kind`
and `font_weight` values are checked against maximum literal length and allowlists before UTF-8
encoding. Pipeline initial/custom-engine input and global admission, fusion ingress/output, final
results, and publication/Markdown snapshots share this boundary. Sidecar output revalidates the
entire result before JSON/deep copy/directory creation, and document output does so before image
writes, preventing post-mutation partial artifacts. This internal defense does not change public
configuration or the sidecar schema/manifest version.

The Marker OCR adapter admits each source crop/OCR token against the shared cumulative budget before
appending it. For an over-budget collection it clears evidence and OCR text context together,
records a bounded error, and continues source reconstruction. Review canonicalizes raw dict/model
inputs into detached records one by one before root/revision provenance reads, trusted replacement,
content digest/commit, or structured `user_edit` insertion. The standalone Structured VLM adapter
applies the same snapshot to all prior evidence before view validation, prompt selection, or a
provider call. Thus an unselected tail with duplicate source-block fan-out, or nested-list mutation
during capture, cannot cross the provider boundary.

The evaluation prediction importer applies the same raw-record admission before converting a
hash-verified plain JSON array into `VisualEvidence` models. It accepts the exact boundaries of
20,000 source-block occurrences including duplicates and 8,000,000 Python characters, while `+1`
atomically rejects the manifest without invoking the report writer. To preserve the public
prediction `0.1` contract of 100,000 records and 64 MiB per artifact, evaluation uses the artifact
byte limit for the full-evidence character limit. The bounded raw object tree produced by the JSON
parser exists before this validator; fully streaming parsing remains separate isolation work.

PDF vector providers are not trusted collections either. Vector sources, raw text/drawings, and
PyMuPDF drawing commands are not fully materialized; the system reads at most one past each bound
to detect overflow. A reconstruction shares defaults of 256 sources, 2,048 primitive/command
records, 5,000 text records, and 8,000,000 text characters. The configured primitive and text
maxima together may not exceed 20,000 observation-evidence records, the primitive maximum may not
exceed 5,000 Scene nodes, and the character maximum may not exceed the shared evidence-input
limit.

Budgets are consumed by raw attempts rather than only by valid records after validation or
deduplication. Malformed, out-of-crop, duplicate records and empty nested drawing containers consume
work just like valid results; once a count or character dimension closes, a later source cannot
reuse it. A polygon above 256 points or a polyline above 512 points is rejected whole so truncated
geometry never becomes provenance. Total retained geometry is limited to 100,000 points, while
non-label tokens such as kind, command, color, and style are limited to 256 characters each. Exact
duplicates are hash-deduplicated. Approximate bbox deduplication stops after 250,000 comparisons,
and text and endpoint ownership each stop after 1,000,000 comparisons with a fail-closed warning.
Warnings are limited to 256 per observation.

Custom extractors and directly injected `VectorObservation` values cannot authorize themselves
through work metadata; engine/Scene boundaries re-bound and clamp them. These checks do not rely on
eventual Pydantic rejection or post hoc O(n²) deduplication and run before fully consuming an
external iterable or handing it to downstream validation. A duck-typed text span from either a
direct attribute or `get_text("dict"/"words")` has its label read once into a plain snapshot. Exact
string length is charged to raw character work before parsing or `strip()`. Numeric scalars must be
exact `int`/`float` values safely convertible to finite float, isolating enormous integers as well.

Separate from the per-record 256-source-block-reference gate, the final vector boundary performs an
earlier allocation preflight using the shared constants. It calculates the aggregate provenance
that would duplicate canonical, deduplicated source-block IDs onto every valid and deduplicated
shape, text, and open-line evidence record. The reconstruction permits exactly 20,000 logical
references and 8,000,000 Python string characters. If either reaches `+1`, the entire vector
observation is isolated as an unknown prediction with empty Scene/evidence and one warning before
any Scene or `VisualEvidence` object is created. No bounded prefix therefore becomes publication
authority. The boundary applies to built-in, direct, and custom extraction. Failure stays inside
that engine while other reconstruction engines continue. A payload-free warning is retained in
the result and sidecar manifest as a bounded generation failure. These aggregate limits are
internal security policy, not public configuration or API.

Source mapping is also frozen once as a bounded index in built-in `observe()`, rather than rescanned
linearly for every vector source. An exact built-in placement list/tuple accepts at most 256 entries
and uses one-entry lookahead. If entry 257 exists, the entire index is invalid and no partial prefix
is used. An exact list/tuple `source_block_ids` on each placement is likewise indexed up to 256;
entry 257 omits that placement's block and page-plus-block keys. The placement itself and valid page
key remain in all/page ambiguity, preventing partial block-ID authority or a false unique match.
The index stores only exact-dict placement references and does not parse affine transforms or bboxes
during construction, so a malformed-transform placement is not pre-excluded in a way that removes
ambiguity.

The index builds all/page/block/page-plus-block tuples and performs only O(1) dictionary lookups for
each source. An explicit source page ID fixes that page first, so a block key cannot escape to a
placement on another page. A present but invalid page ID does not select even a sole placement.
Placement block keys are exact bounded strings. Source identity is an exact bounded string/integer
or the canonical string of a field-validated Marker `BlockId`; arbitrary `str()`, hash, or equality
hooks are not invoked. Only a unique lookup result causes lazy parsing of the selected affine/bbox,
and an invalid selected affine fails closed to bbox fallback. This index is an internal built-in
integration defense and does not expand public configuration or API surface.

These provider limits begin when returned values are consumed. Duck-typed properties/callables,
custom extractors, `get_text()`/`get_drawings()`, and internal library materialization do not yet
have wall-clock or RSS isolation. Provider implementations must therefore be trusted local code.
An untrusted provider requires a separate worker process with kill/reap resource limits.

Style recovery never copies Scene IR values directly into CSS. A node or edge must be supported by
a collision-free contour/line freshly registered for the current source block by the exact
built-in PDF vector engine, with bbox/endpoint ownership established. An edge also requires
agreement across source, vector, generated, and code direction representations. Only these trusted
vector values may supply hex or limited named colors, `stroke-width`, and `stroke-dasharray`.
Vector-backed label weight is limited to the constant `font-weight:bold` and requires both
registered bold `vector_text` evidence and unambiguous source-to-candidate mapping. Evidence is
rechecked for actual vector-engine origin, unique ID, source-bbox-contained span, and matching
source/candidate/span labels. A VLM or fixture cannot become style authority by self-declaring a
color or vector evidence kind/ID. Under `strict` or `portable-basic`, code is unchanged and evidence
remains only in IR. Edge color/style is emitted as one `linkStyle` only when every Mermaid edge can
be mapped in exact order. Generated style code still passes the same source scanner and SVG
inspection.

Typed-to-fused Flowchart/Generic Network node-ID mapping does not gain authority from engine-declared
bbox/owner strings alone. Before invocation, the system checks evidence payloads, the trusted
source-image canvas and block set, and owner-local geometry contours, then seals the mapping record
immutably. The sidecar writer rechecks the private pipeline certification seal, claim digest,
current evidence schema, fused-node reference, and source block. On any mismatch it removes the
temporary bundle and does not publish `node-id-map.json`.

The interactive Review workspace applies the same strict scanner and parse/render/SVG checks before
saving. It provides no `trusted-local` callback executor, so `click` and callbacks remain rejected.

## Review HTTP boundary

The Review server binds to `127.0.0.1` by default. Mutation APIs require a per-session CSRF token,
same-origin `Origin`, JSON content type, a 1 MB body limit, and optimistic version/digest values. A
request `Host` must match the actual listener, so a loopback DNS-rebinding host cannot obtain even
bootstrap content. Concurrent HTTP requests are limited to eight slots by default; excess requests
receive 503 without creating a thread. Every accepted socket has a default 10-second timeout so an
incomplete-header slowloris cannot hold a slot indefinitely. Additional hostnames for a wildcard
listener must appear in the exact `--allowed-host` allowlist. HTML contains no inline script/style,
and CSP uses `script-src 'self'`, `connect-src 'self'`, and `frame-ancestors 'none'`. The artifact
server exposes only the original image and `final.svg/png`; revision/state files are not available
over HTTP.

The source overlay assigns its same-origin allowlisted URL only to a new image element. It performs
no pixel readback and creates no object URL, extra canvas, or fetch path. On a URL change, the old
element and focusable bboxes are destroyed, and only a load matching the current request identity
can restore Scene-coordinate overlays. Allowed static paths are opened component by component using
an `openat`-style directory descriptor and `O_NOFOLLOW`, and the inspected descriptor itself is
streamed, rejecting symlinks and check/use substitution. Validator-produced SVG/PNG is limited to
16 MB each before saving. Undo/redo places creation and deletion of optional IR/SVG/PNG in the same
rollback boundary.

The server has no user authentication. A non-loopback bind such as `--host 0.0.0.0` is suitable only
on a trusted isolated network, and the CLI emits a warning. A CSRF token is not authentication.

Approval never reuses prior validation metadata. After checking the current digest as an optimistic
lock, the same strict validator reparses and rerenders the code, then records the successful new
render artifact and approval revision together. An API embedding without a configured Review
validator cannot approve.

Structural-operation APIs use a closed discriminated schema separate from natural-language
commands. They currently permit only source-backed node-label selection/addition/deletion, edge
addition/reconnection/label setting/deletion, group creation/deletion, and advisory node movement.
Mutation is rejected before execution when existing IR relations/nodes do not map exactly to
independent Mermaid lines, or when out-of-scope groups, styles, chained/labeled edges, or other
references are present. The optimistic revision is checked both before interpreting the operation
against IR and inside the actual commit lock.

Node addition requires a safe ID/label, reason, positive bbox, and explicit Scene canvas bounds. The
server creates revision-based `user_edit` evidence, so the client cannot choose evidence ID, kind,
score, or source. Movement payloads accept only a current Scene node ID and a finite, non-boolean
`0..1` center; they cannot include bbox, style, URL, or evidence fields. `layout-hints.json` is
validated against a content-addressed revision and manifest digest but makes no provenance claim.
The server offers neither movement that reuses a source bbox as Mermaid layout coordinates nor
free-placement addition without provenance.

`group_nodes` prevents the client from choosing group ID, bbox, provenance, layout, or a Mermaid
fragment. The server constructs a deterministic group ID and exact bbox union from unique safe node
IDs in Scene order, checking finite/non-boolean/order/canvas bounds for each member bbox. Before the
operation, existing Scene groups and bounded flat Mermaid subgraph membership must correspond
one-to-one by ID and members. Nested or unbalanced subgraphs, duplicate/overlapping membership,
implicit or duplicate node declarations, and group/node ID collisions reject the whole
transaction. Only the group label undergoes single-line length/escape validation before becoming a
quoted subgraph label. Final code must again pass the same strict parse/render/SVG gate.

`add_edge` accepts only source/target IDs in a closed payload and requires a bounded, top-level
evidence note. The server fixes relation/evidence IDs and relation type, semantics, arrow, and
confidence from the next revision; label, style, and polyline input are not accepted. Before
addition, it compares the full existing IR ordered-endpoint multiset one-to-one with the Mermaid
plain-edge multiset. A non-plain, labeled, chained, or bidirectional edge signal fails closed. Each
endpoint node must also have exactly one quoted rectangle declaration. Generated evidence is
limited to kind `user_edit`, `bbox=None`, the note text, and current source-block IDs.

`delete_edge` performs the same global mapping preflight, then verifies a stable relation ID, unique
ordered pair, and unique plain line and removes only the validated line index. It rejects any
`linkStyle` token outside comments and quoted node labels, preventing an edge ordinal from being
misapplied to another style. Deletion retains evidence so undo can reconnect the relation to the
same provenance. For both operations, strict-render failure or a stale optimistic lock commits no
code, IR, render, history, provenance, or layout file.

`set_edge_label` accepts only a stable relation ID and required `label` in a closed payload. The
label is either `null` or a non-empty single-line string up to 200 characters without
control/format/surrogate/line-separator characters, and it is placed inside a quoted pipe wrapper.
Literal `|` is retained inside the quote. Double quotes and backslashes display as compatibility
glyphs `″` (U+2033) and `∖` (U+2216), while U+200B is placed after `&`, `<`, and `>` to prevent
Mermaid entity/HTML reinterpretation. The original remains in Scene IR and audit history. The label
is rejected if the transformed source still matches a source-wide prohibited external or
protocol-relative URL, directive, callback, HTML, CSS import, or remote-icon pattern.

The server first maps every Scene relation one-to-one to an independent Mermaid edge by ordered
endpoints and canonical label. Parallel edges, chained edges, unsupported connectors, label
mismatches, and ambiguous lines fail closed. On success, only the target relation's `label` and the
exact one edge's quoted `|"..."|` segment are added, replaced, or removed. Provenance and
`evidence_ids` remain unchanged; user input does not become new visual evidence. Setting the same
label is rejected as `no_change`. Optimistic lock, full Scene schema, strict parse/render/SVG,
validated revision, and undo/redo gates match those of other structural operations.

`delete_group` accepts only one stable `group_id`. Before deletion, it revalidates every Scene
group's safe ID, group/node collisions, disjoint existing members, exact bbox union, and every flat
Mermaid subgraph's ID/member mapping and grouped-member declaration count. The bounded parser
removes only the line slice from the target header through its matching `end`. If the same group ID
token appears outside the target block, the operation rejects rather than guessing about dangling
style/class/click/reference use. Member elements, relations, bboxes, provenance/layout, and source
artifacts are unchanged, and the strict-render/optimistic-revision transaction is retained.

Endpoint dragging on the advisory canvas creates no separate mutation authority. The client uses
screen coordinates to select one unique nearest node and then sends only relation/source/target IDs
under the existing `reconnect_edge` schema. The payload contains no coordinates, polyline, bbox,
provenance, or layout field. The server reinterprets stable IDs and Scene-to-Mermaid one-to-one
mapping from the current revision. It also rechecks preservation of the opposite endpoint and
rejects self-loops and no-ops. Select-form and drag operations share the same optimistic-lock,
strict-render, and atomic-revision boundary.

Review's read-only difference blend creates no endpoint, canvas readback, pixel upload, or server
artifact. It is off by default and builds a digest-bound descriptor only for the safe source URL
and an existing current `final.png`, using the same-origin static allowlist. The two images are
composited independently with `contain + center`; the result is not alignment or scoring evidence.
Before browser load, PNG IHDR is limited to 8,192 on each axis and 50 million pixels, in addition to
the existing 16 MB artifact limit. Source and render layers recheck decoded bounds and descriptor
URLs, and stale/error events cannot affect the current diff.

Revision restore accepts only IDs consisting of `r` plus at least six digits. It checks optimistic
version/code digest inside the bundle lock before validating active-timeline membership. The API
neither accepts nor exposes revision file paths, cursor indexes, or snapshots discarded from a
branch. Restore uses the same snapshot digest, optional-artifact deletion, provenance/layout content
digest, manifest hash, and rollback boundary as undo/redo and appends a `checkout_revision` user
audit event. Unknown history-payload fields are rejected.

Review provenance/layout validates the root artifact's sidecar manifest hash and the
`mmx-review-0.4.1` current digest. Every revision references a content-addressed provenance digest;
root artifacts and manifest hashes are replaced in the same rollback and undo/redo boundary as
code, IR, and render. Legacy 0.3 state first validates an existing manifest hash and then pins a
static provenance digest during lazy migration. The HTTP editor exposes no provenance-replacement
switch; only a trusted structured operation can replace it explicitly. Every Review commit,
including one with replacement, also verifies that Scene element/relation `evidence_ids` belong to
the unique ID set of current provenance.

A provenance-backed node-relabel payload accepts only `node_id` and `evidence_id`. The server
interprets evidence text from the current bundle after checking optimistic version/digest; the
client cannot inject label, evidence kind/score/bbox, or provenance replacement. Selected evidence
must be unique `ocr_token`/`vector_text`, attached exactly once to the target node and to no other
node. Text must be a single line up to 200 characters without
control/format/surrogate/line-separator characters. Existing provenance and node `evidence_ids`
remain unchanged, and target, before/after labels, and the selected ID enter the user audit. The
resulting code passes the same strict scanner, parse/render, SVG inspection, and commit-lock
optimistic recheck as every other edit.

VLM/fixture JSON receives Scene/IR count, depth, character, point, and ID limits plus finite-number
checks. Sidecar JSON rejects non-standard `NaN` and Infinity. Structured VLM revalidates mutable
evidence as canonical models and rejects input whose aggregate evidence ID/text/source-block
characters exceed 8,000,000 before canonical copying. Evidence and OCR root containers must be
exact plain lists, and the same bounded snapshot of each is reused for later validation and
selection. A selected OCR prefix also accepts only plain `str` values and is rejected before escape
scanning if its total exceeds 8,000,000 characters. The system never serializes all OCR/evidence to
JSON first; it selects within configured item/character budgets and structural quotas. An OCR string
that cannot fit the prompt is screened by a raw-length lower bound and skipped without JSON escape
scanning. If the fixed system/schema/view region plus Marker 1.10.2's canonical response-schema text
reserve exceeds the prompt limit, the external provider is not called. Selected records are
included only as complete JSON units, and the final prompt is rechecked as UTF-8.

Post-construction mutation is also untrusted. Evidence bbox, score, text, font, and ID are checked
first for exact type, finite number, and field size. Nested `source_block_ids` is frozen as a
bounded snapshot that reads at most one beyond the permitted count. New canonical payloads are
created from those snapshots, avoiding later dumps of live evidence models. Trusted connector and
label IDs are similarly copied from exact `set`/`frozenset` values into bounded UTF-8 ID snapshots,
and only the immutable snapshots participate in priority and provenance selection.

The Marker 1.10.2 stock Ollama path that discards nested `$defs` uses a bounded inline-schema adapter
that accepts only local references. External/recursive references or schema-budget overflow fail
before the provider call. Ollama responses still pass the shared canonical-model validation.

Publication provenance authority for a VLM candidate is limited to non-conflicting evidence IDs
actually selected into that call's prompt immediately beforehand. IDs omitted by character/item
budgets, and response-declared `vlm_observation` records with matching IDs, may remain as Review
overlay and sidecar evidence but cannot authorize automatic publication of original, fused, or
repaired candidates. Another trusted engine retains evidence authority canonicalized for its own
call. A fused typed candidate inherits only the selected original owner's closed authority set and
independently certified ID-mapping evidence. A duplicate Direct Mermaid candidate likewise
inherits only the authority of the original owner actually selected, not the union of all fusion
inputs; an explicitly empty set remains empty.

Structured VLM views are validated before the call for a first `original` view, portable names,
RGB Pillow type, and per-view/total pixel budgets. The view dictionary is not fully materialized;
at most one entry beyond the configured limit is read. A caller-owned image is not passed through
after validation. Instead, an independent plain `PIL.Image.Image` snapshot is copied from Pillow's
exact pixel core, dimensions are revalidated, and that snapshot is used by both the view manifest
and provider image list. The caller object's `size`/`mode` properties and `load`/`copy` hooks do not
run on this path. A lazy ImageFile subclass must already be loaded and expose an exact Pillow pixel
core before the call. A Marker preview image exceeding dimension 8,192 or 50 million pixels, or
triggering Pillow's decompression-bomb judgment, is isolated by omitting only the preview.

The reconstruction entry point is a trust boundary separate from engine adapters. It snapshots
source block/page IDs, OCR, initial evidence, and opaque source/vector object lists only from exact
plain lists under item and aggregate-character limits. An invalid or oversized collection is
isolated whole, not partially trusted by prefix, and a safe default plus source-context failure is
used. Initial/custom-engine evidence is isolated if any of these limits is exceeded: 20,000 items,
20,000 source-block occurrences, 8,000,000 source-block characters, or 8,000,000 full-evidence
characters. Reconstruction-global admission precomputes an entire new evidence-ID batch and either
accepts all of it or excludes all of it from publication authority. Before each engine call, image,
view, evidence, OCR, mapping, and trusted-set snapshots are restored so mutation by an earlier
custom engine cannot reach the next. Built-in fusion-candidate status is determined only by an
internal pipeline marker, never by comparing an engine-controlled name.

`source_mapping` is copied by an iterative walker that accepts only exact built-in
`dict`/`list`/`tuple` and JSON scalars. It applies depth 32, 25,000 items, 50,000 characters per
field, 4,000,000 bytes of escaped compact JSON, and finite safe numeric ranges; tuples normalize to
lists. Only built-in container primitives are used, so subclass iteration, lookup, and `deepcopy`
hooks do not run, and reference cycles are rejected. The pipeline passes only this snapshot to
engines and repairs. Before JSON serialization or deep copy, the sidecar writer revalidates it and
publishes only when before/live/snapshot canonical digests agree.

Typed IR uses the same hook-free exact-built-in walker with depth 64, 100,000 items, 50,000
characters per field, 1,000,000 aggregate UTF-8 text bytes, and 4,000,000 bytes of compact escaped
JSON. All typed candidates in one observation may total at most 8,000,000 JSON bytes. Dict keys,
repeated aliases, JSON escapes, and structural separators all count; tuples normalize to lists;
cycles, subclasses, non-finite numbers, and numbers outside the safe range are rejected before
serialization. Pipeline and fusion snapshot explicit fields before dumping a mutated model, and
accessibility/repair results are revalidated. A sidecar replaces selected and alternative live IR
with safe shallow candidates before deep-copying the full result. A candidate envelope with more
than three public fields is rejected before `dict.copy`. Fusion reapplies global limits of 64
candidates and 8 MB to the merged result so one oversized input does not fail all fusion. Envelope
field names are checked as exact built-in bounded strings. Pydantic validation errors omit hostile
original input so error-string construction cannot invoke equality or representation hooks.

Known typed semantic-record `evidence_ids` exposes the shared Scene limit of 256 references in both
the prompt and nested post-validation. Omitted, `null`, and empty lists remain for compatibility;
oversized lists isolate the candidate during fusion, pipeline, accessibility/repair, and sidecar
current-payload revalidation. Fusion stops before the 257th unique reference while combining Scene
element/relation evidence and source-block IDs from the same `VisualEvidence`. All inputs sharing an
ID are judged atomically. On overflow, it discards cross-input enrichment rather than publishing a
partial union and retains the precedence winner's record. Vector-text combination also validates a
new `SceneElement` and omits the entire over-limit label/font attribution. Before scoring, the
pipeline revalidates internal fused Scene/evidence records and the evidence collection's exact
plain-list, 20,000-item, aggregate source-block occurrence/character, and full-evidence character
limits, preventing post-construction list mutation between publication receipt and sidecar. If this
backstop fails, only the fused candidate is isolated and original engine candidates remain.

## SVG inspection

The runtime's `render_valid` report is insufficient; a non-empty string SVG artifact is required.
Missing, empty, or whitespace-only SVG is converted to a render failure rather than skipping
post-inspection. A valid artifact must have one SVG root and dimensions/viewBox. Script-like
elements, event-handler attributes, external hrefs, and external CSS in style attributes or
`<style>` text are rejected. The `strict` profile also rejects `foreignObject` and forces
`htmlLabels: false`. Only fragment references of the form `href="#local-id"` are accepted.

## Chromium isolation and lifecycle

Every network route in the Playwright context is aborted. No remote font or icon is registered.
The worker reuses one browser, but resets the DOM and uses a deterministic ID seed for every
candidate. Worker stdout is read through a nonblocking JSONL buffer with a 64 MB response limit. A
partial response without a newline cannot exceed the Python deadline. On timeout, malformed or
oversized response, or shutdown, SIGTERM is sent to the recorded worker process group and SIGKILL
follows after the grace period. `bindFunctions` is never called.

`sandbox-experimental` is reserved in the configuration enum but has no separate OS-sandbox
implementation yet. The current runtime maintains the same network isolation under every profile.
