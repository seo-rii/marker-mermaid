from __future__ import annotations

import math
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

import marker_mermaid.serializers_charts_core as chart_core
from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import (
    SerializationError,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.serializers_charts_core import (
    MAX_QUADRANT_POINTS,
    QUADRANT_FALLBACK_TEXT_COMPATIBILITY_WARNING,
    QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING,
    QUADRANT_NATIVE_TEXT_COMPATIBILITY_WARNING,
    plan_quadrant_records,
    serialize_quadrant,
)
from marker_mermaid.validation import NodeMermaidRuntime

SAFE_IR = {
    "title": "Portfolio map",
    "description": "Projects by reach and confidence.",
    "x_axis": {
        "low": "Low reach",
        "high": "High reach",
        "evidence_ids": ["axis-x", "axis-x"],
    },
    "y_axis": {
        "low": "Low confidence",
        "high": "High confidence",
        "evidence_ids": ["axis-y"],
    },
    "quadrants": {"quadrant-1": "Expand", "3": "Revisit"},
    "points": [
        {
            "label": "Project A",
            "x": 0.25,
            "y": 0.75,
            "evidence_ids": ["point-a"],
        },
        {
            "label": "Project B",
            "x": 0.8,
            "y": 0.1,
            "evidence_ids": ["point-b"],
        },
    ],
}


def _with_points(points: list[dict[str, object]]) -> dict[str, object]:
    return {
        "x_axis": {"low": "Low x", "high": "High x"},
        "y_axis": {"low": "Low y", "high": "High y"},
        "points": points,
    }


def test_quadrant_plan_freezes_source_records_slots_geometry_and_evidence() -> None:
    plan = plan_quadrant_records(SAFE_IR)

    assert plan.native_supported
    assert plan.total_points == 2
    assert plan.x_axis.source_record is SAFE_IR["x_axis"]
    assert plan.x_axis.evidence_ids == ("axis-x",)
    assert plan.points[0].source_record is SAFE_IR["points"][0]
    assert plan.points[0].evidence_ids == ("point-a",)
    assert plan.points[0].normalized_point == pytest.approx((0.25, 0.25))
    assert [slot.slot for slot in plan.quadrants] == [1, 2, 3, 4]
    assert [slot.label for slot in plan.quadrants] == ["Expand", "", "Revisit", ""]
    assert all(slot.evidence_ids == () for slot in plan.quadrants)
    assert plan.quadrants[0].normalized_bbox == (0.5, 0.0, 1.0, 0.5)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("description", "   "),
        ("acc_title", []),
        ("acc_description", 7),
    ],
)
def test_public_quadrant_metadata_gate_runs_before_accessibility_enrichment(
    field: str, value: object
) -> None:
    ir = {**SAFE_IR, field: value}

    with pytest.raises(SerializationError, match=field):
        serialize_typed_ir_result("quadrant", ir)
    with pytest.raises(SerializationError, match=field):
        serialize_runtime_fallback_result("quadrant", ir)


def test_quadrant_rejects_axis_and_point_object_reuse() -> None:
    axis = {"low": "Low", "high": "High"}
    point = {"label": "A", "x": 0.2, "y": 0.3}

    with pytest.raises(SerializationError, match="axes cannot reuse"):
        plan_quadrant_records({"x_axis": axis, "y_axis": axis, "points": [point]})
    with pytest.raises(SerializationError, match="cannot reuse"):
        plan_quadrant_records(
            {
                "x_axis": {"low": "Low", "high": "High"},
                "y_axis": {"low": "Low", "high": "High"},
                "points": [point, point],
            }
        )


@pytest.mark.parametrize(
    "quadrants",
    [
        {"1": None},
        ["One", "Two", None, "Four"],
    ],
)
def test_quadrant_rejects_explicit_null_slot_labels(quadrants: object) -> None:
    ir = _with_points([{"label": "A", "x": 0.2, "y": 0.3}])
    ir["quadrants"] = quadrants

    with pytest.raises(SerializationError, match="quadrant label .* non-empty text"):
        plan_quadrant_records(ir)


