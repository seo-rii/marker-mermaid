# Output Format

## Markdown Invariants

The original image always appears first, and at most one Mermaid fence is inserted for the same source.
If `syntax_valid && render_valid` is false, the policy rejects publication, or the final source/SVG
validation receipt does not match the current artifacts, only the image remains. Grades B and C include
warnings, with grade C using the stronger `Experimental reconstruction` wording.

Before validation, the pipeline adds one terminal LF only when the source does not already have one. It
does not subsequently strip trailing whitespace or additional newlines. The receipt's `code_sha256`,
`final.mmd`, and the payload inside the Markdown fence therefore refer to the same UTF-8 bytes. A standalone
validator input without a terminal LF may parse and render, but it does not receive an automatic-publication
certificate. The fence delimiter is chosen to be longer than the longest backtick run in the source, so
even a physical three-backtick line inside a multiline label cannot close the Markdown block early.

When one Marker anchor has virtual sources, output order is original Mermaid, panel image/Mermaid, then
merged image/Mermaid. An original that was assembled successfully is preserved even when a virtual source
has review/failed status.

## Sidecar Bundle

Each source has an independent bundle under `diagrams/<safe-source-id>/`. The writer writes everything to a
temporary directory under the same parent and publishes the final directory with a no-replace rename based
on the same directory descriptor. Final publication uses Linux `renameat2(RENAME_NOREPLACE)` or macOS
`renameatx_np(RENAME_EXCL)`, so it does not overwrite a destination created immediately after the check. A
runtime without a safe no-replace primitive fails before publication instead of using an unsafe fallback.
Existing bundles are never overwritten automatically. Path components are normalized with an allowlist;
absolute paths and `..` are rejected. `diagrams` must be a real direct child of the output root, and symlinks
are rejected. Temporary-directory creation, nested-file writes, final rename, and failure cleanup are all
anchored to the initially opened `diagrams` descriptor through the `openat`/`mkdirat`/`unlinkat` family.
Consequently, replacing a path or symlink after validation cannot make the writer open or remove another
tree.

The `schema_version` in `manifest.json` is `mmx-sidecar-0.5`. Version `0.5` adds a
`generation_validation_receipt` binding the publication candidate's final Mermaid source to the inspected
SVG. Since `0.4`, the source-observed `scene-ir.json` is distinct from `generated-scene-ir.json`, which is
reconstructed from the selected candidate. The former contains OCR/CV/VLM extraction evidence; the latter
is the quality-evaluation target. A candidate whose generated structure cannot be extracted safely, such as
Direct Mermaid, omits `generated-scene-ir.json` and must be treated as structurally unevaluable.

```json
{
  "schema_version": "mmx-sidecar-0.5",
  "source_id": "_page_4_Figure_2",
  "source_image": "images/_page_4_Figure_2.jpeg",
  "source_kind": "panel",
  "source_block_ids": ["/page/4/Figure/2"],
  "page_ids": [4],
  "anchor_block_id": "/page/4/Figure/2",
  "status": "success",
  "grade": "B",
  "publish": true,
  "review_required": false,
  "selected_candidate_id": "candidate-1",
  "requested_diagram_type": "c4",
  "emitted_diagram_type": "architecture",
  "runtime_diagram_type": "architecture",
  "fallback_chain": ["c4", "architecture"],
  "serialization_stability": "experimental",
  "generation_validation_receipt": {
    "schema_version": "1",
    "code_sha256": "sha256...",
    "svg_sha256": "sha256...",
    "png_sha256": "sha256...",
    "security_profile": "strict",
    "emitted_diagram_type": "architecture",
    "runtime_diagram_type": "architecture"
  },
  "generation_publication_receipt": {
    "schema_version": "1",
    "source_id": "_page_4_Figure_2",
    "selected_candidate_id": "candidate-1",
    "candidate_validation_sha256": "sha256...",
    "candidate_quality_sha256": "sha256...",
    "publish_policy": "best_effort_validated",
    "security_profile": "strict",
    "publish": true,
    "review_required": false,
    "status": "success",
    "grade": "B",
    "serialization_stability": "experimental"
  },
  "generation_artifact_presence": {
    "final.mmd": true,
    "final.svg": true,
    "final.png": true
  },
  "files": {
    "final.mmd": "sha256...",
    "final.svg": "sha256...",
    "final.png": "sha256...",
    "scores.json": "sha256...",
    "review-history.json": "sha256...",
    "source-map.json": "sha256..."
  },
  "prompt_budget_notices": [
    {
      "engine": "marker_structured_vlm",
      "selection_profile": "structural-quota-v1",
      "prompt_chars": 72144,
      "max_prompt_chars": 100000,
      "schema_reserve_chars": 14753,
      "max_evidence_items": 256,
      "max_ocr_items": 512,
      "evidence_total": 380,
      "evidence_considered": 259,
      "evidence_included": 256,
      "ocr_total": 640,
      "ocr_considered": 512,
      "ocr_included": 498,
      "omission_reasons": [
        "evidence_item_limit",
        "evidence_char_limit",
        "ocr_item_limit",
        "ocr_char_limit"
      ],
      "selected_evidence_sha256": "sha256..."
    }
  ],
  "failures": []
}
```

