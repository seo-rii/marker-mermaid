from __future__ import annotations

from decimal import Decimal

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.models import TypedIRCandidate
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
            {"label": 'Project "A"', "x": 0.15, "y": 0.65},
            {"label": "Project B", "x": Decimal("0.8"), "y": Decimal("0.15")},
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
    assert '"Web \\"direct\\"" : 62.5' in serialize_chart_core("pie", CASES["pie"])[0]
    assert (
        '"Project ″A″": [0.15, 0.65]'
        in serialize_chart_core("quadrant", CASES["quadrant"])[0]
    )


@pytest.mark.parametrize("bad_value", [None, "12", True, float("nan"), float("inf")])
def test_pie_rejects_missing_or_non_numeric_values(bad_value: object) -> None:
    with pytest.raises(SerializationError, match="numeric"):
        serialize_chart_core("pie", {"slices": [{"label": "Known", "value": bad_value}]})


def test_pie_rejects_negative_values_and_zero_total() -> None:
    negative = TypedIRCandidate(diagram_type="pie", ir={"slices": [{"label": "A", "value": -1}]})
    zero = TypedIRCandidate(diagram_type="pie", ir={"slices": [{"label": "A", "value": 0}]})

    with pytest.raises(SerializationError, match="negative"):
        serialize_chart_core("pie", negative.ir)
    with pytest.raises(SerializationError, match="positive total"):
        serialize_chart_core("pie", zero.ir)


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        ({"slices": []}, "non-empty slices"),
        ({"slices": [{"value": 1}]}, "non-empty text"),
        (
            {"slices": [{"label": "A", "value": 1}, {"label": "A", "value": 2}]},
            "labels must be unique",
        ),
    ],
)
def test_pie_serializer_owns_nonempty_and_unique_slice_semantics(
    ir: dict[str, object], message: str
) -> None:
    candidate = TypedIRCandidate(diagram_type="pie", ir=ir)

    with pytest.raises(SerializationError, match=message):
        serialize_chart_core("pie", candidate.ir)


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
    candidate = TypedIRCandidate(diagram_type="xychart", ir=ir)

    with pytest.raises(SerializationError, match=message):
        serialize_chart_core("xychart", candidate.ir)


@pytest.mark.parametrize(
    ("series", "message"),
    [
        ({"kind": "line"}, "exactly one"),
        ({"kind": "line", "values": [1], "points": [{"x": 0, "y": 1}]}, "exactly one"),
        ({"kind": "line", "points": [{"x": 0, "y": 1}]}, "at least two"),
        ({"kind": "line", "values": [1], "label": "Observed"}, "series-label syntax"),
    ],
)
def test_xychart_serializer_owns_series_shape_semantics(
    series: dict[str, object], message: str
) -> None:
    ir = {
        "x_axis": {"min": 0, "max": 1},
        "y_axis": {"min": 0, "max": 2},
        "series": [series],
    }
    candidate = TypedIRCandidate(diagram_type="xychart", ir=ir)

    with pytest.raises(SerializationError, match=message):
        serialize_chart_core("xychart", candidate.ir)


@pytest.mark.parametrize(
    ("x_axis", "series", "message"),
    [
        (
            {"categories": ["A"], "min": None},
            {"kind": "bar", "values": [1]},
            "cannot mix categories",
        ),
        (
            {"categories": ["A", "A"]},
            {"kind": "bar", "values": [1, 2]},
            "categories must be unique",
        ),
        (
            {"categories": ["A", "B"]},
            {"kind": "bar", "points": [{"x": 0, "y": 1}, {"x": 1, "y": 2}]},
            "points require a numeric x_axis",
        ),
    ],
)
def test_xychart_serializer_owns_category_mode_semantics(
    x_axis: dict[str, object],
    series: dict[str, object],
    message: str,
) -> None:
    ir = {
        "x_axis": x_axis,
        "y_axis": {"min": 0, "max": 2},
        "series": [series],
    }
    candidate = TypedIRCandidate(diagram_type="xychart", ir=ir)

    with pytest.raises(SerializationError, match=message):
        serialize_chart_core("xychart", candidate.ir)


