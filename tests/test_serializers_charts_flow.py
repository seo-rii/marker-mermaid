from __future__ import annotations

from copy import deepcopy

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_charts_flow import (
    MAX_RADAR_TICKS,
    SANKEY_ACCESSIBILITY_LIMITATION,
    serialize_chart_flow,
    serialize_radar,
    serialize_sankey,
)
from marker_mermaid.typed_contracts import validate_typed_ir_contract
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

SANKEY_IR = {
    "title": "Energy transfers",
    "description": "Input energy is divided between useful work and loss.",
    "nodes": [
        {"id": "input", "label": "Input, total"},
        {"id": "work", "label": "Useful work"},
        {"id": "loss", "label": "Loss"},
    ],
    "flows": [
        {"source": "input", "target": "work", "value": 75.5},
        {"source": "input", "target": "loss", "value": 24.5},
    ],
}

RADAR_IR = {
    "title": "Model comparison",
    "description": "Two models are compared on three measured dimensions.",
    "dimensions": [
        {"id": "accuracy", "label": "Accuracy"},
        {"id": "speed", "label": "Speed"},
        {"id": "safety", "label": "Safety"},
    ],
    "series": [
        {"id": "model-a", "label": "Model A", "values": [80, 70.5, 90]},
        {"id": "model-b", "label": "Model B", "values": [60, 90, 75]},
    ],
    "min": 0,
    "max": 100,
    "ticks": 5,
    "show_legend": True,
    "graticule": "polygon",
}


def test_sankey_native_output_is_deterministic_and_discloses_accessibility_limit() -> None:
    first = serialize_sankey(SANKEY_IR, experimental=True)
    second = serialize_sankey(deepcopy(SANKEY_IR), experimental=True)

    assert first == second
    assert not first.used_fallback
    assert first.emitted_type == "sankey"
    assert first.code.startswith("sankey-beta\n")
    assert '"Input, total",Useful work,75.5' in first.code
    assert first.warnings == (SANKEY_ACCESSIBILITY_LIMITATION,)


def test_sankey_cycle_uses_exact_weighted_flowchart_fallback() -> None:
    cyclic = {
        "nodes": [{"id": "A"}, {"id": "B"}],
        "flows": [
            {"source": "A", "target": "B", "value": 2},
            {"source": "B", "target": "A", "value": 1},
        ],
    }

    result = serialize_sankey(cyclic)

    assert result.used_fallback
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("sankey", "flowchart")
    assert "A -->|2| B" in result.code
    assert "B -->|1| A" in result.code
    assert result.warnings and "weighted flowchart" in result.warnings[0]


def test_sankey_non_ascii_labels_fall_back_without_dropping_text() -> None:
    result = serialize_sankey(
        {
            "nodes": [{"id": "source", "label": "입력"}, {"id": "target", "label": "출력"}],
            "flows": [{"source": "source", "target": "target", "value": 3}],
        }
    )

    assert result.emitted_type == "flowchart"
    assert 'source["입력"]' in result.code
    assert 'target["출력"]' in result.code
    assert "source -->|3| target" in result.code


def test_sankey_isolated_node_falls_back_instead_of_disappearing() -> None:
    result = serialize_sankey(
        {
            "nodes": [{"id": "A"}, {"id": "B"}, {"id": "isolated"}],
            "flows": [{"source": "A", "target": "B", "value": 3}],
        }
    )

    assert result.emitted_type == "flowchart"
    assert 'isolated["isolated"]' in result.code


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_sankey_rejects_missing_or_non_finite_values(value: object) -> None:
    ir = {
        "nodes": [{"id": "A"}, {"id": "B"}],
        "flows": [{"source": "A", "target": "B", "value": value}],
    }
    with pytest.raises(SerializationError, match="finite number"):
        serialize_sankey(ir)


