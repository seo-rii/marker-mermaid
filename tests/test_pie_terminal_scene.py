from __future__ import annotations

import math
import re
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

import marker_mermaid.serializers_charts_core as chart_core_module
from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline, _generated_node_provenance_score
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.serializers import (
    SerializationError,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.serializers_charts_core import (
    MAX_PIE_FLOWCHART_SLICES,
    MAX_PIE_NATIVE_SLICES,
    PIE_FALLBACK_TEXT_COMPATIBILITY_WARNING,
    PIE_NATIVE_TEXT_COMPATIBILITY_WARNING,
    plan_pie_records,
    serialize_pie,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

NATIVE_PIE_IR = {
    "title": "Decision split",
    "description": "Approved and rejected requests.",
    "show_data": True,
    "slices": [
        {
            "label": "Approved",
            "value": 20,
            "bbox": [0, 0, 40, 20],
            "evidence_ids": ["ocr-approved"],
        },
        {
            "label": "Rejected",
            "value": 80,
            "bbox": [50, 0, 90, 20],
            "evidence_ids": ["ocr-rejected"],
        },
    ],
}


def _fallback_ir() -> dict[str, object]:
    return {
        "title": "Rare outcome",
        "slices": [
            {
                "label": "Rare",
                "value": 1,
                "bbox": [0, 0, 40, 20],
                "evidence_ids": ["ocr-rare"],
            },
            {
                "label": "Common",
                "value": 199,
                "bbox": [50, 0, 90, 20],
                "evidence_ids": ["ocr-common"],
            },
        ],
    }


def test_native_pie_plan_scene_and_semantic_texts_match_terminal_contract() -> None:
    result = serialize_typed_ir_result("pie", NATIVE_PIE_IR)
    plan = plan_pie_records(NATIVE_PIE_IR)
    scene = typed_ir_to_scene("pie", NATIVE_PIE_IR, emitted_diagram_type=result.emitted_type)

    assert result.emitted_type == "pie"
    assert result.fallback_chain == ("pie",)
    assert plan.native_supported
    assert [slice_plan.value_text for slice_plan in plan.slices] == ["20", "80"]
    assert [slice_plan.percentage_text for slice_plan in plan.slices] == ["20%", "80%"]
    assert scene is not None
    assert scene.reading_direction == "radial"
    assert scene.coordinate_space == "normalized"
    assert scene.relations == []
    assert scene.groups == []
    assert [element.id for element in scene.elements] == ["pie_slice_1", "pie_slice_2"]
    assert [element.role for element in scene.elements] == ["slice", "slice"]
    assert [element.shape for element in scene.elements] == ["sector", "sector"]
    assert [element.text for element in scene.elements] == ["Approved [20]", "Rejected [80]"]
    assert scene.elements[0].bbox == pytest.approx(
        (
            0.5 + 0.375 * math.sin(math.pi / 5),
            0.5 - 0.375 * math.cos(math.pi / 5),
        )
        * 2
    )
    assert scene.elements[1].bbox == pytest.approx(
        (
            0.5 + 0.375 * math.sin(6 * math.pi / 5),
            0.5 - 0.375 * math.cos(6 * math.pi / 5),
        )
        * 2
    )
    assert scene.elements[0].evidence_ids == ["ocr-approved"]
    assert list(
        typed_ir_semantic_texts(
            "pie", NATIVE_PIE_IR, scene, emitted_diagram_type=result.emitted_type
        )
    ) == ["Decision split", "Approved [20]", "20%", "Rejected [80]", "80%"]


def test_zero_value_pie_slice_has_legend_provenance_but_no_invented_sector() -> None:
    ir = {
        "slices": [
            {"label": "None", "value": 0, "evidence_ids": ["ocr-none"]},
            {"label": "All", "value": 10, "evidence_ids": ["ocr-all"]},
        ]
    }

    plan = plan_pie_records(ir)
    scene = typed_ir_to_scene("pie", ir, emitted_diagram_type="pie")

    assert plan.native_supported
    assert plan.slices[0].percentage_text is None
    assert plan.slices[0].normalized_point is None
    assert scene is not None
    assert scene.elements[0].bbox == (0, 0, 0, 0)
    assert scene.elements[0].text == "None"
    assert scene.elements[0].evidence_ids == ["ocr-none"]
    assert list(typed_ir_semantic_texts("pie", ir, scene, emitted_diagram_type="pie")) == [
        "None",
        "All",
        "100%",
    ]


def test_pie_exact_flowchart_fallback_scene_and_semantic_texts_match_terminal_contract() -> None:
    ir = _fallback_ir()
    result = serialize_typed_ir_result("pie", ir)
    scene = typed_ir_to_scene("pie", ir, emitted_diagram_type=result.emitted_type)

    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("pie", "flowchart")
    assert "one-percent visibility threshold" in result.warnings[0]
    assert 'pie_slice_1["Rare: 1"]' in result.code
    assert 'pie_slice_2["Common: 199"]' in result.code
    assert " --> " not in result.code
    assert scene is not None
    assert scene.reading_direction == "TB"
    assert scene.coordinate_space == "pixels"
    assert scene.relations == []
    assert scene.groups == []
    assert [element.text for element in scene.elements] == ["Rare: 1", "Common: 199"]
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    assert list(
        typed_ir_semantic_texts("pie", ir, scene, emitted_diagram_type=result.emitted_type)
    ) == ["Rare: 1", "Common: 199"]


@pytest.mark.parametrize(
    "values",
    [
        [Decimal("1e-325"), 1],
        [Decimal("1e308"), Decimal("1e308")],
        [Decimal("1.234567890123456789"), 2],
        [2**53 + 1, 2**53 + 2],
    ],
)
def test_pie_unsafe_binary64_values_use_exact_flowchart_fallback(
    values: list[object],
) -> None:
    ir = {
        "slices": [
            {"label": "First", "value": values[0]},
            {"label": "Second", "value": values[1]},
        ]
    }

    plan = plan_pie_records(ir)
    result = serialize_typed_ir_result("pie", ir)

    assert not plan.native_supported
    assert result.emitted_type == "flowchart"
    for slice_plan in plan.slices:
        assert slice_plan.fallback_source_label in result.code


def test_pie_show_data_falls_back_when_javascript_would_rewrite_exact_number() -> None:
    ir = {
        "show_data": True,
        "slices": [
            {"label": "Tiny", "value": Decimal("0.0000001")},
            {"label": "Rest", "value": Decimal("0.9999999")},
        ],
    }

    plan = plan_pie_records(ir)
    result = serialize_typed_ir_result("pie", ir)

    assert any("showData text would be rewritten" in item for item in plan.native_limitations)
    assert result.emitted_type == "flowchart"
    assert "Tiny: 0.0000001" in result.code
    assert "Rest: 0.9999999" in result.code


def test_pie_percentage_rounding_matches_mermaid_binary64_half_up_behavior() -> None:
    ir = {
        "slices": [
            {"label": "Half", "value": 1},
            {"label": "Rest", "value": 199},
        ]
    }

    plan = plan_pie_records(ir)

    assert [slice_plan.percentage_text for slice_plan in plan.slices] == [None, "100%"]
    assert not plan.native_supported


def test_pie_provenance_scores_only_emitted_slice_records() -> None:
    scene = typed_ir_to_scene("pie", NATIVE_PIE_IR, emitted_diagram_type="pie")
    evidence = [
        VisualEvidence(id="ocr-approved", kind="ocr_token", text="Approved 20"),
        VisualEvidence(id="ocr-rejected", kind="vector_text", text="Rejected 80"),
    ]

    assert scene is not None
    assert _generated_node_provenance_score(scene, None, evidence) == 1


@pytest.mark.parametrize("native_runtime_valid", [None, 0, 1, "false"])
def test_pie_runtime_validity_flag_requires_an_exact_boolean(
    native_runtime_valid: object,
) -> None:
    with pytest.raises(SerializationError, match="must be a boolean"):
        serialize_pie(NATIVE_PIE_IR, native_runtime_valid=native_runtime_valid)  # type: ignore[arg-type]


def test_pie_native_runtime_rejection_uses_same_slot_exact_fallback() -> None:
    result = serialize_runtime_fallback_result("pie", NATIVE_PIE_IR)

    assert result is not None
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("pie", "flowchart")
    assert any("same candidate slot" in warning for warning in result.warnings)
    assert 'pie_slice_1["Approved: 20"]' in result.code
    assert 'pie_slice_2["Rejected: 80"]' in result.code


def test_pie_terminal_resource_limits_are_preflighted_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_limit = {
        "slices": [
            {"label": f"Slice {index}", "value": 1}
            for index in range(MAX_PIE_NATIVE_SLICES)
        ]
    }
    fallback_limit = deepcopy(native_limit)
    fallback_limit["slices"].append({"label": "Overflow legend", "value": 1})

    assert serialize_typed_ir_result("pie", native_limit).emitted_type == "pie"
    assert serialize_typed_ir_result("pie", fallback_limit).emitted_type == "flowchart"

    over_fallback = {
        "slices": [
            {"label": f"Slice {index}", "value": 1}
            for index in range(MAX_PIE_FLOWCHART_SLICES + 1)
        ]
    }
    with pytest.raises(SerializationError, match="slice runtime limit"):
        serialize_typed_ir_result("pie", over_fallback)

    monkeypatch.setattr(chart_core_module, "MAX_PIE_OUTPUT_CHARS", 20)
    with pytest.raises(SerializationError, match="source-character"):
        serialize_typed_ir_result("pie", NATIVE_PIE_IR)


def test_pie_terminal_character_budget_matches_mermaid_utf16_units() -> None:
    astral_character = "\U0001f7e2"
    labels = [f"{index}{astral_character * 3_600}" for index in range(7)]
    astral_ir = {
        "slices": [
            {"label": label, "value": 1}
            for label in labels
        ]
    }

    assert sum(map(len, labels)) < chart_core_module.MAX_PIE_OUTPUT_CHARS
    assert (
        sum(len(label.encode("utf-16-le")) // 2 for label in labels)
        > chart_core_module.MAX_PIE_OUTPUT_CHARS
    )
    with pytest.raises(SerializationError, match="UTF-16 source-character"):
        serialize_typed_ir_result("pie", astral_ir)


def test_pie_source_line_budget_is_preflighted_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chart_core_module, "MAX_PIE_OUTPUT_LINES", 5)

    with pytest.raises(SerializationError, match="source-line"):
        serialize_typed_ir_result("pie", NATIVE_PIE_IR)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", 7),
        ("description", {}),
        ("acc_title", 7),
        ("acc_description", {}),
    ],
)
def test_pie_public_serialization_rejects_non_text_explicit_metadata(
    field: str,
    value: object,
) -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    ir[field] = value

    with pytest.raises(SerializationError, match=rf"pie {field} must be text"):
        serialize_typed_ir_result("pie", ir)
    with pytest.raises(SerializationError, match=rf"pie {field} must be text"):
        serialize_runtime_fallback_result("pie", ir)
    with pytest.raises(SerializationError, match=rf"pie {field} must be text"):
        serialize_pie(ir)


