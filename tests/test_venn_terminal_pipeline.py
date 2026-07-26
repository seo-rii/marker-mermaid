from __future__ import annotations

from copy import deepcopy

import pytest
from PIL import Image

import marker_mermaid.pipeline as pipeline_module
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
from marker_mermaid.serializers import (
    SerializationError,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.validation import CandidateValidator


def _best_effort_config(**values: object) -> MermaidConfig:
    values.setdefault("publish_policy", PublishPolicy.BEST_EFFORT_VALIDATED)
    return MermaidConfig(**values)


VENN_IR = {
    "sets": [
        {
            "id": "A",
            "label": "Buyers",
            "value": 10,
            "bbox": [5, 10, 120, 110],
            "evidence_ids": ["ocr-a", "contour-a"],
        },
        {
            "id": "B",
            "label": "Members",
            "value": 8,
            "bbox": [80, 10, 195, 110],
            "evidence_ids": ["ocr-b", "contour-b"],
        },
    ],
    "intersections": [
        {
            "id": "both",
            "sets": ["A", "B"],
            "label": "Both",
            "value": 3,
            "bbox": [80, 35, 120, 90],
            "evidence_ids": ["ocr-both", "contour-both"],
        }
    ],
}


class _VennRuntime:
    def __init__(self, *, reject_native: bool = False) -> None:
        self.reject_native = reject_native
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("venn-beta") and self.reject_native:
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="venn",
                error="forced native Venn rejection",
            )
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=("venn" if code.startswith("venn-beta") else "flowchart-v2"),
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 120">'
                "<text>Buyers 10 Members 8 Both 3</text>"
                "</svg>"
            ),
        )

    def close(self) -> None:
        pass


class _VennValueSwapRepair:
    name = "venn_value_swap"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        left, right = typed_ir["sets"]
        left["value"], right["value"] = right["value"], left["value"]
        serialized = serialize_typed_ir_result("venn", typed_ir, experimental=True)
        return RepairProposal(code=serialized.code, operation=self.name, typed_ir=typed_ir)


class _VennMembershipSwapRepair:
    name = "venn_membership_swap"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["intersections"][0]["sets"] = ["A", "C"]
        serialized = serialize_typed_ir_result("venn", typed_ir, experimental=True)
        return RepairProposal(code=serialized.code, operation=self.name, typed_ir=typed_ir)


class _VennTerminalLabelRepair:
    name = "venn_terminal_label"

    def __init__(self, *, exact_empty_metadata: bool = False) -> None:
        self.exact_empty_metadata = exact_empty_metadata

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        first_set = typed_ir["sets"][0]
        first_set["label"] = "Verified Buyers"
        first_set["evidence_ids"] = ["ocr-a-correct", "ocr-a-value", "contour-a"]
        if self.exact_empty_metadata:
            typed_ir.update(
                {
                    "title": "",
                    "description": "",
                    "acc_title": "",
                    "acc_description": "",
                }
            )
        else:
            typed_ir.pop("acc_title", None)
            typed_ir.pop("acc_description", None)
        if candidate.emitted_diagram_type == "flowchart":
            serialized = serialize_runtime_fallback_result(
                "venn",
                typed_ir,
                experimental=True,
            )
            assert serialized is not None
        else:
            serialized = serialize_typed_ir_result("venn", typed_ir, experimental=True)
        return RepairProposal(code=serialized.code, operation=self.name, typed_ir=typed_ir)


class _VennIntrinsicFallbackRepair:
    name = "venn_intrinsic_fallback"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        del typed_ir["sets"][1]["value"]
        serialized = serialize_typed_ir_result("venn", typed_ir, experimental=True)
        return RepairProposal(code=serialized.code, operation=self.name, typed_ir=typed_ir)


class _VennMetadataRepair:
    name = "venn_metadata_injection"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        if candidate.emitted_diagram_type == "flowchart":
            typed_ir["acc_title"] = "Fabricated terminal review"
            serialized = serialize_runtime_fallback_result(
                "venn",
                typed_ir,
                experimental=True,
            )
            assert serialized is not None
        else:
            typed_ir["title"] = "Fabricated terminal review"
            serialized = serialize_typed_ir_result("venn", typed_ir, experimental=True)
        return RepairProposal(code=serialized.code, operation=self.name, typed_ir=typed_ir)


class _VennInvalidRawMetadataRepair:
    name = "venn_invalid_raw_metadata"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["acc_title"] = "Invalid\nmetadata"
        return RepairProposal(
            code=f"{candidate.mermaid_code.rstrip()}\n%% invalid raw metadata repair\n",
            operation=self.name,
            typed_ir=typed_ir,
        )


class _PromptOmittingVennEngine(JsonFixtureEngine):
    name = "prompt_omitting_venn_fixture"
    fusion_source = "vlm"

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(
            {"ocr-a", "ocr-b", "ocr-both", "contour-a", "contour-b"}
        )
        return observation


class _SameResponseVennEngine(JsonFixtureEngine):
    name = "same_response_venn_fixture"
    fusion_source = "vlm"

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(set())
        return observation


class _MetadataPromptOmittingVennEngine(JsonFixtureEngine):
    name = "metadata_prompt_omitting_venn_fixture"
    fusion_source = "vlm"

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(
            {
                "ocr-a",
                "ocr-b",
                "ocr-both",
                "contour-a",
                "contour-b",
                "contour-both",
            }
        )
        return observation


