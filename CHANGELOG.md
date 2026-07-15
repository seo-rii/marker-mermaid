# Changelog

## Unreleased

### Added

- Deterministic composite-panel, fragment-merge, and full-page coverage proposal APIs.
- Page-aware `SourceFragment` and `DiscoveredSource` models for virtual diagram sources.
- Optional OpenCV geometry engine for contour, line, and arrowhead provenance.
- Geometry evidence enrichment before structured VLM extraction.
- Marker adapters for full-page, composite-panel, and adjacent/multi-page virtual sources.
- Deterministic fragment assembly with source/page-to-canvas affine provenance.
- `source-map.json` sidecars and multi-source Markdown rendering.
- Duck-typed PDF vector extraction with page-to-canvas mapping.
- Deterministic vector/geometry/OCR/VLM observation fusion.
- Collision-safe Flowchart/Generic Network typed node-ID harmonization against independently
  corroborated vector/geometry Scene nodes, with snapshotted spatial/text source evidence,
  owner-local contour authority, atomic reference remapping, mapped direction-conflict propagation,
  trusted canvas/block binding, component-level post-mutation revalidation, immutable sealed mapping
  claims, and hash-bound `node-id-map.json` + provenance audit sidecars.
- Conservative topology, arrow, relative-layout, and path quality metrics.
- Vector, detected-arrow, and color-cluster visual priors.
- Native State, Class, ER, Requirement, and Block typed serializers.
- Strict-safe C4, Deployment, Component, and Use-case portable fallbacks.
- Requested/emitted/runtime diagram type and fallback-chain metadata.
- Evidence-strict Pie, XY, Quadrant, Sankey, Radar, Treemap, and Venn serializers.
- Pie serialization, generated Scene, and semantic OCR now share a bounded `PiePlan` that freezes
  `pie_slice_N` identities, exact fixed-decimal values, terminal-visible labels, and record-local
  provenance. Native Mermaid 11.16 output is limited to 12 slices and requires zero-or-normal
  binary64 round trips, a finite positive left-to-right renderer total, at least 1% visibility for
  every positive slice, finite percentage-label centroids, and exact JavaScript `showData` text;
  zero slices remain legend-only. Native-incompatible valid inputs and native runtime rejection use the same
  candidate slot's disconnected `flowchart TB` fallback with up to 256 exact `label: value` cells,
  no invented edges, and a fresh full validation pass. Native Scenes expose normalized radial
  sector-centroid elements/legend text while fallback Scenes expose zero-geometry cells; terminal OCR distinguishes
  visible native title/legend/percentage text from fallback cells and excludes accessibility metadata.
  Pie now participates in the 80% generated-node provenance gate and combines exact document-wide
  numeric completeness with candidate-authorized slice-local OCR/vector binding. Punctuation-preserving
  full label/value records, independent title/accessibility evidence, and global source coverage prevent
  label/value swaps, suffix omissions, uncited slices, shared observations, or fabricated metadata from
  auto-publishing. Both terminals preflight 50,000 UTF-16 source units / 5,000 lines and disclose
  terminal-visible compatibility substitutions while preserving semantic source text in typed/review IR.
- XY Chart serialization, generated Scene, and semantic OCR now share a bounded `XYPlan` that freezes
  axis/series/point source records, exact fixed-decimal x/y values, deterministic Scene identities,
  terminal-visible text, and record-local provenance. Native Mermaid 11.16 output requires
  zero-or-normal binary64 round trips, positive normal finite axis spans, and a bounded simulation
  of the renderer's numeric x loop with exact point count/endpoints and strict progress. One-point
  lines, zero-height bars, overlapping bar or identical line series, palette overflow, non-uniform
  explicit x coordinates, point-drop grids, and stalled loops use the same candidate slot's
  disconnected `flowchart TB` fallback with up to 256 title/axis/category/exact-value cells and no
  invented edges. Native Scenes expose normalized axes, categorical anchors, hidden-text line/bar
  geometry, and marker-less adjacent line topology; fallback Scenes expose the emitted zero-geometry
  cells exactly, while terminal-aware OCR excludes hidden values and accessibility metadata.
  Native runtime rejection receives a fresh full fallback validation pass. Both terminals preflight
  50,000 UTF-16 source units / 5,000 lines, disclose terminal-visible compatibility substitutions,
  and retain semantic source text in typed/review IR. Publication now requires candidate-authorized,
  bbox-contained axis/series/explicit-point OCR or vector records plus global numeric completeness;
  category/value/x swaps, cross-record evidence reuse, invalid geometry, direct candidates without a
  typed plan, fabricated explicit metadata, and engine-emitted self-authorizing user edits remain
  review-only. Explicit-metadata spatial attribution skips unused scans and fails closed beyond
  100,000 evidence-to-record overlap comparisons per candidate.
