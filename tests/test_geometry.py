from __future__ import annotations

from PIL import Image

from marker_mermaid.geometry import (
    ArrowheadObservation,
    ContourObservation,
    GeometryEngine,
    GeometryObservation,
    LineObservation,
)
from marker_mermaid.protocols import SourceContext


def _two_node_observation(*, arrow_at_start: bool = False) -> GeometryObservation:
    arrow_tip = (20.0, 15.0) if arrow_at_start else (80.0, 15.0)
    return GeometryObservation(
        canvas_size=(100, 30),
        contours=(
            ContourObservation(bbox=(0, 5, 20, 25), confidence=0.9),
            ContourObservation(bbox=(80, 5, 100, 25), confidence=0.8),
        ),
        lines=(LineObservation(start=(20, 15), end=(80, 15), confidence=0.7),),
        arrowheads=(
            ArrowheadObservation(
                bbox=(arrow_tip[0] - 3, 12, arrow_tip[0] + 3, 18),
                tip=arrow_tip,
                confidence=0.8,
            ),
        ),
    )


def test_geometry_observation_builds_evidence_backed_scene():
    result = _two_node_observation().to_engine_observation(["figure-1"])

    assert result.prediction.candidates == ["flowchart", "generic_network"]
    assert result.scene_ir is not None
    assert result.scene_ir.reading_direction == "LR"
    assert [element.text for element in result.scene_ir.elements] == [None, None]
    relation = result.scene_ir.relations[0]
    assert relation.source_id == "geometry-node-001"
    assert relation.target_id == "geometry-node-002"
    assert relation.arrow_at_end and not relation.arrow_at_start
    assert relation.evidence_ids == ["geometry-line-001", "geometry-arrowhead-001"]

    evidence_ids = {item.id for item in result.evidence}
    assert all(element.evidence_ids for element in result.scene_ir.elements)
    assert all(set(element.evidence_ids) <= evidence_ids for element in result.scene_ir.elements)
    assert all(set(item.evidence_ids) <= evidence_ids for item in result.scene_ir.relations)
    assert all(item.source_block_ids == ["figure-1"] for item in result.evidence)


def test_arrow_at_line_start_reverses_canonical_relation():
    result = _two_node_observation(arrow_at_start=True).to_engine_observation(["figure-1"])

    assert result.scene_ir is not None
    relation = result.scene_ir.relations[0]
    assert relation.source_id == "geometry-node-002"
    assert relation.target_id == "geometry-node-001"
    assert relation.polyline == [(80.0, 15.0), (20.0, 15.0)]
    assert result.scene_ir.reading_direction == "RL"


def test_unattached_line_is_evidence_but_not_a_scene_relation():
    geometry = GeometryObservation(
        canvas_size=(100, 100),
        contours=(ContourObservation(bbox=(5, 5, 25, 25)),),
        lines=(LineObservation(start=(50, 50), end=(90, 90)),),
    )

    result = geometry.to_engine_observation(["block"])

    assert result.scene_ir is not None
    assert result.scene_ir.relations == []
    assert any(item.kind == "line_segment" for item in result.evidence)
    assert result.prediction.candidates[0] == "generic_network"


def test_conversion_is_deterministic_and_deduplicates_nested_contours():
    left = ContourObservation(bbox=(0, 0, 20, 20), confidence=0.8)
    duplicate = ContourObservation(bbox=(1, 1, 21, 21), confidence=0.7)
    right = ContourObservation(bbox=(80, 0, 100, 20), confidence=0.9)
    line = LineObservation(start=(20, 10), end=(80, 10))
    first = GeometryObservation(
        canvas_size=(100, 20), contours=(left, duplicate, right), lines=(line,)
    )
    second = GeometryObservation(
        canvas_size=(100, 20), contours=(right, duplicate, left), lines=(line,)
    )

    assert first.to_engine_observation(["block"]) == second.to_engine_observation(["block"])
    result = first.to_engine_observation(["block"])
    assert result.scene_ir is not None
    assert len(result.scene_ir.elements) == 2


def test_geometry_engine_satisfies_candidate_engine_contract_with_injected_detector():
    geometry = _two_node_observation()
    engine = GeometryEngine(detector=lambda image: geometry)
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["figure-1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (100, 30), "white"),
    )

    result = engine.observe(context)

    assert engine.name == "geometry"
    assert result.scene_ir is not None
    assert len(result.scene_ir.relations) == 1


def test_missing_opencv_is_a_non_fatal_empty_observation(monkeypatch):
    monkeypatch.setattr("marker_mermaid.geometry._load_opencv", lambda: None)
    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["figure-1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
    )

    result = GeometryEngine().observe(context)

    assert result.scene_ir is None
    assert result.prediction.candidates == ["unknown"]
    assert result.evidence == []
    assert any("OpenCV is unavailable" in warning for warning in result.warnings)
