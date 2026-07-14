from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import FrozenInstanceError
from xml.etree import ElementTree as ET

import pytest

import marker_mermaid.serializers_experimental as experimental_serializers
from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_experimental import (
    CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS,
    plan_cynefin_records,
    plan_data_lineage_records,
    plan_organization_hierarchy,
    plan_railroad_records,
    plan_wardley_records,
    plan_zenuml_records,
    plan_zenuml_structure,
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
    assert 'anchor "사용자" [0.6, 0.95]' in result.code
    assert 'component "결제 API" [0.4, 0.7]' in result.code
    assert '"사용자" -> "결제 API"; 요청' in result.code
    assert "experimental and requires review" in result.code
    assert "directed structure" not in result.code


def test_wardley_runtime_rejection_uses_loss_disclosed_undirected_flowchart() -> None:
    result = serialize_wardley(
        {
            "title": "Payment map",
            "components": [
                {
                    "id": "user-node",
                    "label": 'User "one"',
                    "x": 0.9,
                    "y": 0.8,
                    "anchor": True,
                },
                {"id": "api_node", "label": "API \\ service", "x": 0.5, "y": 0.4},
            ],
            "links": [
                {
                    "source": "user-node",
                    "target": "api_node",
                    "label": "uses | retry later",
                }
            ],
        },
        experimental=True,
        native_runtime_valid=False,
    )

    assert result.requested_type == "wardley"
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("wardley", "flowchart")
    assert result.stability == "experimental"
    assert result.code.startswith("flowchart LR\n")
    assert 'wardley_component_1["User ″one″"]' in result.code
    assert 'wardley_component_2["API ∖ service"]' in result.code
    assert "wardley_component_1 ---|uses ∣ retry later| wardley_component_2" in result.code
    assert "-->" not in result.code
    assert "directed structure" not in result.code
    assert "accTitle: Payment map" in result.code
    assert "[0.8, 0.9]" not in result.code
    assert any("coordinates" in warning and "anchor" in warning for warning in result.warnings)
    assert any(
        "visible Wardley title" in warning and "accTitle" in warning for warning in result.warnings
    )
    assert any("compatibility glyphs" in warning for warning in result.warnings)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe


def test_wardley_fallback_discloses_distinct_explicit_accessible_title() -> None:
    result = serialize_wardley(
        {
            "title": "Visible map title",
            "acc_title": "Different accessible title",
            "components": [{"id": "api", "label": "API", "x": 0.5, "y": 0.4}],
        },
        native_runtime_valid=False,
    )

    assert "accTitle: Different accessible title" in result.code
    assert "Visible map title" not in result.code
    assert any(
        "explicit accTitle is preserved" in warning and "typed IR and review metadata" in warning
        for warning in result.warnings
    )
    assert not any(
        "it remains available through accTitle metadata" in warning for warning in result.warnings
    )


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


def test_wardley_renders_small_finite_coordinates_without_invalid_exponent_suffix():
    plan = plan_wardley_records(
        {"components": [{"id": "api", "label": "API", "x": 1e-10, "y": 0.5}]}
    )

    assert plan.components[0].x == 1e-10
    assert plan.components[0].x_token == "0.0000000001"
    assert (
        "[0.5, 0.0000000001]"
        in serialize_wardley(
            {"components": [{"id": "api", "label": "API", "x": 1e-10, "y": 0.5}]}
        ).code
    )


def test_wardley_canonicalizes_negative_zero_before_native_serialization() -> None:
    result = serialize_wardley(
        {"components": [{"id": "api", "label": "API", "x": -0.0, "y": -0.0}]}
    )

    assert 'component "API" [0.0, 0.0]' in result.code
    assert "-0.0" not in result.code


def test_wardley_plan_coordinates_match_the_rounded_native_tokens() -> None:
    plan = plan_wardley_records(
        {
            "components": [
                {"id": "a", "x": 0.5000000000000001, "y": 0.2},
                {"id": "b", "x": 0.5000000000000002, "y": 0.8},
            ]
        }
    )

    assert [item.x_token for item in plan.components] == ["0.5", "0.5"]
    assert [item.x for item in plan.components] == [0.5, 0.5]


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


def test_wardley_plan_preserves_semantic_source_visible_tokens_and_coordinates():
    component = {
        "id": "api",
        "label": "API &#35; edge",
        "x": 1,
        "y": 0.25,
        "anchor": False,
        "evidence_ids": ["ocr-api"],
    }
    target = {"id": "db", "label": "DB", "x": 0.5, "y": 0}
    link = {
        "source": "api",
        "target": "db",
        "label": "writes &#x23; data",
        "evidence_ids": ["line-api-db"],
    }

    plan = plan_wardley_records(
        {
            "title": "Value &#35; map",
            "components": [component, target],
            "links": [link],
        }
    )

    assert plan.title == "Value ＆＃35; map"
    assert plan.semantic_title == "Value &#35; map"
    assert plan.compatibility_substituted is True
    assert plan.components[0].source_record is component
    assert plan.components[0].source_id == "api"
    assert plan.components[0].emitted_id == "wardley_component_1"
    assert plan.components[0].label == "API ＆＃35; edge"
    assert plan.components[0].semantic_label == "API &#35; edge"
    assert plan.components[0].fallback_label == "API ＆＃35; edge"
    assert plan.components[0].kind == "component"
    assert (plan.components[0].x, plan.components[0].y) == (1.0, 0.25)
    assert (plan.components[0].x_token, plan.components[0].y_token) == ("1.0", "0.25")
    assert plan.components[0].token == '"API ＆＃35; edge"'
    assert plan.links[0].source_record is link
    assert plan.links[0].emitted_id == "wardley_link_1"
    assert plan.links[0].source_id == "api"
    assert plan.links[0].target_id == "db"
    assert plan.links[0].source_emitted_id == "wardley_component_1"
    assert plan.links[0].target_emitted_id == "wardley_component_2"
    assert plan.links[0].source_token == '"API ＆＃35; edge"'
    assert plan.links[0].target_token == '"DB"'
    assert plan.links[0].label == "writes ＆＃x23; data"
    assert plan.links[0].semantic_label == "writes &#x23; data"
    assert plan.links[0].fallback_label == "writes ＆＃x23⁏ data"
    assert plan.flowchart_compatibility_substituted
    with pytest.raises(FrozenInstanceError):
        plan.title = "mutated"  # type: ignore[misc]


def test_wardley_rejects_visible_label_collisions_after_entity_compatibility():
    with pytest.raises(SerializationError, match="duplicate Wardley component label"):
        plan_wardley_records(
            {
                "components": [
                    {"id": "encoded", "label": "Node &#35;", "x": 0.1, "y": 0.2},
                    {"id": "visible", "label": "Node ＆＃35;", "x": 0.3, "y": 0.4},
                ]
            }
        )


@pytest.mark.parametrize("anchor", [1, 0, "true", [], {}])
def test_wardley_anchor_requires_an_exact_boolean_or_null(anchor):
    with pytest.raises(SerializationError, match="anchor must be a boolean or null"):
        plan_wardley_records({"components": [{"id": "api", "x": 0.5, "y": 0.5, "anchor": anchor}]})


@pytest.mark.parametrize("character", ["\n", "\u0080", "\u200b", "\u2028", "\u2029"])
def test_experimental_text_rejects_control_format_and_line_separator_characters(character):
    with pytest.raises(SerializationError, match="unsupported control or format"):
        serialize_wardley(
            {"components": [{"id": "api", "label": f"A{character}B", "x": 0.5, "y": 0.5}]}
        )


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


def test_cynefin_runtime_rejection_preserves_all_explicit_domains_and_confusion_items():
    ir = {
        "title": "Observed Cynefin",
        "description": "Only explicitly extracted content.",
        "domains": [
            {
                "name": "complex",
                "items": ['Emergent "one" \\ path click https://example.com'],
            },
            {"name": "complicated", "items": ["Expert"]},
            {"name": "chaotic", "items": ["Crisis"]},
            {"name": "clear", "items": ["Known"]},
            {"name": "confusion", "items": ["One", "Two", "Three", "Four", "Five"]},
        ],
        "transitions": [
            {
                "source": "complex",
                "target": "clear",
                "label": "stabilize | retry; later",
            }
        ],
    }
    snapshot = repr(ir)

    result = serialize_cynefin(ir, experimental=True, native_runtime_valid=False)

    assert result.requested_type == "cynefin"
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("cynefin", "flowchart")
    assert result.stability == "experimental"
    assert result.code.startswith("flowchart LR\n")
    for domain in ("complex", "complicated", "chaotic", "clear", "confusion"):
        label = domain.title()
        assert f'subgraph cynefin_domain_{domain}["{label}"]' in result.code
        assert f'    cynefin_domain_{domain}["{label}"]' not in result.code
    for index, label in enumerate(("One", "Two", "Three", "Four", "Five"), start=1):
        assert f'cynefin_item_confusion_{index}["{label}"]' in result.code
    assert "Emergent ″one″ ∖ path" in result.code
    assert "click https://example.com" not in result.code
    assert (
        "cynefin_domain_complex -->|stabilize ∣ retry⁏ later| cynefin_domain_clear"
    ) in result.code
    assert "+2 more" not in result.code
    for _element_id, role, label in CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS:
        if role == "runtime_template":
            assert label not in result.code
    assert any("rejected cynefin-beta" in warning for warning in result.warnings)
    assert any(
        "fixed domain/practice/response/disorder template" in warning
        for warning in result.warnings
    )
    assert any("compatibility glyphs" in warning for warning in result.warnings)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert repr(ir) == snapshot


def test_cynefin_flowchart_fallback_never_fabricates_unobserved_domains_or_template():
    result = serialize_cynefin(
        {
            "domains": [
                {"name": "complex", "items": ["Emergent"]},
                {"name": "confusion", "items": ["Unclassified"]},
            ]
        },
        native_runtime_valid=False,
    )

    assert 'subgraph cynefin_domain_complex["Complex"]' in result.code
    assert 'subgraph cynefin_domain_confusion["Confusion"]' in result.code
    for domain in ("complicated", "chaotic", "clear"):
        assert f"cynefin_domain_{domain}" not in result.code
    for _element_id, role, label in CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS:
        if role == "runtime_template":
            assert label not in result.code


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


def test_cynefin_plan_preserves_record_identity_semantics_and_stable_ids():
    item = {"label": "Emergent &#35; practice", "evidence_ids": ["ocr-emergent"]}
    complex_domain = {"name": "Complex", "items": [item, "Probe"]}
    clear_domain = {"name": "clear", "items": ["Known"]}
    transition = {
        "source": "COMPLEX",
        "target": "Clear",
        "label": "stabilize &#x23; path",
        "evidence_ids": ["arrow-transition"],
    }

    plan = plan_cynefin_records(
        {"domains": [complex_domain, clear_domain], "transitions": [transition]}
    )

    assert plan.compatibility_substituted is True
    assert plan.domains[0].source_record is complex_domain
    assert plan.domains[0].name == "complex"
    assert plan.domains[0].emitted_id == "cynefin_domain_complex"
    assert plan.domains[0].group_id == "cynefin_group_complex"
    assert plan.domains[0].items[0].source_record is item
    assert plan.domains[0].items[0].emitted_id == "cynefin_item_complex_1"
    assert plan.domains[0].items[0].label == "Emergent ＆＃35; practice"
    assert plan.domains[0].items[0].semantic_label == "Emergent &#35; practice"
    assert plan.domains[0].items[1].source_record is None
    assert plan.domains[0].items[1].emitted_id == "cynefin_item_complex_2"
    assert plan.transitions[0].source_record is transition
    assert plan.transitions[0].emitted_id == "cynefin_transition_1"
    assert plan.transitions[0].source_name == "complex"
    assert plan.transitions[0].target_name == "clear"
    assert plan.transitions[0].source_emitted_id == "cynefin_domain_complex"
    assert plan.transitions[0].target_emitted_id == "cynefin_domain_clear"
    assert plan.transitions[0].label == "stabilize ＆＃x23; path"
    assert plan.transitions[0].semantic_label == "stabilize &#x23; path"


@pytest.mark.parametrize("label", ["", False, 0])
def test_cynefin_explicit_falsey_transition_label_is_not_silently_omitted(label):
    with pytest.raises(SerializationError, match="non-empty string"):
        plan_cynefin_records(
            {
                "domains": [
                    {"name": "complex", "items": ["Emergent"]},
                    {"name": "clear", "items": ["Known"]},
                ],
                "transitions": [{"source": "complex", "target": "clear", "label": label}],
            }
        )


@pytest.mark.parametrize("label", ["", False, 0])
def test_cynefin_explicit_falsey_item_label_is_rejected(label):
    with pytest.raises(SerializationError, match="non-empty string"):
        plan_cynefin_records({"domains": [{"name": "complex", "items": [{"label": label}]}]})


def test_cynefin_rejects_transition_collisions_after_visible_compatibility() -> None:
    with pytest.raises(SerializationError, match="duplicate Cynefin transition"):
        plan_cynefin_records(
            {
                "domains": [
                    {"name": "complex", "items": ["Probe"]},
                    {"name": "clear", "items": ["Respond"]},
                ],
                "transitions": [
                    {"source": "complex", "target": "clear", "label": "move &#35;"},
                    {"source": "complex", "target": "clear", "label": "move ＆＃35;"},
                ],
            }
        )


def test_wardley_and_cynefin_disclose_entity_compatibility_once_without_mutating_ir():
    wardley_ir = {
        "title": "Map &#35;",
        "description": "Description &#x23;",
        "components": [
            {"id": "a", "label": "A &amp; node", "x": 0.6, "y": 0.7},
            {"id": "b", "label": "B", "x": 0.4, "y": 0.3},
        ],
        "links": [{"source": "a", "target": "b", "label": "link &#35;"}],
    }
    cynefin_ir = {
        "title": "Frame &#35;",
        "description": "Description &#x23;",
        "domains": [
            {"name": "complex", "items": ["Item &amp; one"]},
            {"name": "clear", "items": ["Item two"]},
        ],
        "transitions": [{"source": "complex", "target": "clear", "label": "move &#35;"}],
    }
    wardley_snapshot = repr(wardley_ir)
    cynefin_snapshot = repr(cynefin_ir)

    wardley = serialize_wardley(wardley_ir)
    cynefin = serialize_cynefin(cynefin_ir)

    for result in (wardley, cynefin):
        assert result.warnings == (
            "Entity-like literal text uses visible fullwidth ampersand and number-sign glyphs "
            "(＆ and ＃) because Mermaid 11.16 cannot preserve every literal entity form.",
        )
        assert "&#" not in result.code
    assert "Map ＆＃35;" in wardley.code
    assert '"A ＆amp; node"' in wardley.code
    assert "link ＆＃35;" in wardley.code
    assert "Frame ＆＃35;" in cynefin.code
    assert '"Item ＆amp; one"' in cynefin.code
    assert '"move ＆＃35;"' in cynefin.code
    assert repr(wardley_ir) == wardley_snapshot
    assert repr(cynefin_ir) == cynefin_snapshot


@pytest.mark.parametrize(
    ("serializer", "ir"),
    [
        (
            serialize_wardley,
            {
                "title": "Map",
                "description": "Description",
                "components": [
                    {
                        "id": f"component_{index}",
                        "label": f"{index:010d}" + "W" * 490,
                        "x": 0.5,
                        "y": 0.5,
                    }
                    for index in range(100)
                ],
            },
        ),
        (
            serialize_cynefin,
            {"domains": [{"name": "complex", "items": ["C" * 500 for _ in range(100)]}]},
        ),
        (
            serialize_zenuml,
            {
                "title": "Zen",
                "description": "Observed messages.",
                "participants": [
                    {"id": f"P{index}", "label": f"{index:03d}" + "Z" * 497} for index in range(100)
                ],
                "messages": [{"source": "P0", "target": "P1", "label": "call"}],
            },
        ),
    ],
)
def test_spatial_and_zenuml_serializers_reject_output_above_source_character_budget(serializer, ir):
    with pytest.raises(SerializationError, match="source-character limit of 50000"):
        serializer(ir)


@pytest.mark.parametrize(
    ("serializer", "ir"),
    [
        (
            serialize_wardley,
            {"components": [{"id": "api", "label": "API", "x": 0.5, "y": 0.5}]},
        ),
        (
            serialize_cynefin,
            {"domains": [{"name": "complex", "items": ["Emergent"]}]},
        ),
        (
            serialize_zenuml,
            {
                "participants": ["User", "API"],
                "messages": [{"source": "User", "target": "API", "label": "call"}],
            },
        ),
    ],
)
def test_spatial_and_zenuml_serializers_apply_source_line_budget_before_return(
    monkeypatch, serializer, ir
):
    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_LINES", 4)
    with pytest.raises(SerializationError, match="source-line limit of 4"):
        serializer(ir)


def test_experimental_source_character_budget_accepts_exact_boundary(monkeypatch):
    ir = {"domains": [{"name": "complex", "items": ["Emergent"]}]}
    baseline = serialize_cynefin(ir)
    monkeypatch.setattr(
        experimental_serializers,
        "MAX_EXPERIMENTAL_OUTPUT_CHARS",
        len(baseline.code),
    )
    assert serialize_cynefin(ir).code == baseline.code
    monkeypatch.setattr(
        experimental_serializers,
        "MAX_EXPERIMENTAL_OUTPUT_CHARS",
        len(baseline.code) - 1,
    )
    with pytest.raises(SerializationError, match="source-character limit"):
        serialize_cynefin(ir)


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


def test_railroad_plan_is_frozen_ordered_attributable_and_complete():
    root_rule = {
        "name": "root-rule",
        "evidence_ids": ["ocr-root"],
        "definition": {
            "type": "sequence",
            "evidence_ids": ["shape-sequence"],
            "elements": [
                {
                    "type": "terminal",
                    "value": 'literal &amp; "Q" \\ path',
                    "evidence_ids": ["ocr-terminal"],
                },
                {"type": "nonterminal", "name": "child"},
                {"type": "special", "text": "guard"},
                {
                    "type": "optional",
                    "element": {"type": "terminal", "value": "optional"},
                },
                {
                    "type": "one_or_more",
                    "element": {"type": "nonterminal", "name": "child"},
                },
                {
                    "type": "zero_or_more",
                    "element": {"type": "terminal", "value": "zero"},
                },
                {
                    "type": "choice",
                    "alternatives": [
                        {"type": "terminal", "value": "a"},
                        {"type": "terminal", "value": "b"},
                    ],
                },
            ],
        },
    }
    child_rule = {
        "name": "child",
        "definition": {"type": "terminal", "value": "child-value"},
    }

    plan = plan_railroad_records({"rules": [root_rule, child_rule]})

    assert plan.rules[0].source_record is root_rule
    assert plan.rules[0].source_name == "root-rule"
    assert plan.rules[0].emitted_id == "railroad_rule_root_rule"
    assert plan.rules[0].native_name == "root-rule"
    assert plan.rules[0].label == "root-rule ="
    assert plan.rules[0].definition_expression_id == "railroad_expression_1"
    assert plan.rules[1].emitted_id == "railroad_rule_child"
    assert [expression.emitted_id for expression in plan.expressions] == [
        f"railroad_expression_{index}" for index in range(1, 15)
    ]
    assert plan.expressions[0].source_record is root_rule["definition"]
    assert plan.expressions[0].kind == "sequence"
    assert plan.expressions[0].label is None
    assert plan.expressions[0].child_ids == (
        "railroad_expression_2",
        "railroad_expression_3",
        "railroad_expression_4",
        "railroad_expression_5",
        "railroad_expression_7",
        "railroad_expression_9",
        "railroad_expression_11",
    )
    assert plan.expressions[1].label == 'literal ＆amp; "Q" \\ path'
    assert plan.expressions[1].semantic_label == 'literal &amp; "Q" \\ path'
    assert plan.expressions[2].label == "child"
    assert plan.expressions[2].referenced_rule_id == "railroad_rule_child"
    assert plan.expressions[3].label == "? guard ?"
    assert plan.expressions[4].child_ids == ("railroad_expression_6",)
    assert [relation.emitted_id for relation in plan.relations] == [
        f"railroad_relation_{index}" for index in range(1, 15)
    ]
    assert plan.relations[0].source_record is root_rule["definition"]
    assert plan.relations[0].source_emitted_id == "railroad_rule_root_rule"
    assert plan.relations[0].target_emitted_id == "railroad_expression_1"
    assert plan.relations[1].source_record is root_rule["definition"]["elements"][0]
    assert plan.relations[1].source_emitted_id == "railroad_expression_1"
    assert plan.relations[1].target_emitted_id == "railroad_expression_2"
    assert {relation.semantic_relation for relation in plan.relations} == {"containment"}
    assert len(plan.accessibility) == 2
    with pytest.raises(FrozenInstanceError):
        plan.rules[0].label = "changed"
    with pytest.raises(FrozenInstanceError):
        plan.expressions[0].kind = "terminal"


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        (
            {
                "rules": [
                    {"name": "root", "definition": {"type": "terminal", "value": "x"}},
                    {"name": "root", "definition": {"type": "terminal", "value": "y"}},
                ]
            },
            "must be unique",
        ),
        (
            {
                "rules": [
                    {"name": "a-b", "definition": {"type": "terminal", "value": "x"}},
                    {"name": "a_b", "definition": {"type": "terminal", "value": "y"}},
                ]
            },
            "ambiguous after Mermaid normalization",
        ),
        (
            {"rules": [{"name": "root", "definition": {"type": "terminal"}}]},
            "terminal value",
        ),
        (
            {"rules": [{"name": "root", "definition": {"type": "special"}}]},
            "special text",
        ),
        (
            {
                "rules": [
                    {
                        "name": "root",
                        "definition": {"type": "sequence", "elements": []},
                    }
                ]
            },
            "requires elements",
        ),
        (
            {"rules": [{"name": "root", "definition": {"type": "unsupported"}}]},
            "unsupported railroad expression",
        ),
        (
            {
                "title": 1,
                "rules": [{"name": "root", "definition": {"type": "terminal", "value": "x"}}],
            },
            "title must be a non-empty string",
        ),
        (
            {
                "description": "bad\ud800",
                "rules": [{"name": "root", "definition": {"type": "terminal", "value": "x"}}],
            },
            "unsupported control",
        ),
        (
            {
                "rules": [
                    {
                        "name": "root",
                        "definition": {"type": "terminal", "value": "bad\u0000"},
                    }
                ]
            },
            "unsupported control",
        ),
    ],
)
def test_railroad_plan_rejects_duplicate_missing_unsupported_and_unsafe_records(ir, message):
    with pytest.raises(SerializationError, match=message):
        plan_railroad_records(ir)


