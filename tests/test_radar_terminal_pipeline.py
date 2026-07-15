from __future__ import annotations

from copy import deepcopy

import pytest
from PIL import Image

import marker_mermaid.pipeline as pipeline_module
from marker_mermaid.config import MermaidConfig, Mode
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

RADAR_IR = {
    "title": "Model comparison",
    "description": "Two models across three dimensions.",
    "dimensions": [
        {
            "id": "accuracy",
            "label": "Accuracy",
            "bbox": [75, 5, 125, 25],
            "evidence_ids": ["ocr-accuracy"],
        },
        {
            "id": "speed",
            "label": "Speed",
            "bbox": [145, 115, 195, 135],
            "evidence_ids": ["ocr-speed"],
        },
        {
            "id": "safety",
            "label": "Safety",
            "bbox": [5, 115, 55, 135],
            "evidence_ids": ["ocr-safety"],
        },
    ],
    "series": [
        {
            "id": "model-a",
            "label": "Model A",
            "values": [0, 5, 10],
            "bbox": [5, 165, 95, 190],
            "evidence_ids": ["ocr-model-a"],
        },
        {
            "id": "model-b",
            "label": "Model B",
            "values": [10, 7.5, 2.5],
            "bbox": [105, 165, 195, 190],
            "evidence_ids": ["ocr-model-b"],
        },
    ],
}


class _RadarRuntime:
    def __init__(self, *, reject_native: bool = False) -> None:
        self.reject_native = reject_native
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: int) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("radar-beta") and self.reject_native:
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="radar",
                error="forced native rejection",
            )
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=("radar" if code.startswith("radar-beta") else "flowchart-v2"),
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
                "<text>Model comparison Accuracy Speed Safety Model A Model B</text>"
                "</svg>"
            ),
        )

    def close(self) -> None:
        pass


class _RadarValueSwapRepair:
    name = "radar_value_swap"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["series"][0]["values"], typed_ir["series"][1]["values"] = (
            typed_ir["series"][1]["values"],
            typed_ir["series"][0]["values"],
        )
        serialized = serialize_typed_ir_result(
            "radar",
            typed_ir,
            experimental=True,
        )
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _RadarMetadataRepair:
    name = "radar_add_metadata"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["title"] = "Fabricated title"
        typed_ir["description"] = "Fabricated description"
        typed_ir["acc_title"] = "Fabricated title"
        typed_ir["acc_description"] = "Fabricated description"
        serialized = serialize_typed_ir_result(
            "radar",
            typed_ir,
            experimental=True,
        )
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


def _radar_evidence(
    *,
    accuracy_text: str = "Accuracy",
    speed_text: str = "Speed",
    safety_text: str = "Safety",
    model_a_text: str = "Model A 0 5 10",
    model_b_text: str = "Model B 10 7.5 2.5",
    include_metadata: bool = True,
) -> list[VisualEvidence]:
    evidence = [
        VisualEvidence(
            id="ocr-accuracy",
            kind="ocr_token",
            text=accuracy_text,
            bbox=(80, 10, 120, 20),
        ),
        VisualEvidence(
            id="ocr-speed",
            kind="vector_text",
            text=speed_text,
            bbox=(150, 120, 190, 130),
        ),
        VisualEvidence(
            id="ocr-safety",
            kind="ocr_token",
            text=safety_text,
            bbox=(10, 120, 50, 130),
        ),
        VisualEvidence(
            id="ocr-model-a",
            kind="vector_text",
            text=model_a_text,
            bbox=(10, 172, 90, 184),
        ),
        VisualEvidence(
            id="ocr-model-b",
            kind="ocr_token",
            text=model_b_text,
            bbox=(110, 172, 190, 184),
        ),
    ]
    if include_metadata:
        evidence[0:0] = [
            VisualEvidence(
                id="ocr-title",
                kind="ocr_token",
                text="Model comparison",
                bbox=(50, 30, 150, 45),
            ),
            VisualEvidence(
                id="ocr-description",
                kind="vector_text",
                text="Two models across three dimensions.",
                bbox=(20, 65, 180, 85),
            ),
        ]
    return evidence


def _reconstruct_radar(
    *,
    ir: dict[str, object] | None = None,
    evidence: list[VisualEvidence] | None = None,
    reject_native: bool = False,
    ocr_texts: list[str] | None = None,
    repair_engine: object | None = None,
    image_size: tuple[int, int] = (200, 200),
    initial_evidence: list[VisualEvidence] | None = None,
) -> tuple[object, _RadarRuntime]:
    runtime = _RadarRuntime(reject_native=reject_native)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["radar"], scores=[1]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="radar",
                ir=deepcopy(ir or RADAR_IR),
            )
        ],
        evidence=evidence if evidence is not None else _radar_evidence(),
    )
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=repair_engine,
    ).reconstruct(
        "radar-source",
        "source.png",
        Image.new("RGB", image_size, "white"),
        ocr_texts=(
            [
                "Model comparison Accuracy Speed Safety Model A 0 5 10 "
                "Model B 10 7.5 2.5 Two models across three dimensions."
            ]
            if ocr_texts is None
            else ocr_texts
        ),
        evidence=initial_evidence,
    )
    return result, runtime


