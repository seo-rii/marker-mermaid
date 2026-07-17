# Release evaluation

`marker-mermaid evaluate` aggregates the MMX-001 §23 test corpus and §24 acceptance criteria under
the fixed `mmx-001-v0.3-extended` profile. It is not a benchmark runner that performs PDF
conversion or VLM calls. It accepts predictions and telemetry produced by a trusted runner and is
responsible for artifact integrity and reproducible aggregation.

```bash
marker-mermaid evaluate corpus/manifest.json --output output/evaluation
```

A gate that lacks required evidence is not treated as successful; it is recorded as `unavailable`.
The overall status is `fail` if any gate fails, `unavailable` if none fail but at least one is
unavailable, and `pass` only when every gate is satisfied. The corresponding CLI exit codes are
`1`, `1`, and `0`; manifest/path/hash errors return `2`, and report I/O errors return `3`. The
report's `attestation` value is always `trusted_runner_input`. A `pass` therefore means that
hash-bound inputs satisfied the fixed profile's numeric conditions. It does not cryptographically
prove the runner's identity or the truth of its telemetry.

## Trust boundary

The input is divided into three distinct artifacts:

- `source`: the actual image/PDF or synthetic source
- `ground_truth`: independently annotated diagram type, Scene IR, OCR labels, and numbers
- `prediction`: runner-produced type, `generated_scene_ir`, publication result, validation data,
  and hard-gate telemetry

The manifest binds all three files by relative path and SHA-256. Absolute paths, `..`, symlinks,
paths that resolve outside the root, and files with mismatched digests are rejected. The
source-observed `scene-ir.json` must not be used as the prediction. For Typed/Scene candidates, put
the output-structure sidecar `generated-scene-ir.json` in the prediction artifact's
`generated_scene_ir`. If a Direct Mermaid candidate has no such structure, record `null`;
provenance and structural gates then fail closed as `unavailable` or low recall.

`telemetry` contains trusted-runner measurements, not values independently rerun and verified by
the aggregator. The runner must observe the following directly in an isolated runtime:

- whether the original image exists in and is referenced by the final document
- whether published Mermaid passed the security scan, parse, render, and SVG inspection
- whether injected candidate failures propagate into a whole-document failure
- whether any external action, duplicate insertion, orphan process, or candidate-budget overrun
  occurred

To keep this trust boundary explicit, the prediction schema calls the field `telemetry`, and the
report includes a SHA-256 snapshot of the manifest.

The prediction also includes a `VisualEvidence[]` registry. A non-empty `evidence_ids` string on a
generated node does not establish provenance by itself. It must intersect IDs of kind `ocr_token`,
`vector_text`, `contour`, `vlm_observation`, or `user_edit` that actually exist in the same
hash-bound prediction-artifact registry. `source_crop`, `line_segment`, and `arrowhead` provide no
node-provenance credit. If two or more generated nodes in one case reference the same eligible
evidence ID, that ID is revoked from every claimant. Collision computation is case-local, so an
identical local ID in another corpus case does not collide. Relation and group references also do
not participate in node-claim collisions.

The prediction evidence registry continues to allow up to 100,000 records. This item capacity is
independent of the normal reconstruction runtime's 20,000-evidence-item limit, so a small registry
without source-block references remains valid beyond 20,000 records. Across the whole registry,
however, `source_block_ids` is limited to 20,000 occurrences, including duplicates, and the sum of
Python `len()` values for those IDs is limited to 8,000,000 characters. The exact boundary is
accepted; `+1` makes the prediction artifact invalid even when its hash matches. Before constructing
`VisualEvidence` objects, the loader creates detached canonical snapshots from plain JSON records,
so an over-budget prefix is not left behind for evaluation. A manifest error returns CLI exit code
`2` and writes neither an existing nor a new report directory.

To preserve prediction `0.1`'s contract of 100,000 records and 64 MiB per JSON artifact, evaluation
uses the verified artifact byte limit instead of the normal runtime's 8,000,000-character
full-evidence limit. This strengthens only the provenance fan-out dimension without changing the
schema field or version. The raw object tree for JSON up to 64 MiB is still materialized by the JSON
parser before per-field snapshotting; fully streaming ingestion remains separate process-isolation
work. Unknown fields on evidence objects that the existing Pydantic parser ignored in prediction
0.1 remain ignored, and only public `VisualEvidence` fields enter the canonical registry. Strict
unknown-field rejection at other runtime and Review raw-ingress boundaries is unchanged.

## Manifest contract

The top-level schema is `mmx-eval-manifest-0.1`.