def test_pie_record_reuse_duplicate_labels_and_malformed_evidence_are_isolated() -> None:
    shared = {"label": "Same", "value": 1}
    with pytest.raises(SerializationError, match="reuse"):
        plan_pie_records({"slices": [shared, shared]})
    with pytest.raises(SerializationError, match="unique"):
        plan_pie_records(
            {"slices": [{"label": "Same", "value": 1}, {"label": "Same", "value": 2}]}
        )

    ir = deepcopy(NATIVE_PIE_IR)
    ir["slices"][0]["evidence_ids"] = ["valid", 7]
    plan = plan_pie_records(ir)

    assert plan.slices[0].evidence_ids == ()
    assert plan.slices[1].evidence_ids == ("ocr-rejected",)


def test_pie_terminal_text_projection_is_shared_with_scene_and_disclosed() -> None:
    label = ' A "quoted" \\ value\u00a0&quot; <#> '
    ir = {
        "title": 'Title "quoted" \\ <#>;',
        "slices": [
            {"label": label, "value": 20},
            {"label": "Other", "value": 80},
        ],
    }

    native = serialize_typed_ir_result("pie", ir)
    native_scene = typed_ir_to_scene("pie", ir, emitted_diagram_type="pie")
    fallback_ir = deepcopy(ir)
    fallback_ir["slices"][0]["value"] = 1
    fallback_ir["slices"][1]["value"] = 199
    fallback = serialize_typed_ir_result("pie", fallback_ir)
    fallback_scene = typed_ir_to_scene("pie", fallback_ir, emitted_diagram_type="flowchart")

    assert PIE_NATIVE_TEXT_COMPATIBILITY_WARNING in native.warnings
    assert '\\"quoted\\"' in native.code
    assert native_scene is not None
    assert native_scene.elements[0].text == 'A "quoted" \\ value &quot; <#>'
    assert PIE_FALLBACK_TEXT_COMPATIBILITY_WARNING in fallback.warnings
    assert fallback_scene is not None
    assert fallback_scene.elements[0].text == "A ″quoted″ ∖ value &quot; ＜＃＞: 1"

    url_ir = deepcopy(ir)
    url_ir["slices"][0]["label"] = "https://example.com"
    url_result = serialize_typed_ir_result("pie", url_ir)
    url_scene = typed_ir_to_scene("pie", url_ir, emitted_diagram_type="pie")

    assert "https://example.com" not in url_result.code
    assert "https\u200b:/\u200b/example.com" in url_result.code
    assert url_scene is not None
    assert url_scene.elements[0].text == "https://example.com"