- Quadrant serialization, generated Scene, and semantic OCR now share a bounded `QuadrantPlan` that
  freezes two axis records, four quadrant regions, up to 256 exact fixed-decimal points, deterministic
  Scene identities, terminal-visible text, and record-local provenance. Native Mermaid 11.16 output
  requires `[0,1]` zero-or-normal binary64 round trips plus finite, distinct, unclipped, non-overlapping
  point and text placement on the pinned 500×500 canvas; duplicate/near points, float collapse,
  subnormal coordinates, and canvas occlusion use the same candidate slot's disconnected
  `flowchart TB` fallback. The fallback preserves optional title, both axes, supplied quadrant labels,
  and every exact `label · x X, y Y` cell without inventing edges or geometry, and native runtime
  rejection receives a fresh full validation pass. Native Scenes expose four visible axis endpoints,
  normalized point circles, and four unlabeled-or-supplied region groups with no inferred relation or
  membership; fallback Scenes expose emitted zero-geometry cells. Terminal OCR distinguishes visible
  native labels from exact fallback cells and excludes native coordinates/accessibility metadata.
  A two-pass point budget preflight counts native and fallback UTF-16 source independently before allocating
  terminal projections, stopping early only when both outputs are impossible. Both terminals enforce 50,000
  UTF-16 source units / 5,000 lines and disclose visible compatibility substitutions. Native output also records
  Mermaid 11.16's non-finite point-paint compatibility warning without leaking it into the Flowchart fallback.
  Publication now combines global numeric completeness with candidate-authorized,
  record-local axis/point OCR or vector binding, independent source-quadrant evidence for supplied slot
  labels, and independent explicit-metadata attribution. Observation/record reuse, swaps, invalid
  geometry, direct candidates without a typed plan, engine-emitted self-authorizing user edits, and
  shared 100,000-comparison budget overflow remain review-only. Horizontal/bottom x-axis versus
  vertical/left y-axis geometry prevents whole-record swaps. Supplied slot attribution uses a conservative
  whole-crop midpoint and requires a spatially targeted initial user edit; off-center plots may therefore
  require review. Explicit title/description evidence currently proves content existence rather than an
  immutable semantic role, so best-effort output records a limitation warning and strict validation keeps
  the candidate in review. Default accessible descriptions enumerate up to five observed point labels
  without inventing a trend or quadrant membership. Publication-policy warnings are retained ahead of noisy
  engine diagnostics at the bounded warning sink, and a rejected strict semantic-repair proposal cannot
  downgrade the preceding validated candidate. The sealed Markdown snapshot now carries serializer stability,
  so experimental Quadrant output remains visibly marked even when its quality score is grade A.
- Radar serialization, generated Scene, and semantic OCR now share a bounded `RadarPlan` with
  reserved-safe cross-terminal IDs, fixed-decimal values, record-local provenance, and explicit
  native-versus-tabular terminal semantics. Native Radar is limited to 12 series and requires
  non-negative, zero-or-normal binary64 round-trip-safe values/bounds, a finite positive renderer
  span, and finite curve radii; unsupported data and native runtime rejection use the same candidate slot's
  exact-value `flowchart TB` fallback, capped at 256 points. Native Scenes expose normalized radial
  axes/data points, per-series curve envelopes, and closed marker-less curve relations, while fallback
  Scenes expose zero-geometry series groups and value cells without invented edges. Both paths preflight
  50,000 UTF-16 source units / 5,000 lines, disclose terminal-visible compatibility substitutions, and keep hidden options/accessibility
  metadata out of canvas OCR. Native generated-node provenance scores directly attributable axes and
  series rather than derived curve points. The Flowchart terminal now preserves the visible native title
  and emits series group labels only when the source requested a legend, with Scene/OCR projections following
  the same terminal plan. Its dimension/series-derived cells use record-scoped provenance rather than claiming
  exclusive evidence per cell. Publication requires every dimension label and angular slot plus every series
  label and ordered value row to bind to non-overlapping, candidate-authorized, bbox-contained OCR/vector
  records, followed by exact global numeric occurrence coverage. Cross-owner evidence/observation reuse,
  uncited same-bbox contradictions, dimension or series permutations, invalid geometry, direct candidates
  without a typed plan, and bounded reference/text/token/spatial work exhaustion remain review-only across
  native output, same-slot Flowchart fallback, and semantic repair. Visible title and non-derived explicit
  accessibility text also require independent candidate-authorized spatial OCR/vector evidence or an approved
  initial user edit; record-owned observations and engine-emitted edits cannot self-authorize metadata.
  Render validation now rejects non-finite SVG geometry attributes such as `NaN` or `Infinity` even when
  Mermaid reports parse/render success.
- Numeric multiset precision/recall scoring and no-evidence publication guard.
- Interactive source/render/provenance review workspace with external CSP-safe assets.
- Atomic Mermaid/Scene IR/render revisions, optimistic concurrency, and undo/redo.
- Alternative selection, approval/rejection audit history, and conservative Korean/English patches.
- Same-origin review API with CSRF, body limits, path confinement, and strict revalidation.
- Evidence-strict Journey, Kanban, GitGraph, Packet, Ishikawa, TreeView, and Event Modeling serializers.
- Native Wardley, Cynefin, and Railroad serializers plus explicit ZenUML, organization, and lineage fallbacks.
- Strict nested Event Modeling lane/frame/relation and ZenUML participant/message extraction contracts,
  with canonical object-only prompts, case-insensitive closed Event frame types, preserved legacy scalar
  ZenUML participants, extra metadata, and original IR. Shared frozen fallback plans now assign
  reserved-safe lane/frame/participant emitted identities plus Scene/provenance-only relation/message
  slots, resolve only explicit endpoints, and project the exact Flowchart/Sequence-visible compatibility
  glyphs through serialization, OCR, and generated Scenes. The serializer paths enforce validator-sized
  source budgets before returning code. The Scenes retain requested types while using the actual
  `LR`/end-arrow topology, zero geometry, and record-local element/relation provenance instead of copying
  raw bbox, direction, shape, style, bidirectional, or relation-ID metadata.
- Strict nested Organization hierarchy and Data Lineage dataset/process/relation extraction contracts,
  with canonical prompts, unadvertised legacy Organization `name` compatibility, and preserved original
  IR/unknown metadata. Shared frozen plans assign reserved-safe `treeview_node_*`,
  `data_lineage_dataset_*`, and `data_lineage_process_*` identities plus Scene/provenance-only ordered
  relation slots, reject normalization collisions and invalid Lineage endpoints, and expose the exact
  Flowchart/TreeView-visible compatibility labels to serialization, OCR, and generated Scenes. Organization
  uses the actual `LR` parent-to-child containment topology across native and nested fallback paths; Data
  Lineage preserves strict direction plus cylinder/rectangle node kinds and directed data-flow relations.
  Legacy partial inputs retain deterministic preorder Organization IDs and source-ID Lineage labels, and
  TreeView-sized Organization IDs remain accepted. Runtime diagram type now selects marker-less native
  TreeView connectors versus rectangle/end-arrow Flowchart fallback Scenes, and an accepted runtime
  fallback replaces stale native serialization warnings with terminal Flowchart warnings. Data Lineage edge-label
  `()[]{}`/`@` grammar conflicts and accessibility-only angle-bracket substitutions use disclosed visible
  compatibility glyphs; source-only separation keeps fullwidth `＠` safe after NFKC normalization.
  Both generated Scenes use zero geometry, no inferred groups, and record-local evidence instead of raw
  bbox/style metadata; Organization ignores raw direction while Lineage uses only its validated direction.
  Serializer output is bounded to 500 records and 50,000 characters / 5,000 lines.
