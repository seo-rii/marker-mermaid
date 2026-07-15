from __future__ import annotations

from copy import deepcopy

import pytest
from PIL import Image

import marker_mermaid.pipeline as pipeline_module
from marker_mermaid.config import MermaidConfig, Mode, PublishPolicy
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.markdown import reconstruction_markdown
from marker_mermaid.models import (
    MAX_OBSERVATION_WARNINGS,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.serializers import serialize_typed_ir_result
from marker_mermaid.serializers_charts_core import (
    QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING,
)
from marker_mermaid.validation import CandidateValidator

QUADRANT_IR = {
    "title": "Portfolio",
    "description": "Projects by reach and confidence.",
    "x_axis": {
        "low": "Low reach",
        "high": "High reach",
        "bbox": [20, 180, 180, 198],
        "evidence_ids": ["ocr-x-axis"],
    },
    "y_axis": {
        "low": "Low confidence",
        "high": "High confidence",
        "bbox": [0, 20, 18, 180],
        "evidence_ids": ["ocr-y-axis"],
    },
    "quadrants": {
        "quadrant-1": "Expand",
        "quadrant-2": "Promote",
        "quadrant-3": "Review",
        "quadrant-4": "Improve",
    },
    "points": [
        {
            "label": "Project A",
            "x": 0.85,
            "y": 0.65,
            "bbox": [160, 60, 180, 80],
            "evidence_ids": ["ocr-point-a"],
        },
        {
            "label": "Project B",
            "x": 0.15,
            "y": 0.15,
            "bbox": [20, 165, 40, 178],
            "evidence_ids": ["ocr-point-b"],
        },
    ],
}


class _QuadrantRuntime:
    def __init__(self, *, reject_native: bool = False) -> None:
        self.reject_native = reject_native
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: int) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("quadrantChart") and self.reject_native:
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="quadrantChart",
                error="forced native rejection",
            )
        diagram_type = "quadrantChart" if code.startswith("quadrantChart") else "flowchart-v2"
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=diagram_type,
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
                "<text>Portfolio Expand Promote Review Improve Project A Project B</text>"
                "</svg>"
            ),
        )

    def close(self) -> None:
        pass


class _QuadrantPointSwapRepair:
    name = "quadrant_point_swap"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["points"][0]["x"], typed_ir["points"][1]["x"] = (
            typed_ir["points"][1]["x"],
            typed_ir["points"][0]["x"],
        )
        serialized = serialize_typed_ir_result(
            "quadrant",
            typed_ir,
            experimental=True,
        )
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _QuadrantTitleRepair:
    name = "quadrant_add_title"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["title"] = "Portfolio"
        typed_ir["acc_title"] = "Portfolio"
        serialized = serialize_typed_ir_result(
            "quadrant",
            typed_ir,
            experimental=True,
        )
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


def _quadrant_evidence(
    *,
    x_text: str = "Low reach High reach",
    y_text: str = "Low confidence High confidence",
    point_a_text: str = "Project A 0.85 0.65",
    point_b_text: str = "Project B 0.15 0.15",
    slot_positions: dict[str, tuple[float, float, float, float]] | None = None,
    include_slots: bool = True,
) -> list[VisualEvidence]:
    positions = slot_positions or {
        "Expand": (105, 20, 145, 32),
        "Promote": (25, 20, 75, 32),
        "Review": (55, 120, 95, 134),
        "Improve": (120, 120, 175, 134),
    }
    evidence = [
        VisualEvidence(
            id="ocr-title",
            kind="ocr_token",
            text="Portfolio",
            bbox=(5, 2, 75, 12),
        ),
        VisualEvidence(
            id="ocr-description",
            kind="vector_text",
            text="Projects by reach and confidence.",
            bbox=(80, 2, 195, 12),
        ),
        VisualEvidence(
            id="ocr-x-axis",
            kind="ocr_token",
            text=x_text,
            bbox=(25, 184, 175, 194),
        ),
        VisualEvidence(
            id="ocr-y-axis",
            kind="vector_text",
            text=y_text,
            bbox=(2, 25, 16, 175),
        ),
        VisualEvidence(
            id="ocr-point-a",
            kind="ocr_token",
            text=point_a_text,
            bbox=(163, 65, 177, 75),
        ),
        VisualEvidence(
            id="ocr-point-b",
            kind="vector_text",
            text=point_b_text,
            bbox=(23, 168, 37, 175),
        ),
    ]
    if include_slots:
        evidence.extend(
            VisualEvidence(
                id=f"ocr-slot-{index}",
                kind="ocr_token" if index % 2 else "vector_text",
                text=label,
                bbox=positions[label],
            )
            for index, label in enumerate(
                ("Expand", "Promote", "Review", "Improve"), start=1
            )
        )
    return evidence


