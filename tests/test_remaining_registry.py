from __future__ import annotations

import pytest

from marker_mermaid.config import ALL_TYPES
from marker_mermaid.serializers import (
    SERIALIZATION_REGISTRY,
    serialize_typed_ir_result,
)

CASES = {
    "journey": {
        "sections": [
            {"title": "Build", "tasks": [{"label": "Test", "score": 4, "actors": ["Ada"]}]}
        ]
    },
    "kanban": {
        "columns": [{"id": "todo", "label": "Todo"}],
        "cards": [{"id": "task", "label": "Test", "column_id": "todo"}],
    },
    "gitgraph": {
        "initial_branch": "main",
        "operations": [{"type": "commit", "branch": "main", "id": "root"}],
    },
    "packet": {"fields": [{"id": "version", "start": 0, "end": 3, "label": "Version"}]},
    "ishikawa": {
        "effect": {"id": "late", "label": "Late"},
        "categories": [
            {"id": "people", "label": "People", "children": [{"id": "skill", "label": "Skill"}]}
        ],
    },
    "treeview": {
        "root": {
            "id": "root",
            "label": "Root",
            "children": [{"id": "child", "label": "Child"}],
        }
    },
    "eventmodeling": {
        "lanes": [
            {
                "id": "orders",
                "label": "Orders",
                "frames": [
                    {"id": "submit", "type": "command", "label": "Submit"},
                    {"id": "placed", "type": "event", "label": "Placed"},
                ],
            }
        ],
        "relations": [{"source": "submit", "target": "placed"}],
    },
    "wardley": {
        "components": [
            {"id": "user", "label": "User", "x": 0.9, "y": 0.6, "anchor": True},
            {"id": "api", "label": "API", "x": 0.7, "y": 0.4},
        ],
        "links": [{"source": "user", "target": "api"}],
    },
    "cynefin": {"domains": [{"name": "complex", "items": ["Emergent"]}]},
    "railroad": {"rules": [{"name": "root", "definition": {"type": "terminal", "value": "x"}}]},
    "zenuml": {
        "participants": ["User", "API"],
        "messages": [{"source": "User", "target": "API", "label": "call"}],
    },
    "organization": {
        "root": {
            "id": "ceo",
            "label": "CEO",
            "children": [{"id": "cto", "label": "CTO"}],
        }
    },
    "data_lineage": {
        "datasets": [{"id": "raw"}, {"id": "clean"}],
        "processes": [{"id": "etl"}],
        "relations": [
            {"source": "raw", "target": "etl"},
            {"source": "etl", "target": "clean"},
        ],
    },
}


@pytest.mark.parametrize("diagram_type", CASES)
def test_remaining_types_dispatch_through_result_aware_registry(diagram_type):
    result = serialize_typed_ir_result(diagram_type, CASES[diagram_type], experimental=True)

    assert result.requested_type == diagram_type
    assert result.code.strip()
    assert result.fallback_chain[0] == diagram_type
    assert result.fallback_chain[-1] == result.emitted_type


def test_every_configured_type_has_a_typed_serializer_registration():
    serialize_typed_ir_result("flowchart", {"nodes": [{"id": "A", "label": "A"}]})

    assert set(SERIALIZATION_REGISTRY.registered_types) == set(ALL_TYPES)