def test_railroad_preserves_expression_and_depth_limits(monkeypatch):
    accepted = {
        "rules": [
            {
                "name": "root",
                "definition": {
                    "type": "sequence",
                    "elements": [{"type": "terminal", "value": str(index)} for index in range(499)],
                },
            }
        ]
    }
    assert len(plan_railroad_records(accepted).expressions) == 500
    accepted["rules"][0]["definition"]["elements"].append({"type": "terminal", "value": "overflow"})
    with pytest.raises(SerializationError, match="expression limit"):
        plan_railroad_records(accepted)

    rule_boundary = {
        "rules": [
            {
                "name": f"rule_{index}",
                "definition": {"type": "terminal", "value": "x"},
            }
            for index in range(500)
        ]
    }
    assert len(plan_railroad_records(rule_boundary).rules) == 500
    rule_boundary["rules"].append(
        {"name": "overflow", "definition": {"type": "terminal", "value": "x"}}
    )
    with pytest.raises(SerializationError, match="bounded non-empty rules"):
        plan_railroad_records(rule_boundary)

    depth_boundary: dict[str, object] = {"type": "terminal", "value": "x"}
    for _ in range(20):
        depth_boundary = {"type": "optional", "element": depth_boundary}
    assert (
        len(
            plan_railroad_records(
                {"rules": [{"name": "root", "definition": depth_boundary}]}
            ).expressions
        )
        == 21
    )
    depth_boundary = {"type": "optional", "element": depth_boundary}
    with pytest.raises(SerializationError, match="too deep"):
        plan_railroad_records({"rules": [{"name": "root", "definition": depth_boundary}]})

    monkeypatch.setattr(experimental_serializers, "MAX_ITEMS", 2)
    with pytest.raises(SerializationError, match="bounded non-empty rules"):
        plan_railroad_records(
            {
                "rules": [
                    {"name": f"rule_{index}", "definition": {"type": "terminal", "value": "x"}}
                    for index in range(3)
                ]
            }
        )