def _reconstruct_quadrant(
    *,
    ir: dict[str, object] | None = None,
    evidence: list[VisualEvidence] | None = None,
    initial_evidence: list[VisualEvidence] | None = None,
    reject_native: bool = False,
    ocr_texts: list[str] | None = None,
    repair_engine: object | None = None,
    publish_policy: PublishPolicy = PublishPolicy.BEST_EFFORT_VALIDATED,
    review_below_score: float = 0.70,
    engine_warnings: list[str] | None = None,
) -> tuple[object, _QuadrantRuntime]:
    runtime = _QuadrantRuntime(reject_native=reject_native)
    config = MermaidConfig(
        candidate_count=1,
        publish_min_score=0,
        publish_policy=publish_policy,
        review_below_score=review_below_score,
    )
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["quadrant"], scores=[1]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="quadrant",
                ir=deepcopy(ir or QUADRANT_IR),
            )
        ],
        evidence=evidence if evidence is not None else _quadrant_evidence(),
        warnings=engine_warnings or [],
    )
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=repair_engine,
    ).reconstruct(
        "quadrant-source",
        "source.png",
        Image.new("RGB", (200, 200), "white"),
        ocr_texts=(
            [
                "Portfolio Expand Promote Review Improve Low reach High reach "
                "Low confidence High confidence Project A 0.85 0.65 "
                "Project B 0.15 0.15"
            ]
            if ocr_texts is None
            else ocr_texts
        ),
        evidence=initial_evidence,
    )
    return result, runtime


