from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

import marker_mermaid.serializers_charts_flow as chart_flow_module
from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_ID_CHARS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline, _generated_node_provenance_score
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_charts_flow import (
    MAX_RADAR_DIMENSIONS,
    MAX_RADAR_FLOWCHART_POINTS,
    MAX_RADAR_NATIVE_SERIES,
    RADAR_FALLBACK_TEXT_COMPATIBILITY_WARNING,
    RADAR_NATIVE_TEXT_COMPATIBILITY_WARNING,
    plan_radar_records,
    serialize_radar,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

NATIVE_RADAR_IR = {
    "title": "Model comparison",
    "description": "Two models across three dimensions.",
    "dimensions": [
        {
            "id": "accuracy",
            "label": "Accuracy",
            "bbox": [40, 0, 60, 10],
            "evidence_ids": ["ocr-accuracy"],
        },
        {
            "id": "speed",
            "label": "Speed",
            "bbox": [75, 50, 95, 65],
            "evidence_ids": ["ocr-speed"],
        },
        {
            "id": "safety",
            "label": "Safety",
            "bbox": [5, 50, 25, 65],
            "evidence_ids": ["ocr-safety"],
        },
    ],
    "series": [
        {
            "id": "model-a",
            "label": "Model A",
            "values": [0, 5, 10],
            "bbox": [5, 75, 45, 90],
            "evidence_ids": ["curve-a"],
        },
        {
            "id": "model-b",
            "label": "Model B",
            "values": [10, 7.5, 2.5],
            "bbox": [55, 75, 95, 90],
            "evidence_ids": ["curve-b"],
        },
    ],
    "min": 0,
    "max": 10,
    "ticks": 5,
    "show_legend": True,
    "graticule": "polygon",
}


def _fallback_ir() -> dict[str, object]:
    ir = deepcopy(NATIVE_RADAR_IR)
    ir.pop("min")
    ir["series"][0]["values"] = [-2.5, 0, 4]
    return ir


def test_native_radar_scene_and_semantic_texts_match_terminal_contract() -> None:
    result = serialize_radar(NATIVE_RADAR_IR)
    plan = plan_radar_records(NATIVE_RADAR_IR)
    scene = typed_ir_to_scene("radar", NATIVE_RADAR_IR, emitted_diagram_type=result.emitted_type)

    assert result.emitted_type == "radar"
    assert plan.native_supported
    assert scene is not None
    assert scene.reading_direction == "radial"
    assert scene.coordinate_space == "normalized"
    assert scene.groups == []
    assert [element.id for element in scene.elements] == [
        "accuracy",
        "speed",
        "safety",
        "model-a",
        "model-b",
        "model-a_1",
        "model-a_2",
        "model-a_3",
        "model-b_1",
        "model-b_2",
        "model-b_3",
    ]
    assert [element.role for element in scene.elements[:5]] == [
        "axis",
        "axis",
        "axis",
        "series",
        "series",
    ]
    assert scene.elements[0].bbox == pytest.approx((0.5, 0, 0.5, 0))
    assert scene.elements[0].bbox != (40, 0, 60, 10)
    assert scene.elements[0].evidence_ids == ["ocr-accuracy"]
    assert scene.elements[3].bbox == pytest.approx(
        (0.5 - math.sqrt(3) / 4, 0.5, 0.5 + math.sqrt(3) / 8, 0.75)
    )
    assert scene.elements[3].evidence_ids == ["curve-a"]
    assert scene.elements[5].bbox == pytest.approx((0.5, 0.5, 0.5, 0.5))
    assert scene.elements[5].evidence_ids == ["ocr-accuracy", "curve-a"]
    assert scene.elements[7].bbox == pytest.approx(
        (0.5 + 0.5 * math.cos(5 * math.pi / 6), 0.5 + 0.5 * math.sin(5 * math.pi / 6)) * 2
    )
    assert len(scene.relations) == 6
    assert [
        (
            relation.source_id,
            relation.target_id,
            relation.relation_type,
            relation.label,
            relation.arrow_at_end,
        )
        for relation in scene.relations[:3]
    ] == [
        ("model-a_1", "model-a_2", "series_curve", None, False),
        ("model-a_2", "model-a_3", "series_curve", None, False),
        ("model-a_3", "model-a_1", "series_curve", None, False),
    ]
    assert scene.relations[0].evidence_ids == ["curve-a"]
    assert list(
        typed_ir_semantic_texts(
            "radar", NATIVE_RADAR_IR, scene, emitted_diagram_type=result.emitted_type
        )
    ) == ["Model comparison", "Accuracy", "Speed", "Safety", "Model A", "Model B"]


def test_native_radar_hidden_legend_is_not_credited_as_canvas_text() -> None:
    ir = deepcopy(NATIVE_RADAR_IR)
    ir["show_legend"] = False

    scene = typed_ir_to_scene("radar", ir, emitted_diagram_type="radar")

    assert scene is not None
    assert [element.text for element in scene.elements if element.role == "series"] == [None, None]
    assert list(typed_ir_semantic_texts("radar", ir, scene, emitted_diagram_type="radar")) == [
        "Model comparison",
        "Accuracy",
        "Speed",
        "Safety",
    ]


def test_native_radar_provenance_scores_emitted_axes_and_series_not_derived_points() -> None:
    result = serialize_radar(NATIVE_RADAR_IR)
    scene = typed_ir_to_scene("radar", NATIVE_RADAR_IR, emitted_diagram_type=result.emitted_type)
    evidence = [
        VisualEvidence(id="ocr-accuracy", kind="ocr_token", text="Accuracy"),
        VisualEvidence(id="ocr-speed", kind="ocr_token", text="Speed"),
        VisualEvidence(id="ocr-safety", kind="ocr_token", text="Safety"),
        VisualEvidence(id="curve-a", kind="vlm_observation", text="Model A curve"),
        VisualEvidence(id="curve-b", kind="vlm_observation", text="Model B curve"),
    ]

    assert scene is not None
    assert _generated_node_provenance_score(scene, None, evidence) == 1


def test_radar_flowchart_scene_and_semantic_texts_match_terminal_contract() -> None:
    ir = _fallback_ir()
    result = serialize_radar(ir)
    plan = plan_radar_records(ir)
    scene = typed_ir_to_scene("radar", ir, emitted_diagram_type=result.emitted_type)

    assert result.emitted_type == "flowchart"
    assert scene is not None
    assert scene.reading_direction == "TB"
    assert scene.coordinate_space == "pixels"
    assert scene.relations == []
    assert scene.elements[0].id == plan.fallback_title_id
    assert scene.elements[0].role == "title"
    assert scene.elements[0].text == "Model comparison"
    assert scene.elements[0].evidence_ids == []
    assert [(group.id, group.label, group.member_ids) for group in scene.groups] == [
        ("model-a", "Model A", ["model-a_1", "model-a_2", "model-a_3"]),
        ("model-b", "Model B", ["model-b_1", "model-b_2", "model-b_3"]),
    ]
    assert [element.text for element in scene.elements[1:]] == [
        "Accuracy: -2.5",
        "Speed: 0",
        "Safety: 4",
        "Accuracy: 10",
        "Speed: 7.5",
        "Safety: 2.5",
    ]
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    assert scene.elements[1].evidence_ids == ["ocr-accuracy", "curve-a"]
    assert list(
        typed_ir_semantic_texts("radar", ir, scene, emitted_diagram_type=result.emitted_type)
    ) == [
        "Model comparison",
        "Model A",
        "Accuracy: -2.5",
        "Speed: 0",
        "Safety: 4",
        "Model B",
        "Accuracy: 10",
        "Speed: 7.5",
        "Safety: 2.5",
    ]


@pytest.mark.parametrize(
    "values",
    [
        [0, 0, 0],
        [Decimal("1e-325"), Decimal("2e-325"), Decimal("3e-325")],
        [Decimal("1e308"), Decimal("5e307"), Decimal("2e307")],
        [Decimal("1.234567890123456789"), 2, 3],
        [2**53 + 1, 2**53 + 2, 2**53 + 3],
    ],
)
def test_radar_unsafe_or_non_renderable_native_numbers_use_exact_fallback(
    values: list[object],
) -> None:
    ir = {
        "dimensions": [
            {"id": "a", "label": "A"},
            {"id": "b", "label": "B"},
            {"id": "c", "label": "C"},
        ],
        "series": [{"id": "s", "label": "S", "values": values}],
    }

    result = serialize_radar(ir)

    assert result.emitted_type == "flowchart"
    plan = plan_radar_records(ir)
    assert not plan.native_supported
    for point in plan.series[0].points:
        assert point.value_text in result.code


def test_radar_flat_nonzero_domain_and_explicit_positive_zero_domain_are_distinct() -> None:
    flat = {
        "dimensions": [{"id": value, "label": value.upper()} for value in ("a", "b", "c")],
        "series": [{"id": "s", "label": "S", "values": [5, 5, 5]}],
        "min": 5,
    }
    explicit_scale = deepcopy(flat)
    explicit_scale["series"][0]["values"] = [0, 0, 0]
    explicit_scale["min"] = 0
    explicit_scale["max"] = 10

    assert serialize_radar(flat).emitted_type == "flowchart"
    assert serialize_radar(explicit_scale).emitted_type == "radar"


def test_radar_native_fixed_decimal_values_avoid_exponent_notation() -> None:
    ir = {
        "dimensions": [{"id": value, "label": value.upper()} for value in ("a", "b", "c")],
        "series": [{"id": "s", "label": "S", "values": [1e-7, 1e20, 5]}],
        "max": 1e20,
    }

    result = serialize_radar(ir)

    assert result.emitted_type == "radar"
    assert "0.0000001" in result.code
    assert "100000000000000000000" in result.code
    assert "e-" not in result.code.casefold()
    assert "e+" not in result.code.casefold()


def test_radar_reserves_ids_across_native_and_flowchart_namespaces() -> None:
    ir = {
        "dimensions": [
            {"id": "axis", "label": "Axis"},
            {"id": "radar_axis", "label": "Other axis"},
            {"id": "curve", "label": "Curve"},
        ],
        "series": [
            {"id": "title", "label": "Title", "values": [1, 2, 3]},
            {"id": "radar_title", "label": "Other title", "values": [3, 2, 1]},
        ],
        "max": 3,
    }

    plan = plan_radar_records(ir)
    result = serialize_radar(ir)
    scene = typed_ir_to_scene("radar", ir, emitted_diagram_type=result.emitted_type)

    assert [dimension.emitted_id for dimension in plan.dimensions] == [
        "radar_axis",
        "radar_axis_2",
        "radar_curve",
    ]
    assert [series.emitted_id for series in plan.series] == ["radar_title", "radar_title_2"]
    assert scene is not None
    assert len({element.id for element in scene.elements}) == len(scene.elements)


def test_radar_record_reuse_and_malformed_evidence_are_isolated() -> None:
    shared = {"id": "same", "label": "Same"}
    with pytest.raises(SerializationError, match="reuse"):
        plan_radar_records(
            {
                "dimensions": [shared, shared, {"id": "third", "label": "Third"}],
                "series": [{"id": "s", "values": [1, 2, 3]}],
            }
        )

    ir = deepcopy(NATIVE_RADAR_IR)
    ir["dimensions"][0]["evidence_ids"] = ["valid", 7]
    plan = plan_radar_records(ir)

    assert plan.dimensions[0].evidence_ids == ()
    assert plan.dimensions[1].evidence_ids == ("ocr-speed",)
    assert plan.series[0].points[0].evidence_ids == ("curve-a",)


def test_radar_rejects_oversized_series_id_before_terminal_planning() -> None:
    ir = deepcopy(NATIVE_RADAR_IR)
    ir["series"][0]["id"] = "s" * (MAX_ID_CHARS + 1)

    with pytest.raises(SerializationError, match="bounded canonical id"):
        plan_radar_records(ir)


@pytest.mark.parametrize("native_runtime_valid", [None, 0, 1, "false"])
def test_radar_runtime_validity_flag_requires_an_exact_boolean(
    native_runtime_valid: object,
) -> None:
    with pytest.raises(SerializationError, match="must be a boolean"):
        serialize_radar(NATIVE_RADAR_IR, native_runtime_valid=native_runtime_valid)  # type: ignore[arg-type]


def test_radar_runtime_rejection_uses_exact_same_slot_fallback() -> None:
    result = serialize_radar(NATIVE_RADAR_IR, native_runtime_valid=False)

    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("radar", "flowchart")
    assert result.warnings and "same candidate slot" in result.warnings[0]
    assert 'radar_title["Model comparison"]' in result.code
    assert 'model-a_1["Accuracy: 0"]' in result.code


def test_radar_runtime_fallback_preserves_title_without_exposing_hidden_legend() -> None:
    ir = deepcopy(NATIVE_RADAR_IR)
    ir["show_legend"] = False

    plan = plan_radar_records(ir)
    result = serialize_radar(ir, native_runtime_valid=False)
    scene = typed_ir_to_scene("radar", ir, emitted_diagram_type=result.emitted_type)

    assert plan.fallback_title_id == "radar_title"
    assert plan.fallback_source_title == "Model comparison"
    assert plan.fallback_canvas_title == "Model comparison"
    assert f'{plan.fallback_title_id}["{plan.fallback_source_title}"]' in result.code
    assert 'subgraph model-a["\u200b"]' in result.code
    assert 'subgraph model-b["\u200b"]' in result.code
    assert 'subgraph model-a["Model A"]' not in result.code
    assert 'subgraph model-b["Model B"]' not in result.code
    assert scene is not None
    assert [(element.id, element.role, element.text) for element in scene.elements[:1]] == [
        ("radar_title", "title", "Model comparison")
    ]
    assert scene.elements[0].evidence_ids == []
    assert [group.label for group in scene.groups] == [None, None]
    assert list(typed_ir_semantic_texts("radar", ir, scene, emitted_diagram_type="flowchart")) == [
        "Model comparison",
        "Accuracy: 0",
        "Speed: 5",
        "Safety: 10",
        "Accuracy: 10",
        "Speed: 7.5",
        "Safety: 2.5",
    ]


def test_radar_fallback_title_id_is_collision_safe_across_terminal_namespace() -> None:
    ir = deepcopy(NATIVE_RADAR_IR)
    ir["dimensions"][0]["id"] = "radar_title"

    plan = plan_radar_records(ir)
    result = serialize_radar(ir, native_runtime_valid=False)
    scene = typed_ir_to_scene("radar", ir, emitted_diagram_type="flowchart")

    assert plan.dimensions[0].emitted_id == "radar_title"
    assert plan.fallback_title_id is not None
    assert plan.fallback_title_id != plan.dimensions[0].emitted_id
    assert f'    {plan.fallback_title_id}["Model comparison"]' in result.code
    assert scene is not None
    assert len({element.id for element in scene.elements}) == len(scene.elements)
    assert scene.elements[0].id == plan.fallback_title_id


def test_radar_terminal_resource_limits_are_applied_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SerializationError, match="dimension count"):
        serialize_radar(
            {
                "dimensions": [
                    {"id": f"d{index}", "label": f"D{index}"}
                    for index in range(MAX_RADAR_DIMENSIONS + 1)
                ],
                "series": [{"id": "s", "values": [1] * (MAX_RADAR_DIMENSIONS + 1)}],
            }
        )

    twelve = {
        "dimensions": [{"id": value, "label": value.upper()} for value in ("a", "b", "c")],
        "series": [
            {"id": f"s{index}", "label": f"S{index}", "values": [1, 2, 3]}
            for index in range(MAX_RADAR_NATIVE_SERIES)
        ],
        "max": 3,
    }
    thirteen = deepcopy(twelve)
    thirteen["series"].append({"id": "s12", "label": "S12", "values": [1, 2, 3]})
    assert serialize_radar(twelve).emitted_type == "radar"
    assert serialize_radar(thirteen).emitted_type == "flowchart"

    over_fallback = deepcopy(thirteen)
    while len(over_fallback["series"]) * 3 <= MAX_RADAR_FLOWCHART_POINTS:
        index = len(over_fallback["series"])
        over_fallback["series"].append(
            {"id": f"s{index}", "label": f"S{index}", "values": [-1, 2, 3]}
        )
    with pytest.raises(SerializationError, match="point runtime limit"):
        serialize_radar(over_fallback)

    monkeypatch.setattr(chart_flow_module, "MAX_RADAR_OUTPUT_CHARS", 20)
    with pytest.raises(SerializationError, match="source-character"):
        serialize_radar(NATIVE_RADAR_IR)


