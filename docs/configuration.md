# Configuration Reference

Marker JSON uses the `MermaidDiagramProcessor_` prefix. Python `MermaidConfig` accepts the
unprefixed names directly. Prefixed settings override concise settings.

## Mode Defaults

| Mode | Type candidates | Mermaid candidates | Repair | Direct Mermaid | Style recovery |
| --- | ---: | ---: | ---: | --- | --- |
| `strict` | 1 | 1 | 1 | Off | Off |
| `extended` | 2 | 3 | 3 | On | On |
| `maximal` | 3 | 6 | 10 | On | On |

Explicit `candidate_count`, `type_candidate_count`, and `max_repair_iterations` values take
precedence over mode defaults. They cannot exceed 12, 3, and 10, respectively.
The repair count is the proposal limit for the structured `RepairEngine`. The default Marker processor
and fixture CLI configure an evidence-backed Flowchart repair engine. Labels are corrected only when
trusted Marker OCR or exact built-in Vector text matches the source block/bbox. In the Marker processor,
reversed edges and unlabeled missing edges can also be corrected when they are supported solely by a
built-in Geometry relation and there is no directional conflict between engines. An existing conditional
edge label is corrected label-only when trusted OCR/vector text and a unique built-in Geometry connector
independently support its text and direction/position, and exactly one source/typed edge exists in that
same exact direction. This is allowed only when the typed label is empty or is a typo similar to the source
label; an existing label with a different meaning is not overwritten.
The fixture CLI does not enable connector topology repair, preventing JSON from declaring its own trust,
and it does not automatically correct label fixtures without trusted Marker/Vector provenance. Missing
nodes, conditional topology, endpoint or direction changes, new branches and Yes/No semantic inference,
parallel relations, and layout repair are not yet wired into the default engine.

## Publication Policies

| Policy | Behavior after parse/render succeeds |
| --- | --- |
| `strict_validated` | Publish only when both aggregate and semantic scores are at least `review_below_score` |
| `best_effort_validated` | Publish A/B/C results when both aggregate and semantic scores are at least `publish_min_score` |
| `review_required` | Do not insert into Markdown; create sidecars/review data only |
| `sidecar_only` | Do not insert into Markdown; create sidecars only |

The alpha default is `review_required`. Automatic publication is an explicit opt-in until
production-scale corpus results demonstrate the automatic-publication precision target.
Under `sidecar_only`, saving a validated candidate to a sidecar records a successful result without
publishing it or requesting review.

No policy may publish a result that fails parse or render validation. The `trusted-local` security profile
can be combined only with `review_required` or `sidecar_only`.

## Main Options

| Name | Default | Description |
| --- | --- | --- |
| `mode` | `extended` | Safety/feature/budget preset |
| `publish_policy` | `review_required` | Automatic Markdown publication policy |
| `enabled_types` | All known types | Allowlist for typed/direct candidates |
| `publish_min_score` | `0.50` | Minimum best-effort score |
| `review_below_score` | `0.70` | Strict minimum score and review boundary |
| `security_profile` | `strict` | Mermaid source allowlist |
| `compatibility_profile` | `portable-rich` | Serializer style compatibility target |
| `candidate_count` | Mode-specific | Candidate limit per source |
| `type_candidate_count` | Mode-specific | Type top-k per source |
| `max_repair_iterations` | Mode-specific | Limit for improving repair candidates |
| `enable_fusion` | `true` | Deterministic merging of observations from multiple engines |
| `enable_page_detector` | `true` | Full-page coverage and missed structural-region proposals |
| `enable_style_recovery` | `true` | Emit node/edge/trusted-vector-group style evidence when compatibility/security permits |
| `runtime_dir` | Automatic cache lookup | Location of the Node worker and dependencies |
| `render_timeout_seconds` | `20` | Finite, positive parse/render limit per candidate |
| `max_mermaid_chars` | `50000` | Source character limit before browser delivery (`1`–`50000`, matching the pinned worker) |
| `max_mermaid_lines` | `5000` | Source line limit before browser delivery |
| `max_vlm_prompt_chars` | `100000` | Combined limit for the provider-visible prompt and Marker 1.10.2 response-schema reserve (`32768`–`1000000`) |
| `max_vlm_evidence_items` | `256` | Provenance evidence limit in the prompt (`1`–`4096`) |
| `max_vlm_ocr_items` | `512` | OCR text candidates examined for the prompt (`0`–`4096`) |
| `max_image_dimension` | `2048` | Maximum side length for VLM originals/overlays (`1`–`4096`) |
| `tile_size` | `1280` | Side length of a source-resolution tile (`64`–`4096`) |
| `max_virtual_source_dimension` | `32768` | Maximum side length for panel/merge canvases |
| `max_virtual_source_pixels` | `100000000` | Pixel budget for panel/merge canvases |
| `max_views` | `8` | Maximum views passed to the VLM (`1`–`16`) |

