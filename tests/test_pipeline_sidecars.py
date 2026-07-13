from __future__ import annotations

import hashlib
import json

import pytest
from PIL import Image

import marker_mermaid.sidecars as sidecar_module
from marker_mermaid.config import MermaidConfig, PublishPolicy
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.geometry import ContourObservation, GeometryEngine, GeometryObservation
from marker_mermaid.markdown import standalone_document_markdown
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    MermaidCandidate,
    NodeIdMapping,
    ReconstructionResult,
    SceneElement,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.scoring import aggregate_scores, decide_publication
from marker_mermaid.sidecars import SidecarStore
from marker_mermaid.validation import CandidateValidator


def observation():
    return EngineObservation(
        prediction=DiagramTypePrediction(
            candidates=["flowchart", "architecture"], scores=[0.9, 0.1]
        ),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="A",
                    role="process",
                    text="Start",
                    bbox=(0, 0, 10, 10),
                    evidence_ids=["ocr-1"],
                ),
                SceneElement(
                    id="B", role="process", text="End", bbox=(20, 0, 30, 10), evidence_ids=["vlm-1"]
                ),
            ],
            relations=[
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type="arrow",
                    evidence_ids=["vlm-1"],
                )
            ],
            reading_direction="LR",
            diagram_type_candidates=["flowchart"],
        ),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "title": "Process",
                    "nodes": [{"id": "A", "label": "Start"}, {"id": "B", "label": "End"}],
                    "edges": [{"source": "A", "target": "B"}],
                },
            )
        ],
        evidence=[
            VisualEvidence(id="ocr-1", kind="ocr_token", text="Start", bbox=(0, 0, 10, 10)),
            VisualEvidence(id="vlm-1", kind="vlm_observation", score=0.9),
        ],
    )


def test_pipeline_selects_valid_candidate_and_respects_budget(fake_runtime):
    config = MermaidConfig(candidate_count=1)
    pipeline = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    )
    result = pipeline.reconstruct(
        "page-1-figure-1",
        "source.png",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )
    assert result.selected is not None
    assert result.publish
    assert len(fake_runtime.calls) == 1
    assert result.selected.generation_method == "typed_ir"
    assert result.selected.generated_scene_ir is not None
    assert result.selected.generated_scene_ir is not result.selected.scene_ir
    assert result.selected.scores["edge_agreement"] == 1
    assert result.selected.scores["arrow_agreement"] == 1
    assert result.selected.scores["path_consistency"] == 1
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert "layout_similarity" not in result.selected.scores
    assert result.selected.typed_ir["acc_title"] == "Process"
    assert "Start" in result.selected.typed_ir["acc_description"]


def test_pipeline_ocr_recall_uses_generated_labels_and_spatial_occurrence_max(fake_runtime):
    repeated = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="A",
                    role="node",
                    text="X",
                    bbox=(0, 0, 10, 10),
                    evidence_ids=["ocr-1"],
                )
            ]
        ),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "nodes": [{"id": "A", "label": "X", "evidence_ids": ["ocr-1"]}],
                    "edges": [],
                },
            )
        ],
        evidence=[
            VisualEvidence(id="ocr-1", kind="ocr_token", text="X", bbox=(0, 0, 1, 1)),
            VisualEvidence(id="ocr-2", kind="ocr_token", text="X", bbox=(2, 0, 3, 1)),
            VisualEvidence(id="ocr-3", kind="ocr_token", text="X", bbox=(4, 0, 5, 1)),
            VisualEvidence(
                id="vector-duplicate",
                kind="vector_text",
                text="X",
                bbox=(0, 0, 1, 1),
            ),
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(repeated)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (40, 20), "white"),
        ocr_texts=["X X"],
    )

    assert result.selected is not None
    assert result.selected.scores["ocr_recall"] == pytest.approx(1 / 3)


def test_pipeline_ocr_recall_includes_generated_relation_and_group_labels(fake_runtime):
    labeled_structure = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(id="A", role="node", text="Start", bbox=(0, 0, 1, 1)),
                SceneElement(id="B", role="node", text="End", bbox=(2, 0, 3, 1)),
            ],
            relations=[
                SceneRelation(
                    id="edge",
                    source_id="A",
                    target_id="B",
                    relation_type="arrow",
                    label="Approved",
                )
            ],
        ),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "nodes": [
                        {"id": "A", "label": "Start"},
                        {"id": "B", "label": "End"},
                    ],
                    "edges": [{"source": "A", "target": "B", "label": "Approved"}],
                    "groups": [{"id": "G", "label": "Payment lane", "member_ids": ["A", "B"]}],
                },
            )
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(labeled_structure)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (40, 20), "white"),
        ocr_texts=["Start End Approved Payment lane"],
    )

    assert result.selected is not None
    assert result.selected.scores["ocr_recall"] == 1


def test_pipeline_deduplicates_bboxless_evidence_occurrences(fake_runtime):
    repeated = observation()
    repeated.evidence = [
        VisualEvidence(id="ocr-a", kind="ocr_token", text="Start"),
        VisualEvidence(id="ocr-b", kind="vector_text", text="Start"),
    ]
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(repeated)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (40, 20), "white"),
    )

    assert result.selected is not None
    assert result.selected.scores["ocr_recall"] == 1


def test_pipeline_marks_oversized_ocr_reference_scoring_unavailable(fake_runtime, monkeypatch):
    monkeypatch.setattr("marker_mermaid.pipeline._MAX_OCR_REFERENCE_CHARS", 3)
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (40, 20), "white"),
        ocr_texts=["Start"],
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert "ocr_recall" not in result.selected.scores
    assert any("semantic scoring budget" in warning for warning in result.selected.warnings)
    assert result.review_required


def test_pipeline_marks_oversized_generated_semantic_labels_unavailable(fake_runtime, monkeypatch):
    oversized = observation()
    oversized.typed_candidates[0].ir["nodes"][0]["label"] = "Very long generated label"
    monkeypatch.setattr("marker_mermaid.pipeline._MAX_OCR_REFERENCE_CHARS", 10)
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(oversized)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (40, 20), "white"),
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert "ocr_recall" not in result.selected.scores
    assert any(
        "generated semantic labels exceed" in warning for warning in result.selected.warnings
    )
    assert result.review_required


def test_pipeline_isolates_generated_scene_conversion_failure(fake_runtime, monkeypatch):
    def fail_scene_conversion(diagram_type, ir):
        raise ValueError("oversized generated scene")

    monkeypatch.setattr("marker_mermaid.pipeline.typed_ir_to_scene", fail_scene_conversion)
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (40, 20), "white"),
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert any("scene conversion was isolated" in warning for warning in result.selected.warnings)
    assert result.review_required


def test_pipeline_isolates_generated_semantic_text_projection_failure(fake_runtime, monkeypatch):
    def fail_text_projection(diagram_type, ir, scene):
        yield from ()
        raise ValueError("invalid semantic projection")

    monkeypatch.setattr("marker_mermaid.pipeline.typed_ir_semantic_texts", fail_text_projection)
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (40, 20), "white"),
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert any(
        "semantic text projection was isolated" in warning for warning in result.selected.warnings
    )
    assert result.review_required


