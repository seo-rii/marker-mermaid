from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import marker_mermaid.cli as cli_module
import marker_mermaid.evaluation as evaluation_module
import marker_mermaid.models as models_module
from marker_mermaid.evaluation import (
    EvaluationManifestError,
    evaluate_manifest,
    load_evaluation_manifest,
    write_evaluation_report,
)


def _scene(*, evidence: bool = True) -> dict:
    evidence_ids = ["ocr-a"] if evidence else []
    return {
        "elements": [
            {
                "id": "A",
                "role": "process",
                "text": "Start",
                "bbox": [0, 0, 10, 10],
                "evidence_ids": evidence_ids,
            },
            {
                "id": "B",
                "role": "process",
                "text": "End",
                "bbox": [20, 0, 30, 10],
                "evidence_ids": ["ocr-b"] if evidence else [],
            },
        ],
        "relations": [
            {
                "id": "E",
                "source_id": "A",
                "target_id": "B",
                "relation_type": "arrow",
                "evidence_ids": ["line-e"] if evidence else [],
            }
        ],
        "groups": [],
        "reading_direction": "LR",
        "diagram_type_candidates": ["flowchart"],
    }


def _write_json(path: Path, value: dict) -> str:
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _write_manifest(
    root: Path,
    *,
    ground_truth: dict | None = None,
    prediction: dict | None = None,
    fixture_group: str = "flowchart",
    fixture_tiers: list[str] | None = None,
    scope: str = "end_to_end",
) -> Path:
    source = root / "source.bin"
    source.write_bytes(b"source-image")
    reference = ground_truth or {
        "schema_version": "mmx-eval-ground-truth-0.1",
        "expected_reconstruction": True,
        "diagram_type": "flowchart",
        "type_stability": "core",
        "scene_ir": _scene(),
        "ocr_labels": ["Start", "End"],
        "numbers": [],
        "path_applicable": True,
        "human_accepted": True,
    }
    candidate = prediction or {
        "schema_version": "mmx-eval-prediction-0.1",
        "reconstruction_present": True,
        "diagram_type": "flowchart",
        "generated_scene_ir": _scene(),
        "evidence": [
            {"id": "ocr-a", "kind": "ocr_token", "text": "Start"},
            {"id": "ocr-b", "kind": "ocr_token", "text": "End"},
            {"id": "line-e", "kind": "line_segment"},
        ],
        "numbers": [],
        "published": True,
        "syntax_valid": True,
        "render_valid": True,
        "grade": "A",
        "telemetry": {
            "original_preserved": True,
            "candidate_failure_injected": True,
            "document_failed": False,
            "forbidden_external_actions": 0,
            "duplicate_mermaid_insertions": 0,
            "orphan_processes": 0,
            "candidate_budget_exceeded": False,
        },
    }
    ground_truth_path = root / "ground-truth.json"
    prediction_path = root / "prediction.json"
    ground_truth_hash = _write_json(ground_truth_path, reference)
    prediction_hash = _write_json(prediction_path, candidate)
    manifest = {
        "schema_version": "mmx-eval-manifest-0.1",
        "gate_profile": "mmx-001-v0.3-extended",
        "corpus": {
            "corpus_id": "fixture-corpus",
            "version": "1",
            "license": "CC0-1.0",
            "split": "test",
        },
        "cases": [
            {
                "case_id": "case-1",
                "fixture_group": fixture_group,
                "fixture_tiers": fixture_tiers or ["real_enterprise"],
                "source_origin": "real",
                "scope": scope,
                "languages": ["en"],
                "source": {
                    "path": source.name,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                "ground_truth": {
                    "path": ground_truth_path.name,
                    "sha256": ground_truth_hash,
                },
                "prediction": {
                    "path": prediction_path.name,
                    "sha256": prediction_hash,
                },
            }
        ],
    }
    manifest_path = root / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def _gate(report, name: str):
    return next(gate for gate in report.gates if gate.name == name)


def test_hash_bound_manifest_computes_micro_metrics_and_writes_report(tmp_path):
    loaded = load_evaluation_manifest(_write_manifest(tmp_path))
    report = evaluate_manifest(loaded)

    manifest_digest = hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest()
    assert report.manifest_sha256 == manifest_digest
    assert _gate(report, "published_parse_success").status == "pass"
    assert _gate(report, "candidate_failure_document_failure").status == "unavailable"
    assert _gate(report, "generated_nodes_without_provenance").observed == 0
    assert _gate(report, "structural_precision").observed == 1
    assert _gate(report, "structural_recall").observed == 1
    assert _gate(report, "flowchart_edge_f1").observed == 1
    assert _gate(report, "flowchart_path_f1").observed == 1
    assert _gate(report, "functional_type:flowchart").status == "pass"
    assert report.overall_status == "fail"  # the tiny fixture does not satisfy corpus gates

    output = tmp_path / "report"
    report_path = write_evaluation_report(report, output, evaluation=loaded)
    assert report_path.is_file()
    assert (output / "evaluation-report.md").is_file()
    assert (output / "manifest-snapshot.json").is_file()
    snapshot_digest = hashlib.sha256((output / "manifest-snapshot.json").read_bytes()).hexdigest()
    assert snapshot_digest == loaded.manifest_sha256
    assert (output / "cases/case-1.json").is_file()
    (output / "stale.txt").write_text("old", encoding="utf-8")
    write_evaluation_report(report, output, evaluation=loaded)
    assert not (output / "stale.txt").exists()
    assert not list(tmp_path.glob(".report-previous-*"))


def test_prediction_evidence_preserves_100k_item_and_artifact_contract(tmp_path, monkeypatch):
    schema = evaluation_module.EvaluationPrediction.model_json_schema()
    assert schema["properties"]["evidence"]["maxItems"] == 100_000
    assert "additionalProperties" not in schema["$defs"]["VisualEvidence"]
    assert evaluation_module.MANIFEST_SCHEMA_VERSION == "mmx-eval-manifest-0.1"
    assert evaluation_module.PREDICTION_SCHEMA_VERSION == "mmx-eval-prediction-0.1"

    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["evidence"] = [
        {"id": f"evidence-{index}", "kind": "contour"} for index in range(20_001)
    ]
    prediction["evidence"][0]["legacy_extra"] = {"ignored": [1, 2, 3]}
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)
    monkeypatch.setattr(models_module, "MAX_OBSERVATION_EVIDENCE", 1)
    monkeypatch.setattr(models_module, "MAX_EVIDENCE_INPUT_CHARS", 1)

    loaded = load_evaluation_manifest(manifest_path)

    assert len(loaded.cases[0].prediction.evidence) == 20_001
    assert not hasattr(loaded.cases[0].prediction.evidence[0], "legacy_extra")