Runtime lookup is consistent across the CLI: an explicit `--runtime-dir` on `doctor` or
`install-runtime` takes precedence over `runtime_dir` in `--config`, then
`MARKER_MERMAID_RUNTIME_DIR`, then the automatic cache location. Validation and reconstruction use
the same order without the command-only override.

`sidecar_root` is not a supported option. Both its concise and Marker-prefixed forms fail closed:
sidecars, source images, Markdown, and metadata must stay inside one whole-document atomic output
transaction. A future external artifact backend will need an explicit cross-filesystem commit
contract rather than a path-only setting.

`write_ir`, `write_svg`, `write_png`, `write_alternatives`, and `write_provenance` control the
creation of their respective sidecar artifacts. The selected `final.mmd`, `scores.json`,
`review-history.json`, and manifest always remain as the bundle's minimum audit record. If the selected
candidate has a provenance-backed `node-id-map.json`, however, `provenance.json` is written even when
`write_provenance=false` so that no dangling reference is created. An automatically published bundle must
allow independent verification of its validation receipt, so preserving `final.svg` takes precedence over
`write_svg=false`. `write_png=false` still applies; in this case the optional PNG digest in the public
generation receipt remains as a validation-time audit value, while
`generation_artifact_presence.final.png=false` explicitly records that the file is absent.

At the type level, `include_original_image` and `extract_images` accept only `true`. They cannot be used
with Marker's shared `--disable_image_extraction` option.

`MermaidMarkdownRenderer_include_rendered_preview=true` stores the validation runtime's PNG separately as
`images/*--mermaid-preview.png` and inserts it after the original image. The default is `false`; no preview
is inferred for a candidate without a PNG, and an SVG is not rasterized implicitly. If the current PNG
bytes do not match the digest in the validation receipt, the Mermaid code is published but only the
preview is omitted.

## Options with Implemented Behavior

The edge map, Hough line map, detected-arrow overlay, OCR/vector/contour overlay, grayscale view,
adaptive threshold view, color-cluster view, thumbnail, source-resolution tiles, GeometryEngine, and
duck-typed VectorPrimitiveEngine are implemented. The vector engine extracts only from providers exposing
`get_drawings()`, `get_text()`, `vector_primitives`, or `vector_texts`. The Marker processor opens an actual
PDF page provider through PyMuPDF from the `marker` extra and passes it with source page-to-canvas mapping.
If a provider cannot be opened, the engine falls back to block duck typing and then returns a fail-closed
empty observation. The page-level detector uses bounded edge/component heuristics and occupied-region
exclusion. Unanchored proposals pass through the PageGroup queue and are preserved in sidecars, but are not
inserted into Markdown automatically.
See the [specification coverage matrix](spec-coverage.md) for the detailed distinction.

Detailed vector-extraction budgets are not currently exposed through Marker JSON or environment settings.
Custom integrations may adjust them within hard validation limits by constructing
`VectorPrimitiveEngine(max_primitives=..., max_texts=..., max_text_chars=..., max_points=...)`; do not invent
and pass new `MermaidDiagramProcessor_*` keys. Constructor defaults and hard validation limits follow.

| Vector-engine resource | Constructor default | Non-expandable limit |
| --- | ---: | ---: |
| Primitive/command raw work | 2,048 | 5,000 |
| Vector-text raw work | 5,000 | 20,000 primitive+text combined |
| Vector-text characters | 8,000,000 | 8,000,000 |
| Vector sources | 256 | 256 |
| Polygon / polyline points | 256 / 512 | 256 / 512 |
| Retained points across one reconstruction | 100,000 | 100,000 |
| Vector metadata token | 256 characters | 256 characters |
| Approximate-dedup comparisons | 250,000 | 250,000 |
| Text-ownership / endpoint comparisons | 1,000,000 / 1,000,000 | Same |
| Observation warnings | 256 | 256 |