- Strict nested Railroad rule/expression extraction with an exact recursive discriminator for terminal,
  nonterminal, special, sequence, choice, optional, one-or-more, and zero-or-more AST nodes. A shared frozen
  preorder plan now binds the native serializer, generated Scene, OCR projection, and provenance to the same
  `railroad_rule_*`, `railroad_expression_N`, and containment-only `railroad_relation_N` identities. The Scene
  models Mermaid 11.16's marker-less `LR` structure with zero geometry, exact `native_name =` and leaf-visible
  text, no invented nonterminal-reference connector, and record-local evidence. Rule/reference/depth and separate
  500-rule/500-expression limits plus 50,000-character/5,000-line source preflight fail closed. A canonical
  visible compatibility layer maps ASCII angle brackets to `〈`/`〉`, every ASCII `#` to `＃`, entity-like `&`
  prefixes to `＆`, and NFKC quote/backslash hazards to `″`/`∖`, with a compatibility warning; raw semantics
  remain in typed IR. This covers bare `#word;`/`#35;` forms changed by Mermaid's global `encodeEntities`.
  Zero-width separators exist only in emitted source and also split Mermaid preprocessor-active
  `style`/`classDef` substrings. Raw and NFKC-normalized emitted forms both pass the strict source scan; only raw
  source enters the production CandidateValidator parse/render hard gate. Rule names that are
  scanner/preprocessor source-active or conservatively native-grammar-reserved (the case-folded
  expression-word namespace,
  `railroad-beta`, and the case-folded lowercase `title*` prefix) are mapped to collision-safe
  `rrmapped_N[_suffix]` native names with a visible-change warning. Logical IDs remain source-based and normalized
  source names remain in nonterminal labels. Scene/OCR share the exact compatibility text, and direct Scene
  projection accepts only null or string-list `evidence_ids`, failing closed on malformed evidence. Runtime
  integration covers compatibility-normalized injection text, bare hash forms, preprocessor substrings, reserved
  rule names, accessibility, the raw parse/render gate, an NFKC parse/render grammar-injection safety probe, and
  process cleanup.
- Strict nested Wardley component/link and Cynefin domain/item/transition extraction contracts with shared
  bounded serializer/Scene/OCR plans. Wardley maps horizontal/vertical IR coordinates to the native
  `[visibility, evolution]` order as `[y, x]`, canonicalizes the Scene to the rendered token values and
  normalized `(x, 1-y)` screen position, and models `->` as the marker-less link Mermaid 11.16 actually
  renders. Cynefin attribution includes the runtime's fixed domain/practice/response/disorder template and
  its first-three-plus-`+N more` confusion-item projection; the fixed content remains unprovenanced and makes
  native Cynefin review-only instead of allowing a false automatic publication. Both native grammars enforce
  validator-sized source budgets and disclose visible compatibility glyphs when Mermaid 11.16 cannot preserve
  entity-like literals in SVG text.
- Wardley native runtime rejection now retries one loss-disclosed Flowchart in the same candidate slot. The
  shared plan assigns ordered fallback component/relation identities and exact visible compatibility labels;
  the fallback preserves only explicit component/link topology with marker-less connectors. Coordinates,
  visibility/evolution axes, anchor notation, and the visible native title are not fabricated: warnings disclose
  those losses, the generated fallback Scene uses zero geometry, and OCR/layout scoring follows the terminal
  Flowchart. The fallback must independently pass strict source scan, parse/render, and terminal-type checks.
- Cynefin native runtime rejection now retries one loss-disclosed `flowchart LR` in the same candidate slot.
  The native-success path retains Mermaid 11.16's five-domain practice/response/disorder template, confusion
  `+N more` projection, and native-only review hold. The fallback instead creates one subgraph for each supplied
  domain (including confusion only when explicit), preserves every explicit item without truncation, and emits
  only explicit directed transitions between domain subgraphs; it invents neither fixed-template nodes nor
  membership connectors. Its terminal Scene/OCR projection uses same-identity conceptual domain elements/groups,
  counts each domain label once, retains exact item/transition visibility and record provenance, and uses zero
  geometry with `LR` direction while warning that quadrant/layout semantics were lost. A fallback satisfying the
  generated-node attribution threshold may publish through normal gates after independent
  security/parse/render/type validation, while candidate
  budgets, requested/emitted/runtime metadata, accessibility, and the native review hold remain intact.
- Core generated Scene/OCR projection now follows the exact serializer-visible defaults for four previously
  divergent cases: Block reuses collision-safe emitted IDs and `[unreadable]` labels, ordinary State nodes use
  only their rendered label/ID while choice/fork/join pseudo-states retain topology without inventing canvas text,
  Sequence supplies `[unreadable]` for unlabeled messages, and Gantt uses the per-section `Task N` default instead
  of hidden task text or IDs. State serialization and Scene attribution now share normalized node IDs, exact
  transition endpoints, and boundary-transition validation; Gantt allocates collision-free Scene identities so
  duplicate source section/task IDs cannot erase rendered records. Invalid Block endpoints and malformed State
  records or transitions fail closed instead of producing a partial semantic Scene. Boundary markers stay out of
  structural Scene relations while their rendered transition labels remain in the semantic OCR projection.