def test_railroad_unsafe_rule_mapping_avoids_safe_native_name_collisions():
    plan = plan_railroad_records(
        {
            "rules": [
                {"name": "click", "definition": {"type": "terminal", "value": "x"}},
                {"name": "rrmapped_1", "definition": {"type": "terminal", "value": "y"}},
                {
                    "name": "railroad_rule_click",
                    "definition": {"type": "terminal", "value": "z"},
                },
            ]
        }
    )

    assert [rule.source_name for rule in plan.rules] == [
        "click",
        "rrmapped_1",
        "railroad_rule_click",
    ]
    assert [rule.native_name for rule in plan.rules] == [
        "rrmapped_1_2",
        "rrmapped_1",
        "railroad_rule_click",
    ]
    assert len({rule.native_name for rule in plan.rules}) == 3
    assert plan.mapped_rule_names == ("click",)


@pytest.mark.parametrize(
    "name",
    [
        "terminal",
        "nonterminal",
        "special",
        "sequence",
        "choice",
        "optional",
        "oneOrMore",
        "zeroOrMore",
        "railroad-beta",
        "title",
        "titleRule",
        "title2",
        "xstyle",
        "xclassDef",
        "myStyle",
        "linkStyle",
    ],
)
def test_railroad_maps_native_grammar_reserved_rule_names(name: str) -> None:
    ir = {
        "rules": [
            {
                "name": "root",
                "definition": {"type": "nonterminal", "name": name},
            },
            {"name": name, "definition": {"type": "terminal", "value": "value"}},
        ]
    }

    plan = plan_railroad_records(ir)
    result = serialize_railroad(ir)

    assert plan.rules[1].source_name == name
    assert plan.rules[1].native_name == "rrmapped_2"
    assert plan.rules[1].label == "rrmapped_2 ="
    assert plan.expressions[0].label == name
    assert plan.expressions[0].referenced_rule_id == f"railroad_rule_{name.replace('-', '_')}"
    assert plan.mapped_rule_names == (name,)
    assert f"\n{name} =" not in result.code
    assert "\nrrmapped_2 =" in result.code
    assert result.warnings[0] == (
        "Source-active or grammar-reserved Railroad rule names were mapped to reserved "
        "native identifiers; source names remain in typed IR and nonterminal labels."
    )


def test_railroad_plan_preflights_character_and_line_budgets(monkeypatch):
    ir = {"rules": [{"name": "root", "definition": {"type": "terminal", "value": "x"}}]}
    regular = serialize_railroad(ir).code
    experimental = serialize_railroad(ir, experimental=True).code
    boundary = max(len(regular), len(experimental))

    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_CHARS", boundary)
    assert plan_railroad_records(ir).rules[0].source_name == "root"
    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_CHARS", boundary - 1)
    with pytest.raises(SerializationError, match="railroad output exceeds source-character"):
        plan_railroad_records(ir)

    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_CHARS", 50_000)
    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_LINES", 2)
    with pytest.raises(SerializationError, match="railroad output exceeds source-line"):
        plan_railroad_records(ir)