@pytest.mark.parametrize(
    ("limit_name", "accepted_refs", "overflow_tail_refs", "error_pattern"),
    [
        (
            "MAX_EVIDENCE_SOURCE_BLOCK_REFS",
            ["shared", "shared"],
            ["shared"],
            "source-block references exceed the aggregate limit",
        ),
        (
            "MAX_EVIDENCE_SOURCE_BLOCK_CHARS",
            ["가나"],
            ["다"],
            "source-block characters exceed the aggregate limit",
        ),
    ],
)
def test_prediction_evidence_aggregate_provenance_exact_and_plus_one_are_atomic(
    tmp_path,
    monkeypatch,
    capsys,
    limit_name,
    accepted_refs,
    overflow_tail_refs,
    error_pattern,
):
    monkeypatch.setattr(models_module, limit_name, 2)
    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["evidence"][0]["source_block_ids"] = accepted_refs
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    loaded = load_evaluation_manifest(manifest_path)
    assert loaded.cases[0].prediction.evidence[0].source_block_ids == accepted_refs

    prediction["evidence"][1]["source_block_ids"] = overflow_tail_refs
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)
    output = tmp_path / "existing-report"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    writer_called = False

    def forbidden_writer(*_args, **_kwargs):
        nonlocal writer_called
        writer_called = True
        raise AssertionError("aggregate-invalid prediction must not reach the report writer")

    original_validate = models_module.VisualEvidence.model_validate

    def reject_overflow_record_construction(cls, value, *args, **kwargs):
        if type(value) is dict and value.get("id") == "ocr-b":
            raise AssertionError("aggregate overflow must precede evidence model construction")
        return original_validate(value, *args, **kwargs)

    monkeypatch.setattr(
        models_module.VisualEvidence,
        "model_validate",
        classmethod(reject_overflow_record_construction),
    )
    monkeypatch.setattr(cli_module, "write_evaluation_report", forbidden_writer)
    with pytest.raises(EvaluationManifestError, match=error_pattern):
        load_evaluation_manifest(manifest_path)
    status = cli_module.main(["evaluate", str(manifest_path), "--output", str(output)])

    assert status == 2
    assert error_pattern in capsys.readouterr().err
    assert not writer_called
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list(output.iterdir()) == [sentinel]