Budgets measure reconstruction-global raw work, not retained output per source. Malformed, out-of-crop,
and deduplicated records, as well as empty nested drawing containers, consume budget. Once a count or
character dimension closes, later sources cannot reopen it. Source/raw iterables use at most one item of
lookahead. Geometry that exceeds its point limit is omitted as a whole rather than truncated to a prefix.
Point-free primitives can still be processed within the record-count budget after the aggregate point
budget is exhausted. After a comparison limit is reached, labels remain unassigned and connectors remain
unresolved, with a warning. Custom extractor output and direct input to
`VectorObservation.to_engine_observation()` are revalidated against the same limits. Raw-work accounting
and the fusion boundary are documented in [Vector Extraction and Fusion](vector-fusion.md).

State/Class/ER/Requirement/Block typed serializers and C4/Deployment/Component/Use-case fallbacks are
enabled when their types are present in the `enabled_types` allowlist. Because the requested type and actual
grammar can differ, also inspect the emitted type and fallback chain in the
[serializer contract](serialization.md). Pie/XY/Quadrant/Sankey/Radar/Treemap/Venn use the same allowlist and
contract. Automatic publication of numeric charts requires OCR or vector numeric evidence and minimum
numeric consistency. Structural candidates use only `ocr_token`, `vector_text`, `contour`,
`vlm_observation`, and `user_edit` as node evidence; `source_crop`, `line_segment`, and `arrowhead` do not
grant node credit. If two or more generated nodes cite the same eligible ID, that ID is canceled for all
of them before attribution is calculated. A result is not published automatically if this collision-free
attribution cannot be computed or is below 80%. This rule does not change existing configuration or schemas.

`tile_size` must be at least 64, and `tile_overlap` must be nonnegative and smaller than `tile_size`. View
slots first reserve one or two tiles for large sources, then apply type-specific priorities based on the
preceding engines' type top-k. Empty OCR/arrow/contour/Hough overlays do not consume a slot.

The Structured VLM provider-visible prompt combines the system instruction, active type contract,
view/selection manifest, prior evidence, OCR text, and the canonical `EngineObservation` schema reserve that
Marker 1.10.2 sends separately. This total must fit within `max_vlm_prompt_chars`; the provider is not called
when fixed content alone exceeds the limit. This value does not cover SDK-internal wire encoding or hidden
text added by an arbitrary custom service.

For Marker 1.10.2's stock Ollama service, a bounded inline response schema is used automatically so that
`$defs` are not lost. Other Marker services receive the original Pydantic schema class, and every response
passes the same canonical `EngineObservation` post-validation.

Evidence selection preserves user edits and trusted connectors first, then reserves at least 25% of the
remaining slots for arrowheads, lines, contours, and vector text in source-order round-robin. Remaining
slots are deterministically backfilled with trusted labels and the existing global priority, preventing a
large amount of OCR from displacing all later structural evidence. If a large record does not fit the
character budget, its JSON escape length is calculated without allocation; it is skipped before
serialization and a smaller later record backfills the slot. Every record and OCR string is included only
as a complete compact JSON item. Input/considered/included counts and the selection profile are recorded in
the prompt manifest, while candidate warnings summarize omission counts. Structured item/character omission
reasons and totals are recorded in the top-level `prompt_budget_notices` result.

Before canonical copying, the aggregate evidence-string length and the OCR-prefix string length truncated
by `max_vlm_ocr_items` each have a hard cap of 8,000,000 characters. OCR accepts exact plain strings only;
an item whose raw JSON-string lower bound exceeds the remaining prompt is skipped before escape scanning.
Nested source-block ID lists in evidence and trusted label/connector ID sets are also converted to immutable
snapshots only up to each schema item limit. Only those snapshots enter canonical validation and selection.