def _venn_evidence(
    *,
    a_text: str = "Buyers: 10",
    b_text: str = "Members value 8",
    both_text: str = "Both (value: 3)",
) -> list[VisualEvidence]:
    return [
        VisualEvidence(
            id="ocr-a",
            kind="ocr_token",
            text=a_text,
            bbox=(10, 20, 50, 30),
        ),
        VisualEvidence(
            id="ocr-b",
            kind="vector_text",
            text=b_text,
            bbox=(145, 20, 190, 30),
        ),
        VisualEvidence(
            id="ocr-both",
            kind="ocr_token",
            text=both_text,
            bbox=(85, 50, 115, 60),
        ),
        VisualEvidence(id="contour-a", kind="contour", bbox=(5, 10, 120, 110)),
        VisualEvidence(id="contour-b", kind="contour", bbox=(80, 10, 195, 110)),
        VisualEvidence(id="contour-both", kind="contour", bbox=(80, 35, 120, 90)),
    ]


def _evidence_by_id(evidence: list[VisualEvidence], evidence_id: str) -> VisualEvidence:
    return next(item for item in evidence if item.id == evidence_id)


def _venn_metadata_evidence(
    evidence_id: str,
    text: str,
    *,
    bbox: tuple[float, float, float, float] | None = (5, 0, 95, 8),
    kind: str = "ocr_token",
) -> VisualEvidence:
    return VisualEvidence(id=evidence_id, kind=kind, text=text, bbox=bbox)


def _intrinsic_venn_fallback_ir(
    **metadata: str,
) -> tuple[dict[str, object], list[VisualEvidence]]:
    ir = deepcopy(VENN_IR)
    del ir["sets"][1]["value"]
    ir.update(metadata)
    return ir, _venn_evidence(b_text="Members")


def _nested_venn_ir_and_evidence() -> tuple[dict[str, object], list[VisualEvidence]]:
    ir: dict[str, object] = {
        "sets": [
            {
                "id": "A",
                "label": "Set A",
                "value": 10,
                "bbox": [0, 0, 145, 105],
                "evidence_ids": ["ocr-set-a", "contour-set-a"],
            },
            {
                "id": "B",
                "label": "Set B",
                "value": 9,
                "bbox": [55, 0, 200, 105],
                "evidence_ids": ["ocr-set-b", "contour-set-b"],
            },
            {
                "id": "C",
                "label": "Set C",
                "value": 8,
                "bbox": [25, 20, 175, 120],
                "evidence_ids": ["ocr-set-c", "contour-set-c"],
            },
        ],
        "intersections": [
            {
                "id": "ab",
                "sets": ["A", "B"],
                "label": "AB",
                "value": 4,
                "bbox": [55, 10, 145, 95],
                "evidence_ids": ["ocr-ab", "contour-ab"],
            },
            {
                "id": "ac",
                "sets": ["A", "C"],
                "label": "AC",
                "value": 3,
                "bbox": [25, 20, 140, 105],
                "evidence_ids": ["ocr-ac", "contour-ac"],
            },
            {
                "id": "bc",
                "sets": ["B", "C"],
                "label": "BC",
                "value": 2,
                "bbox": [60, 20, 175, 105],
                "evidence_ids": ["ocr-bc", "contour-bc"],
            },
            {
                "id": "abc",
                "sets": ["A", "B", "C"],
                "label": "ABC",
                "value": 1,
                "bbox": [60, 30, 140, 90],
                "evidence_ids": ["ocr-abc", "contour-abc"],
            },
        ],
    }
    evidence = [
        VisualEvidence(id="ocr-set-a", kind="ocr_token", text="Set A: 10", bbox=(5, 2, 30, 8)),
        VisualEvidence(id="ocr-set-b", kind="vector_text", text="Set B: 9", bbox=(165, 2, 195, 8)),
        VisualEvidence(id="ocr-set-c", kind="ocr_token", text="Set C: 8", bbox=(28, 110, 55, 118)),
        VisualEvidence(id="ocr-ab", kind="ocr_token", text="AB: 4", bbox=(60, 12, 85, 18)),
        VisualEvidence(id="ocr-ac", kind="vector_text", text="AC: 3", bbox=(28, 22, 50, 28)),
        VisualEvidence(id="ocr-bc", kind="ocr_token", text="BC: 2", bbox=(150, 22, 172, 28)),
        VisualEvidence(id="ocr-abc", kind="vector_text", text="ABC: 1", bbox=(80, 40, 120, 50)),
        VisualEvidence(id="contour-set-a", kind="contour", bbox=(0, 0, 145, 105)),
        VisualEvidence(id="contour-set-b", kind="contour", bbox=(55, 0, 200, 105)),
        VisualEvidence(id="contour-set-c", kind="contour", bbox=(25, 20, 175, 120)),
        VisualEvidence(id="contour-ab", kind="contour", bbox=(55, 10, 145, 95)),
        VisualEvidence(id="contour-ac", kind="contour", bbox=(25, 20, 140, 105)),
        VisualEvidence(id="contour-bc", kind="contour", bbox=(60, 20, 175, 105)),
        VisualEvidence(id="contour-abc", kind="contour", bbox=(60, 30, 140, 90)),
    ]
    return ir, evidence