@pytest.mark.parametrize(
    ("policy", "sparse_semantic", "rich_semantic"),
    [
        (
            PublishPolicy.BEST_EFFORT_VALIDATED,
            {"visual_entailment_precision": 0.8, "type_fitness": 0.0},
            {
                "ocr_recall": 0.45,
                "visual_entailment_precision": 0.8,
                "edge_agreement": 0.45,
                "arrow_agreement": 0.45,
                "layout_similarity": 0.45,
                "type_fitness": 0.45,
                "path_consistency": 0.45,
                "numeric_consistency": 0.45,
            },
        ),
        (
            PublishPolicy.STRICT_VALIDATED,
            {"type_fitness": 0.69},
            {
                "ocr_recall": 0.72,
                "visual_entailment_precision": 0.8,
                "edge_agreement": 0.72,
                "arrow_agreement": 0.72,
                "layout_similarity": 0.72,
                "type_fitness": 0.72,
                "path_consistency": 0.72,
                "numeric_consistency": 0.72,
            },
        ),
    ],
)
def test_automatic_policy_selects_publishable_candidate_before_higher_aggregate(
    fake_runtime, policy, sparse_semantic, rich_semantic
):
    config = MermaidConfig(publish_policy=policy)
    sparse_scores = {"syntax": 1.0, "render": 1.0, **sparse_semantic}
    rich_scores = {"syntax": 1.0, "render": 1.0, **rich_semantic}
    sparse = MermaidCandidate(
        candidate_id="sparse-higher-total",
        generation_method="typed_ir",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=True,
        scores=sparse_scores,
        aggregate_score=aggregate_scores(sparse_scores, config),
    )
    rich = MermaidCandidate(
        candidate_id="rich-publishable",
        generation_method="direct_mermaid",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=True,
        scores=rich_scores,
        aggregate_score=aggregate_scores(rich_scores, config),
    )

    assert sparse.aggregate_score > rich.aggregate_score
    assert not decide_publication(sparse, config).publish
    assert decide_publication(rich, config).publish
    pipeline = ReconstructionPipeline(
        config,
        [],
        CandidateValidator(fake_runtime, config.security_profile),
    )
    assert pipeline._select([sparse, rich]) is rich


@pytest.mark.parametrize("policy", [PublishPolicy.REVIEW_REQUIRED, PublishPolicy.SIDECAR_ONLY])
def test_nonautomatic_policy_preserves_aggregate_candidate_order(fake_runtime, policy):
    config = MermaidConfig(publish_policy=policy)
    high = MermaidCandidate(
        candidate_id="higher-total",
        generation_method="direct_mermaid",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=True,
        scores={"syntax": 1.0, "render": 1.0, "type_fitness": 0.9},
        aggregate_score=0.9,
    )
    low = MermaidCandidate(
        candidate_id="lower-total",
        generation_method="typed_ir",
        diagram_type="flowchart",
        syntax_valid=True,
        render_valid=True,
        scores={"syntax": 1.0, "render": 1.0, "type_fitness": 0.8},
        aggregate_score=0.8,
    )
    pipeline = ReconstructionPipeline(
        config,
        [],
        CandidateValidator(fake_runtime, config.security_profile),
    )

    assert pipeline._select([low, high]) is high


def test_sidecar_only_reconstruction_succeeds_without_requesting_review(fake_runtime):
    config = MermaidConfig(publish_policy=PublishPolicy.SIDECAR_ONLY)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "sidecar-source",
        "source.png",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )

    assert result.selected is not None
    assert not result.publish
    assert not result.review_required
    assert result.status == "success"


@pytest.mark.parametrize(("scores", "expected_id"), [((0.9, 0.8), "first"), ((0.2, 0.1), "first")])
def test_same_publication_class_keeps_aggregate_order(fake_runtime, scores, expected_id):
    config = MermaidConfig()
    candidates = [
        MermaidCandidate(
            candidate_id=candidate_id,
            generation_method="typed_ir",
            diagram_type="flowchart",
            syntax_valid=True,
            render_valid=True,
            scores={"syntax": 1.0, "render": 1.0, "type_fitness": score},
            aggregate_score=score,
        )
        for candidate_id, score in zip(("first", "second"), scores, strict=True)
    ]
    pipeline = ReconstructionPipeline(
        config,
        [],
        CandidateValidator(fake_runtime, config.security_profile),
    )

    assert pipeline._select(candidates).candidate_id == expected_id


def test_generated_node_provenance_gate_holds_unattributed_typed_nodes(fake_runtime):
    source = observation()
    source.typed_candidates[0].ir["nodes"].append({"id": "C", "label": "Invented"})
    source.typed_candidates[0].ir["edges"].append({"source": "B", "target": "C"})
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(source)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.scores["visual_entailment_precision"] == pytest.approx(2 / 3)
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert result.review_required
    assert any("provenance gate" in warning for warning in result.selected.warnings)