def test_railroad_active_text_is_source_only_and_visible_glyphs_remain_semantic():
    ir = {
        "title": 'Grammar &amp; "Q" \\ https://title.invalid <title> @import',
        "description": "click %%{init}%% https://description.invalid <desc>",
        "rules": [
            {
                "name": "root",
                "definition": {
                    "type": "sequence",
                    "elements": [
                        {
                            "type": "terminal",
                            "value": 'style https://x.invalid <script> &amp; "T" \\ path',
                        },
                        {
                            "type": "special",
                            "text": 'click %% <guard> directive &amp; "S" \\ path',
                        },
                        {"type": "nonterminal", "name": "style"},
                        {"type": "nonterminal", "name": "iconify"},
                    ],
                },
            },
            {
                "name": "style",
                "definition": {"type": "terminal", "value": "mapped"},
            },
            {
                "name": "iconify",
                "definition": {"type": "terminal", "value": "icon"},
            },
        ],
    }

    plan = plan_railroad_records(ir)
    result = serialize_railroad(ir)

    assert plan.semantic_title == 'Grammar &amp; "Q" \\ https://title.invalid <title> @import'
    assert plan.title == 'Grammar ＆amp; "Q" \\ https://title.invalid 〈title〉 @import'
    assert plan.accessibility[0].source_description == (
        "click %%{init}%% https://description.invalid <desc>"
    )
    assert plan.accessibility[0].description == (
        "click %%{init}%% https://description.invalid 〈desc〉"
    )
    assert plan.expressions[1].label == ('style https://x.invalid 〈script〉 ＆amp; "T" \\ path')
    assert (
        plan.expressions[1].semantic_label == 'style https://x.invalid <script> &amp; "T" \\ path'
    )
    assert plan.expressions[2].label == ('? click %% 〈guard〉 directive ＆amp; "S" \\ path ?')
    assert plan.expressions[2].semantic_label == '? click %% <guard> directive &amp; "S" \\ path ?'
    assert plan.expressions[3].label == "style"
    assert plan.expressions[3].referenced_rule_id == "railroad_rule_style"
    assert plan.expressions[4].label == "iconify"
    assert plan.expressions[4].referenced_rule_id == "railroad_rule_iconify"
    assert plan.rules[1].source_name == "style"
    assert plan.rules[1].native_name == "rrmapped_2"
    assert plan.rules[1].label == "rrmapped_2 ="
    assert plan.rules[2].native_name == "rrmapped_3"
    assert plan.mapped_rule_names == ("style", "iconify")
    assert "\nrrmapped_2 =" in result.code
    assert "\nrrmapped_3 =" in result.code
    assert "\nstyle =" not in result.code
    assert "https://" not in result.code
    assert "<script>" not in result.code
    assert "@import" not in result.code
    assert "%%" not in result.code
    assert "＆" in result.code
    assert "&amp;" not in result.code
    assert '\\"T\\"' in result.code
    assert "\\\\ path" in result.code
    assert result.warnings == (
        "Source-active or grammar-reserved Railroad rule names were mapped to reserved "
        "native identifiers; source names remain in typed IR and nonterminal labels.",
        "Railroad uses visible compatibility glyphs for angle brackets, number signs, "
        "entity-like text, and NFKC-sensitive quote or backslash characters.",
    )
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert (
        MermaidSecurityScanner(SecurityProfile.STRICT)
        .scan(unicodedata.normalize("NFKC", result.code))
        .safe
    )


def test_railroad_compatibility_normalized_active_text_stays_strict_safe() -> None:
    visible = (
        "safe； ｓｔｙｌｅ ｈｔｔｐｓ：／／example.invalid "
        "＜ｓｃｒｉｐｔ＞ ＠ｉｍｐｏｒｔ ｉｃｏｎｉｆｙ"
    )
    ir = {
        "rules": [
            {
                "name": "root",
                "definition": {"type": "terminal", "value": visible},
            }
        ]
    }

    plan = plan_railroad_records(ir)
    result = serialize_railroad(ir)

    assert plan.expressions[0].semantic_label == visible
    assert plan.expressions[0].label == visible
    assert "\u200b" not in plan.expressions[0].label
    assert "\u200b" in result.code
    assert result.warnings == ()
    scanner = MermaidSecurityScanner(SecurityProfile.STRICT)
    assert scanner.scan(result.code).safe
    assert scanner.scan(unicodedata.normalize("NFKC", result.code)).safe


def test_railroad_nfkc_quote_injection_is_neutralized_without_losing_semantics() -> None:
    visible = "a＂); evil = terminal(＂b"
    ir = {
        "rules": [
            {
                "name": "root",
                "definition": {"type": "terminal", "value": visible},
            }
        ]
    }

    plan = plan_railroad_records(ir)
    result = serialize_railroad(ir)
    normalized = unicodedata.normalize("NFKC", result.code)
    normalized_rule_lines = normalized.splitlines()[3:]

    assert plan.expressions[0].semantic_label == visible
    assert plan.expressions[0].label == "a″); evil = terminal(″b"
    assert len(normalized_rule_lines) == 1
    assert normalized_rule_lines[0].startswith("root = terminal(")
    assert not any(line.startswith("evil =") for line in normalized_rule_lines)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(normalized).safe
    assert result.warnings == (
        "Railroad uses visible compatibility glyphs for angle brackets, number signs, "
        "entity-like text, and NFKC-sensitive quote or backslash characters.",
    )


def test_railroad_hash_and_preprocessor_substrings_have_exact_safe_visible_text() -> None:
    values = ["plain #35; text", "xstyle:a#foo;tail", "xclassDef:a#foo;tail"]
    ir = {
        "rules": [
            {
                "name": "root",
                "definition": {
                    "type": "sequence",
                    "elements": [{"type": "terminal", "value": value} for value in values],
                },
            }
        ]
    }

    plan = plan_railroad_records(ir)
    result = serialize_railroad(ir)

    assert [expression.semantic_label for expression in plan.expressions[1:]] == values
    assert [expression.label for expression in plan.expressions[1:]] == [
        "plain ＃35; text",
        "xstyle:a＃foo;tail",
        "xclassDef:a＃foo;tail",
    ]
    assert "#" not in result.code
    assert "xstyle" not in result.code
    assert "xclassDef" not in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert (
        MermaidSecurityScanner(SecurityProfile.STRICT)
        .scan(unicodedata.normalize("NFKC", result.code))
        .safe
    )
    assert result.warnings == (
        "Railroad uses visible compatibility glyphs for angle brackets, number signs, "
        "entity-like text, and NFKC-sensitive quote or backslash characters.",
    )


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
    assert "zenuml_participant_User->>zenuml_participant_API: 결제" in result.code


def test_zenuml_plan_preserves_records_legacy_scalar_and_exact_visible_labels() -> None:
    participant = {
        "id": "end",
        "label": "User #1; participant X as X",
        "bbox": [1, 2, 3, 4],
        "evidence_ids": ["ocr-user"],
        "style": "must not leak",
    }
    message = {
        "source": "end",
        "target": "API",
        "label": "call &#35;; retry",
        "evidence_ids": ["arrow-1"],
    }
    ir = {"participants": [participant, "API"], "messages": [message]}
    snapshot = repr(ir)

    plan = plan_zenuml_structure(ir)

    assert plan.participants[0].source_record is participant
    assert plan.participants[0].source_id == "end"
    assert plan.participants[0].emitted_id == "zenuml_participant_end"
    assert plan.participants[0].label == "User ＃1⁏ participant X as X"
    assert plan.participants[0].semantic_label == "User #1; participant X as X"
    assert plan.participants[1].source_record is None
    assert plan.participants[1].emitted_id == "zenuml_participant_API"
    assert plan.messages[0].source_record is message
    assert plan.messages[0].emitted_id == "zenuml_message_1"
    assert plan.messages[0].source_emitted_id == "zenuml_participant_end"
    assert plan.messages[0].target_emitted_id == "zenuml_participant_API"
    assert plan.messages[0].label == "call ＆＃35⁏⁏ retry"
    assert plan.messages[0].semantic_label == "call &#35;; retry"
    assert plan.compatibility_substituted
    assert repr(ir) == snapshot
    assert "must not leak" not in serialize_zenuml(ir).code
    with pytest.raises(FrozenInstanceError):
        plan.participants[0].label = "mutated"


def test_zenuml_legacy_record_planner_preserves_exact_tuple_of_dicts_contract() -> None:
    participants, messages = plan_zenuml_records(
        {
            "participants": [
                {"id": "User", "label": "User #1; literal", "bbox": [1, 2, 3, 4]},
                "API",
            ],
            "messages": [
                {
                    "source": "User",
                    "target": "API",
                    "label": "call &#35;; literal",
                    "evidence_ids": ["arrow-1"],
                }
            ],
        }
    )

    assert participants == [
        {"id": "User", "label": "User #1; literal"},
        {"id": "API", "label": "API"},
    ]
    assert messages == [{"source": "User", "target": "API", "label": "call &#35;; literal"}]


def test_zenuml_allows_duplicate_visible_aliases_because_endpoints_are_namespaced() -> None:
    ir = {
        "participants": [
            {"id": "User-A", "label": "User"},
            {"id": "User_B", "label": "User"},
        ],
        "messages": [{"source": "User-A", "target": "User_B", "label": "call"}],
    }

    plan = plan_zenuml_structure(ir)
    result = serialize_zenuml(ir)

    assert [participant.label for participant in plan.participants] == ["User", "User"]
    assert "zenuml_participant_User-A->>zenuml_participant_User_B: call" in result.code


