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

SANKEY_IR = {
    "nodes": [
        {
            "id": "source",
            "label": "Source",
            "bbox": [5, 10, 35, 30],
            "evidence_ids": ["ocr-source"],
        },
        {
            "id": "middle",
            "label": "Middle",
            "bbox": [85, 45, 115, 65],
            "evidence_ids": ["ocr-middle"],
        },
        {
            "id": "sink",
            "label": "Sink",
            "bbox": [165, 80, 195, 100],
            "evidence_ids": ["ocr-sink"],
        },
    ],
    "flows": [
        {
            "id": "flow-1",
            "source": "source",
            "target": "middle",
            "value": 20,
            "bbox": [45, 20, 85, 40],
            "evidence_ids": ["ocr-flow-1"],
        },
        {
            "id": "flow-2",
            "source": "middle",
            "target": "sink",
            "value": 30,
            "bbox": [115, 65, 155, 85],
            "evidence_ids": ["ocr-flow-2"],
        },
    ],
}


class _SankeyRuntime:
    def __init__(self, *, reject_native: bool = False) -> None:
        self.reject_native = reject_native
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("sankey-beta") and self.reject_native:
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="sankey",
                error="forced native rejection",
            )
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=("sankey" if code.startswith("sankey-beta") else "flowchart-v2"),
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 120">'
                "<text>Source Middle Sink 20 30</text>"
                "</svg>"
            ),
        )

    def close(self) -> None:
        pass


