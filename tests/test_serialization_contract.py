from __future__ import annotations

import pytest

from marker_mermaid.config import SecurityProfile
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serialization import (
    SerializationContractError,
    SerializationRegistry,
    SerializationResult,
    SerializerAlreadyRegisteredError,
    UnknownSerializerError,
    registry_from_string_serializers,
)
from marker_mermaid.serializers import (
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)


def _flowchart(ir: dict[str, object], *, experimental: bool = False) -> str:
    suffix = " %% experimental" if experimental else ""
    return f'flowchart LR\n    A["{ir["label"]}"]{suffix}\n'


def test_native_result_defaults_to_a_one_item_chain() -> None:
    result = SerializationResult(
        requested_type="flowchart",
        emitted_type="flowchart",
        code="flowchart LR\n",
    )

    assert result.fallback_chain == ("flowchart",)
    assert result.used_fallback is False


def test_fallback_factory_records_full_route_and_default_warning() -> None:
    result = SerializationResult.fallback(
        "bpmn",
        "flowchart",
        "flowchart LR\n",
        via=("swimlane",),
        stability="extended",
    )

    assert result.fallback_chain == ("bpmn", "swimlane", "flowchart")
    assert result.warnings == ("Requested bpmn was emitted as flowchart.",)
    assert result.used_fallback is True
    assert result.stability == "extended"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"requested_type": "", "emitted_type": "flowchart"}, "requested_type"),
        ({"requested_type": "bpmn", "emitted_type": ""}, "emitted_type"),
        (
            {
                "requested_type": "bpmn",
                "emitted_type": "flowchart",
                "fallback_chain": ("swimlane", "flowchart"),
                "warnings": ("fallback",),
            },
            "must start",
        ),
        (
            {
                "requested_type": "bpmn",
                "emitted_type": "flowchart",
                "fallback_chain": ("bpmn", "swimlane"),
                "warnings": ("fallback",),
            },
            "must end",
        ),
        (
            {
                "requested_type": "bpmn",
                "emitted_type": "flowchart",
                "fallback_chain": ("bpmn", "flowchart", "bpmn", "flowchart"),
                "warnings": ("fallback",),
            },
            "cycles",
        ),
        (
            {
                "requested_type": "bpmn",
                "emitted_type": "flowchart",
                "fallback_chain": ("bpmn", "flowchart"),
            },
            "include a warning",
        ),
    ],
)
def test_result_rejects_invalid_contracts(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(SerializationContractError, match=message):
        SerializationResult(code="flowchart LR\n", **kwargs)  # type: ignore[arg-type]


def test_registry_wraps_a_legacy_native_serializer() -> None:
    registry = SerializationRegistry()
    registry.register_string("flowchart", _flowchart)

    result = registry.dispatch("flowchart", {"label": "Start"}, experimental=True)

    assert result.requested_type == "flowchart"
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("flowchart",)
    assert "experimental" in result.code


def test_registry_wraps_a_legacy_fallback_serializer() -> None:
    registry = SerializationRegistry()
    registry.register_string(
        "bpmn",
        _flowchart,
        emitted_type="flowchart",
        fallback_via=("swimlane",),
        stability="extended",
    )

    result = registry.dispatch("bpmn", {"label": "Approve"})

    assert result.requested_type == "bpmn"
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == ("bpmn", "swimlane", "flowchart")
    assert result.warnings


def test_registry_accepts_result_aware_serializers() -> None:
    def serialize(ir: dict[str, object], *, experimental: bool = False) -> SerializationResult:
        del experimental
        return SerializationResult.native(
            "state", f"stateDiagram-v2\n    s1: {ir['label']}\n", stability="stable"
        )

    registry = SerializationRegistry()
    registry.register_result("state", serialize)

    result = registry.dispatch("state", {"label": "Ready"})

    assert result.emitted_type == "state"
    assert result.code.startswith("stateDiagram-v2")


def test_result_serializer_cannot_misreport_the_dispatched_request() -> None:
    registry = SerializationRegistry()
    registry.register_result(
        "state",
        lambda ir, *, experimental=False: SerializationResult.native("flowchart", "flowchart LR\n"),
    )

    with pytest.raises(SerializationContractError, match="does not match"):
        registry.dispatch("state", {})


def test_registry_rejects_unknown_and_duplicate_registrations() -> None:
    registry = SerializationRegistry()
    registry.register_string("flowchart", _flowchart)

    with pytest.raises(SerializerAlreadyRegisteredError):
        registry.register_string("flowchart", _flowchart)
    with pytest.raises(UnknownSerializerError):
        registry.dispatch("sequence", {})


def test_registry_replace_is_explicit() -> None:
    registry = SerializationRegistry()
    registry.register_string("flowchart", _flowchart)
    registry.register_string(
        "flowchart", lambda ir, *, experimental=False: "flowchart TB\n", replace=True
    )

    assert registry.dispatch("flowchart", {}).code == "flowchart TB\n"


def test_registry_factory_wraps_existing_mapping_with_fallback_metadata() -> None:
    registry = registry_from_string_serializers(
        {"flowchart": _flowchart, "bpmn": _flowchart},
        emitted_types={"bpmn": "flowchart"},
        fallback_paths={"bpmn": ("swimlane",)},
        stabilities={"bpmn": "extended"},
    )

    assert registry.registered_types == ("flowchart", "bpmn")
    bpmn = registry.dispatch("bpmn", {"label": "Task"})
    assert bpmn.fallback_chain == ("bpmn", "swimlane", "flowchart")


def test_registry_factory_rejects_orphan_metadata() -> None:
    with pytest.raises(SerializationContractError, match="unregistered types"):
        registry_from_string_serializers(
            {"flowchart": _flowchart}, emitted_types={"bpmn": "flowchart"}
        )


def test_registry_rejects_non_string_legacy_output() -> None:
    registry = SerializationRegistry()
    registry.register_string("flowchart", lambda ir, *, experimental=False: None)  # type: ignore[arg-type]

    with pytest.raises(SerializationContractError, match="returned NoneType"):
        registry.dispatch("flowchart", {})


def test_project_dispatch_records_native_and_portable_fallback_grammars() -> None:
    state = serialize_typed_ir_result(
        "state",
        {"states": [{"id": "ready", "evidence_ids": ["ocr-1"]}]},
    )
    bpmn = serialize_typed_ir_result(
        "bpmn",
        {"lanes": [{"id": "lane", "nodes": [{"id": "task", "label": "Task"}]}]},
    )
    c4 = serialize_typed_ir_result(
        "c4",
        {"elements": [{"id": "api", "kind": "system", "label": "API"}]},
    )
    block = serialize_typed_ir_result(
        "block",
        {"blocks": [{"id": "api", "label": "API"}]},
    )

    assert state.emitted_type == "state" and not state.used_fallback
    assert bpmn.fallback_chain == ("bpmn", "swimlane", "flowchart")
    assert c4.emitted_type == "architecture" and c4.used_fallback
    assert block.emitted_type == "block" and "accTitle" in block.warnings[0]


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        ("mindmap", {"root": {"label": "Root"}}),
        ("timeline", {"events": [{"time": "Q1", "label": "Launch"}]}),
        (
            "kanban",
            {
                "columns": [{"id": "todo", "label": "Todo"}],
                "cards": [{"id": "ship", "label": "Ship", "column_id": "todo"}],
            },
        ),
        (
            "venn",
            {
                "sets": [
                    {"id": "a", "label": "A", "value": 2},
                    {"id": "b", "label": "B", "value": 2},
                ],
                "intersections": [{"sets": ["a", "b"], "value": 1}],
            },
        ),
        (
            "ishikawa",
            {
                "effect": {"id": "late", "label": "Late"},
                "categories": [{"id": "people", "label": "People"}],
            },
        ),
    ],
)
def test_unsupported_native_grammars_retain_accessibility_text_without_directives(diagram_type, ir):
    result = serialize_typed_ir_result(diagram_type, ir)

    assert "accTitle:" not in result.code
    assert "accDescr:" not in result.code
    assert any("resolved accessibility text remains" in warning for warning in result.warnings)