- Packet semantic OCR projection is now terminal-aware: a validated native Packet includes its normalized canvas
  title, while a same-slot Flowchart fallback excludes that native-only text. Native serialization and scoring
  share entity-compatible title normalization; invisible source-security separators are omitted from OCR tokens,
  and field labels plus field-local numeric binding remain unchanged.
- Sankey serialization, generated Scene attribution, and semantic OCR projection now share one bounded terminal
  plan. Native Mermaid 11.16 output keeps source node identities, marker-less data-flow topology, fixed `LR`
  layout, and the renderer-visible per-node maximum of incoming/outgoing totals rounded to two decimals; individual
  weights, title, description, and unsupported style metadata are not invented as canvas text. Portable Flowchart
  output instead uses collision-safe emitted IDs, the requested normalized direction, exact edge-weight labels,
  and end arrows. Decimal values that JavaScript would underflow, overflow, or visibly round are kept in the exact
  fallback; flow counts and Scene IDs are bounded, while malformed record provenance is quarantined instead of
  manufacturing attribution or invalidating otherwise serializable evidence. Portable projection fails before
  serialization above Mermaid's 500-edge Flowchart runtime limit, without restricting a valid native Sankey.
  A native runtime rejection retries that Flowchart once in the same candidate slot while retaining the Sankey
  semantic type, complete fallback metadata, record provenance, and numeric publication gate.
- Sankey numeric publication now binds every planned flow's exact `value_text` to candidate-authorized OCR/vector
  observations fully contained by a positive, in-image, mutually non-overlapping flow bbox, then also requires
  global numeric occurrence
  exactness. Cross-flow evidence or normalized-observation reuse, same-bbox ambiguity, swapped weights, invalid
  geometry, and bounded association-budget exhaustion force review. Native Sankey, its same-slot Flowchart
  fallback, and semantic repair all rerun this gate; direct or otherwise untyped Sankey remains review-only.
- Sankey accessibility attribution is now terminal-specific. Native Sankey emits no title or description and is
  therefore exempt. Its same-slot Flowchart fallback emits the resolved accessibility title and description as SVG
  metadata; each non-derived resolved output role requires independent, candidate-authorized non-data-record spatial
  OCR/vector evidence or an approved exact `user_edit` from the initial reconstruction input. When `acc_title` or
  `acc_description` shadows its legacy counterpart, the hidden legacy text is exempt. Deterministic defaults and the
  experimental notice remain exempt. Node/flow-record-owned, reused, ambiguous, overlapping, or over-budget
  evidence, missing or invalid data-record geometry, and engine-emitted `user_edit` self-authorization force
  review, and semantic repair reruns the same gate. Numeric tokens from the selected OCR/vector metadata proof are
  removed only from the flow-weight reference multiset so valid metadata digits do not masquerade as extra flows.
- Treemap serialization, generated Scene attribution, and semantic OCR projection now share one bounded DFS
  preorder plan. It freezes source or collision-safe `treemap_node_N[_suffix]` Scene identities, Flowchart
  `N1..Nn` identities, parent/child slots, exact value tokens, and record-local provenance. Source image/bbox
  stays in typed IR and review provenance while both generated terminal Scenes use zero geometry so source
  coordinates cannot manufacture a perfect layout score.
  Native Mermaid 11.16 output models sections and leaves as marker-less nested regions with unknown reading
  direction, reproduces d3-hierarchy's reverse-order binary64 sums and d3 `format(",")` canvas totals, and
  distinguishes the visible native title from `accTitle`/`accDescr` SVG metadata. Internal-node values, unsafe
  binary64/renderer totals, and native runtime rejection use the same-slot `flowchart TB` fallback with preorder
  rectangle nodes, end arrows, and exact explicit ` (value: x)` suffixes. Portable projection stops above the
  500-relation worker limit without rejecting an otherwise valid native hierarchy. Malformed evidence is
  quarantined atomically per record, source-only security separators and disclosed terminal-visible
  quote/angle/backslash/hash compatibility text are shared with Scene/OCR, and documentation notes that the
  native renderer can hide text in very small cells with `display:none`. Unicode whitespace is frozen to one
  ASCII space, accessibility metadata substitutions are disclosed too, and both terminals are preflighted at
  50,000 source characters and 5,000 lines before runtime work.
- Venn serialization, generated Scene attribution, and semantic OCR projection now share one bounded terminal
  plan. It freezes portable set identities, collision-safe explicit or `intersection_N[_suffix]` area identities,
  canonical membership relations, fixed-decimal non-exponent value tokens, and record-local provenance. Native
  Mermaid 11.16 output is limited to positive normal binary64-safe areas, Python integer inputs within the
  JavaScript safe-integer range, a `200:1`
  maximum set-to-smallest-area visibility ratio, no exact-containment cases, and complete explicit pairwise
  intersections for every higher-order union; zero, unsafe, missing, or runtime-rejected native data uses the
  exact-value Flowchart without synthesizing areas or implicit pairs. Native Scene/OCR models marker-less logical
  membership, unknown direction, visible title and area labels but no canvas value text, while the portable
  terminal models circle/round nodes, exact value suffixes, labeled end arrows, and `LR`. Both terminal Scenes use
  zero geometry and preserve element/relation provenance with record-local malformed-evidence quarantine.
  Runtime fallback is retried once in the same candidate slot, applies the 500-edge worker cap only to Flowchart,
  and re-runs the full validation gate. Native and fallback source share 50,000-character/5,000-line preflight,
  terminal-visible compatibility warnings, and the independent numeric publication gate. Native Venn's
  `accTitle`/`accDescr` limitation remains explicit; the Flowchart stores resolved accessibility text as SVG
  metadata rather than OCR content.