class _PieRuntime:
    def __init__(self, *, reject_native: bool = False) -> None:
        self.reject_native = reject_native
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: int) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("pie") and self.reject_native:
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="pie",
                error="forced native rejection",
            )
        diagram_type = "pie" if code.startswith("pie") else "flowchart-v2"
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=diagram_type,
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                "<text>Decision split Approved 20% Rejected 80%</text></svg>"
            ),
        )

    def close(self) -> None:
        pass


def _pie_evidence(
    *, approved_text: str = "Approved 20", rejected_text: str = "Rejected 80"
) -> list[VisualEvidence]:
    return [
        VisualEvidence(
            id="ocr-title",
            kind="ocr_token",
            text="Decision split",
            bbox=(5, 25, 45, 35),
        ),
        VisualEvidence(
            id="ocr-description",
            kind="vector_text",
            text="Approved and rejected requests.",
            bbox=(5, 37, 95, 47),
        ),
        VisualEvidence(
            id="ocr-approved",
            kind="ocr_token",
            text=approved_text,
            bbox=(5, 5, 35, 15),
        ),
        VisualEvidence(
            id="ocr-rejected",
            kind="vector_text",
            text=rejected_text,
            bbox=(55, 5, 85, 15),
        ),
    ]


def _reconstruct_pie(
    *,
    evidence: list[VisualEvidence] | None = None,
    initial_evidence: list[VisualEvidence] | None = None,
    ir: dict[str, object] | None = None,
    reject_native: bool = False,
    ocr_texts: list[str] | None = None,
) -> tuple[object, _PieRuntime]:
    runtime = _PieRuntime(reject_native=reject_native)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    result = ReconstructionPipeline(
        config,
        [
            JsonFixtureEngine(
                EngineObservation(
                    prediction=DiagramTypePrediction(candidates=["pie"], scores=[1]),
                    typed_candidates=[
                        TypedIRCandidate(
                            diagram_type="pie",
                            ir=deepcopy(ir or NATIVE_PIE_IR),
                        )
                    ],
                    evidence=evidence if evidence is not None else _pie_evidence(),
                )
            )
        ],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "pie-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        ocr_texts=(
            ["Decision split Approved 20 Rejected 80"]
            if ocr_texts is None
            else ocr_texts
        ),
        evidence=initial_evidence,
    )
    return result, runtime