def test_prediction_evidence_malformed_container_is_a_manifest_error(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["evidence"] = {"id": "not-an-array", "kind": "contour"}
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    with pytest.raises(EvaluationManifestError, match="evidence input must be an exact plain list"):
        load_evaluation_manifest(manifest_path)


def test_report_writer_refuses_unowned_or_protected_output(tmp_path):
    loaded = load_evaluation_manifest(_write_manifest(tmp_path))
    report = evaluate_manifest(loaded)
    unowned = tmp_path / "unowned"
    unowned.mkdir()
    sentinel = unowned / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="not owned"):
        write_evaluation_report(report, unowned, evaluation=loaded)
    with pytest.raises(ValueError, match="corpus root"):
        write_evaluation_report(report, tmp_path, evaluation=loaded)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_report_writer_refuses_symlink_output(tmp_path):
    loaded = load_evaluation_manifest(_write_manifest(tmp_path))
    report = evaluate_manifest(loaded)
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "report-link"
    link.symlink_to(target.name)

    with pytest.raises(ValueError, match="symlink"):
        write_evaluation_report(report, link, evaluation=loaded)


def test_manifest_rejects_tampered_prediction(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    (tmp_path / "prediction.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(EvaluationManifestError, match="sha256 mismatch"):
        load_evaluation_manifest(manifest_path)


def test_manifest_rejects_symlink_artifact(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    source = tmp_path / "source.bin"
    real_source = tmp_path / "real-source.bin"
    source.rename(real_source)
    source.symlink_to(real_source.name)

    with pytest.raises(EvaluationManifestError, match="symlink"):
        load_evaluation_manifest(manifest_path)


def test_manifest_rejects_type_stability_and_fixture_group_mismatch(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    ground_truth = json.loads((tmp_path / "ground-truth.json").read_text())
    ground_truth["type_stability"] = "experimental"
    ground_truth_hash = _write_json(tmp_path / "ground-truth.json", ground_truth)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["ground_truth"]["sha256"] = ground_truth_hash
    _write_json(manifest_path, manifest)

    with pytest.raises(EvaluationManifestError, match="requires core type stability"):
        load_evaluation_manifest(manifest_path)

    second_root = tmp_path / "group"
    second_root.mkdir()
    mismatched_group = _write_manifest(second_root, fixture_group="uml")
    with pytest.raises(EvaluationManifestError, match="requires fixture group flowchart"):
        load_evaluation_manifest(mismatched_group)


def test_manifest_rejects_duplicate_source_digest(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    first = manifest["cases"][0]
    source_two = tmp_path / "source-2.bin"
    source_two.write_bytes((tmp_path / "source.bin").read_bytes())
    truth_two = tmp_path / "ground-truth-2.json"
    truth_two.write_bytes((tmp_path / "ground-truth.json").read_bytes())
    prediction_two = tmp_path / "prediction-2.json"
    prediction_two.write_bytes((tmp_path / "prediction.json").read_bytes())
    second = json.loads(json.dumps(first))
    second["case_id"] = "case-2"
    second["source"]["path"] = source_two.name
    second["ground_truth"] = {
        "path": truth_two.name,
        "sha256": hashlib.sha256(truth_two.read_bytes()).hexdigest(),
    }
    second["prediction"] = {
        "path": prediction_two.name,
        "sha256": hashlib.sha256(prediction_two.read_bytes()).hexdigest(),
    }
    manifest["cases"].append(second)
    _write_json(manifest_path, manifest)

    with pytest.raises(EvaluationManifestError, match="duplicate source digest"):
        load_evaluation_manifest(manifest_path)


def test_manifest_rejects_empty_scene_and_incomplete_ocr(tmp_path):
    ground_truth = {
        "schema_version": "mmx-eval-ground-truth-0.1",
        "expected_reconstruction": True,
        "diagram_type": "flowchart",
        "type_stability": "core",
        "scene_ir": {"elements": [], "relations": [], "groups": []},
        "ocr_labels": [],
        "numbers": [],
        "path_applicable": False,
        "path_unavailable_reason": "empty",
    }
    with pytest.raises(EvaluationManifestError, match="non-empty independent scene"):
        load_evaluation_manifest(_write_manifest(tmp_path, ground_truth=ground_truth))

    second_root = tmp_path / "ocr"
    second_root.mkdir()
    incomplete = {
        **ground_truth,
        "scene_ir": _scene(),
        "path_applicable": True,
        "path_unavailable_reason": None,
    }
    with pytest.raises(EvaluationManifestError, match="OCR annotations must cover"):
        load_evaluation_manifest(_write_manifest(second_root, ground_truth=incomplete))


def test_missing_generated_scene_fails_closed(tmp_path):
    prediction = {
        "schema_version": "mmx-eval-prediction-0.1",
        "reconstruction_present": True,
        "diagram_type": "flowchart",
        "generated_scene_ir": None,
        "evidence": [],
        "numbers": [],
        "published": True,
        "syntax_valid": True,
        "render_valid": True,
        "grade": "C",
        "telemetry": {
            "original_preserved": True,
            "candidate_failure_injected": True,
            "document_failed": False,
            "forbidden_external_actions": 0,
            "duplicate_mermaid_insertions": 0,
            "orphan_processes": 0,
            "candidate_budget_exceeded": False,
        },
    }
    report = evaluate_manifest(
        load_evaluation_manifest(_write_manifest(tmp_path, prediction=prediction))
    )

    assert _gate(report, "generated_nodes_without_provenance").status == "unavailable"
    assert _gate(report, "structural_recall").observed == 0
    assert _gate(report, "flowchart_edge_f1").observed == 0
    assert _gate(report, "flowchart_path_f1").observed == 0
    assert report.cases[0].metrics["node_recall"] == 0
    assert report.cases[0].metrics["edge_f1"] == 0
    assert report.cases[0].metrics["path_f1"] == 0


def test_published_validation_failure_is_a_gate_failure_not_manifest_error(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["syntax_valid"] = False
    prediction["render_valid"] = False
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    report = evaluate_manifest(load_evaluation_manifest(manifest_path))

    assert _gate(report, "published_parse_success").status == "fail"
    assert _gate(report, "published_render_success").status == "fail"


def test_fake_provenance_ids_do_not_satisfy_registry_gate(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["evidence"] = [{"id": "unrelated", "kind": "vlm_observation"}]
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    report = evaluate_manifest(load_evaluation_manifest(manifest_path))

    assert _gate(report, "generated_nodes_without_provenance").observed == 1
    assert _gate(report, "generated_nodes_without_provenance").status == "fail"


def test_shared_node_evidence_fails_the_release_provenance_gate(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["generated_scene_ir"]["elements"][1]["evidence_ids"] = ["ocr-a"]
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    report = evaluate_manifest(load_evaluation_manifest(manifest_path))

    assert _gate(report, "generated_nodes_without_provenance").observed == 1
    assert _gate(report, "generated_nodes_without_provenance").status == "fail"


def test_release_provenance_evidence_ids_are_scoped_to_each_case(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    first = manifest["cases"][0]

    source_two = tmp_path / "source-2.bin"
    source_two.write_bytes(b"second-source-image")
    truth_two = tmp_path / "ground-truth-2.json"
    truth_two.write_bytes((tmp_path / "ground-truth.json").read_bytes())
    prediction_two = tmp_path / "prediction-2.json"
    prediction_two.write_bytes((tmp_path / "prediction.json").read_bytes())

    second = json.loads(json.dumps(first))
    second["case_id"] = "case-2"
    second["source"] = {
        "path": source_two.name,
        "sha256": hashlib.sha256(source_two.read_bytes()).hexdigest(),
    }
    second["ground_truth"] = {
        "path": truth_two.name,
        "sha256": hashlib.sha256(truth_two.read_bytes()).hexdigest(),
    }
    second["prediction"] = {
        "path": prediction_two.name,
        "sha256": hashlib.sha256(prediction_two.read_bytes()).hexdigest(),
    }
    manifest["cases"].append(second)
    _write_json(manifest_path, manifest)

    report = evaluate_manifest(load_evaluation_manifest(manifest_path))

    assert _gate(report, "generated_nodes_without_provenance").observed == 0


def test_serializer_scope_does_not_enter_end_to_end_quality_denominators(tmp_path):
    report = evaluate_manifest(
        load_evaluation_manifest(_write_manifest(tmp_path, scope="serializer"))
    )

    assert _gate(report, "structural_recall").status == "unavailable"
    assert _gate(report, "core_type_accuracy").status == "unavailable"
    assert _gate(report, "ocr_label_recall").status == "unavailable"
    assert report.fixture_counts["flowchart"] == 0


def test_same_ids_with_wrong_labels_do_not_align_without_shared_namespace(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["generated_scene_ir"]["elements"][0]["text"] = "Wrong"
    prediction["generated_scene_ir"]["elements"][1]["text"] = "Also wrong"
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    report = evaluate_manifest(load_evaluation_manifest(manifest_path))

    assert _gate(report, "structural_recall").observed == 0
    assert report.cases[0].metrics["node_recall"] == 0


def test_ocr_recall_counts_repeated_tokens(tmp_path):
    ground_truth = {
        "schema_version": "mmx-eval-ground-truth-0.1",
        "expected_reconstruction": True,
        "diagram_type": "flowchart",
        "type_stability": "core",
        "scene_ir": {
            "elements": [
                {"id": "A", "role": "process", "text": "X X X", "bbox": [0, 0, 1, 1]}
            ],
            "relations": [],
            "groups": [],
        },
        "ocr_labels": ["X X X"],
        "numbers": [],
        "path_applicable": False,
        "path_unavailable_reason": "single node",
        "human_accepted": True,
    }
    prediction = {
        "schema_version": "mmx-eval-prediction-0.1",
        "reconstruction_present": True,
        "diagram_type": "flowchart",
        "generated_scene_ir": {
            "elements": [
                {
                    "id": "A",
                    "role": "process",
                    "text": "X",
                    "bbox": [0, 0, 1, 1],
                    "evidence_ids": ["ocr-x"],
                }
            ],
            "relations": [],
            "groups": [],
        },
        "evidence": [{"id": "ocr-x", "kind": "ocr_token", "text": "X"}],
        "numbers": [],
        "published": True,
        "syntax_valid": True,
        "render_valid": True,
        "grade": "A",
        "telemetry": {
            "original_preserved": True,
            "document_failed": False,
            "forbidden_external_actions": 0,
            "duplicate_mermaid_insertions": 0,
            "orphan_processes": 0,
            "candidate_budget_exceeded": False,
        },
    }
    report = evaluate_manifest(
        load_evaluation_manifest(
            _write_manifest(tmp_path, ground_truth=ground_truth, prediction=prediction)
        )
    )

    assert _gate(report, "ocr_label_recall").observed == pytest.approx(1 / 3)


def test_flowchart_edge_f1_is_directional(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    relation = prediction["generated_scene_ir"]["relations"][0]
    relation["source_id"], relation["target_id"] = relation["target_id"], relation["source_id"]
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    report = evaluate_manifest(load_evaluation_manifest(manifest_path))

    assert _gate(report, "flowchart_edge_f1").observed == 0


def test_path_budget_exhaustion_makes_required_metric_unavailable(
    tmp_path, monkeypatch
):
    loaded = load_evaluation_manifest(_write_manifest(tmp_path))
    monkeypatch.setattr(evaluation_module, "MAX_PATH_STATES", 1)

    report = evaluate_manifest(loaded)

    assert _gate(report, "flowchart_path_f1").status == "unavailable"


def test_missing_human_review_label_fails_coverage_instead_of_shrinking_denominator(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    ground_truth = json.loads((tmp_path / "ground-truth.json").read_text())
    ground_truth.pop("human_accepted")
    ground_truth_hash = _write_json(tmp_path / "ground-truth.json", ground_truth)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["ground_truth"]["sha256"] = ground_truth_hash
    _write_json(manifest_path, manifest)

    report = evaluate_manifest(load_evaluation_manifest(manifest_path))

    assert _gate(report, "human_review_coverage").status == "fail"
    assert _gate(report, "human_accept_rate").status == "unavailable"


def test_experimental_output_requires_warning_sidecar_review_and_score(tmp_path):
    ground_truth = {
        "schema_version": "mmx-eval-ground-truth-0.1",
        "expected_reconstruction": True,
        "diagram_type": "c4",
        "type_stability": "experimental",
        "scene_ir": _scene(),
        "ocr_labels": ["Start", "End"],
        "numbers": [],
        "human_accepted": True,
    }
    prediction = {
        "schema_version": "mmx-eval-prediction-0.1",
        "reconstruction_present": True,
        "diagram_type": "c4",
        "generated_scene_ir": _scene(),
        "evidence": [
            {"id": "ocr-a", "kind": "ocr_token", "text": "Start"},
            {"id": "ocr-b", "kind": "ocr_token", "text": "End"},
        ],
        "numbers": [],
        "published": True,
        "syntax_valid": True,
        "render_valid": True,
        "grade": "C",
        "telemetry": {
            "original_preserved": True,
            "document_failed": False,
            "forbidden_external_actions": 0,
            "duplicate_mermaid_insertions": 0,
            "orphan_processes": 0,
            "candidate_budget_exceeded": False,
        },
    }
    report = evaluate_manifest(
        load_evaluation_manifest(
            _write_manifest(
                tmp_path,
                ground_truth=ground_truth,
                prediction=prediction,
                fixture_group="architecture_c4",
            )
        )
    )

    assert _gate(report, "experimental_warning_present").status == "fail"
    assert _gate(report, "experimental_sidecar_present").status == "fail"
    assert _gate(report, "experimental_review_available").status == "fail"
    assert _gate(report, "experimental_hallucination_score_present").status == "fail"


def test_numeric_annotations_are_required_and_decimal_canonical(tmp_path):
    ground_truth = {
        "schema_version": "mmx-eval-ground-truth-0.1",
        "expected_reconstruction": True,
        "diagram_type": "pie",
        "type_stability": "extended",
        "scene_ir": _scene(),
        "ocr_labels": ["Start", "End"],
        "numbers": ["1.0"],
        "numeric_applicable": True,
        "human_accepted": True,
    }
    prediction = {
        "schema_version": "mmx-eval-prediction-0.1",
        "reconstruction_present": True,
        "diagram_type": "pie",
        "generated_scene_ir": _scene(),
        "evidence": [
            {"id": "ocr-a", "kind": "ocr_token", "text": "Start"},
            {"id": "ocr-b", "kind": "ocr_token", "text": "End"},
        ],
        "numbers": ["1e0"],
        "published": True,
        "syntax_valid": True,
        "render_valid": True,
        "grade": "A",
        "telemetry": {
            "original_preserved": True,
            "document_failed": False,
            "forbidden_external_actions": 0,
            "duplicate_mermaid_insertions": 0,
            "orphan_processes": 0,
            "candidate_budget_exceeded": False,
        },
    }
    report = evaluate_manifest(
        load_evaluation_manifest(
            _write_manifest(
                tmp_path,
                ground_truth=ground_truth,
                prediction=prediction,
                fixture_group="data_chart",
            )
        )
    )
    assert _gate(report, "chart_numeric_exact_match").observed == 1

    second_root = tmp_path / "missing"
    second_root.mkdir()
    ground_truth["numbers"] = []
    with pytest.raises(EvaluationManifestError, match="requires annotated numbers"):
        load_evaluation_manifest(
            _write_manifest(
                second_root,
                ground_truth=ground_truth,
                prediction=prediction,
                fixture_group="data_chart",
            )
        )


def test_negative_hallucination_counts_against_structural_precision(tmp_path):
    ground_truth = {
        "schema_version": "mmx-eval-ground-truth-0.1",
        "expected_reconstruction": False,
        "diagram_type": None,
        "type_stability": "negative",
        "scene_ir": None,
        "ocr_labels": [],
        "numbers": [],
    }
    prediction = {
        "schema_version": "mmx-eval-prediction-0.1",
        "reconstruction_present": True,
        "diagram_type": "flowchart",
        "generated_scene_ir": _scene(),
        "evidence": [
            {"id": "ocr-a", "kind": "ocr_token", "text": "Start"},
            {"id": "ocr-b", "kind": "ocr_token", "text": "End"},
            {"id": "line-e", "kind": "line_segment"},
        ],
        "numbers": [],
        "published": False,
        "syntax_valid": True,
        "render_valid": True,
        "grade": "D",
        "telemetry": {
            "original_preserved": True,
            "candidate_failure_injected": False,
            "document_failed": False,
            "forbidden_external_actions": 0,
            "duplicate_mermaid_insertions": 0,
            "orphan_processes": 0,
            "candidate_budget_exceeded": False,
        },
    }
    manifest = _write_manifest(
        tmp_path,
        ground_truth=ground_truth,
        prediction=prediction,
        fixture_group="negative",
        fixture_tiers=["negative"],
        scope="detector",
    )
    report = evaluate_manifest(load_evaluation_manifest(manifest))

    assert _gate(report, "structural_precision").observed == 0
    assert _gate(report, "structural_recall").status == "unavailable"


def test_fault_probe_requires_injected_failure_telemetry(tmp_path):
    manifest_path = _write_manifest(tmp_path, scope="fault_probe")
    prediction = json.loads((tmp_path / "prediction.json").read_text())
    prediction["telemetry"]["candidate_failure_injected"] = False
    prediction_hash = _write_json(tmp_path / "prediction.json", prediction)
    manifest = json.loads(manifest_path.read_text())
    manifest["cases"][0]["prediction"]["sha256"] = prediction_hash
    _write_json(manifest_path, manifest)

    with pytest.raises(EvaluationManifestError, match="fault_probe requires"):
        load_evaluation_manifest(manifest_path)


def test_fault_probe_supplies_candidate_isolation_gate(tmp_path):
    report = evaluate_manifest(
        load_evaluation_manifest(_write_manifest(tmp_path, scope="fault_probe"))
    )

    assert _gate(report, "candidate_failure_document_failure").status == "pass"