def test_zenuml_rejects_duplicate_ids_coercion_and_control_characters() -> None:
    valid_message = {"source": "User", "target": "API", "label": "call"}
    with pytest.raises(SerializationError, match="duplicate ZenUML participant"):
        plan_zenuml_structure(
            {"participants": ["User", {"id": "User"}], "messages": [valid_message]}
        )
    for value in (7, True, "bad\u200bvalue", "bad\nvalue", "bad\ud800value"):
        with pytest.raises(SerializationError, match="string|control or format|identifier"):
            plan_zenuml_structure(
                {
                    "participants": [{"id": "User", "label": value}, "API"],
                    "messages": [valid_message],
                }
            )
    with pytest.raises(SerializationError, match="whitespace normalization"):
        plan_zenuml_structure(
            {
                "participants": [" User ", "API"],
                "messages": [{"source": " User ", "target": "API", "label": "call"}],
            }
        )
    with pytest.raises(SerializationError, match="whitespace normalization"):
        plan_zenuml_structure(
            {
                "participants": ["User", "API"],
                "messages": [{"source": " User ", "target": "API", "label": "call"}],
            }
        )


def test_zenuml_neutralizes_active_text_and_discloses_visible_grammar_glyphs() -> None:
    ir = {
        "title": "Zen &#35;",
        "description": "See https://docs.invalid <guide>",
        "participants": [
            {"id": "end", "label": "User #1; participant X as X"},
            {"id": "API", "label": "API &amp; service"},
        ],
        "messages": [
            {
                "source": "end",
                "target": "API",
                "label": "style https://x.invalid <script> #tag; A->>A: inject",
            }
        ],
    }

    result = serialize_zenuml(ir)

    assert "participant zenuml_participant_end as User ＃1⁏ participant X as X" in result.code
    assert "API ＆amp⁏ service" in result.code
    assert "＃tag⁏ A->>A: inject" in result.code
    assert "https://" not in result.code
    assert "<script>" not in result.code
    assert result.warnings == (
        "ZenUML is unavailable in Mermaid 11.16 and was emitted as sequence.",
        "ZenUML sequence fallback uses visible compatibility glyphs for "
        "grammar-conflicting label characters.",
    )
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert (
        MermaidSecurityScanner(SecurityProfile.STRICT)
        .scan(unicodedata.normalize("NFKC", result.code))
        .safe
    )


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
    assert 'data_lineage_dataset_raw[("Raw")]' in lineage.code
    assert "data_lineage_process_etl -->|writes| data_lineage_dataset_clean" in lineage.code


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


def test_organization_plan_is_frozen_attributable_and_exactly_parented():
    ceo = {
        "id": "end",
        "name": "CEO &amp; Chair",
        "bbox": [1, 2, 30, 40],
        "evidence_ids": ["ocr-ceo"],
        "children": [
            {
                "id": "platform-team",
                "label": 'Platform "Lead" \\ owner',
                "evidence_ids": ["ocr-lead"],
            }
        ],
    }

    plan = plan_organization_hierarchy({"root": ceo, "direction": "TB"})

    assert plan.direction == "LR"
    assert plan.nodes[0].source_record is ceo
    assert plan.nodes[0].emitted_id == "treeview_node_end"
    assert plan.nodes[0].semantic_label == "CEO &amp; Chair"
    assert plan.nodes[0].label == "CEO ＆amp; Chair"
    assert plan.nodes[1].emitted_id == "treeview_node_platform_team"
    assert plan.nodes[1].label == "Platform ″Lead″ ∖ owner"
    assert plan.nodes[1].parent_emitted_id == "treeview_node_end"
    assert plan.relations[0].source_record is ceo["children"][0]
    assert plan.relations[0].emitted_id == "organization_relation_1"
    assert plan.relations[0].source_emitted_id == "treeview_node_end"
    assert plan.relations[0].target_emitted_id == "treeview_node_platform_team"
    assert plan.compatibility_substituted
    with pytest.raises(FrozenInstanceError):
        plan.direction = "TB"


def test_organization_preserves_legacy_name_but_rejects_alias_conflict_and_invention():
    result = serialize_organization(
        {
            "root": {
                "id": "ceo",
                "name": "Chief Executive",
                "children": [{"id": "cto", "name": "Technology"}],
            }
        }
    )
    assert '"Chief Executive"' in result.code
    assert '"Technology"' in result.code

    with pytest.raises(SerializationError, match="aliases must agree"):
        plan_organization_hierarchy(
            {
                "root": {
                    "id": "ceo",
                    "label": "CEO",
                    "name": "Chief",
                    "children": [{"id": "cto", "label": "CTO"}],
                }
            }
        )
    with pytest.raises(SerializationError, match=r"nodes\[1\]\.label"):
        plan_organization_hierarchy(
            {
                "root": {
                    "id": "ceo",
                    "label": "CEO",
                    "children": [{"id": "cto"}],
                }
            }
        )


def test_organization_preserves_deterministic_legacy_ids_without_inventing_labels():
    plan = plan_organization_hierarchy(
        {
            "root": {
                "label": "CEO",
                "children": [{"label": "CTO"}],
            }
        }
    )

    assert [node.source_id for node in plan.nodes] == ["node_1", "node_2"]
    assert [node.emitted_id for node in plan.nodes] == [
        "treeview_node_node_1",
        "treeview_node_node_2",
    ]
    assert [node.semantic_label for node in plan.nodes] == ["CEO", "CTO"]
    assert plan.relations[0].source_id == "node_1"
    assert plan.relations[0].target_id == "node_2"


def test_organization_preserves_treeview_sized_source_identifiers() -> None:
    long_id = "a" * 200

    plan = plan_organization_hierarchy(
        {
            "root": {
                "id": long_id,
                "label": "Executive",
                "children": [{"id": "report", "label": "Report"}],
            }
        }
    )

    assert plan.nodes[0].source_id == long_id
    assert plan.nodes[0].emitted_id == f"treeview_node_{long_id}"


@pytest.mark.parametrize(
    ("root", "message"),
    [
        (
            {"id": 1, "label": "CEO", "children": [{"id": "cto", "label": "CTO"}]},
            r"nodes\[0\]\.id",
        ),
        (
            {
                "id": "ceo",
                "label": "CEO\ud800",
                "children": [{"id": "cto", "label": "CTO"}],
            },
            "unsupported control",
        ),
        (
            {
                "id": "a-b",
                "label": "CEO",
                "children": [{"id": "a_b", "label": "CTO"}],
            },
            "ambiguous after Mermaid normalization",
        ),
    ],
)
def test_organization_direct_planner_rejects_invalid_types_unicode_and_ids(root, message):
    with pytest.raises(SerializationError, match=message):
        plan_organization_hierarchy({"root": root})


def test_organization_security_projection_matches_visible_compatibility_labels():
    result = serialize_organization(
        {
            "root": {
                "id": "end",
                "label": 'CEO "Q" \\ &amp; https://x.invalid <script>',
                "children": [{"id": "subgraph", "label": "style click callback"}],
            }
        }
    )

    assert result.fallback_chain == ("organization", "treeview", "flowchart")
    assert result.warnings[0].startswith(
        "Organization chart was projected through TreeView semantics"
    )
    assert "treeview_node_end" in result.code
    assert "CEO ″Q″ ∖ ＆amp;" in result.code
    assert "https://" not in result.code
    assert "<script>" not in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert (
        MermaidSecurityScanner(SecurityProfile.STRICT)
        .scan(unicodedata.normalize("NFKC", result.code))
        .safe
    )


def test_data_lineage_plan_is_frozen_namespaced_and_attributable():
    raw = {
        "id": "end",
        "label": 'Raw &amp; "Q" \\ path',
        "bbox": [1, 2, 3, 4],
        "evidence_ids": ["ocr-raw"],
    }
    etl = {"id": "style", "label": "ETL", "evidence_ids": ["ocr-etl"]}
    relation = {
        "source": "end",
        "target": "style",
        "label": "writes|daily; verified",
        "evidence_ids": ["line-etl"],
    }

    plan = plan_data_lineage_records(
        {
            "datasets": [raw],
            "processes": [etl],
            "relations": [relation],
            "direction": "RL",
        }
    )

    assert plan.direction == "RL"
    assert plan.nodes[0].source_record is raw
    assert plan.nodes[0].emitted_id == "data_lineage_dataset_end"
    assert plan.nodes[0].shape == "cylinder"
    assert plan.nodes[0].label == "Raw ＆amp; ″Q″ ∖ path"
    assert plan.nodes[1].source_record is etl
    assert plan.nodes[1].emitted_id == "data_lineage_process_style"
    assert plan.relations[0].source_record is relation
    assert plan.relations[0].emitted_id == "data_lineage_relation_1"
    assert plan.relations[0].source_emitted_id == "data_lineage_dataset_end"
    assert plan.relations[0].target_emitted_id == "data_lineage_process_style"
    assert plan.relations[0].semantic_label == "writes|daily; verified"
    assert plan.relations[0].label == "writes∣daily⁏ verified"
    assert plan.compatibility_substituted
    with pytest.raises(FrozenInstanceError):
        plan.direction = "LR"