@pytest.mark.parametrize("reject_native", [False, True])
def test_pie_slice_local_evidence_can_pass_native_and_runtime_fallback_gates(
    reject_native: bool,
) -> None:
    result, runtime = _reconstruct_pie(reject_native=reject_native)

    assert result.selected is not None
    selected = result.selected
    assert selected.scores["numeric_consistency"] == 1
    assert selected.scores["visual_entailment_precision"] == 1
    assert selected.aggregate_score is not None
    assert result.publish
    assert selected.emitted_diagram_type == ("flowchart" if reject_native else "pie")
    assert selected.fallback_chain == (["pie", "flowchart"] if reject_native else ["pie"])
    assert selected.generated_scene_ir is not None
    assert selected.generated_scene_ir.relations == []
    assert len(runtime.calls) == (2 if reject_native else 1)
    if reject_native:
        assert runtime.calls[0].startswith("pie")
        assert runtime.calls[1].startswith("flowchart TB")
        assert selected.repair_history[-1].operation == "runtime_portable_fallback"


@pytest.mark.parametrize("reject_native", [False, True])
def test_pie_value_swap_requires_review_even_when_global_numeric_multiset_matches(
    reject_native: bool,
) -> None:
    result, _runtime = _reconstruct_pie(
        evidence=_pie_evidence(
            approved_text="Approved 80",
            rejected_text="Rejected 20",
        ),
        reject_native=reject_native,
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie slice/value association conflicts with source numeric evidence" in warning
        for warning in result.selected.warnings
    )