`prompt_budget_notices` is an optional additive `0.5` field containing a bounded audit record made by the
adapter for each Structured VLM call. The provider response schema contains neither this field nor the
selected evidence-ID set, so a response cannot forge a notice or publication authority. The record remains
whether or not a candidate is produced and includes `prompt_chars + schema_reserve_chars <=
max_prompt_chars`, input/considered/included counts, and item/character omission reasons. If the provider
call or canonical response validation fails after the bounded prompt is complete, the same notice is kept
in the failure result and sidecar. The numbers above illustrate the output format; schema serialization
length can differ across supported Pydantic environments. `selected_evidence_sha256` is an opaque run
commitment and correlation identifier for the sorted selected-ID set. Because that set is process-private,
the digest is neither independently auditable from the sidecar nor publication authority; actual
publication authority also remains process-private metadata.

Values under `files` are content SHA-256 hashes. `final.*` files are created only for the candidate selected
after passing the hard gate. Failed or unselected candidates remain under `alternatives/` as JSON and, when
available, `.mmd`. `generation_validation_receipt.code_sha256` and `svg_sha256` are the exact UTF-8 artifact
digests of `final.mmd` and `final.svg` at bundle creation. Public digests audit the automatically generated
baseline; they are not publication authority by themselves. The generation pipeline attaches a
process-private HMAC seal to the same receipt, which the Markdown renderer and sidecar writer verify again.
If a `publish=true` result loses its seal or either artifact changes, the writer removes the temporary bundle
and fails atomically. Republishing a deserialized result requires regenerating and inspecting the source and
SVG with a trusted validator. Once Review begins, this receipt describes the immutable `r000000`
automatically generated baseline; content digests in `manifest.files` and `review-state.json` track the
current revision separately.

A detailed ReviewStore load checks both generation receipts and `generation_artifact_presence` from a `0.5`
bundle against root `final.*` before the first mutation, and against the immutable
`versions/r000000.*` baseline and `scores.json` afterward. The current revision is validated separately by
the content digests in `review-state.json` and `manifest.files`. Summary listings perform only a lightweight,
code-focused check. They never mix the generated baseline with user-edited revisions to reinterpret a
receipt, and they preserve read compatibility without reapproving an uninspected PNG from an older sidecar
schema as a new artifact. Optional `png_sha256` remains when the validation runtime produced a PNG, while
`generation_artifact_presence` separately states whether those bytes are included in the bundle. Thus,
`write_png=false` omits only `final.png` and does not mutate the validation receipt referenced by the
publication receipt. In contrast, the SVG supporting the automatic-publication hard gate is forcibly
preserved as `final.svg` even with `write_svg=false`, allowing independent in-bundle verification. When a
nonautomatic bundle omits its SVG, both mutually referencing generation receipts are omitted as well so no
orphan reference remains. `generation_publication_receipt` fixes the policy/status/review decision for the
same baseline, so changing flags alone cannot automatically publish a `review_required`, `sidecar_only`, or
`trusted-local` result. The two candidate digests use the following canonical encoding:

- `candidate_validation_sha256`: convert `generation_validation_receipt` to JSON-mode enum values,
  serialize it as UTF-8 JSON with `ensure_ascii=false`, sorted keys, and compact `,`/`:` separators, then
  calculate SHA-256.