def test_attributed_timeline_typed_candidate_can_pass_extended_provenance_gate():
    class TimelineRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="timeline",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    timeline_observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["timeline"], scores=[0.9]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="timeline",
                ir={
                    "events": [
                        {
                            "id": "launch",
                            "time": "Q1",
                            "events": ["Launch", "Beta"],
                            "evidence_ids": ["ocr-launch"],
                        }
                    ]
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="ocr-launch",
                kind="ocr_token",
                text="Q1 Launch Beta",
                bbox=(0, 0, 20, 10),
            )
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(timeline_observation)],
        CandidateValidator(TimelineRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.scores["ocr_recall"] == 1
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert result.selected.aggregate_score is not None
    assert result.publish


def test_c4_pipeline_scores_only_architecture_fallback_visible_labels():
    class ArchitectureRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="architecture",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    ir = {
        "level": "container",
        "elements": [
            {"id": "user", "label": "User", "kind": "person", "evidence_ids": ["ocr"]},
            {
                "id": "api",
                "name": "Payment API",
                "kind": "container",
                "technology": "Hidden runtime",
                "boundary": "payments",
                "evidence_ids": ["ocr"],
            },
            {
                "id": "worker",
                "label": "Worker",
                "boundary": "payments",
                "evidence_ids": ["ocr"],
            },
        ],
        "boundaries": [{"id": "payments", "label": "Payments"}],
        "relations": [{"source": "user", "target": "api", "label": "Hidden relation"}],
    }
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["c4"], scores=[0.9]),
        typed_candidates=[TypedIRCandidate(diagram_type="c4", ir=ir)],
        evidence=[
            VisualEvidence(
                id="ocr",
                kind="ocr_token",
                text="Payments User Payment API Worker",
                bbox=(0, 0, 50, 10),
            )
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(ArchitectureRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "architecture"
    assert result.selected.scores["ocr_recall"] == 1
    assert result.selected.scores["visual_entailment_precision"] == 1


def test_eventmodeling_pipeline_scores_lane_typed_frame_and_relation_labels():
    class FlowchartRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart-v2",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    ir = {
        "lanes": [
            {
                "id": "customer",
                "label": "Customer lane",
                "frames": [
                    {
                        "id": "open",
                        "type": "ui",
                        "time": "T0",
                        "label": "Open checkout",
                        "evidence_ids": ["ocr"],
                    }
                ],
            },
            {
                "id": "operations",
                "frames": [
                    {
                        "id": "placed",
                        "label": "Order placed",
                        "evidence_ids": ["ocr"],
                    }
                ],
            },
        ],
        "relations": [{"source": "open", "target": "placed", "label": "continue"}],
    }
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["eventmodeling"], scores=[0.9]),
        typed_candidates=[TypedIRCandidate(diagram_type="eventmodeling", ir=ir)],
        evidence=[
            VisualEvidence(
                id="ocr",
                kind="ocr_token",
                text=("Customer lane T0 ui Open checkout operations unknown Order placed continue"),
                bbox=(0, 0, 50, 10),
            )
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(FlowchartRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.scores["ocr_recall"] == 1


@pytest.mark.parametrize(
    ("diagram_type", "runtime_type", "emitted_type", "ir", "visible_text"),
    [
        (
            "wardley",
            "wardley",
            "wardley",
            {
                "title": "Payment value chain",
                "components": [
                    {
                        "id": "customer",
                        "label": "Customer",
                        "x": 0.9,
                        "y": 0.8,
                        "evidence_ids": ["ocr"],
                    },
                    {
                        "id": "payment_api",
                        "text": "Hidden component text",
                        "x": 0.5,
                        "y": 0.4,
                        "evidence_ids": ["ocr"],
                    },
                ],
                "links": [{"source": "customer", "target": "payment_api", "label": "requests"}],
            },
            "Payment value chain Customer payment_api requests",
        ),
        (
            "zenuml",
            "sequence",
            "sequence",
            {
                "participants": [
                    {"id": "InternalUser", "label": "Customer", "evidence_ids": ["ocr"]},
                    {
                        "id": "PaymentAPI",
                        "text": "Hidden participant text",
                        "evidence_ids": ["ocr"],
                    },
                ],
                "messages": [
                    {
                        "source": "InternalUser",
                        "target": "PaymentAPI",
                        "label": "Authorize payment",
                    }
                ],
            },
            "Customer PaymentAPI Authorize payment",
        ),
    ],
)
def test_experimental_typed_pipeline_scores_emitted_visible_text(
    diagram_type, runtime_type, emitted_type, ir, visible_text
):
    class TypedRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type=runtime_type,
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=[diagram_type], scores=[0.9]),
        typed_candidates=[TypedIRCandidate(diagram_type=diagram_type, ir=ir)],
        evidence=[
            VisualEvidence(
                id="ocr",
                kind="ocr_token",
                text=visible_text,
                bbox=(0, 0, 50, 10),
            )
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(TypedRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == emitted_type
    assert result.selected.scores["ocr_recall"] == 1


def test_direct_structural_candidate_without_attribution_requires_review(fake_runtime):
    class DirectOnlyEngine:
        name = "direct-only"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
                direct_candidates=[
                    DirectMermaidCandidate(
                        diagram_type="flowchart",
                        code='flowchart LR\n    A["Start"] --> B["End"]\n',
                    )
                ],
            )

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [DirectOnlyEngine()],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("attribution is unavailable" in warning for warning in result.selected.warnings)


def test_candidate_budget_is_shared_fairly_across_engines(fake_runtime):
    class DirectEngine:
        name = "direct"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.8]),
                direct_candidates=[
                    DirectMermaidCandidate(
                        diagram_type="flowchart",
                        code='flowchart LR\n    X["One"] --> Y["Two"]\n',
                    )
                ],
            )

    config = MermaidConfig(candidate_count=2)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation()), DirectEngine()],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    methods = {
        result.selected.generation_method,
        *(item.generation_method for item in result.alternatives),
    }
    assert methods == {"typed_ir", "direct_mermaid"}
    assert result.selected.generation_engine == "deterministic_fusion"
    assert len(fake_runtime.calls) == 3
    assert "accTitle:" in fake_runtime.calls[-1]
    direct = next(
        item
        for item in [result.selected, *result.alternatives]
        if item.generation_method == "direct_mermaid"
    )
    assert "accDescr:" in direct.mermaid_code


def test_direct_accessibility_augmentation_is_discarded_on_runtime_type_drift():
    class DirectEngine:
        name = "direct"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
                direct_candidates=[
                    DirectMermaidCandidate(
                        diagram_type="flowchart",
                        code='flowchart LR\n    A["One"] --> B["Two"]\n',
                    )
                ],
            )

    class TypeDriftRuntime:
        def validate_and_render(self, code, timeout_seconds):
            diagram_type = "sequence" if "accTitle:" in code else "flowchart-v2"
            return RuntimeResult(
                True,
                True,
                diagram_type=diagram_type,
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [DirectEngine()],
        CandidateValidator(TypeDriftRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert "accTitle:" not in result.selected.mermaid_code
    assert any("augmentation was discarded" in warning for warning in result.selected.warnings)


def test_pipeline_preserves_requested_type_when_serializer_falls_back(fake_runtime):
    fallback_observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["bpmn"], scores=[0.9]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="bpmn",
                ir={
                    "lanes": [
                        {
                            "id": "customer",
                            "label": "Customer",
                            "nodes": [{"id": "pay", "label": "Pay"}],
                        }
                    ]
                },
            )
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(fallback_observation)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.diagram_type == "bpmn"
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.fallback_chain == ["bpmn", "swimlane", "flowchart"]
    assert result.selected.serialization_stability == "extended"


def test_direct_mermaid_is_reclassified_when_runtime_detects_another_type(fake_runtime):
    class MislabeledDirectEngine:
        name = "mislabeled"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["architecture"], scores=[0.9]),
                direct_candidates=[
                    DirectMermaidCandidate(
                        diagram_type="architecture",
                        code='flowchart LR\n    A["API"] --> B["DB"]\n',
                    )
                ],
            )

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [MislabeledDirectEngine()],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.diagram_type == "architecture"
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.fallback_chain == ["architecture", "flowchart"]
    assert result.selected.scores["type_fitness"] == 0
    assert any("emitted type mismatch" in item for item in result.selected.warnings)


def test_typed_serializer_runtime_type_mismatch_fails_the_render_gate():
    class WrongTypeRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="sequence",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(WrongTypeRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is None
    assert result.status == "failed"
    assert not result.alternatives[0].render_valid
    assert result.alternatives[0].scores["render"] == 0


def test_native_runtime_rejection_retries_declared_portable_fallback():
    class NativeRejectingRuntime:
        def __init__(self):
            self.calls = []

        def validate_and_render(self, code, timeout_seconds):
            self.calls.append(code)
            if code.startswith("packet-beta"):
                return RuntimeResult(False, False, error="native parser rejected packet")
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    packet_observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["packet"], scores=[0.9]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="packet",
                ir={
                    "fields": [
                        {"id": "version", "start": 0, "end": 3, "label": "Version"},
                        {"id": "ihl", "start": 4, "end": 7, "label": "IHL"},
                    ]
                },
            )
        ],
    )
    runtime = NativeRejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(packet_observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        ocr_texts=["Version 0 3 IHL 4 7"],
    )

    assert result.selected is not None
    assert result.selected.diagram_type == "packet"
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.fallback_chain == ["packet", "flowchart"]
    assert result.selected.render_valid
    assert len(runtime.calls) == 2
    assert result.selected.repair_history[-1].operation == "runtime_portable_fallback"


