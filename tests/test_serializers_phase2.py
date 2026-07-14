from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.models import TypedIRCandidate
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError, serialize_runtime_fallback_result
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


@pytest.mark.parametrize(
    ("source_type", "emitted_type"),
    [
        ("requirement", "requirement"),
        ("functional", "functionalRequirement"),
        ("functional_requirement", "functionalRequirement"),
        ("interface", "interfaceRequirement"),
        ("interface_requirement", "interfaceRequirement"),
        ("performance", "performanceRequirement"),
        ("performance_requirement", "performanceRequirement"),
        ("physical", "physicalRequirement"),
        ("physical_requirement", "physicalRequirement"),
        ("design_constraint", "designConstraint"),
    ],
)
def test_requirement_nested_contract_tracks_every_serializer_type_alias(
    source_type: str,
    emitted_type: str,
) -> None:
    ir = {"requirements": [{"id": "REQ", "text": "Must work", "type": source_type}]}

    assert TypedIRCandidate(diagram_type="requirement", ir=ir).ir == ir
    assert f"{emitted_type} REQ {{" in serialize_phase2("requirement", ir)[0]


@pytest.mark.parametrize("risk", ["low", "medium", "high"])
def test_requirement_nested_contract_tracks_every_serializer_risk(risk: str) -> None:
    ir = {"requirements": [{"id": "REQ", "text": "Must work", "risk": risk}]}

    assert TypedIRCandidate(diagram_type="requirement", ir=ir).ir == ir
    assert f"risk: {risk}" in serialize_phase2("requirement", ir)[0]


@pytest.mark.parametrize("field", ["verify_method", "verifymethod"])
@pytest.mark.parametrize("verify_method", ["analysis", "demonstration", "inspection", "test"])
def test_requirement_nested_contract_tracks_every_serializer_verify_method_and_alias(
    field: str,
    verify_method: str,
) -> None:
    ir = {"requirements": [{"id": "REQ", "text": "Must work", field: verify_method}]}

    assert TypedIRCandidate(diagram_type="requirement", ir=ir).ir == ir
    assert f"verifyMethod: {verify_method}" in serialize_phase2("requirement", ir)[0]


@pytest.mark.parametrize(
    "relation_type",
    ["contains", "copies", "derives", "satisfies", "verifies", "refines", "traces"],
)
def test_requirement_nested_contract_tracks_every_serializer_relation_type(
    relation_type: str,
) -> None:
    ir = {
        "requirements": [
            {"id": "A", "text": "First"},
            {"id": "B", "text": "Second"},
        ],
        "relations": [{"source": "A", "target": "B", "type": relation_type}],
    }

    assert TypedIRCandidate(diagram_type="requirement", ir=ir).ir == ir
    assert f"A - {relation_type} -> B" in serialize_phase2("requirement", ir)[0]


@pytest.mark.parametrize(
    ("shape", "emitted_node"),
    [
        ("rectangle", 'node["Node"]'),
        ("round", 'node("Node")'),
        ("stadium", 'node(["Node"])'),
        ("circle", 'node(("Node"))'),
        ("diamond", 'node{"Node"}'),
        ("hexagon", 'node{{"Node"}}'),
        ("cylinder", 'node[("Node")]'),
        ("subroutine", 'node[["Node"]]'),
    ],
)
def test_block_nested_contract_tracks_every_serializer_shape(
    shape: str,
    emitted_node: str,
) -> None:
    ir = {"blocks": [{"id": "node", "label": "Node", "shape": shape}]}

    assert TypedIRCandidate(diagram_type="block", ir=ir).ir == ir
    assert emitted_node in serialize_phase2("block", ir)[0]


@pytest.mark.parametrize(
    ("columns", "emitted_columns"),
    [("auto", "auto"), (1, "1"), (12, "12"), ("2", "2")],
)
def test_block_nested_contract_preserves_supported_serializer_column_scalars(
    columns: str | int,
    emitted_columns: str,
) -> None:
    ir = {"blocks": [{"id": "node"}], "columns": columns}

    assert TypedIRCandidate(diagram_type="block", ir=ir).ir == ir
    assert f"columns {emitted_columns}" in serialize_phase2("block", ir)[0]


