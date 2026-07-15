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
from marker_mermaid.serializers import (
    SerializationError,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
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


class _TreemapMetadataRepair:
    name = "treemap_metadata_injection"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["title"] = "Fabricated 2026 review"
        serialized = serialize_typed_ir_result("treemap", typed_ir, experimental=True)
        return RepairProposal(code=serialized.code, operation=self.name, typed_ir=typed_ir)


class _TreemapInvalidRawMetadataRepair:
    name = "treemap_invalid_raw_metadata"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        typed_ir["acc_title"] = "Invalid\nmetadata"
        return RepairProposal(
            code=f"{candidate.mermaid_code.rstrip()}\n%% invalid raw metadata repair\n",
            operation=self.name,
            typed_ir=typed_ir,
        )


class _TreemapTerminalLabelRepair:
    name = "treemap_terminal_label"

    def repair(self, context, candidate):
        del context
        typed_ir = deepcopy(candidate.typed_ir)
        api = typed_ir["root"]["children"][0]["children"][0]
        api["label"] = "Verified API"
        api["evidence_ids"] = ["ocr-api-correct", "ocr-api-value"]
        typed_ir.update(
            {
                "title": "",
                "description": "",
                "acc_title": "",
                "acc_description": "",
            }
        )
        if candidate.emitted_diagram_type == "flowchart":
            serialized = serialize_runtime_fallback_result(
                "treemap",
                typed_ir,
                experimental=True,
            )
            assert serialized is not None
        else:
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


class _MetadataPromptOmittingTreemapEngine(JsonFixtureEngine):
    name = "metadata_prompt_omitting_treemap_fixture"
    fusion_source = "vlm"

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(
            {"ocr-portfolio", "ocr-core", "ocr-api", "ocr-database", "ocr-edge"}
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


def _metadata_evidence(
    evidence_id: str,
    text: str,
    *,
    bbox: tuple[float, float, float, float] | None = (5, 0, 100, 4),
    kind: str = "ocr_token",
) -> VisualEvidence:
    return VisualEvidence(id=evidence_id, kind=kind, text=text, bbox=bbox)


def _intrinsic_fallback_ir(**metadata: str) -> tuple[dict[str, object], list[VisualEvidence]]:
    ir = deepcopy(TREEMAP_IR)
    ir["root"]["value"] = 90
    ir.update(metadata)
    evidence = _treemap_evidence()
    evidence[0].text = "Portfolio 90"
    return ir, evidence


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
@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
@pytest.mark.parametrize(
    "value",
    [" ", "Visible\nmetadata"],
    ids=["whitespace", "newline"],
)
def test_treemap_invalid_raw_metadata_never_reaches_either_runtime_terminal(
    field: str,
    reject_native: bool,
    value: str,
) -> None:
    ir = deepcopy(TREEMAP_IR)
    ir[field] = value

    result, runtime = _reconstruct_treemap(ir=ir, reject_native=reject_native)

    assert result.selected is None
    assert not result.publish
    assert runtime.calls == []
    assert any(
        failure.stage == "serialization" and failure.error_type == "SerializationError"
        for failure in result.failures
    )


@pytest.mark.parametrize("terminal", ["native", "forced-fallback", "intrinsic-fallback"])
def test_treemap_exact_empty_metadata_derives_defaults_for_every_terminal(
    terminal: str,
) -> None:
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_fallback_ir()
        reject_native = False
    else:
        ir = deepcopy(TREEMAP_IR)
        evidence = _treemap_evidence()
        reject_native = terminal == "forced-fallback"
    ir.update(
        {
            "title": "",
            "description": "",
            "acc_title": "",
            "acc_description": "",
        }
    )

    result, runtime = _reconstruct_treemap(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    expected_type = "treemap" if terminal == "native" else "flowchart"
    assert result.selected.emitted_diagram_type == expected_type
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish
    assert "accTitle: Treemap reconstruction" in result.selected.mermaid_code
    assert "Treemap reconstruction containing Portfolio, Core, Edge, API, Database." in (
        result.selected.mermaid_code
    )
    assert "accDescr: This reconstruction is experimental" not in (result.selected.mermaid_code)
    assert len(runtime.calls) == (2 if terminal == "forced-fallback" else 1)


def test_treemap_pipeline_validates_raw_metadata_before_accessibility_enrichment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def reject_raw_metadata(ir: object) -> None:
        del ir
        calls.append("validate")
        raise SerializationError("invalid raw Treemap metadata")

    def unexpected_enrichment(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        calls.append("enrich")
        raise AssertionError("accessibility enrichment ran before validation")

    monkeypatch.setattr(
        pipeline_module,
        "validated_treemap_accessibility_ir",
        reject_raw_metadata,
    )
    monkeypatch.setattr(pipeline_module, "enrich_accessibility_ir", unexpected_enrichment)

    result, runtime = _reconstruct_treemap()

    assert result.selected is None
    assert not result.publish
    assert runtime.calls == []
    assert calls == ["validate"]
    assert any(
        failure.stage == "serialization" and failure.error_type == "SerializationError"
        for failure in result.failures
    )


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


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
@pytest.mark.parametrize("terminal", ["native", "forced-fallback", "intrinsic-fallback"])
def test_treemap_fabricated_terminal_metadata_requires_review(
    field: str,
    terminal: str,
) -> None:
    value = f"Fabricated {field} 2026"
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_fallback_ir(**{field: value})
        reject_native = False
    else:
        ir = {**deepcopy(TREEMAP_IR), field: value}
        evidence = _treemap_evidence()
        reject_native = terminal == "forced-fallback"

    result, _runtime = _reconstruct_treemap(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    warning_role = "title/accTitle" if field in {"title", "acc_title"} else "description/accDescr"
    assert any(warning_role in warning for warning in result.selected.warnings)


def test_native_treemap_requires_independent_effective_metadata_proofs() -> None:
    ir = {
        **deepcopy(TREEMAP_IR),
        "title": "Legacy 2024 title",
        "acc_title": "Accessible 2025 title",
        "description": "Legacy summary",
        "acc_description": "Accessible 2026 summary",
    }
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("meta-visible", "Legacy 2024 title", bbox=(5, 0, 55, 4)),
        _metadata_evidence("meta-acc-title", "Accessible 2025 title", bbox=(60, 0, 115, 4)),
        _metadata_evidence(
            "meta-acc-description",
            "Accessible 2026 summary",
            bbox=(5, 156, 150, 159),
            kind="vector_text",
        ),
    ]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "treemap"
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("terminal", ["forced-fallback", "intrinsic-fallback"])
def test_treemap_fallback_exempts_shadowed_legacy_metadata(terminal: str) -> None:
    metadata = {
        "title": "Shadowed title",
        "acc_title": "Effective 2025 title",
        "description": "Shadowed description",
        "acc_description": "Effective 2026 description",
    }
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_fallback_ir(**metadata)
        reject_native = False
    else:
        ir = {**deepcopy(TREEMAP_IR), **metadata}
        evidence = _treemap_evidence()
        reject_native = True
    evidence.extend(
        [
            _metadata_evidence("meta-title", "Effective 2025 title", bbox=(5, 0, 100, 4)),
            _metadata_evidence(
                "meta-description",
                "Effective 2026 description",
                bbox=(5, 156, 180, 159),
            ),
        ]
    )

    result, _runtime = _reconstruct_treemap(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_treemap_derived_accessibility_defaults_need_no_metadata_proof() -> None:
    result, _runtime = _reconstruct_treemap()

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert not any("terminal title/accTitle" in w for w in result.selected.warnings)
    assert not any("terminal description/accDescr" in w for w in result.selected.warnings)
    assert result.publish


def test_native_treemap_collapses_identical_visible_and_accessible_title_role() -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Portfolio 2026"}
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("meta-title", "Portfolio 2026"),
    ]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_treemap_approved_initial_user_edit_can_prove_metadata() -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Confirmed review"}
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("user-title", "Confirmed review", bbox=None, kind="user_edit"),
    ]

    result, _runtime = _reconstruct_treemap(
        ir=ir,
        evidence=evidence,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


@pytest.mark.parametrize("user_edit_bbox", [None, (5, 0, 100, 4)])
def test_treemap_user_edit_metadata_cannot_subtract_unrelated_ocr_number(
    user_edit_bbox: tuple[float, float, float, float] | None,
) -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Confirmed 50 review"}
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence(
            "ocr-unrelated-number",
            "50",
            bbox=(130, 120, 150, 130),
        ),
        _metadata_evidence(
            "user-title",
            "Confirmed 50 review",
            bbox=user_edit_bbox,
            kind="user_edit",
        ),
    ]

    result, _runtime = _reconstruct_treemap(
        ir=ir,
        evidence=evidence,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] < 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Treemap node/value association conflicts" in warning
        for warning in result.selected.warnings
    )
    assert not any("terminal title/accTitle" in warning for warning in result.selected.warnings)


def test_treemap_engine_user_edit_cannot_self_authorize_metadata() -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Engine review"}
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("engine-title", "Engine review", bbox=None, kind="user_edit"),
    ]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title/accTitle" in w for w in result.selected.warnings)


def test_treemap_node_owned_evidence_cannot_prove_metadata() -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Portfolio"}

    result, _runtime = _reconstruct_treemap(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title/accTitle" in w for w in result.selected.warnings)


def test_treemap_metadata_must_not_overlap_any_node_bbox() -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Observed title"}
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("meta-title", "Observed title", bbox=(20, 10, 100, 20)),
    ]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title/accTitle" in w for w in result.selected.warnings)


@pytest.mark.parametrize("unsafe", ["missing", "nonfinite", "zero-area"])
def test_treemap_ocr_metadata_requires_valid_geometry(unsafe: str) -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Observed title"}
    metadata = _metadata_evidence("meta-title", "Observed title")
    if unsafe == "missing":
        metadata.bbox = None
    elif unsafe == "nonfinite":
        metadata.bbox = (5, 0, float("nan"), 4)
    else:
        metadata.bbox = (5, 0, 5, 4)
    evidence = [
        *_treemap_evidence(),
        metadata,
    ]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


@pytest.mark.parametrize("field", ["description", "acc_description"])
@pytest.mark.parametrize("reject_native", [False, True])
def test_treemap_notice_only_description_override_fails_closed(
    field: str,
    reject_native: bool,
) -> None:
    ir = {**deepcopy(TREEMAP_IR), field: pipeline_module.EXPERIMENTAL_NOTICE}

    result, _runtime = _reconstruct_treemap(ir=ir, reject_native=reject_native)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal description/accDescr" in w for w in result.selected.warnings)


def test_treemap_same_bbox_metadata_contradiction_requires_review() -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Observed title"}
    bbox = (5, 0, 100, 4)
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("meta-title", "Observed title", bbox=bbox),
        _metadata_evidence("meta-conflict", "Different title", bbox=bbox, kind="vector_text"),
    ]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_title_and_description_roles_require_distinct_proofs() -> None:
    ir = {
        **deepcopy(TREEMAP_IR),
        "title": "Shared 2026 metadata",
        "description": "Shared 2026 metadata",
    }
    one_proof = [
        *_treemap_evidence(),
        _metadata_evidence("meta-shared", "Shared 2026 metadata"),
    ]

    rejected, _runtime = _reconstruct_treemap(ir=ir, evidence=one_proof)

    assert rejected.selected is not None
    assert rejected.selected.aggregate_score is None
    assert not rejected.publish

    two_proofs = [
        *one_proof,
        _metadata_evidence(
            "meta-shared-description",
            "Shared 2026 metadata",
            bbox=(5, 156, 150, 159),
            kind="vector_text",
        ),
    ]
    accepted, _runtime = _reconstruct_treemap(ir=ir, evidence=two_proofs)

    assert accepted.selected is not None
    assert accepted.selected.scores["numeric_consistency"] == 1
    assert accepted.selected.aggregate_score is not None, accepted.selected.warnings
    assert accepted.publish