def test_pie_punctuation_distinct_label_swap_cannot_reuse_equal_ocr_token_signatures() -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    ir["slices"][0]["label"] = "A+B"
    ir["slices"][1]["label"] = "A-B"
    evidence = _pie_evidence(
        approved_text="A-B 20",
        rejected_text="A+B 80",
    )

    result, _runtime = _reconstruct_pie(evidence=evidence, ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie slice/value association lacks candidate-authorized spatial OCR/vector evidence"
        in warning
        for warning in result.selected.warnings
    )


def test_pie_label_value_separator_is_bound_without_losing_punctuation() -> None:
    result, _runtime = _reconstruct_pie(
        evidence=_pie_evidence(
            approved_text="Approved: 20",
            rejected_text="Rejected=80",
        )
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_pie_label_suffix_omission_cannot_pass_token_only_binding() -> None:
    result, _runtime = _reconstruct_pie(
        evidence=_pie_evidence(approved_text="Approved pending 20")
    )

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie slice/value association lacks candidate-authorized spatial OCR/vector evidence"
        in warning
        for warning in result.selected.warnings
    )


def test_pie_uncited_source_slice_cannot_be_hidden_by_exact_local_bindings() -> None:
    evidence = [
        *_pie_evidence(),
        VisualEvidence(
            id="ocr-pending",
            kind="ocr_token",
            text="Pending 50",
            bbox=(55, 25, 85, 35),
        ),
    ]

    result, _runtime = _reconstruct_pie(evidence=evidence)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 0
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie slice/value association conflicts with source numeric evidence" in warning
        for warning in result.selected.warnings
    )


def test_pie_visible_native_title_requires_independent_source_attribution() -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    ir["title"] = "ADMIN APPROVED SECRET"

    result, _runtime = _reconstruct_pie(ir=ir)

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie title/accTitle lacks independent candidate-authorized"
        in warning
        for warning in result.selected.warnings
    )


def test_pie_title_suffix_omission_requires_review() -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    ir["title"] = "Decision split"
    evidence = _pie_evidence()
    evidence[0] = evidence[0].model_copy(update={"text": "Decision split appendix"})

    result, _runtime = _reconstruct_pie(evidence=evidence, ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie title/accTitle lacks independent candidate-authorized"
        in warning
        for warning in result.selected.warnings
    )


def test_pie_title_cannot_reuse_a_slice_observation_under_another_evidence_id() -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    ir["title"] = "Approved 20"
    evidence = _pie_evidence()
    evidence[0] = evidence[0].model_copy(update={"text": "Unrelated heading"})
    evidence.append(
        VisualEvidence(
            id="duplicate-approved-title",
            kind="ocr_token",
            text="Approved 20",
            bbox=(5, 5, 35, 15),
        )
    )

    result, _runtime = _reconstruct_pie(evidence=evidence, ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie title/accTitle lacks independent candidate-authorized"
        in warning
        for warning in result.selected.warnings
    )