def test_phase_two_nested_contract_case_insensitive_tokens_match_serializers() -> None:
    requirement_ir = {
        "requirements": [
            {
                "id": "A",
                "text": "First",
                "type": "FUNCTIONAL_REQUIREMENT",
                "risk": "HIGH",
                "verifymethod": "TEST",
            },
            {"id": "B", "text": "Second"},
        ],
        "relations": [{"source": "A", "target": "B", "type": "SATISFIES"}],
    }
    block_ir = {"blocks": [{"id": "db", "label": "Database", "shape": "CYLINDER"}]}

    assert TypedIRCandidate(diagram_type="requirement", ir=requirement_ir).ir == requirement_ir
    assert TypedIRCandidate(diagram_type="block", ir=block_ir).ir == block_ir
    requirement_code = serialize_phase2("requirement", requirement_ir)[0]
    block_code = serialize_phase2("block", block_ir)[0]
    assert "functionalRequirement A {" in requirement_code
    assert "risk: high" in requirement_code
    assert "verifyMethod: test" in requirement_code
    assert "A - satisfies -> B" in requirement_code
    assert 'db[("Database")]' in block_code


@pytest.mark.parametrize(
    ("diagram_type", "ir", "message"),
    [
        ("requirement", {"requirements": []}, "non-empty list"),
        ("block", {"blocks": []}, "non-empty list"),
        (
            "requirement",
            {
                "requirements": [{"id": "REQ", "text": "Must work"}],
                "relations": [{"source": "REQ", "target": "missing"}],
            },
            "unknown endpoint",
        ),
        (
            "block",
            {
                "blocks": [{"id": "node"}],
                "edges": [{"source": "node", "target": "missing"}],
            },
            "unknown endpoint",
        ),
        (
            "block",
            {"blocks": [{"id": "node"}], "columns": 0},
            "positive integer",
        ),
    ],
)
def test_phase_two_nested_contract_leaves_semantic_validation_to_serializer(
    diagram_type: str,
    ir: dict[str, object],
    message: str,
) -> None:
    assert TypedIRCandidate(diagram_type=diagram_type, ir=ir).ir == ir

    with pytest.raises(SerializationError, match=message):
        serialize_phase2(diagram_type, ir)


def test_overlapping_semantic_ids_fail_instead_of_rerouting_relations() -> None:
    ir = {
        "actors": [{"id": "shared"}],
        "use_cases": [{"id": "shared"}],
        "relations": [],
    }
    assert TypedIRCandidate(diagram_type="usecase", ir=ir).ir == ir

    with pytest.raises(SerializationError, match="source ids must be distinct"):
        serialize_phase2("usecase", ir)


def test_usecase_final_ids_avoid_second_order_actor_namespace_collisions() -> None:
    code = serialize_phase2(
        "usecase",
        {
            "actors": [{"id": "a-b"}, {"id": "usecase_y"}],
            "use_cases": [{"id": "a b"}, {"id": "y"}],
            "relations": [{"source": "usecase_y", "target": "y", "type": "association"}],
        },
    )[0]

    assert 'a_b(["a-b"])' in code
    assert 'usecase_y(["usecase_y"])' in code
    assert 'usecase_a_b("a b")' in code
    assert 'usecase_y_2("y")' in code
    assert "usecase_y -->|association| usecase_y_2" in code


def test_usecase_fallback_emits_distinct_shapes_and_suppresses_system_boundaries() -> None:
    ir = {
        "actors": [{"id": "shopper", "label": "Shopper"}],
        "use_cases": [{"id": "checkout", "label": "Checkout"}],
        "relations": [{"source": "shopper", "target": "checkout"}],
        "groups": [
            {
                "id": "hidden-system",
                "label": "Hidden system boundary",
                "member_ids": ["checkout"],
            }
        ],
        "system_boundary": "Hidden checkout system",
        "system_boundaries": [{"id": "hidden", "label": "Hidden boundary record"}],
    }

    code, emitted_type, reason = serialize_phase2("usecase", ir)

    assert emitted_type == "flowchart"
    assert reason is not None and "system boundaries" in reason
    assert 'shopper(["Shopper"])' in code
    assert 'checkout("Checkout")' in code
    assert "shopper --> checkout" in code
    assert "subgraph" not in code
    assert "Hidden system boundary" not in code
    assert "Hidden checkout system" not in code
    assert "Hidden boundary record" not in code