def test_treemap_duplicate_metadata_observation_cannot_prove_two_roles() -> None:
    ir = {
        **deepcopy(TREEMAP_IR),
        "title": "Shared metadata",
        "description": "Shared metadata",
    }
    bbox = (5, 0, 100, 4)
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("meta-ocr", "Shared metadata", bbox=bbox),
        _metadata_evidence("meta-vector", "Shared metadata", bbox=bbox, kind="vector_text"),
    ]

    result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish


def test_treemap_metadata_candidate_authority_omission_requires_review() -> None:
    ir = {**deepcopy(TREEMAP_IR), "title": "Authorized title"}
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("meta-title", "Authorized title"),
    ]

    result, _runtime = _reconstruct_treemap(
        ir=ir,
        evidence=evidence,
        engine_type=_MetadataPromptOmittingTreemapEngine,
        evidence_as_prior=True,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any("terminal title/accTitle" in w for w in result.selected.warnings)


@pytest.mark.parametrize(
    ("limit_name", "exact_limit"),
    [
        ("_MAX_TREEMAP_ASSOCIATION_REFERENCES", 7),
        ("_MAX_TREEMAP_NODE_OVERLAP_COMPARISONS", 23),
        ("_MAX_OCR_REFERENCE_TEXTS", 23),
        ("_MAX_OCR_REFERENCE_CHARS", 256),
        ("_MAX_OCR_REFERENCE_TOKENS", 49),
    ],
)
def test_treemap_combined_record_and_metadata_budget_exact_and_plus_one(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    exact_limit: int,
) -> None:
    ir = {
        **deepcopy(TREEMAP_IR),
        "title": "Title 2025",
        "description": "Description 2026",
    }
    evidence = [
        *_treemap_evidence(),
        _metadata_evidence("meta-title", "Title 2025"),
        _metadata_evidence(
            "meta-description",
            "Description 2026",
            bbox=(5, 156, 150, 159),
        ),
    ]
    monkeypatch.setattr(pipeline_module, limit_name, exact_limit)

    exact_result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert exact_result.selected is not None
    assert exact_result.selected.aggregate_score is not None, exact_result.selected.warnings
    assert exact_result.publish

    monkeypatch.setattr(pipeline_module, limit_name, exact_limit - 1)
    over_result, _runtime = _reconstruct_treemap(ir=ir, evidence=evidence)

    assert over_result.selected is not None
    assert over_result.selected.aggregate_score is None
    assert not over_result.publish
    assert any(
        "terminal title/accTitle" in warning or "terminal description/accDescr" in warning
        for warning in over_result.selected.warnings
    )


def test_treemap_semantic_repair_cannot_bypass_node_local_binding() -> None:
    result, _runtime = _reconstruct_treemap(repair_engine=_TreemapValueSwapRepair())

    assert result.selected is not None
    api = result.selected.typed_ir["root"]["children"][0]["children"][0]
    assert api["value"] == 20
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


def test_treemap_semantic_repair_cannot_inject_unproven_metadata() -> None:
    result, _runtime = _reconstruct_treemap(repair_engine=_TreemapMetadataRepair())

    assert result.selected is not None
    assert "title" not in result.selected.typed_ir
    assert result.selected.repair_history
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None


@pytest.mark.parametrize("terminal", ["native", "forced-fallback", "intrinsic-fallback"])
def test_treemap_semantic_repair_uses_sanitized_metadata_for_every_terminal(
    terminal: str,
) -> None:
    if terminal == "intrinsic-fallback":
        ir, evidence = _intrinsic_fallback_ir()
        reject_native = False
    else:
        ir = deepcopy(TREEMAP_IR)
        evidence = _treemap_evidence()
        reject_native = terminal == "forced-fallback"
    api = ir["root"]["children"][0]["children"][0]
    api["label"] = "X"
    api["evidence_ids"] = ["ocr-api-wrong", "ocr-api-value"]
    evidence = [item for item in evidence if item.id != "ocr-api"]
    evidence.extend(
        [
            VisualEvidence(
                id="ocr-api-wrong",
                kind="ocr_token",
                text="X",
                bbox=(20, 65, 30, 75),
            ),
            VisualEvidence(
                id="ocr-api-correct",
                kind="vector_text",
                text="Verified API",
                bbox=(32, 65, 58, 75),
            ),
            VisualEvidence(
                id="ocr-api-value",
                kind="ocr_token",
                text="20",
                bbox=(20, 78, 30, 86),
            ),
        ]
    )

    result, _runtime = _reconstruct_treemap(
        ir=ir,
        evidence=evidence,
        reject_native=reject_native,
        repair_engine=_TreemapTerminalLabelRepair(),
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["root"]["children"][0]["children"][0]["label"] == (
        "Verified API"
    )
    assert not {"title", "description", "acc_title", "acc_description"} & (
        result.selected.typed_ir.keys()
    )
    assert not any("code and typed IR diverged" in warning for warning in result.selected.warnings)


@pytest.mark.parametrize("reject_native", [False, True])
def test_treemap_semantic_repair_rejects_invalid_raw_metadata_before_runtime(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_treemap(
        reject_native=reject_native,
        repair_engine=_TreemapInvalidRawMetadataRepair(),
    )

    assert result.selected is not None
    assert result.selected.typed_ir.get("acc_title") != "Invalid\nmetadata"
    assert len(runtime.calls) == (2 if reject_native else 1)
    assert result.selected.repair_history
    assert result.selected.repair_history[-1].operation == "treemap_invalid_raw_metadata"
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.repair_history[-1].after_score is None
    assert any(
        "semantic repair IR could not be serialized: SerializationError" in warning
        for warning in result.selected.warnings
    )


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
