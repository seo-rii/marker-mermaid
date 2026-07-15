from __future__ import annotations

import re
from xml.etree import ElementTree as ET

import pytest

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.serializers import (
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.serializers_charts_core import plan_quadrant_records
from marker_mermaid.validation import NodeMermaidRuntime

NATIVE_QUADRANT_IR = {
    "title": "Portfolio map",
    "acc_title": "Hidden accessible title",
    "acc_description": "Hidden accessible description 987654.",
    "x_axis": {
        "low": "Low reach",
        "high": "High reach",
        "bbox": [0, 90, 100, 100],
        "evidence_ids": ["axis-x"],
    },
    "y_axis": {
        "low": "Low confidence",
        "high": "High confidence",
        "bbox": [0, 0, 10, 100],
        "evidence_ids": ["axis-y"],
    },
    "quadrants": {
        "quadrant-1": "Expand",
        "quadrant-3": "Revisit",
    },
    "points": [
        {
            "label": "Project A",
            "x": 0.25,
            "y": 0.75,
            "bbox": [20, 20, 30, 30],
            "evidence_ids": ["point-a"],
        },
        {
            "label": "Project B",
            "x": 0.8,
            "y": 0.1,
            "bbox": [75, 80, 85, 90],
            "evidence_ids": ["point-b"],
        },
    ],
}


def test_native_quadrant_scene_matches_terminal_geometry_without_membership_invention() -> None:
    result = serialize_typed_ir_result("quadrant", NATIVE_QUADRANT_IR)
    plan = plan_quadrant_records(NATIVE_QUADRANT_IR)
    scene = typed_ir_to_scene(
        "quadrant",
        NATIVE_QUADRANT_IR,
        emitted_diagram_type=result.emitted_type,
    )

    assert result.emitted_type == "quadrant"
    assert plan.native_supported
    assert scene is not None
    assert scene.coordinate_space == "normalized"
    assert scene.reading_direction == "unknown"
    assert scene.relations == []

    axis_elements = scene.elements[:4]
    assert [(element.id, element.text) for element in axis_elements] == [
        ("quadrant_x_axis_low", plan.x_axis.native_canvas_low),
        ("quadrant_x_axis_high", plan.x_axis.native_canvas_high),
        ("quadrant_y_axis_low", plan.y_axis.native_canvas_low),
        ("quadrant_y_axis_high", plan.y_axis.native_canvas_high),
    ]
    assert [element.bbox[:2] for element in axis_elements] == [
        plan.x_axis.normalized_low_point,
        plan.x_axis.normalized_high_point,
        plan.y_axis.normalized_low_point,
        plan.y_axis.normalized_high_point,
    ]
    assert [element.evidence_ids for element in axis_elements] == [
        ["axis-x"],
        ["axis-x"],
        ["axis-y"],
        ["axis-y"],
    ]
    assert all(element.role == "axis_endpoint" for element in axis_elements)

    point_elements = scene.elements[4:]
    assert [element.id for element in point_elements] == [
        point.scene_id for point in plan.points
    ]
    assert [element.text for element in point_elements] == ["Project A", "Project B"]
    assert [element.bbox[:2] for element in point_elements] == pytest.approx(
        [(0.25, 0.25), (0.8, 0.9)]
    )
    assert [element.evidence_ids for element in point_elements] == [
        ["point-a"],
        ["point-b"],
    ]
    assert all(
        element.role == "data_point" and element.shape == "circle"
        for element in point_elements
    )

    assert len(scene.groups) == 4
    assert [(group.id, group.label, group.bbox) for group in scene.groups] == [
        ("quadrant_slot_1", "Expand", (0.5, 0.0, 1.0, 0.5)),
        ("quadrant_slot_2", None, (0.0, 0.0, 0.5, 0.5)),
        ("quadrant_slot_3", "Revisit", (0.0, 0.5, 0.5, 1.0)),
        ("quadrant_slot_4", None, (0.5, 0.5, 1.0, 1.0)),
    ]
    assert all(group.role == "quadrant" and group.member_ids == [] for group in scene.groups)


def test_native_quadrant_semantic_text_is_only_visible_canvas_text() -> None:
    plan = plan_quadrant_records(NATIVE_QUADRANT_IR)
    scene = typed_ir_to_scene("quadrant", NATIVE_QUADRANT_IR, emitted_diagram_type="quadrant")

    assert scene is not None
    assert list(
        typed_ir_semantic_texts(
            "quadrant",
            NATIVE_QUADRANT_IR,
            scene,
            emitted_diagram_type="quadrant",
        )
    ) == [
        plan.native_canvas_title,
        plan.x_axis.native_canvas_low,
        plan.x_axis.native_canvas_high,
        plan.y_axis.native_canvas_low,
        plan.y_axis.native_canvas_high,
        "Expand",
        "Revisit",
        "Project A",
        "Project B",
    ]
    semantic_text = " ".join(
        typed_ir_semantic_texts(
            "quadrant",
            NATIVE_QUADRANT_IR,
            scene,
            emitted_diagram_type="quadrant",
        )
    )
    assert "Hidden accessible" not in semantic_text
    assert "987654" not in semantic_text
    assert "0.25" not in semantic_text
    assert "0.75" not in semantic_text


def test_quadrant_flowchart_scene_matches_same_slot_exact_value_fallback() -> None:
    plan = plan_quadrant_records(NATIVE_QUADRANT_IR)
    result = serialize_runtime_fallback_result("quadrant", NATIVE_QUADRANT_IR)
    scene = typed_ir_to_scene(
        "quadrant",
        NATIVE_QUADRANT_IR,
        emitted_diagram_type="flowchart",
    )

    assert result is not None
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("quadrant", "flowchart")
    assert " --> " not in result.code
    assert scene is not None
    assert scene.coordinate_space == "pixels"
    assert scene.reading_direction == "TB"
    assert scene.relations == []
    assert scene.groups == []

    supplied_slots = [slot for slot in plan.quadrants if slot.label]
    expected_ids = [
        "quadrant_title",
        plan.x_axis.scene_id,
        plan.y_axis.scene_id,
        *(slot.scene_id for slot in supplied_slots),
        *(point.scene_id for point in plan.points),
    ]
    expected_text = [
        plan.fallback_canvas_title,
        plan.x_axis.fallback_canvas_label,
        plan.y_axis.fallback_canvas_label,
        *(slot.fallback_canvas_label for slot in supplied_slots),
        *(point.fallback_canvas_label for point in plan.points),
    ]
    assert [element.id for element in scene.elements] == expected_ids
    assert [element.text for element in scene.elements] == expected_text
    assert [element.role for element in scene.elements] == [
        "title",
        "axis",
        "axis",
        "quadrant_label",
        "quadrant_label",
        "data_point",
        "data_point",
    ]
    assert all(element.bbox == (0, 0, 0, 0) for element in scene.elements)
    assert scene.elements[0].evidence_ids == []
    assert scene.elements[1].evidence_ids == ["axis-x"]
    assert scene.elements[2].evidence_ids == ["axis-y"]
    assert scene.elements[-2].evidence_ids == ["point-a"]
    assert scene.elements[-1].evidence_ids == ["point-b"]
    assert "x 0.25, y 0.75" in plan.points[0].fallback_canvas_label
    assert "x 0.8, y 0.1" in plan.points[1].fallback_canvas_label
    for element in scene.elements:
        assert f'    {element.id}["' in result.code
    assert list(
        typed_ir_semantic_texts(
            "quadrant",
            NATIVE_QUADRANT_IR,
            scene,
            emitted_diagram_type="flowchart",
        )
    ) == expected_text


def test_quadrant_scene_fails_closed_with_invalid_or_mismatched_terminal_plan() -> None:
    malformed = {
        "x_axis": {"low": "Low", "high": "High"},
        "y_axis": {"low": "Low", "high": "High"},
        "points": [{"label": "Missing y", "x": 0.5}],
    }

    assert typed_ir_to_scene("quadrant", malformed) is None
    assert (
        typed_ir_to_scene(
            "quadrant",
            NATIVE_QUADRANT_IR,
            emitted_diagram_type="flowchart",
        )
        is not None
    )


@pytest.mark.integration
def test_mermaid_11_16_quadrant_native_and_fallback_match_scene_projection() -> None:
    plan = plan_quadrant_records(NATIVE_QUADRANT_IR)
    native = serialize_typed_ir_result("quadrant", NATIVE_QUADRANT_IR)
    fallback = serialize_runtime_fallback_result("quadrant", NATIVE_QUADRANT_IR)
    native_scene = typed_ir_to_scene(
        "quadrant",
        NATIVE_QUADRANT_IR,
        emitted_diagram_type=native.emitted_type,
    )
    fallback_scene = typed_ir_to_scene(
        "quadrant",
        NATIVE_QUADRANT_IR,
        emitted_diagram_type="flowchart",
    )

    assert fallback is not None
    assert native_scene is not None
    assert fallback_scene is not None
    runtime = NodeMermaidRuntime()
    try:
        rendered_native = runtime.validate_and_render(native.code, timeout_seconds=20)
        rendered_fallback = runtime.validate_and_render(fallback.code, timeout_seconds=20)
    finally:
        runtime.close()

    assert rendered_native.syntax_valid and rendered_native.render_valid, rendered_native.error
    assert (
        rendered_fallback.syntax_valid and rendered_fallback.render_valid
    ), rendered_fallback.error
    assert rendered_native.svg is not None
    assert rendered_fallback.svg is not None

    native_root = ET.fromstring(rendered_native.svg)
    native_visible_text = " ".join(
        text.strip()
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        for text in element.itertext()
        if text.strip()
    )
    for expected in typed_ir_semantic_texts(
        "quadrant",
        NATIVE_QUADRANT_IR,
        native_scene,
        emitted_diagram_type=native.emitted_type,
    ):
        assert expected in native_visible_text
    assert "Hidden accessible" not in native_visible_text
    assert "987654" not in native_visible_text

    quadrant_rectangles = [
        child
        for group in native_root.iter()
        if group.tag.rsplit("}", 1)[-1] == "g" and group.get("class") == "quadrant"
        for child in group
        if child.tag.rsplit("}", 1)[-1] == "rect"
    ]
    assert len(quadrant_rectangles) == 4
    plot_left = min(float(rectangle.get("x", "nan")) for rectangle in quadrant_rectangles)
    plot_top = min(float(rectangle.get("y", "nan")) for rectangle in quadrant_rectangles)
    plot_right = max(
        float(rectangle.get("x", "nan")) + float(rectangle.get("width", "nan"))
        for rectangle in quadrant_rectangles
    )
    plot_bottom = max(
        float(rectangle.get("y", "nan")) + float(rectangle.get("height", "nan"))
        for rectangle in quadrant_rectangles
    )
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top
    rendered_quadrants = [
        group
        for group in native_root.iter()
        if group.tag.rsplit("}", 1)[-1] == "g" and group.get("class") == "quadrant"
    ]
    assert len(rendered_quadrants) == 4
    for group, slot in zip(rendered_quadrants, plan.quadrants, strict=True):
        rectangle = next(
            child for child in group if child.tag.rsplit("}", 1)[-1] == "rect"
        )
        normalized_bbox = (
            (float(rectangle.get("x", "nan")) - plot_left) / plot_width,
            (float(rectangle.get("y", "nan")) - plot_top) / plot_height,
            (
                float(rectangle.get("x", "nan"))
                + float(rectangle.get("width", "nan"))
                - plot_left
            )
            / plot_width,
            (
                float(rectangle.get("y", "nan"))
                + float(rectangle.get("height", "nan"))
                - plot_top
            )
            / plot_height,
        )
        assert normalized_bbox == pytest.approx(slot.normalized_bbox)
        rendered_labels = [
            "".join(child.itertext())
            for child in group
            if child.tag.rsplit("}", 1)[-1] == "text"
            and "".join(child.itertext())
        ]
        assert rendered_labels == ([slot.native_canvas_label] if slot.label else [])

    axis_text_elements = {
        "".join(element.itertext()): element
        for group in native_root.iter()
        if group.tag.rsplit("}", 1)[-1] == "g" and group.get("class") == "labels"
        for element in group.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
    }

    def translated_anchor(label: str) -> tuple[float, float]:
        transform = axis_text_elements[label].get("transform", "")
        match = re.search(r"translate\(([-+0-9.eE]+),\s*([-+0-9.eE]+)\)", transform)
        assert match is not None, transform
        return float(match.group(1)), float(match.group(2))

    assert translated_anchor(plan.x_axis.native_canvas_low) == pytest.approx(
        (
            plot_left + plot_width * plan.x_axis.normalized_low_point[0],
            plot_bottom + 10,
        )
    )
    assert translated_anchor(plan.x_axis.native_canvas_high) == pytest.approx(
        (
            plot_left + plot_width * plan.x_axis.normalized_high_point[0],
            plot_bottom + 10,
        )
    )
    assert translated_anchor(plan.y_axis.native_canvas_low) == pytest.approx(
        (5, plot_top + plot_height * plan.y_axis.normalized_low_point[1])
    )
    assert translated_anchor(plan.y_axis.native_canvas_high) == pytest.approx(
        (5, plot_top + plot_height * plan.y_axis.normalized_high_point[1])
    )

    rendered_points: dict[str, tuple[float, float]] = {}
    for group in native_root.iter():
        if group.tag.rsplit("}", 1)[-1] != "g" or group.get("class") != "data-point":
            continue
        circle = next(
            child for child in group if child.tag.rsplit("}", 1)[-1] == "circle"
        )
        label = next(
            "".join(child.itertext())
            for child in group
            if child.tag.rsplit("}", 1)[-1] == "text"
        )
        rendered_points[label] = (
            (float(circle.get("cx", "nan")) - plot_left) / plot_width,
            (float(circle.get("cy", "nan")) - plot_top) / plot_height,
        )
    expected_points = {
        point.native_canvas_label: point.normalized_point
        for point in plan.points
        if point.normalized_point is not None
    }
    assert rendered_points.keys() == expected_points.keys()
    for label, expected_point in expected_points.items():
        assert rendered_points[label] == pytest.approx(expected_point)

    fallback_root = ET.fromstring(rendered_fallback.svg)
    fallback_visible_text = " ".join(
        text.strip()
        for element in fallback_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        for text in element.itertext()
        if text.strip()
    )
    for element in fallback_scene.elements:
        assert element.text is not None
        assert element.text in fallback_visible_text