- `candidate_quality_sha256`: project `aggregate_score`, `grade`, `metrics`, and `warnings` from
  `scores.json`, converting the aggregate and each metric to exponent-free decimal strings. Strip trailing
  fractional zeroes and the decimal point, normalize `-0` to `"0"`, and preserve `null` for an unevaluable
  aggregate. Metric keys accept lowercase ASCII `[a-z][a-z0-9_]*` only, eliminating locale-dependent Unicode
  key-order differences. Serialize that object under the same canonical JSON rules and calculate SHA-256
  over its UTF-8 bytes.

For example, `aggregate_score=-0.0`, `metrics={"tiny": 1e-7, "zero": -0.0}`, grade C, and no warnings encode
as `{"aggregate_score":"0","grade":"C","metrics":{"tiny":"0.0000001","zero":"0"},"warnings":[]}`
with SHA-256
`ee36d80539010204f914e727bf574ddd015272566ff6981b57a377d86d2d09a5`. NaN and infinity are not valid
canonical inputs. These rules are distinct from the content hash of a pretty-printed sidecar file. Before
calculating paths, the automatic-publication writer deep-copies the entire `ReconstructionResult` once,
checks continuity of the publication core and private seal before and after copying, and writes only that
snapshot's receipts and artifacts to the temporary directory before the atomic rename. It therefore fails
instead of creating a mixed bundle if the live result changes concurrently or a `__deepcopy__` hook changes
the source.

`node-id-map.json` is added, and its content hash recorded in `manifest.json.files`, only when the selected
typed `flowchart` or `generic_network` candidate safely completes a full, injective node-ID remap. For every
mapping, the file preserves `source_owner`, original `source_id`, `fused_id`, independent `vector`/`geometry`
authority owner, `match_method` (`identity`/`unique_iou`), IoU of at least 0.45, original `source_text`, and
both bbox/evidence-ID sets. The immutable mapping's `claim_digest` is a canonical SHA-256 consistency digest
of those fields. It is audit material for an ID change; it neither replaces `provenance.json` nor declares
new evidence.

The file's top level is a JSON array of mapping objects. `source_bbox` and `authority_bbox` are
`[x1, y1, x2, y2]` normalized to `[0, 1]`, not per-source pixel coordinates. `source_owner` and
`authority_owner` are deterministic identifiers that distinguish inputs within one fusion run, not durable
IDs across document reruns. Every source/authority evidence ID must occur exactly once in the same bundle's
reconstruction provenance; the atomic writer rejects bundle creation for a missing or duplicate reference.
The writer revalidates evidence payloads against the current Pydantic schema and checks that source-evidence
bbox/text, authority contour bbox, and mapping evidence are actually connected to the fused Scene node. It
also verifies that both sides' block intersections overlap the reconstruction's `source_block_ids`. Each
evidence ID may be cited only once across all mappings. The generation pipeline places a process-private
HMAC certification seal on the mapping list, and the writer requires it, so a model copy or directly
constructed mapping cannot be recertified as an automatic extraction result. This seal is not a sidecar
field; it is a trust boundary within the same reconstruction process. When a mapping exists, this reference
integrity contract takes precedence over `write_provenance=false`, so `provenance.json` is forcibly written
with it.
This file is a generation-time audit artifact. Later Review edits add revision history and user evidence,
but do not recompute the existing automatic mapping as though it were a new extraction result.

The file is not created for unsupported nested/non-flow types, Direct Mermaid, Scene fallback, or a
candidate whose mapping is ambiguous, partial, or conflicting. In these cases, the typed candidate retains
the entire original ID space, and no sidecar with only some references changed is created. Absence of this
file therefore means no remap was performed; it does not by itself mean candidate parse/render failed.

`review-history.json` starts as an empty array and records review edits, candidate selection, natural-language
patches, approval/rejection, and undo/redo as append-only `ReviewHistoryEntry` records. The first mutation
creates `review-state.json` and the initial `versions/r000000.*` snapshot; later revisions are added
immutably. Mermaid, Scene IR, SVG/PNG, provenance, advisory layout, manifest hash, state, and history are
replaced as one review commit, restoring prior artifacts on an I/O failure. Provenance payloads are retained
without duplication as SHA-256 content-addressed `versions/provenance/<digest>.json` files, which each
revision snapshot references by digest. Layout payloads are similarly preserved under
`versions/layout/<digest>.json`; root `layout-hints.json` and its manifest hash are created or removed together
during undo/redo. Layout contains only normalized node centers and does not modify source Scene bboxes.