def test_chart_dispatch_records_numeric_native_and_fallback_grammars() -> None:
    pie = serialize_typed_ir_result(
        "pie",
        {"slices": [{"label": "Approved", "value": 20}]},
    )
    sankey = serialize_typed_ir_result(
        "sankey",
        {
            "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
            "flows": [{"source": "a", "target": "b", "value": 5}],
        },
    )
    treemap = serialize_typed_ir_result(
        "treemap",
        {
            "root": {
                "label": "All",
                "value": 5,
                "children": [{"label": "Leaf", "value": 5}],
            }
        },
    )

    assert pie.emitted_type == "pie" and pie.stability == "extended"
    assert sankey.emitted_type == "sankey" and not sankey.used_fallback
    assert treemap.emitted_type == "flowchart" and treemap.used_fallback
    assert "non-leaf" in treemap.warnings[0]


@pytest.mark.parametrize(
    ("diagram_type", "ir", "fallback_chain", "stability"),
    [
        (
            "architecture",
            {
                "services": [
                    {"id": "api", "label": "Public API"},
                    {"id": "db", "label": "Ledger DB"},
                ],
                "edges": [{"source": "api", "target": "db"}],
            },
            ("architecture", "flowchart"),
            "extended",
        ),
        (
            "c4",
            {
                "elements": [
                    {"id": "api", "kind": "container", "label": "API"},
                    {"id": "db", "kind": "container_database", "label": "DB"},
                ],
                "relations": [{"source": "api", "target": "db"}],
            },
            ("c4", "architecture", "flowchart"),
            "experimental",
        ),
        (
            "deployment",
            {
                "nodes": [{"id": "app", "label": "App"}],
                "artifacts": [{"id": "image", "label": "Image"}],
                "links": [{"source": "app", "target": "image"}],
            },
            ("deployment", "architecture", "flowchart"),
            "extended",
        ),
        (
            "component",
            {
                "components": [{"id": "web", "label": "Web"}],
                "interfaces": [{"id": "auth", "label": "Auth"}],
                "dependencies": [{"source": "web", "target": "auth"}],
            },
            ("component", "architecture", "flowchart"),
            "extended",
        ),
    ],
)
def test_architecture_family_runtime_fallback_records_complete_route(
    diagram_type: str,
    ir: dict[str, object],
    fallback_chain: tuple[str, ...],
    stability: str,
) -> None:
    result = serialize_runtime_fallback_result(diagram_type, ir, experimental=True)

    assert result is not None
    assert result.requested_type == diagram_type
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == fallback_chain
    assert result.stability == stability
    assert result.code.startswith("flowchart LR\n")
    assert any("CandidateValidator rejected architecture-beta" in item for item in result.warnings)