def test_sankey_rejects_unknown_endpoint_and_duplicate_node_id() -> None:
    with pytest.raises(SerializationError, match="unknown endpoint"):
        serialize_sankey(
            {
                "nodes": [{"id": "A"}],
                "flows": [{"source": "A", "target": "missing", "value": 1}],
            }
        )
    with pytest.raises(SerializationError, match="duplicated"):
        serialize_sankey(
            {
                "nodes": [{"id": "A"}, {"id": "A"}],
                "flows": [{"source": "A", "target": "A", "value": 1}],
            }
        )


def test_sankey_contract_defers_endpoint_semantics_to_serializer() -> None:
    ir = {
        "nodes": [{"id": "A"}, {"id": "B"}],
        "flows": [{"source": "A", "target": "missing", "value": 1}],
    }

    validate_typed_ir_contract("sankey", ir)
    with pytest.raises(SerializationError, match="unknown endpoint"):
        serialize_sankey(ir)


def test_sankey_prefers_canonical_flows_but_keeps_direct_links_compatibility() -> None:
    canonical = {
        "nodes": [{"id": "A"}, {"id": "B"}],
        "flows": [{"source": "A", "target": "B", "value": 2}],
        "links": [{"source": "B", "target": "A", "value": 99}],
    }

    validate_typed_ir_contract("sankey", canonical)
    canonical_result = serialize_sankey(canonical)
    assert "A,B,2" in canonical_result.code
    assert "99" not in canonical_result.code

    legacy = {
        "nodes": [{"id": "A"}, {"id": "B"}],
        "links": [{"source": "A", "target": "B", "value": 3}],
    }
    legacy_result = serialize_sankey(legacy)
    assert "A,B,3" in legacy_result.code
    with pytest.raises(ValueError, match="requires root field 'flows'"):
        validate_typed_ir_contract("sankey", legacy)


def test_radar_native_output_is_deterministic_and_preserves_explicit_options() -> None:
    first = serialize_radar(RADAR_IR, experimental=True)
    second = serialize_radar(deepcopy(RADAR_IR), experimental=True)

    assert first == second
    assert not first.used_fallback
    assert first.emitted_type == "radar"
    assert first.code.startswith("radar-beta\n")
    assert 'axis accuracy["Accuracy"], speed["Speed"], safety["Safety"]' in first.code
    assert 'curve model-a["Model A"]{80, 70.5, 90}' in first.code
    assert "showLegend true" in first.code
    assert "ticks 5" in first.code
    assert "max 100" in first.code
    assert "min 0" in first.code
    assert "graticule polygon" in first.code
    assert "experimental and requires review" in first.code


def test_radar_negative_values_use_tabular_fallback_without_synthesis() -> None:
    ir = deepcopy(RADAR_IR)
    ir.pop("min")
    ir["series"][0]["values"] = [-2.5, 0, 4]

    result = serialize_radar(ir)

    assert result.used_fallback
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("radar", "flowchart")
    assert 'model-a_1["Accuracy: -2.5"]' in result.code
    assert 'model-a_2["Speed: 0"]' in result.code
    assert 'model-a_3["Safety: 4"]' in result.code
    assert "-->" not in result.code
    assert result.warnings and "tabular flowchart" in result.warnings[0]


def test_radar_requires_exact_dimension_series_alignment() -> None:
    ir = deepcopy(RADAR_IR)
    ir["series"][0]["values"] = [1, 2]

    with pytest.raises(SerializationError, match="2 values for 3 dimensions"):
        serialize_radar(ir)


@pytest.mark.parametrize("value", [True, float("nan"), float("-inf")])
def test_radar_rejects_non_numeric_or_non_finite_values(value: object) -> None:
    ir = deepcopy(RADAR_IR)
    ir["series"][0]["values"][0] = value

    with pytest.raises(SerializationError, match="finite number"):
        serialize_radar(ir)


def test_radar_rejects_duplicate_ids_and_inconsistent_bounds() -> None:
    duplicate = deepcopy(RADAR_IR)
    duplicate["dimensions"][1]["id"] = "accuracy"
    with pytest.raises(SerializationError, match="duplicated"):
        serialize_radar(duplicate)

    clipped = deepcopy(RADAR_IR)
    clipped["max"] = 50
    with pytest.raises(SerializationError, match="must not exceed"):
        serialize_radar(clipped)