def test_pie_fallback_acc_title_requires_source_attribution() -> None:
    ir = _fallback_ir()
    ir["title"] = "ADMIN APPROVED SECRET"
    evidence = [
        VisualEvidence(
            id="ocr-rare",
            kind="ocr_token",
            text="Rare 1",
            bbox=(5, 5, 35, 15),
        ),
        VisualEvidence(
            id="ocr-common",
            kind="vector_text",
            text="Common 199",
            bbox=(55, 5, 85, 15),
        ),
    ]

    result, _runtime = _reconstruct_pie(
        evidence=evidence,
        ir=ir,
        ocr_texts=["Rare 1 Common 199"],
    )

    assert result.selected is not None
    assert result.selected.emitted_diagram_type == "flowchart"
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie title/accTitle lacks independent candidate-authorized" in warning
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize(
    ("field", "value", "warning_text"),
    [
        (
            "acc_title",
            "Hallucinated accessible title",
            "Pie title/accTitle lacks independent candidate-authorized",
        ),
        (
            "acc_description",
            "Hallucinated accessible description",
            "Pie explicit accessibility description lacks independent",
        ),
    ],
)
def test_pie_explicit_accessibility_text_requires_independent_evidence(
    field: str,
    value: str,
    warning_text: str,
) -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    ir[field] = value

    result, _runtime = _reconstruct_pie(ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(warning_text in warning for warning in result.selected.warnings)


def test_pie_user_edit_evidence_can_authorize_explicit_accessibility_text() -> None:
    ir = deepcopy(NATIVE_PIE_IR)
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

    result, _runtime = _reconstruct_pie(initial_evidence=initial_evidence, ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is not None, result.selected.warnings
    assert result.publish


def test_pie_engine_emitted_user_edit_cannot_self_authorize_accessibility() -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    ir["acc_title"] = "Engine title"
    evidence = [
        *_pie_evidence(),
        VisualEvidence(id="engine-edit", kind="user_edit", text="Engine title"),
    ]

    result, _runtime = _reconstruct_pie(evidence=evidence, ir=ir)

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie title/accTitle lacks independent candidate-authorized" in warning
        for warning in result.selected.warnings
    )


@pytest.mark.parametrize("unsafe_case", ["missing_bbox", "overlap", "outside", "missing_numbers"])
def test_pie_unsafe_spatial_value_bindings_require_review(unsafe_case: str) -> None:
    ir = deepcopy(NATIVE_PIE_IR)
    evidence = _pie_evidence()
    if unsafe_case == "missing_bbox":
        del ir["slices"][0]["bbox"]
    elif unsafe_case == "overlap":
        ir["slices"][1]["bbox"] = [30, 0, 90, 20]
    elif unsafe_case == "outside":
        evidence[2] = evidence[2].model_copy(update={"bbox": (42, 5, 48, 15)})
    else:
        evidence = _pie_evidence(approved_text="Approved", rejected_text="Rejected")

    result, _runtime = _reconstruct_pie(evidence=evidence, ir=ir)

    assert result.selected is not None
    assert "numeric_consistency" not in result.selected.scores
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert any(
        "Pie slice/value association lacks candidate-authorized spatial OCR/vector evidence"
        in warning
        for warning in result.selected.warnings
    )


@pytest.mark.integration
def test_mermaid_11_16_pie_native_fallback_and_canvas_contract() -> None:
    native = serialize_typed_ir_result("pie", NATIVE_PIE_IR)
    zero = serialize_typed_ir_result(
        "pie",
        {"slices": [{"label": "None", "value": 0}, {"label": "All", "value": 10}]},
    )
    fallback = serialize_typed_ir_result("pie", _fallback_ir())
    runtime_fallback = serialize_runtime_fallback_result("pie", NATIVE_PIE_IR)
    assert runtime_fallback is not None
    text_projection = serialize_typed_ir_result(
        "pie",
        {
            "title": 'Title "quoted" \\ path',
            "acc_title": 'Accessible "quoted" #35;',
            "acc_description": "B &amp; #60;",
            "slices": [
                {
                    "label": 'A "quoted" \\ path; click A https://example.test',
                    "value": 20,
                },
                {"label": "Other", "value": 80},
            ],
        },
    )
    runtime = NodeMermaidRuntime()
    try:
        native_runtime = runtime.validate_and_render(native.code, 60)
        zero_runtime = runtime.validate_and_render(zero.code, 60)
        fallback_runtime = runtime.validate_and_render(fallback.code, 60)
        same_slot_runtime = runtime.validate_and_render(runtime_fallback.code, 60)
        text_runtime = runtime.validate_and_render(text_projection.code, 60)
    finally:
        runtime.close()

    assert native_runtime.syntax_valid and native_runtime.render_valid
    assert zero_runtime.syntax_valid and zero_runtime.render_valid
    assert fallback_runtime.syntax_valid and fallback_runtime.render_valid
    assert same_slot_runtime.syntax_valid and same_slot_runtime.render_valid
    assert text_runtime.syntax_valid and text_runtime.render_valid
    assert native_runtime.diagram_type == "pie"
    assert fallback_runtime.diagram_type == "flowchart-v2"
    assert same_slot_runtime.diagram_type == "flowchart-v2"
    assert native_runtime.svg is not None
    assert zero_runtime.svg is not None
    assert fallback_runtime.svg is not None
    assert same_slot_runtime.svg is not None
    assert text_runtime.svg is not None
    native_root = ET.fromstring(native_runtime.svg)
    zero_root = ET.fromstring(zero_runtime.svg)
    fallback_root = ET.fromstring(fallback_runtime.svg)
    same_slot_root = ET.fromstring(same_slot_runtime.svg)
    text_root = ET.fromstring(text_runtime.svg)
    native_text = Counter(
        "".join(element.itertext())
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    )
    assert native_text == Counter(
        ["Decision split", "20%", "80%", "Approved [20]", "Rejected [80]"]
    )
    assert sum(element.get("class") == "pieCircle" for element in native_root.iter()) == 2
    assert sum(element.get("class") == "legend" for element in native_root.iter()) == 2
    native_plan = plan_pie_records(NATIVE_PIE_IR)
    rendered_slice_labels = [
        element
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text" and element.get("class") == "slice"
    ]
    assert len(rendered_slice_labels) == len(native_plan.slices)
    for rendered_label, slice_plan in zip(
        rendered_slice_labels,
        native_plan.slices,
        strict=True,
    ):
        transform = rendered_label.get("transform", "")
        match = re.fullmatch(r"translate\(([^,]+),\s*([^\)]+)\)", transform)
        assert match is not None
        assert slice_plan.normalized_point is not None
        rendered_point = (
            0.5 + float(match.group(1)) / 370,
            0.5 + float(match.group(2)) / 370,
        )
        assert rendered_point == pytest.approx(slice_plan.normalized_point, abs=1e-6)
    zero_text = {
        "".join(element.itertext())
        for element in zero_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    }
    assert {"None", "All", "100%"} <= zero_text
    assert "0%" not in zero_text
    assert sum(element.get("class") == "pieCircle" for element in zero_root.iter()) == 1
    fallback_canvas = " ".join(" ".join(fallback_root.itertext()).split())
    assert "Rare: 1" in fallback_canvas
    assert "Common: 199" in fallback_canvas
    assert not any(
        element.tag.rsplit("}", 1)[-1] == "path"
        and "flowchart-link" in element.get("class", "")
        for element in fallback_root.iter()
    )
    fallback_title = next(
        "".join(element.itertext()).replace("\u200b", "")
        for element in fallback_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "title"
    )
    fallback_description = next(
        "".join(element.itertext()).replace("\u200b", "")
        for element in fallback_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "desc"
    )
    assert fallback_title == "Rare outcome"
    assert fallback_description.endswith(
        "This Pie reconstruction uses an exact-value Flowchart fallback."
    )
    same_slot_description = next(
        "".join(element.itertext()).replace("\u200b", "")
        for element in same_slot_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "desc"
    )
    assert same_slot_description == (
        "Approved and rejected requests. "
        "This Pie reconstruction uses an exact-value Flowchart fallback."
    )
    assert not any(
        "nan" in value.casefold() or "infinity" in value.casefold()
        for root in (native_root, zero_root, fallback_root, same_slot_root, text_root)
        for element in root.iter()
        for value in element.attrib.values()
    )
    text_canvas = {
        "".join(element.itertext()).replace("\u200b", "")
        for element in text_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    }
    accessibility_title = next(
        "".join(element.itertext()).replace("\u200b", "")
        for element in text_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "title"
    )
    accessibility_description = next(
        "".join(element.itertext()).replace("\u200b", "")
        for element in text_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "desc"
    )
    assert accessibility_title == 'Accessible "quoted" #35;'
    assert accessibility_description == "B &amp; #60;"
    assert "&quot;" not in accessibility_title
    assert 'Title ″quoted″ ∖ path' in text_canvas
    assert 'A "quoted" \\ path; click A https://example.test' in text_canvas