def test_radar_source_line_budget_is_preflighted_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chart_flow_module, "MAX_RADAR_OUTPUT_LINES", 5)

    with pytest.raises(SerializationError, match="source-line"):
        serialize_radar(NATIVE_RADAR_IR)


def test_radar_flowchart_line_budget_accounts_for_visible_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    untitled = deepcopy(NATIVE_RADAR_IR)
    untitled.pop("title")
    monkeypatch.setattr(chart_flow_module, "MAX_RADAR_OUTPUT_LINES", 14)

    assert serialize_radar(untitled, native_runtime_valid=False).emitted_type == "flowchart"
    with pytest.raises(SerializationError, match="source-line"):
        serialize_radar(NATIVE_RADAR_IR, native_runtime_valid=False)


def test_radar_source_budget_uses_mermaid_utf16_code_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir = deepcopy(NATIVE_RADAR_IR)
    ir["dimensions"][0]["label"] = "Accuracy \U0001f600\U0001f600\U0001f600"
    result = serialize_radar(ir)
    python_characters = len(result.code)
    utf16_units = len(result.code.encode("utf-16-le")) // 2
    assert python_characters < utf16_units
    monkeypatch.setattr(
        chart_flow_module,
        "MAX_RADAR_OUTPUT_CHARS",
        (python_characters + utf16_units) // 2,
    )

    with pytest.raises(SerializationError, match="UTF-16 source-character"):
        serialize_radar(ir)