def _reconstruct_venn(
    *,
    ir: dict[str, object] | None = None,
    evidence: list[VisualEvidence] | None = None,
    reject_native: bool = False,
    repair_engine: object | None = None,
    engine_type: type[JsonFixtureEngine] = JsonFixtureEngine,
    evidence_as_prior: bool = False,
    evaluation_mode: Mode = Mode.EXTENDED,
) -> tuple[object, _VennRuntime]:
    runtime = _VennRuntime(reject_native=reject_native)
    config = _best_effort_config(candidate_count=1, publish_min_score=0)
    config.mode = evaluation_mode
    active_evidence = evidence if evidence is not None else _venn_evidence()
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["venn"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="venn", ir=deepcopy(ir or VENN_IR))],
        evidence=[] if evidence_as_prior else active_evidence,
    )
    result = ReconstructionPipeline(
        config,
        [engine_type(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=repair_engine,
    ).reconstruct(
        "venn-source",
        "source.png",
        Image.new("RGB", (200, 120), "white"),
        evidence=active_evidence if evidence_as_prior else None,
    )
    return result, runtime


@pytest.mark.parametrize("reject_native", [False, True])
@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
@pytest.mark.parametrize(
    "value",
    [" ", "Visible\nmetadata"],
    ids=["whitespace", "newline"],
)
def test_venn_invalid_raw_metadata_never_reaches_either_runtime_terminal(
    field: str,
    reject_native: bool,
    value: str,
) -> None:
    ir = deepcopy(VENN_IR)
    ir[field] = value

    result, runtime = _reconstruct_venn(ir=ir, reject_native=reject_native)

    assert result.selected is None
    assert not result.publish
    assert runtime.calls == []
    assert any(
        failure.stage == "serialization" and failure.error_type == "SerializationError"
        for failure in result.failures
    )


@pytest.mark.parametrize("reject_native", [False, True])
def test_venn_exact_empty_metadata_derives_defaults_for_both_terminals(
    reject_native: bool,
) -> None:
    ir = {
        **deepcopy(VENN_IR),
        "title": "",
        "description": "",
        "acc_title": "",
        "acc_description": "",
    }

    result, _runtime = _reconstruct_venn(ir=ir, reject_native=reject_native)

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == ("flowchart" if reject_native else "venn")
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    if reject_native:
        assert "accTitle: Venn reconstruction" in result.selected.mermaid_code
    else:
        assert not any(line.strip().startswith("title ") for line in result.selected.mermaid_code)


def test_venn_pipeline_validates_raw_metadata_before_accessibility_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def reject_raw_metadata(ir: object) -> None:
        del ir
        calls.append("validate")
        raise SerializationError("invalid raw Venn metadata")

    def unexpected_enrichment(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        calls.append("enrich")
        raise AssertionError("accessibility enrichment ran before validation")

    monkeypatch.setattr(
        pipeline_module,
        "validated_venn_accessibility_ir",
        reject_raw_metadata,
    )
    monkeypatch.setattr(pipeline_module, "enrich_accessibility_ir", unexpected_enrichment)

    result, runtime = _reconstruct_venn()

    assert result.selected is None
    assert not result.publish
    assert runtime.calls == []
    assert calls == ["validate"]
    assert any(
        failure.stage == "serialization" and failure.error_type == "SerializationError"
        for failure in result.failures
    )


@pytest.mark.parametrize("reject_native", [False, True])
def test_venn_record_local_values_publish_native_and_fallback(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_venn(reject_native=reject_native)

    assert result.selected is not None
    selected = result.selected
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert selected.aggregate_score is not None, selected.warnings
    assert result.publish
    assert selected.emitted_diagram_type == ("flowchart" if reject_native else "venn")
    assert len(runtime.calls) == (2 if reject_native else 1)


def test_venn_record_bbox_overlap_is_not_an_ambiguity_by_itself() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"][1]["bbox"] = [5, 10, 195, 110]
    evidence = _venn_evidence()
    _evidence_by_id(evidence, "contour-b").bbox = (5, 10, 195, 110)

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("reject_native", [False, True])
def test_venn_equal_declared_record_bboxes_publish_native_and_fallback(
    reject_native: bool,
) -> None:
    ir = deepcopy(VENN_IR)
    for record in [*ir["sets"], *ir["intersections"]]:
        record["bbox"] = [5, 10, 195, 110]
    evidence = _venn_evidence()
    for evidence_id in ("contour-a", "contour-b", "contour-both"):
        _evidence_by_id(evidence, evidence_id).bbox = (5, 10, 195, 110)

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("reject_native", [False, True])
def test_venn_nested_higher_order_intersection_geometry_publishes(
    reject_native: bool,
) -> None:
    ir, evidence = _nested_venn_ir_and_evidence()

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_venn_higher_order_intersection_must_stay_inside_every_explicit_subset() -> None:
    ir, evidence = _nested_venn_ir_and_evidence()
    ir["intersections"][3]["bbox"] = [55, 30, 140, 90]
    _evidence_by_id(evidence, "contour-abc").bbox = (55, 30, 140, 90)

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_declared_member_outside_intersection_geometry_requires_review() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"][1]["bbox"] = [130, 10, 195, 110]
    evidence = _venn_evidence()
    _evidence_by_id(evidence, "contour-b").bbox = (130, 10, 195, 110)

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in result.selected.warnings
    )


def test_venn_membership_swap_requires_review() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"].append(
        {
            "id": "C",
            "label": "Customers",
            "value": 7,
            "bbox": [130, 35, 195, 110],
            "evidence_ids": ["ocr-c", "contour-c"],
        }
    )
    ir["intersections"][0]["sets"] = ["A", "C"]
    evidence = _venn_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-c",
            kind="ocr_token",
            text="Customers: 7",
            bbox=(135, 40, 180, 50),
        )
    )
    evidence.append(VisualEvidence(id="contour-c", kind="contour", bbox=(130, 35, 195, 110)))

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_undeclared_containing_set_is_ambiguous() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"].append(
        {
            "id": "C",
            "label": "Customers",
            "value": 7,
            "bbox": [70, 30, 130, 100],
            "evidence_ids": ["ocr-c", "contour-c"],
        }
    )
    evidence = _venn_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-c",
            kind="ocr_token",
            text="Customers: 7",
            bbox=(72, 32, 78, 38),
        )
    )
    evidence.append(VisualEvidence(id="contour-c", kind="contour", bbox=(70, 30, 130, 100)))

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("reject_native", [False, True])
def test_venn_swapped_values_with_same_global_numbers_require_review(
    reject_native: bool,
) -> None:
    result, _runtime = _reconstruct_venn(
        evidence=_venn_evidence(a_text="Buyers: 8", b_text="Members value 10"),
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection association conflicts" in warning
        for warning in result.selected.warnings
    )