def test_radar_contract_defers_value_alignment_and_option_ranges_to_serializer() -> None:
    misaligned = deepcopy(RADAR_IR)
    misaligned["series"][0]["values"] = [1, 2]
    validate_typed_ir_contract("radar", misaligned)
    with pytest.raises(SerializationError, match="2 values for 3 dimensions"):
        serialize_radar(misaligned)

    invalid_ticks = deepcopy(RADAR_IR)
    invalid_ticks["ticks"] = 0
    validate_typed_ir_contract("radar", invalid_ticks)
    with pytest.raises(SerializationError, match="positive integer"):
        serialize_radar(invalid_ticks)

    excessive_ticks = deepcopy(RADAR_IR)
    excessive_ticks["ticks"] = MAX_RADAR_TICKS + 1
    validate_typed_ir_contract("radar", excessive_ticks)
    with pytest.raises(SerializationError, match=rf"must not exceed {MAX_RADAR_TICKS}"):
        serialize_radar(excessive_ticks)

    bounded_ticks = deepcopy(RADAR_IR)
    bounded_ticks["ticks"] = MAX_RADAR_TICKS
    assert f"ticks {MAX_RADAR_TICKS}" in serialize_radar(bounded_ticks).code


def test_radar_prefers_dimensions_but_keeps_direct_axes_compatibility() -> None:
    canonical = deepcopy(RADAR_IR)
    canonical["axes"] = [
        {"id": "legacy-a", "label": "Legacy A"},
        {"id": "legacy-b", "label": "Legacy B"},
        {"id": "legacy-c", "label": "Legacy C"},
    ]

    validate_typed_ir_contract("radar", canonical)
    assert (
        'axis accuracy["Accuracy"], speed["Speed"], safety["Safety"]'
        in serialize_radar(canonical).code
    )

    legacy = deepcopy(RADAR_IR)
    legacy["axes"] = legacy.pop("dimensions")
    assert (
        'axis accuracy["Accuracy"], speed["Speed"], safety["Safety"]'
        in serialize_radar(legacy).code
    )
    with pytest.raises(ValueError, match="requires root field 'dimensions'"):
        validate_typed_ir_contract("radar", legacy)


@pytest.mark.parametrize(
    ("field", "value", "location"),
    [
        ("ticks", True, "ticks"),
        ("show_legend", 1, "show_legend"),
        ("graticule", "Polygon", "graticule"),
    ],
)
def test_radar_nested_contract_rejects_noncanonical_option_types(
    field: str, value: object, location: str
) -> None:
    ir = deepcopy(RADAR_IR)
    ir[field] = value

    with pytest.raises(ValueError, match=rf"at {location}"):
        validate_typed_ir_contract("radar", ir)


def test_chart_flow_dispatch_rejects_unknown_type() -> None:
    assert serialize_chart_flow("sankey", SANKEY_IR).requested_type == "sankey"
    assert serialize_chart_flow("radar", RADAR_IR).requested_type == "radar"
    with pytest.raises(SerializationError, match="unsupported chart-flow"):
        serialize_chart_flow("pie", {})


@pytest.mark.integration
def test_native_and_fallback_chart_flow_sources_pass_strict_mermaid_11_16() -> None:
    negative_radar = deepcopy(RADAR_IR)
    negative_radar.pop("min")
    negative_radar["series"][0]["values"] = [-1, 2, 3]
    cyclic_sankey = {
        "nodes": [{"id": "A"}, {"id": "B"}],
        "flows": [
            {"source": "A", "target": "B", "value": 2},
            {"source": "B", "target": "A", "value": 1},
        ],
    }
    cases = [
        serialize_sankey(SANKEY_IR).code,
        serialize_sankey(cyclic_sankey).code,
        serialize_radar(RADAR_IR).code,
        serialize_radar(negative_radar).code,
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
        runtime.close()