@pytest.mark.parametrize(
    ("relation", "message"),
    [
        ("not-an-object", "relations must be objects"),
        ({"source": "actor", "target": "missing"}, "unknown endpoint"),
        ({"source": "missing", "target": "case"}, "unknown endpoint"),
    ],
)
def test_usecase_fallback_rejects_malformed_or_dangling_relations(
    relation: object,
    message: str,
) -> None:
    ir = {
        "actors": [{"id": "actor"}],
        "use_cases": [{"id": "case"}],
        "relations": [relation],
    }
    if isinstance(relation, dict):
        assert TypedIRCandidate(diagram_type="usecase", ir=ir).ir == ir

    with pytest.raises(SerializationError, match=message):
        serialize_phase2("usecase", ir)


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        ({"actors": [], "use_cases": [{"id": "case"}]}, "non-empty list"),
        ({"actors": [{"id": "actor"}], "use_cases": []}, "non-empty list"),
    ],
)
def test_usecase_nested_contract_leaves_nonempty_semantics_to_serializer(
    ir: dict[str, object],
    message: str,
) -> None:
    assert TypedIRCandidate(diagram_type="usecase", ir=ir).ir == ir

    with pytest.raises(SerializationError, match=message):
        serialize_phase2("usecase", ir)


def test_c4_architecture_rejection_preserves_boundary_as_flowchart_subgraph() -> None:
    result = serialize_runtime_fallback_result("c4", CASES["c4"], experimental=True)

    assert result is not None
    assert result.fallback_chain == ("c4", "architecture", "flowchart")
    assert 'subgraph system["Payments"]' in result.code
    assert 'user["User"]' in result.code
    assert 'api["API"]' in result.code
    assert 'db["DB"]' in result.code
    assert "user --> api" in result.code
    assert "api --> db" in result.code


def test_c4_architecture_and_nested_flowchart_share_planned_identity_and_topology() -> None:
    ir = {
        "level": "container",
        "elements": [
            {
                "id": "A-B",
                "label": "API",
                "kind": "container",
                "boundary": "결제 영역",
            },
            {
                "id": "A B",
                "name": "Database",
                "kind": "container_database",
                "boundary": "결제 영역",
            },
            {"id": "same", "label": "First duplicate", "boundary": "결제 영역"},
            {"id": "same", "label": "Second duplicate", "boundary": "결제 영역"},
            {"kind": "person", "boundary": "결제 영역"},
        ],
        "boundaries": [{"id": "결제 영역"}],
        "relations": [
            {"source": "A-B", "target": "A B", "bidirectional": True},
            {"source": "same", "target": "A-B"},
        ],
    }

    architecture_code, architecture_type, _reason = serialize_phase2("c4", ir)
    flowchart_code, flowchart_type, _reason = serialize_phase2("c4", ir, native_runtime_valid=False)

    assert architecture_type == "architecture"
    assert 'group group_1(cloud)["G1"]' in architecture_code
    assert 'service A_B(server)["API"] in group_1' in architecture_code
    assert 'service A_B_2(database)["Database"] in group_1' in architecture_code
    assert 'service same(server)["First duplicate"] in group_1' in architecture_code
    assert 'service same_2(server)["Second duplicate"] in group_1' in architecture_code
    assert 'service S5(internet)["S5"] in group_1' in architecture_code
    assert "A_B:R <--> L:A_B_2" in architecture_code
    assert "same:R --> L:A_B" in architecture_code

    assert flowchart_type == "flowchart"
    assert 'subgraph group_1["G1"]' in flowchart_code
    assert 'A_B["API"]' in flowchart_code
    assert 'A_B_2["Database"]' in flowchart_code
    assert 'same["First duplicate"]' in flowchart_code
    assert 'same_2["Second duplicate"]' in flowchart_code
    assert 'S5["S5"]' in flowchart_code
    assert "A_B <--> A_B_2" in flowchart_code
    assert "same --> A_B" in flowchart_code