def test_nested_organization_runtime_rejection_retries_flowchart_fallback():
    class TreeViewRejectingRuntime:
        def __init__(self):
            self.calls = []

        def validate_and_render(self, code, timeout_seconds):
            self.calls.append(code)
            if code.startswith("treeView-beta"):
                return RuntimeResult(
                    True,
                    False,
                    diagram_type="treeview",
                    error="native parser rejected TreeView",
                )
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart-v2",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    organization_ir = {
        "root": {
            "id": "ceo",
            "label": "CEO",
            "evidence_ids": ["ocr"],
            "children": [
                {
                    "id": "cto",
                    "label": "CTO",
                    "evidence_ids": ["ocr"],
                }
            ],
        }
    }
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["organization"], scores=[0.9]),
        typed_candidates=[TypedIRCandidate(diagram_type="organization", ir=organization_ir)],
        evidence=[
            VisualEvidence(
                id="ocr",
                kind="ocr_token",
                text="CEO CTO",
                bbox=(0, 0, 50, 10),
            )
        ],
    )
    runtime = TreeViewRejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.diagram_type == "organization"
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.fallback_chain == ["organization", "treeview", "flowchart"]
    assert result.selected.render_valid
    assert result.selected.scores["ocr_recall"] == 1
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert len(runtime.calls) == 2
    assert runtime.calls[0].startswith("treeView-beta")
    assert runtime.calls[1].startswith("flowchart LR")
    assert result.selected.repair_history[-1].operation == "runtime_portable_fallback"


_ARCHITECTURE_RUNTIME_CASES = {
    "architecture": {
        "ir": {
            "services": [
                {"id": "api", "label": "API", "evidence_ids": ["ocr"]},
                {"id": "db", "label": "DB", "evidence_ids": ["ocr"]},
            ],
            "edges": [{"source": "api", "target": "db"}],
        },
        "ocr": "API DB",
        "chain": ["architecture", "flowchart"],
        "stability": "extended",
    },
    "c4": {
        "ir": {
            "elements": [
                {
                    "id": "api",
                    "kind": "container",
                    "label": "API",
                    "boundary": "payments",
                    "evidence_ids": ["ocr"],
                },
                {
                    "id": "db",
                    "kind": "container_database",
                    "label": "DB",
                    "boundary": "payments",
                    "evidence_ids": ["ocr"],
                },
            ],
            "boundaries": [{"id": "payments", "type": "system", "label": "Payments"}],
            "relations": [{"source": "api", "target": "db"}],
        },
        "ocr": "Payments API DB",
        "chain": ["c4", "architecture", "flowchart"],
        "stability": "experimental",
    },
    "deployment": {
        "ir": {
            "nodes": [{"id": "app", "label": "App", "evidence_ids": ["ocr"]}],
            "artifacts": [{"id": "image", "label": "Image", "evidence_ids": ["ocr"]}],
            "links": [{"source": "app", "target": "image"}],
        },
        "ocr": "App Image",
        "chain": ["deployment", "architecture", "flowchart"],
        "stability": "extended",
    },
    "component": {
        "ir": {
            "components": [{"id": "web", "label": "Web", "evidence_ids": ["ocr"]}],
            "interfaces": [{"id": "auth", "label": "Auth", "evidence_ids": ["ocr"]}],
            "dependencies": [{"source": "web", "target": "auth"}],
        },
        "ocr": "Web Auth",
        "chain": ["component", "architecture", "flowchart"],
        "stability": "extended",
    },
}


class _ArchitectureRejectingRuntime:
    def __init__(self, *, terminal_type="flowchart-v2", fallback_error=None):
        self.terminal_type = terminal_type
        self.fallback_error = fallback_error
        self.calls = []

    def validate_and_render(self, code, timeout_seconds):
        self.calls.append(code)
        if code.startswith("architecture-beta"):
            return RuntimeResult(
                True,
                False,
                diagram_type="architecture",
                error="native parser rejected architecture-beta",
            )
        if self.fallback_error is not None:
            raise self.fallback_error
        return RuntimeResult(
            True,
            True,
            diagram_type=self.terminal_type,
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
        )

    def close(self):
        pass


def _architecture_runtime_observation(diagram_type):
    case = _ARCHITECTURE_RUNTIME_CASES[diagram_type]
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=[diagram_type], scores=[0.9]),
        typed_candidates=[TypedIRCandidate(diagram_type=diagram_type, ir=case["ir"])],
        evidence=[
            VisualEvidence(
                id="ocr",
                kind="ocr_token",
                text=case["ocr"],
                bbox=(0, 0, 90, 10),
            )
        ],
    )


@pytest.mark.parametrize("diagram_type", list(_ARCHITECTURE_RUNTIME_CASES))
def test_architecture_family_runtime_rejection_retries_flowchart_in_same_candidate_slot(
    diagram_type,
):
    case = _ARCHITECTURE_RUNTIME_CASES[diagram_type]
    runtime = _ArchitectureRejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(_architecture_runtime_observation(diagram_type))],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        ocr_texts=[case["ocr"]],
    )

    assert result.selected is not None
    assert result.alternatives == []
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.generation_method == "typed_ir"
    assert result.selected.generation_engine == "json_fixture"
    assert result.selected.diagram_type == diagram_type
    assert result.selected.typed_ir is not None
    assert {key: result.selected.typed_ir[key] for key in case["ir"]} == case["ir"]
    assert set(result.selected.typed_ir) == {
        *case["ir"],
        "acc_title",
        "acc_description",
    }
    assert result.selected.typed_ir["acc_title"]
    assert result.selected.typed_ir["acc_description"]
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.runtime_diagram_type == "flowchart-v2"
    assert result.selected.fallback_chain == case["chain"]
    assert result.selected.serialization_stability == case["stability"]
    assert result.selected.render_valid
    assert result.selected.scores["type_fitness"] == 0.9
    assert result.selected.scores["ocr_recall"] == 1
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert len(runtime.calls) == 2
    assert runtime.calls[0].startswith("architecture-beta")
    assert runtime.calls[1].startswith("flowchart LR")
    repair = result.selected.repair_history[-1]
    assert repair.iteration == 0
    assert repair.operation == "runtime_portable_fallback"
    assert repair.accepted
    assert repair.details == {
        "requested_type": diagram_type,
        "rejected_emitted_type": "architecture",
        "emitted_type": "flowchart",
        "fallback_chain": case["chain"],
        "stage": "validation",
    }


def test_runtime_fallback_validator_exception_is_isolated_to_the_candidate():
    runtime = _ArchitectureRejectingRuntime(
        fallback_error=RuntimeError("fallback validator exploded")
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(_architecture_runtime_observation("architecture"))],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is None
    assert result.status == "failed"
    assert len(result.alternatives) == 1
    candidate = result.alternatives[0]
    assert not candidate.render_valid
    assert len(runtime.calls) == 2
    assert any(
        failure.stage == "runtime_fallback_validation"
        and failure.engine == "json_fixture"
        and failure.error_type == "RuntimeError"
        and failure.message == "fallback validator exploded"
        for failure in result.failures
    )
    assert any("fallback validator exploded" in warning for warning in candidate.warnings)
    assert candidate.emitted_diagram_type == "architecture"
    assert candidate.fallback_chain == ["architecture"]
    assert candidate.mermaid_code == runtime.calls[0]
    repair = candidate.repair_history[-1]
    assert repair.iteration == 0
    assert repair.operation == "runtime_portable_fallback"
    assert not repair.accepted
    assert repair.details == {
        "requested_type": "architecture",
        "rejected_emitted_type": "architecture",
        "emitted_type": "flowchart",
        "fallback_chain": ["architecture", "flowchart"],
        "stage": "validation",
        "error_type": "RuntimeError",
        "error": "fallback validator exploded",
    }


