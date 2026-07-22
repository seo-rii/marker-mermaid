from __future__ import annotations

import unicodedata
from copy import deepcopy
from dataclasses import FrozenInstanceError
from xml.etree import ElementTree

import pytest

import marker_mermaid.serializers_special as special_serializers
from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_special import (
    MAX_SPECIAL_OUTPUT_CHARS,
    SPECIAL_TYPES,
    EventModelingPlan,
    PacketFieldPlan,
    PacketPlan,
    SpecialHierarchyNodePlan,
    plan_eventmodeling_records,
    plan_ishikawa_hierarchy,
    plan_packet_fields,
    plan_treeview_hierarchy,
    serialize_special,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

PACKET_IR = {
    "title": "IPv4 prefix",
    "description": "Observed packet header fields.",
    "fields": [
        {"id": "version", "start": 0, "end": 3, "label": "Version"},
        {"id": "ihl", "start": 4, "end": 7, "label": "IHL"},
        {"id": "dscp", "start": 8, "end": 13, "label": "DSCP"},
    ],
}

ISHIKAWA_IR = {
    "title": "Late delivery",
    "description": "Observed causes of late delivery.",
    "effect": {"id": "late", "label": "Late delivery"},
    "categories": [
        {
            "id": "people",
            "label": "People",
            "children": [{"id": "training", "label": "Limited training"}],
        },
        {
            "id": "process",
            "label": "Process",
            "children": [{"id": "handoff", "label": "Manual handoff"}],
        },
    ],
}

TREEVIEW_IR = {
    "title": "Repository",
    "description": "Observed repository hierarchy.",
    "root": {
        "id": "root",
        "label": "marker-mermaid",
        "children": [
            {
                "id": "src",
                "label": "src",
                "children": [{"id": "module", "label": "serializers.py"}],
            },
            {"id": "tests", "label": "tests"},
        ],
    },
}

EVENTMODELING_IR = {
    "title": "Checkout",
    "description": "Observed checkout frames grouped by lane.",
    "lanes": [
        {
            "id": "customer",
            "label": "Customer",
            "frames": [
                {
                    "id": "open",
                    "type": "ui",
                    "label": "Open checkout",
                    "time": "T0",
                },
                {"id": "submit", "type": "command", "label": "Submit order"},
            ],
        },
        {
            "id": "orders",
            "label": "Orders",
            "frames": [
                {"id": "placed", "type": "event", "label": "Order placed"},
                {"id": "summary", "type": "readmodel", "label": "Order summary"},
            ],
        },
    ],
    "relations": [
        {"source": "open", "target": "submit", "label": "continues"},
        {"source": "submit", "target": "placed"},
        {"source": "placed", "target": "summary"},
    ],
}


@pytest.mark.parametrize(
    ("diagram_type", "ir", "prefix"),
    [
        ("packet", PACKET_IR, "packet-beta"),
        ("ishikawa", ISHIKAWA_IR, "ishikawa-beta"),
        ("treeview", TREEVIEW_IR, "treeView-beta"),
    ],
)
def test_native_special_serializers_disclose_requested_grammar(
    diagram_type: str, ir: dict[str, object], prefix: str
) -> None:
    result = serialize_special(diagram_type, ir, experimental=True)

    assert result.requested_type == diagram_type
    assert result.emitted_type == diagram_type
    assert result.fallback_chain == (diagram_type,)
    assert not result.used_fallback
    assert result.code.startswith(prefix)
    assert result.stability == "experimental"
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe


def test_public_packet_plan_preserves_records_ranges_and_reserved_word_safe_ids() -> None:
    source = {
        "id": "click",
        "start": 0,
        "end": 3,
        "label": "Version",
        "bbox": [1, 2, 3, 4],
        "evidence_ids": ["ocr-1"],
    }

    plan = plan_packet_fields({"fields": [source]})

    assert isinstance(plan, PacketPlan)
    assert plan.contiguous
    assert len(plan.fields) == 1
    field = plan.fields[0]
    assert isinstance(field, PacketFieldPlan)
    assert field.source_record is source
    assert field.source_id == "click"
    assert field.emitted_id == "packet_field_click"
    assert (field.label, field.start, field.end) == ("Version", 0, 3)
    with pytest.raises(FrozenInstanceError):
        field.label = "mutated"


def test_public_hierarchy_plans_preserve_parentage_and_reserved_word_safe_ids() -> None:
    effect = {"id": "end", "label": "Late", "evidence_ids": ["ocr-effect"]}
    category = {
        "id": "class",
        "label": "People",
        "bbox": [1, 2, 3, 4],
        "children": [{"id": "style", "label": "Training"}],
    }
    ishikawa = plan_ishikawa_hierarchy({"effect": effect, "categories": [category]})
    tree_root = {"id": "end", "label": "Root", "children": [category]}
    treeview = plan_treeview_hierarchy({"root": tree_root})

    assert all(isinstance(node, SpecialHierarchyNodePlan) for node in ishikawa)
    assert ishikawa[0].source_record is effect
    assert ishikawa[0].emitted_id == "ishikawa_node_end"
    assert ishikawa[1].source_record is category
    assert ishikawa[1].emitted_id == "ishikawa_node_class"
    assert ishikawa[1].parent_source_id == "end"
    assert ishikawa[1].parent_emitted_id == "ishikawa_node_end"
    assert ishikawa[2].depth == 2
    assert treeview[0].source_record is tree_root
    assert treeview[0].emitted_id == "treeview_node_end"
    assert treeview[1].emitted_id == "treeview_node_class"
    assert treeview[1].parent_emitted_id == "treeview_node_end"
    with pytest.raises(FrozenInstanceError):
        treeview[0].depth = 4


def test_eventmodeling_plan_preserves_records_and_exact_fallback_projection() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    lane = ir["lanes"][0]
    frame = lane["frames"][0]
    relation = ir["relations"][0]
    lane["unused"] = {"label": "must not leak"}
    lane["name"] = "not an Event Modeling label alias"
    frame["label"] = 'Open &#35; "quoted" \\ path'
    frame["text"] = "not an Event Modeling label alias"
    relation["label"] = "continue | retry; later"
    relation["style"] = "dashed"
    snapshot = repr(ir)

    plan = plan_eventmodeling_records(ir)

    assert isinstance(plan, EventModelingPlan)
    assert plan.lanes[0].source_record is lane
    assert plan.lanes[0].emitted_id == "eventmodeling_lane_customer"
    assert plan.frames[0].source_record is frame
    assert plan.frames[0].emitted_id == "eventmodeling_frame_open"
    assert plan.frames[0].label == "Open ＆＃35; ″quoted″ ∖ path"
    assert plan.frames[0].semantic_label == 'Open &#35; "quoted" \\ path'
    assert plan.frames[0].rendered_label == "T0 — [ui] Open ＆＃35; ″quoted″ ∖ path"
    assert plan.relations[0].source_record is relation
    assert plan.relations[0].emitted_id == "eventmodeling_relation_1"
    assert plan.relations[0].source_emitted_id == "eventmodeling_frame_open"
    assert plan.relations[0].target_emitted_id == "eventmodeling_frame_submit"
    assert plan.relations[0].label == "continue ∣ retry⁏ later"
    assert plan.compatibility_substituted
    assert "not an Event Modeling label alias" not in serialize_special("eventmodeling", ir).code
    assert repr(ir) == snapshot
    with pytest.raises(FrozenInstanceError):
        plan.frames[0].label = "mutated"


def test_special_plans_resolve_label_name_alias_without_hiding_conflicts() -> None:
    packet = plan_packet_fields(
        {
            "fields": [
                {"id": "a", "start": 0, "end": 0, "name": "Flag"},
                {
                    "id": "b",
                    "start": 1,
                    "end": 1,
                    "label": " Kind ",
                    "name": "Kind",
                },
            ]
        }
    )
    tree = plan_treeview_hierarchy(
        {
            "root": {
                "name": "Root",
                "children": [{"label": "Child", "name": " Child "}],
            }
        }
    )

    assert [field.label for field in packet.fields] == ["Flag", "Kind"]
    assert [node.label for node in tree] == ["Root", "Child"]
    with pytest.raises(SerializationError, match="label and name aliases must agree"):
        plan_packet_fields(
            {
                "fields": [
                    {
                        "start": 0,
                        "end": 0,
                        "label": "Observed",
                        "name": "Invented",
                    }
                ]
            }
        )
    with pytest.raises(SerializationError, match="label and name aliases must agree"):
        plan_treeview_hierarchy(
            {
                "root": {
                    "label": "Root",
                    "name": "Other",
                    "children": [{"label": "Child"}],
                }
            }
        )


def test_packet_keeps_only_explicit_bit_ranges() -> None:
    result = serialize_special("packet", PACKET_IR)

    assert '0-3: "Version"' in result.code
    assert '4-7: "IHL"' in result.code
    assert '8-13: "DSCP"' in result.code
    assert "14" not in result.code


def test_packet_rejects_missing_invalid_or_overlapping_ranges() -> None:
    with pytest.raises(SerializationError, match="explicit start and end"):
        serialize_special("packet", {"fields": [{"id": "a", "start": 0, "label": "A"}]})
    with pytest.raises(SerializationError, match="non-negative integer"):
        serialize_special("packet", {"fields": [{"id": "a", "start": "0", "end": 3, "label": "A"}]})
    with pytest.raises(SerializationError, match="overlaps or is out of order"):
        serialize_special(
            "packet",
            {
                "fields": [
                    {"id": "a", "start": 0, "end": 3, "label": "A"},
                    {"id": "b", "start": 3, "end": 5, "label": "B"},
                ]
            },
        )
    with pytest.raises(SerializationError, match="bit-range limit"):
        serialize_special(
            "packet", {"fields": [{"id": "a", "start": 0, "end": 1_000_000, "label": "A"}]}
        )


def test_packet_bit_and_record_limits_have_explicit_boundaries() -> None:
    plan = plan_packet_fields(
        {"fields": [{"id": "all", "start": 0, "end": 4_095, "label": "All bits"}]}
    )

    assert plan.fields[0].end == 4_095
    with pytest.raises(SerializationError, match="fields exceed deterministic resource limits"):
        plan_packet_fields(
            {
                "fields": [
                    {"id": f"field_{index}", "start": 0, "end": 0, "label": "X"}
                    for index in range(4_097)
                ]
            }
        )


@pytest.mark.parametrize(
    ("planner", "ir"),
    [
        (
            plan_packet_fields,
            {
                "fields": [
                    {"id": "a-b", "start": 0, "end": 0, "label": "A"},
                    {"id": "a_b", "start": 1, "end": 1, "label": "B"},
                ]
            },
        ),
        (
            plan_ishikawa_hierarchy,
            {
                "effect": {"id": "root", "label": "Effect"},
                "categories": [
                    {"id": "a-b", "label": "A"},
                    {"id": "a_b", "label": "B"},
                ],
            },
        ),
        (
            plan_treeview_hierarchy,
            {
                "root": {
                    "id": "root",
                    "label": "Root",
                    "children": [
                        {"id": "a-b", "label": "A"},
                        {"id": "a_b", "label": "B"},
                    ],
                }
            },
        ),
    ],
)
def test_special_plans_reject_ids_that_collide_after_normalization(planner, ir) -> None:
    with pytest.raises(SerializationError, match="ambiguous after Mermaid normalization"):
        planner(ir)


@pytest.mark.parametrize(
    ("planner", "ir"),
    [
        (
            plan_packet_fields,
            {
                "fields": [
                    {"id": "a" * 256, "start": 0, "end": 0, "label": "A"},
                ]
            },
        ),
        (
            plan_ishikawa_hierarchy,
            {
                "effect": {"id": "a" * 256, "label": "Effect"},
                "categories": [{"id": "cause", "label": "Cause"}],
            },
        ),
        (
            plan_treeview_hierarchy,
            {
                "root": {
                    "id": "a" * 256,
                    "label": "Root",
                    "children": [{"id": "child", "label": "Child"}],
                }
            },
        ),
    ],
)
def test_special_plans_reject_ids_that_overflow_the_prefixed_identifier(planner, ir) -> None:
    with pytest.raises(SerializationError, match="emitted identifier limit"):
        planner(ir)


def test_non_contiguous_packet_uses_loss_disclosed_fallback_without_filling_gap() -> None:
    result = serialize_special(
        "packet",
        {
            "fields": [
                {"id": "a", "start": 0, "end": 3, "label": "A"},
                {"id": "b", "start": 8, "end": 9, "label": "B"},
            ]
        },
    )

    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("packet", "flowchart")
    assert any("contiguous" in warning for warning in result.warnings)
    assert "0-3: A" in result.code
    assert "8-9: B" in result.code
    assert "4-7" not in result.code
    assert "packet_field_a" in result.code
    assert "packet_field_b" in result.code
    assert "-->" not in result.code
    assert any("without inferred relationships" in warning for warning in result.warnings)


def test_native_runtime_rejection_selects_portable_fallback() -> None:
    for diagram_type, ir in (
        ("packet", PACKET_IR),
        ("ishikawa", ISHIKAWA_IR),
        ("treeview", TREEVIEW_IR),
    ):
        result = serialize_special(diagram_type, ir, native_runtime_valid=False)

        assert result.used_fallback
        assert result.emitted_type == "flowchart"
        assert any("CandidateValidator rejected" in warning for warning in result.warnings)
        assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe


def test_ishikawa_preserves_effect_categories_and_nested_causes() -> None:
    result = serialize_special("ishikawa", ISHIKAWA_IR)

    assert result.code.splitlines() == [
        "ishikawa-beta",
        "Late delivery",
        "  People",
        "    Limited training",
        "  Process",
        "    Manual handoff",
    ]
    assert any("accessibility" in warning for warning in result.warnings)


def test_treeview_is_deterministic_and_does_not_mutate_ir() -> None:
    original = deepcopy(TREEVIEW_IR)

    first = serialize_special("treeview", TREEVIEW_IR, experimental=True)
    second = serialize_special("treeview", TREEVIEW_IR, experimental=True)

    assert first == second
    assert original == TREEVIEW_IR
    assert '  "src"' in first.code
    assert '    "serializers.py"' in first.code
    assert "experimental and requires review" in first.code


def test_hierarchies_validate_ids_cycles_and_structure() -> None:
    with pytest.raises(SerializationError, match="duplicate treeview node id"):
        serialize_special(
            "treeview",
            {
                "root": {
                    "id": "same",
                    "label": "Root",
                    "children": [{"id": "same", "label": "Child"}],
                }
            },
        )
    cyclic = {"id": "root", "label": "Root", "children": []}
    cyclic["children"].append(cyclic)
    with pytest.raises(SerializationError, match="contains a cycle"):
        serialize_special("treeview", {"root": cyclic})
    with pytest.raises(SerializationError, match="hierarchy below the root"):
        serialize_special("treeview", {"root": {"id": "root", "label": "Root"}})


def test_hierarchy_plans_reject_irrelevant_effect_children_and_reused_objects() -> None:
    with pytest.raises(SerializationError, match=r"effect\.children"):
        plan_ishikawa_hierarchy(
            {
                "effect": {"id": "effect", "label": "Effect", "children": []},
                "categories": [{"id": "cause", "label": "Cause"}],
            }
        )

    reused = {"id": "same-object", "label": "Shared"}
    with pytest.raises(SerializationError, match="reuses a node object"):
        plan_treeview_hierarchy(
            {
                "root": {
                    "id": "root",
                    "label": "Root",
                    "children": [reused, reused],
                }
            }
        )

    effect = {"id": "effect", "label": "Effect"}
    category = {"id": "cause", "label": "Cause", "children": [effect]}
    with pytest.raises(SerializationError, match="contains a cycle"):
        plan_ishikawa_hierarchy({"effect": effect, "categories": [category]})


def test_hierarchy_node_and_depth_limits_have_explicit_boundaries() -> None:
    root = {"id": "node_0", "label": "0", "children": []}
    cursor = root
    for depth in range(1, 65):
        child = {"id": f"node_{depth}", "label": str(depth), "children": []}
        cursor["children"].append(child)
        cursor = child
    assert len(plan_treeview_hierarchy({"root": root})) == 65
    cursor["children"].append({"id": "node_65", "label": "65"})
    with pytest.raises(SerializationError, match="resource limits"):
        plan_treeview_hierarchy({"root": root})

    accepted = {
        "root": {
            "id": "root",
            "label": "Root",
            "children": [{"id": f"child_{index}", "label": "Child"} for index in range(1_999)],
        }
    }
    assert len(plan_treeview_hierarchy(accepted)) == 2_000
    accepted["root"]["children"].append({"id": "overflow", "label": "Overflow"})
    with pytest.raises(SerializationError, match="resource limits"):
        plan_treeview_hierarchy(accepted)


def test_hierarchy_portable_fallback_stops_before_mermaid_edge_limit() -> None:
    accepted = {
        "root": {
            "id": "root",
            "label": "Root",
            "children": [{"id": f"child_{index}", "label": "Child"} for index in range(500)],
        }
    }
    assert (
        serialize_special("treeview", accepted, native_runtime_valid=False).emitted_type
        == "flowchart"
    )
    accepted["root"]["children"].append({"id": "overflow", "label": "Overflow"})
    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_special("treeview", accepted, native_runtime_valid=False)


def test_eventmodeling_uses_lane_aware_loss_disclosed_fallback() -> None:
    result = serialize_special("eventmodeling", EVENTMODELING_IR, experimental=True)

    assert result.requested_type == "eventmodeling"
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("eventmodeling", "flowchart")
    assert 'subgraph eventmodeling_lane_customer["Customer"]' in result.code
    assert 'eventmodeling_frame_open["T0 — [ui] Open checkout"]' in result.code
    assert "eventmodeling_frame_submit --> eventmodeling_frame_placed" in result.code
    assert any("not reliable" in warning for warning in result.warnings)
    assert any("Time/reset-frame notation" in warning for warning in result.warnings)


def test_eventmodeling_edge_label_delimiters_are_encoded() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["relations"][0]["label"] = "continue | retry; later"

    result = serialize_special("eventmodeling", ir)

    assert "|continue ∣ retry⁏ later|" in result.code


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "packet",
            {
                "fields": [
                    {
                        "start": 0,
                        "end": 0,
                        "label": "X" * MAX_SPECIAL_OUTPUT_CHARS,
                    }
                ]
            },
        ),
        (
            "ishikawa",
            {
                "effect": {"label": "Effect"},
                "categories": [{"label": "X" * MAX_SPECIAL_OUTPUT_CHARS}],
            },
        ),
        (
            "treeview",
            {
                "root": {
                    "label": "Root",
                    "children": [{"label": "X" * MAX_SPECIAL_OUTPUT_CHARS}],
                }
            },
        ),
        (
            "eventmodeling",
            {
                "lanes": [
                    {
                        "id": "lane",
                        "frames": [
                            {
                                "id": "frame",
                                "type": "event",
                                "label": "X" * MAX_SPECIAL_OUTPUT_CHARS,
                            }
                        ],
                    }
                ]
            },
        ),
    ],
)
def test_all_special_serializers_apply_the_common_source_character_budget(diagram_type, ir) -> None:
    with pytest.raises(SerializationError, match="source-character limit of 50000"):
        serialize_special(diagram_type, ir)