def test_venn_unlabeled_intersection_requires_only_its_explicit_value() -> None:
    ir = deepcopy(VENN_IR)
    del ir["intersections"][0]["label"]
    evidence = _venn_evidence(both_text="value: 3")

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_venn_missing_values_remain_label_only_without_fabricated_numbers() -> None:
    ir = deepcopy(VENN_IR)
    del ir["sets"][1]["value"]
    del ir["intersections"][0]["value"]
    evidence = _venn_evidence(b_text="Members", both_text="Both")

    result, runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert len(runtime.calls) == 1
    assert "Members (value:" not in runtime.calls[0]
    assert "Both (value:" not in runtime.calls[0]


def test_venn_intersection_without_label_or_value_requires_review() -> None:
    ir = deepcopy(VENN_IR)
    del ir["intersections"][0]["label"]
    del ir["intersections"][0]["value"]
    evidence = _venn_evidence(both_text="A and B")

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        "missing-record-box",
        "zero-area-record-box",
        "outside-record-box",
        "evidence-outside-owner",
        "nonfinite-evidence",
        "missing-evidence",
        "empty-evidence",
    ],
)
def test_venn_missing_geometry_or_evidence_requires_review(unsafe: str) -> None:
    ir = deepcopy(VENN_IR)
    evidence = _venn_evidence()
    first_set = ir["sets"][0]
    if unsafe == "missing-record-box":
        del first_set["bbox"]
    elif unsafe == "zero-area-record-box":
        first_set["bbox"] = [5, 10, 5, 110]
    elif unsafe == "outside-record-box":
        first_set["bbox"] = [5, 10, 205, 110]
    elif unsafe == "evidence-outside-owner":
        evidence[0].bbox = (125, 20, 150, 30)
    elif unsafe == "nonfinite-evidence":
        evidence[0].bbox = (10, 20, float("nan"), 30)
    elif unsafe == "missing-evidence":
        first_set["evidence_ids"] = ["missing"]
    else:
        first_set["evidence_ids"] = []

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in result.selected.warnings
    )


def test_venn_cross_owner_evidence_id_reuse_requires_review() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"][1]["bbox"] = [5, 10, 195, 110]
    ir["sets"][1]["evidence_ids"] = ["ocr-a", "contour-b"]
    evidence = _venn_evidence()
    _evidence_by_id(evidence, "contour-b").bbox = (5, 10, 195, 110)

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_cross_owner_contour_id_reuse_requires_review() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"][1]["bbox"] = [5, 10, 120, 110]
    ir["sets"][1]["evidence_ids"] = ["ocr-b", "contour-a"]
    evidence = _venn_evidence()
    _evidence_by_id(evidence, "ocr-b").bbox = (60, 20, 100, 30)

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_cross_owner_observation_reuse_requires_review() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"][1]["label"] = "Buyers"
    ir["sets"][1]["value"] = 10
    ir["sets"][1]["bbox"] = [5, 10, 195, 110]
    evidence = _venn_evidence(b_text="Buyers: 10")
    evidence[1].bbox = evidence[0].bbox
    _evidence_by_id(evidence, "contour-b").bbox = (5, 10, 195, 110)

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_same_bbox_contradictory_text_requires_review() -> None:
    evidence = _venn_evidence()
    evidence.append(
        VisualEvidence(
            id="vector-a-conflict",
            kind="vector_text",
            text="Buyers: 99",
            bbox=evidence[0].bbox,
        )
    )

    result, _runtime = _reconstruct_venn(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in result.selected.warnings
    )


