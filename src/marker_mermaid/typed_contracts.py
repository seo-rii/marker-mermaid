"""Type-specific root contracts and prompt guidance for structured extraction.

Serializers remain the authoritative deep semantic validators.  These contracts form
the earlier extraction boundary: they reject a typed candidate that uses another
diagram family's root shape and give the VLM a compact, enabled-type-only schema
catalog instead of asking it to guess field names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from marker_mermaid.config import ALL_TYPES

RootKind = Literal["list", "object", "string"]


@dataclass(frozen=True, slots=True)
class TypedIRContract:
    required: tuple[tuple[str, RootKind], ...]
    optional: tuple[str, ...] = ()
    guidance: str = ""


TYPED_IR_CONTRACTS: dict[str, TypedIRContract] = {
    "flowchart": TypedIRContract((("nodes", "list"),), ("edges", "groups"), "node/edge graph"),
    "generic_network": TypedIRContract(
        (("nodes", "list"),), ("edges", "groups"), "generic node/edge graph"
    ),
    "swimlane": TypedIRContract((("lanes", "list"),), ("edges",), "lanes with nested nodes"),
    "bpmn": TypedIRContract((("lanes", "list"),), ("edges",), "BPMN lanes/tasks/events"),
    "sequence": TypedIRContract(
        (("participants", "list"), ("messages", "list")), guidance="ordered messages"
    ),
    "state": TypedIRContract(
        (("states", "list"), ("transitions", "list")), guidance="states and transitions"
    ),
    "class": TypedIRContract((("classes", "list"),), ("relations",), "classes, members, relations"),
    "er": TypedIRContract(
        (("entities", "list"),), ("relationships",), "entities and cardinalities"
    ),
    "architecture": TypedIRContract(
        (("services", "list"),), ("groups", "edges"), "services, groups, connections"
    ),
    "c4": TypedIRContract(
        (("elements", "list"),), ("boundaries", "relations", "level"), "C4 elements"
    ),
    "requirement": TypedIRContract(
        (("requirements", "list"),), ("elements", "relations"), "requirements and relations"
    ),
    "block": TypedIRContract((("blocks", "list"),), ("edges", "columns"), "blocks and ports"),
    "deployment": TypedIRContract(
        (("nodes", "list"),), ("artifacts", "links", "edges"), "deployment nodes/artifacts"
    ),
    "component": TypedIRContract(
        (("components", "list"),),
        ("interfaces", "dependencies", "edges"),
        "components and interfaces",
    ),
    "usecase": TypedIRContract(
        (("actors", "list"), ("use_cases", "list")),
        ("relations",),
        "actors and use cases",
    ),
    "mindmap": TypedIRContract((("root", "object"),), guidance="rooted hierarchy"),
    "timeline": TypedIRContract((("events", "list"),), guidance="time/event records"),
    "gantt": TypedIRContract((("sections", "list"),), guidance="sections with dated tasks"),
    "journey": TypedIRContract((("sections", "list"),), guidance="sections with scored tasks"),
    "kanban": TypedIRContract(
        (("columns", "list"), ("cards", "list")), guidance="columns and assigned cards"
    ),
    "gitgraph": TypedIRContract(
        (("initial_branch", "string"), ("operations", "list")),
        guidance="ordered branch/commit/merge operations",
    ),
    "pie": TypedIRContract((("slices", "list"),), guidance="labels with explicit values"),
    "xychart": TypedIRContract(
        (("x_axis", "object"), ("y_axis", "object"), ("series", "list")),
        guidance="explicit axes and series values",
    ),
    "quadrant": TypedIRContract(
        (("x_axis", "object"), ("y_axis", "object"), ("points", "list")),
        ("quadrants",),
        "normalized positioned points",
    ),
    "sankey": TypedIRContract(
        (("nodes", "list"), ("flows", "list")), ("links",), "weighted directed flows"
    ),
    "radar": TypedIRContract(
        (("dimensions", "list"), ("series", "list")), guidance="dimensions and explicit values"
    ),
    "treemap": TypedIRContract((("root", "object"),), guidance="valued hierarchy"),
    "venn": TypedIRContract(
        (("sets", "list"), ("intersections", "list")), guidance="sets and explicit intersections"
    ),
    "packet": TypedIRContract((("fields", "list"),), guidance="explicit bit ranges"),
    "ishikawa": TypedIRContract(
        (("effect", "object"), ("categories", "list")), guidance="effect/category/cause tree"
    ),
    "wardley": TypedIRContract(
        (("components", "list"),), ("links",), "components with explicit x/y coordinates"
    ),
    "cynefin": TypedIRContract((("domains", "list"),), ("transitions",), "named domains and items"),
    "treeview": TypedIRContract((("root", "object"),), guidance="rooted hierarchy"),
    "eventmodeling": TypedIRContract(
        (("lanes", "list"),), ("relations",), "lanes with command/event frames"
    ),
    "zenuml": TypedIRContract(
        (("participants", "list"), ("messages", "list")), guidance="sequence-like messages"
    ),
    "railroad": TypedIRContract((("rules", "list"),), guidance="grammar rule AST"),
    "organization": TypedIRContract((("root", "object"),), guidance="organization hierarchy"),
    "data_lineage": TypedIRContract(
        (("datasets", "list"), ("relations", "list")),
        ("processes",),
        "datasets, processes, lineage relations",
    ),
}

if set(TYPED_IR_CONTRACTS) != set(ALL_TYPES):  # pragma: no cover - import-time invariant
    missing = sorted(set(ALL_TYPES) - set(TYPED_IR_CONTRACTS))
    extra = sorted(set(TYPED_IR_CONTRACTS) - set(ALL_TYPES))
    raise RuntimeError(f"typed IR contract registry mismatch: missing={missing}, extra={extra}")


def validate_typed_ir_contract(diagram_type: str, ir: dict[str, Any]) -> None:
    """Validate the diagram-family root shape without duplicating serializer semantics."""

    contract = TYPED_IR_CONTRACTS.get(diagram_type)
    if contract is None:
        raise ValueError(f"typed IR has no registered contract for {diagram_type!r}")
    for field, kind in contract.required:
        if field not in ir:
            raise ValueError(f"{diagram_type} typed IR requires root field {field!r}")
        value = ir[field]
        if kind == "list" and not isinstance(value, list):
            raise ValueError(f"{diagram_type} typed IR field {field!r} must be a list")
        if kind == "object" and not isinstance(value, dict):
            raise ValueError(f"{diagram_type} typed IR field {field!r} must be an object")
        if kind == "string" and not isinstance(value, str):
            raise ValueError(f"{diagram_type} typed IR field {field!r} must be a string")


def typed_ir_contract_prompt(enabled_types: set[str]) -> str:
    """Render a compact deterministic schema catalog for the enabled VLM types."""

    lines = [
        "Typed IR root contracts (use these exact field names):",
        "Common optional fields: title, description, acc_title, acc_description, direction.",
        "Every semantic node/relation record must include evidence_ids from Prior evidence.",
    ]
    for diagram_type in sorted(enabled_types):
        contract = TYPED_IR_CONTRACTS[diagram_type]
        required = ", ".join(f"{field}:{kind}" for field, kind in contract.required)
        optional = f"; optional {', '.join(contract.optional)}" if contract.optional else ""
        guidance = f"; {contract.guidance}" if contract.guidance else ""
        lines.append(f"- {diagram_type}: {required}{optional}{guidance}")
    return "\n".join(lines)
