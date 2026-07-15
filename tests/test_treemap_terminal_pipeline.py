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

TREEMAP_IR = {
    "root": {
        "id": "portfolio",
        "label": "Portfolio",
        "bbox": [5, 5, 195, 155],
        "evidence_ids": ["ocr-portfolio"],
        "children": [
            {
                "id": "core",
                "label": "Core",
                "bbox": [10, 30, 120, 145],
                "evidence_ids": ["ocr-core"],
                "children": [
                    {
                        "id": "api",
                        "label": "API",
                        "value": 20,
                        "bbox": [15, 55, 60, 135],
                        "evidence_ids": ["ocr-api"],
                    },
                    {
                        "id": "database",
                        "label": "Database",
                        "value": 30,
                        "bbox": [65, 55, 115, 135],
                        "evidence_ids": ["ocr-database"],
                    },
                ],
            },
            {
                "id": "edge",
                "label": "Edge",
                "value": 40,
                "bbox": [125, 30, 190, 145],
                "evidence_ids": ["ocr-edge"],
            },
        ],
    }
}


class _TreemapRuntime:
    def __init__(self, *, reject_native: bool = False) -> None:
        self.reject_native = reject_native
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("treemap-beta") and self.reject_native:
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="treemap",
                error="forced native rejection",
            )
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=("treemap" if code.startswith("treemap-beta") else "flowchart-v2"),
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 160">'
                "<text>Portfolio Core API 20 Database 30 Edge 40</text>"
                "</svg>"
            ),
        )

    def close(self) -> None:
        pass


class _TreemapValueSwapRepair:
    name = "treemap_value_swap"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        api, database = typed_ir["root"]["children"][0]["children"]
        api["value"], database["value"] = database["value"], api["value"]
        serialized = serialize_typed_ir_result("treemap", typed_ir, experimental=True)
        return RepairProposal(code=serialized.code, operation=self.name, typed_ir=typed_ir)


class _PromptOmittingTreemapEngine(JsonFixtureEngine):
    name = "prompt_omitting_treemap_fixture"
    fusion_source = "vlm"

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(
            {"ocr-portfolio", "ocr-core", "ocr-api", "ocr-database"}
        )
        return observation


def _treemap_evidence(
    *,
    api_text: str = "API 20",
    database_text: str = "Database: 30",
    edge_text: str = "Edge (value: 40)",
) -> list[VisualEvidence]:
    return [
        VisualEvidence(
            id="ocr-portfolio",
            kind="ocr_token",
            text="Portfolio",
            bbox=(10, 10, 100, 20),
        ),
        VisualEvidence(
            id="ocr-core",
            kind="vector_text",
            text="Core",
            bbox=(15, 35, 60, 45),
        ),
        VisualEvidence(
            id="ocr-api",
            kind="ocr_token",
            text=api_text,
            bbox=(20, 65, 55, 75),
        ),
        VisualEvidence(
            id="ocr-database",
            kind="vector_text",
            text=database_text,
            bbox=(70, 65, 110, 75),
        ),
        VisualEvidence(
            id="ocr-edge",
            kind="ocr_token",
            text=edge_text,
            bbox=(130, 65, 185, 75),
        ),
    ]