def test_venn_same_owner_identical_ocr_vector_observation_is_deduplicated() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"][0]["evidence_ids"].append("vector-a")
    evidence = _venn_evidence()
    evidence.append(
        VisualEvidence(
            id="vector-a",
            kind="vector_text",
            text="Buyers: 10",
            bbox=evidence[0].bbox,
        )
    )

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_venn_record_bbox_requires_an_exact_cited_contour() -> None:
    ir = deepcopy(VENN_IR)
    for record in [*ir["sets"], *ir["intersections"]]:
        record["evidence_ids"] = [
            evidence_id
            for evidence_id in record["evidence_ids"]
            if not evidence_id.startswith("contour-")
        ]

    result, _runtime = _reconstruct_venn(ir=ir)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_contour_bbox_must_exactly_match_its_source_record() -> None:
    evidence = _venn_evidence()
    _evidence_by_id(evidence, "contour-a").bbox = (5, 10, 119, 110)

    result, _runtime = _reconstruct_venn(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_coordinated_membership_and_bbox_rewrite_cannot_reuse_old_contour() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"].append(
        {
            "id": "C",
            "label": "Customers",
            "value": 7,
            "bbox": [0, 35, 75, 100],
            "evidence_ids": ["ocr-c", "contour-c"],
        }
    )
    intersection = ir["intersections"][0]
    intersection["sets"] = ["A", "C"]
    intersection["bbox"] = [20, 40, 60, 80]
    evidence = _venn_evidence()
    _evidence_by_id(evidence, "ocr-both").bbox = (25, 50, 55, 60)
    evidence.extend(
        [
            VisualEvidence(
                id="ocr-c",
                kind="ocr_token",
                text="Customers: 7",
                bbox=(2, 40, 18, 48),
            ),
            VisualEvidence(id="contour-c", kind="contour", bbox=(0, 35, 75, 100)),
        ]
    )

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_uncited_extra_numeric_observation_breaks_global_exactness() -> None:
    evidence = _venn_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-extra",
            kind="ocr_token",
            text="99",
            bbox=(5, 112, 20, 118),
        )
    )

    result, _runtime = _reconstruct_venn(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_candidate_authority_omission_requires_review() -> None:
    result, _runtime = _reconstruct_venn(
        engine_type=_PromptOmittingVennEngine,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in result.selected.warnings
    )


def test_venn_same_response_contours_have_no_prompt_publication_authority() -> None:
    result, _runtime = _reconstruct_venn(engine_type=_SameResponseVennEngine)

    assert result.selected is not None
    assert result.selected.publication_evidence_authority_ids == frozenset()
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_native_venn_requires_only_its_visible_explicit_title() -> None:
    ir = {
        **deepcopy(VENN_IR),
        "title": "Visible 2025 title",
        "acc_title": "Hidden accessible title",
        "description": "Hidden description",
        "acc_description": "Hidden accessible description",
    }
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence("meta-visible-title", "Visible 2025 title"),
    ]

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "venn"
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_native_venn_unproven_explicit_title_requires_review() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Unproven visible title"}

    result, _runtime = _reconstruct_venn(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title role" in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("field", ["description", "acc_title", "acc_description"])
def test_native_venn_exempts_unsupported_explicit_metadata(field: str) -> None:
    ir = {**deepcopy(VENN_IR), field: f"Unsupported native {field}"}

    result, _runtime = _reconstruct_venn(ir=ir)

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "venn"
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert not any("terminal title role" in warning for warning in result.selected.warnings)
    assert not any("terminal description role" in warning for warning in result.selected.warnings)


def test_native_venn_does_not_subtract_hidden_accessibility_numbers() -> None:
    ir = {**deepcopy(VENN_IR), "acc_title": "Hidden 2029 accessibility title"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "hidden-acc-title",
            "Hidden 2029 accessibility title",
        ),
    ]

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert not any("terminal title role" in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
@pytest.mark.parametrize("terminal", ["forced-fallback", "intrinsic-fallback"])
def test_venn_fallback_requires_effective_explicit_metadata_proof(
    field: str,
    terminal: str,
) -> None:
    value = f"Fabricated {field} metadata"
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_venn_fallback_ir(**{field: value})
        reject_native = False
    else:
        ir = {**deepcopy(VENN_IR), field: value}
        evidence = _venn_evidence()
        reject_native = True

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.aggregate_score is None
    assert not result.publish
    role = "title role" if field in {"title", "acc_title"} else "description role"
    assert any(role in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("terminal", ["forced-fallback", "intrinsic-fallback"])
def test_venn_fallback_proves_only_effective_shadowing_metadata(terminal: str) -> None:
    metadata = {
        "title": "Shadowed legacy title",
        "acc_title": "Effective accessible title",
        "description": "Shadowed legacy description",
        "acc_description": "Effective accessible description",
    }
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_venn_fallback_ir(**metadata)
        reject_native = False
    else:
        ir = {**deepcopy(VENN_IR), **metadata}
        evidence = _venn_evidence()
        reject_native = True
    evidence.extend(
        [
            _venn_metadata_evidence("meta-effective-title", "Effective accessible title"),
            _venn_metadata_evidence(
                "meta-effective-description",
                "Effective accessible description",
                bbox=(105, 0, 195, 8),
                kind="vector_text",
            ),
        ]
    )

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("terminal", ["native", "forced-fallback", "intrinsic-fallback"])
def test_venn_deterministic_accessibility_defaults_need_no_proof(terminal: str) -> None:
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_venn_fallback_ir()
        reject_native = False
    else:
        ir = deepcopy(VENN_IR)
        evidence = _venn_evidence()
        reject_native = terminal == "forced-fallback"

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert not any("terminal title role" in warning for warning in result.selected.warnings)
    assert not any("terminal description role" in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("terminal", ["native", "forced-fallback", "intrinsic-fallback"])
def test_venn_identical_effective_title_fields_collapse_to_one_owner(terminal: str) -> None:
    metadata = {"title": "Shared effective title", "acc_title": "Shared effective title"}
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_venn_fallback_ir(**metadata)
        reject_native = False
    else:
        ir = {**deepcopy(VENN_IR), **metadata}
        evidence = _venn_evidence()
        reject_native = terminal == "forced-fallback"
    evidence.append(_venn_metadata_evidence("meta-shared-title", "Shared effective title"))

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("terminal", ["forced-fallback", "intrinsic-fallback"])
def test_venn_fallback_title_and_description_roles_need_distinct_proofs(
    terminal: str,
) -> None:
    metadata = {
        "acc_title": "Shared terminal metadata",
        "acc_description": "Shared terminal metadata",
    }
    if terminal == "intrinsic-fallback":
        ir, base_evidence = _intrinsic_venn_fallback_ir(**metadata)
        reject_native = False
    else:
        ir = {**deepcopy(VENN_IR), **metadata}
        base_evidence = _venn_evidence()
        reject_native = True
    one_proof = [
        *base_evidence,
        _venn_metadata_evidence("meta-shared", "Shared terminal metadata"),
    ]

    rejected, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=one_proof,
        reject_native=reject_native,
    )

    assert rejected.selected is not None
    assert rejected.selected.aggregate_score is None
    assert not rejected.publish

    two_proofs = [
        *one_proof,
        _venn_metadata_evidence(
            "meta-shared-description",
            "Shared terminal metadata",
            bbox=(105, 0, 195, 8),
            kind="vector_text",
        ),
    ]
    accepted, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=two_proofs,
        reject_native=reject_native,
    )

    assert accepted.selected is not None
    assert accepted.selected.aggregate_score is not None, accepted.selected.warnings
    assert accepted.publish


@pytest.mark.parametrize("field", ["description", "acc_description"])
@pytest.mark.parametrize("terminal", ["forced-fallback", "intrinsic-fallback"])
def test_venn_fallback_notice_only_description_override_fails_closed(
    field: str,
    terminal: str,
) -> None:
    metadata = {field: pipeline_module.EXPERIMENTAL_NOTICE}
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_venn_fallback_ir(**metadata)
        reject_native = False
    else:
        ir = {**deepcopy(VENN_IR), **metadata}
        evidence = _venn_evidence()
        reject_native = True

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal description role" in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("field", ["description", "acc_description"])
def test_venn_strict_fallback_proves_an_explicit_notice_as_source_text(field: str) -> None:
    ir, evidence = _intrinsic_venn_fallback_ir(**{field: pipeline_module.EXPERIMENTAL_NOTICE})
    evidence.append(
        _venn_metadata_evidence(
            "meta-explicit-notice",
            pipeline_module.EXPERIMENTAL_NOTICE,
        )
    )

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        evaluation_mode=Mode.STRICT,
    )

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_venn_approved_initial_user_edit_can_prove_metadata() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Confirmed review title"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "user-title",
            "Confirmed review title",
            bbox=None,
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_venn_bbox_user_edit_cannot_prove_metadata_inside_a_data_area() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Confirmed review title"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "user-title",
            "Confirmed review title",
            bbox=(10, 20, 70, 28),
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title role" in warning for warning in result.selected.warnings)