@pytest.mark.parametrize("terminal_type", ["sequence", None])
def test_runtime_fallback_rejects_wrong_or_missing_terminal_runtime_type(terminal_type):
    runtime = _ArchitectureRejectingRuntime(terminal_type=terminal_type)
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(_architecture_runtime_observation("architecture"))],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is None
    assert result.status == "failed"
    assert len(result.alternatives) == 1
    candidate = result.alternatives[0]
    assert not candidate.render_valid
    assert len(runtime.calls) == 2
    assert candidate.emitted_diagram_type == "architecture"
    assert candidate.runtime_diagram_type == "architecture"
    assert candidate.fallback_chain == ["architecture"]
    assert candidate.serialization_stability == "extended"
    assert candidate.mermaid_code == runtime.calls[0]
    assert any(
        "fallback" in warning.lower() and "type" in warning.lower()
        for warning in candidate.warnings
    )
    repair = candidate.repair_history[-1]
    assert repair.iteration == 0
    assert repair.operation == "runtime_portable_fallback"
    assert not repair.accepted
    assert repair.details == {
        "requested_type": "architecture",
        "rejected_emitted_type": "architecture",
        "emitted_type": "flowchart",
        "fallback_chain": ["architecture", "flowchart"],
        "stage": "validation",
        "syntax_valid": True,
        "render_valid": True,
        "runtime_diagram_type": terminal_type,
        "error": None,
    }


def test_runtime_fallback_does_not_revalidate_an_identical_portable_candidate():
    class RejectingRuntime:
        def __init__(self):
            self.calls = []

        def validate_and_render(self, code, timeout_seconds):
            self.calls.append(code)
            return RuntimeResult(False, False, error="portable renderer rejected candidate")

        def close(self):
            pass

    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["treeview"], scores=[0.9]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="treeview",
                ir={
                    "root": {
                        "id": "root",
                        "label": "Root",
                        "children": [{"id": "child", "label": 'Child "quoted"'}],
                    }
                },
            )
        ],
    )
    runtime = RejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is None
    assert len(result.alternatives) == 1
    assert result.alternatives[0].emitted_diagram_type == "flowchart"
    assert result.alternatives[0].fallback_chain == ["treeview", "flowchart"]
    assert len(runtime.calls) == 1


def test_numeric_diagram_without_source_numeric_evidence_requires_review():
    class PieEngine:
        name = "pie"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["pie"], scores=[0.9]),
                direct_candidates=[
                    DirectMermaidCandidate(
                        diagram_type="pie",
                        code='pie\n    "Approved" : 20\n',
                    )
                ],
            )

    class PieRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="pie",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [PieEngine()],
        CandidateValidator(PieRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert result.status == "review_required"
    assert any("lacks OCR/vector numeric evidence" in item for item in result.selected.warnings)

    class VectorNumberEngine:
        name = "vector-number"
        fusion_source = "vector"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["pie"], scores=[0.7]),
                evidence=[
                    VisualEvidence(
                        id="vector-number-1",
                        kind="vector_text",
                        text="20",
                        bbox=(0, 0, 10, 10),
                    )
                ],
            )

    supported = ReconstructionPipeline(
        config,
        [VectorNumberEngine(), PieEngine()],
        CandidateValidator(PieRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))

    assert supported.selected is not None
    assert supported.selected.scores["numeric_consistency"] == 1
    assert supported.selected.aggregate_score is not None


def test_numeric_diagram_with_conflicting_source_values_requires_review(fake_runtime):
    class PieEngine:
        name = "pie"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["pie"], scores=[0.9]),
                direct_candidates=[
                    DirectMermaidCandidate(
                        diagram_type="pie",
                        code='pie\n    "Approved" : 20\n',
                    )
                ],
            )

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [PieEngine()],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        ocr_texts=["Approved 99"],
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("numeric consistency" in warning for warning in result.selected.warnings)


def test_geometry_evidence_is_available_to_later_engines(fake_runtime):
    class EvidenceEngine:
        name = "evidence"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["unknown"], scores=[1.0]),
                evidence=[
                    VisualEvidence(id="contour-1", kind="contour", bbox=(0, 0, 5, 5)),
                    VisualEvidence(
                        id="vector-text-1",
                        kind="vector_text",
                        bbox=(10, 10, 30, 20),
                        text="API",
                    ),
                ],
            )

    class CapturingEngine:
        name = "capturing"

        def observe(self, context):
            assert [item.id for item in context.evidence] == ["contour-1", "vector-text-1"]
            assert "vector_overlay" in context.views
            return observation()

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [EvidenceEngine(), CapturingEngine()],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.selected is not None


