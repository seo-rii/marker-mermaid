from __future__ import annotations

from decimal import Decimal

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_charts_core import serialize_chart_core
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

CASES = {
    "pie": {
        "title": 'Traffic "mix"',
        "description": "Requests by channel.",
        "show_data": True,
        "slices": [
            {"label": 'Web "direct"', "value": 62.5},
            {"label": "Mobile", "value": Decimal("37.5")},
        ],
    },
    "xychart": {
        "title": "Quarterly revenue",
        "description": "Revenue rises across three quarters.",
        "x_axis": {"label": "Quarter", "categories": ["Q1", "Q 2", "Q3"]},
        "y_axis": {"label": "Revenue", "min": 0, "max": 100.5},
        "series": [
            {"kind": "bar", "values": [20, 40.25, 70]},
            {"kind": "line", "values": [10, 50, 90]},
        ],
    },
    "quadrant": {
        "title": "Portfolio",
        "description": "Projects by reach and confidence.",
        "x_axis": {"low": "Low reach", "high": "High reach"},
        "y_axis": {"low": "Low confidence", "high": "High confidence"},
        "quadrants": {
            "quadrant-1": "Expand",
            "2": "Promote",
            3: "Re-evaluate",
            "4": "Improve",
        },
        "points": [
            {"label": 'Project "A"', "x": 0.3, "y": 0.6},
            {"label": "Project B", "x": Decimal("0.8"), "y": Decimal("0.25")},
        ],
    },
}


@pytest.mark.parametrize(
    ("requested_type", "prefix"),
    [("pie", "pie showData"), ("xychart", "xychart-beta"), ("quadrant", "quadrantChart")],
)
def test_core_chart_serializer_emits_native_type_and_strict_safe_source(
    requested_type: str, prefix: str
) -> None:
    code, emitted_type, fallback = serialize_chart_core(
        requested_type, CASES[requested_type], experimental=True
    )

    assert code.startswith(prefix)
    assert emitted_type == requested_type
    assert fallback is None
    assert "accTitle:" in code
    assert "experimental and requires review" in code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_outputs_are_deterministic_and_quote_safe() -> None:
    for diagram_type, ir in CASES.items():
        first = serialize_chart_core(diagram_type, ir)[0]
        second = serialize_chart_core(diagram_type, ir)[0]
        assert first == second
        assert '"A"' not in first
    assert '"Web &quot;direct&quot;" : 62.5' in serialize_chart_core("pie", CASES["pie"])[0]
    assert (
        '"Project &quot;A&quot;": [0.3, 0.6]'
        in serialize_chart_core("quadrant", CASES["quadrant"])[0]
    )


@pytest.mark.parametrize("bad_value", [None, "12", True, float("nan"), float("inf")])
def test_pie_rejects_missing_or_non_numeric_values(bad_value: object) -> None:
    with pytest.raises(SerializationError, match="numeric"):
        serialize_chart_core("pie", {"slices": [{"label": "Known", "value": bad_value}]})


def test_pie_rejects_negative_values_and_zero_total() -> None:
    with pytest.raises(SerializationError, match="negative"):
        serialize_chart_core("pie", {"slices": [{"label": "A", "value": -1}]})
    with pytest.raises(SerializationError, match="positive total"):
        serialize_chart_core("pie", {"slices": [{"label": "A", "value": 0}]})


@pytest.mark.parametrize(
    "ir, message",
    [
        (
            {
                "x_axis": {"categories": ["A"]},
                "y_axis": {"min": 0},
                "series": [{"kind": "line", "values": [1]}],
            },
            "y_axis.max",
        ),
        (
            {
                "x_axis": {"categories": ["A", "B"]},
                "y_axis": {"min": 0, "max": 1},
                "series": [{"kind": "line", "values": [1]}],
            },
            "2 categories",
        ),
        (
            {
                "x_axis": {"min": 0, "max": 10},
                "y_axis": {"min": 1, "max": 1},
                "series": [{"kind": "line", "values": [1, 2]}],
            },
            "must be smaller",
        ),
    ],
)
def test_xychart_requires_complete_axes_and_values(ir: dict, message: str) -> None:
    with pytest.raises(SerializationError, match=message):
        serialize_chart_core("xychart", ir)


def test_xychart_accepts_uniform_explicit_points() -> None:
    code = serialize_chart_core(
        "xychart",
        {
            "x_axis": {"label": "Time", "min": 0, "max": 10},
            "y_axis": {"min": -2, "max": 8},
            "series": [
                {
                    "kind": "line",
                    "points": [
                        {"x": 0, "y": -1},
                        {"x": 5, "y": 2.5},
                        {"x": 10, "y": 7},
                    ],
                }
            ],
        },
    )[0]

    assert 'x-axis "Time" 0 --> 10' in code
    assert "line [-1, 2.5, 7]" in code


def test_xychart_rejects_non_uniform_points_instead_of_distorting_x_coordinates() -> None:
    with pytest.raises(SerializationError, match="cannot preserve non-uniform"):
        serialize_chart_core(
            "xychart",
            {
                "x_axis": {"min": 0, "max": 10},
                "y_axis": {"min": 0, "max": 10},
                "series": [
                    {
                        "kind": "line",
                        "points": [
                            {"x": 0, "y": 1},
                            {"x": 3, "y": 2},
                            {"x": 10, "y": 3},
                        ],
                    }
                ],
            },
        )


@pytest.mark.parametrize("coordinate", [-0.01, 1.01, None, "0.5"])
def test_quadrant_requires_explicit_normalized_coordinates(coordinate: object) -> None:
    ir = {
        "x_axis": {"low": "Low", "high": "High"},
        "y_axis": {"low": "Low", "high": "High"},
        "points": [{"label": "A", "x": coordinate, "y": 0.5}],
    }
    with pytest.raises(SerializationError, match="numeric|normalized"):
        serialize_chart_core("quadrant", ir)


def test_unknown_chart_type_is_explicitly_rejected() -> None:
    with pytest.raises(SerializationError, match="no core chart typed serializer"):
        serialize_chart_core("radar", {})


@pytest.mark.integration
def test_core_chart_serializers_parse_and_render_with_mermaid_11_16() -> None:
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    runtime_types = {"pie": "pie", "xychart": "xychart", "quadrant": "quadrantChart"}
    try:
        for requested_type, ir in CASES.items():
            code = serialize_chart_core(requested_type, ir, experimental=True)[0]
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (requested_type, code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (
                requested_type,
                code,
                outcome.runtime.error,
                outcome.warnings,
            )
            assert outcome.runtime.diagram_type == runtime_types[requested_type]
    finally:
        runtime.close()