def test_venn_engine_user_edit_cannot_self_authorize_metadata() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Engine review title"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "engine-title",
            "Engine review title",
            bbox=None,
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title role" in warning for warning in result.selected.warnings)


def test_venn_bbox_user_edits_with_conflicting_text_are_ambiguous() -> None:
    ir = {
        **deepcopy(VENN_IR),
        "acc_title": "Approved title",
        "acc_description": "Approved description",
    }
    shared_bbox = (5, 0, 95, 8)
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "user-title",
            "Approved title",
            bbox=shared_bbox,
            kind="user_edit",
        ),
        _venn_metadata_evidence(
            "user-description",
            "Approved description",
            bbox=shared_bbox,
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=True,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_distinct_bboxless_user_edits_can_prove_separate_roles() -> None:
    ir = {
        **deepcopy(VENN_IR),
        "acc_title": "Approved title",
        "acc_description": "Approved description",
    }
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "user-title",
            "Approved title",
            bbox=None,
            kind="user_edit",
        ),
        _venn_metadata_evidence(
            "user-description",
            "Approved description",
            bbox=None,
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=True,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize(
    ("ocr_id", "user_edit_id"),
    [("a-ocr", "z-user"), ("z-ocr", "a-user")],
)
def test_venn_spatial_source_proof_precedes_same_observation_user_edit(
    ocr_id: str,
    user_edit_id: str,
) -> None:
    ir = {**deepcopy(VENN_IR), "title": "Cohort 2025 summary"}
    bbox = (5, 0, 95, 8)
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(ocr_id, "Cohort 2025 summary", bbox=bbox),
        _venn_metadata_evidence(
            user_edit_id,
            "Cohort 2025 summary",
            bbox=bbox,
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_venn_metadata_candidate_authority_omission_requires_review() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Authorized visible title"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence("meta-title", "Authorized visible title"),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        engine_type=_MetadataPromptOmittingVennEngine,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title role" in warning for warning in result.selected.warnings)


def test_venn_data_text_evidence_cannot_be_reused_as_metadata() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Buyers: 10"}

    result, _runtime = _reconstruct_venn(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title role" in warning for warning in result.selected.warnings)


def test_venn_data_contour_evidence_cannot_be_reused_as_metadata() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Observed contour title"}
    evidence = _venn_evidence()
    _evidence_by_id(evidence, "contour-a").text = "Observed contour title"

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title role" in warning for warning in result.selected.warnings)


def test_venn_metadata_must_not_overlap_any_area_bbox() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Observed title"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "meta-title",
            "Observed title",
            bbox=(10, 20, 70, 28),
        ),
    ]

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("unsafe", ["missing", "nonfinite", "zero-area", "outside"])
def test_venn_ocr_metadata_requires_valid_geometry(unsafe: str) -> None:
    ir = {**deepcopy(VENN_IR), "title": "Observed title"}
    metadata = _venn_metadata_evidence("meta-title", "Observed title")
    if unsafe == "missing":
        metadata.bbox = None
    elif unsafe == "nonfinite":
        metadata.bbox = (5, 0, float("nan"), 8)
    elif unsafe == "zero-area":
        metadata.bbox = (5, 0, 5, 8)
    else:
        metadata.bbox = (5, 0, 205, 8)
    evidence = [*_venn_evidence(), metadata]

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_same_bbox_metadata_contradiction_requires_review() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Observed title"}
    bbox = (5, 0, 95, 8)
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence("meta-title", "Observed title", bbox=bbox),
        _venn_metadata_evidence(
            "meta-conflict",
            "Different title",
            bbox=bbox,
            kind="vector_text",
        ),
    ]

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_duplicate_metadata_observation_cannot_prove_two_roles() -> None:
    ir = {
        **deepcopy(VENN_IR),
        "acc_title": "Shared metadata",
        "acc_description": "Shared metadata",
    }
    bbox = (5, 0, 95, 8)
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence("meta-ocr", "Shared metadata", bbox=bbox),
        _venn_metadata_evidence(
            "meta-vector",
            "Shared metadata",
            bbox=bbox,
            kind="vector_text",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_venn_ocr_metadata_numeric_occurrence_is_removed_from_data_score() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Cohort 2025 summary"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence("meta-title", "Cohort 2025 summary"),
    ]

    result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_venn_metadata_numeric_subtraction_preserves_occurrence_multiplicity() -> None:
    ir = {**deepcopy(VENN_IR), "title": "Cohort 2025 2025 summary"}
    base_evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "meta-title",
            "Cohort 2025 2025 summary",
        ),
    ]

    exact, _runtime = _reconstruct_venn(ir=ir, evidence=base_evidence)

    assert exact.selected is not None
    assert exact.selected.scores["numeric_consistency"] == 1
    assert exact.selected.aggregate_score is not None, exact.selected.warnings
    assert exact.publish

    extra_evidence = [
        *base_evidence,
        _venn_metadata_evidence(
            "ocr-extra-2025",
            "2025",
            bbox=(105, 0, 130, 8),
        ),
    ]
    extra, _runtime = _reconstruct_venn(ir=ir, evidence=extra_evidence)

    assert extra.selected is not None
    assert extra.selected.scores["numeric_consistency"] < 1
    assert extra.selected.aggregate_score is None
    assert not extra.publish


