from __future__ import annotations

from marker_mermaid.fusion import FusionEngine, FusionInput
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    SceneElement,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)


def _observation(
    diagram_type: str,
    score: float,
    *,
    scene: DiagramSceneIR | None = None,
    evidence: list[VisualEvidence] | None = None,
) -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=[diagram_type], scores=[score]),
        scene_ir=scene,
        evidence=evidence or [],
    )


def _scene(
    element: SceneElement,
    *,
    relation: SceneRelation | None = None,
    direction: str = "unknown",
) -> DiagramSceneIR:
    elements = [element]
    if relation is not None:
        elements.append(
            SceneElement(id="target", role="process", bbox=(70, 10, 100, 40), confidence=0.8)
        )
    return DiagramSceneIR(
        elements=elements,
        relations=[relation] if relation else [],
        reading_direction=direction,
        canvas_size=(100, 100),
    )


def test_fuses_geometry_with_vlm_text_role_and_semantics() -> None:
    geometry_scene = DiagramSceneIR(
        elements=[
            SceneElement(
                id="cv-node",
                role="unknown",
                bbox=(10, 10, 42, 42),
                shape="rectangle",
                confidence=0.7,
                evidence_ids=["contour-1"],
            ),
            SceneElement(id="target", role="unknown", bbox=(70, 10, 100, 40), confidence=0.7),
        ],
        relations=[
            SceneRelation(
                id="cv-edge",
                source_id="cv-node",
                target_id="target",
                relation_type="connector",
                polyline=[(42, 25), (70, 25)],
                confidence=0.7,
                evidence_ids=["line-1"],
            )
        ],
        canvas_size=(100, 100),
    )
    vlm_scene = DiagramSceneIR(
        elements=[
            SceneElement(
                id="request",
                role="process",
                text="Payment request",
                bbox=(11, 11, 41, 41),
                confidence=0.9,
                evidence_ids=["vlm-node"],
            ),
            SceneElement(id="target", role="decision", bbox=(69, 9, 99, 39), confidence=0.9),
        ],
        relations=[
            SceneRelation(
                id="vlm-edge",
                source_id="request",
                target_id="target",
                relation_type="decision_branch",
                semantic_relation="conditional",
                label="yes",
                polyline=[(41, 24), (69, 24)],
                confidence=0.9,
                evidence_ids=["vlm-edge-evidence"],
            )
        ],
        canvas_size=(100, 100),
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm_scene), "vlm"),
            FusionInput("geometry", _observation("flowchart", 0.7, scene=geometry_scene), "opencv"),
        ]
    )

    assert fused.scene_ir is not None
    assert len(fused.scene_ir.elements) == 2
    node = fused.scene_ir.elements[0]
    assert node.id == "cv-node"
    assert node.bbox == (10, 10, 42, 42)
    assert node.shape == "rectangle"
    assert node.text == "Payment request"
    assert node.role == "process"
    assert node.evidence_ids == ["contour-1", "vlm-node"]
    edge = fused.scene_ir.relations[0]
    assert edge.polyline == [(42, 25), (70, 25)]
    assert edge.semantic_relation == "conditional"
    assert edge.relation_type == "decision_branch"
    assert edge.label == "yes"


def test_vector_geometry_and_label_override_other_sources() -> None:
    vector = _scene(
        SceneElement(
            id="node",
            role="unknown",
            text="Vector label",
            bbox=(10, 10, 40, 40),
            confidence=0.8,
        )
    )
    geometry = _scene(
        SceneElement(id="shape", role="unknown", bbox=(12, 12, 42, 42), confidence=0.9)
    )
    vlm = _scene(
        SceneElement(
            id="meaning",
            role="process",
            text="Invented label",
            bbox=(11, 11, 41, 41),
            confidence=0.99,
        )
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("geometry", _observation("flowchart", 0.8, scene=geometry), "cv"),
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm), "vlm"),
            FusionInput("vector", _observation("flowchart", 0.7, scene=vector), "pdf"),
        ]
    )

    assert fused.scene_ir is not None
    node = fused.scene_ir.elements[0]
    assert node.id == "node"
    assert node.bbox == (10, 10, 40, 40)
    assert node.text == "Vector label"
    assert node.role == "process"
    assert any("label conflict" in warning for warning in fused.warnings)