def test_radar_compatibility_text_is_shared_with_scene_and_disclosed() -> None:
    label = ' A "quoted" \\ value\u00a0&quot; <#> '
    ir = {
        "title": "Title <#>",
        "dimensions": [
            {"id": "a", "label": label},
            {"id": "b", "label": "B"},
            {"id": "c", "label": "C"},
        ],
        "series": [{"id": "s", "label": label, "values": [1, 2, 3]}],
        "max": 3,
    }

    native = serialize_radar(ir)
    native_scene = typed_ir_to_scene("radar", ir, emitted_diagram_type="radar")
    fallback = serialize_radar(
        {**ir, "series": [{"id": "s", "label": label, "values": [-1, 2, 3]}]}
    )
    fallback_scene = typed_ir_to_scene(
        "radar",
        {**ir, "series": [{"id": "s", "label": label, "values": [-1, 2, 3]}]},
        emitted_diagram_type="flowchart",
    )

    assert RADAR_NATIVE_TEXT_COMPATIBILITY_WARNING in native.warnings
    assert native_scene is not None
    assert native_scene.elements[0].text == 'A "quoted" \\ value &quot; ＜＃＞'
    assert RADAR_FALLBACK_TEXT_COMPATIBILITY_WARNING in fallback.warnings
    assert fallback_scene is not None
    assert fallback_scene.groups[0].label == "A ″quoted″ ∖ value &quot; ＜＃＞"

    unsafe = deepcopy(ir)
    unsafe["dimensions"][0]["label"] = "https://example.com"
    with pytest.raises(SerializationError, match="external_url"):
        serialize_radar(unsafe)