- Bounded pre-validation source repair with audit events, diagnostics, idempotence, and AST adapter seam.
- Page-level missed-diagram proposals with occupied-region exclusion and virtual source crops.
- Profile-gated flowchart fill, border, and link style recovery with strict CSS allowlists.
- Enabled-type typed IR root contracts shared by Structured VLM prompts and response validation.
- Type-aware grayscale, adaptive-threshold, contour, and source-resolution tile visual priors.
- Candidate Scene adapters for planning, hierarchy, event, Wardley, Cynefin, lineage, Venn, and ZenUML structures.
- Requested-type accessibility enrichment with emitted-grammar limitations and validated direct-code augmentation.
- Evidence-backed typed Flowchart label repair with structured IR/code proposals.
- Validated Review Workspace edge reconnection and node deletion with synchronized IR/code audit history.
- Vector-backed flowchart edge color recovery with allowlisted, exact-order `linkStyle` mapping.
- Digest-verified, content-addressed Review provenance revisions with legacy timeline migration.
- Source-anchored Review node addition with server-created `user_edit` evidence.
- Separate source-observation and generated-candidate Scene IR sidecar artifacts.
- Hash-bound MMX-001 corpus manifests, fixed release gates, auditable reports, and `evaluate` CLI.
- Revisioned advisory Review layout hints with pointer and keyboard node movement.
- Evidence-backed PDF vector bold-label recovery with source-to-candidate attribution.
- Off-by-default, bounds-normalized source/render difference blend in the Review Workspace.
- Optimistically locked active-timeline revision restore with complete artifact rollback and audit.
- Advisory Review edge endpoint drag with screen-space snapping and validated reconnect reuse.
- Validated Review node grouping with canonical IDs and exact Scene-to-subgraph membership checks.
- Provenance-backed Review edge creation and exact-mapped deletion with global topology preflight.
- Exact-mapped Review group deletion that preserves member nodes, edges, provenance, and layout.
- Trusted-geometry-backed typed Flowchart direction reversal and unlabeled missing-edge repair with shared source-block attribution and collision revocation.
- Dual-gated typed Flowchart conditional-edge label-only repair from trusted OCR/vector text and one conflict-free built-in Geometry connector, without topology or direction inference.
- Labeled Flowchart serialization that retains dashed and bidirectional connector operators.
- Shared deterministic Flowchart group emission plans with validated flat subgraphs and generated SceneGroup reconstruction.
- Trusted vector-container Flowchart group style recovery with exact membership, bbox, collision, and audit gates.
- Trusted vector-origin gates and explicit attribution for Flowchart node and edge styles.
- Publication-aware candidate selection that retains aggregate ordering within the same eligibility class.
- Bounded online path-consistency search with explicit state/stack exhaustion reporting.
- Occurrence-preserving, resource-bounded OCR recall over generated node, relation, group, and Gantt labels instead of Mermaid metadata and identifiers.
- Serializer-aware OCR text projection for Class members/cardinalities, ER attributes, and multi-event Timeline records.
- Source-sized Review provenance overlays with Scene-coordinate view boxes and stale image-load isolation.
- Emitted-serializer OCR projection for C4 architecture fallbacks and native Requirement diagrams.
- Emitted-visible OCR projection for Event Modeling, Wardley, Cynefin, and ZenUML fallbacks/native output.
- Serializer-aligned Event Modeling lane Scenes plus grammar-specific, fallback-safe special-label and accessibility neutralization.
- Entity-shaped Flowchart source-text preservation, pre-normalization line/control rejection, and Event Modeling compatibility-glyph disclosure.
- Ishikawa raw-line labels that begin with either reserved header token remain visible instead of being consumed by the lexer.
- Runtime render success without a non-empty SVG artifact now fails the post-render security gate.
- Organization TreeView runtime rejection now retries the declared nested Flowchart fallback in the same candidate slot.
- Closed Review node relabeling from uniquely linked OCR/vector provenance with synchronized overlay selection, transactional revision history, and unchanged evidence snapshots.
- Bounded Review audit history view with the newest 100 operation/target/source/timestamp/reason entries, expandable text-only deltas, and malformed-entry reporting while preserving canonical `review-history.json`.
- Architecture, C4, Deployment, and Component candidates now retry one declared nested Flowchart fallback in the same candidate slot when `architecture-beta` runtime validation fails, preserving candidate budgets and requested/emitted/runtime fallback metadata.
- SHA-256 validation receipts bind each automatically published Mermaid source to its inspected SVG and optional PNG, while a second authorization receipt binds policy/security/status/quality decisions; sealed immutable Markdown/Marker snapshots and atomic sidecar snapshots prevent check-then-reread races.
- Validator-issued artifact certificates and canonical decimal quality digests prevent unchecked model flags and cross-runtime float formatting from manufacturing publication receipts.
- Structured VLM requests now reserve Marker 1.10.2 response-schema text, validate bounded RGB view
  sets through bounded independent provider snapshots, preflight aggregate and nested evidence/OCR
  input, freeze trusted provenance ID sets, preserve structural evidence under OCR saturation, and
  persist source-level prompt budget notices even when no candidate is produced.
- Reconstruction source collections and source mappings are now normalized into bounded plain
  snapshots before any engine sees them; every engine receives an independent full-coordinate source
  context, invalid collections are isolated as a whole, aggregate provenance growth is capped, and
  sidecar source maps are serialized only from a hook-free canonical JSON snapshot with mutation
  checks.
- Typed IR now uses detached exact-built-in snapshots with a 1,000,000-byte UTF-8 text budget, a
  4,000,000-byte compact JSON budget, and an 8,000,000-byte observation budget; pipeline, fusion,
  accessibility, repair, canonical-key, and sidecar paths revalidate live IR before copying or
  serializing it. The three-field candidate envelope is bounded before copying, and fusion applies
  the same item/JSON limits globally across all input observations. Exact field-name checks and
  input-hidden validation errors prevent hostile dictionary-key or error-formatting hooks.
- Stable State, Class, and ER typed IR now has strict nested extraction and post-validation
  contracts for serializer-visible records and closed token sets, with records advertised only when
  those diagram types are enabled and without changing the generic provider response envelope.