Independently of prompt settings, retained runtime evidence has a non-configurable aggregate provenance
contract. `VisualEvidence.source_block_ids` permits 20,000 total occurrences and a combined Python `len()`
of 8,000,000 for those IDs; duplicates count separately. The existing aggregate evidence-character total,
including `id`, `kind`, `text`, and `font_weight`, independently cannot exceed 8,000,000. Exact boundaries
are accepted; `+1` is rejected atomically at the initial/custom-engine collection,
reconstruction-global new-ID batch, fusion, or final-sink snapshot boundary. There is no
`MermaidDiagramProcessor_*` key for these values, and they do not alter the public Python configuration,
sidecar schema, or manifest version. Marker OCR production, Review provenance read/replacement/structured-add
boundaries, and standalone Structured VLM prior-evidence ingress use the same fixed budget. Structured VLM
checks the whole collection before choosing prompt items, so lowering `max_vlm_evidence_items` cannot hide
an oversized tail. Evaluation predictions share the source-block occurrence/character budget, but preserve
the public `mmx-eval-prediction-0.1` contract of 100,000 records/64 MiB; this is independent of the normal
runtime limits of 20,000 evidence items and 8,000,000 full-evidence characters. This evaluation exception
also has no configuration key.

`max_image_dimension` and `tile_size` are capped at 4,096 px. Views must be RGB Pillow images with
`original` as the first item. Names, count, a 4,096 px side limit, 16,777,216 pixels per view, and
33,554,432 pixels in total are checked before the provider call. At most `max_views + 1` entries are read
from the input dictionary, and both the manifest and image list are built from the same validated,
independent ordered list of plain-Pillow snapshots. Caller-owned images or stateful Pillow subclasses are
therefore not passed to the provider after validation. Caller property/load/copy hooks are not executed;
lazy ImageFile subclasses must already be loaded before the call.

The following values are non-configurable hard caps for reconstruction sources.

| Input | Hard cap | Behavior for excessive or noncanonical input |
| --- | ---: | --- |
| `source_block_ids`, `page_ids`, `source_blocks`, `vector_sources` | 256 items each | Isolate the entire collection |
| Initial/custom-engine/fused evidence | 20,000 items across the reconstruction | Isolate the initial/engine collection or fused observation |
| `source_block_ids` in retained evidence | 20,000 logical occurrences and 8,000,000 Python characters total | Count duplicates; atomically isolate the collection or whole new-ID batch |
| Source OCR | 50,000 items and 1,000,000 characters total | Isolate the entire OCR collection |
| Evidence ID/kind/text/font-weight/source-block text | 8,000,000 Python characters total | Isolate the evidence collection or whole new-ID batch |
| Typed IR candidate | Three envelope fields, depth 64, 100,000 items, 50,000 characters per field, 1,000,000 bytes of UTF-8 text, 4,000,000 bytes of compact JSON | Isolate that candidate |
| Observation/fused typed IR | At most 64 candidates and 8,000,000 compact JSON bytes total | Reject the provider/fixture observation or retain a bounded fusion prefix |
| `source_mapping` | Depth 32, 25,000 items, 50,000 characters per string, 4,000,000 bytes of compact JSON | Isolate only the mapping as `null` |

The `vector_sources` source-context entry above is the pipeline-boundary rule that isolates an entire
noncanonical or oversized collection. When `VectorPrimitiveEngine` is injected directly outside the
pipeline, its additional backstop consumes only a 256-source prefix plus one lookahead item and records a
warning. These boundaries protect the caller container and engine work independently; neither replaces
the other.

`source_mapping` accepts exact `dict`/`list`/`tuple` containers and JSON scalars only. Tuples normalize to
JSON arrays, keys are sorted, and numbers must be finite and within the JavaScript safe-integer range. This
snapshot is reused and revalidated by engines, repair, the final result, and sidecars, so container-subclass
iteration and `deepcopy` hooks are not executed.

Typed IR hard caps are likewise not configurable. Dictionary keys and string values are counted by
occurrence, and tuples normalize to JSON arrays. Numbers must be finite and within the JavaScript safe
range; cycles and container/scalar subclasses are rejected. Extra fields beyond a candidate's
`diagram_type`, `ir`, and `confidence` are rejected before any unbounded copy. Accessibility-added
title/description fields and semantic-repair proposals must pass the same limits again before reaching a
serializer or sidecar.

Under the default `strict` security profile, `enable_style_recovery=true` still emits no style statements.
To produce actual style code, explicitly combine `portable-rich`/`style-rich` compatibility with a
non-strict security profile such as `style-only`; results still pass the parse/render/SVG hard gate. PDF
label weight is restored only as the constant `font-weight:bold`, and only when trusted vector-span evidence
has a collision-free ID and maps unambiguously by text/bbox to a generated Flowchart node.