@pytest.mark.parametrize("reject_native", [False, True])
def test_radar_record_local_evidence_publishes_native_and_fallback(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_radar(reject_native=reject_native)

    assert result.selected is not None
    selected = result.selected
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert selected.aggregate_score is not None, selected.warnings
    assert result.publish
    assert selected.emitted_diagram_type == ("flowchart" if reject_native else "radar")
    assert len(runtime.calls) == (2 if reject_native else 1)


def test_radar_dimension_centroid_allows_off_center_source_crop() -> None:
    result, _runtime = _reconstruct_radar(image_size=(400, 200))

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_radar_top_anchor_allows_small_label_bbox_offset() -> None:
    ir = deepcopy(RADAR_IR)
    ir["dimensions"][0]["bbox"] = [65, 5, 115, 25]
    evidence = _radar_evidence()
    accuracy = next(item for item in evidence if item.id == "ocr-accuracy")
    accuracy.bbox = (70, 10, 110, 20)

    result, _runtime = _reconstruct_radar(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_radar_explicit_metadata_requires_independent_evidence() -> None:
    result, _runtime = _reconstruct_radar(evidence=_radar_evidence(include_metadata=False))

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("Radar title/accTitle lacks" in warning for warning in result.selected.warnings)
    assert any(
        "Radar explicit description/accDescr lacks" in warning
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize(
    "field",
    ["title", "description", "acc_title", "acc_description"],
)
def test_radar_fabricated_metadata_requires_review(field: str) -> None:
    ir = deepcopy(RADAR_IR)
    ir[field] = "Fabricated metadata"

    result, _runtime = _reconstruct_radar(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_radar_initial_user_edits_can_authorize_explicit_metadata() -> None:
    initial_evidence = [
        VisualEvidence(id="user-title", kind="user_edit", text="Model comparison"),
        VisualEvidence(
            id="user-description",
            kind="user_edit",
            text="Two models across three dimensions.",
        ),
    ]

    result, _runtime = _reconstruct_radar(
        evidence=_radar_evidence(include_metadata=False),
        initial_evidence=initial_evidence,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_radar_engine_user_edits_cannot_self_authorize_metadata() -> None:
    evidence = _radar_evidence(include_metadata=False)
    evidence.extend(
        [
            VisualEvidence(
                id="engine-title",
                kind="user_edit",
                text="Model comparison",
            ),
            VisualEvidence(
                id="engine-description",
                kind="user_edit",
                text="Two models across three dimensions.",
            ),
        ]
    )

    result, _runtime = _reconstruct_radar(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_radar_derived_accessibility_defaults_do_not_require_source_metadata() -> None:
    ir = deepcopy(RADAR_IR)
    del ir["title"]
    del ir["description"]

    result, _runtime = _reconstruct_radar(
        ir=ir,
        evidence=_radar_evidence(include_metadata=False),
        ocr_texts=["Accuracy Speed Safety Model A 0 5 10 Model B 10 7.5 2.5"],
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("reject_native", [False, True])
def test_radar_dimension_label_swap_requires_review(reject_native: bool) -> None:
    result, _runtime = _reconstruct_radar(
        evidence=_radar_evidence(
            accuracy_text="Speed",
            speed_text="Accuracy",
        ),
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("reject_native", [False, True])
def test_radar_dimension_record_permutation_requires_review(
    reject_native: bool,
) -> None:
    ir = deepcopy(RADAR_IR)
    ir["dimensions"][0], ir["dimensions"][1] = (
        ir["dimensions"][1],
        ir["dimensions"][0],
    )

    result, _runtime = _reconstruct_radar(
        ir=ir,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("model_a_text", "model_b_text"),
    [
        ("Model B 0 5 10", "Model A 10 7.5 2.5"),
        ("Model A 10 5 0", "Model B 2.5 7.5 10"),
        ("Model A 10 7.5 2.5", "Model B 0 5 10"),
    ],
)
def test_radar_series_name_or_ordered_value_swap_requires_review(
    model_a_text: str,
    model_b_text: str,
) -> None:
    result, _runtime = _reconstruct_radar(
        evidence=_radar_evidence(
            model_a_text=model_a_text,
            model_b_text=model_b_text,
        )
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_radar_cross_record_evidence_reuse_requires_review() -> None:
    ir = deepcopy(RADAR_IR)
    ir["series"][1]["evidence_ids"] = ["ocr-model-a"]

    result, _runtime = _reconstruct_radar(ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("collection", ["dimensions", "series"])
def test_radar_duplicate_source_record_is_isolated(collection: str) -> None:
    ir = deepcopy(RADAR_IR)
    ir[collection][1] = ir[collection][0]

    result, runtime = _reconstruct_radar(ir=ir)

    assert result.selected is None
    assert not result.publish
    assert not runtime.calls
    assert any(failure.stage == "serialization" for failure in result.failures)


def test_radar_uncited_contradiction_at_cited_bbox_requires_review() -> None:
    evidence = _radar_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-accuracy-conflict",
            kind="ocr_token",
            text="Speed",
            bbox=(80, 10, 120, 20),
        )
    )

    result, _runtime = _reconstruct_radar(evidence=evidence)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_radar_same_owner_identical_ocr_vector_observation_is_deduplicated() -> None:
    ir = deepcopy(RADAR_IR)
    ir["dimensions"][0]["evidence_ids"].append("vector-accuracy")
    evidence = _radar_evidence()
    evidence.append(
        VisualEvidence(
            id="vector-accuracy",
            kind="vector_text",
            text="Accuracy",
            bbox=(80, 10, 120, 20),
        )
    )

    result, _runtime = _reconstruct_radar(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_radar_same_owner_spatially_distinct_repetition_is_preserved() -> None:
    ir = deepcopy(RADAR_IR)
    ir["dimensions"][0]["evidence_ids"].append("vector-accuracy-repeat")
    evidence = _radar_evidence()
    evidence.append(
        VisualEvidence(
            id="vector-accuracy-repeat",
            kind="vector_text",
            text="Accuracy",
            bbox=(80, 5, 120, 9),
        )
    )

    result, _runtime = _reconstruct_radar(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    "unsafe_case",
    ["missing", "outside", "overlap"],
)
def test_radar_invalid_record_geometry_requires_review(unsafe_case: str) -> None:
    ir = deepcopy(RADAR_IR)
    if unsafe_case == "missing":
        del ir["dimensions"][0]["bbox"]
    elif unsafe_case == "outside":
        ir["series"][1]["bbox"] = [105, 165, 205, 190]
    else:
        ir["dimensions"][1]["bbox"] = [100, 10, 170, 30]

    result, _runtime = _reconstruct_radar(ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_radar_cited_non_text_evidence_requires_review() -> None:
    evidence = _radar_evidence()
    evidence[-1] = VisualEvidence(
        id="ocr-model-b",
        kind="vlm_observation",
        text="Model B 10 7.5 2.5",
        bbox=(110, 172, 190, 184),
    )

    result, _runtime = _reconstruct_radar(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("constant_name", "limit"),
    [
        ("_MAX_RADAR_ASSOCIATION_REFERENCES", 4),
        ("_MAX_RADAR_RECORD_OVERLAP_COMPARISONS", 25),
        ("_MAX_OCR_REFERENCE_TEXTS", 19),
        ("_MAX_OCR_REFERENCE_CHARS", 1),
        ("_MAX_OCR_REFERENCE_TOKENS", 1),
    ],
)
def test_radar_association_budgets_fail_closed(
    monkeypatch,
    constant_name: str,
    limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, constant_name, limit)

    result, _runtime = _reconstruct_radar()

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("constant_name", "limit"),
    [
        ("_MAX_RADAR_ASSOCIATION_REFERENCES", 5),
        ("_MAX_RADAR_RECORD_OVERLAP_COMPARISONS", 26),
        ("_MAX_OCR_REFERENCE_TEXTS", 20),
    ],
)
def test_radar_association_budget_exact_boundary_publishes(
    monkeypatch,
    constant_name: str,
    limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, constant_name, limit)

    result, _runtime = _reconstruct_radar()

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_radar_uncited_numeric_source_record_blocks_publication() -> None:
    evidence = [
        *_radar_evidence(),
        VisualEvidence(
            id="ocr-extra-series",
            kind="ocr_token",
            text="Uncited 4 4 4",
            bbox=(70, 140, 130, 155),
        ),
    ]

    result, _runtime = _reconstruct_radar(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_radar_semantic_repair_cannot_bypass_record_local_binding() -> None:
    result, _runtime = _reconstruct_radar(repair_engine=_RadarValueSwapRepair())

    assert result.selected is not None
    assert result.selected.typed_ir["series"][0]["values"] == [0, 5, 10]
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted


def test_radar_semantic_repair_cannot_add_unproven_metadata() -> None:
    ir = deepcopy(RADAR_IR)
    del ir["title"]
    del ir["description"]

    result, _runtime = _reconstruct_radar(
        ir=ir,
        evidence=_radar_evidence(include_metadata=False),
        ocr_texts=["Accuracy Speed Safety Model A 0 5 10 Model B 10 7.5 2.5"],
        repair_engine=_RadarMetadataRepair(),
    )

    assert result.selected is not None
    assert "title" not in result.selected.typed_ir
    assert "description" not in result.selected.typed_ir
    assert result.selected.aggregate_score is not None
    assert result.publish
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted


def test_direct_radar_candidate_remains_review_only_without_typed_plan() -> None:
    runtime = _RadarRuntime()
    config = MermaidConfig(mode=Mode.MAXIMAL, candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["radar"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="radar",
                code=(
                    "radar-beta\n"
                    'axis accuracy["Accuracy"], speed["Speed"], safety["Safety"]\n'
                    'curve model_a["Model A"]{0, 5, 10}\n'
                ),
            )
        ],
        evidence=_radar_evidence(),
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "direct-radar",
        "source.png",
        Image.new("RGB", (200, 200), "white"),
        ocr_texts=["Accuracy Speed Safety Model A 0 5 10"],
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Radar dimension/series association lacks" in warning
        for warning in result.selected.warnings
    )