def test_quadrant_point_budget_is_hard_bounded() -> None:
    points = [
        {"label": f"P{index}", "x": index / MAX_QUADRANT_POINTS, "y": 0.5}
        for index in range(MAX_QUADRANT_POINTS)
    ]
    assert plan_quadrant_records(_with_points(points)).total_points == MAX_QUADRANT_POINTS

    with pytest.raises(SerializationError, match="point runtime limit"):
        plan_quadrant_records(
            _with_points(
                [
                    {
                        "label": f"P{index}",
                        "x": index / MAX_QUADRANT_POINTS,
                        "y": 0.5,
                    }
                    for index in range(MAX_QUADRANT_POINTS + 1)
                ]
            )
        )


@pytest.mark.parametrize(
    "coordinate",
    [Decimal("0.10000000000000000000000000000000000000001"), math.nextafter(0.0, 1.0)],
)
def test_binary64_loss_uses_exact_value_flowchart_fallback(coordinate: Decimal | float) -> None:
    ir = _with_points([{"label": "Precise", "x": coordinate, "y": Decimal("0.5")}])

    plan = plan_quadrant_records(ir)
    code, emitted_type, warning = serialize_quadrant(ir)

    assert not plan.native_supported
    assert emitted_type == "flowchart"
    assert warning is not None
    assert f"x {plan.points[0].x_text}, y 0.5" in code
    assert " --> " not in code


def test_binary64_y_that_collapses_to_canvas_bottom_uses_exact_fallback() -> None:
    ir = _with_points(
        [
            {
                "label": "Near zero",
                "x": Decimal("0.5"),
                "y": Decimal("0.00000000000000000001"),
            }
        ]
    )

    plan = plan_quadrant_records(ir)
    code, emitted_type, warning = serialize_quadrant(ir)

    assert not plan.native_supported
    assert any("interior progress" in item for item in plan.native_limitations)
    assert emitted_type == "flowchart"
    assert warning is not None
    assert "x 0.5, y 0.00000000000000000001" in code


@pytest.mark.parametrize(
    "points",
    [
        [
            {"label": "First", "x": 0.5, "y": 0.5},
            {"label": "Second", "x": 0.5, "y": 0.5},
        ],
        [
            {"label": "First", "x": 0.5, "y": 0.5},
            {"label": "Second", "x": 0.501, "y": 0.501},
        ],
    ],
)
def test_duplicate_or_near_native_geometry_uses_disconnected_fallback(
    points: list[dict[str, object]],
) -> None:
    ir = _with_points(points)

    plan = plan_quadrant_records(ir)
    code, emitted_type, _warning = serialize_quadrant(ir)

    assert not plan.native_supported
    assert any("overlap" in limitation for limitation in plan.native_limitations)
    assert emitted_type == "flowchart"
    assert " --> " not in code
    assert 'quadrant_point_1["First · x 0.5, y 0.5"]' in code
    assert 'quadrant_point_2["Second · x ' in code


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "A" * 120),
        ("axis", "A" * 120),
        ("quadrant", "A" * 120),
        ("point", "A" * 120),
    ],
)
def test_pinned_canvas_text_clipping_uses_fallback(field: str, value: str) -> None:
    ir = _with_points([{"label": "Point", "x": 0.2, "y": 0.2}])
    if field == "title":
        ir["title"] = value
    elif field == "axis":
        ir["x_axis"] = {"low": value, "high": value + "B"}
    elif field == "quadrant":
        ir["quadrants"] = {"1": value}
    else:
        ir["points"] = [{"label": value, "x": 0.2, "y": 0.2}]

    plan = plan_quadrant_records(ir)
    assert not plan.native_supported
    assert any("clipped" in limitation for limitation in plan.native_limitations)
    assert serialize_quadrant(ir)[1] == "flowchart"


def test_text_compatibility_is_visible_strict_safe_and_warned() -> None:
    ir = {
        "title": 'Portfolio "A"; review',
        "x_axis": {"low": "https://low.example", "high": "High\\reach"},
        "y_axis": {"low": "Low", "high": "High"},
        "quadrants": {"1": "<Expand>"},
        "points": [{"label": 'Project "A"', "x": 0.2, "y": 0.2}],
    }

    result = serialize_typed_ir_result("quadrant", ir)
    plan = plan_quadrant_records(ir)

    assert plan.native_compatibility_substitutions
    assert "&quot;" not in result.code
    assert "″" in result.code
    assert "；" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    expected_warning = (
        QUADRANT_NATIVE_TEXT_COMPATIBILITY_WARNING
        if result.emitted_type == "quadrant"
        else QUADRANT_FALLBACK_TEXT_COMPATIBILITY_WARNING
    )
    assert expected_warning in result.warnings