def test_unlabeled_geometry_only_candidate_requires_review(fake_runtime):
    geometry = GeometryObservation(
        canvas_size=(100, 50),
        contours=(ContourObservation(bbox=(10, 10, 40, 40), confidence=0.9),),
    )
    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [GeometryEngine(detector=lambda image: geometry)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.selected is not None
    assert result.selected.generation_engine == "geometry"
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert result.status == "review_required"


def test_engine_failure_is_isolated(fake_runtime):
    class Broken:
        name = "broken"

        def observe(self, context):
            raise RuntimeError("offline")

    config = MermaidConfig()
    pipeline = ReconstructionPipeline(
        config,
        [Broken(), JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    )
    result = pipeline.reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.selected is not None
    assert result.failures[0].engine == "broken"


def test_validator_failure_is_isolated():
    class ExplodingRuntime:
        def validate_and_render(self, code, timeout_seconds):
            raise RuntimeError("browser unavailable")

        def close(self):
            pass

    config = MermaidConfig()
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(ExplodingRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.status == "failed"
    assert result.failures[0].stage == "validation"


def test_sidecar_tree_and_markdown(tmp_path, fake_runtime):
    config = MermaidConfig(candidate_count=2)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "page-1-figure-1",
        "source.png",
        Image.new("RGB", (100, 60), "white"),
        source_block_ids=["/page/1/Figure/1"],
        source_kind="panel",
        page_ids=[1],
        anchor_block_id="/page/1/Figure/1",
        source_mapping={
            "source": {"source_id": "page-1-figure-1"},
            "assembly": {"canvas_size": [100, 60]},
        },
        ocr_texts=["Start End"],
    )
    relative = SidecarStore(tmp_path).write(result)
    bundle = tmp_path / relative
    assert (bundle / "final.mmd").is_file()
    assert (bundle / "final.svg").is_file()
    assert (bundle / "provenance.json").is_file()
    assert (bundle / "source-map.json").is_file()
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["schema_version"] == "mmx-sidecar-0.5"
    assert manifest["source_kind"] == "panel"
    assert manifest["source_block_ids"] == ["/page/1/Figure/1"]
    assert manifest["page_ids"] == [1]
    assert manifest["anchor_block_id"] == "/page/1/Figure/1"
    assert manifest["requested_diagram_type"] == "flowchart"
    assert manifest["emitted_diagram_type"] == "flowchart"
    assert manifest["fallback_chain"] == ["flowchart"]
    source_scene = json.loads((bundle / "scene-ir.json").read_text())
    generated_scene = json.loads((bundle / "generated-scene-ir.json").read_text())
    assert source_scene == result.selected.scene_ir.model_dump(mode="json")
    assert generated_scene == result.selected.generated_scene_ir.model_dump(mode="json")
    assert "generated-scene-ir.json" in manifest["files"]
    assert json.loads((bundle / "source-map.json").read_text())["assembly"]["canvas_size"] == [
        100,
        60,
    ]
    markdown = standalone_document_markdown(result, image_path="images/source.png")
    assert markdown.index("images/source.png") < markdown.index("```mermaid")


def test_sidecar_hash_binds_selected_node_id_map_without_changing_provenance(tmp_path):
    mappings = [
        NodeIdMapping(
            source_owner="vlm_fixture#001",
            source_id="A",
            fused_id="geometry-node-001",
            authority_source="geometry",
            authority_owner="geometry#000",
            match_method="unique_iou",
            iou=1,
            source_bbox=(0, 0, 0.1, 0.2),
            authority_bbox=(0, 0, 0.1, 0.2),
            source_text="Approve?",
            source_evidence_ids=["vlm-node-a"],
            authority_evidence_ids=["geometry-contour-001"],
        ),
        NodeIdMapping(
            source_owner="vlm_fixture#001",
            source_id="B",
            fused_id="geometry-node-002",
            authority_source="geometry",
            authority_owner="geometry#000",
            match_method="unique_iou",
            iou=1,
            source_bbox=(0.4, 0, 0.5, 0.2),
            authority_bbox=(0.4, 0, 0.5, 0.2),
            source_text="Done",
            source_evidence_ids=["vlm-node-b"],
            authority_evidence_ids=["geometry-contour-002"],
        ),
    ]
    evidence = [
        VisualEvidence(
            id="vlm-node-a",
            kind="ocr_token",
            text="Approve?",
            bbox=(0, 0, 10, 10),
            score=0.9,
            source_block_ids=["source"],
        ),
        VisualEvidence(
            id="vlm-node-b",
            kind="ocr_token",
            text="Done",
            bbox=(40, 0, 50, 10),
            score=0.9,
            source_block_ids=["source"],
        ),
        VisualEvidence(
            id="geometry-contour-001",
            kind="contour",
            bbox=(0, 0, 10, 10),
            score=0.95,
            source_block_ids=["source"],
        ),
        VisualEvidence(
            id="geometry-contour-002",
            kind="contour",
            bbox=(40, 0, 50, 10),
            score=0.95,
            source_block_ids=["source"],
        ),
    ]
    selected = MermaidCandidate(
        candidate_id="candidate-1-repair-1",
        generation_method="typed_ir",
        generation_engine="deterministic_fusion",
        diagram_type="flowchart",
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="geometry-node-001",
                    role="decision",
                    text="Approve?",
                    bbox=(0, 0, 10, 10),
                    evidence_ids=["geometry-contour-001", "vlm-node-a"],
                ),
                SceneElement(
                    id="geometry-node-002",
                    role="process",
                    text="Done",
                    bbox=(40, 0, 50, 10),
                    evidence_ids=["geometry-contour-002", "vlm-node-b"],
                ),
            ],
            canvas_size=(100, 50),
        ),
        typed_ir={
            "nodes": [
                {"id": "geometry-node-001", "label": "Approve?"},
                {"id": "geometry-node-002", "label": "Done"},
            ],
            "edges": [
                {
                    "source": "geometry-node-001",
                    "target": "geometry-node-002",
                    "label": "Yes",
                }
            ],
        },
        node_id_mappings=mappings,
        mermaid_code=(
            'flowchart LR\n    geometry_node_001{"Approve?"} -->|Yes| geometry_node_002["Done"]\n'
        ),
        syntax_valid=True,
        render_valid=True,
    )
    selected._seal_node_id_mappings()
    result = ReconstructionResult(
        source_id="mapped-flowchart",
        source_image_name="source.png",
        selected=selected,
        evidence=evidence,
        source_block_ids=["source"],
    )
    expected_provenance = [item.model_dump(mode="json") for item in evidence]
    expected_mapping = [item.model_dump(mode="json") for item in mappings]
    assert all(len(item.claim_digest or "") == 64 for item in mappings)

    relative = SidecarStore(tmp_path).write(result)
    bundle = tmp_path / relative
    mapping_path = bundle / "node-id-map.json"
    provenance_path = bundle / "provenance.json"
    manifest = json.loads((bundle / "manifest.json").read_text())

    assert json.loads(mapping_path.read_text()) == expected_mapping
    assert (
        manifest["files"]["node-id-map.json"]
        == hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    )
    assert json.loads(provenance_path.read_text()) == expected_provenance
    assert "node_id_mappings" not in provenance_path.read_text()

    invalid_result = result.model_copy(deep=True)
    invalid_result.source_id = "mapped-flowchart-missing-provenance"
    invalid_result.evidence = evidence[:-1]
    invalid_root = tmp_path / "invalid"
    with pytest.raises(ValueError, match="occur exactly once in provenance"):
        SidecarStore(invalid_root).write(invalid_result)
    assert not (invalid_root / "diagrams" / invalid_result.source_id).exists()

    aliased_payload = result.model_dump(mode="python")
    aliased_payload["source_id"] = "mapped-flowchart-aliased-provenance"
    aliased_mappings = aliased_payload["selected"]["node_id_mappings"]
    aliased_mappings[1]["source_evidence_ids"] = aliased_mappings[0]["source_evidence_ids"]
    aliased_mappings[1]["claim_digest"] = None
    aliased_result = ReconstructionResult.model_validate(aliased_payload)
    aliased_result.selected._seal_node_id_mappings()
    aliased_root = tmp_path / "aliased"
    with pytest.raises(ValueError, match="occur exactly once in provenance"):
        SidecarStore(aliased_root).write(aliased_result)
    assert not (aliased_root / "diagrams" / aliased_result.source_id).exists()

    with pytest.raises(ValueError, match="frozen"):
        result.selected.node_id_mappings[0].source_id = "B"

    digest_tampered_result = result.model_copy(deep=True)
    digest_tampered_result.source_id = "mapped-flowchart-digest-tampered"
    first_mapping, second_mapping = digest_tampered_result.selected.node_id_mappings
    digest_tampered_result.selected.node_id_mappings = [
        first_mapping.model_copy(update={"source_id": second_mapping.source_id}),
        second_mapping.model_copy(update={"source_id": first_mapping.source_id}),
    ]
    digest_tampered_root = tmp_path / "digest-tampered"
    with pytest.raises(ValueError, match="certification seal"):
        SidecarStore(digest_tampered_root).write(digest_tampered_result)
    assert not (digest_tampered_root / "diagrams" / digest_tampered_result.source_id).exists()

    text_swapped_result = result.model_copy(deep=True)
    text_swapped_result.source_id = "mapped-flowchart-text-swapped-provenance"
    first_evidence, second_evidence = text_swapped_result.evidence[:2]
    first_evidence.text, second_evidence.text = second_evidence.text, first_evidence.text
    text_swapped_root = tmp_path / "text-swapped"
    with pytest.raises(ValueError, match="spatially/text aligned"):
        SidecarStore(text_swapped_root).write(text_swapped_result)
    assert not (text_swapped_root / "diagrams" / text_swapped_result.source_id).exists()

    displaced_scene_result = result.model_copy(deep=True)
    displaced_scene_result.source_id = "mapped-flowchart-displaced-fused-node"
    displaced_scene_result.selected.scene_ir.elements[0].bbox = (70, 30, 80, 40)
    displaced_scene_root = tmp_path / "displaced-scene"
    with pytest.raises(ValueError, match="spatially/text aligned"):
        SidecarStore(displaced_scene_root).write(displaced_scene_result)
    assert not (displaced_scene_root / "diagrams" / displaced_scene_result.source_id).exists()

    blockless_result = result.model_copy(deep=True)
    blockless_result.source_id = "mapped-flowchart-blockless-provenance"
    for item in blockless_result.evidence:
        item.source_block_ids = []
    blockless_root = tmp_path / "blockless"
    with pytest.raises(ValueError, match="share a source block"):
        SidecarStore(blockless_root).write(blockless_result)
    assert not (blockless_root / "diagrams" / blockless_result.source_id).exists()

    rebound_result = result.model_copy(deep=True)
    rebound_result.source_id = "mapped-flowchart-rebound-provenance"
    for item in rebound_result.evidence:
        item.source_block_ids = ["fake-block"]
    rebound_root = tmp_path / "rebound"
    with pytest.raises(ValueError, match="share a source block"):
        SidecarStore(rebound_root).write(rebound_result)
    assert not (rebound_root / "diagrams" / rebound_result.source_id).exists()

    invalid_evidence_result = result.model_copy(deep=True)
    invalid_evidence_result.source_id = "mapped-flowchart-invalid-evidence"
    invalid_evidence_result.evidence[0].kind = "bogus"
    invalid_evidence_root = tmp_path / "invalid-evidence"
    with pytest.raises(ValueError):
        SidecarStore(invalid_evidence_root).write(invalid_evidence_result)
    assert not (invalid_evidence_root / "diagrams" / invalid_evidence_result.source_id).exists()

    unreferenced_result = result.model_copy(deep=True)
    unreferenced_result.source_id = "mapped-flowchart-unreferenced-evidence"
    unreferenced_result.selected.scene_ir.elements[0].evidence_ids = []
    unreferenced_root = tmp_path / "unreferenced"
    with pytest.raises(ValueError, match="spatially/text aligned"):
        SidecarStore(unreferenced_root).write(unreferenced_result)
    assert not (unreferenced_root / "diagrams" / unreferenced_result.source_id).exists()

    no_provenance_result = result.model_copy(deep=True)
    no_provenance_result.source_id = "mapped-flowchart-no-provenance"
    no_provenance_root = tmp_path / "no-provenance"
    relative = SidecarStore(no_provenance_root, write_provenance=False).write(no_provenance_result)
    no_provenance_bundle = no_provenance_root / relative
    assert (no_provenance_bundle / "node-id-map.json").is_file()
    assert (no_provenance_bundle / "provenance.json").is_file()