def test_data_lineage_edge_labels_use_parse_safe_visible_compatibility_glyphs() -> None:
    ir = {
        "datasets": [{"id": "raw", "label": "Raw"}],
        "processes": [{"id": "etl", "label": "ETL"}],
        "relations": [
            {
                "source": "raw",
                "target": "etl",
                "label": "callback() [raw] {ok} ops@import",
            }
        ],
    }

    plan = plan_data_lineage_records(ir)
    result = serialize_data_lineage(ir)

    assert plan.relations[0].label == "callback❨❩ ⟦raw⟧ ⦃ok⦄ ops＠import"
    assert "callback❨❩ ⟦raw⟧ ⦃ok⦄ ops＠\u200bimport" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert (
        MermaidSecurityScanner(SecurityProfile.STRICT)
        .scan(unicodedata.normalize("NFKC", result.code))
        .safe
    )
    assert result.warnings[-1].startswith(
        "Data Lineage Flowchart fallback uses visible compatibility glyphs"
    )


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        (
            {
                "datasets": [{"id": 1, "label": "Raw"}],
                "processes": [],
                "relations": [{"source": "raw", "target": "etl"}],
            },
            r"datasets\[0\]\.id",
        ),
        (
            {
                "datasets": [{"id": "raw", "label": "Raw\u0000"}],
                "processes": [{"id": "etl", "label": "ETL"}],
                "relations": [{"source": "raw", "target": "etl"}],
            },
            "unsupported control",
        ),
        (
            {
                "datasets": [
                    {"id": "raw-data", "label": "Raw"},
                    {"id": "raw_data", "label": "Duplicate"},
                ],
                "processes": [{"id": "etl", "label": "ETL"}],
                "relations": [{"source": "raw-data", "target": "etl"}],
            },
            "ambiguous after Mermaid normalization",
        ),
        (
            {
                "datasets": [{"id": "raw", "label": "Raw"}],
                "processes": [{"id": "etl", "label": "ETL"}],
                "relations": [{"source": " raw ", "target": "etl"}],
            },
            "must not require whitespace normalization",
        ),
        (
            {
                "datasets": [{"id": "raw", "label": "Raw"}],
                "processes": [{"id": "etl", "label": "ETL"}],
                "relations": [{"source": "raw", "target": "etl"}],
                "direction": "diagonal",
            },
            "direction must be",
        ),
    ],
)
def test_data_lineage_direct_planner_rejects_invalid_types_unicode_and_ids(ir, message):
    with pytest.raises(SerializationError, match=message):
        plan_data_lineage_records(ir)


def test_data_lineage_security_projection_uses_exact_visible_labels_and_warning():
    result = serialize_data_lineage(
        {
            "title": "Lineage &#35;",
            "description": "See https://docs.invalid <guide>",
            "datasets": [{"id": "end", "label": 'Raw "Q" \\ &amp; https://raw.invalid'}],
            "processes": [{"id": "style", "label": "style <script> callback"}],
            "relations": [
                {
                    "source": "end",
                    "target": "style",
                    "label": "writes|daily; click https://edge.invalid",
                }
            ],
        }
    )

    assert "data_lineage_dataset_end" in result.code
    assert "Raw ″Q″ ∖ ＆amp;" in result.code
    assert "writes∣daily⁏" in result.code
    assert "https://" not in result.code
    assert "<script>" not in result.code
    assert result.warnings == (
        "Data lineage was emitted as a portable flowchart.",
        "Data Lineage Flowchart fallback uses visible compatibility glyphs for "
        "grammar-conflicting label characters.",
    )
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert (
        MermaidSecurityScanner(SecurityProfile.STRICT)
        .scan(unicodedata.normalize("NFKC", result.code))
        .safe
    )


def test_data_lineage_preserves_explicit_source_ids_as_legacy_missing_labels():
    ir = {
        "datasets": [{"id": "raw"}],
        "processes": [{"id": "etl"}],
        "relations": [{"source": "raw", "target": "etl"}],
    }

    plan = plan_data_lineage_records(ir)
    result = serialize_data_lineage(ir)

    assert [node.semantic_label for node in plan.nodes] == ["raw", "etl"]
    assert [node.label for node in plan.nodes] == ["raw", "etl"]
    assert 'data_lineage_dataset_raw[("raw")]' in result.code
    assert 'data_lineage_process_etl["etl"]' in result.code


def test_data_lineage_warns_when_only_accessibility_text_needs_visible_compatibility():
    result = serialize_data_lineage(
        {
            "title": "Lineage <review>",
            "description": "Safe description",
            "datasets": [{"id": "raw", "label": "Raw"}],
            "processes": [{"id": "etl", "label": "ETL"}],
            "relations": [{"source": "raw", "target": "etl"}],
        }
    )

    assert "accTitle: Lineage 〈review〉" in result.code
    assert not plan_data_lineage_records(
        {
            "datasets": [{"id": "raw", "label": "Raw"}],
            "processes": [{"id": "etl", "label": "ETL"}],
            "relations": [{"source": "raw", "target": "etl"}],
        }
    ).compatibility_substituted
    assert result.warnings == (
        "Data lineage was emitted as a portable flowchart.",
        "Data Lineage Flowchart fallback uses visible compatibility glyphs for "
        "grammar-conflicting label characters.",
    )


def test_organization_and_lineage_apply_record_and_output_budgets(monkeypatch):
    monkeypatch.setattr(experimental_serializers, "MAX_ITEMS", 2)
    with pytest.raises(SerializationError, match="record limits"):
        plan_organization_hierarchy(
            {
                "root": {
                    "id": "ceo",
                    "label": "CEO",
                    "children": [
                        {
                            "id": "cto",
                            "label": "CTO",
                            "children": [{"id": "lead", "label": "Lead"}],
                        }
                    ],
                }
            }
        )
    with pytest.raises(SerializationError, match="item limit"):
        plan_data_lineage_records(
            {
                "datasets": [{"id": "raw", "label": "Raw"}],
                "processes": [{"id": "etl", "label": "ETL"}],
                "relations": [{"source": "raw", "target": "etl"}],
            }
        )

    monkeypatch.setattr(experimental_serializers, "MAX_ITEMS", 500)
    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_CHARS", 20)
    organization_ir = {
        "root": {
            "id": "ceo",
            "label": "CEO",
            "children": [{"id": "cto", "label": "CTO"}],
        }
    }
    lineage_ir = {
        "datasets": [{"id": "raw", "label": "Raw"}],
        "processes": [{"id": "etl", "label": "ETL"}],
        "relations": [{"source": "raw", "target": "etl"}],
    }
    with pytest.raises(SerializationError, match="organization output exceeds"):
        serialize_organization(organization_ir)
    with pytest.raises(SerializationError, match="data_lineage output exceeds"):
        serialize_data_lineage(lineage_ir)

    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_CHARS", 50_000)
    monkeypatch.setattr(experimental_serializers, "MAX_EXPERIMENTAL_OUTPUT_LINES", 2)
    with pytest.raises(SerializationError, match="organization output exceeds source-line"):
        plan_organization_hierarchy(organization_ir)
    with pytest.raises(SerializationError, match="data_lineage output exceeds source-line"):
        plan_data_lineage_records(lineage_ir)


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
        (
            "wardley",
            serialize_wardley(
                {
                    "components": [
                        {"id": "user", "label": "User", "x": 0.9, "y": 0.6, "anchor": True},
                        {"id": "api", "label": "API", "x": 1e-10, "y": 0.4},
                    ],
                    "links": [{"source": "user", "target": "api"}],
                }
            ).code,
        ),
        (
            "cynefin",
            serialize_cynefin(
                {
                    "domains": [
                        {"name": "complex", "items": ["Emergent"]},
                        {"name": "clear", "items": ["Known"]},
                    ],
                    "transitions": [{"source": "complex", "target": "clear"}],
                }
            ).code,
        ),
        (
            "railroad",
            serialize_railroad(
                {
                    "rules": [
                        {
                            "name": "root",
                            "definition": {"type": "terminal", "value": "x"},
                        }
                    ]
                }
            ).code,
        ),
        (
            "sequence",
            serialize_zenuml(
                {
                    "participants": ["User", "API"],
                    "messages": [{"source": "User", "target": "API", "label": "call"}],
                }
            ).code,
        ),
        (
            "treeview",
            serialize_organization(
                {
                    "root": {
                        "id": "ceo",
                        "label": "CEO",
                        "children": [{"id": "cto", "label": "CTO"}],
                    }
                }
            ).code,
        ),
        (
            "flowchart-v2",
            serialize_data_lineage(
                {
                    "datasets": [{"id": "raw", "label": "Raw"}],
                    "processes": [{"id": "etl", "label": "ETL"}],
                    "relations": [{"source": "raw", "target": "etl"}],
                }
            ).code,
        ),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for expected_type, code in cases:
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (code, outcome.runtime.error, outcome.warnings)
            assert outcome.runtime.diagram_type.casefold() == expected_type
    finally:
        runtime.close()


