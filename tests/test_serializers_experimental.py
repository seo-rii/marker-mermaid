from __future__ import annotations

import unicodedata
from dataclasses import FrozenInstanceError
from xml.etree import ElementTree as ET

import pytest

import marker_mermaid.serializers_experimental as experimental_serializers
from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_experimental import (
    plan_cynefin_records,
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
    assert plan.components[0].label == "API ＆＃35; edge"
    assert plan.components[0].semantic_label == "API &#35; edge"
    assert plan.components[0].kind == "component"
    assert (plan.components[0].x, plan.components[0].y) == (1.0, 0.25)
    assert (plan.components[0].x_token, plan.components[0].y_token) == ("1.0", "0.25")
    assert plan.components[0].token == '"API ＆＃35; edge"'
    assert plan.links[0].source_record is link
    assert plan.links[0].source_id == "api"
    assert plan.links[0].target_id == "db"
    assert plan.links[0].source_token == '"API ＆＃35; edge"'
    assert plan.links[0].target_token == '"DB"'
    assert plan.links[0].label == "writes ＆＃x23; data"
    assert plan.links[0].semantic_label == "writes &#x23; data"
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
