from __future__ import annotations

import unicodedata
from copy import deepcopy
from xml.etree import ElementTree

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_special import SPECIAL_TYPES, serialize_special
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


def test_eventmodeling_uses_lane_aware_loss_disclosed_fallback() -> None:
    result = serialize_special("eventmodeling", EVENTMODELING_IR, experimental=True)

    assert result.requested_type == "eventmodeling"
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("eventmodeling", "flowchart")
    assert 'subgraph lane_customer["Customer"]' in result.code
    assert 'open["T0 — [ui] Open checkout"]' in result.code
    assert "submit --> placed" in result.code
    assert any("not reliable" in warning for warning in result.warnings)
    assert any("Time/reset-frame notation" in warning for warning in result.warnings)


def test_eventmodeling_edge_label_delimiters_are_encoded() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["relations"][0]["label"] = "continue | retry"

    result = serialize_special("eventmodeling", ir)

    assert "|continue ∣ retry|" in result.code


@pytest.mark.integration
def test_eventmodeling_neutralized_tokens_render_as_visible_labels() -> None:
    ir = deepcopy(EVENTMODELING_IR)
    ir["lanes"][0]["frames"][0]["time"] = "https://clock"
    ir["lanes"][0]["frames"][0]["label"] = (
        "style #checkout &amp; &quot; &lt;script&gt; &NewLine; ready"
    )
    ir["relations"][0]["label"] = "continue | retry"
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
        "https://clock — [ui] style #checkout &amp; &quot; &lt;script&gt; &NewLine; ready"
        in visible_text
    )
    assert "continue ∣ retry" in visible_text
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

    assert 'subgraph lane_customer["Customer ″quoted″"]' in result.code
    assert 'open["T∖0 — [ui] Open checkout"]' in result.code
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
                "frames": [{"id": "lane_customer", "type": "ui", "label": "Open"}],
            }
        ]
    }
    with pytest.raises(SerializationError, match="collides after Mermaid normalization"):
        serialize_special("eventmodeling", collision)


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
    cases = [
        serialize_special("packet", PACKET_IR, experimental=True).code,
        serialize_special("ishikawa", ISHIKAWA_IR, experimental=True).code,
        serialize_special("treeview", TREEVIEW_IR, experimental=True).code,
        serialize_special("packet", PACKET_IR, native_runtime_valid=False).code,
        serialize_special("ishikawa", ISHIKAWA_IR, native_runtime_valid=False).code,
        serialize_special("treeview", TREEVIEW_IR, native_runtime_valid=False).code,
        serialize_special("eventmodeling", EVENTMODELING_IR, experimental=True).code,
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
