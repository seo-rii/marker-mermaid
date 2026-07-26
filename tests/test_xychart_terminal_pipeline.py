from __future__ import annotations

from copy import deepcopy

import pytest
from PIL import Image

from marker_mermaid import pipeline as pipeline_module
from marker_mermaid.config import MermaidConfig, Mode, PublishPolicy
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.serializers import serialize_typed_ir_result
from marker_mermaid.validation import CandidateValidator


def _best_effort_config(**values: object) -> MermaidConfig:
    values.setdefault("publish_policy", PublishPolicy.BEST_EFFORT_VALIDATED)
    return MermaidConfig(**values)


XY_IR = {
    "title": "Sales trend",
    "description": "Monthly sales.",
    "x_axis": {
        "label": "Month",
        "categories": ["Jan", "Feb"],
        "bbox": [0, 70, 120, 90],
        "evidence_ids": ["ocr-x-axis"],
    },
    "y_axis": {
        "label": "Sales",
        "min": 0,
        "max": 20,
        "bbox": [0, 20, 20, 70],
        "evidence_ids": ["ocr-y-axis"],
    },
    "series": [
        {
            "kind": "line",
            "values": [5, 10],
            "bbox": [20, 20, 120, 40],
            "evidence_ids": ["ocr-line"],
        },
        {
            "kind": "bar",
            "values": [7, 12],
            "bbox": [20, 45, 120, 65],
            "evidence_ids": ["ocr-bar"],
        },
    ],
}


class _XYRuntime:
    def __init__(self, *, reject_native: bool = False) -> None:
        self.reject_native = reject_native
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: int) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("xychart-beta") and self.reject_native:
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="xychart",
                error="forced native rejection",
            )
        diagram_type = "xychart" if code.startswith("xychart-beta") else "flowchart-v2"
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=diagram_type,
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 100">'
                "<text>Sales trend Month Jan Feb Sales 0 20 line 5 10 bar 7 12</text>"
                "</svg>"
            ),
        )

    def close(self) -> None:
        pass


class _XYSeriesSwapRepair:
    name = "xy_series_swap"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["series"][0]["values"], typed_ir["series"][1]["values"] = (
            typed_ir["series"][1]["values"],
            typed_ir["series"][0]["values"],
        )
        serialized = serialize_typed_ir_result(
            "xychart",
            typed_ir,
            experimental=True,
        )
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


def _xy_evidence(
    *,
    x_text: str = "Month Jan Feb",
    y_text: str = "Sales 0 20",
    line_text: str = "line 5 10",
    bar_text: str = "bar 7 12",
) -> list[VisualEvidence]:
    return [
        VisualEvidence(
            id="ocr-title",
            kind="ocr_token",
            text="Sales trend",
            bbox=(5, 2, 55, 10),
        ),
        VisualEvidence(
            id="ocr-description",
            kind="vector_text",
            text="Monthly sales.",
            bbox=(60, 2, 115, 10),
        ),
        VisualEvidence(
            id="ocr-x-axis",
            kind="ocr_token",
            text=x_text,
            bbox=(5, 75, 115, 85),
        ),
        VisualEvidence(
            id="ocr-y-axis",
            kind="vector_text",
            text=y_text,
            bbox=(2, 25, 18, 65),
        ),
        VisualEvidence(
            id="ocr-line",
            kind="ocr_token",
            text=line_text,
            bbox=(25, 25, 115, 35),
        ),
        VisualEvidence(
            id="ocr-bar",
            kind="vector_text",
            text=bar_text,
            bbox=(25, 50, 115, 60),
        ),
    ]


