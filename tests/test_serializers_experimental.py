from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_experimental import (
    serialize_cynefin,
    serialize_data_lineage,
    serialize_organization,
    serialize_railroad,
    serialize_wardley,
    serialize_zenuml,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


def test_wardley_native_preserves_explicit_positions_links_and_unicode_labels():
    result = serialize_wardley(
        {
            "title": "결제 가치 사슬",
            "components": [
                {"id": "user", "label": "사용자", "x": 0.95, "y": 0.6, "anchor": True},
                {"id": "api", "label": "결제 API", "x": 0.7, "y": 0.4},
            ],
            "links": [{"source": "user", "target": "api", "label": "요청"}],
        },
        experimental=True,
    )

    assert result.requested_type == result.emitted_type == "wardley"
    assert result.stability == "experimental"
    assert 'anchor "사용자" [0.95, 0.6]' in result.code
    assert 'component "결제 API" [0.7, 0.4]' in result.code
    assert '"사용자" -> "결제 API"; 요청' in result.code
    assert "experimental and requires review" in result.code


@pytest.mark.parametrize(
    "component",
    [
        {"id": "api", "label": "API", "x": None, "y": 0.5},
        {"id": "api", "label": "API", "x": -0.1, "y": 0.5},
        {"id": "api", "label": "API", "x": float("nan"), "y": 0.5},
    ],
)
def test_wardley_never_invents_or_clamps_coordinates(component):
    with pytest.raises(SerializationError, match="coordinate|between"):
        serialize_wardley({"components": [component]})


def test_wardley_rejects_duplicate_labels_and_unresolved_links():
    duplicate = {
        "components": [
            {"id": "a", "label": "Same", "x": 0.1, "y": 0.2},
            {"id": "b", "label": "Same", "x": 0.3, "y": 0.4},
        ]
    }
    unresolved = {
        "components": [{"id": "a", "x": 0.1, "y": 0.2}],
        "links": [{"source": "a", "target": "missing"}],
    }
    with pytest.raises(SerializationError, match="duplicate"):
        serialize_wardley(duplicate)
    with pytest.raises(SerializationError, match="unresolved"):
        serialize_wardley(unresolved)


def test_cynefin_native_requires_explicit_domains_and_quotes_items_and_transitions():
    result = serialize_cynefin(
        {
            "domains": [
                {"name": "complex", "items": ["Emergent practice"]},
                {"name": "complicated", "items": [{"label": "Expert analysis"}]},
            ],
            "transitions": [{"source": "complex", "target": "complicated", "label": "stabilize"}],
        }
    )

    assert result.emitted_type == "cynefin"
    assert '  "Emergent practice"' in result.code
    assert 'complex --> complicated : "stabilize"' in result.code


def test_cynefin_rejects_unknown_duplicate_or_empty_domains():
    with pytest.raises(SerializationError, match="invalid or duplicate"):
        serialize_cynefin({"domains": [{"name": "obvious", "items": ["x"]}]})
    with pytest.raises(SerializationError, match="invalid or duplicate"):
        serialize_cynefin(
            {
                "domains": [
                    {"name": "clear", "items": ["x"]},
                    {"name": "clear", "items": ["y"]},
                ]
            }
        )
    with pytest.raises(SerializationError, match="at least one"):
        serialize_cynefin({"domains": [{"name": "clear", "items": []}]})


def test_railroad_native_serializes_bounded_recursive_expression_ast():
    result = serialize_railroad(
        {
            "title": "Expression grammar",
            "rules": [
                {
                    "name": "expression",
                    "definition": {
                        "type": "choice",
                        "alternatives": [
                            {"type": "terminal", "value": "number"},
                            {
                                "type": "sequence",
                                "elements": [
                                    {"type": "terminal", "value": "("},
                                    {"type": "nonterminal", "name": "expression"},
                                    {"type": "terminal", "value": ")"},
                                ],
                            },
                        ],
                    },
                }
            ],
        }
    )

    assert result.emitted_type == "railroad"
    assert result.code.startswith("railroad-beta\n")
    assert 'expression = choice(terminal("number"), sequence(' in result.code
    assert 'nonterminal("expression")' in result.code


def test_railroad_rejects_unknown_nonterminal_and_excess_depth():
    with pytest.raises(SerializationError, match="unresolved"):
        serialize_railroad(
            {
                "rules": [
                    {
                        "name": "root",
                        "definition": {"type": "nonterminal", "name": "missing"},
                    }
                ]
            }
        )
    expression = {"type": "terminal", "value": "x"}
    for _ in range(22):
        expression = {"type": "optional", "element": expression}
    with pytest.raises(SerializationError, match="too deep"):
        serialize_railroad({"rules": [{"name": "root", "definition": expression}]})


def test_zenuml_is_an_explicit_sequence_fallback():
    result = serialize_zenuml(
        {
            "participants": [
                {"id": "User", "label": "사용자"},
                {"id": "API", "label": "API"},
            ],
            "messages": [{"source": "User", "target": "API", "label": "결제"}],
        },
        experimental=True,
    )

    assert result.requested_type == "zenuml"
    assert result.emitted_type == "sequence"
    assert result.fallback_chain == ("zenuml", "sequence")
    assert result.warnings == (
        "ZenUML is unavailable in Mermaid 11.16 and was emitted as sequence.",
    )
    assert "User->>API: 결제" in result.code


def test_zenuml_rejects_unresolved_messages():
    with pytest.raises(SerializationError, match="unresolved"):
        serialize_zenuml(
            {
                "participants": ["User"],
                "messages": [{"source": "User", "target": "API", "label": "call"}],
            }
        )


def test_organization_and_data_lineage_have_explicit_portable_fallbacks():
    organization = serialize_organization(
        {
            "root": {
                "id": "ceo",
                "label": "CEO",
                "children": [{"id": "cto", "label": "CTO"}],
            }
        }
    )
    lineage = serialize_data_lineage(
        {
            "datasets": [
                {"id": "raw", "label": "Raw"},
                {"id": "clean", "label": "Clean"},
            ],
            "processes": [{"id": "etl", "label": "ETL"}],
            "relations": [
                {"source": "raw", "target": "etl"},
                {"source": "etl", "target": "clean", "label": "writes"},
            ],
        }
    )

    assert organization.fallback_chain == ("organization", "treeview")
    assert organization.code.startswith("treeView-beta")
    assert lineage.fallback_chain == ("data_lineage", "flowchart")
    assert 'raw[("Raw")]' in lineage.code
    assert "etl -->|writes| clean" in lineage.code


def test_organization_runtime_rejection_uses_nested_flowchart_fallback():
    result = serialize_organization(
        {
            "root": {
                "id": "ceo",
                "label": "CEO",
                "children": [{"id": "cto", "label": "CTO"}],
            }
        },
        native_runtime_valid=False,
    )

    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("organization", "treeview", "flowchart")
    assert result.code.startswith("flowchart LR\n")
    assert 'treeview_node_ceo["CEO"]' in result.code
    assert "treeview_node_ceo --> treeview_node_cto" in result.code
    assert any("CandidateValidator rejected" in warning for warning in result.warnings)


def test_data_lineage_rejects_missing_and_unresolved_evidence():
    with pytest.raises(SerializationError, match="requires datasets"):
        serialize_data_lineage({"processes": [], "relations": []})
    with pytest.raises(SerializationError, match="invalid data lineage relation"):
        serialize_data_lineage(
            {
                "datasets": [{"id": "raw"}],
                "processes": [],
                "relations": [{"source": "raw", "target": "missing"}],
            }
        )


@pytest.mark.integration
def test_experimental_serializers_pass_strict_mermaid_11_16_parse_and_render():
    cases = [
        serialize_wardley(
            {
                "components": [
                    {"id": "user", "label": "User", "x": 0.9, "y": 0.6, "anchor": True},
                    {"id": "api", "label": "API", "x": 0.7, "y": 0.4},
                ],
                "links": [{"source": "user", "target": "api"}],
            }
        ).code,
        serialize_cynefin(
            {
                "domains": [
                    {"name": "complex", "items": ["Emergent"]},
                    {"name": "clear", "items": ["Known"]},
                ],
                "transitions": [{"source": "complex", "target": "clear"}],
            }
        ).code,
        serialize_railroad(
            {"rules": [{"name": "root", "definition": {"type": "terminal", "value": "x"}}]}
        ).code,
        serialize_zenuml(
            {
                "participants": ["User", "API"],
                "messages": [{"source": "User", "target": "API", "label": "call"}],
            }
        ).code,
        serialize_organization(
            {
                "root": {
                    "id": "ceo",
                    "label": "CEO",
                    "children": [{"id": "cto", "label": "CTO"}],
                }
            }
        ).code,
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for code in cases:
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (code, outcome.runtime.error, outcome.warnings)
    finally:
        runtime.close()