def test_special_serializer_applies_the_common_source_line_budget(monkeypatch) -> None:
    ir = deepcopy(EVENTMODELING_IR)
    monkeypatch.setattr(special_serializers, "MAX_SPECIAL_OUTPUT_LINES", 10)

    with pytest.raises(SerializationError, match="source-line limit of 10"):
        serialize_special("eventmodeling", ir)


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "packet",
            {"fields": [{"start": 0, "end": 0, "label": "A &#34; &amp; B"}]},
        ),
        (
            "ishikawa",
            {
                "effect": {"label": "Effect"},
                "categories": [{"label": "A &#34; &amp; B"}],
            },
        ),
        (
            "treeview",
            {
                "root": {
                    "label": "Root",
                    "children": [{"label": "A &#34; &amp; B"}],
                }
            },
        ),
    ],
)
def test_special_entity_literals_use_disclosed_visible_compatibility_glyphs(
    diagram_type, ir
) -> None:
    result = serialize_special(diagram_type, ir)

    assert "A ＆＃34; ＆amp; B" in result.code
    assert "A &#34; &amp; B" not in result.code
    assert any("Entity-like literal text" in warning for warning in result.warnings)
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code)
    assert report.safe, report.findings
    normalized_report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(
        unicodedata.normalize("NFKC", result.code)
    )
    assert normalized_report.safe, normalized_report.findings


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "packet",
            {"fields": [{"start": 0, "end": 0, "label": "Step #65; #quot; done"}]},
        ),
        (
            "ishikawa",
            {
                "effect": {"label": "Effect"},
                "categories": [{"label": "Step #65; #quot; done"}],
            },
        ),
        (
            "treeview",
            {
                "root": {
                    "label": "Root",
                    "children": [{"label": "Step #65; #quot; done"}],
                }
            },
        ),
    ],
)
def test_special_bare_entity_literals_remain_visible_text(diagram_type, ir) -> None:
    result = serialize_special(diagram_type, ir)

    assert "Step ＃65; ＃quot; done" in result.code
    assert "Step #65; #quot; done" not in result.code
    assert any("Entity-like literal text" in warning for warning in result.warnings)


