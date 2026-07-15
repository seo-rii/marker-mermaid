from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import (
    SerializationError,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.serializers_charts_core import (
    MAX_XY_FLOWCHART_POINTS,
    MAX_XY_NATIVE_SERIES,
    XY_FALLBACK_TEXT_COMPATIBILITY_WARNING,
    XY_RUNTIME_FALLBACK_WARNING,
    plan_xychart_records,
    serialize_xychart,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


def _categorical_ir(*, kind: str = "line") -> dict[str, object]:
    return {
        "title": "Quarterly revenue",
        "x_axis": {
            "label": "Quarter",
            "categories": ["Q1", "Q2", "Q3"],
            "evidence_ids": ["x-axis"],
        },
        "y_axis": {
            "label": "Revenue",
            "min": 0,
            "max": 100,
            "evidence_ids": ["y-axis"],
        },
        "series": [
            {
                "kind": kind,
                "values": [20, 40, 70],
                "evidence_ids": ["series-1"],
            }
        ],
    }


def _numeric_ir(values: list[object], *, minimum: object = 0, maximum: object = 1):
    return {
        "x_axis": {"min": minimum, "max": maximum},
        "y_axis": {"min": 0, "max": 100},
        "series": [{"kind": "line", "values": values}],
    }


def test_xy_plan_freezes_native_axis_series_points_and_evidence() -> None:
    plan = plan_xychart_records(_categorical_ir())

    assert plan.native_supported
    assert plan.flowchart_supported
    assert plan.total_points == 3
    assert [category.scene_id for category in plan.x_axis.categories] == [
        "xy_category_1",
        "xy_category_2",
        "xy_category_3",
    ]
    assert [category.normalized_point for category in plan.x_axis.categories] == [
        (0.0, 1.0),
        (0.5, 1.0),
        (1.0, 1.0),
    ]
    assert plan.x_axis.evidence_ids == ("x-axis",)
    assert plan.series[0].emitted_id == "xy_series_1"
    assert plan.series[0].evidence_ids == ("series-1",)
    assert [point.scene_id for point in plan.series[0].points] == [
        "xy_series_1_point_1",
        "xy_series_1_point_2",
        "xy_series_1_point_3",
    ]
    assert [point.y_text for point in plan.series[0].points] == ["20", "40", "70"]
    assert all(
        point.source_record is plan.series[0].source_record
        for point in plan.series[0].points
    )


def test_numeric_grid_that_silently_drops_last_mermaid_point_uses_fallback() -> None:
    ir = _numeric_ir(list(range(10)))

    plan = plan_xychart_records(ir)
    code, emitted_type, warning = serialize_xychart(ir)

    assert not plan.native_supported
    assert any("bounded exact progress" in item for item in plan.native_limitations)
    assert emitted_type == "flowchart"
    assert warning is not None and "drop" in warning
    assert 'xy_series_1_point_10["line · value 9"]' in code
    assert " --> " not in code


def test_numeric_grid_that_stalls_near_binary64_integer_limit_uses_fallback() -> None:
    ir = _numeric_ir(
        [0, 1, 2],
        minimum=9_007_199_254_740_990,
        maximum=9_007_199_254_740_991,
    )

    plan = plan_xychart_records(ir)
    code, emitted_type, _warning = serialize_xychart(ir)

    assert not plan.native_supported
    assert any("bounded exact progress" in item for item in plan.native_limitations)
    assert emitted_type == "flowchart"
    assert "9007199254740990 to 9007199254740991" in code


def test_safe_numeric_grid_keeps_exact_native_points() -> None:
    ir = _numeric_ir([10, 20, 30], minimum=0, maximum=10)

    plan = plan_xychart_records(ir)
    code, emitted_type, warning = serialize_xychart(ir)

    assert plan.native_supported
    assert emitted_type == "xychart"
    assert warning is None
    assert [point.native_x for point in plan.series[0].points] == [0.0, 5.0, 10.0]
    assert [point.normalized_point for point in plan.series[0].points] == [
        (0.0, 0.9),
        (0.5, 0.8),
        (1.0, 0.7),
    ]
    assert "line [10, 20, 30]" in code


@pytest.mark.parametrize("axis_name", ["x_axis", "y_axis"])
def test_subnormal_binary64_axis_span_uses_fallback(axis_name: str) -> None:
    ir = {
        "x_axis": {"min": 0, "max": 1},
        "y_axis": {"min": 0, "max": 1},
        "series": [{"kind": "bar", "values": [Decimal("1e-307")]}],
    }
    ir[axis_name] = {
        "min": Decimal("1e-307"),
        "max": Decimal("1.0000000000000001e-307"),
    }
    if axis_name == "y_axis":
        ir["series"] = [{"kind": "bar", "values": [Decimal("1e-307")]}]

    plan = plan_xychart_records(ir)

    assert not plan.native_supported
    assert any(
        f"positive normal finite {axis_name[0]}-axis span" in item
        for item in plan.native_limitations
    )
    assert serialize_xychart(ir)[1] == "flowchart"


def test_subnormal_numeric_grid_step_uses_fallback() -> None:
    ir = _numeric_ir(
        [1] * MAX_XY_FLOWCHART_POINTS,
        minimum=Decimal("0"),
        maximum=Decimal("1e-307"),
    )

    plan = plan_xychart_records(ir)

    assert not plan.native_supported
    assert "bounded exact progress" in " ".join(plan.native_limitations)
    assert serialize_xychart(ir)[1] == "flowchart"


@pytest.mark.parametrize(
    ("ir", "limitation"),
    [
        (
            _numeric_ir(
                [Decimal("0.5")],
                minimum=Decimal("1.00000000000000000001"),
                maximum=Decimal("1.00000000000000000002"),
            ),
            "x-axis bounds",
        ),
        (
            {
                "x_axis": {"categories": ["A", "B"]},
                "y_axis": {"min": 0, "max": 1},
                "series": [
                    {"kind": "bar", "values": [Decimal("1e-400"), Decimal("0.5")]}
                ],
            },
            "y value",
        ),
    ],
)
def test_binary64_collapse_and_subnormal_values_use_exact_fallback(
    ir: dict[str, object], limitation: str
) -> None:
    plan = plan_xychart_records(ir)
    code, emitted_type, _warning = serialize_xychart(ir)

    assert not plan.native_supported
    assert any(limitation in item for item in plan.native_limitations)
    assert emitted_type == "flowchart"
    assert "0.000000000000000000000000000000000000000000000000" in code or (
        "1.00000000000000000001 to 1.00000000000000000002" in code
    )


def test_one_point_line_falls_back_but_one_bar_remains_visible_native() -> None:
    line = {
        "x_axis": {"categories": ["Only"]},
        "y_axis": {"min": 0, "max": 2},
        "series": [{"kind": "line", "values": [1]}],
    }
    bar = {
        **line,
        "series": [{"kind": "bar", "values": [1]}],
    }

    assert serialize_xychart(line)[1] == "flowchart"
    assert serialize_xychart(bar)[1] == "xychart"


def test_zero_height_and_overlapping_series_use_fallback() -> None:
    zero_height = {
        "x_axis": {"categories": ["A"]},
        "y_axis": {"min": 0, "max": 2},
        "series": [{"kind": "bar", "values": [0]}],
    }
    two_bars = {
        "x_axis": {"categories": ["A", "B"]},
        "y_axis": {"min": 0, "max": 3},
        "series": [
            {"kind": "bar", "values": [1, 2]},
            {"kind": "bar", "values": [2, 3]},
        ],
    }
    duplicate_lines = {
        "x_axis": {"categories": ["A", "B"]},
        "y_axis": {"min": 0, "max": 3},
        "series": [
            {"kind": "line", "values": [1, 2]},
            {"kind": "line", "values": [1, 2]},
        ],
    }

    assert "zero-height bar" in " ".join(
        plan_xychart_records(zero_height).native_limitations
    )
    assert "multiple bar series" in " ".join(
        plan_xychart_records(two_bars).native_limitations
    )
    assert "identical line series" in " ".join(
        plan_xychart_records(duplicate_lines).native_limitations
    )
    assert serialize_xychart(zero_height)[1] == "flowchart"
    assert serialize_xychart(two_bars)[1] == "flowchart"
    assert serialize_xychart(duplicate_lines)[1] == "flowchart"


def test_native_palette_limit_falls_back_after_ten_series() -> None:
    def ir_with_series(count: int) -> dict[str, object]:
        return {
            "x_axis": {"categories": ["A", "B"]},
            "y_axis": {"min": 0, "max": 20},
            "series": [
                {"kind": "line", "values": [index, index + 1]}
                for index in range(count)
            ],
        }

    assert len(plan_xychart_records(ir_with_series(MAX_XY_NATIVE_SERIES)).series) == 10
    assert serialize_xychart(ir_with_series(MAX_XY_NATIVE_SERIES))[1] == "xychart"
    plan = plan_xychart_records(ir_with_series(MAX_XY_NATIVE_SERIES + 1))
    assert not plan.native_supported
    assert "color palette" in " ".join(plan.native_limitations)
    assert serialize_xychart(ir_with_series(MAX_XY_NATIVE_SERIES + 1))[1] == "flowchart"


def test_point_budget_is_exact_and_checked_before_render() -> None:
    accepted = {
        "x_axis": {"categories": [f"C{index}" for index in range(MAX_XY_FLOWCHART_POINTS)]},
        "y_axis": {"min": 0, "max": 1},
        "series": [{"kind": "bar", "values": [1] * MAX_XY_FLOWCHART_POINTS}],
    }
    rejected = {
        "x_axis": {
            "categories": [f"C{index}" for index in range(MAX_XY_FLOWCHART_POINTS + 1)]
        },
        "y_axis": {"min": 0, "max": 1},
        "series": [{"kind": "bar", "values": [1] * (MAX_XY_FLOWCHART_POINTS + 1)}],
    }

    assert plan_xychart_records(accepted).total_points == MAX_XY_FLOWCHART_POINTS
    with pytest.raises(SerializationError, match="256-point runtime limit"):
        plan_xychart_records(rejected)


def test_explicit_points_keep_source_records_and_exact_fallback_values() -> None:
    first = {"x": 0, "y": 1, "evidence_ids": ["point-a"]}
    second = {"x": 10, "y": 2, "evidence_ids": ["point-b"]}
    ir = {
        "x_axis": {"min": 0, "max": 10},
        "y_axis": {"min": 0, "max": 2},
        "series": [{"kind": "line", "points": [first, second]}],
    }

    plan = plan_xychart_records(ir)
    assert plan.series[0].points[0].source_record is first
    assert plan.series[0].points[0].x_text == "0"
    assert plan.series[0].points[1].evidence_ids == ("point-b",)
    code, emitted_type, warning = serialize_xychart(ir, native_runtime_valid=False)
    assert emitted_type == "flowchart"
    assert warning == XY_RUNTIME_FALLBACK_WARNING
    assert 'xy_series_1_point_1["line · x 0, y 1"]' in code
    assert 'xy_series_1_point_2["line · x 10, y 2"]' in code


def test_categorical_fallback_cells_bind_each_value_to_its_category() -> None:
    ir = _categorical_ir(kind="bar")

    plan = plan_xychart_records(ir)
    code = serialize_xychart(ir, native_runtime_valid=False)[0]

    assert plan.fallback_canvas_title == "Quarterly revenue"
    assert plan.fallback_source_title == "Quarterly revenue"
    assert plan.series[0].points[0].fallback_canvas_label == "bar · Q1: value 20"
    assert 'xy_title["Quarterly revenue"]' in code
    assert 'xy_series_1_point_1["bar · Q1: value 20"]' in code
    assert 'xy_series_1_point_3["bar · Q3: value 70"]' in code


def test_non_uniform_explicit_points_use_exact_fallback_instead_of_inventing_geometry() -> None:
    ir = {
        "x_axis": {"min": 0, "max": 10},
        "y_axis": {"min": 0, "max": 3},
        "series": [
            {
                "kind": "line",
                "points": [{"x": 0, "y": 1}, {"x": 3, "y": 2}, {"x": 10, "y": 3}],
            }
        ],
    }

    plan = plan_xychart_records(ir)
    code, emitted_type, _warning = serialize_xychart(ir)

    assert not plan.native_supported
    assert "not on the exact uniform source grid" in " ".join(plan.native_limitations)
    assert emitted_type == "flowchart"
    assert 'xy_series_1_point_2["line · x 3, y 2"]' in code


def test_text_projection_is_strict_safe_and_tracks_visible_substitutions() -> None:
    ir = {
        "title": 'Title "quoted" \\ path; %% https://example.com',
        "acc_title": 'Accessible "quoted" #35;',
        "acc_description": "Description &amp; #60; https://example.com",
        "x_axis": {
            "label": 'X "quoted" \\ path',
            "categories": ['A "quoted" \\ path; click callback(foo)'],
        },
        "y_axis": {"label": "Y", "min": 0, "max": 2},
        "series": [{"kind": "bar", "values": [1]}],
    }

    plan = plan_xychart_records(ir)
    code, emitted_type, _warning = serialize_xychart(ir)

    assert emitted_type == "xychart"
    assert plan.native_compatibility_substitutions
    assert plan.native_canvas_title == 'Title ″quoted″ ∖ path; %% https://example.com'
    assert "&quot;" not in code
    assert "https://example.com" not in code
    assert _remove_zero_width(code).count("https://example.com") == 2
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_native_category_collision_after_text_substitution_uses_fallback() -> None:
    ir = {
        "x_axis": {"categories": ['A "quote"', "A ″quote″"]},
        "y_axis": {"min": 0, "max": 2},
        "series": [{"kind": "bar", "values": [1, 2]}],
    }

    plan = plan_xychart_records(ir)

    assert not plan.native_supported
    assert "categories collide" in " ".join(plan.native_limitations)
    assert serialize_xychart(ir)[1] == "flowchart"


def _remove_zero_width(value: str) -> str:
    return value.replace("\u200b", "")


def _svg_text(value: str) -> str:
    return " ".join(
        " ".join(_remove_zero_width(text).split())
        for text in ET.fromstring(value).itertext()
        if text.strip()
    )


@pytest.mark.parametrize(
    "field",
    ["title", "description", "acc_title", "acc_description"],
)
def test_explicit_accessibility_metadata_requires_text(field: str) -> None:
    ir = _categorical_ir(kind="bar")
    ir[field] = 7
    with pytest.raises(SerializationError, match=rf"xychart {field} must be text"):
        plan_xychart_records(ir)


def test_public_serializers_validate_metadata_before_accessibility_enrichment() -> None:
    ir = _categorical_ir(kind="bar")
    ir["acc_title"] = 7

    with pytest.raises(SerializationError, match="xychart acc_title must be text"):
        serialize_typed_ir_result("xychart", ir)
    with pytest.raises(SerializationError, match="xychart acc_title must be text"):
        serialize_runtime_fallback_result("xychart", ir)


def test_objects_cannot_be_reused_and_bad_evidence_is_record_local() -> None:
    shared_axis = {"min": 0, "max": 2}
    with pytest.raises(SerializationError, match="axes cannot reuse"):
        plan_xychart_records(
            {
                "x_axis": shared_axis,
                "y_axis": shared_axis,
                "series": [{"kind": "line", "values": [1, 2]}],
            }
        )

    shared_point = {"x": 0, "y": 1}
    with pytest.raises(SerializationError, match="cannot reuse one object"):
        plan_xychart_records(
            {
                "x_axis": {"min": 0, "max": 2},
                "y_axis": {"min": 0, "max": 2},
                "series": [{"kind": "line", "points": [shared_point, shared_point]}],
            }
        )

    isolated = _categorical_ir(kind="bar")
    isolated["series"][0]["evidence_ids"] = [""]  # type: ignore[index]
    plan = plan_xychart_records(isolated)
    assert plan.x_axis.evidence_ids == ("x-axis",)
    assert plan.series[0].evidence_ids == ()


def test_output_character_budget_fails_before_runtime() -> None:
    labels = [f"category-{index}-" + "x" * 220 for index in range(MAX_XY_FLOWCHART_POINTS)]
    ir = {
        "x_axis": {"categories": labels},
        "y_axis": {"min": 0, "max": 1},
        "series": [{"kind": "bar", "values": [1] * len(labels)}],
    }

    with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
        serialize_xychart(ir)


def test_registry_declares_planner_and_runtime_fallbacks_with_same_slot() -> None:
    unsafe = _numeric_ir(list(range(10)))
    planned = serialize_typed_ir_result("xychart", unsafe)
    assert planned.emitted_type == "flowchart"
    assert planned.fallback_chain == ("xychart", "flowchart")

    safe = _categorical_ir()
    runtime = serialize_runtime_fallback_result("xychart", safe)
    assert runtime is not None
    assert runtime.emitted_type == "flowchart"
    assert runtime.fallback_chain == ("xychart", "flowchart")
    assert XY_RUNTIME_FALLBACK_WARNING in runtime.warnings


def test_registry_reports_fallback_text_compatibility() -> None:
    ir = _numeric_ir(list(range(10)))
    ir["title"] = 'Title "quoted"'

    result = serialize_typed_ir_result("xychart", ir)

    assert result.emitted_type == "flowchart"
    assert XY_FALLBACK_TEXT_COMPATIBILITY_WARNING in result.warnings


@pytest.mark.integration
def test_xy_native_and_fallback_parse_and_render_with_mermaid_11_16() -> None:
    native_ir = {
        "title": 'Title "quoted" \\ path; %% https://example.com',
        "acc_title": 'Accessible "quoted" #35;',
        "acc_description": "Description &amp; #60;",
        "x_axis": {"label": 'X "quoted"', "categories": ["A", "B"]},
        "y_axis": {"label": "Y", "min": 0, "max": 2},
        "series": [{"kind": "bar", "values": [1, 2]}],
    }
    fallback_ir = _numeric_ir(list(range(10)))
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        native_code = serialize_xychart(native_ir)[0]
        native = validator.validate(native_code, 20)
        assert native.runtime.syntax_valid, (native_code, native.runtime.error)
        assert native.runtime.render_valid, (native_code, native.runtime.error)
        assert native.runtime.diagram_type == "xychart"
        assert "Accessible \"quoted\" #35;" in _remove_zero_width(native.runtime.svg or "")
        assert "Title ″quoted″ ∖ path; %% https://example.com" in _remove_zero_width(
            native.runtime.svg or ""
        )

        safe_numeric_code = serialize_xychart(
            _numeric_ir([10, 20, 30], minimum=0, maximum=10)
        )[0]
        safe_numeric = validator.validate(safe_numeric_code, 20)
        assert safe_numeric.runtime.syntax_valid, (
            safe_numeric_code,
            safe_numeric.runtime.error,
        )
        assert safe_numeric.runtime.render_valid, (
            safe_numeric_code,
            safe_numeric.runtime.error,
        )
        safe_root = ET.fromstring(safe_numeric.runtime.svg or "")
        three_point_paths = [
            element.attrib.get("d", "")
            for element in safe_root.iter()
            if element.tag.rsplit("}", 1)[-1] == "path"
            and element.attrib.get("d", "").count("L") == 2
        ]
        assert len(three_point_paths) == 1
        coordinates = re.findall(
            r"[ML](-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
            three_point_paths[0],
        )
        assert len(coordinates) == 3
        x_positions = [float(x) for x, _y in coordinates]
        assert x_positions[0] < x_positions[1] < x_positions[2]

        fallback_code = serialize_xychart(fallback_ir)[0]
        fallback = validator.validate(fallback_code, 20)
        assert fallback.runtime.syntax_valid, (fallback_code, fallback.runtime.error)
        assert fallback.runtime.render_valid, (fallback_code, fallback.runtime.error)
        assert fallback.runtime.diagram_type == "flowchart-v2"
        assert "line · value 9" in _svg_text(fallback.runtime.svg or "")
    finally:
        runtime.close()
