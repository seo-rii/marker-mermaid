from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError
from marker_mermaid.serializers_phase2 import (
    BLOCK_ACCESSIBILITY_LIMITATION,
    serialize_c4_native,
    serialize_phase2,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

CASES = {
    "requirement": {
        "title": "Payment requirements",
        "description": "API satisfies the payment requirement.",
        "requirements": [
            {
                "id": "REQ-1",
                "text": 'User can "pay"',
                "type": "functional",
                "risk": "high",
                "verify_method": "test",
            }
        ],
        "elements": [{"id": "API", "type": "system", "docref": "api.md"}],
        "relations": [{"source": "API", "target": "REQ-1", "type": "satisfies"}],
    },
    "block": {
        "title": "Data blocks",
        "description": "API sends data to DB.",
        "columns": 2,
        "blocks": [
            {"id": "api", "label": "API"},
            {"id": "db", "label": "DB", "shape": "cylinder"},
        ],
        "edges": [{"source": "api", "target": "db", "label": "writes"}],
    },
    "c4": {
        "level": "container",
        "title": "Payment system",
        "description": "User calls the API, which stores payment data.",
        "elements": [
            {"id": "user", "kind": "person", "label": "User"},
            {
                "id": "api",
                "kind": "container",
                "label": "API",
                "technology": "Python",
                "boundary": "system",
            },
            {
                "id": "db",
                "kind": "container_database",
                "label": "DB",
                "technology": "Postgres",
                "boundary": "system",
            },
        ],
        "boundaries": [{"id": "system", "type": "system", "label": "Payments"}],
        "relations": [
            {"source": "user", "target": "api", "label": "Uses", "technology": "HTTPS"},
            {"source": "api", "target": "db", "label": "Writes"},
        ],
    },
    "deployment": {
        "title": "Deployment",
        "description": "An application node connects to a database node.",
        "nodes": [
            {"id": "app", "label": "App node", "icon": "server"},
            {"id": "db", "label": "DB node", "icon": "database"},
        ],
        "artifacts": [],
        "links": [{"source": "app", "target": "db", "label": "JDBC"}],
    },
    "component": {
        "title": "Components",
        "description": "Web depends on authentication.",
        "components": [
            {"id": "web", "label": "Web"},
            {"id": "auth", "label": "Auth"},
        ],
        "interfaces": [],
        "dependencies": [{"source": "web", "target": "auth", "label": "OAuth"}],
    },
    "usecase": {
        "title": "Checkout use cases",
        "description": "A shopper checks out.",
        "actors": [{"id": "shopper", "label": "Shopper"}],
        "use_cases": [{"id": "checkout", "label": "Checkout"}],
        "relations": [{"source": "shopper", "target": "checkout", "type": "association"}],
    },
}


@pytest.mark.parametrize(
    ("requested_type", "emitted_type", "prefix"),
    [
        ("requirement", "requirement", "requirementDiagram"),
        ("block", "block", "block-beta"),
        ("c4", "architecture", "architecture-beta"),
        ("deployment", "architecture", "architecture-beta"),
        ("component", "architecture", "architecture-beta"),
        ("usecase", "flowchart", "flowchart LR"),
    ],
)
def test_phase2_serializer_discloses_native_or_fallback_type(
    requested_type: str, emitted_type: str, prefix: str
) -> None:
    result = serialize_phase2(requested_type, CASES[requested_type], experimental=True)

    assert result[0].startswith(prefix)
    assert result[1] == emitted_type
    assert (result[2] is None) == (requested_type in {"requirement", "block"})
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result[0]).safe


def test_requirement_output_is_deterministic_and_escaped() -> None:
    first = serialize_phase2("requirement", CASES["requirement"])[0]
    second = serialize_phase2("requirement", CASES["requirement"])[0]

    assert first == second
    assert "functionalRequirement REQ_1 {" in first
    assert 'text: "User can &quot;pay&quot;"' in first
    assert "API - satisfies -> REQ_1" in first
    assert "accTitle: Payment requirements" in first


def test_duplicate_and_non_ascii_ids_are_stably_normalized() -> None:
    code = serialize_phase2(
        "block",
        {
            "blocks": [
                {"id": "결제", "label": "첫째"},
                {"id": "결제", "label": "둘째"},
            ]
        },
    )[0]

    assert 'B1["첫째"]' in code
    assert 'B2["둘째"]' in code


def test_block_keeps_native_syntax_without_unsupported_accessibility_directives() -> None:
    code, emitted_type, fallback = serialize_phase2("block", CASES["block"])

    assert emitted_type == "block"
    assert fallback is None
    assert "accTitle" not in code
    assert "accDescr" not in code
    assert 'api -- "writes" --> db' in code
    assert "typed IR" in BLOCK_ACCESSIBILITY_LIMITATION


@pytest.mark.parametrize("requested_type", ["c4", "deployment", "component", "usecase"])
def test_portable_fallback_reason_names_lost_notation(requested_type: str) -> None:
    _, _, reason = serialize_phase2(requested_type, CASES[requested_type])

    assert reason is not None
    assert f"fallback from {requested_type}" in reason


def test_unresolved_relations_fail_instead_of_inventing_endpoints() -> None:
    with pytest.raises(SerializationError, match="unknown endpoint"):
        serialize_phase2(
            "usecase",
            {
                "actors": [{"id": "actor"}],
                "use_cases": [{"id": "case"}],
                "relations": [{"source": "actor", "target": "missing"}],
            },
        )


def test_unsupported_requirement_enum_fails() -> None:
    broken = {**CASES["requirement"]}
    broken["requirements"] = [{**CASES["requirement"]["requirements"][0], "risk": "urgent"}]

    with pytest.raises(SerializationError, match="unsupported risk"):
        serialize_phase2("requirement", broken)


def test_overlapping_semantic_ids_fail_instead_of_rerouting_relations() -> None:
    with pytest.raises(SerializationError, match="source ids must be distinct"):
        serialize_phase2(
            "usecase",
            {
                "actors": [{"id": "shared"}],
                "use_cases": [{"id": "shared"}],
                "relations": [],
            },
        )


@pytest.mark.integration
def test_native_c4_source_renders_but_is_not_strict_svg_safe() -> None:
    code, emitted_type, fallback = serialize_c4_native(CASES["c4"], experimental=True)
    runtime = NodeMermaidRuntime()
    try:
        outcome = runtime.validate_and_render(code, 20)
    finally:
        runtime.close()

    assert emitted_type == "c4"
    assert fallback is None
    assert outcome.syntax_valid
    assert outcome.render_valid
    assert 'xlink:href="data:' in (outcome.svg or "")


@pytest.mark.integration
def test_phase2_serializers_parse_and_render_with_mermaid_11_16() -> None:
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        for requested_type, ir in CASES.items():
            code = serialize_phase2(requested_type, ir, experimental=True)[0]
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (requested_type, code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (
                requested_type,
                code,
                outcome.runtime.error,
                outcome.warnings,
            )
    finally:
        runtime.close()