def test_node_id_mapping_rejects_iou_below_shared_sidecar_contract() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0.45"):
        NodeIdMapping(
            source_owner="vlm#001",
            source_id="A",
            fused_id="geometry-node-001",
            authority_source="geometry",
            authority_owner="geometry#000",
            match_method="unique_iou",
            iou=0.44,
            source_bbox=(0, 0, 0.1, 0.2),
            authority_bbox=(0, 0, 0.1, 0.2),
            source_text="Approve?",
            source_evidence_ids=["ocr-a"],
            authority_evidence_ids=["contour-a"],
        )


def test_node_id_mapping_rejects_iou_inconsistent_with_recorded_boxes() -> None:
    with pytest.raises(ValueError, match="must match its normalized"):
        NodeIdMapping(
            source_owner="vlm#001",
            source_id="A",
            fused_id="geometry-node-001",
            authority_source="geometry",
            authority_owner="geometry#000",
            match_method="unique_iou",
            iou=1,
            source_bbox=(0, 0, 0.2, 0.2),
            authority_bbox=(0.05, 0, 0.25, 0.2),
            source_text="Approve?",
            source_evidence_ids=["ocr-a"],
            authority_evidence_ids=["contour-a"],
        )


def test_sidecar_rejects_empty_traversal_component(tmp_path):
    result = ReconstructionResult(
        source_id="../",
        source_image_name="source.png",
        status="failed",
    )
    with pytest.raises(ValueError, match="unsafe sidecar"):
        SidecarStore(tmp_path).write(result)


def test_sidecar_rejects_symlinked_diagrams_directory_without_external_writes(tmp_path):
    output_root = tmp_path / "output"
    outside = tmp_path / "outside"
    output_root.mkdir()
    outside.mkdir()
    (output_root / "diagrams").symlink_to(outside, target_is_directory=True)
    result = ReconstructionResult(
        source_id="safe-source",
        source_image_name="source.png",
        status="failed",
    )

    with pytest.raises(ValueError, match="real direct child"):
        SidecarStore(output_root).write(result)

    assert list(outside.iterdir()) == []


def test_sidecar_rechecks_diagrams_identity_before_atomic_publish(
    tmp_path,
    monkeypatch,
):
    output_root = tmp_path / "output"
    outside = tmp_path / "outside"
    moved = tmp_path / "moved-diagrams"
    outside.mkdir()
    result = ReconstructionResult(
        source_id="safe-source",
        source_image_name="source.png",
        status="failed",
    )
    original_write = sidecar_module._write
    swapped = False

    def write_then_swap_directory(path, data):
        nonlocal swapped
        digest = original_write(path, data)
        if path.name == "manifest.json" and not swapped:
            (output_root / "diagrams").rename(moved)
            (output_root / "diagrams").symlink_to(outside, target_is_directory=True)
            swapped = True
        return digest

    monkeypatch.setattr(sidecar_module, "_write", write_then_swap_directory)

    with pytest.raises(ValueError, match="identity changed before bundle publication"):
        SidecarStore(output_root).write(result)

    assert swapped
    assert list(outside.iterdir()) == []
    assert not (outside / "safe-source").exists()