def test_conflicting_font_weight_evidence_is_omitted_during_fusion() -> None:
    vector = _scene(
        SceneElement(
            id="node",
            role="unknown",
            text="Label",
            bbox=(10, 10, 40, 40),
            font_weight="bold",
        )
    )
    vlm = _scene(
        SceneElement(
            id="semantic-node",
            role="process",
            text="Label",
            bbox=(10, 10, 40, 40),
            font_weight="normal",
        )
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vector", _observation("flowchart", 0.8, scene=vector), "pdf"),
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm), "vlm"),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].font_weight is None
    assert any("font-weight conflict" in warning for warning in fused.warnings)


def test_ocr_consensus_beats_vlm_label() -> None:
    vlm = _scene(
        SceneElement(
            id="node",
            role="process",
            text="Approval?",
            bbox=(10, 10, 60, 40),
            confidence=0.95,
        )
    )
    ocr_one = _scene(
        SceneElement(
            id="ocr-a",
            role="unknown",
            text="Payment approval",
            bbox=(10, 10, 60, 40),
            confidence=0.7,
        )
    )
    ocr_two = _scene(
        SceneElement(
            id="ocr-b",
            role="unknown",
            text=" payment   approval ",
            bbox=(10, 10, 60, 40),
            confidence=0.6,
        )
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("ocr", _observation("flowchart", 0.5, scene=ocr_two), "ocr-b"),
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm), "vlm"),
            FusionInput("ocr", _observation("flowchart", 0.5, scene=ocr_one), "ocr-a"),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].text == "Payment approval"


def test_ocr_evidence_can_enrich_a_scene_without_an_ocr_scene() -> None:
    scene = _scene(
        SceneElement(
            id="node",
            role="process",
            text="VLM guess",
            bbox=(10, 10, 60, 40),
            confidence=0.9,
        )
    )
    ocr = _observation(
        "unknown",
        1.0,
        evidence=[
            VisualEvidence(
                id="ocr-token",
                kind="ocr_token",
                bbox=(20, 15, 50, 30),
                text="Verified label",
                score=0.8,
            )
        ],
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vlm", _observation("flowchart", 0.9, scene=scene), "vlm"),
            FusionInput("ocr", ocr, "surya"),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].text == "Verified label"


def test_vector_direction_and_vlm_relation_semantics_are_combined() -> None:
    vector_scene = DiagramSceneIR(
        elements=[
            SceneElement(id="a", role="unknown", bbox=(0, 0, 20, 20)),
            SceneElement(id="b", role="unknown", bbox=(80, 0, 100, 20)),
        ],
        relations=[
            SceneRelation(
                id="path",
                source_id="a",
                target_id="b",
                relation_type="connector",
                polyline=[(20, 10), (80, 10)],
                line_color="#445566",
            )
        ],
        canvas_size=(100, 100),
    )
    vlm_scene = DiagramSceneIR(
        elements=[
            SceneElement(id="a", role="service", bbox=(0, 0, 20, 20)),
            SceneElement(id="b", role="database", bbox=(80, 0, 100, 20)),
        ],
        relations=[
            SceneRelation(
                id="meaning",
                source_id="b",
                target_id="a",
                relation_type="query",
                semantic_relation="data_flow",
            )
        ],
        canvas_size=(100, 100),
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vlm", _observation("architecture", 0.9, scene=vlm_scene), "vlm"),
            FusionInput("vector", _observation("architecture", 0.8, scene=vector_scene), "pdf"),
        ]
    )

    assert fused.scene_ir is not None
    edge = fused.scene_ir.relations[0]
    assert (edge.source_id, edge.target_id) == ("a", "b")
    assert edge.polyline == [(20, 10), (80, 10)]
    assert edge.line_color == "#445566"
    assert edge.semantic_relation == "data_flow"
    assert edge.relation_type == "query"
    assert any("direction conflict" in warning for warning in fused.warnings)