def test_eventmodeling_entity_and_edge_delimiters_use_visible_compatibility_glyphs() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["lanes"][0]["label"] = "Customer &#35;"
    ir["lanes"][0]["frames"][0]["label"] = "Open &amp; ready"
    ir["relations"][0]["label"] = "continue | retry; later"

    result = serialize_special("eventmodeling", ir)

    assert "Customer ＆＃35;" in result.code
    assert "Open ＆amp; ready" in result.code
    assert "continue ∣ retry⁏ later" in result.code
    assert "&#35;" not in result.code
    assert any("compatibility glyphs" in warning for warning in result.warnings)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert (
        MermaidSecurityScanner(SecurityProfile.STRICT)
        .scan(unicodedata.normalize("NFKC", result.code))
        .safe
    )


@pytest.mark.integration
def test_eventmodeling_neutralized_tokens_render_as_visible_labels() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["lanes"][0]["frames"][0]["time"] = "https://clock"
    ir["lanes"][0]["frames"][0]["label"] = (
        "style #checkout &amp; &quot; &lt;script&gt; &NewLine; ready"
    )
    ir["relations"][0]["label"] = "continue | retry; later"
    code = serialize_special("eventmodeling", ir).code
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    visible_text = " ".join(ElementTree.fromstring(outcome.runtime.svg or "").itertext())
    visible_text = " ".join(visible_text.replace("\u200b", "").split())
    assert (
        "https://clock — [ui] style #checkout ＆amp; ＆quot; ＆lt;script＆gt; "
        "＆NewLine; ready" in visible_text
    )
    assert "continue ∣ retry⁏ later" in visible_text
    assert not any(entity in visible_text for entity in ("&#8203;", "&#35;", "&#58;", "&#124;"))