def test_architecture_runtime_fallback_keeps_labels_and_plain_topology() -> None:
    result = serialize_runtime_fallback_result(
        "architecture",
        {
            "services": [
                {"id": "api", "label": "Public API"},
                {"id": "db", "label": "Ledger DB"},
                {"id": "cache", "name": "Hidden fallback-only alias"},
            ],
            "edges": [{"source": "api", "target": "db"}],
        },
    )

    assert result is not None
    assert 'api["Public API"]' in result.code
    assert 'db["Ledger DB"]' in result.code
    assert 'cache["Hidden fallback-only alias"]' in result.code
    assert "api --> db" in result.code
    assert "api -->|" not in result.code


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "kanban",
            {
                "title": "Release board",
                "columns": [{"id": "ready lane", "title": "Ready & / : % @"}],
                "cards": [
                    {
                        "id": "write docs",
                        "text": "Write & / : % @",
                        "column_id": "ready lane",
                    }
                ],
            },
        ),
        (
            "gitgraph",
            {
                "title": "Release history",
                "initial_branch": "main",
                "operations": [
                    {"type": "commit", "branch": "main", "id": "root"},
                    {"type": "branch", "name": "feature/api", "from": "main"},
                    {"type": "commit", "branch": "feature/api", "id": "work"},
                    {"type": "commit", "branch": "main", "id": "docs"},
                    {
                        "type": "merge",
                        "source": "feature/api",
                        "target": "main",
                        "id": "merge",
                    },
                ],
            },
        ),
    ],
)
def test_planning_runtime_fallback_dispatch_records_complete_route(
    diagram_type: str, ir: dict[str, object]
) -> None:
    result = serialize_runtime_fallback_result(diagram_type, ir, experimental=True)

    assert result is not None
    assert result.requested_type == diagram_type
    assert result.emitted_type == "flowchart"
    assert result.fallback_chain == (diagram_type, "flowchart")
    assert result.used_fallback
    assert result.stability == "experimental"
    assert result.code.startswith("flowchart ")
    assert any("CandidateValidator rejected native" in warning for warning in result.warnings)
    assert any("not preserved" in warning for warning in result.warnings)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