def test_xychart_accepts_uniform_explicit_points() -> None:
    candidate = TypedIRCandidate(
        diagram_type="xychart",
        ir={
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
    )
    code = serialize_chart_core("xychart", candidate.ir)[0]

    assert 'x-axis "Time" 0 --> 10' in code
    assert "line [-1, 2.5, 7]" in code


@pytest.mark.parametrize(
    ("x_axis", "series", "location"),
    [
        (
            {"categories": ["A"]},
            {"kind": "bar", "values": [2.01]},
            r"values\[0\]",
        ),
        (
            {"min": 0, "max": 10},
            {"kind": "line", "values": [-0.01, 1]},
            r"values\[0\]",
        ),
        (
            {"min": 0, "max": 10},
            {
                "kind": "line",
                "points": [
                    {"x": 0, "y": 0},
                    {"x": 10, "y": 2.01},
                ],
            },
            r"points\[1\]\.y",
        ),
    ],
)
def test_xychart_rejects_series_y_outside_explicit_axis_bounds(
    x_axis: dict[str, object],
    series: dict[str, object],
    location: str,
) -> None:
    candidate = TypedIRCandidate(
        diagram_type="xychart",
        ir={
            "x_axis": x_axis,
            "y_axis": {"min": 0, "max": 2},
            "series": [series],
        },
    )

    with pytest.raises(SerializationError, match=rf"{location}.*y_axis bounds"):
        serialize_chart_core("xychart", candidate.ir)


@pytest.mark.parametrize(
    ("x_axis", "series"),
    [
        (
            {"categories": ["Low", "High"]},
            {"kind": "bar", "values": [Decimal("-2"), Decimal("2")]},
        ),
        (
            {"min": Decimal("0"), "max": Decimal("10")},
            {"kind": "line", "values": [Decimal("-2"), Decimal("2")]},
        ),
        (
            {"min": Decimal("0"), "max": Decimal("10")},
            {
                "kind": "line",
                "points": [
                    {"x": Decimal("0"), "y": Decimal("-2")},
                    {"x": Decimal("10"), "y": Decimal("2")},
                ],
            },
        ),
    ],
)
def test_xychart_accepts_decimal_y_values_on_inclusive_axis_bounds(
    x_axis: dict[str, object],
    series: dict[str, object],
) -> None:
    code, emitted_type, warning = serialize_chart_core(
        "xychart",
        {
            "x_axis": x_axis,
            "y_axis": {"min": Decimal("-2"), "max": Decimal("2")},
            "series": [series],
        },
    )

    if series["kind"] == "bar":
        assert emitted_type == "flowchart"
        assert warning is not None
        assert 'xy_series_1_point_1["bar · Low: value -2"]' in code
        assert 'xy_series_1_point_2["bar · High: value 2"]' in code
    else:
        assert emitted_type == "xychart"
        assert warning is None
        assert "[-2, 2]" in code


def test_xychart_falls_back_for_non_uniform_points_instead_of_distorting_coordinates() -> None:
    candidate = TypedIRCandidate(
        diagram_type="xychart",
        ir={
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

    code, emitted_type, warning = serialize_chart_core("xychart", candidate.ir)

    assert emitted_type == "flowchart"
    assert warning is not None
    assert 'xy_series_1_point_2["line · x 3, y 2"]' in code
    assert " --> " not in code


def test_xychart_preserves_uppercase_kind_until_serializer_normalizes_it() -> None:
    ir = {
        "x_axis": {"categories": ["A", "B"]},
        "y_axis": {"min": 0, "max": 2},
        "series": [{"kind": "LINE", "values": [1, 2]}],
    }
    candidate = TypedIRCandidate(diagram_type="xychart", ir=ir)

    assert candidate.ir == ir
    assert "    line [1, 2]" in serialize_chart_core("xychart", candidate.ir)[0]


@pytest.mark.parametrize("coordinate", [-0.01, 1.01, None, "0.5"])
def test_quadrant_requires_explicit_normalized_coordinates(coordinate: object) -> None:
    ir = {
        "x_axis": {"low": "Low", "high": "High"},
        "y_axis": {"low": "Low", "high": "High"},
        "points": [{"label": "A", "x": coordinate, "y": 0.5}],
    }
    with pytest.raises(SerializationError, match="numeric|normalized"):
        serialize_chart_core("quadrant", ir)


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        (
            {"x_axis": {}, "y_axis": {"low": "Low", "high": "High"}, "points": []},
            "x_axis.low",
        ),
        (
            {
                "x_axis": {"low": "Low", "high": "High"},
                "y_axis": {"low": "Low", "high": "High"},
                "points": [],
            },
            "non-empty points",
        ),
        (
            {
                "x_axis": {"low": "Low", "high": "High"},
                "y_axis": {"low": "Low", "high": "High"},
                "points": [
                    {"label": "A", "x": 0.2, "y": 0.3},
                    {"label": "A", "x": 0.7, "y": 0.8},
                ],
            },
            "point labels must be unique",
        ),
        (
            {
                "x_axis": {"low": "Low", "high": "High"},
                "y_axis": {"low": "Low", "high": "High"},
                "quadrants": ["One", "Two", "Three"],
                "points": [{"label": "A", "x": 0.2, "y": 0.3}],
            },
            "exactly four",
        ),
        (
            {
                "x_axis": {"low": "Low", "high": "High"},
                "y_axis": {"low": "Low", "high": "High"},
                "quadrants": {"north": "Unknown"},
                "points": [{"label": "A", "x": 0.2, "y": 0.3}],
            },
            "unsupported quadrant label key",
        ),
    ],
)
def test_quadrant_serializer_owns_axis_point_and_label_semantics(
    ir: dict[str, object], message: str
) -> None:
    candidate = TypedIRCandidate(diagram_type="quadrant", ir=ir)

    with pytest.raises(SerializationError, match=message):
        serialize_chart_core("quadrant", candidate.ir)


def test_quadrant_serializer_accepts_partial_case_insensitive_label_aliases() -> None:
    ir = {
        "x_axis": {"low": "Low", "high": "High"},
        "y_axis": {"low": "Low", "high": "High"},
        "quadrants": {"QUADRANT-1": "Expand", "2": "Promote"},
        "points": [{"label": "A", "x": 0.2, "y": 0.3}],
    }
    candidate = TypedIRCandidate(diagram_type="quadrant", ir=ir)

    code = serialize_chart_core("quadrant", candidate.ir)[0]

    assert candidate.ir == ir
    assert 'quadrant-1 "Expand"' in code
    assert 'quadrant-2 "Promote"' in code
    assert "quadrant-3" not in code


@pytest.mark.parametrize(
    ("quadrants", "slot"),
    [
        ({"1": "First", "quadrant-1": "Second"}, 1),
        ({"quadrant-1": "Second", "1": "First"}, 1),
        ({"QUADRANT-2": "First", "Quadrant-2": "Second"}, 2),
    ],
)
def test_quadrant_rejects_duplicate_normalized_label_aliases(
    quadrants: dict[str, str], slot: int
) -> None:
    candidate = TypedIRCandidate(
        diagram_type="quadrant",
        ir={
            "x_axis": {"low": "Low", "high": "High"},
            "y_axis": {"low": "Low", "high": "High"},
            "quadrants": quadrants,
            "points": [{"label": "A", "x": 0.2, "y": 0.3}],
        },
    )

    with pytest.raises(
        SerializationError,
        match=rf"duplicate quadrant label alias for quadrant-{slot}",
    ):
        serialize_chart_core("quadrant", candidate.ir)


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