- Native Requirement and Block typed IR now has strict nested prompt/post-validation contracts for
  serializer-visible records and case-insensitive closed tokens, while preserving optional partial
  fields, evidence, extra metadata, and the original generic `ir` dictionary; Block column layout
  semantics remain serializer-owned.
- C4 fallback typed IR now has a strict nested prompt/post-validation contract for level, element,
  boundary, and relation records, with case-insensitive canonical element kinds, legacy `type`
  preservation, exact Architecture port sides, and serializer-owned reference/collision semantics.
  Native-only description, technology, relation labels, and boundary notation remain review metadata;
  automatic publication still uses the shared bounded Architecture-to-Flowchart fallback path.
- Deployment and Component fallback typed IR now has strict nested contracts for primary and flattened
  secondary service records, groups, and canonical links/dependencies. Legacy `edges` remains a
  validated, non-advertised compatibility alias; open icon strings, exact ports/booleans, extra
  notation metadata, and the original dictionary preserve the existing Architecture-to-Flowchart
  fallback behavior while semantic references remain serializer-owned.
- Use-case fallback typed IR now has a strict nested actor/use-case/relation contract with open
  type-over-label relation text, optional evidence, and original extra metadata preservation, while
  non-empty, collision, endpoint, and resource-cap semantics remain owned by the shared fallback plan.
- Pie, XY, and Quadrant typed IR now have strict nested prompt/post-validation contracts for finite
  JSON chart numbers, axes, series, slices, points, and canonical quadrant labels. Completeness and
  representability checks remain serializer-owned; all three use shared native/Flowchart terminal plans,
  generated Scene/OCR projections, and record provenance.
  Publication still requires independent source OCR/vector numeric evidence. XY values must remain inside the
  declared y-axis, and XY axes/series/points require record-local source association;
  ambiguous Quadrant label aliases fail closed, and grammar-aware numeric projection excludes comments,
  supported native/colon titles, supported colon/block accessibility metadata, and Quadrant slot indices
  while retaining visible labels/data and Sankey metadata-like CSV labels. Spatially distinct source
  occurrences remain multiplicity-preserving without double-counting the same OCR/vector observation.
  Direct candidates use their validated emitted/runtime type for publication holds, while typed
  fallbacks retain their semantic type, including Venn.
- Sankey, Radar, Treemap, and Venn typed IR now have strict nested prompt/post-validation contracts
  for finite weighted flows, ordered series, recursive hierarchy nodes, explicit set membership,
  record geometry, and evidence. Canonical prompts do not advertise legacy `links`, `axes`, or
  label aliases; serializer-owned completeness, reference, range, hierarchy-budget, and native versus
  Flowchart fallback selection remain authoritative. Radar ticks are capped at 100 before rendering to
  bound the experimental runtime loop. Venn now reserves portable set IDs before assigning collision-safe
  explicit or deterministic intersection Scene IDs, preventing collision-driven self-loops without inventing
  sizes. Treemap moves missing, duplicate, or malformed attribution IDs into reserved-safe preorder slots.
  Both plans keep source bbox in typed/review provenance while their terminal generated Scenes use zero geometry.
- Journey, Kanban, and GitGraph typed IR now have strict nested prompt/post-validation contracts for
  scored tasks, assigned cards, and ordered branch operations while preserving compatibility aliases
  and extra evidence metadata without rewriting the original IR. Shared bounded planning records align
  Kanban/GitGraph native output, generated Scene attribution, and same-slot Flowchart runtime fallback;
  Journey scores now require independent source numeric evidence. GitGraph uses grammar-specific safe
  quoting verified against Mermaid 11.16 SVG text, preserving quote, backslash, and ordinary punctuation
  while disclosing the visible compatibility glyphs needed for angle brackets. Journey Timeline items,
  native Kanban labels, and portable planning fallbacks likewise disclose grammar-specific compatibility
  glyphs instead of silently losing text. Canonical/compatibility alias conflicts and post-encoding
  GitGraph identity collisions now fail closed, Kanban shares a reserved-word-safe ID namespace across
  native/Scene/fallback output, and every planning result enforces the validator's 50,000-character and
  5,000-line source budgets before publication. GitGraph also rejects known fields that are irrelevant to
  the declared commit, branch, or merge operation instead of silently discarding them.
- Packet, Ishikawa, and TreeView typed IR now have strict nested prompt/post-validation contracts for
  explicit bit ranges, an effect leaf with recursive causes, and rooted hierarchy nodes. Native output,
  portable runtime fallback, and generated Scene attribution share identity and parent plans; the
  fallback and Scene use their reserved-safe emitted IDs while native grammars consume the same validated
  labels, ranges, and depth. The plans reject alias conflicts, normalized collisions, cycles, reused node
  objects, and bounded source overflows, and preserve exact record bbox/evidence. Packet fallback no longer
  invents arrows between fields, and Packet now participates in both provenance and numeric publication
  gates. Every special result is preflighted against the validator's 50,000-character and 5,000-line source
  budgets.
  Entity-like literals use disclosed visible compatibility glyphs where Mermaid 11.16 cannot preserve the
  original SVG text. Organization generated Scenes and TreeView/Flowchart output now consume the dedicated
  bounded organization identity/parent plan.
- Packet numeric publication now uses one field-local association across native output, same-slot
  Flowchart fallback, and semantic repair. Only candidate-authorized, field-cited OCR/vector observations
  with positive image-bounded bboxes fully contained by the field can bind its label and bit range;
  source-wide OCR text cannot provide that authority. Exact bindings score `1.0`, bound wrong or extra
  numbers score `0.0` and require review, while overlapping fields, broad/shared/same-position ambiguous
  observations, invalid geometry or authority, and exhausted budgets make the metric unavailable. Exact
  normalized text+bbox OCR/vector duplicates count once, spatially distinct repeats remain, and single-bit
  fields require one endpoint occurrence. Pie uses its own slice-local binding; all remaining numeric types
  retain global occurrence-multiset scoring.