@pytest.mark.parametrize(
    ("kind", "icon"),
    [
        ("person", "internet"),
        ("external_person", "internet"),
        ("system", "server"),
        ("external_system", "server"),
        ("database", "database"),
        ("external_database", "database"),
        ("queue", "disk"),
        ("external_queue", "disk"),
        ("container", "server"),
        ("container_database", "database"),
        ("container_queue", "disk"),
        ("component", "server"),
        ("component_database", "database"),
        ("component_queue", "disk"),
    ],
)
def test_c4_nested_contract_tracks_every_serializer_element_kind(kind: str, icon: str) -> None:
    ir = {"elements": [{"id": "node", "label": "Node", "kind": kind}]}

    assert TypedIRCandidate(diagram_type="c4", ir=ir).ir == ir
    code, emitted_type, _reason = serialize_phase2("c4", ir)
    assert emitted_type == "architecture"
    assert f'service node({icon})["Node"]' in code


@pytest.mark.parametrize("level", ["context", "container", "component"])
def test_c4_nested_contract_tracks_every_serializer_level(level: str) -> None:
    ir = {"level": level.upper(), "elements": [{"id": "system"}]}

    assert TypedIRCandidate(diagram_type="c4", ir=ir).ir == ir
    assert serialize_phase2("c4", ir)[1] == "architecture"


@pytest.mark.parametrize("field", ["kind", "type"])
def test_c4_nested_contract_tracks_kind_alias_and_serializer_casefolding(field: str) -> None:
    ir = {"elements": [{"id": "db", "label": "DB", field: "CONTAINER_DATABASE"}]}

    assert TypedIRCandidate(diagram_type="c4", ir=ir).ir == ir
    code = serialize_phase2("c4", ir)[0]
    assert 'service db(database)["DB"]' in code


def test_c4_nested_contract_tracks_exact_architecture_ports_and_direction() -> None:
    ir = {
        "elements": [{"id": "api"}, {"id": "db"}],
        "relations": [
            {
                "source": "api",
                "target": "db",
                "source_side": "T",
                "target_side": "B",
                "bidirectional": True,
            }
        ],
    }

    assert TypedIRCandidate(diagram_type="c4", ir=ir).ir == ir
    code = serialize_phase2("c4", ir)[0]
    assert "api:T <--> B:db" in code


def test_c4_boundary_type_remains_automatic_fallback_compatible_metadata() -> None:
    ir = {
        "elements": [{"id": "api", "boundary": "scope"}],
        "boundaries": [{"id": "scope", "label": "Scope", "type": "vendor_extension"}],
    }

    assert TypedIRCandidate(diagram_type="c4", ir=ir).ir == ir
    code, emitted_type, _reason = serialize_phase2("c4", ir)
    assert emitted_type == "architecture"
    assert 'group scope(cloud)["Scope"]' in code
    with pytest.raises(SerializationError, match="unsupported C4 boundary type"):
        serialize_c4_native(ir)


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        ({"elements": []}, "non-empty list"),
        (
            {
                "elements": [{"id": "api"}],
                "relations": [{"source": "api", "target": "missing"}],
            },
            "unknown endpoint",
        ),
        (
            {
                "elements": [{"id": "api", "boundary": "missing"}],
                "boundaries": [{"id": "known"}],
            },
            "unknown boundary",
        ),
        (
            {
                "elements": [{"id": "A-B", "boundary": "A B"}],
                "boundaries": [{"id": "A B"}],
            },
            "collides with a.*id",
        ),
    ],
)
def test_c4_nested_contract_leaves_semantic_validation_to_serializer(
    ir: dict[str, object],
    message: str,
) -> None:
    assert TypedIRCandidate(diagram_type="c4", ir=ir).ir == ir

    with pytest.raises(SerializationError, match=message):
        serialize_phase2("c4", ir)


def test_c4_nested_contract_preserves_duplicate_identity_and_empty_boundary_semantics() -> None:
    duplicate_ir = {
        "elements": [
            {"id": "same", "label": "First"},
            {"id": "same", "label": "Second"},
        ],
        "relations": [{"source": "same", "target": "same"}],
    }
    empty_boundary_ir = {
        "elements": [{"id": "api"}],
        "boundaries": [{"id": "empty", "label": "Empty"}],
    }

    assert TypedIRCandidate(diagram_type="c4", ir=duplicate_ir).ir == duplicate_ir
    duplicate_code = serialize_phase2("c4", duplicate_ir)[0]
    assert 'service same(server)["First"]' in duplicate_code
    assert 'service same_2(server)["Second"]' in duplicate_code
    assert "same:R --> L:same" in duplicate_code

    assert TypedIRCandidate(diagram_type="c4", ir=empty_boundary_ir).ir == empty_boundary_ir
    architecture_code, emitted_type, _reason = serialize_phase2("c4", empty_boundary_ir)
    assert emitted_type == "architecture"
    assert 'group empty(cloud)["Empty"]' in architecture_code
    with pytest.raises(SerializationError, match="has no services"):
        serialize_phase2("c4", empty_boundary_ir, native_runtime_valid=False)