def test_parallel_labeled_relations_are_not_collapsed() -> None:
    scene = DiagramSceneIR(
        elements=[
            SceneElement(id="a", role="decision", bbox=(0, 0, 20, 20)),
            SceneElement(id="b", role="process", bbox=(80, 0, 100, 20)),
        ],
        relations=[
            SceneRelation(
                id="yes-edge",
                source_id="a",
                target_id="b",
                relation_type="branch",
                label="yes",
            ),
            SceneRelation(
                id="no-edge",
                source_id="a",
                target_id="b",
                relation_type="branch",
                label="no",
            ),
        ],
        canvas_size=(100, 100),
    )

    fused = FusionEngine().fuse(
        [FusionInput("vlm", _observation("flowchart", 0.9, scene=scene), "vlm")]
    )

    assert fused.scene_ir is not None
    assert {relation.label for relation in fused.scene_ir.relations} == {"yes", "no"}


def test_deduplicates_evidence_and_reports_payload_conflict() -> None:
    left = _observation(
        "flowchart",
        0.7,
        evidence=[
            VisualEvidence(
                id="shared",
                kind="ocr_token",
                text="left",
                score=0.5,
                source_block_ids=["block-b"],
            )
        ],
    )
    right = _observation(
        "flowchart",
        0.8,
        evidence=[
            VisualEvidence(
                id="shared",
                kind="vector_text",
                text="right",
                score=0.9,
                source_block_ids=["block-a"],
            )
        ],
    )

    fused = FusionEngine().fuse(
        [FusionInput("ocr", left, "ocr"), FusionInput("vector", right, "pdf")]
    )

    assert len(fused.evidence) == 1
    assert fused.evidence[0].kind == "vector_text"
    assert fused.evidence[0].text == "right"
    assert fused.evidence[0].score == 0.9
    assert fused.evidence[0].source_block_ids == ["block-a", "block-b"]
    assert any("evidence conflict" in warning for warning in fused.warnings)


def test_prediction_and_candidate_fusion_is_order_independent() -> None:
    vector = _observation("architecture", 0.8)
    vector.prediction = DiagramTypePrediction(
        candidates=["architecture", "flowchart"],
        scores=[0.8, 0.2],
        visual_signals=["groups"],
    )
    vector.typed_candidates = [
        TypedIRCandidate(diagram_type="architecture", ir={"services": []}, confidence=0.7)
    ]
    vlm = _observation("flowchart", 0.9)
    vlm.prediction.visual_signals = ["arrows", "groups"]
    vlm.direct_candidates = [
        DirectMermaidCandidate(
            diagram_type="flowchart", code="flowchart LR\n A --> B", confidence=0.8
        ),
        DirectMermaidCandidate(
            diagram_type="flowchart", code="flowchart LR\n A --> B", confidence=0.6
        ),
    ]
    inputs = [FusionInput("vector", vector, "pdf"), FusionInput("vlm", vlm, "vlm")]

    forward = FusionEngine().fuse(inputs)
    backward = FusionEngine().fuse(reversed(inputs))

    assert forward == backward
    # The weighted mean is 0.55 for flowchart and 0.40 for architecture.
    # This documents averaging rather than accidental max-score fusion.
    assert forward.prediction.candidates == ["flowchart", "architecture"]
    assert forward.prediction.scores == [0.55, 0.4]
    assert forward.prediction.visual_signals == ["arrows", "groups"]
    assert len(forward.typed_candidates) == 1
    assert len(forward.direct_candidates) == 1


def test_rejects_empty_or_untyped_inputs() -> None:
    engine = FusionEngine()

    try:
        engine.fuse([])
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty fusion should fail")

    try:
        engine.fuse([object()])  # type: ignore[list-item]
    except TypeError as exc:
        assert "FusionInput" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("untyped fusion input should fail")
