from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from marker_mermaid.config import MermaidConfig, Mode, quality_grade
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    MetricResult,
    SceneElement,
    SceneGroup,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)


def test_mode_budgets_and_marker_prefixes():
    assert MermaidConfig().candidate_count == 3
    assert MermaidConfig(mode=Mode.MAXIMAL).max_repair_iterations == 10
    strict = MermaidConfig.from_marker_config(
        {"MermaidDiagramProcessor_mode": "strict", "candidate_count": 1}
    )
    assert strict.mode == Mode.STRICT
    assert strict.candidate_count == 1


def test_original_image_cannot_be_disabled():
    with pytest.raises(ValidationError):
        MermaidConfig(extract_images=False)
    with pytest.raises(ValidationError):
        MermaidConfig(include_original_image=False)


@pytest.mark.parametrize(
    "values",
    [
        {"tile_size": 63},
        {"tile_size": 128, "tile_overlap": -1},
        {"tile_size": 128, "tile_overlap": 128},
    ],
)
def test_tile_geometry_budget_is_validated(values):
    with pytest.raises(ValidationError, match="tile_"):
        MermaidConfig(**values)


@pytest.mark.parametrize(
    ("score", "grade"),
    [(0.85, "A"), (0.849, "B"), (0.70, "B"), (0.699, "C"), (0.50, "C"), (0.49, "D"), (None, "U")],
)
def test_grade_boundaries(score, grade):
    assert quality_grade(score) == grade


def test_scene_rejects_dangling_relations():
    with pytest.raises(ValidationError, match="missing elements"):
        DiagramSceneIR(
            elements=[SceneElement(id="A", role="node", bbox=(0, 0, 1, 1))],
            relations=[
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type="edge",
                )
            ],
        )


def test_prediction_and_metric_invariants():
    with pytest.raises(ValidationError):
        DiagramTypePrediction(candidates=["flowchart"], scores=[])
    with pytest.raises(ValidationError):
        MetricResult(name="ocr_recall", value=None, available=True)
    with pytest.raises(ValidationError, match="descending"):
        DiagramTypePrediction(candidates=["flowchart", "architecture"], scores=[0.1, 0.9])


def test_engine_observation_and_typed_ir_are_resource_bounded():
    prediction = DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
    candidate = TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []})
    with pytest.raises(ValidationError, match="too_long"):
        EngineObservation(prediction=prediction, typed_candidates=[candidate] * 65)

    deeply_nested: dict = {}
    cursor = deeply_nested
    for _ in range(66):
        child: dict = {}
        cursor["child"] = child
        cursor = child
    with pytest.raises(ValidationError, match="nesting depth"):
        TypedIRCandidate(diagram_type="mindmap", ir=deeply_nested)


def test_typed_candidate_rejects_another_diagram_familys_root_shape():
    with pytest.raises(ValidationError, match="requires root field 'participants'"):
        TypedIRCandidate(
            diagram_type="sequence",
            ir={"nodes": [{"id": "A"}], "edges": []},
        )
    with pytest.raises(ValidationError, match="must be a list"):
        TypedIRCandidate(
            diagram_type="flowchart",
            ir={"nodes": {"A": {"label": "wrong container"}}},
        )


def test_candidate_confidence_is_a_probability():
    with pytest.raises(ValidationError):
        TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []}, confidence=1.1)


def test_observation_text_and_scene_coordinates_are_json_bounded():
    with pytest.raises(ValidationError, match="text size limit"):
        VisualEvidence(id="e", kind="ocr_token", text="x" * 50_001)
    with pytest.raises(ValidationError, match="finite"):
        SceneElement(id="A", role="node", bbox=(0, 0, math.nan, 1))
    with pytest.raises(ValidationError, match="finite"):
        SceneRelation(
            id="E",
            source_id=None,
            target_id=None,
            relation_type="edge",
            polyline=[(0, 0), (math.inf, 1)],
        )
    with pytest.raises(ValidationError, match="endpoint"):
        SceneRelation(id="E", source_id="x" * 257, relation_type="edge")
    with pytest.raises(ValidationError, match="warning"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
            warnings=["x" * 4_097],
        )


def test_scene_group_budget_is_independent_from_evidence_reference_budget():
    members = [f"N{index}" for index in range(257)]
    group = SceneGroup(id="G", role="group", bbox=(0, 0, 1, 1), member_ids=members)

    assert len(group.member_ids) == 257