@pytest.mark.parametrize("user_edit_bbox", [None, (5, 0, 95, 8)])
def test_venn_user_edit_metadata_never_subtracts_an_ocr_number(
    user_edit_bbox: tuple[float, float, float, float] | None,
) -> None:
    ir = {**deepcopy(VENN_IR), "title": "Confirmed 50 review"}
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "ocr-unrelated-number",
            "50",
            bbox=(105, 0, 120, 8),
        ),
        _venn_metadata_evidence(
            "user-title",
            "Confirmed 50 review",
            bbox=user_edit_bbox,
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert not any("terminal title role" in warning for warning in result.selected.warnings)


@pytest.mark.parametrize(
    ("limit_name", "exact_limit"),
    [
        ("_MAX_VENN_ASSOCIATION_REFERENCES", 8),
        ("_MAX_VENN_RECORD_COMPARISONS", 18),
        ("_MAX_OCR_REFERENCE_TEXTS", 19),
        ("_MAX_OCR_REFERENCE_CHARS", 236),
        ("_MAX_OCR_REFERENCE_TOKENS", 46),
    ],
)
def test_venn_combined_data_and_metadata_budget_exact_and_plus_one(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    exact_limit: int,
) -> None:
    ir = {
        **deepcopy(VENN_IR),
        "acc_title": "Title 2025",
        "acc_description": "Description 2026",
    }
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence("meta-title", "Title 2025"),
        _venn_metadata_evidence(
            "meta-description",
            "Description 2026",
            bbox=(105, 0, 195, 8),
            kind="vector_text",
        ),
    ]
    monkeypatch.setattr(pipeline_module, limit_name, exact_limit)

    exact_result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert exact_result.selected is not None
    assert exact_result.selected.aggregate_score is not None, exact_result.selected.warnings
    assert exact_result.publish

    monkeypatch.setattr(pipeline_module, limit_name, exact_limit - 1)
    over_result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert over_result.selected is not None
    assert over_result.selected.aggregate_score is None
    assert not over_result.publish
    assert any(
        "terminal title role" in warning or "terminal description role" in warning
        for warning in over_result.selected.warnings
    )


def test_venn_semantic_repair_cannot_bypass_record_local_binding() -> None:
    result, _runtime = _reconstruct_venn(repair_engine=_VennValueSwapRepair())

    assert result.selected is not None
    assert result.selected.typed_ir["sets"][0]["value"] == 10
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


def test_venn_semantic_repair_cannot_bypass_membership_geometry() -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"].append(
        {
            "id": "C",
            "label": "Customers",
            "value": 7,
            "bbox": [130, 35, 195, 110],
            "evidence_ids": ["ocr-c", "contour-c"],
        }
    )
    evidence = _venn_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-c",
            kind="ocr_token",
            text="Customers: 7",
            bbox=(135, 40, 180, 50),
        )
    )
    evidence.append(VisualEvidence(id="contour-c", kind="contour", bbox=(130, 35, 195, 110)))

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        repair_engine=_VennMembershipSwapRepair(),
    )

    assert result.selected is not None
    assert result.selected.typed_ir["intersections"][0]["sets"] == ["A", "B"]
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.repair_history
    assert result.selected.repair_history[-1].operation == "venn_membership_swap"
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


@pytest.mark.parametrize("terminal", ["native", "forced-fallback", "intrinsic-fallback"])
def test_venn_semantic_repair_cannot_inject_unproven_terminal_metadata(
    terminal: str,
) -> None:
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_venn_fallback_ir()
        reject_native = False
    else:
        ir = deepcopy(VENN_IR)
        evidence = _venn_evidence()
        reject_native = terminal == "forced-fallback"

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
        repair_engine=_VennMetadataRepair(),
    )

    assert result.selected is not None
    assert result.selected.typed_ir.get("title") != "Fabricated terminal review"
    assert result.selected.typed_ir.get("acc_title") != "Fabricated terminal review"
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


