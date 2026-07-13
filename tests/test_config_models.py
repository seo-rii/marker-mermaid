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
    PromptBudgetNotice,
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


def test_structured_vlm_prompt_budgets_are_bounded_and_marker_configurable():
    config = MermaidConfig.from_marker_config(
        {
            "MermaidDiagramProcessor_max_vlm_prompt_chars": 32_768,
            "MermaidDiagramProcessor_max_vlm_evidence_items": 32,
            "MermaidDiagramProcessor_max_vlm_ocr_items": 64,
        }
    )

    assert config.max_vlm_prompt_chars == 32_768
    assert config.max_vlm_evidence_items == 32
    assert config.max_vlm_ocr_items == 64
    for values in (
        {"max_vlm_prompt_chars": 32_767},
        {"max_vlm_prompt_chars": 1_000_001},
        {"max_views": 17},
        {"max_image_dimension": 4_097},
        {"tile_size": 4_097},
        {"max_vlm_evidence_items": 0},
        {"max_vlm_evidence_items": 4_097},
        {"max_vlm_ocr_items": -1},
        {"max_vlm_ocr_items": 4_097},
    ):
        with pytest.raises(ValidationError):
            MermaidConfig(**values)


def test_prompt_budget_notice_cross_checks_caps_counts_and_reasons():
    valid = {
        "engine": "marker_structured_vlm",
        "selection_profile": "structural-quota-v1",
        "prompt_chars": 10_000,
        "max_prompt_chars": 100_000,
        "schema_reserve_chars": 14_753,
        "max_evidence_items": 1,
        "max_ocr_items": 1,
        "evidence_total": 2,
        "evidence_considered": 2,
        "evidence_included": 1,
        "ocr_total": 2,
        "ocr_considered": 1,
        "ocr_included": 1,
        "omission_reasons": ["evidence_item_limit", "evidence_char_limit", "ocr_item_limit"],
        "selected_evidence_sha256": "0" * 64,
    }
    PromptBudgetNotice.model_validate(valid)

    for changes in (
        {"max_evidence_items": 2},
        {"max_ocr_items": 2},
        {"evidence_included": 2},
        {"ocr_considered": 2},
        {
            "omission_reasons": [
                "evidence_item_limit",
                "ocr_item_limit",
            ]
        },
    ):
        with pytest.raises(ValidationError):
            PromptBudgetNotice.model_validate({**valid, **changes})


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


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        (
            "flowchart",
            {"nodes": [{"id": "A", "evidence_ids": [1]}]},
            "nodes[0].evidence_ids[0]",
        ),
        (
            "generic_network",
            {"nodes": [{"id": "A"}], "groups": [{"member_ids": "A"}]},
            "groups[0].member_ids",
        ),
        (
            "swimlane",
            {"lanes": [{"id": "lane", "nodes": ["not-an-object"]}]},
            "lanes[0].nodes[0]",
        ),
        (
            "bpmn",
            {"lanes": [{"nodes": [{"id": "task", "label": ["wrong"]}]}]},
            "lanes[0].nodes[0].label",
        ),
        (
            "sequence",
            {"participants": ["A"], "messages": [{"source": [], "target": "A"}]},
            "messages[0].source",
        ),
        (
            "mindmap",
            {"root": {"label": "Root", "children": ["not-an-object"]}},
            "root.children[0]",
        ),
        (
            "timeline",
            {"events": [{"time": "Q1", "events": ["Launch", {}]}]},
            "events[0].events[1]",
        ),
        (
            "gantt",
            {"sections": [{"tasks": [{"start": 2026, "duration": "1d"}]}]},
            "sections[0].tasks[0].start",
        ),
        (
            "architecture",
            {"services": [{"id": "api", "group": ["cloud"]}]},
            "services[0].group",
        ),
        (
            "architecture",
            {
                "services": [{"id": "api"}, {"id": "db"}],
                "edges": [{"source": "api", "target": "db", "source_side": "X"}],
            },
            "edges[0].source_side",
        ),
    ],
)
def test_phase_one_nested_contracts_reject_wrong_record_shapes(
    diagram_type: str,
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    message = str(exc_info.value)
    assert "violates its nested contract" in message
    assert location in message


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        ("flowchart", {"nodes": []}),
        ("generic_network", {"nodes": [{"label": "[unreadable]"}]}),
        ("swimlane", {"lanes": [{"id": "lane"}]}),
        ("bpmn", {"lanes": [{"nodes": []}]}),
        ("sequence", {"participants": ["Client"], "messages": []}),
        ("mindmap", {"root": {"children": [{"text": "Child"}]}}),
        ("timeline", {"events": [{"period": "Q1", "events": ["Launch"]}]}),
        (
            "gantt",
            {
                "date_format": "YYYY-MM-DD",
                "sections": [{"title": "Build", "tasks": []}],
            },
        ),
        (
            "architecture",
            {
                "services": [
                    {
                        "id": "api",
                        "name": "API",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["vector-api"],
                        "future_metadata": {"kept": True},
                    }
                ]
            },
        ),
    ],
)
def test_phase_one_nested_contracts_preserve_partial_and_forward_compatible_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize("bbox", [[0, 0, 10], [0, 0, True, 10], ["0", 0, 10, 10]])
def test_phase_one_nested_contracts_require_four_strict_finite_bbox_numbers(bbox) -> None:
    with pytest.raises(ValidationError, match="bbox"):
        TypedIRCandidate(
            diagram_type="architecture",
            ir={"services": [{"id": "api", "bbox": bbox}]},
        )


def test_canonical_key_revalidates_mutated_nested_contracts() -> None:
    candidate = TypedIRCandidate(
        diagram_type="timeline",
        ir={"events": [{"time": "Q1", "events": ["Launch"]}]},
    )
    candidate.ir["events"][0]["events"] = "Launch"

    with pytest.raises(ValidationError, match=r"events\[0\]\.events"):
        candidate.canonical_key()


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