@pytest.mark.parametrize(
    ("diagram_type", "ir", "expected_lines", "hidden_label"),
    [
        (
            "deployment",
            {
                "nodes": [
                    {
                        "id": "A-B",
                        "label": "Application",
                        "name": "Hidden app name",
                        "icon": "DATABASE",
                        "group": "runtime zone",
                    }
                ],
                "artifacts": [
                    {
                        "id": "A B",
                        "name": "Image",
                        "icon": "vendor-runtime",
                        "group": "runtime zone",
                    }
                ],
                "groups": [{"id": "runtime zone", "label": "Runtime", "icon": "cloud"}],
                "links": [
                    {
                        "source": "A-B",
                        "target": "A B",
                        "label": "Hidden JDBC",
                        "source_side": "T",
                        "target_side": "B",
                        "bidirectional": True,
                    }
                ],
                "edges": [{"source": "A B", "target": "A-B", "label": "Legacy"}],
            },
            (
                'group runtime_zone(cloud)["Runtime"]',
                'service A_B(database)["Application"] in runtime_zone',
                'service A_B_2(server)["Image"] in runtime_zone',
                "A_B:T <--> B:A_B_2",
            ),
            "Hidden JDBC",
        ),
        (
            "component",
            {
                "components": [
                    {
                        "id": "web-api",
                        "label": "Web",
                        "name": "Hidden web name",
                        "icon": "SERVER",
                        "group": "application",
                    }
                ],
                "interfaces": [
                    {
                        "id": "web api",
                        "name": "Auth port",
                        "icon": "custom-interface",
                        "group": "application",
                    }
                ],
                "groups": [{"id": "application", "label": "Application"}],
                "dependencies": [
                    {
                        "source": "web-api",
                        "target": "web api",
                        "label": "Hidden OAuth",
                        "source_side": "L",
                        "target_side": "R",
                        "bidirectional": False,
                    }
                ],
                "edges": [{"source": "web api", "target": "web-api", "label": "Legacy"}],
            },
            (
                'group application(cloud)["Application"]',
                'service web_api(server)["Web"] in application',
                'service web_api_2(server)["Auth port"] in application',
                "web_api:L --> R:web_api_2",
            ),
            "Hidden OAuth",
        ),
    ],
)
def test_architecture_fallback_contract_tracks_visible_combined_records_groups_and_ports(
    diagram_type: str,
    ir: dict[str, object],
    expected_lines: tuple[str, ...],
    hidden_label: str,
) -> None:
    assert TypedIRCandidate(diagram_type=diagram_type, ir=ir).ir == ir
    code, emitted_type, _reason = serialize_phase2(diagram_type, ir)

    assert emitted_type == "architecture"
    assert all(line in code for line in expected_lines)
    assert hidden_label not in code
    assert "Legacy" not in code


@pytest.mark.parametrize(
    ("diagram_type", "primary_field", "root_field", "secondary_field"),
    [
        ("deployment", "links", "nodes", "artifacts"),
        ("component", "dependencies", "components", "interfaces"),
    ],
)
def test_architecture_fallback_relation_alias_precedence_is_not_merged(
    diagram_type: str,
    primary_field: str,
    root_field: str,
    secondary_field: str,
) -> None:
    records = [{"id": "a"}, {"id": "b"}]
    legacy_only = {
        root_field: records,
        secondary_field: [],
        "edges": [{"source": "a", "target": "b"}],
    }
    suppressed_legacy = {
        **legacy_only,
        primary_field: [],
    }

    assert TypedIRCandidate(diagram_type=diagram_type, ir=legacy_only).ir == legacy_only
    assert "a:R --> L:b" in serialize_phase2(diagram_type, legacy_only)[0]
    assert TypedIRCandidate(diagram_type=diagram_type, ir=suppressed_legacy).ir == suppressed_legacy
    assert "a:R --> L:b" not in serialize_phase2(diagram_type, suppressed_legacy)[0]


