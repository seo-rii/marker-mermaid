from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_charts_sets import serialize_chart_set
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

TREEMAP_IR = {
    "title": "Portfolio",
    "description": "Observed portfolio hierarchy and leaf values.",
    "root": {
        "label": "Portfolio",
        "children": [
            {
                "label": "Core",
                "children": [
                    {"label": "API", "value": 10},
                    {"label": "Database", "value": 8},
                ],
            },
            {"label": "Edge", "value": 4.5},
        ],
    },
}

VENN_IR = {
    "title": "Audiences",
    "sets": [
        {"id": "buyers", "label": "Buyers", "value": 10},
        {"id": "members", "label": "Members", "value": 8},
    ],
    "intersections": [
        {"sets": ["buyers", "members"], "label": "Both", "value": 3},
    ],
}


@pytest.mark.parametrize(
    ("diagram_type", "ir", "prefix"),
    [
        ("treemap", TREEMAP_IR, "treemap-beta"),
        ("venn", VENN_IR, "venn-beta"),
    ],
)
def test_native_serializers_disclose_requested_grammar(
    diagram_type: str, ir: dict[str, object], prefix: str
) -> None:
    code, emitted_type, fallback = serialize_chart_set(diagram_type, ir)

    assert code.startswith(prefix)
    assert emitted_type == diagram_type
    assert fallback is None
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_treemap_output_is_deterministic_and_keeps_only_observed_values() -> None:
    first = serialize_chart_set("treemap", TREEMAP_IR, experimental=True)[0]
    second = serialize_chart_set("treemap", TREEMAP_IR, experimental=True)[0]

    assert first == second
    assert '            "API": 10' in first
    assert '        "Edge": 4.5' in first
    assert '"Core":' not in first
    assert "accTitle: Portfolio" in first
    assert "experimental and requires review" in first


def test_venn_output_uses_explicit_set_and_intersection_sizes() -> None:
    code = serialize_chart_set("venn", VENN_IR)[0]

    assert 'set buyers["Buyers"]: 10' in code
    assert 'set members["Members"]: 8' in code
    assert 'union buyers,members["Both"]: 3' in code


def test_treemap_rejects_missing_leaf_values_instead_of_inventing_them() -> None:
    with pytest.raises(SerializationError, match="explicit numeric value"):
        serialize_chart_set(
            "treemap",
            {
                "root": {
                    "label": "Root",
                    "children": [{"label": "Unmeasured"}],
                }
            },
        )


def test_treemap_requires_a_hierarchy_below_the_root() -> None:
    with pytest.raises(SerializationError, match="explicit hierarchy"):
        serialize_chart_set("treemap", {"root": {"label": "Only", "value": 1}})


def test_internal_treemap_value_uses_explicit_flowchart_fallback() -> None:
    ir = {
        "root": {
            "label": "Portfolio",
            "value": 12,
            "children": [{"label": "Core", "value": 12}],
        }
    }

    code, emitted_type, fallback = serialize_chart_set("treemap", ir)

    assert code.startswith("flowchart TB")
    assert emitted_type == "flowchart"
    assert fallback is not None and "non-leaf" in fallback
    assert "Portfolio (value: 12)" in code
    assert "Core (value: 12)" in code


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [("treemap", TREEMAP_IR), ("venn", VENN_IR)],
)
def test_runtime_rejection_selects_a_disclosed_portable_fallback(
    diagram_type: str, ir: dict[str, object]
) -> None:
    code, emitted_type, fallback = serialize_chart_set(diagram_type, ir, native_runtime_valid=False)

    assert code.startswith("flowchart")
    assert emitted_type == "flowchart"
    assert fallback is not None and "CandidateValidator rejected" in fallback
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_venn_without_sizes_falls_back_without_fabricating_numbers() -> None:
    code, emitted_type, fallback = serialize_chart_set(
        "venn",
        {
            "sets": [
                {"id": "a", "label": "A"},
                {"id": "b", "label": "B"},
            ],
            "intersections": [{"sets": ["a", "b"], "label": "Shared"}],
        },
    )

    assert emitted_type == "flowchart"
    assert fallback is not None and "not observed" in fallback
    assert "value:" not in code
    assert "Shared" in code


def test_venn_requires_explicit_intersection_structure() -> None:
    with pytest.raises(SerializationError, match="explicit intersections"):
        serialize_chart_set(
            "venn",
            {
                "sets": [
                    {"id": "a", "label": "A", "value": 1},
                    {"id": "b", "label": "B", "value": 1},
                ],
                "intersections": [],
            },
        )


def test_venn_rejects_unknown_or_duplicate_intersections() -> None:
    sets = [
        {"id": "a", "label": "A", "value": 5},
        {"id": "b", "label": "B", "value": 5},
    ]
    with pytest.raises(SerializationError, match="unknown set"):
        serialize_chart_set(
            "venn",
            {"sets": sets, "intersections": [{"sets": ["a", "missing"], "value": 1}]},
        )
    with pytest.raises(SerializationError, match="duplicate venn intersection"):
        serialize_chart_set(
            "venn",
            {
                "sets": sets,
                "intersections": [
                    {"sets": ["a", "b"], "value": 1},
                    {"sets": ["b", "a"], "value": 1},
                ],
            },
        )


@pytest.mark.parametrize("value", [True, "10", float("nan"), float("inf"), -1])
def test_chart_values_must_be_observed_finite_numbers(value: object) -> None:
    with pytest.raises(SerializationError, match="numeric value"):
        serialize_chart_set(
            "treemap",
            {
                "root": {
                    "label": "Root",
                    "children": [{"label": "Leaf", "value": value}],
                }
            },
        )


def test_intersection_size_cannot_exceed_an_observed_set_size() -> None:
    with pytest.raises(SerializationError, match="exceeds observed size"):
        serialize_chart_set(
            "venn",
            {
                "sets": [
                    {"id": "a", "label": "A", "value": 2},
                    {"id": "b", "label": "B", "value": 3},
                ],
                "intersections": [{"sets": ["a", "b"], "value": 4}],
            },
        )


def test_labels_are_neutralized_before_strict_security_scanning() -> None:
    code = serialize_chart_set(
        "venn",
        {
            "title": "%%{init}; <script>",
            "sets": [
                {"id": "a", "label": "https://example.invalid", "value": 2},
                {"id": "b", "label": "@ImPoRt logos:evil", "value": 2},
            ],
            "intersections": [{"sets": ["a", "b"], "label": 'Both "sets"', "value": 1}],
        },
    )[0]

    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)

    assert report.safe, report.findings
    assert "<script>" not in code
    assert "https://" not in code
    assert "@import" not in code


def test_unknown_chart_set_serializer_fails_deterministically() -> None:
    with pytest.raises(SerializationError, match="no hierarchy/set chart serializer"):
        serialize_chart_set("radar", {})


@pytest.mark.integration
def test_native_and_fallback_outputs_render_with_strict_mermaid_11_16() -> None:
    cases = [
        serialize_chart_set("treemap", TREEMAP_IR, experimental=True)[0],
        serialize_chart_set("venn", VENN_IR, experimental=True)[0],
        serialize_chart_set("treemap", TREEMAP_IR, native_runtime_valid=False)[0],
        serialize_chart_set("venn", VENN_IR, native_runtime_valid=False)[0],
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for code in cases:
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (
                code,
                outcome.runtime.error,
                outcome.warnings,
            )
    finally:
        process = runtime._process
        runtime.close()
    assert process is not None
    assert process.poll() is not None