def _reconstruct_treemap(
    *,
    ir: dict[str, object] | None = None,
    evidence: list[VisualEvidence] | None = None,
    reject_native: bool = False,
    repair_engine: object | None = None,
    engine_type: type[JsonFixtureEngine] = JsonFixtureEngine,
    evidence_as_prior: bool = False,
) -> tuple[object, _TreemapRuntime]:
    runtime = _TreemapRuntime(reject_native=reject_native)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    active_evidence = evidence if evidence is not None else _treemap_evidence()
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["treemap"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="treemap", ir=deepcopy(ir or TREEMAP_IR))],
        evidence=[] if evidence_as_prior else active_evidence,
    )
    result = ReconstructionPipeline(
        config,
        [engine_type(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=repair_engine,
    ).reconstruct(
        "treemap-source",
        "source.png",
        Image.new("RGB", (200, 160), "white"),
        evidence=active_evidence if evidence_as_prior else None,
    )
    return result, runtime


@pytest.mark.parametrize("reject_native", [False, True])
def test_treemap_record_local_values_publish_native_and_fallback(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_treemap(reject_native=reject_native)

    assert result.selected is not None
    selected = result.selected
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert selected.aggregate_score is not None, selected.warnings
    assert result.publish
    assert selected.emitted_diagram_type == ("flowchart" if reject_native else "treemap")
    assert len(runtime.calls) == (2 if reject_native else 1)
    assert selected.generated_scene_ir is not None
    assert all(element.bbox == (0, 0, 0, 0) for element in selected.generated_scene_ir.elements)


def test_treemap_value_prefix_form_is_exact() -> None:
    result, _runtime = _reconstruct_treemap(evidence=_treemap_evidence(edge_text="Edge value 40"))

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_treemap_intrinsic_internal_value_fallback_keeps_binding() -> None:
    ir = deepcopy(TREEMAP_IR)
    ir["root"]["value"] = 90
    evidence = _treemap_evidence()
    evidence[0].text = "Portfolio 90"

    result, runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert len(runtime.calls) == 1
    assert runtime.calls[0].startswith("flowchart TB")


@pytest.mark.parametrize("reject_native", [False, True])
def test_treemap_swapped_values_with_same_global_numbers_require_review(
    reject_native: bool,
) -> None:
    result, _runtime = _reconstruct_treemap(
        evidence=_treemap_evidence(api_text="API 30", database_text="Database: 20"),
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Treemap node/value association conflicts" in warning
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize(
    "unsafe",
    ["missing-box", "zero-area", "outside-box", "nonfinite-evidence", "missing-evidence"],
)
def test_treemap_missing_geometry_or_evidence_requires_review(unsafe: str) -> None:
    ir = deepcopy(TREEMAP_IR)
    evidence = _treemap_evidence()
    api = ir["root"]["children"][0]["children"][0]
    if unsafe == "missing-box":
        del api["bbox"]
    elif unsafe == "zero-area":
        api["bbox"] = [15, 55, 15, 135]
    elif unsafe == "outside-box":
        api["bbox"] = [15, 55, 205, 135]
    elif unsafe == "nonfinite-evidence":
        evidence[2].bbox = (20, 65, float("nan"), 75)
    else:
        api["evidence_ids"] = ["missing"]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("Treemap node/value association lacks" in w for w in result.selected.warnings)


@pytest.mark.parametrize("unsafe", ["equal-parent", "cross-parent", "overlap-sibling"])
def test_treemap_hierarchy_geometry_fails_closed(unsafe: str) -> None:
    ir = deepcopy(TREEMAP_IR)
    core = ir["root"]["children"][0]
    api, database = core["children"]
    if unsafe == "equal-parent":
        api["bbox"] = deepcopy(core["bbox"])
    elif unsafe == "cross-parent":
        api["bbox"] = [5, 55, 60, 135]
    else:
        database["bbox"] = [50, 55, 115, 135]

    result, _runtime = _reconstruct_treemap(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_child_may_share_parent_boundary_without_being_equal() -> None:
    ir = deepcopy(TREEMAP_IR)
    ir["root"]["children"][1]["bbox"] = [125, 30, 195, 155]

    result, _runtime = _reconstruct_treemap(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_treemap_internal_text_must_not_overlap_direct_child() -> None:
    evidence = _treemap_evidence()
    evidence[1].bbox = (15, 50, 60, 65)

    result, _runtime = _reconstruct_treemap(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_cross_owner_evidence_id_reuse_requires_review() -> None:
    ir = deepcopy(TREEMAP_IR)
    ir["root"]["children"][1]["evidence_ids"] = ["ocr-api"]

    result, _runtime = _reconstruct_treemap(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_cross_owner_observation_reuse_requires_review() -> None:
    ir = deepcopy(TREEMAP_IR)
    api, database = ir["root"]["children"][0]["children"]
    database["label"] = "API"
    database["value"] = 20
    database["bbox"] = [60, 55, 115, 135]
    evidence = _treemap_evidence(database_text="API 20")
    evidence[3].bbox = evidence[2].bbox

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_same_bbox_contradictory_text_requires_review() -> None:
    evidence = _treemap_evidence()
    evidence.append(
        VisualEvidence(
            id="vector-api-conflict",
            kind="vector_text",
            text="API 99",
            bbox=evidence[2].bbox,
        )
    )

    result, _runtime = _reconstruct_treemap(evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("Treemap node/value association lacks" in w for w in result.selected.warnings)


def test_treemap_same_owner_identical_ocr_vector_observation_is_deduplicated() -> None:
    ir = deepcopy(TREEMAP_IR)
    api = ir["root"]["children"][0]["children"][0]
    api["evidence_ids"].append("vector-api")
    evidence = _treemap_evidence()
    evidence.append(
        VisualEvidence(
            id="vector-api",
            kind="vector_text",
            text="API 20",
            bbox=evidence[2].bbox,
        )
    )

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_treemap_non_text_provenance_is_ignored_but_still_owned() -> None:
    ir = deepcopy(TREEMAP_IR)
    api, database = ir["root"]["children"][0]["children"]
    api["evidence_ids"].append("shared-contour")
    database["evidence_ids"].append("shared-contour")
    evidence = _treemap_evidence()
    evidence.append(VisualEvidence(id="shared-contour", kind="contour"))

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_uncited_extra_numeric_observation_breaks_global_exactness() -> None:
    evidence = _treemap_evidence()
    evidence.append(
        VisualEvidence(
            id="ocr-extra",
            kind="ocr_token",
            text="50",
            bbox=(130, 120, 150, 130),
        )
    )

    result, _runtime = _reconstruct_treemap(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_all_node_labels_require_exact_local_text() -> None:
    evidence = _treemap_evidence()
    evidence[0].text = "Fabricated portfolio"

    result, _runtime = _reconstruct_treemap(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("Treemap node/value association conflicts" in w for w in result.selected.warnings)


def test_treemap_distinct_repeated_values_preserve_numeric_multiplicity() -> None:
    ir = deepcopy(TREEMAP_IR)
    ir["root"]["children"][0]["children"][1]["value"] = 20
    evidence = _treemap_evidence(database_text="Database 20")

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_treemap_derived_internal_total_observations_remain_review_only() -> None:
    evidence = _treemap_evidence()
    evidence[0].text = "Portfolio 90"
    evidence[1].text = "Core 50"

    result, _runtime = _reconstruct_treemap(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize(
    ("limit_name", "exact_limit"),
    [
        ("_MAX_TREEMAP_ASSOCIATION_REFERENCES", 5),
        ("_MAX_TREEMAP_NODE_OVERLAP_COMPARISONS", 10),
        ("_MAX_OCR_REFERENCE_TEXTS", 19),
        ("_MAX_OCR_REFERENCE_CHARS", 204),
        ("_MAX_OCR_REFERENCE_TOKENS", 41),
    ],
)
def test_treemap_aggregate_association_budget_exact_and_plus_one(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    exact_limit: int,
) -> None:
    monkeypatch.setattr(pipeline_module, limit_name, exact_limit)

    exact_result, _runtime = _reconstruct_treemap()

    assert exact_result.selected is not None
    assert exact_result.selected.aggregate_score is not None, exact_result.selected.warnings
    assert exact_result.publish

    monkeypatch.setattr(pipeline_module, limit_name, exact_limit - 1)

    over_result, _runtime = _reconstruct_treemap()

    assert over_result.selected is not None
    assert over_result.selected.aggregate_score is None
    assert not over_result.publish
    assert any(
        "Treemap node/value association lacks" in warning
        for warning in over_result.selected.warnings
    )


def test_treemap_candidate_authority_omission_requires_review() -> None:
    result, _runtime = _reconstruct_treemap(
        engine_type=_PromptOmittingTreemapEngine,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("Treemap node/value association lacks" in w for w in result.selected.warnings)


def test_treemap_semantic_repair_cannot_bypass_node_local_binding() -> None:
    result, _runtime = _reconstruct_treemap(repair_engine=_TreemapValueSwapRepair())

    assert result.selected is not None
    api = result.selected.typed_ir["root"]["children"][0]["children"][0]
    assert api["value"] == 20
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


def test_direct_treemap_candidate_remains_review_only_without_typed_plan() -> None:
    runtime = _TreemapRuntime()
    config = MermaidConfig(mode=Mode.MAXIMAL, candidate_count=1, publish_min_score=0)
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["treemap"], scores=[1]),
        direct_candidates=[
            DirectMermaidCandidate(
                diagram_type="treemap",
                code=(
                    "treemap-beta\n"
                    '    "Portfolio"\n'
                    '        "Core"\n'
                    '            "API": 20\n'
                    '            "Database": 30\n'
                    '        "Edge": 40\n'
                ),
            )
        ],
        evidence=_treemap_evidence(),
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "direct-treemap",
        "source.png",
        Image.new("RGB", (200, 160), "white"),
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("Treemap node/value association lacks" in w for w in result.selected.warnings)