def test_sidecar_anchors_first_staging_write_and_cleanup_to_open_diagrams_fd(
    tmp_path,
    monkeypatch,
):
    output_root = tmp_path / "output"
    outside = tmp_path / "outside"
    moved = tmp_path / "moved-diagrams"
    outside.mkdir()
    result = ReconstructionResult(
        source_id="safe-source",
        source_image_name="source.png",
        status="failed",
        alternatives=[
            MermaidCandidate(
                candidate_id="nested-candidate",
                generation_method="test",
                diagram_type="flowchart",
                mermaid_code="flowchart LR\n    A --> B\n",
            )
        ],
    )
    original_write = sidecar_module._write
    swapped = False

    def swap_directory_then_write(path, data):
        nonlocal swapped
        if not swapped:
            (output_root / "diagrams").rename(moved)
            (output_root / "diagrams").symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_write(path, data)

    monkeypatch.setattr(sidecar_module, "_write", swap_directory_then_write)

    with pytest.raises(ValueError, match="identity changed before bundle publication"):
        SidecarStore(output_root).write(result)

    assert swapped
    assert list(outside.iterdir()) == []
    assert list(moved.iterdir()) == []


def test_sidecar_atomic_publish_never_replaces_racing_destination(
    tmp_path,
    monkeypatch,
):
    result = ReconstructionResult(
        source_id="safe-source",
        source_image_name="source.png",
        status="failed",
    )
    original_stat = sidecar_module.os.stat
    destination_checks = 0

    def create_destination_after_absence_check(
        path,
        *args,
        dir_fd=None,
        follow_symlinks=True,
        **kwargs,
    ):
        nonlocal destination_checks
        try:
            return original_stat(
                path,
                *args,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
                **kwargs,
            )
        except FileNotFoundError:
            if path == "safe-source" and dir_fd is not None:
                destination_checks += 1
                if destination_checks == 2:
                    sidecar_module.os.mkdir("safe-source", mode=0o700, dir_fd=dir_fd)
            raise

    monkeypatch.setattr(sidecar_module.os, "stat", create_destination_after_absence_check)

    with pytest.raises(FileExistsError, match="sidecar bundle already exists"):
        SidecarStore(tmp_path).write(result)

    destination = tmp_path / "diagrams" / "safe-source"
    assert destination_checks == 2
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not (destination / "manifest.json").exists()
    assert sorted(path.name for path in destination.parent.iterdir()) == ["safe-source"]


def test_sidecar_publish_fails_closed_without_atomic_no_replace_primitive(
    tmp_path,
    monkeypatch,
):
    result = ReconstructionResult(
        source_id="safe-source",
        source_image_name="source.png",
        status="failed",
    )

    monkeypatch.setattr(sidecar_module.ctypes, "CDLL", lambda *args, **kwargs: object())

    with pytest.raises(RuntimeError, match="no-replace rename support"):
        SidecarStore(tmp_path).write(result)

    diagrams = tmp_path / "diagrams"
    assert diagrams.is_dir()
    assert list(diagrams.iterdir()) == []


@pytest.mark.parametrize(
    ("platform", "primitive_name", "expected_flags"),
    [
        ("linux", "renameat2", 1),
        ("darwin", "renameatx_np", 0x00000004),
    ],
)
def test_sidecar_no_replace_backend_dispatch(
    monkeypatch,
    platform,
    primitive_name,
    expected_flags,
):
    calls = []

    class FakePrimitive:
        argtypes = None
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 0

    class FakeLibrary:
        pass

    primitive = FakePrimitive()
    library = FakeLibrary()
    setattr(library, primitive_name, primitive)
    monkeypatch.setattr(sidecar_module.sys, "platform", platform)
    monkeypatch.setattr(sidecar_module.ctypes, "CDLL", lambda *args, **kwargs: library)

    sidecar_module._rename_noreplace(11, ".source", 12, "destination")

    assert calls == [(11, b".source", 12, b"destination", expected_flags)]
    assert primitive.argtypes is not None
    assert primitive.restype is sidecar_module.ctypes.c_int


@pytest.mark.parametrize(
    ("rename_errno", "expected_exception"),
    [
        (sidecar_module.errno.EEXIST, FileExistsError),
        (sidecar_module.errno.EACCES, PermissionError),
    ],
)
def test_sidecar_no_replace_backend_maps_errno(
    monkeypatch,
    rename_errno,
    expected_exception,
):
    class FailingPrimitive:
        argtypes = None
        restype = None

        def __call__(self, *args):
            sidecar_module.ctypes.set_errno(rename_errno)
            return -1

    class FakeLibrary:
        renameat2 = FailingPrimitive()

    monkeypatch.setattr(sidecar_module.sys, "platform", "linux")
    monkeypatch.setattr(sidecar_module.ctypes, "CDLL", lambda *args, **kwargs: FakeLibrary())

    with pytest.raises(expected_exception):
        sidecar_module._rename_noreplace(11, ".source", 12, "destination")


def test_sidecar_no_replace_backend_fails_closed_on_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sidecar_module.sys, "platform", "win32")
    monkeypatch.setattr(
        sidecar_module.ctypes,
        "CDLL",
        lambda *args, **kwargs: pytest.fail("unsupported platforms must not call libc rename"),
    )

    with pytest.raises(RuntimeError, match="no-replace rename support"):
        sidecar_module._rename_noreplace(11, ".source", 12, "destination")


def test_sidecar_rejects_torn_snapshot_created_by_deepcopy_side_effect(
    tmp_path,
    fake_runtime,
):
    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "atomic-source",
        "source.png",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )
    assert result.selected is not None
    assert result.publish

    class MutatingSourceMapping(dict):
        def __deepcopy__(self, memo):
            result.publish = False
            result.review_required = True
            result.status = "review_required"
            assert result.selected is not None
            result.selected.mermaid_code = (
                'flowchart LR\n    A --> B; click A "https://evil.example"\n'
            )
            return dict(self)

    result.source_mapping = MutatingSourceMapping(source={"source_id": result.source_id})
    target = tmp_path / "diagrams" / "atomic-source"

    with pytest.raises(ValueError, match="changed while its snapshot was captured"):
        SidecarStore(tmp_path).write(result)

    assert not target.exists()


def test_sidecar_rejects_typed_ir_mutation_during_deepcopy_snapshot(
    tmp_path,
    fake_runtime,
):
    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "atomic-ir-source",
        "source.png",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )
    assert result.selected is not None
    assert result.selected.typed_ir is not None

    class MutatingSourceMapping(dict):
        def __deepcopy__(self, memo):
            assert result.selected is not None
            result.selected.typed_ir = {
                "title": "Injected",
                "nodes": [{"id": "X", "label": "Injected"}],
                "edges": [],
            }
            return dict(self)

    result.source_mapping = MutatingSourceMapping(source={"source_id": result.source_id})
    target = tmp_path / "diagrams" / "atomic-ir-source"

    with pytest.raises(ValueError, match="changed while its snapshot was captured"):
        SidecarStore(tmp_path).write(result)

    assert not target.exists()


def test_sidecar_write_flags_are_honored(tmp_path, fake_runtime):
    config = MermaidConfig(candidate_count=2)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("minimal", "source.png", Image.new("RGB", (100, 60), "white"))
    relative = SidecarStore(
        tmp_path,
        write_ir=False,
        write_svg=False,
        write_png=False,
        write_alternatives=False,
        write_provenance=False,
    ).write(result)
    bundle = tmp_path / relative
    assert (bundle / "final.mmd").is_file()
    assert (bundle / "final.svg").is_file()
    assert not (bundle / "scene-ir.json").exists()
    assert not (bundle / "generated-scene-ir.json").exists()
    assert not (bundle / "provenance.json").exists()
    assert not (bundle / "alternatives").exists()