def test_forced_runtime_rejection_uses_same_slot_exact_fallback() -> None:
    result = serialize_runtime_fallback_result("quadrant", SAFE_IR)

    assert result is not None
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("quadrant", "flowchart")
    assert " --> " not in result.code
    assert "upper right quadrant · Expand" in result.code
    assert "quadrant-1" not in result.code
    assert "Project A · x 0.25, y 0.75" in result.code


def test_native_quadrant_discloses_pinned_point_paint_compatibility_only() -> None:
    native = serialize_typed_ir_result("quadrant", SAFE_IR)
    fallback = serialize_runtime_fallback_result("quadrant", SAFE_IR)

    assert native.emitted_type == "quadrant"
    assert QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING in native.warnings
    assert fallback is not None
    assert fallback.emitted_type == "flowchart"
    assert QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING not in fallback.warnings


def test_quadrant_output_budget_counts_utf16_before_point_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points = [
        {"label": ("😀" * 1_500) + chr(65 + index), "x": index / 19, "y": 0.5}
        for index in range(20)
    ]
    projected_oversized_labels: list[str] = []
    original_neutralize = chart_core._xy_neutralize_source_text

    def record_point_projection(text: str) -> str:
        if text.startswith("😀"):
            projected_oversized_labels.append(text)
        return original_neutralize(text)

    monkeypatch.setattr(chart_core, "_xy_neutralize_source_text", record_point_projection)

    with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
        serialize_quadrant(_with_points(points))
    assert projected_oversized_labels == []


def test_fallback_source_budget_does_not_reject_a_fitting_native_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_code = serialize_quadrant(SAFE_IR)[0]
    fallback_code = serialize_quadrant(SAFE_IR, native_runtime_valid=False)[0]
    native_units = len(native_code.encode("utf-16-le")) // 2
    fallback_units = len(fallback_code.encode("utf-16-le")) // 2
    terminal_specific_limit = (native_units + fallback_units) // 2
    assert native_units < terminal_specific_limit < fallback_units

    monkeypatch.setattr(
        chart_core,
        "MAX_QUADRANT_OUTPUT_CHARS",
        terminal_specific_limit,
    )

    plan = plan_quadrant_records(SAFE_IR)
    code, emitted_type, warning = serialize_quadrant(SAFE_IR)

    assert plan.native_supported
    assert not plan.flowchart_supported
    assert emitted_type == "quadrant"
    assert warning is None
    assert len(code.encode("utf-16-le")) // 2 <= terminal_specific_limit
    with pytest.raises(SerializationError, match="bounded source or point runtime limits"):
        serialize_quadrant(SAFE_IR, native_runtime_valid=False)


@pytest.mark.integration
def test_mermaid_11_16_native_and_fallback_render_with_exact_visible_semantics() -> None:
    native = serialize_typed_ir_result("quadrant", SAFE_IR)
    fallback = serialize_runtime_fallback_result("quadrant", SAFE_IR)
    assert native.emitted_type == "quadrant"
    assert fallback is not None

    runtime = NodeMermaidRuntime()
    try:
        native_render = runtime.validate_and_render(native.code, timeout_seconds=20)
        fallback_render = runtime.validate_and_render(fallback.code, timeout_seconds=20)
    finally:
        runtime.close()

    assert native_render.syntax_valid and native_render.render_valid, native_render.error
    assert fallback_render.syntax_valid and fallback_render.render_valid, fallback_render.error
    assert native_render.svg is not None
    assert fallback_render.svg is not None
    native_root = ET.fromstring(native_render.svg)
    native_text = [
        "".join(element.itertext())
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    ]
    circles = [
        element for element in native_root.iter() if element.tag.rsplit("}", 1)[-1] == "circle"
    ]
    assert {"Project A", "Project B"} <= set(native_text)
    assert len(circles) == 2
    assert all("NaN%" in (circle.get("fill") or "") for circle in circles)
    assert QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING in native.warnings
    fallback_root = ET.fromstring(fallback_render.svg)
    fallback_text = " ".join(" ".join(fallback_root.itertext()).split())
    assert "Project A · x 0.25, y 0.75" in fallback_text
    assert "upper right quadrant · Expand" in fallback_text
