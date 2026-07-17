# Marker 1.10.2 integration

## Processor order

`MarkerMermaidPdfConverter` retains Marker's complete default processor tuple and inserts two
processors at exactly this position:

```text
ReferenceProcessor
MermaidCandidateDiscoveryProcessor
MermaidDiagramProcessor
BlankPageProcessor
DebugProcessor
```

Passing `processor_list` to Marker replaces the entire default list, so passing only the Mermaid
processors is incorrect. This is why the dedicated converter exists. The integration does not
modify Marker's global block registry.

## Target blocks

```python
DEFAULT_BLOCK_TYPES = (
    BlockTypes.Figure,
    BlockTypes.Picture,
    BlockTypes.ComplexRegion,
)
```

Discovery, reconstruction, and rendering share one candidate iterator over both structured
blocks and page `current_children`. An anchor can own original/full-page, panel, and merged
sources. Their deterministic order is original/full-page → panel ID → merged.

Reconstruction calls `Block.get_image(document, highres=True)` without expansion. An original is
the exact block crop; a panel is a bounding box within that raw crop; a merge places multiple raw
fragments on a virtual canvas. Each span is intersected and clipped against its fragment page
bounding box before the page-to-canvas affine transform is applied. Labels from another panel
therefore do not leak into OCR recall, and labels on a following page receive the correct canvas
offset.

## LLM service adapter

Marker's dependency resolver injects the service through the constructor parameter
`llm_service`. Only `MarkerStructuredVLMEngine` knows the Marker service API; the core pipeline
depends solely on the `CandidateEngine` protocol. The default order is Vector Primitive,
Geometry, then Structured VLM. For a merged source, all source blocks and assembly
`page_to_canvas` mappings are passed to the vector engine.

A VLM request contains the prompt, an updated multi-view image list, the anchor block, and the
`EngineObservation` response schema. The prompt includes OCR tokens and a bounded selection from
earlier vector/geometry evidence. Marker 1.10.2's Claude service adds the same schema to system
text, so the adapter conservatively reserves that size in the request budget; the reserve also
applies to the other built-in providers. The adapter attaches the actually selected prior IDs
and a `PromptBudgetNotice` to the final observation as private metadata outside the provider
schema. The pipeline revalidates the observation, preserves this metadata separately, and applies
the candidate budget in round-robin order across fused and per-engine candidates.

Before the engine adapter boundary, the pipeline validates block/page IDs, OCR, evidence, and
block/vector source collections as exact plain lists with hard caps. One malformed collection is
isolated to a safe default without failing other source metadata or document conversion.
Composite/merged `source_mapping` is likewise frozen as a bounded canonical JSON snapshot; an
invalid mapping is omitted while reviewable failure metadata remains. Every engine receives a
freshly restored source snapshot, so mutations by one custom engine cannot reach the next engine
or semantic repair.

## Why a dedicated renderer is required

Marker 1.10.2's default renderer treats only `Figure` and `Picture` as image blocks and does not
copy internal metadata into document metadata. `MermaidMarkdownRenderer` adds:

- Original extraction for `ComplexRegion`
- Exactly one validated Mermaid insertion per source after the original image
- Revalidation of the pipeline receipt binding final Mermaid/SVG to the publication decision
- Additional panel and merged virtual-source images
- Grade B/C warnings
- Mermaid metadata collection
- Relocation of image references beneath `images/`
- Rejection of `extract_images=false`

Marker's default `save_output` cannot write nested sidecars, so the CLI uses
`save_document_output`.

Neither `publish=true` nor the `syntax_valid`/`render_valid` flags can bypass the renderer. If
source, SVG, or policy results change after validation—or if the process-private receipt seal is
missing—the renderer omits Mermaid and preview for that source while keeping the original image.
Preview PNG is included only when its separate digest matches the current bytes. A mismatch
isolates the preview but retains Mermaid code.

After boolean checks, the renderer never reads the live candidate again. Code, grade/score,
optional PNG, and both receipts are captured in one immutable publication snapshot and sealed
with a process-private HMAC. Mermaid fences and previews come from that same snapshot. Manually
constructing a snapshot or changing values with `model_copy(update=...)` invalidates the seal.
Preview-omission diagnostics do not mutate the already sealed candidate warning list, so the
later sidecar quality receipt remains valid.

The snapshot and Marker's JSON-safe `mermaid` summary do not infer stability from the grade; they
preserve the selected serializer's `stable`, `extended`, or `experimental` status. A grade-A
experimental candidate therefore remains marked experimental in both Markdown and internal
metadata. If `publish=true` but no trusted snapshot can be produced, the summary does not trust
the mutated live stability value and downgrades it to `experimental`.

## Metadata

Keys are separated to keep the serialization boundary explicit.

| Key | Content |
| --- | --- |
| `mermaid_candidate` | JSON-safe source-registry summary |
| `mermaid_candidate_images` | Runtime-only fragment ID → raw Pillow image |
| `mermaid` | JSON-safe reconstruction summary and per-source errors |
| `mermaid_results` | Runtime-only list of `ReconstructionResult` objects |
| `mermaid_source_images` | Runtime-only virtual-output Pillow images |

The document metadata writer does not use a `default=str` fallback. If a Pillow image or Marker
object enters a JSON summary accidentally, serialization fails immediately before writing.