def _reconstruct_xy(
    *,
    ir: dict[str, object] | None = None,
    evidence: list[VisualEvidence] | None = None,
    initial_evidence: list[VisualEvidence] | None = None,
    reject_native: bool = False,
    ocr_texts: list[str] | None = None,
    repair_engine: object | None = None,
) -> tuple[object, _XYRuntime]:
    runtime = _XYRuntime(reject_native=reject_native)
    config = _best_effort_config(candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["xychart"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="xychart", ir=deepcopy(ir or XY_IR))],
        evidence=evidence if evidence is not None else _xy_evidence(),
    )
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=repair_engine,
    ).reconstruct(
        "xy-source",
        "source.png",
        Image.new("RGB", (120, 100), "white"),
        ocr_texts=(
            ["Sales trend Month Jan Feb Sales 0 20 line 5 10 bar 7 12"]
            if ocr_texts is None
            else ocr_texts
        ),
        evidence=initial_evidence,
    )
    return result, runtime


@pytest.mark.parametrize("reject_native", [False, True])
def test_xy_record_local_evidence_can_publish_native_and_runtime_fallback(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_xy(reject_native=reject_native)

    assert result.selected is not None
    selected = result.selected
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert selected.aggregate_score is not None, selected.warnings
    assert result.publish
    assert selected.emitted_diagram_type == ("flowchart" if reject_native else "xychart")
    assert selected.generated_scene_ir is not None
    assert len(runtime.calls) == (2 if reject_native else 1)


@pytest.mark.parametrize("reject_native", [False, True])
def test_xy_series_value_swap_requires_review_for_native_and_fallback(
    reject_native: bool,
) -> None:
    result, _runtime = _reconstruct_xy(
        evidence=_xy_evidence(line_text="line 10 5"),
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "XY axis/series/point association conflicts with source evidence" in warning
        for warning in result.selected.warnings
    )


def test_xy_category_order_swap_requires_review() -> None:
    result, _runtime = _reconstruct_xy(evidence=_xy_evidence(x_text="Month Feb Jan"))

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_xy_series_swap_requires_review_even_when_global_numbers_match() -> None:
    result, _runtime = _reconstruct_xy(
        evidence=_xy_evidence(line_text="line 7 12", bar_text="bar 5 10")
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("first_text", "second_text", "expected_numeric"),
    [("0 5", "10 10", 1), ("10 5", "0 10", 0)],
)
@pytest.mark.parametrize("reject_native", [False, True])
def test_xy_explicit_point_x_binding_is_record_local(
    first_text: str,
    second_text: str,
    expected_numeric: int,
    reject_native: bool,
) -> None:
    ir = {
        "x_axis": {
            "label": "Time",
            "min": 0,
            "max": 10,
            "bbox": [0, 70, 120, 90],
            "evidence_ids": ["ocr-x-axis"],
        },
        "y_axis": {
            "label": "Load",
            "min": 0,
            "max": 20,
            "bbox": [0, 20, 20, 70],
            "evidence_ids": ["ocr-y-axis"],
        },
        "series": [
            {
                "kind": "line",
                "bbox": [20, 20, 120, 65],
                "evidence_ids": ["ocr-kind"],
                "points": [
                    {
                        "x": 0,
                        "y": 5,
                        "bbox": [20, 20, 60, 40],
                        "evidence_ids": ["ocr-point-1"],
                    },
                    {
                        "x": 10,
                        "y": 10,
                        "bbox": [60, 40, 120, 65],
                        "evidence_ids": ["ocr-point-2"],
                    },
                ],
            }
        ],
    }
    evidence = [
        VisualEvidence(
            id="ocr-x-axis",
            kind="ocr_token",
            text="Time 0 10",
            bbox=(5, 75, 115, 85),
        ),
        VisualEvidence(
            id="ocr-y-axis",
            kind="vector_text",
            text="Load 0 20",
            bbox=(2, 25, 18, 65),
        ),
        VisualEvidence(
            id="ocr-kind",
            kind="ocr_token",
            text="line",
            bbox=(22, 22, 40, 30),
        ),
        VisualEvidence(
            id="ocr-point-1",
            kind="ocr_token",
            text=first_text,
            bbox=(30, 30, 50, 38),
        ),
        VisualEvidence(
            id="ocr-point-2",
            kind="vector_text",
            text=second_text,
            bbox=(70, 50, 110, 60),
        ),
    ]

    result, _runtime = _reconstruct_xy(
        ir=ir,
        evidence=evidence,
        ocr_texts=[f"Time 0 10 Load 0 20 line {first_text} {second_text}"],
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == expected_numeric
    if expected_numeric:
        assert result.selected.aggregate_score is not None, result.selected.warnings
        assert result.publish
    else:
        assert result.selected.aggregate_score is None
        assert not result.publish


def test_xy_cross_record_evidence_reuse_requires_review() -> None:
    ir = deepcopy(XY_IR)
    ir["series"][1]["evidence_ids"] = ["ocr-line"]

    result, _runtime = _reconstruct_xy(ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "lacks candidate-authorized spatial OCR/vector evidence" in warning
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize("unsafe_bbox", [None, [0, 70, 121, 90]])
def test_xy_unbounded_or_missing_record_bbox_requires_review(
    unsafe_bbox: list[int] | None,
) -> None:
    ir = deepcopy(XY_IR)
    if unsafe_bbox is None:
        del ir["x_axis"]["bbox"]
    else:
        ir["x_axis"]["bbox"] = unsafe_bbox

    result, _runtime = _reconstruct_xy(ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "lacks candidate-authorized spatial OCR/vector evidence" in warning
        for warning in result.selected.warnings
    )


def test_xy_semantic_repair_cannot_bypass_record_local_binding() -> None:
    result, _runtime = _reconstruct_xy(repair_engine=_XYSeriesSwapRepair())

    assert result.selected is not None
    assert result.selected.typed_ir["series"][0]["values"] == [5, 10]
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted


@pytest.mark.parametrize("field", ["title", "acc_title", "acc_description"])
@pytest.mark.parametrize("reject_native", [False, True])
def test_xy_explicit_title_and_accessibility_require_independent_evidence(
    field: str,
    reject_native: bool,
) -> None:
    ir = deepcopy(XY_IR)
    ir[field] = "Hallucinated metadata"

    result, _runtime = _reconstruct_xy(ir=ir, reject_native=reject_native)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_xy_initial_user_edit_can_authorize_explicit_accessibility_text() -> None:
    ir = deepcopy(XY_IR)
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

    result, _runtime = _reconstruct_xy(ir=ir, initial_evidence=initial_evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_xy_engine_user_edit_cannot_self_authorize_accessibility() -> None:
    ir = deepcopy(XY_IR)
    ir["acc_title"] = "Engine title"
    evidence = [
        *_xy_evidence(),
        VisualEvidence(id="engine-edit", kind="user_edit", text="Engine title"),
    ]

    result, _runtime = _reconstruct_xy(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "XY title/accTitle lacks independent candidate-authorized" in warning
        for warning in result.selected.warnings
    )


def test_xy_accessibility_overlap_budget_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_module, "_MAX_XY_RECORD_OVERLAP_COMPARISONS", 1)

    result, _runtime = _reconstruct_xy()

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "XY title/accTitle lacks independent candidate-authorized" in warning
        for warning in result.selected.warnings
    )


def test_direct_xy_candidate_remains_review_only_without_typed_plan() -> None:
    runtime = _XYRuntime()
    config = _best_effort_config(mode=Mode.MAXIMAL, candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["xychart"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="xychart",
                code=(
                    "xychart-beta\n"
                    '    x-axis ["Jan", "Feb"]\n'
                    "    y-axis 0 --> 20\n"
                    "    line [5, 10]\n"
                ),
            )
        ],
        evidence=_xy_evidence(),
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "direct-xy",
        "source.png",
        Image.new("RGB", (120, 100), "white"),
        ocr_texts=["Month Jan Feb Sales 0 20 line 5 10"],
    )

    assert result.selected is not None
    assert result.selected.generation_method == "direct_mermaid"
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "XY axis/series/point association lacks" in warning for warning in result.selected.warnings
    )
