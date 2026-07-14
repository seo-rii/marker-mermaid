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
- Strict nested Wardley component/link and Cynefin domain/item/transition extraction contracts with shared
  bounded serializer/Scene/OCR plans. Wardley maps horizontal/vertical IR coordinates to the native
  `[visibility, evolution]` order as `[y, x]`, canonicalizes the Scene to the rendered token values and
  normalized `(x, 1-y)` screen position, and models `->` as the marker-less link Mermaid 11.16 actually
  renders. Cynefin attribution includes the runtime's fixed domain/practice/response/disorder template and
  its first-three-plus-`+N more` confusion-item projection; the fixed content remains unprovenanced and makes
  native Cynefin review-only instead of allowing a false automatic publication. Both native grammars enforce
  validator-sized source budgets and disclose visible compatibility glyphs when Mermaid 11.16 cannot preserve
  entity-like literals in SVG text.
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
  JSON chart numbers, axes, series, slices, points, and canonical quadrant labels. Their native-only
  completeness and representability checks remain serializer-owned; record evidence stays in typed
  sidecars because these chart types have no generated Scene adapter, and publication still requires
  independent source OCR/vector numeric evidence. XY values must remain inside the declared y-axis,
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
  Flowchart fallback semantics remain unchanged. Radar ticks are capped at 100 before rendering to
  bound the experimental runtime loop. Venn fallback intersection nodes now receive IDs
  distinct from normalized set IDs, preventing collision-driven self-loops without inventing sizes;
  Treemap/Venn Scene attribution fails closed on duplicate node IDs instead of under-counting them.
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
