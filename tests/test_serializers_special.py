from __future__ import annotations

from copy import deepcopy

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

    assert "|continue &#124; retry|" in result.code


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