@pytest.mark.parametrize("reject_native", [False, True])
def test_quadrant_record_and_slot_evidence_publish_native_and_fallback(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_quadrant(reject_native=reject_native)

    assert result.selected is not None
    selected = result.selected
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert selected.aggregate_score is not None, selected.warnings
    assert result.publish
    assert selected.emitted_diagram_type == ("flowchart" if reject_native else "quadrant")
    assert selected.generated_scene_ir is not None
    assert len(runtime.calls) == (2 if reject_native else 1)


@pytest.mark.parametrize("reject_native", [False, True])
def test_quadrant_axis_swap_requires_review(reject_native: bool) -> None:
    result, _runtime = _reconstruct_quadrant(
        evidence=_quadrant_evidence(
            x_text="Low confidence High confidence",
            y_text="Low reach High reach",
        ),
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("reject_native", [False, True])
def test_quadrant_whole_axis_record_swap_requires_review(
    reject_native: bool,
) -> None:
    ir = deepcopy(QUADRANT_IR)
    ir["x_axis"], ir["y_axis"] = ir["y_axis"], ir["x_axis"]

    result, _runtime = _reconstruct_quadrant(
        ir=ir,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("reverse_labels", [False, True])
def test_quadrant_split_y_axis_reads_low_from_bottom_to_high_at_top(
    reverse_labels: bool,
) -> None:
    ir = deepcopy(QUADRANT_IR)
    ir["y_axis"]["evidence_ids"] = ["ocr-y-low", "ocr-y-high"]
    evidence = [
        item for item in _quadrant_evidence() if item.id != "ocr-y-axis"
    ]
    evidence.extend(
        [
            VisualEvidence(
                id="ocr-y-low",
                kind="ocr_token",
                text="High confidence" if reverse_labels else "Low confidence",
                bbox=(2, 145, 16, 175),
            ),
            VisualEvidence(
                id="ocr-y-high",
                kind="vector_text",
                text="Low confidence" if reverse_labels else "High confidence",
                bbox=(2, 25, 16, 55),
            ),
        ]
    )

    result, _runtime = _reconstruct_quadrant(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == (0 if reverse_labels else 1)
    if reverse_labels:
        assert result.selected.aggregate_score is None
        assert not result.publish
    else:
        assert result.selected.aggregate_score is not None, result.selected.warnings
        assert result.publish


@pytest.mark.parametrize(
    ("point_a_text", "point_b_text"),
    [
        ("Project B 0.85 0.65", "Project A 0.15 0.15"),
        ("Project A 0.85 0.15", "Project B 0.15 0.65"),
    ],
)
def test_quadrant_point_label_or_coordinate_swap_requires_review(
    point_a_text: str,
    point_b_text: str,
) -> None:
    result, _runtime = _reconstruct_quadrant(
        evidence=_quadrant_evidence(
            point_a_text=point_a_text,
            point_b_text=point_b_text,
        )
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_quadrant_cross_record_evidence_reuse_requires_review() -> None:
    ir = deepcopy(QUADRANT_IR)
    ir["points"][1]["evidence_ids"] = ["ocr-point-a"]

    result, _runtime = _reconstruct_quadrant(ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("unsafe_case", ["missing", "outside", "overlap"])
def test_quadrant_invalid_record_bbox_requires_review(unsafe_case: str) -> None:
    ir = deepcopy(QUADRANT_IR)
    if unsafe_case == "missing":
        del ir["points"][0]["bbox"]
    elif unsafe_case == "outside":
        ir["points"][0]["bbox"] = [160, 60, 205, 80]
    else:
        ir["points"][1]["bbox"] = [170, 70, 190, 90]

    result, _runtime = _reconstruct_quadrant(ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_quadrant_slot_swap_is_rejected_by_source_quadrant_location() -> None:
    positions = {
        "Expand": (25, 20, 75, 32),
        "Promote": (105, 20, 145, 32),
        "Review": (55, 120, 95, 134),
        "Improve": (120, 120, 175, 134),
    }

    result, _runtime = _reconstruct_quadrant(
        evidence=_quadrant_evidence(slot_positions=positions)
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Quadrant slot label lacks independent" in warning
        for warning in result.selected.warnings
    )


def test_quadrant_initial_user_edits_can_authorize_slot_labels() -> None:
    positions = (
        (105, 20, 145, 32),
        (25, 20, 75, 32),
        (55, 120, 95, 134),
        (120, 120, 175, 134),
    )
    initial_evidence = [
        VisualEvidence(
            id=f"user-slot-{index}",
            kind="user_edit",
            text=label,
            bbox=bbox,
        )
        for index, (label, bbox) in enumerate(
            zip(
                ("Expand", "Promote", "Review", "Improve"),
                positions,
                strict=True,
            ),
            start=1,
        )
    ]

    result, _runtime = _reconstruct_quadrant(
        evidence=_quadrant_evidence(include_slots=False),
        initial_evidence=initial_evidence,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_quadrant_bboxless_user_edits_cannot_authorize_slot_labels() -> None:
    initial_evidence = [
        VisualEvidence(id=f"user-slot-{index}", kind="user_edit", text=label)
        for index, label in enumerate(
            ("Promote", "Expand", "Improve", "Review"), start=1
        )
    ]

    result, _runtime = _reconstruct_quadrant(
        evidence=_quadrant_evidence(include_slots=False),
        initial_evidence=initial_evidence,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Quadrant slot label lacks independent" in warning
        for warning in result.selected.warnings
    )


def test_quadrant_record_cited_user_edit_cannot_be_reused_for_slot() -> None:
    ir = deepcopy(QUADRANT_IR)
    ir["x_axis"]["evidence_ids"].append("user-expand-cited")
    evidence = [
        item
        for item in _quadrant_evidence()
        if item.id != "ocr-slot-1"
    ]
    initial_evidence = [
        VisualEvidence(
            id="user-expand-cited",
            kind="user_edit",
            text="Expand",
            bbox=(105, 20, 145, 32),
        )
    ]

    result, _runtime = _reconstruct_quadrant(
        ir=ir,
        evidence=evidence,
        initial_evidence=initial_evidence,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Quadrant slot label lacks independent" in warning
        for warning in result.selected.warnings
    )


def test_quadrant_engine_user_edit_cannot_self_authorize_slot_label() -> None:
    evidence = _quadrant_evidence(include_slots=False)
    evidence.extend(
        VisualEvidence(id=f"engine-slot-{index}", kind="user_edit", text=label)
        for index, label in enumerate(
            ("Expand", "Promote", "Review", "Improve"), start=1
        )
    )

    result, _runtime = _reconstruct_quadrant(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("field", ["title", "acc_title", "acc_description"])
@pytest.mark.parametrize("reject_native", [False, True])
def test_quadrant_explicit_metadata_requires_independent_evidence(
    field: str,
    reject_native: bool,
) -> None:
    ir = deepcopy(QUADRANT_IR)
    ir[field] = "Hallucinated metadata"

    result, _runtime = _reconstruct_quadrant(ir=ir, reject_native=reject_native)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("publish_policy", "expected_publish"),
    [
        (PublishPolicy.BEST_EFFORT_VALIDATED, True),
        (PublishPolicy.STRICT_VALIDATED, False),
    ],
)
def test_quadrant_metadata_role_swap_is_warned_and_policy_gated(
    publish_policy: PublishPolicy,
    expected_publish: bool,
) -> None:
    ir = deepcopy(QUADRANT_IR)
    ir["title"], ir["description"] = ir["description"], ir["title"]

    result, _runtime = _reconstruct_quadrant(
        ir=ir,
        publish_policy=publish_policy,
    )

    assert result.selected is not None
    assert result.publish is expected_publish
    assert (result.selected.aggregate_score is not None) is expected_publish
    assert any(
        "explicit metadata uses content-existence attribution" in warning
        for warning in result.selected.warnings
    )
    assert QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING in result.selected.warnings


def test_quadrant_metadata_role_warning_survives_engine_warning_budget() -> None:
    result, _runtime = _reconstruct_quadrant(
        engine_warnings=[
            f"engine noise {index}" for index in range(MAX_OBSERVATION_WARNINGS)
        ]
    )

    assert result.selected is not None
    assert result.publish
    assert len(result.selected.warnings) == MAX_OBSERVATION_WARNINGS
    assert any(
        "explicit metadata uses content-existence attribution" in warning
        for warning in result.selected.warnings
    )
    assert QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING in result.selected.warnings
    assert result.selected.warnings[-1] == (
        "candidate warnings were truncated to the publication metadata budget"
    )


@pytest.mark.integration
def test_best_effort_grade_a_quadrant_is_disclosed_as_experimental_markdown() -> None:
    from marker_mermaid.marker_integration import _result_summary

    result, _runtime = _reconstruct_quadrant()

    assert result.publish
    assert result.grade == "A"
    assert result.selected is not None
    assert result.selected.serialization_stability == "experimental"
    assert any(
        "explicit metadata uses content-existence attribution" in warning
        for warning in result.selected.warnings
    )
    assert reconstruction_markdown(result).startswith(
        "> **Experimental reconstruction:**"
    )
    assert reconstruction_markdown(result, show_warning=False).startswith("```mermaid\n")
    assert _result_summary(
        result,
        publication_snapshot=result.authorized_publication_snapshot(),
    )["stability"] == "experimental"


@pytest.mark.parametrize(
    ("publish_policy", "expected_repair_accepted"),
    [
        (PublishPolicy.BEST_EFFORT_VALIDATED, True),
        (PublishPolicy.STRICT_VALIDATED, False),
    ],
)
def test_quadrant_repair_added_title_is_warned_and_policy_gated(
    publish_policy: PublishPolicy,
    expected_repair_accepted: bool,
) -> None:
    ir = deepcopy(QUADRANT_IR)
    del ir["title"]
    del ir["description"]

    result, _runtime = _reconstruct_quadrant(
        ir=ir,
        repair_engine=_QuadrantTitleRepair(),
        publish_policy=publish_policy,
        review_below_score=0,
    )

    assert result.selected is not None
    assert result.publish
    if expected_repair_accepted:
        assert result.selected.typed_ir["title"] == "Portfolio"
        assert result.selected.repair_history[-1].accepted
        assert any(
            "explicit metadata uses content-existence attribution" in warning
            for warning in result.selected.warnings
        )
    else:
        assert "title" not in result.selected.typed_ir
        assert not result.selected.repair_history[-1].accepted
        assert not any(
            "explicit metadata uses content-existence attribution" in warning
            for warning in result.selected.warnings
        )
    assert QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING in result.selected.warnings


def test_quadrant_initial_user_edit_can_authorize_explicit_accessibility() -> None:
    ir = deepcopy(QUADRANT_IR)
    ir["acc_title"] = "Reviewed title"
    ir["acc_description"] = "Reviewed description"
    initial_evidence = [
        VisualEvidence(id="user-title", kind="user_edit", text="Reviewed title"),
        VisualEvidence(
            id="user-description",
            kind="user_edit",
            text="Reviewed description",
        ),
    ]

    result, _runtime = _reconstruct_quadrant(
        ir=ir,
        initial_evidence=initial_evidence,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize(
    "field",
    ["title", "description", "acc_title", "acc_description"],
)
def test_quadrant_empty_explicit_metadata_is_rejected_before_enrichment(
    field: str,
) -> None:
    ir = deepcopy(QUADRANT_IR)
    ir[field] = ""

    result, runtime = _reconstruct_quadrant(ir=ir)

    assert result.selected is None
    assert not result.publish
    assert not runtime.calls
    assert any(
        failure.stage == "serialization" and failure.error_type == "SerializationError"
        for failure in result.failures
    )


@pytest.mark.parametrize(
    "field",
    ["title", "description", "acc_title", "acc_description"],
)
def test_quadrant_null_metadata_does_not_trigger_strict_role_hold(
    field: str,
) -> None:
    ir = deepcopy(QUADRANT_IR)
    del ir["title"]
    del ir["description"]
    ir[field] = None

    result, _runtime = _reconstruct_quadrant(
        ir=ir,
        publish_policy=PublishPolicy.STRICT_VALIDATED,
        review_below_score=0,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert not any(
        "explicit metadata uses content-existence attribution" in warning
        for warning in result.selected.warnings
    )


def test_quadrant_uncited_numeric_source_record_blocks_publication() -> None:
    evidence = [
        *_quadrant_evidence(),
        VisualEvidence(
            id="ocr-extra-point",
            kind="ocr_token",
            text="Uncited point 0.5 0.5",
            bbox=(85, 85, 115, 100),
        ),
    ]

    result, _runtime = _reconstruct_quadrant(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_quadrant_overlap_budget_exhaustion_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "_MAX_QUADRANT_OVERLAP_COMPARISONS", 0)

    result, _runtime = _reconstruct_quadrant()

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("comparison_budget", "expected_publish"),
    [(50, False), (51, True)],
)
def test_quadrant_comparison_budget_is_shared_across_all_matching_loops(
    monkeypatch,
    comparison_budget: int,
    expected_publish: bool,
) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "_MAX_QUADRANT_OVERLAP_COMPARISONS",
        comparison_budget,
    )

    result, _runtime = _reconstruct_quadrant()

    assert result.selected is not None
    assert result.publish is expected_publish
    assert (result.selected.aggregate_score is not None) is expected_publish


def test_quadrant_empty_independent_requirements_skip_spatial_scan(monkeypatch) -> None:
    ir = deepcopy(QUADRANT_IR)
    del ir["title"]
    del ir["description"]
    ir["quadrants"] = {}
    monkeypatch.setattr(pipeline_module, "_MAX_QUADRANT_OVERLAP_COMPARISONS", 6)

    result, _runtime = _reconstruct_quadrant(
        ir=ir,
        ocr_texts=[
            "Low reach High reach Low confidence High confidence "
            "Project A 0.85 0.65 Project B 0.15 0.15"
        ],
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_quadrant_semantic_repair_cannot_bypass_record_local_binding() -> None:
    result, _runtime = _reconstruct_quadrant(
        repair_engine=_QuadrantPointSwapRepair()
    )

    assert result.selected is not None
    assert result.selected.typed_ir["points"][0]["x"] == 0.85
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted


def test_direct_quadrant_candidate_remains_review_only_without_typed_plan() -> None:
    runtime = _QuadrantRuntime()
    config = MermaidConfig(mode=Mode.MAXIMAL, candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["quadrant"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="quadrant",
                code=(
                    "quadrantChart\n"
                    "    x-axis Low reach --> High reach\n"
                    "    y-axis Low confidence --> High confidence\n"
                    "    Project A: [0.75, 0.75]\n"
                ),
            )
        ],
        evidence=_quadrant_evidence(),
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "direct-quadrant",
        "source.png",
        Image.new("RGB", (200, 200), "white"),
        ocr_texts=["Low reach High reach Low confidence High confidence 0.75 0.75"],
    )

    assert result.selected is not None
    assert result.selected.generation_method == "direct_mermaid"
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Quadrant axis/point association lacks" in warning
        for warning in result.selected.warnings
    )