- Serializer-visible Scene text now includes Architecture group labels, excludes hidden
  Deployment/Component relation labels, and mirrors Use-case Flowchart relation type-over-label
  precedence; hidden role/shape/style/semantic metadata cannot enter the projected Scene, and
  label-less Architecture groups use one portable label across native and runtime fallback output.
- Native Architecture, its portable runtime fallback, and generated Scene now share one bounded
  service/group/edge identity plan with collision-free service suffixes and fail-closed group or
  endpoint ambiguity.
- Use-case serialization and Scene projection now share a bounded, collision-free Actor/UseCase
  namespace, including second-order prefix collisions, deterministic relation IDs, and exact
  Flowchart direction and arrow behavior.
- Use-case Flowchart serialization and generated Scene now share one strict relation/endpoint
  projection, suppress unsupported groups, and keep round use-case nodes distinct from actor stadium
  proxies.
- Closed Review edge-label add, replace, and remove operations keyed by stable relation ID, with
  validated canonical Scene-to-Mermaid label mapping, compatibility-neutralized quoted output,
  unchanged provenance, and transactional validation, revision, audit, and undo/redo gates.

### Changed

- Runtime evidence retention now uses one shared hook-free aggregate snapshot contract. Across a
  retained collection, at most 20,000 logical `VisualEvidence.source_block_ids` occurrences and
  8,000,000 Python characters of source-block IDs are allowed, in addition to the existing
  8,000,000-character full-evidence budget. Duplicate references consume the budget; exact limits
  pass and `+1` rejects the affected collection or reconstruction-global new-ID batch atomically.
  Initial/custom-engine input, fusion ingress/output, final result and publication/Markdown
  snapshots, sidecar preflight, and document-output preflight all use detached canonical records
  before live `model_dump`, deep copy, JSON encoding, directory creation, or image writes. The
  snapshot bounds mutable enum-like fields before UTF-8 encoding, fusion checks prior-evidence item
  count before tuple materialization, and document output reuses its preflight evidence snapshot for
  sidecar emission. The optimized vector preflight shares the same constants and remains an earlier
  allocation guard.
  Marker OCR source-crop/token production now admits each record against the same cumulative budget;
  overflow atomically removes OCR evidence/text context, records a bounded source diagnostic, and lets
  reconstruction continue. Review root/revision reads, trusted replacement, digest/commit sinks, and
  structured `user_edit` additions normalize detached evidence through the same contract before copying
  or writing. The standalone Structured VLM adapter now snapshots its complete prior-evidence collection
  through that contract before view, prompt-selection, or provider work, so prompt item limits cannot hide
  an over-budget tail and direct callers receive the same source-block occurrence/character protection as
  the pipeline. Hash-bound evaluation prediction JSON now applies the same detached raw-record admission
  before `VisualEvidence` model construction. Evaluation preserves the public 100,000-record and 64 MiB
  artifact contract while enforcing the shared 20,000 source-block occurrence and 8,000,000 Python-character
  source-block limits; aggregate-invalid artifacts are rejected before the overflow record is modeled and
  cannot reach report writing. Prediction 0.1's previously ignored unknown evidence fields remain ignored,
  while the retained registry contains only canonical public fields.
  These internal guards add no public configuration or schema/manifest version change.
- Generated-node provenance scoring and the release evaluator now share a collision-free attribution
  policy. Only `ocr_token`, `vector_text`, `contour`, `vlm_observation`, and `user_edit` evidence can
  support a node; `source_crop`, `line_segment`, and `arrowhead` cannot. If multiple generated nodes
  claim the same eligible evidence ID, that ID is conservatively revoked from every claimant instead
  of inflating coverage. Relation/group references remain outside node-collision accounting, and the
  release evaluator applies the rule independently within each corpus case.
- PDF vector extraction now applies reconstruction-global streaming budgets before parsing,
  mapping, or deduplication: 256 sources, 2,048 primitive/command records, 5,000 text records,
  and 8,000,000 text characters by default. Malformed, out-of-crop, duplicate, and empty nested
  drawing records consume work; exhausted dimensions remain closed across later sources. Polygon
  and polyline point collections are bounded at 256/512, aggregate retained geometry at 100,000
  points, and vector metadata tokens at 256 characters; oversized geometry is omitted whole.
  Exact duplicate hashing plus 250,000 approximate-dedup comparisons and 1,000,000 comparisons each
  for text ownership and connector endpoints bound the remaining geometry matching work.
  Custom extractors, reported work metadata, direct vector observations, and warning collections
  are defensively rebound at the engine/Scene boundary with at most one lookahead per iterable.
  Direct/dict/words duck-typed span strings are read once into a plain snapshot and charged before
  parsing, and huge integer coordinates/IDs fail closed before float or decimal conversion. Built-in
  reconstruction now builds one observe-local bounded index of exact-dict placement references and
  resolves every source with O(1) page/block/page+block dictionary lookups. Transform parsing is
  deferred until a unique placement is selected; nested providers then reuse that resolved mapping.
  Exactly 256 placements and 256 block IDs per placement are accepted, while the +1 placement
  invalidates the whole index and the +1 block ID atomically withholds that placement's block keys.
  Because transform validity does not filter the index, malformed placements still contribute to
  ambiguity instead of creating false uniqueness; an invalid selected affine falls back to bbox.
  Exact source page IDs reject block-matched and sole mismatched placements, while present-but-invalid
  page identities reject mapping altogether. The shared final vector boundary also preflights the
  canonical source-block provenance copied across every prospective shape, text, and open-line evidence
  record, with reconstruction-global limits of 20,000 logical references and 8,000,000 characters.
  Either aggregate overflow atomically returns an unknown observation with no Scene or evidence, so no
  retained prefix can gain publication authority. Warning-only empty observations are retained as bounded
  generation diagnostics in the result and sidecar manifest. These internal limits cover built-in, direct,
  and custom extraction without adding a public configuration or API surface.