class _SankeyWeightSwapRepair:
    name = "sankey_weight_swap"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["flows"][0]["value"], typed_ir["flows"][1]["value"] = (
            typed_ir["flows"][1]["value"],
            typed_ir["flows"][0]["value"],
        )
        serialized = serialize_typed_ir_result("sankey", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _SankeyMetadataRepair:
    name = "sankey_add_metadata"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["acc_title"] = "Fabricated summary"
        typed_ir["acc_description"] = "Fabricated weighted flow description."
        serialized = serialize_typed_ir_result("sankey", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _PromptOmittingSankeyEngine(JsonFixtureEngine):
    name = "prompt_omitting_sankey_fixture"
    fusion_source = "vlm"

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(
            {"ocr-source", "ocr-middle", "ocr-sink"}
        )
        return observation


class _PromptOmittingSankeyMetadataEngine(JsonFixtureEngine):
    name = "prompt_omitting_sankey_metadata_fixture"
    fusion_source = "vlm"

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(
            {
                "ocr-source",
                "ocr-middle",
                "ocr-sink",
                "ocr-flow-1",
                "ocr-flow-2",
            }
        )
        return observation


def _sankey_evidence(
    *,
    flow_1_text: str = "20",
    flow_2_text: str = "30",
) -> list[VisualEvidence]:
    return [
        VisualEvidence(
            id="ocr-source",
            kind="ocr_token",
            text="Source",
            bbox=(10, 15, 30, 25),
        ),
        VisualEvidence(
            id="ocr-middle",
            kind="vector_text",
            text="Middle",
            bbox=(90, 50, 110, 60),
        ),
        VisualEvidence(
            id="ocr-sink",
            kind="ocr_token",
            text="Sink",
            bbox=(170, 85, 190, 95),
        ),
        VisualEvidence(
            id="ocr-flow-1",
            kind="ocr_token",
            text=flow_1_text,
            bbox=(55, 25, 75, 35),
        ),
        VisualEvidence(
            id="ocr-flow-2",
            kind="vector_text",
            text=flow_2_text,
            bbox=(125, 70, 145, 80),
        ),
    ]


def _sankey_metadata_evidence(
    *,
    title: str = "Flow summary",
    description: str = "Two weighted stages.",
) -> list[VisualEvidence]:
    return [
        VisualEvidence(
            id="ocr-sankey-title",
            kind="ocr_token",
            text=title,
            bbox=(5, 105, 75, 115),
        ),
        VisualEvidence(
            id="ocr-sankey-description",
            kind="vector_text",
            text=description,
            bbox=(80, 105, 195, 118),
        ),
    ]


def _reconstruct_sankey(
    *,
    ir: dict[str, object] | None = None,
    evidence: list[VisualEvidence] | None = None,
    reject_native: bool = False,
    repair_engine: object | None = None,
    engine_type: type[JsonFixtureEngine] = JsonFixtureEngine,
    evidence_as_prior: bool = False,
    initial_evidence: list[VisualEvidence] | None = None,
) -> tuple[object, _SankeyRuntime]:
    runtime = _SankeyRuntime(reject_native=reject_native)
    active_evidence = evidence if evidence is not None else _sankey_evidence()
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["sankey"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="sankey", ir=deepcopy(ir or SANKEY_IR))],
        evidence=[] if evidence_as_prior else active_evidence,
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    source_evidence = list(initial_evidence or ())
    if evidence_as_prior:
        source_evidence = [*active_evidence, *source_evidence]
    result = ReconstructionPipeline(
        config,
        [engine_type(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=repair_engine,
    ).reconstruct(
        "sankey-source",
        "source.png",
        Image.new("RGB", (200, 120), "white"),
        evidence=source_evidence or None,
    )
    return result, runtime


@pytest.mark.parametrize("reject_native", [False, True])
def test_sankey_flow_local_values_publish_native_and_same_slot_fallback(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_sankey(reject_native=reject_native)

    assert result.selected is not None
    selected = result.selected
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert selected.aggregate_score is not None, selected.warnings
    assert result.publish
    assert selected.emitted_diagram_type == ("flowchart" if reject_native else "sankey")
    assert len(runtime.calls) == (2 if reject_native else 1)


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_sankey_fabricated_explicit_fallback_metadata_requires_review(field: str) -> None:
    ir = deepcopy(SANKEY_IR)
    ir[field] = "Fabricated metadata"

    result, _runtime = _reconstruct_sankey(ir=ir, reject_native=True)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    expected = "title/accTitle lacks" if "title" in field else "description/accDescr lacks"
    assert any(expected in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_sankey_native_terminal_does_not_require_omitted_accessibility_metadata(
    field: str,
) -> None:
    ir = deepcopy(SANKEY_IR)
    ir[field] = "Native-only metadata"

    result, _runtime = _reconstruct_sankey(ir=ir)

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "sankey"
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert not any("Sankey fallback" in warning for warning in result.selected.warnings)


def test_sankey_independently_proven_plain_metadata_publishes_fallback() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    ir["description"] = "Two weighted stages."
    evidence = [*_sankey_evidence(), *_sankey_metadata_evidence()]

    result, runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert "accTitle: Flow summary" in runtime.calls[-1]
    assert "accDescr: Two weighted stages." in runtime.calls[-1]


def test_sankey_accessibility_overrides_shadow_unemitted_plain_metadata() -> None:
    ir = deepcopy(SANKEY_IR)
    ir.update(
        {
            "title": "Hidden plain title",
            "description": "Hidden plain description.",
            "acc_title": "Accessible flow summary",
            "acc_description": "Accessible weighted flow description.",
        }
    )
    evidence = [
        *_sankey_evidence(),
        *_sankey_metadata_evidence(
            title="Accessible flow summary",
            description="Accessible weighted flow description.",
        ),
    ]

    result, runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert "accTitle: Accessible flow summary" in runtime.calls[-1]
    assert "accDescr: Accessible weighted flow description." in runtime.calls[-1]
    assert "Hidden plain" not in runtime.calls[-1]


def test_sankey_derived_fallback_accessibility_defaults_are_exempt() -> None:
    result, _runtime = _reconstruct_sankey(reject_native=True)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert not any("Sankey fallback" in warning for warning in result.selected.warnings)


def test_sankey_initial_user_edits_can_authorize_fallback_metadata() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    ir["description"] = "Two weighted stages."
    initial_evidence = [
        VisualEvidence(id="user-title", kind="user_edit", text="Flow summary"),
        VisualEvidence(
            id="user-description",
            kind="user_edit",
            text="Two weighted stages.",
        ),
    ]

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        reject_native=True,
        initial_evidence=initial_evidence,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_sankey_engine_user_edits_cannot_self_authorize_fallback_metadata() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    ir["description"] = "Two weighted stages."
    evidence = _sankey_evidence()
    evidence.extend(
        [
            VisualEvidence(id="engine-title", kind="user_edit", text="Flow summary"),
            VisualEvidence(
                id="engine-description",
                kind="user_edit",
                text="Two weighted stages.",
            ),
        ]
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_sankey_flow_owned_evidence_cannot_authorize_fallback_title() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["acc_title"] = "20"

    result, _runtime = _reconstruct_sankey(ir=ir, reject_native=True)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("title/accTitle lacks" in warning for warning in result.selected.warnings)


def test_sankey_flow_owned_observation_cannot_authorize_fallback_title() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["acc_title"] = "20"
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-title-copy",
            kind="vector_text",
            text="20",
            bbox=(55, 25, 75, 35),
        )
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("title/accTitle lacks" in warning for warning in result.selected.warnings)


def test_sankey_metadata_same_bbox_contradiction_fails_closed() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    evidence = _sankey_evidence()
    evidence.extend(
        [
            VisualEvidence(
                id="ocr-sankey-title",
                kind="ocr_token",
                text="Flow summary",
                bbox=(5, 105, 75, 115),
            ),
            VisualEvidence(
                id="vector-sankey-title-conflict",
                kind="vector_text",
                text="Different summary",
                bbox=(5, 105, 75, 115),
            ),
        ]
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("title/accTitle lacks" in warning for warning in result.selected.warnings)


def test_sankey_metadata_overlapping_a_flow_record_fails_closed() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-sankey-title",
            kind="ocr_token",
            text="Flow summary",
            bbox=(48, 21, 82, 39),
        )
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("title/accTitle lacks" in warning for warning in result.selected.warnings)


def test_sankey_title_and_description_cannot_reuse_one_evidence_id() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Shared summary"
    ir["description"] = "Shared summary"
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-shared-metadata",
            kind="ocr_token",
            text="Shared summary",
            bbox=(5, 105, 75, 115),
        )
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("description/accDescr lacks" in warning for warning in result.selected.warnings)


def test_sankey_title_and_description_cannot_reuse_one_spatial_observation() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Shared summary"
    ir["description"] = "Shared summary"
    evidence = _sankey_evidence()
    evidence.extend(
        [
            VisualEvidence(
                id="ocr-shared-metadata",
                kind="ocr_token",
                text="Shared summary",
                bbox=(5, 105, 75, 115),
            ),
            VisualEvidence(
                id="vector-shared-metadata",
                kind="vector_text",
                text="Shared summary",
                bbox=(5, 105, 75, 115),
            ),
        ]
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("description/accDescr lacks" in warning for warning in result.selected.warnings)


def test_sankey_same_metadata_text_with_distinct_observations_publishes() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Shared summary"
    ir["description"] = "Shared summary"
    evidence = _sankey_evidence()
    evidence.extend(
        [
            VisualEvidence(
                id="ocr-shared-title",
                kind="ocr_token",
                text="Shared summary",
                bbox=(5, 105, 75, 115),
            ),
            VisualEvidence(
                id="vector-shared-description",
                kind="vector_text",
                text="Shared summary",
                bbox=(80, 105, 195, 118),
            ),
        ]
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_sankey_metadata_must_be_inside_candidate_evidence_authority() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    ir["description"] = "Two weighted stages."
    evidence = [*_sankey_evidence(), *_sankey_metadata_evidence()]

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
        engine_type=_PromptOmittingSankeyMetadataEngine,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("Sankey fallback" in warning for warning in result.selected.warnings)


def test_sankey_node_owned_evidence_cannot_authorize_fallback_title() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["acc_title"] = "Source"

    result, _runtime = _reconstruct_sankey(ir=ir, reject_native=True)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("title/accTitle lacks" in warning for warning in result.selected.warnings)


def test_sankey_metadata_overlapping_a_node_record_fails_closed() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-sankey-title",
            kind="ocr_token",
            text="Flow summary",
            bbox=(6, 11, 34, 29),
        )
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("title/accTitle lacks" in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("unsafe_bbox", [None, [5, 10, 5, 30], [5, 10, 205, 30]])
def test_sankey_missing_or_malformed_node_bbox_blocks_fallback_metadata_attribution(
    unsafe_bbox: list[int] | None,
) -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    if unsafe_bbox is None:
        del ir["nodes"][0]["bbox"]
    else:
        ir["nodes"][0]["bbox"] = unsafe_bbox
    evidence = [*_sankey_evidence(), _sankey_metadata_evidence()[0]]

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("title/accTitle lacks" in warning for warning in result.selected.warnings)


def test_sankey_proven_numeric_metadata_is_excluded_from_flow_weight_scoring() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary 2025"
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-sankey-title",
            kind="ocr_token",
            text="Flow summary 2025",
            bbox=(5, 105, 75, 115),
        )
    )

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("reject_native", [False, True])
def test_sankey_weight_swap_requires_review_for_both_terminals(
    reject_native: bool,
) -> None:
    result, _runtime = _reconstruct_sankey(
        evidence=_sankey_evidence(flow_1_text="30", flow_2_text="20"),
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Sankey flow/value association conflicts" in warning for warning in result.selected.warnings
    )


@pytest.mark.parametrize("evidence_ids", [[], ["missing-flow-evidence"]])
def test_sankey_uncited_or_missing_value_evidence_requires_review(
    evidence_ids: list[str],
) -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][0]["evidence_ids"] = evidence_ids

    result, _runtime = _reconstruct_sankey(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Sankey flow/value association lacks" in warning for warning in result.selected.warnings
    )


def test_sankey_uncited_extra_numeric_observation_breaks_global_exactness() -> None:
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-extra-flow",
            kind="ocr_token",
            text="40",
            bbox=(85, 95, 105, 105),
        )
    )

    result, _runtime = _reconstruct_sankey(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Sankey flow/value association conflicts" in warning for warning in result.selected.warnings
    )


def test_sankey_cross_flow_evidence_id_reuse_requires_review() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][1]["evidence_ids"] = ["ocr-flow-1"]

    result, _runtime = _reconstruct_sankey(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_sankey_cross_flow_observation_reuse_requires_review() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][1]["value"] = 20
    ir["flows"][1]["bbox"] = deepcopy(ir["flows"][0]["bbox"])
    evidence = _sankey_evidence(flow_2_text="20")
    evidence[-1].bbox = evidence[-2].bbox

    result, _runtime = _reconstruct_sankey(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_sankey_same_bbox_contradictory_text_requires_review() -> None:
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-flow-1-contradiction",
            kind="vector_text",
            text="99",
            bbox=(55, 25, 75, 35),
        )
    )

    result, _runtime = _reconstruct_sankey(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_sankey_positive_area_flow_bbox_overlap_requires_review() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][1]["bbox"] = [75, 30, 115, 50]
    evidence = _sankey_evidence()
    evidence[-1].bbox = (85, 35, 105, 45)

    result, _runtime = _reconstruct_sankey(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Sankey flow/value association lacks" in warning for warning in result.selected.warnings
    )