@pytest.mark.integration
def test_mermaid_11_16_radar_native_fallback_and_canvas_contract() -> None:
    native = serialize_radar(NATIVE_RADAR_IR)
    no_legend_ir = deepcopy(NATIVE_RADAR_IR)
    no_legend_ir["show_legend"] = False
    no_legend = serialize_radar(no_legend_ir)
    no_legend_fallback = serialize_radar(no_legend_ir, native_runtime_valid=False)
    fallback = serialize_radar(_fallback_ir())
    reserved = serialize_radar(
        {
            "dimensions": [
                {"id": "axis", "label": "Axis"},
                {"id": "curve", "label": "Curve"},
                {"id": "title", "label": "Title axis"},
            ],
            "series": [{"id": "min", "label": "Minimum", "values": [1, 2, 3]}],
            "max": 3,
        }
    )
    runtime = NodeMermaidRuntime()
    try:
        native_runtime = runtime.validate_and_render(native.code, 20)
        no_legend_runtime = runtime.validate_and_render(no_legend.code, 20)
        no_legend_fallback_runtime = runtime.validate_and_render(no_legend_fallback.code, 20)
        fallback_runtime = runtime.validate_and_render(fallback.code, 20)
        reserved_runtime = runtime.validate_and_render(reserved.code, 20)
    finally:
        runtime.close()

    assert native_runtime.syntax_valid and native_runtime.render_valid
    assert no_legend_runtime.syntax_valid and no_legend_runtime.render_valid
    assert no_legend_fallback_runtime.syntax_valid and no_legend_fallback_runtime.render_valid
    assert fallback_runtime.syntax_valid and fallback_runtime.render_valid
    assert reserved_runtime.syntax_valid and reserved_runtime.render_valid
    assert native_runtime.diagram_type == "radar"
    assert fallback_runtime.diagram_type == "flowchart-v2"
    assert native_runtime.svg is not None
    assert no_legend_runtime.svg is not None
    assert no_legend_fallback_runtime.svg is not None
    assert fallback_runtime.svg is not None
    native_root = ET.fromstring(native_runtime.svg)
    no_legend_root = ET.fromstring(no_legend_runtime.svg)
    no_legend_fallback_root = ET.fromstring(no_legend_fallback_runtime.svg)
    fallback_root = ET.fromstring(fallback_runtime.svg)
    assert native_root.get("viewBox") == "0 0 700 700"
    native_text = Counter(
        "".join(element.itertext())
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    )
    assert native_text == Counter(
        ["Accuracy", "Speed", "Safety", "Model A", "Model B", "Model comparison"]
    )
    assert "Model A" not in {
        "".join(element.itertext())
        for element in no_legend_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    }
    no_legend_fallback_text = Counter(
        "".join(element.itertext()).replace("\u200b", "")
        for element in no_legend_fallback_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        and "".join(element.itertext()).replace("\u200b", "")
    )
    assert no_legend_fallback_text == Counter(
        [
            "Model comparison",
            "Accuracy: 0",
            "Speed: 5",
            "Safety: 10",
            "Accuracy: 10",
            "Speed: 7.5",
            "Safety: 2.5",
        ]
    )
    assert "Model A" not in no_legend_fallback_text
    assert "Model B" not in no_legend_fallback_text
    assert sum("radarCurve-" in element.get("class", "") for element in native_root.iter()) == 2
    assert sum(element.get("class") == "radarGraticule" for element in native_root.iter()) == 5
    assert not any(
        "nan" in value.casefold() or "infinity" in value.casefold()
        for element in native_root.iter()
        for value in element.attrib.values()
    )
    fallback_canvas = " ".join(" ".join(fallback_root.itertext()).split())
    for expected in ("Model A", "Accuracy: -2.5", "Speed: 0", "Safety: 4"):
        assert expected in fallback_canvas
    assert not any(
        element.tag.rsplit("}", 1)[-1] == "path" and "flowchart-link" in element.get("class", "")
        for element in fallback_root.iter()
    )