@pytest.mark.integration
def test_native_packet_and_treeview_preserve_safe_visible_and_accessible_text() -> None:
    packet = deepcopy(PACKET_IR)
    packet["title"] = 'Packet & title; "Q"'
    packet["description"] = "See https://docs.invalid <guide>"
    packet["fields"][0]["label"] = 'A & B; "Q" <x> #tag'
    treeview = deepcopy(TREEVIEW_IR)
    treeview["title"] = 'Tree & title; "Q"'
    treeview["description"] = "See https://docs.invalid <guide>"
    treeview["root"]["children"][0]["label"] = "A & B; <x> #tag"
    cases = [
        (serialize_special("packet", packet), 'A & B; "Q" <x> #tag'),
        (serialize_special("treeview", treeview), "A & B; <x> #tag"),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcomes = [(result, validator.validate(result.code, 20), label) for result, label in cases]
    finally:
        runtime.close()

    for result, outcome, label in outcomes:
        assert result.emitted_type == result.requested_type
        assert outcome.runtime.syntax_valid, outcome.runtime.error
        assert outcome.runtime.render_valid, outcome.runtime.error
        root = ElementTree.fromstring(outcome.runtime.svg or "")
        visible_text = " ".join(" ".join(root.itertext()).replace("\u200b", "").split())
        assert label in visible_text
        assert 'title; "Q"' in visible_text
        assert "See https://docs.invalid <guide>" not in visible_text
        assert "original accessibility text remains in review metadata" in visible_text
        assert any("generic SVG description" in warning for warning in result.warnings)


@pytest.mark.integration
def test_special_entity_compatibility_glyphs_remain_visible_in_mermaid_11_16() -> None:
    cases = [
        (
            serialize_special(
                "packet",
                {"fields": [{"start": 0, "end": 0, "label": "A &#34; &amp; B"}]},
            ),
            "A ＆＃34; ＆amp; B",
            "A &#34; &amp; B",
        ),
        (
            serialize_special(
                "ishikawa",
                {
                    "effect": {"label": "Effect"},
                    "categories": [{"label": "A &#34; &amp; B"}],
                },
            ),
            "A ＆＃34; ＆amp; B",
            "A &#34; &amp; B",
        ),
        (
            serialize_special(
                "treeview",
                {
                    "root": {
                        "label": "Root",
                        "children": [{"label": "A &#34; &amp; B"}],
                    }
                },
            ),
            "A ＆＃34; ＆amp; B",
            "A &#34; &amp; B",
        ),
        (
            serialize_special(
                "packet",
                {"fields": [{"start": 0, "end": 0, "label": "Step #65; #quot; done"}]},
            ),
            "Step ＃65; ＃quot; done",
            "Step #65; #quot; done",
        ),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcomes = [
            (result, validator.validate(result.code, 20), expected, forbidden)
            for result, expected, forbidden in cases
        ]
    finally:
        runtime.close()

    for result, outcome, expected, forbidden in outcomes:
        assert outcome.runtime.syntax_valid, (result.code, outcome.runtime.error)
        assert outcome.runtime.render_valid, (result.code, outcome.runtime.error)
        visible_text = " ".join(
            " ".join(ElementTree.fromstring(outcome.runtime.svg or "").itertext())
            .replace("\u200b", "")
            .split()
        )
        assert expected in visible_text
        assert forbidden not in visible_text


@pytest.mark.integration
def test_special_character_loss_uses_safe_flowchart_fallback_without_raw_ir_leaks() -> None:
    packet = deepcopy(PACKET_IR)
    packet["fields"][0].update(start=1, end=1, label="https://packet.invalid <field>")
    packet["fields"] = packet["fields"][:1]
    treeview = deepcopy(TREEVIEW_IR)
    treeview["root"]["children"][0]["label"] = 'Tree "quoted" \\ path'
    ishikawa = deepcopy(ISHIKAWA_IR)
    ishikawa["effect"]["label"] = "A & B <effect>"
    cases = [
        (serialize_special("packet", packet), "https://packet.invalid <field>"),
        (serialize_special("treeview", treeview), "Tree ″quoted″ ∖ path"),
        (serialize_special("ishikawa", ishikawa), "A & B <effect>"),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcomes = [(result, validator.validate(result.code, 20), label) for result, label in cases]
    finally:
        runtime.close()

    for result, outcome, label in outcomes:
        assert result.emitted_type == "flowchart"
        assert "flowchart" in result.fallback_chain
        assert outcome.runtime.syntax_valid, (result.code, outcome.runtime.error)
        assert outcome.runtime.render_valid, (result.code, outcome.runtime.error)
        visible_text = " ".join(
            " ".join(ElementTree.fromstring(outcome.runtime.svg or "").itertext())
            .replace("\u200b", "")
            .split()
        )
        assert label in visible_text
        assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert any("compatibility glyphs" in warning for warning in cases[1][0].warnings)


@pytest.mark.integration
def test_ishikawa_comment_and_header_labels_remain_visible_native_text() -> None:
    ir = deepcopy(ISHIKAWA_IR)
    ir["effect"]["label"] = "%% hidden effect"
    ir["categories"][0]["label"] = "ishikawa-beta"
    ir["categories"][1]["label"] = "Ishikawa"
    result = serialize_special("ishikawa", ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert result.emitted_type == "ishikawa"
    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    visible_text = " ".join(
        " ".join(ElementTree.fromstring(outcome.runtime.svg or "").itertext())
        .replace("\u200b", "")
        .split()
    )
    assert "%% hidden effect" in visible_text
    assert "ishikawa-beta" in visible_text
    assert "Ishikawa" in visible_text


def test_special_text_rejects_source_control_and_format_characters() -> None:
    for label in (
        "bad\x00label",
        "bad\tlabel",
        "bad\nlabel",
        "bad\u200blabel",
        "bad\u2028label",
        "bad\u2029label",
        "bad\ud800label",
    ):
        ir = deepcopy(EVENTMODELING_IR)
        ir["lanes"][0]["frames"][0]["label"] = label
        with pytest.raises(SerializationError, match="control characters"):
            serialize_special("eventmodeling", ir)


def test_eventmodeling_discloses_flowchart_compatibility_glyph_replacement() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["lanes"][0]["label"] = 'Customer "quoted"'
    ir["lanes"][0]["frames"][0]["time"] = "T\\0"
    ir["relations"][0]["label"] = 'continue "now"'

    result = serialize_special("eventmodeling", ir)

    assert 'subgraph eventmodeling_lane_customer["Customer ″quoted″"]' in result.code
    assert 'eventmodeling_frame_open["T∖0 — [ui] Open checkout"]' in result.code
    assert "continue ″now″" in result.code
    assert any("compatibility glyphs" in warning for warning in result.warnings)


def test_eventmodeling_neutralization_stays_safe_after_nfkc() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["lanes"][0]["frames"][0]["label"] = "style https://docs.invalid #tag"
    ir["relations"][0]["label"] = "continue | retry"
    code = serialize_special("eventmodeling", ir).code

    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(
        unicodedata.normalize("NFKC", code)
    )

    assert report.safe, report.findings
    assert "∣" in code


def test_eventmodeling_rejects_unknown_types_references_and_id_collisions() -> None:
    bad_type = deepcopy(EVENTMODELING_IR)
    bad_type["lanes"][0]["frames"][0]["type"] = "aggregate"
    with pytest.raises(SerializationError, match="unsupported type"):
        serialize_special("eventmodeling", bad_type)

    bad_reference = deepcopy(EVENTMODELING_IR)
    bad_reference["relations"].append({"source": "missing", "target": "placed"})
    with pytest.raises(SerializationError, match="unknown endpoint"):
        serialize_special("eventmodeling", bad_reference)

    collision = {
        "lanes": [
            {
                "id": "customer",
                "frames": [
                    {"id": "frame-a", "type": "ui", "label": "Open"},
                    {"id": "frame_a", "type": "event", "label": "Opened"},
                ],
            }
        ]
    }
    with pytest.raises(SerializationError, match="ambiguous after Mermaid normalization"):
        serialize_special("eventmodeling", collision)


def test_eventmodeling_direct_contract_rejects_coercion_and_noncanonical_references() -> None:
    for field, value, message in (
        ("id", 7, "id must be a string"),
        ("label", 7, "must be a string"),
        ("type", 7, "type must be a string"),
        ("type", " event ", "noncanonical type"),
    ):
        ir = deepcopy(EVENTMODELING_IR)
        ir["lanes"][0]["frames"][0][field] = value
        with pytest.raises(SerializationError, match=message):
            serialize_special("eventmodeling", ir)

    for endpoint in (" open ", "open\u200b", 7):
        ir = deepcopy(EVENTMODELING_IR)
        ir["relations"][0]["source"] = endpoint
        with pytest.raises(SerializationError, match="id must match|endpoints must be strings"):
            serialize_special("eventmodeling", ir)

    missing_type = deepcopy(EVENTMODELING_IR)
    missing_type["lanes"][0]["frames"][0]["type"] = ""
    assert plan_eventmodeling_records(missing_type).frames[0].frame_type == "unknown"


def test_eventmodeling_preserves_parallel_relations_with_distinct_provenance_slots() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["relations"] = [
        {"source": "open", "target": "submit", "label": "continues"},
        {"source": "open", "target": "submit", "label": "continues"},
    ]

    plan = plan_eventmodeling_records(ir)
    result = serialize_special("eventmodeling", ir)

    assert [relation.emitted_id for relation in plan.relations] == [
        "eventmodeling_relation_1",
        "eventmodeling_relation_2",
    ]
    assert (
        result.code.count("eventmodeling_frame_open -->|continues| eventmodeling_frame_submit") == 2
    )


def test_eventmodeling_edge_budget_fails_before_unbounded_fallback_output() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["relations"] = [{"source": "open", "target": "submit"} for _index in range(501)]

    with pytest.raises(SerializationError, match="Mermaid edge limit of 500"):
        serialize_special("eventmodeling", ir)


def test_labels_are_neutralized_before_strict_security_scanning() -> None:
    unsafe = deepcopy(TREEVIEW_IR)
    unsafe["title"] = "%%{init}; <script>"
    unsafe["root"]["children"][0]["label"] = (
        "https://example.invalid @import logos:evil iconify callback(x)"
    )

    result = serialize_special("treeview", unsafe)
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code)

    assert report.safe, report.findings
    assert "https://" not in result.code
    assert "@import" not in result.code
    assert "logos:" not in result.code
    assert "iconify" not in result.code
    assert "callback(" not in result.code
    assert "<script>" not in result.code


def test_ishikawa_label_lines_cannot_become_active_mermaid_statements() -> None:
    unsafe = deepcopy(ISHIKAWA_IR)
    unsafe["effect"]["label"] = "click target"
    unsafe["categories"][0]["label"] = "style node"
    unsafe["categories"][0]["children"][0]["label"] = "config: ---"

    result = serialize_special("ishikawa", unsafe)

    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code)
    assert report.safe, report.findings


def test_public_special_type_set_and_unknown_dispatch_are_deterministic() -> None:
    assert SPECIAL_TYPES == ("packet", "ishikawa", "treeview", "eventmodeling")
    with pytest.raises(SerializationError, match="no special serializer"):
        serialize_special("wardley", {})


@pytest.mark.integration
def test_native_and_fallback_outputs_render_with_strict_mermaid_11_16() -> None:
    fallback_boundary = {
        "root": {
            "id": "root",
            "label": "Root",
            "children": [{"id": f"child_{index}", "label": "Child"} for index in range(500)],
        }
    }
    reserved_packet = {"fields": [{"id": "click", "start": 0, "end": 3, "label": "Flags"}]}
    reserved_tree = {
        "root": {
            "id": "end",
            "label": "Root",
            "children": [{"id": "class", "label": "Child"}],
        }
    }
    cases = [
        (serialize_special("packet", PACKET_IR, experimental=True).code, "packet"),
        (serialize_special("ishikawa", ISHIKAWA_IR, experimental=True).code, "ishikawa"),
        (serialize_special("treeview", TREEVIEW_IR, experimental=True).code, "treeView"),
        (
            serialize_special("packet", PACKET_IR, native_runtime_valid=False).code,
            "flowchart-v2",
        ),
        (
            serialize_special("ishikawa", ISHIKAWA_IR, native_runtime_valid=False).code,
            "flowchart-v2",
        ),
        (
            serialize_special("treeview", TREEVIEW_IR, native_runtime_valid=False).code,
            "flowchart-v2",
        ),
        (
            serialize_special("treeview", fallback_boundary, native_runtime_valid=False).code,
            "flowchart-v2",
        ),
        (
            serialize_special("packet", reserved_packet, native_runtime_valid=False).code,
            "flowchart-v2",
        ),
        (
            serialize_special("treeview", reserved_tree, native_runtime_valid=False).code,
            "flowchart-v2",
        ),
        (
            serialize_special("eventmodeling", EVENTMODELING_IR, experimental=True).code,
            "flowchart-v2",
        ),
    ]
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for code, expected_runtime_type in cases:
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (code, outcome.runtime.error, outcome.warnings)
            assert outcome.runtime.diagram_type == expected_runtime_type
    finally:
        runtime.close()