def test_sankey_flow_bboxes_may_touch_without_overlapping() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][1]["bbox"] = [85, 20, 125, 40]
    evidence = _sankey_evidence()
    evidence[-1].bbox = (95, 25, 115, 35)

    result, _runtime = _reconstruct_sankey(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_sankey_same_owner_identical_ocr_vector_observation_is_deduplicated() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][0]["evidence_ids"].append("vector-flow-1")
    evidence = _sankey_evidence()
    evidence.append(
        VisualEvidence(
            id="vector-flow-1",
            kind="vector_text",
            text="20",
            bbox=(55, 25, 75, 35),
        )
    )

    result, _runtime = _reconstruct_sankey(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_sankey_distinct_repeated_weights_preserve_numeric_multiplicity() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][1]["value"] = 20

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=_sankey_evidence(flow_2_text="20"),
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize(
    "unsafe_case",
    ["missing-flow-box", "outside-flow-box", "outside-observation", "zero-weight"],
)
def test_sankey_invalid_flow_geometry_or_nonpositive_weight_requires_review(
    unsafe_case: str,
) -> None:
    ir = deepcopy(SANKEY_IR)
    evidence = _sankey_evidence()
    if unsafe_case == "missing-flow-box":
        del ir["flows"][0]["bbox"]
    elif unsafe_case == "outside-flow-box":
        ir["flows"][0]["bbox"] = [45, 20, 205, 40]
    elif unsafe_case == "outside-observation":
        evidence[-2].bbox = (5, 100, 25, 110)
    else:
        ir["flows"][0]["value"] = 0
        evidence[-2].text = "0"

    result, _runtime = _reconstruct_sankey(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_sankey_cited_non_text_evidence_cannot_authorize_a_weight() -> None:
    evidence = _sankey_evidence()
    evidence[-2] = VisualEvidence(
        id="ocr-flow-1",
        kind="line_segment",
        bbox=(55, 25, 75, 35),
    )

    result, _runtime = _reconstruct_sankey(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_sankey_candidate_authority_mismatch_requires_review() -> None:
    result, _runtime = _reconstruct_sankey(
        engine_type=_PromptOmittingSankeyEngine,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Sankey flow/value association lacks" in warning for warning in result.selected.warnings
    )


@pytest.mark.parametrize(
    ("constant_name", "exact_limit"),
    [
        ("_MAX_SANKEY_ASSOCIATION_REFERENCES", 2),
        ("_MAX_SANKEY_FLOW_OVERLAP_COMPARISONS", 1),
        ("_MAX_OCR_REFERENCE_TEXTS", 7),
        ("_MAX_OCR_REFERENCE_CHARS", 24),
        ("_MAX_OCR_REFERENCE_TOKENS", 7),
    ],
)
def test_sankey_aggregate_association_budget_exact_boundary_publishes(
    monkeypatch,
    constant_name: str,
    exact_limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, constant_name, exact_limit)

    result, _runtime = _reconstruct_sankey()

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize(
    ("constant_name", "over_limit"),
    [
        ("_MAX_SANKEY_ASSOCIATION_REFERENCES", 1),
        ("_MAX_SANKEY_FLOW_OVERLAP_COMPARISONS", 0),
        ("_MAX_OCR_REFERENCE_TEXTS", 6),
        ("_MAX_OCR_REFERENCE_CHARS", 23),
        ("_MAX_OCR_REFERENCE_TOKENS", 6),
    ],
)
def test_sankey_aggregate_association_budget_plus_one_fails_closed(
    monkeypatch,
    constant_name: str,
    over_limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, constant_name, over_limit)

    result, _runtime = _reconstruct_sankey()

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("constant_name", "exact_limit"),
    [
        ("_MAX_SANKEY_ASSOCIATION_REFERENCES", 4),
        ("_MAX_SANKEY_FLOW_OVERLAP_COMPARISONS", 14),
        ("_MAX_OCR_REFERENCE_TEXTS", 11),
        ("_MAX_OCR_REFERENCE_CHARS", 88),
        ("_MAX_OCR_REFERENCE_TOKENS", 17),
    ],
)
def test_sankey_fallback_metadata_shares_exact_association_budgets(
    monkeypatch,
    constant_name: str,
    exact_limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, constant_name, exact_limit)
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    ir["description"] = "Two weighted stages."
    evidence = [*_sankey_evidence(), *_sankey_metadata_evidence()]

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize(
    ("constant_name", "over_limit"),
    [
        ("_MAX_SANKEY_ASSOCIATION_REFERENCES", 3),
        ("_MAX_SANKEY_FLOW_OVERLAP_COMPARISONS", 13),
        ("_MAX_OCR_REFERENCE_TEXTS", 10),
        ("_MAX_OCR_REFERENCE_CHARS", 87),
        ("_MAX_OCR_REFERENCE_TOKENS", 16),
    ],
)
def test_sankey_fallback_metadata_budget_plus_one_fails_closed(
    monkeypatch,
    constant_name: str,
    over_limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, constant_name, over_limit)
    ir = deepcopy(SANKEY_IR)
    ir["title"] = "Flow summary"
    ir["description"] = "Two weighted stages."
    evidence = [*_sankey_evidence(), *_sankey_metadata_evidence()]

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        evidence=evidence,
        reject_native=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_sankey_semantic_repair_cannot_bypass_flow_local_binding() -> None:
    result, _runtime = _reconstruct_sankey(repair_engine=_SankeyWeightSwapRepair())

    assert result.selected is not None
    assert result.selected.typed_ir["flows"][0]["value"] == 20
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted


def test_sankey_semantic_repair_cannot_add_unproven_fallback_metadata() -> None:
    ir = deepcopy(SANKEY_IR)
    ir["flows"][1]["target"] = "source"

    result, _runtime = _reconstruct_sankey(
        ir=ir,
        repair_engine=_SankeyMetadataRepair(),
    )

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert result.selected.typed_ir["acc_title"] != "Fabricated summary"
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


def test_direct_sankey_candidate_remains_review_only_without_typed_plan() -> None:
    runtime = _SankeyRuntime()
    config = MermaidConfig(
        mode=Mode.MAXIMAL,
        candidate_count=1,
        publish_min_score=0,
    )
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["sankey"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="sankey",
                code="sankey-beta\nSource,Middle,20\nMiddle,Sink,30\n",
            )
        ],
        evidence=_sankey_evidence(),
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "direct-sankey",
        "source.png",
        Image.new("RGB", (200, 120), "white"),
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Sankey flow/value association lacks" in warning for warning in result.selected.warnings
    )
