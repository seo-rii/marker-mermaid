from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.serializers import serialize_typed_ir_result
from marker_mermaid.serializers_charts_core import plan_xychart_records
from marker_mermaid.validation import NodeMermaidRuntime

NATIVE_XY_IR = {
    "title": "Quarterly trend",
    "description": "Revenue by quarter.",
    "x_axis": {
        "label": "Quarter",
        "categories": ["Q1", "Q2", "Q3"],
        "bbox": [0, 80, 100, 100],
        "evidence_ids": ["axis-x"],
    },
    "y_axis": {
        "label": "Revenue",
        "min": 0,
        "max": 100,
        "bbox": [0, 0, 20, 100],
        "evidence_ids": ["axis-y"],
    },
    "series": [
        {
            "kind": "line",
            "values": [20, 50, 80],
            "evidence_ids": ["curve-line"],
        },
        {
            "kind": "bar",
            "values": [10, 40, 70],
            "evidence_ids": ["bars"],
        },
    ],
}


def test_native_xy_scene_matches_axis_data_geometry_and_line_topology() -> None:
    result = serialize_typed_ir_result("xychart", NATIVE_XY_IR)
    plan = plan_xychart_records(NATIVE_XY_IR)
    scene = typed_ir_to_scene(
        "xychart",
        NATIVE_XY_IR,
        emitted_diagram_type=result.emitted_type,
    )

    assert result.emitted_type == "xychart"
    assert plan.native_supported
    assert scene is not None
    assert scene.reading_direction == "LR"
    assert scene.coordinate_space == "normalized"
    assert scene.groups == []
    assert [(element.id, element.role, element.text) for element in scene.elements[:2]] == [
        (plan.x_axis.scene_id, "axis", "Quarter"),
        (plan.y_axis.scene_id, "axis", "Revenue"),
    ]
    assert scene.elements[0].bbox == (0, 1, 1, 1)
    assert scene.elements[1].bbox == (0, 0, 0, 1)
    assert scene.elements[0].evidence_ids == ["axis-x"]
    assert scene.elements[1].evidence_ids == ["axis-y"]

    category_elements = scene.elements[2:5]
    assert [element.id for element in category_elements] == [
        category.scene_id for category in plan.x_axis.categories
    ]
    assert [element.text for element in category_elements] == ["Q1", "Q2", "Q3"]
    assert [element.bbox[:2] for element in category_elements] == pytest.approx(
        [category.normalized_point for category in plan.x_axis.categories]
    )
    assert all(element.evidence_ids == ["axis-x"] for element in category_elements)

    line_points = plan.series[0].points
    bar_points = plan.series[1].points
    data_elements = scene.elements[5:]
    assert [element.id for element in data_elements] == [
        *(point.scene_id for point in line_points),
        *(point.scene_id for point in bar_points),
    ]
    assert [element.role for element in data_elements] == [
        "data_point",
        "data_point",
        "data_point",
        "data_bar",
        "data_bar",
        "data_bar",
    ]
    assert all(element.text is None for element in data_elements)
    assert all(element.evidence_ids == ["curve-line"] for element in data_elements[:3])
    assert all(element.evidence_ids == ["bars"] for element in data_elements[3:])
    for element, point in zip(data_elements[:3], line_points, strict=True):
        assert point.normalized_point is not None
        assert element.bbox == (*point.normalized_point, *point.normalized_point)
    for element, point in zip(data_elements[3:], bar_points, strict=True):
        assert point.normalized_point is not None
        x_position, y_position = point.normalized_point
        assert element.bbox == (x_position, y_position, x_position, 1)

    assert [
        (relation.source_id, relation.target_id, relation.relation_type)
        for relation in scene.relations
    ] == [
        (line_points[0].scene_id, line_points[1].scene_id, "series_line"),
        (line_points[1].scene_id, line_points[2].scene_id, "series_line"),
    ]
    assert all(not relation.arrow_at_end for relation in scene.relations)
    assert all(relation.evidence_ids == ["curve-line"] for relation in scene.relations)
    assert scene.relations[0].polyline == [
        line_points[0].normalized_point,
        line_points[1].normalized_point,
    ]


def test_numeric_xy_points_keep_point_local_evidence_and_normalized_coordinates() -> None:
    ir = {
        "x_axis": {
            "label": "Time",
            "min": 0,
            "max": 10,
            "evidence_ids": ["axis-x"],
        },
        "y_axis": {
            "label": "Load",
            "min": -10,
            "max": 10,
            "evidence_ids": ["axis-y"],
        },
        "series": [
            {
                "kind": "line",
                "evidence_ids": ["curve"],
                "points": [
                    {"x": 0, "y": -10, "evidence_ids": ["point-a"]},
                    {"x": 5, "y": 0, "evidence_ids": ["point-b"]},
                    {"x": 10, "y": 10, "evidence_ids": ["point-c"]},
                ],
            }
        ],
    }

    plan = plan_xychart_records(ir)
    scene = typed_ir_to_scene("xychart", ir, emitted_diagram_type="xychart")

    assert plan.native_supported
    assert [point.normalized_point for point in plan.series[0].points] == [
        (0, 1),
        (0.5, 0.5),
        (1, 0),
    ]
    assert scene is not None
    point_elements = [element for element in scene.elements if element.role == "data_point"]
    assert [element.evidence_ids for element in point_elements] == [
        ["point-a"],
        ["point-b"],
        ["point-c"],
    ]
    assert scene.relations[0].evidence_ids == ["curve"]