The `mmx-review-0.4.1` state records current/legacy provenance and advisory-layout digests. Version `0.4`
snapshot provenance and the `0.3` static provenance timeline support lazy migration and undo/redo without
rewriting existing snapshots. Optimistic `version` and code SHA-256 values block stale browser writes. The
Review API exposes only the active state-validated `timeline` and `cursor`. `checkout_revision` restores the
target root artifacts and manifest hashes without creating a new snapshot. Subsequent edits branch the
active timeline after the cursor while retaining existing immutable snapshots. If Mermaid source changes
but the validator returns only a boolean/`None`, previous SVG/PNG files are removed from root artifacts and
are not treated as a render of the current code. Approval is possible only when a
`ReviewValidationResult` returns a new strict SVG and optional inspected PNG, so stale renders cannot be
reused as approval evidence. After an alternative candidate is selected, the generation-time manifest is
not overwritten; `review-state.json.selected_candidate_id` represents the current review selection.

`source-map.json` preserves serialized `DiscoveredSource` data, fragment crop/page bboxes, canvas placement,
and source-to-canvas/page-to-canvas affine transforms so canvas provenance can be traced back to PDF pages
and source blocks. It records only a canonical snapshot of exact JSON-compatible mapping accepted by the
pipeline. Object keys are sorted and tuples become JSON arrays. Mappings are limited to depth 32, 25,000
total items, 50,000 characters per field, and 4,000,000 bytes of compact escaped JSON; non-finite numbers
and values outside the JavaScript safe-integer range are rejected. Before serialization or deep copy, the
sidecar writer fixes the mapping again with the same hook-free walker. If the live mapping changes during
snapshotting, the bundle is not published. A mapping isolated by the pipeline does not create
`source-map.json`; its cause remains in `failures`.

`typed-ir.json` and alternative-candidate JSON contain only typed IR recanonicalized immediately before the
sink. Each IR is at most 1,000,000 bytes of UTF-8 text and 4,000,000 bytes of compact escaped JSON, and must
pass the exact plain-JSON container/scalar, depth/item/field/numeric/cycle contracts and the 256-item
`evidence_ids` limit for each known record. Before `model_dump`, JSON serialization, or deep copy of selected
and alternative candidates, the writer replaces their IR with this snapshot inside a safe shallow
candidate. If a live candidate changed after generation or changes again during snapshotting, the temporary
bundle is not published.

Retained `VisualEvidence.source_block_ids` in `provenance.json` cannot exceed 20,000 logical occurrences,
including duplicates, or 8,000,000 Python string characters. A separate existing 8,000,000-character
evidence cap covers all `id`, `kind`, `text`, `font_weight`, and source-block IDs. Exact boundaries are
preserved; `+1` atomically rejects the entire collection. Before calling `model_dump` on live evidence,
deep-copying the result, or producing JSON, the writer checks this contract with a hook-free detached
snapshot and uses only that validated snapshot for sink payloads and `provenance.json`. Output preflight
applies the same check to every final result before writing any image, and reuses the reconstruction snapshot
with detached evidence through the later sidecar write. Unvalidated provenance therefore cannot be mixed
into the bundle later even if a caller changes live evidence while images are being saved.

This is an internal runtime change that strengthens write ordering and the memory boundary; it does not
change the `provenance.json` record shape, `manifest.json`, or the `mmx-sidecar-0.5` schema version. Marker OCR
production and Review root/revision reads, trusted replacement, digest/commit, and structured-add boundaries
use the same aggregate gate. Evaluation prediction artifacts must also pass the same source-block aggregate
gate after hash verification; failure does not create or replace the evaluation-report tree. Prediction and
report schemas remain `mmx-eval-prediction-0.1` and `mmx-eval-report-0.1`.

Marker Markdown output with `include_rendered_preview` enabled adds a runtime PNG under `images/` only when
its exact bytes match the PNG SHA-256 in the validation receipt.
The original image remains first, and a preview cannot bypass the publication decision for Mermaid code.
Requested and emitted/runtime types remain separate, so a portable fallback is not presented as a native
reconstruction.

Before creating files, the writer checks source/image/sidecar/alternative name collisions, a missing source
image, an existing bundle, metadata JSON serializability, and the final-result evidence budget. Each
source-specific bundle is then published atomically from a temporary directory.

## JSON Serialization

PNG bytes and SVG text are not duplicated in candidate JSON. They are saved as artifact files; candidate
JSON contains validation state, scores, warnings, IR, and code. Document metadata provides a per-source
summary and sidecar path.