@pytest.mark.parametrize(
    ("diagram_type", "root_field", "secondary_field"),
    [
        ("deployment", "nodes", "artifacts"),
        ("component", "components", "interfaces"),
    ],
)
def test_architecture_fallback_secondary_records_share_identity_and_duplicate_semantics(
    diagram_type: str,
    root_field: str,
    secondary_field: str,
) -> None:
    ir = {
        root_field: [{"id": "same", "label": "Primary"}],
        secondary_field: [{"id": "same", "label": "Secondary"}],
        "edges": [{"source": "same", "target": "same"}],
    }

    assert TypedIRCandidate(diagram_type=diagram_type, ir=ir).ir == ir
    code = serialize_phase2(diagram_type, ir)[0]
    assert 'service same(server)["Primary"]' in code
    assert 'service same_2(server)["Secondary"]' in code
    assert "same:R --> L:same" in code


@pytest.mark.parametrize(
    ("diagram_type", "ir", "message"),
    [
        ("deployment", {"nodes": [], "artifacts": []}, "non-empty list"),
        ("component", {"components": [], "interfaces": []}, "non-empty list"),
        (
            "deployment",
            {
                "nodes": [{"id": "app", "group": "missing"}],
                "groups": [{"id": "known"}],
            },
            "unknown group",
        ),
        (
            "component",
            {
                "components": [{"id": "web"}],
                "dependencies": [{"source": "web", "target": "missing"}],
            },
            "unknown endpoint",
        ),
    ],
)
def test_architecture_fallback_contract_leaves_semantic_validation_to_serializer(
    diagram_type: str,
    ir: dict[str, object],
    message: str,
) -> None:
    assert TypedIRCandidate(diagram_type=diagram_type, ir=ir).ir == ir

    with pytest.raises(SerializationError, match=message):
        serialize_phase2(diagram_type, ir)


@pytest.mark.parametrize(
    ("ir", "message"),
    [
        (
            {
                "services": [{"id": "api", "label": "API"}],
                "edges": [{"source": "api", "target": "missing"}],
            },
            "unknown endpoint",
        ),
        (
            {
                "groups": [{"id": "cloud", "label": "Cloud"}],
                "services": [{"id": "api", "label": "API", "group": "missing"}],
                "edges": [],
            },
            "unknown group",
        ),
        (
            {
                "services": [
                    {"id": "api", "label": "First"},
                    {"id": "api", "label": "Second"},
                ],
                "edges": [],
            },
            "unique|duplicate",
        ),
    ],
)
def test_architecture_runtime_fallback_rejects_ambiguous_or_unresolved_ir(
    ir: dict[str, object], message: str
) -> None:
    with pytest.raises(SerializationError, match=message):
        serialize_runtime_fallback_result("architecture", ir)


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

        for requested_type in ("c4", "deployment", "component"):
            code, emitted_type, _ = serialize_phase2(
                requested_type,
                CASES[requested_type],
                experimental=True,
                native_runtime_valid=False,
            )
            assert emitted_type == "flowchart"
            outcome = validator.validate(code, 20)
            assert outcome.runtime.syntax_valid, (requested_type, code, outcome.runtime.error)
            assert outcome.runtime.render_valid, (
                requested_type,
                code,
                outcome.runtime.error,
                outcome.warnings,
            )

        architecture = serialize_runtime_fallback_result(
            "architecture",
            {
                "services": [
                    {"id": "api", "label": "API"},
                    {"id": "db", "label": "Database"},
                ],
                "edges": [{"source": "api", "target": "db"}],
            },
            experimental=True,
        )
        assert architecture is not None
        assert architecture.emitted_type == "flowchart"
        outcome = validator.validate(architecture.code, 20)
        assert outcome.runtime.syntax_valid, (
            "architecture",
            architecture.code,
            outcome.runtime.error,
        )
        assert outcome.runtime.render_valid, (
            "architecture",
            architecture.code,
            outcome.runtime.error,
            outcome.warnings,
        )
    finally:
        runtime.close()