def test_native_xy_semantic_text_excludes_accessibility_and_hidden_data_values() -> None:
    scene = typed_ir_to_scene("xychart", NATIVE_XY_IR, emitted_diagram_type="xychart")

    assert scene is not None
    assert list(
        typed_ir_semantic_texts(
            "xychart",
            NATIVE_XY_IR,
            scene,
            emitted_diagram_type="xychart",
        )
    ) == ["Quarterly trend", "Quarter", "Q1", "Q2", "Q3", "Revenue"]


def test_xy_exact_flowchart_fallback_has_only_disconnected_axis_and_data_cells() -> None:
    ir = deepcopy(NATIVE_XY_IR)
    ir["series"][0]["values"][1] = Decimal("50.0000000000000000001")

    result = serialize_typed_ir_result("xychart", ir)
    plan = plan_xychart_records(ir)
    scene = typed_ir_to_scene("xychart", ir, emitted_diagram_type=result.emitted_type)

    assert not plan.native_supported
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("xychart", "flowchart")
    assert " --> " not in result.code
    assert scene is not None
    assert scene.reading_direction == "TB"
    assert scene.coordinate_space == "pixels"
    assert scene.relations == []
    assert scene.groups == []
    expected_text = [
        plan.fallback_canvas_title,
        plan.x_axis.fallback_canvas_label,
        plan.y_axis.fallback_canvas_label,
        *(category.native_canvas_label for category in plan.x_axis.categories),
        *(point.fallback_canvas_label for series in plan.series for point in series.points),
    ]
    assert [element.text for element in scene.elements] == expected_text
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    assert [element.role for element in scene.elements[:6]] == [
        "title",
        "axis",
        "axis",
        "category",
        "category",
        "category",
    ]
    assert scene.elements[0].evidence_ids == []
    assert all(element.evidence_ids == ["axis-x"] for element in scene.elements[3:6])
    for element in scene.elements:
        assert f'    {element.id}["' in result.code
    assert f'    xy_title["{plan.fallback_source_title}"]' in result.code
    assert (
        f'    {plan.x_axis.scene_id}["{plan.x_axis.fallback_source_label}"]'
        in result.code
    )
    assert (
        f'    {plan.y_axis.scene_id}["{plan.y_axis.fallback_source_label}"]'
        in result.code
    )
    for category in plan.x_axis.categories:
        assert f'    {category.scene_id}["{category.native_source_label}"]' in result.code
    for series in plan.series:
        for point in series.points:
            assert f'    {point.scene_id}["{point.fallback_source_label}"]' in result.code
    assert list(
        typed_ir_semantic_texts(
            "xychart",
            ir,
            scene,
            emitted_diagram_type=result.emitted_type,
        )
    ) == expected_text


def test_xy_runtime_flowchart_projection_uses_the_same_plan_without_native_leakage() -> None:
    plan = plan_xychart_records(NATIVE_XY_IR)
    scene = typed_ir_to_scene("xychart", NATIVE_XY_IR, emitted_diagram_type="flowchart")

    assert plan.native_supported
    assert scene is not None
    assert scene.relations == []
    assert [element.id for element in scene.elements] == [
        "xy_title",
        plan.x_axis.scene_id,
        plan.y_axis.scene_id,
        *(category.scene_id for category in plan.x_axis.categories),
        *(point.scene_id for series in plan.series for point in series.points),
    ]
    assert [element.text for element in scene.elements] == [
        plan.fallback_canvas_title,
        plan.x_axis.fallback_canvas_label,
        plan.y_axis.fallback_canvas_label,
        *(category.native_canvas_label for category in plan.x_axis.categories),
        *(point.fallback_canvas_label for series in plan.series for point in series.points),
    ]
    assert list(
        typed_ir_semantic_texts(
            "xychart",
            NATIVE_XY_IR,
            scene,
            emitted_diagram_type="flowchart",
        )
    ) == [element.text for element in scene.elements]


@pytest.mark.integration
def test_xy_native_semantic_projection_matches_mermaid_11_16_visible_text() -> None:
    ir = {
        "title": "Observed trend",
        "acc_title": "Hidden accessible title",
        "acc_description": "Hidden accessible description 987654.",
        "x_axis": {"label": "Period", "categories": ["First", "Second"]},
        "y_axis": {"label": "Measure", "min": 0, "max": 100},
        "series": [{"kind": "line", "values": [Decimal("13.37"), Decimal("42.42")]}],
    }
    result = serialize_typed_ir_result("xychart", ir)
    runtime = NodeMermaidRuntime()
    try:
        rendered = runtime.validate_and_render(result.code, timeout_seconds=20)
    finally:
        runtime.close()

    assert result.emitted_type == "xychart"
    assert rendered.syntax_valid
    assert rendered.render_valid, rendered.error
    assert rendered.svg is not None
    root = ET.fromstring(rendered.svg)
    visible_text = " ".join(
        text.strip()
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        for text in element.itertext()
        if text.strip()
    )
    for expected in ("Observed trend", "Period", "First", "Second", "Measure"):
        assert expected in visible_text
    assert "Hidden accessible" not in visible_text
    assert "987654" not in visible_text
    assert "13.37" not in visible_text
    assert "42.42" not in visible_text