```json
{
  "schema_version": "mmx-eval-manifest-0.1",
  "gate_profile": "mmx-001-v0.3-extended",
  "corpus": {
    "corpus_id": "enterprise-diagrams",
    "version": "2026.07",
    "license": "CC0-1.0",
    "split": "release-test"
  },
  "cases": [
    {
      "case_id": "architecture-001",
      "fixture_group": "architecture_c4",
      "fixture_tiers": ["real_enterprise", "multilingual"],
      "source_origin": "real",
      "scope": "end_to_end",
      "languages": ["ko", "en"],
      "source": {"path": "sources/architecture-001.png", "sha256": "...64 hex..."},
      "ground_truth": {"path": "truth/architecture-001.json", "sha256": "..."},
      "prediction": {"path": "predictions/architecture-001.json", "sha256": "..."}
    }
  ]
}
```

`fixture_tiers` is an array because one case can be both real enterprise and multilingual. `scope`
is one of `serializer`, `end_to_end`, `detector`, or `fault_probe`. The §24 feature gate recognizes
only cases with `source_origin=real` and `scope=end_to_end`. A `fault_probe` must record actual
candidate-failure injection in prediction telemetry.

Each configured diagram type has a fixed stability and fixture group. For example, Flowchart must
be `core/flowchart`, C4 must be `experimental/architecture_c4`, and Pie must be
`extended/data_chart`. A source SHA-256 may not count as two fixtures even when its path and case ID
differ; such a manifest is rejected.

The `mmx-eval-ground-truth-0.1` ground-truth schema requires an independent Scene IR for a positive
case. A negative case has `expected_reconstruction=false` and `type_stability=negative` and may not
contain a type, Scene, labels, or numbers. The prediction schema is `mmx-eval-prediction-0.1`. A
published result whose `syntax_valid`/`render_valid` is false or null remains a valid artifact; the
hard gate reports it as `fail`. An aggregate provenance resource violation is an artifact/manifest
error rather than a quality failure and is rejected before report aggregation.

A positive Scene must contain at least one node, and the `ocr_labels` token multiset must include
every token from text-bearing nodes. A numeric type requires either `numeric_applicable=true` plus
at least one finite Decimal value, or `numeric_applicable=false` plus a reason. `1`, `1.0`, and `1e0`
compare as the same canonical Decimal. A Flowchart must state `path_applicable` and give a reason
when it is false. `human_accepted` for automatically published A/B/C end-to-end results belongs in
the hash-bound ground-truth annotation, not the prediction. If any such annotation is missing, the
human-review coverage gate fails and the acceptance rate is unavailable.

## Fixed gates

Minimum fixture counts are Flowchart 100, UML 100, Architecture/C4 80, BPMN/Swimlane 80, planning
80, data chart 120, mindmap/tree 50, specialized 100, and negative 150. The feature gate requires at
least one real, end-to-end, parse/render-successful fixture for each of the specification's 22
types. Only end-to-end cases count toward positive fixture minima, and only detector cases count
toward negative minima, so serializer and fault probes cannot inflate corpus size.

Structural precision/recall and Flowchart edge/path F1 are micro scores computed from total true
positives, false positives, and false negatives, not means of per-case F1. OCR recall separates the
token multiset by case ID even when the same label appears in multiple cases, preserving repeated
label omissions within and across cases. Architecture node recall does not mix in C4; it uses only
cases whose ground-truth type is exactly `architecture`. Nodes and relations generated for a
negative image are structural false positives.

Only `scope=end_to_end` contributes to the positive-quality denominator; serializer and fault
probes are excluded. Only `scope=detector` negative hallucinations contribute to structural
precision. Data charts are excluded from structural-diagram precision/recall and are evaluated by
numeric exact match. Flowchart edge F1 uses a directed-arc multiset derived from arrow flags, so
reversing every arrow does not match. Node IDs match directly only when ground truth declares
`shared_id_namespace=true`; by default, independently annotated truth and prediction are aligned by
unique normalized label.

When paths are applicable to a Flowchart, reference paths must actually be enumerated. If generated
path enumeration exceeds the 10,000-path or 100,000-state budget, the required metric is not
silently omitted; it becomes unavailable and records `unavailable_case_ids`. Experimental
end-to-end results have separate gates for a warning, a sidecar, reviewability, and recorded
hallucination precision.

## Output

The output directory is built as a complete temporary tree and then replaced.

```text
output/evaluation/
├── .marker-mermaid-evaluation.json
├── evaluation-report.json
├── evaluation-report.md
├── manifest-snapshot.json
└── cases/
    └── architecture-001.json
```

The JSON report schema is `mmx-eval-report-0.1`. It retains the manifest digest, corpus metadata,
fixture counts, observed/required/sample counts for every gate, raw TP/FP/FN, evidence and
unavailable case IDs, and per-case type/node/edge/path metrics. `manifest-snapshot.json` contains
the verified original bytes rather than a normalized reserialization, so it reproduces the
report's manifest SHA-256 exactly.

Replacing an existing output directory requires an evaluator ownership marker. An ordinary
directory, symlink, corpus root, or ancestor of the corpus root is rejected, preventing input such
as `--output .` from deleting existing work.