@pytest.mark.integration
def test_railroad_shared_plan_matches_strict_mermaid_11_16_visible_runtime() -> None:
    compatibility_visible = (
        "safe； ｓｔｙｌｅ ｈｔｔｐｓ：／／example.invalid "
        "＜ｓｃｒｉｐｔ＞ ＠ｉｍｐｏｒｔ ｉｃｏｎｉｆｙ"
    )
    injection_visible = "a＂); evil = terminal(＂b"
    preprocessor_values = ["plain #35; text", "xstyle:a#foo;tail", "xclassDef:a#foo;tail"]
    ir = {
        "title": 'Grammar &amp; "Q" \\ https://title.invalid <title> @import',
        "description": "click %%{init}%% https://description.invalid <desc>",
        "rules": [
            {
                "name": "root",
                "definition": {
                    "type": "sequence",
                    "elements": [
                        {
                            "type": "terminal",
                            "value": 'style https://x.invalid <script> &amp; "T" \\ path',
                        },
                        {"type": "nonterminal", "name": "style"},
                        {
                            "type": "special",
                            "text": 'click %% <guard> directive &amp; "S" \\ path',
                        },
                        {
                            "type": "optional",
                            "element": {
                                "type": "choice",
                                "alternatives": [
                                    {"type": "terminal", "value": "alpha"},
                                    {"type": "terminal", "value": "beta"},
                                ],
                            },
                        },
                        {"type": "nonterminal", "name": "iconify"},
                        {"type": "terminal", "value": compatibility_visible},
                        {"type": "terminal", "value": injection_visible},
                        *({"type": "terminal", "value": value} for value in preprocessor_values),
                        {"type": "nonterminal", "name": "terminal"},
                        {"type": "nonterminal", "name": "titleRule"},
                        {"type": "nonterminal", "name": "railroad-beta"},
                    ],
                },
            },
            {"name": "style", "definition": {"type": "terminal", "value": "mapped"}},
            {"name": "iconify", "definition": {"type": "terminal", "value": "icon"}},
            {"name": "terminal", "definition": {"type": "terminal", "value": "term"}},
            {"name": "titleRule", "definition": {"type": "terminal", "value": "named"}},
            {
                "name": "railroad-beta",
                "definition": {"type": "terminal", "value": "header"},
            },
        ],
    }
    plan = plan_railroad_records(ir)
    result = serialize_railroad(ir)

    assert plan.rules[1].label == "rrmapped_2 ="
    assert plan.rules[2].label == "rrmapped_3 ="
    assert plan.expressions[1].label == ('style https://x.invalid 〈script〉 ＆amp; "T" \\ path')
    assert plan.expressions[2].label == "style"
    assert plan.expressions[3].label == ('? click %% 〈guard〉 directive ＆amp; "S" \\ path ?')
    injection_expression = next(
        expression
        for expression in plan.expressions
        if expression.semantic_label == injection_visible
    )
    assert injection_expression.label == "a″); evil = terminal(″b"
    assert [
        expression.label
        for expression in plan.expressions
        if expression.semantic_label in preprocessor_values
    ] == ["plain ＃35; text", "xstyle:a＃foo;tail", "xclassDef:a＃foo;tail"]
    assert [rule.label for rule in plan.rules[3:]] == [
        "rrmapped_4 =",
        "rrmapped_5 =",
        "rrmapped_6 =",
    ]
    assert len(plan.relations) == len(plan.expressions)
    assert result.warnings == (
        "Source-active or grammar-reserved Railroad rule names were mapped to reserved "
        "native identifiers; source names remain in typed IR and nonterminal labels.",
        "Railroad uses visible compatibility glyphs for angle brackets, number signs, "
        "entity-like text, and NFKC-sensitive quote or backslash characters.",
    )

    runtime = NodeMermaidRuntime()
    process = None
    try:
        outcome = CandidateValidator(runtime, SecurityProfile.STRICT).validate(result.code, 20)
        normalized_outcome = CandidateValidator(runtime, SecurityProfile.STRICT).validate(
            unicodedata.normalize("NFKC", result.code), 20
        )
        process = runtime._process
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.diagram_type.casefold() == "railroad"
    assert normalized_outcome.runtime.syntax_valid, normalized_outcome.runtime.error
    assert normalized_outcome.runtime.render_valid, normalized_outcome.runtime.error
    assert process is not None and process.poll() is not None
    assert runtime._process is None
    assert runtime._process_group_id is None

    root = ET.fromstring(outcome.runtime.svg or "")
    visible_texts = {
        " ".join("".join(element.itertext()).replace("\u200b", "").split())
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"title", "desc", "text"}
        and "".join(element.itertext()).strip()
    }
    assert 'Grammar ＆amp; "Q" \\ https://title.invalid 〈title〉 @import' in visible_texts
    assert "click %%{init}%% https://description.invalid 〈desc〉" in visible_texts
    assert 'style https://x.invalid 〈script〉 ＆amp; "T" \\ path' in visible_texts
    assert '? click %% 〈guard〉 directive ＆amp; "S" \\ path ?' in visible_texts
    assert compatibility_visible in visible_texts
    assert "a″); evil = terminal(″b" in visible_texts
    assert {"plain ＃35; text", "xstyle:a＃foo;tail", "xclassDef:a＃foo;tail"} <= visible_texts
    assert {
        "style",
        "iconify",
        "terminal",
        "titleRule",
        "railroad-beta",
        "alpha",
        "beta",
        "mapped",
        "icon",
        "term",
        "named",
        "header",
    } <= visible_texts
    assert {
        "root =",
        "rrmapped_2 =",
        "rrmapped_3 =",
        "rrmapped_4 =",
        "rrmapped_5 =",
        "rrmapped_6 =",
    } <= visible_texts
    normalized_root = ET.fromstring(normalized_outcome.runtime.svg or "")
    normalized_texts = {
        " ".join("".join(element.itertext()).replace("\u200b", "").split())
        for element in normalized_root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"title", "desc", "text"}
        and "".join(element.itertext()).strip()
    }
    assert "evil =" not in normalized_texts
    assert all(
        "marker-start" not in element.attrib and "marker-end" not in element.attrib
        for element in root.iter()
    )


@pytest.mark.integration
def test_organization_and_lineage_match_visible_mermaid_11_16_fallback_contracts() -> None:
    organization_ir = {
        "root": {
            "id": "end",
            "label": "CEO &amp; Chair https://org.invalid",
            "children": [{"id": "style", "label": "Platform <team> callback"}],
        }
    }
    native_organization = serialize_organization(organization_ir)
    nested_organization = serialize_organization(organization_ir, native_runtime_valid=False)
    lineage = serialize_data_lineage(
        {
            "title": "Lineage &#35;",
            "description": "See https://docs.invalid <guide>",
            "datasets": [{"id": "end", "label": 'Raw "Q" \\ &amp; source'}],
            "processes": [{"id": "style", "label": "ETL <verified>"}],
            "relations": [
                {
                    "source": "end",
                    "target": "style",
                    "label": "writes|daily; callback() [raw] {ok} ops@import https://edge.invalid",
                }
            ],
        }
    )

    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        native_outcome = validator.validate(native_organization.code, 20)
        nested_outcome = validator.validate(nested_organization.code, 20)
        lineage_outcome = validator.validate(lineage.code, 20)
    finally:
        runtime.close()

    assert native_outcome.runtime.render_valid, native_outcome.runtime.error
    assert native_outcome.runtime.diagram_type.casefold() == "treeview"
    assert nested_outcome.runtime.render_valid, nested_outcome.runtime.error
    assert nested_outcome.runtime.diagram_type.casefold() == "flowchart-v2"
    assert lineage_outcome.runtime.render_valid, lineage_outcome.runtime.error
    assert lineage_outcome.runtime.diagram_type.casefold() == "flowchart-v2"

    native_root = ET.fromstring(native_outcome.runtime.svg or "")
    native_nodes = [
        element
        for element in native_root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text"
        and "treeView-node-label" in element.attrib.get("class", "")
        and "".join(element.itertext()).strip() != "/"
    ]
    assert [
        " ".join("".join(element.itertext()).replace("\u200b", "").split())
        for element in native_nodes
    ] == ["CEO ＆amp; Chair https://org.invalid", "Platform <team> callback"]
    assert [float(element.attrib["x"]) for element in native_nodes] == [20.0, 35.0]

    for outcome in (nested_outcome, lineage_outcome):
        root = ET.fromstring(outcome.runtime.svg or "")
        visible_text = {
            " ".join("".join(element.itertext()).replace("\u200b", "").split())
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] in {"title", "desc", "text", "span", "p"}
            and "".join(element.itertext()).strip()
        }
        if outcome is nested_outcome:
            assert "CEO ＆amp; Chairhttps://org.invalid" in visible_text
            assert "Platform <team> callback" in visible_text
        else:
            assert "Lineage ＆＃35;" in visible_text
            assert "See https://docs.invalid 〈guide〉" in visible_text
            assert "Raw ″Q″ ∖ ＆amp; source" in visible_text
            assert "ETL <verified>" in visible_text
            assert (
                "writes∣daily⁏ callback❨❩⟦raw⟧ ⦃ok⦄ ops＠importhttps://edge.invalid" in visible_text
            )


@pytest.mark.integration
def test_zenuml_sequence_fallback_preserves_namespaced_ids_and_visible_safe_text() -> None:
    result = serialize_zenuml(
        {
            "title": "Zen &#35;",
            "description": "See https://docs.invalid <guide>",
            "participants": [
                {"id": "end", "label": "User #1; participant X as X"},
                {"id": "A-B", "label": "API &amp; service"},
            ],
            "messages": [
                {
                    "source": "end",
                    "target": "A-B",
                    "label": 'style https://x.invalid <script> #tag; inject "Q" \\ path',
                }
            ],
        }
    )
    assert "participant zenuml_participant_end" in result.code
    assert "participant zenuml_participant_A-B" in result.code
    assert "zenuml_participant_end->>zenuml_participant_A-B" in result.code

    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.diagram_type.casefold() == "sequence"
    root = ET.fromstring(outcome.runtime.svg or "")
    visible_texts = [
        " ".join("".join(element.itertext()).replace("\u200b", "").split())
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] in {"title", "desc", "text"}
    ]
    assert "Zen ＆＃35;" in visible_texts
    assert "See https://docs.invalid 〈guide〉" in visible_texts
    assert visible_texts.count("User ＃1⁏ participant X as X") == 2
    assert visible_texts.count("API ＆amp⁏ service") == 2
    assert 'style https://x.invalid <script> ＃tag⁏ inject "Q" \\ path' in visible_texts
    assert "X" not in visible_texts