@pytest.mark.parametrize("reject_native", [False, True])
def test_venn_semantic_repair_rejects_invalid_raw_metadata_before_runtime(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_venn(
        reject_native=reject_native,
        repair_engine=_VennInvalidRawMetadataRepair(),
    )

    assert result.selected is not None
    assert result.selected.typed_ir.get("acc_title") != "Invalid\nmetadata"
    assert len(runtime.calls) == (2 if reject_native else 1)
    assert result.selected.repair_history
    assert result.selected.repair_history[-1].operation == "venn_invalid_raw_metadata"
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None
    assert any(
        "semantic repair IR could not be serialized: SerializationError" in warning
        for warning in result.selected.warnings
    )


def test_venn_semantic_repair_accepts_independently_proven_native_title() -> None:
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "meta-repair-title",
            "Fabricated terminal review",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        evidence=evidence,
        repair_engine=_VennMetadataRepair(),
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.typed_ir["title"] == "Fabricated terminal review"
    assert result.selected.repair_history[-1].accepted


def test_venn_semantic_repair_cannot_use_metadata_outside_candidate_authority() -> None:
    evidence = [
        *_venn_evidence(),
        _venn_metadata_evidence(
            "meta-repair-title",
            "Fabricated terminal review",
        ),
    ]

    result, _runtime = _reconstruct_venn(
        evidence=evidence,
        evidence_as_prior=True,
        engine_type=_MetadataPromptOmittingVennEngine,
        repair_engine=_VennMetadataRepair(),
    )

    assert result.selected is not None
    assert result.selected.typed_ir.get("title") != "Fabricated terminal review"
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


@pytest.mark.parametrize(
    ("reject_native", "exact_empty_metadata"),
    [(True, False), (False, True), (True, True)],
    ids=["forced-fallback", "native-exact-empty", "forced-fallback-exact-empty"],
)
def test_venn_terminal_repair_uses_matching_serializer_and_omits_exact_empty_metadata(
    reject_native: bool,
    exact_empty_metadata: bool,
) -> None:
    ir = deepcopy(VENN_IR)
    ir["sets"][0]["label"] = "Buyres"
    ir["sets"][0]["evidence_ids"] = [
        "ocr-a-wrong",
        "ocr-a-value",
        "contour-a",
    ]
    evidence = [item for item in _venn_evidence() if item.id != "ocr-a"]
    evidence.extend(
        [
            VisualEvidence(
                id="ocr-a-wrong",
                kind="ocr_token",
                text="Buyres",
                bbox=(10, 20, 40, 28),
            ),
            VisualEvidence(
                id="ocr-a-correct",
                kind="vector_text",
                text="Verified Buyers",
                bbox=(10, 20, 70, 28),
            ),
            VisualEvidence(
                id="ocr-a-value",
                kind="ocr_token",
                text="10",
                bbox=(10, 32, 25, 40),
            ),
        ]
    )

    result, _runtime = _reconstruct_venn(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
        repair_engine=_VennTerminalLabelRepair(
            exact_empty_metadata=exact_empty_metadata,
        ),
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.emitted_diagram_type == ("flowchart" if reject_native else "venn")
    assert result.selected.typed_ir["sets"][0]["label"] == "Verified Buyers"
    assert result.selected.repair_history[-1].accepted
    assert not any("code and typed IR diverged" in warning for warning in result.selected.warnings)
    if exact_empty_metadata:
        assert (
            not {
                "title",
                "description",
                "acc_title",
                "acc_description",
            }
            & result.selected.typed_ir.keys()
        )
    [baseline] = result.alternatives
    assert baseline.typed_ir["sets"][0]["label"] == "Buyres"


def test_venn_native_repair_cannot_switch_to_an_intrinsic_fallback() -> None:
    result, _runtime = _reconstruct_venn(repair_engine=_VennIntrinsicFallbackRepair())

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "venn"
    assert result.selected.typed_ir["sets"][1]["value"] == 8
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert any("code and typed IR diverged" in warning for warning in result.selected.warnings)


def test_direct_venn_candidate_remains_review_only_without_typed_plan() -> None:
    runtime = _VennRuntime()
    config = _best_effort_config(mode=Mode.MAXIMAL, candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["venn"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="venn",
                code=(
                    "venn-beta\n"
                    '    set A["Buyers"]: 10\n'
                    '    set B["Members"]: 8\n'
                    '    union A,B["Both"]: 3\n'
                ),
            )
        ],
        evidence=_venn_evidence(),
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "direct-venn",
        "source.png",
        Image.new("RGB", (200, 120), "white"),
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in result.selected.warnings
    )


def test_direct_venn_metadata_evidence_cannot_replace_a_typed_terminal_plan() -> None:
    runtime = _VennRuntime()
    config = _best_effort_config(mode=Mode.MAXIMAL, candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["venn"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="venn",
                code=(
                    "venn-beta\n"
                    "    title Direct observed title\n"
                    '    set A["Buyers"]: 10\n'
                    '    set B["Members"]: 8\n'
                    '    union A,B["Both"]: 3\n'
                ),
            )
        ],
        evidence=[
            *_venn_evidence(),
            _venn_metadata_evidence("meta-direct-title", "Direct observed title"),
        ],
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "direct-venn-metadata",
        "source.png",
        Image.new("RGB", (200, 120), "white"),
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize(
    ("limit_name", "exact_limit"),
    [
        ("_MAX_VENN_ASSOCIATION_REFERENCES", 6),
        ("_MAX_VENN_RECORD_COMPARISONS", 9),
        ("_MAX_OCR_REFERENCE_TEXTS", 15),
        ("_MAX_OCR_REFERENCE_CHARS", 184),
        ("_MAX_OCR_REFERENCE_TOKENS", 38),
    ],
)
def test_venn_aggregate_association_budget_exact_and_plus_one(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    exact_limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, limit_name, exact_limit)

    exact_result, _runtime = _reconstruct_venn()

    assert exact_result.selected is not None
    assert exact_result.selected.aggregate_score is not None, exact_result.selected.warnings
    assert exact_result.publish

    monkeypatch.setattr(pipeline_module, limit_name, exact_limit - 1)
    over_result, _runtime = _reconstruct_venn()

    assert over_result.selected is not None
    assert over_result.selected.aggregate_score is None
    assert not over_result.publish
    assert any(
        "Venn set/intersection label/value association lacks" in warning
        for warning in over_result.selected.warnings
    )


def test_venn_incomparable_intersection_work_is_fully_budgeted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir, evidence = _nested_venn_ir_and_evidence()
    ir["intersections"] = ir["intersections"][:3]
    evidence = [item for item in evidence if item.id not in {"ocr-abc", "contour-abc"}]
    monkeypatch.setattr(pipeline_module, "_MAX_VENN_RECORD_COMPARISONS", 30)

    exact_result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert exact_result.selected is not None
    assert exact_result.selected.aggregate_score is not None, exact_result.selected.warnings
    assert exact_result.publish

    monkeypatch.setattr(pipeline_module, "_MAX_VENN_RECORD_COMPARISONS", 29)
    over_result, _runtime = _reconstruct_venn(ir=ir, evidence=evidence)

    assert over_result.selected is not None
    assert over_result.selected.aggregate_score is None
    assert not over_result.publish