- Fusion now bounds Scene element/relation evidence and VisualEvidence source-block unions before
  assignment. Source-block unions are decided atomically across every matching input, and an
  overflowing cluster keeps its deterministic precedence winner without partial provenance
  truncation. Vector text overflow keeps a contour-only node and reusable span evidence. Rebuilt
  records plus a pipeline fused-observation backstop enforce the exact-list/20,000-item collection
  contract and keep scoring, publication, and atomic sidecars on the same Scene contract.
- Known typed semantic records now share the Scene model's 256-reference `evidence_ids` cap in
  Structured VLM prompts and local nested schemas. Exact-boundary provenance remains publishable,
  while oversized or post-construction-mutated records are isolated before fusion, pipeline
  serialization, or atomic sidecar publication without dropping valid sibling candidates.
- Automatic C4 publication and evaluation Scenes now use the shared bounded
  Architecture-to-Flowchart fallback identity, group, and topology plan instead of diagnostic
  native C4 structure, aligning emitted labels, memberships, and endpoints while preserving
  valid bounded provenance and existing fallback-loss warnings; malformed provenance is omitted
  from attribution without breaking the previously valid Mermaid publication path.
- Candidate budgets are distributed round-robin across successful engines.
- Fused direct Mermaid candidates retain only their selected owner's closed publication-evidence
  authority instead of inheriting unrelated fusion input authority.
- Internal fusion handling is identified by pipeline state rather than an engine-controlled display
  name, so a custom engine cannot claim fused provenance privileges by reusing the built-in name.
- Visual priors are refreshed as earlier engines add structural evidence.
- Unlabeled geometry-only reconstructions are retained for review but cannot auto-publish.
- Marker OCR provenance now uses the exact, unexpanded block crop transform.
- Output saving now preflights source/image/artifact collisions and strict metadata JSON.
- Publication now requires an independent semantic threshold and generated-node attribution gate.
- Numeric diagrams with missing or sub-threshold source agreement are held for review.
- Review static serving rejects symlinks and DNS-rebinding Host values; render artifacts are bounded.
- Undo/redo transactionally removes optional artifacts absent from the target revision.
- Engine observations, typed IR, and per-engine serialization now have explicit resource budgets.
- Page proposals retain bounded crops instead of one full-page copy per proposal.
- Unanchored page proposals now flow through PageGroup metadata into sidecar output.
- Declared portable fallbacks are revalidated after supported native runtime failures.
- Marker now supplies real PyMuPDF page providers to vector extraction when available.
- Review edits validate full Scene IR integrity and invalidate stale automated quality metadata.
- Failed bundles without `final.mmd` can be repaired from retained alternatives.
- Natural-language review patches retain their structured operation, target, and delta history.
- Chromium worker responses use a nonblocking bounded protocol with partial-line deadlines.
- Review HTTP processing has a fixed in-flight worker budget and explicit busy responses.
- The Marker rendered-preview option now emits validated PNG previews when requested.
- Generated and Review SVG/PNG artifacts now share byte, format, dimension, and strict SVG security gates; detailed v0.5 Review loads verify immutable generation receipts against their root or `r000000` baseline and current revision hashes independently.
- Sidecar and Review publication keep staging, replacement, rollback, and cleanup anchored to no-follow directory descriptors; sidecar disclosure additionally uses Linux/macOS kernel no-replace rename primitives so a racing destination is never overwritten.
- Markdown publication sizes its backtick fence above every run in the validated Mermaid source, keeping multiline label content inside the authenticated code block.
- SVG CSS inspection covers `<style>` text and edge styles require fully mapped Mermaid ordering.
- Review approval revalidates the current digest and persists a fresh validated render; code-changing edits that return only a boolean discard stale SVG/PNG and cannot be approved without structured artifacts.
- Review HTTP sockets have header deadlines, explicit wildcard Host allowlists, descriptor-based static serving, and bounded lightweight listing.
- Scene/evidence/VLM inputs reject oversized identifiers, collections, paths, text, non-finite coordinates, and non-finite JSON.
- Hostile or decompression-bomb Marker previews are omitted without failing document conversion.
- Numeric consistency excludes Mermaid comments plus only the title/accessibility directives supported by
  the detected grammar, while preserving visible chart labels, data values, and Sankey metadata-like CSV rows.
- Initial and repaired candidates share OCR/vector, provenance, structural, numeric, and publication-gate evaluation.
- Structured review operations reject stale revisions before interpretation and require exact Scene-to-Mermaid mappings.
- Review undo/redo now restores provenance presence and content in the same boundary as code, IR, and renders.
- Review editors preserve local drafts across validation errors and conflict refreshes, lock summary-only or stale states until guarded detail reload succeeds, reject stale responses, and confirm before dirty edits are discarded.
- Security scanning now treats unquoted semicolons as Mermaid statement boundaries, closing same-line `click` and strict-profile style bypasses without rejecting quoted labels or comments.
- Automatic publication now prefers validator-sealed candidates within the same semantic eligibility class and downgrades missing/mismatched runtime type certificates to review instead of returning contradictory publish metadata.
- Malformed Unicode scalar values in engine IR/code are isolated per candidate, while diagnostic warnings and failure messages are converted to bounded sink-safe UTF-8 text.
- Marker VLM publication provenance is limited to collision-free evidence actually selected into that
  request; prompt-omitted and same-response evidence remain reviewable but cannot authenticate original,
  fused, or repaired candidates.
- Structured VLM request notices now cross-check configured caps and omission counts, survive provider or
  response-validation failure, and are canonically revalidated by Marker metadata and atomic sidecar sinks.
- Marker 1.10.2 stock Ollama now receives a bounded, local-reference-inlined response schema instead of
  unresolved `$defs`, while all returned payloads still pass the common canonical observation validator.

## 0.1.0

- Initial MMX-001 Phase 1 engineering baseline.