class _RadarRejectingRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: int) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("radar-beta"):
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="radar",
                error="forced native rejection",
            )
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="flowchart-v2",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                "<text>Model A Accuracy: 0 Speed: 5 Safety: 10</text></svg>"
            ),
        )

    def close(self) -> None:
        pass


class _RadarAcceptingRuntime:
    def validate_and_render(self, code: str, timeout_seconds: int) -> RuntimeResult:
        del code, timeout_seconds
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="radar",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                "<text>Model comparison Accuracy Speed Safety Model A Model B</text></svg>"
            ),
        )

    def close(self) -> None:
        pass


def test_native_radar_direct_component_provenance_can_pass_publication_gate() -> None:
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["radar"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="radar", ir=NATIVE_RADAR_IR)],
        evidence=[
            VisualEvidence(
                id="ocr-title",
                kind="ocr_token",
                text="Model comparison",
                bbox=(30, 12, 70, 20),
            ),
            VisualEvidence(
                id="ocr-description",
                kind="vector_text",
                text="Two models across three dimensions.",
                bbox=(15, 25, 85, 40),
            ),
            VisualEvidence(
                id="ocr-accuracy",
                kind="ocr_token",
                text="Accuracy",
                bbox=(42, 2, 58, 8),
            ),
            VisualEvidence(
                id="ocr-speed",
                kind="ocr_token",
                text="Speed",
                bbox=(78, 53, 92, 62),
            ),
            VisualEvidence(
                id="ocr-safety",
                kind="ocr_token",
                text="Safety",
                bbox=(8, 53, 22, 62),
            ),
            VisualEvidence(
                id="curve-a",
                kind="vector_text",
                text="Model A 0 5 10",
                bbox=(8, 78, 42, 87),
            ),
            VisualEvidence(
                id="curve-b",
                kind="vector_text",
                text="Model B 10 7.5 2.5",
                bbox=(58, 78, 92, 87),
            ),
        ],
    )
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_RadarAcceptingRuntime(), config.security_profile),
    ).reconstruct(
        "native-radar-provenance",
        "source.png",
        Image.new("RGB", (100, 100), "white"),
        ocr_texts=[
            "Model comparison Accuracy Speed Safety Model A Model B 0 5 10 10 7.5 2.5 5 10 0"
        ],
    )

    assert result.selected is not None
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert result.selected.aggregate_score is not None
    assert result.publish