@pytest.mark.integration
def test_wardley_runtime_uses_xy_screen_projection_and_plain_links() -> None:
    result = serialize_wardley(
        {
            "components": [
                {"id": "a", "label": "A", "x": 0.2, "y": 0.8},
                {"id": "b", "label": "B", "x": 0.8, "y": 0.2},
            ],
            "links": [{"source": "a", "target": "b"}],
        }
    )
    assert 'component "A" [0.8, 0.2]' in result.code
    assert 'component "B" [0.2, 0.8]' in result.code

    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
        assert outcome.runtime.render_valid, outcome.runtime.error
        root = ET.fromstring(outcome.runtime.svg)
        circles = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "circle"]
        assert [(float(item.attrib["cx"]), float(item.attrib["cy"])) for item in circles] == [
            (pytest.approx(208.8), pytest.approx(148.8)),
            (pytest.approx(691.2), pytest.approx(451.2)),
        ]
        link = next(
            element
            for element in root.iter()
            if "wardley-link" in element.attrib.get("class", "").split()
        )
        assert "marker-start" not in link.attrib
        assert "marker-end" not in link.attrib
    finally:
        runtime.close()


@pytest.mark.integration
def test_wardley_flowchart_fallback_renders_plain_links_and_compatibility_text() -> None:
    result = serialize_wardley(
        {
            "components": [
                {"id": "a", "label": 'A "quoted"', "x": 0.2, "y": 0.8},
                {"id": "b", "label": "B \\ path", "x": 0.8, "y": 0.2},
            ],
            "links": [{"source": "a", "target": "b", "label": "uses | link"}],
        },
        native_runtime_valid=False,
    )

    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.diagram_type.casefold().startswith("flowchart")
    root = ET.fromstring(outcome.runtime.svg or "")
    visible_text = " ".join("".join(element.itertext()) for element in root.iter())
    assert "A ″quoted″" in visible_text
    assert "B ∖ path" in visible_text
    assert "uses ∣ link" in visible_text
    flowchart_links = [
        element
        for element in root.iter()
        if "flowchart-link" in element.attrib.get("class", "").split()
    ]
    assert len(flowchart_links) == 1
    assert "marker-start" not in flowchart_links[0].attrib
    assert "marker-end" not in flowchart_links[0].attrib


@pytest.mark.integration
def test_cynefin_runtime_fixed_template_and_confusion_summary_are_explicit() -> None:
    result = serialize_cynefin(
        {
            "domains": [
                {"name": "complex", "items": ["Probe"]},
                {"name": "clear", "items": ["Respond"]},
                {"name": "confusion", "items": ["One", "Two", "Three", "Four", "Five"]},
            ],
            "transitions": [{"source": "complex", "target": "clear", "label": "stabilize"}],
        }
    )

    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
        assert outcome.runtime.render_valid, outcome.runtime.error
        root = ET.fromstring(outcome.runtime.svg)
        visible_texts = {
            "".join(element.itertext()).strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "text" and "".join(element.itertext()).strip()
        }
        assert visible_texts == {
            "Complex",
            "Complicated",
            "Chaotic",
            "Clear",
            "Confusion",
            "Probe → Sense → Respond",
            "Emergent Practices",
            "Sense → Analyse → Respond",
            "Good Practices",
            "Act → Sense → Respond",
            "Novel Practices",
            "Sense → Categorise → Respond",
            "Best Practices",
            "Disorder",
            "Probe",
            "Respond",
            "One",
            "Two",
            "Three",
            "+2 more",
            "stabilize",
        }
        assert "Four" not in visible_texts
        assert "Five" not in visible_texts
    finally:
        runtime.close()


@pytest.mark.integration
def test_cynefin_flowchart_fallback_renders_all_explicit_content_without_native_template() -> None:
    result = serialize_cynefin(
        {
            "title": "Explicit Cynefin",
            "description": "Only explicitly observed content.",
            "domains": [
                {"name": "complex", "items": ["Emergent"]},
                {"name": "complicated", "items": ["Expert"]},
                {"name": "chaotic", "items": ["Crisis"]},
                {"name": "clear", "items": ["Known"]},
                {
                    "name": "confusion",
                    "items": ["One", "Two", "Three", "Four", "Five"],
                },
            ],
            "transitions": [{"source": "complex", "target": "clear", "label": "stabilize"}],
        },
        native_runtime_valid=False,
    )

    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
        assert outcome.runtime.syntax_valid, outcome.runtime.error
        assert outcome.runtime.render_valid, outcome.runtime.error
        assert outcome.runtime.diagram_type.casefold().startswith("flowchart")
        root = ET.fromstring(outcome.runtime.svg or "")
        visible_texts = Counter(
            "".join(element.itertext()).strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] in {"span", "text"}
            and "".join(element.itertext()).strip()
        )
        expected_visible = {
            "Complex",
            "Complicated",
            "Chaotic",
            "Clear",
            "Confusion",
            "Emergent",
            "Expert",
            "Crisis",
            "Known",
            "One",
            "Two",
            "Three",
            "Four",
            "Five",
            "stabilize",
        }
        assert set(visible_texts) == expected_visible
        assert all(visible_texts[label] == 1 for label in expected_visible)
        assert "+2 more" not in visible_texts
        for _element_id, role, label in CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS:
            if role == "runtime_template":
                assert label not in visible_texts
        links = [
            element
            for element in root.iter()
            if "flowchart-link" in element.attrib.get("class", "").split()
        ]
        assert len(links) == 1
        assert "marker-start" not in links[0].attrib
        assert "marker-end" in links[0].attrib
        title = next(
            "".join(element.itertext())
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "title"
        )
        description = next(
            "".join(element.itertext())
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "desc"
        )
        assert title == "Explicit Cynefin"
        assert description == "Only explicitly observed content."
    finally:
        runtime.close()


@pytest.mark.integration
def test_wardley_and_cynefin_preserve_compatibility_and_punctuation_in_mermaid_11_16_svg():
    cases = [
        (
            "wardley",
            serialize_wardley(
                {
                    "title": 'Map &#35; "Q" \\ path: punctuation',
                    "description": 'Wardley &#x23; "Q" \\ description: punctuation',
                    "components": [
                        {
                            "id": "user",
                            "label": 'User &#35; "Q" \\ path: punctuation',
                            "x": 0.9,
                            "y": 0.6,
                            "anchor": True,
                        },
                        {"id": "api", "label": "API", "x": 0.7, "y": 0.4},
                    ],
                    "links": [
                        {
                            "source": "user",
                            "target": "api",
                            "label": 'calls &#x23; "Q" \\ path: punctuation',
                        }
                    ],
                }
            ),
            'Map ＆＃35; "Q" \\ path: punctuation',
            'Wardley ＆＃x23; "Q" \\ description: punctuation',
            {
                'User ＆＃35; "Q" \\ path: punctuation',
                'calls ＆＃x23; "Q" \\ path: punctuation',
            },
        ),
        (
            "cynefin",
            serialize_cynefin(
                {
                    "title": 'Frame &#35; "Q" \\ path: punctuation',
                    "description": 'Cynefin &#x23; "Q" \\ description: punctuation',
                    "domains": [
                        {
                            "name": "complex",
                            "items": ['Emergent &#35; "Q" \\ path: punctuation'],
                        },
                        {"name": "clear", "items": ["Known"]},
                    ],
                    "transitions": [
                        {
                            "source": "complex",
                            "target": "clear",
                            "label": 'stabilize &#x23; "Q" \\ path: punctuation',
                        }
                    ],
                }
            ),
            'Frame ＆＃35; "Q" \\ path: punctuation',
            'Cynefin ＆＃x23; "Q" \\ description: punctuation',
            {
                'Emergent ＆＃35; "Q" \\ path: punctuation',
                'stabilize ＆＃x23; "Q" \\ path: punctuation',
            },
        ),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for expected_type, result, expected_title, expected_description, expected_texts in cases:
            assert len(result.warnings) == 1
            assert "Entity-like literal text" in result.warnings[0]
            outcome = validator.validate(result.code, 20)
            assert outcome.runtime.syntax_valid, (result.code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (
                result.code,
                outcome.runtime.error,
                outcome.warnings,
            )
            assert outcome.runtime.diagram_type.casefold() == expected_type
            root = ET.fromstring(outcome.runtime.svg)
            title = next(
                "".join(element.itertext())
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "title"
            )
            description = next(
                "".join(element.itertext())
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "desc"
            )
            visible_texts = {
                "".join(element.itertext()).strip()
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] in {"text", "tspan"}
                and "".join(element.itertext()).strip()
            }
            assert title == expected_title
            assert description == expected_description
            assert expected_texts <= visible_texts
    finally:
        runtime.close()