def test_native_radar_rejection_retries_flowchart_in_same_candidate_slot() -> None:
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["radar"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="radar", ir=NATIVE_RADAR_IR)],
        evidence=[
            VisualEvidence(
                id="ocr-title",
                kind="ocr_token",
                text="Model comparison",
                bbox=(30, 12, 70, 20),
            ),
            VisualEvidence(
                id="ocr-description",
                kind="vector_text",
                text="Two models across three dimensions.",
                bbox=(15, 25, 85, 40),
            ),
            VisualEvidence(
                id="ocr-accuracy",
                kind="ocr_token",
                text="Accuracy",
                bbox=(42, 2, 58, 8),
            ),
            VisualEvidence(
                id="ocr-speed",
                kind="ocr_token",
                text="Speed",
                bbox=(78, 53, 92, 62),
            ),
            VisualEvidence(
                id="ocr-safety",
                kind="ocr_token",
                text="Safety",
                bbox=(8, 53, 22, 62),
            ),
            VisualEvidence(
                id="curve-a",
                kind="vector_text",
                text="Model A 0 5 10",
                bbox=(8, 78, 42, 87),
            ),
            VisualEvidence(
                id="curve-b",
                kind="vector_text",
                text="Model B 10 7.5 2.5",
                bbox=(58, 78, 92, 87),
            ),
        ],
    )
    runtime = _RadarRejectingRuntime()
    config = MermaidConfig(candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "radar-runtime-fallback",
        "source.png",
        Image.new("RGB", (100, 100), "white"),
        ocr_texts=["Model comparison Accuracy Speed Safety Model A Model B 0 5 10 10 7.5 2.5"],
    )

    assert result.selected is not None
    selected = result.selected
    assert selected.candidate_id == "candidate-1"
    assert result.alternatives == []
    assert len(runtime.calls) == 2
    assert runtime.calls[0].startswith("radar-beta")
    assert runtime.calls[1].startswith("flowchart TB")
    assert selected.diagram_type == "radar"
    assert selected.emitted_diagram_type == "flowchart"
    assert selected.runtime_diagram_type == "flowchart-v2"
    assert selected.fallback_chain == ["radar", "flowchart"]
    assert selected.generated_scene_ir is not None
    assert selected.generated_scene_ir.reading_direction == "TB"
    assert selected.generated_scene_ir.relations == []
    assert selected.scores["numeric_consistency"] == 1
    assert any("same candidate slot" in warning for warning in selected.warnings)
    assert selected.repair_history[-1].operation == "runtime_portable_fallback"
    assert selected.repair_history[-1].accepted
