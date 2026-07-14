"""Phase 2 software-model serializers and explicit portable fallbacks.

The public adapter returns ``(code, emitted_type, fallback_reason)`` so the
pipeline can preserve the requested type separately from the Mermaid grammar
that was actually emitted.  Native syntax is used only where Mermaid 11.16
ships and renders it; the remaining UML-like types use deterministic
``architecture-beta`` or ``flowchart`` representations.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, TypeAlias

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.models import MAX_SCENE_ELEMENTS, MAX_SCENE_RELATIONS
from marker_mermaid.serializers import (
    SerializationError,
    serialize_architecture,
    serialize_architecture_flowchart_fallback,
    serialize_flowchart,
)

Phase2Serialization: TypeAlias = tuple[str, str, str | None]

REQUIREMENT_TYPE_TOKENS = {
    "requirement": "requirement",
    "functional": "functionalRequirement",
    "functional_requirement": "functionalRequirement",
    "interface": "interfaceRequirement",
    "interface_requirement": "interfaceRequirement",
    "performance": "performanceRequirement",
    "performance_requirement": "performanceRequirement",
    "physical": "physicalRequirement",
    "physical_requirement": "physicalRequirement",
    "design_constraint": "designConstraint",
}
_REQUIREMENT_RELATIONS = {
    "contains",
    "copies",
    "derives",
    "satisfies",
    "verifies",
    "refines",
    "traces",
}
_RISKS = {"low", "medium", "high"}
_VERIFY_METHODS = {"analysis", "demonstration", "inspection", "test"}
_C4_HEADERS = {
    "context": "C4Context",
    "container": "C4Container",
    "component": "C4Component",
}
_C4_KINDS = {
    "person": "Person",
    "external_person": "Person_Ext",
    "system": "System",
    "external_system": "System_Ext",
    "database": "SystemDb",
    "external_database": "SystemDb_Ext",
    "queue": "SystemQueue",
    "external_queue": "SystemQueue_Ext",
    "container": "Container",
    "container_database": "ContainerDb",
    "container_queue": "ContainerQueue",
    "component": "Component",
    "component_database": "ComponentDb",
    "component_queue": "ComponentQueue",
}
_C4_BOUNDARIES = {
    "enterprise": "Enterprise_Boundary",
    "system": "System_Boundary",
    "container": "Container_Boundary",
}
_ARCHITECTURE_ICONS = {"cloud", "database", "disk", "internet", "server"}
BLOCK_ACCESSIBILITY_LIMITATION = (
    "Mermaid 11.16 block grammar rejects accTitle and accDescr; accessibility text "
    "must remain in typed IR and review metadata."
)


def _identifier(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", str(value)).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"n_{normalized}"
    return normalized


def _text(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _accessibility(ir: dict[str, Any], diagram_type: str, *, experimental: bool) -> list[str]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    return [
        f"accTitle: {_text(resolved.title)}",
        f"accDescr: {_text(resolved.description)}",
    ]


def plan_phase2_record_ids(
    records: Any, *, field: str, fallback_prefix: str
) -> tuple[list[tuple[dict[str, Any], str, str]], dict[str, str]]:
    if not isinstance(records, list) or not records:
        raise SerializationError(f"{field} requires a non-empty list")
    used: set[str] = set()
    normalized: list[tuple[dict[str, Any], str, str]] = []
    id_map: dict[str, str] = {}
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise SerializationError(f"{field} entries must be objects")
        source_id = str(record.get("id") or f"{fallback_prefix}{index}")
        output_id = _identifier(source_id, f"{fallback_prefix}{index}")
        base = output_id
        suffix = 2
        while output_id in used:
            output_id = f"{base}_{suffix}"
            suffix += 1
        used.add(output_id)
        id_map.setdefault(source_id, output_id)
        normalized.append((record, source_id, output_id))
    return normalized, id_map


def plan_requirement_records(
    ir: dict[str, Any],
) -> tuple[
    list[tuple[dict[str, Any], str, str]],
    list[tuple[dict[str, Any], str, str]],
    dict[str, str],
]:
    """Return the exact normalized requirement/element IDs used by serialization."""

    requirements, requirement_ids = plan_phase2_record_ids(
        ir.get("requirements"), field="requirement IR", fallback_prefix="R"
    )
    elements_raw = ir.get("elements", [])
    if not isinstance(elements_raw, list):
        raise SerializationError("requirement elements must be a list")
    elements: list[tuple[dict[str, Any], str, str]] = []
    element_ids: dict[str, str] = {}
    if elements_raw:
        elements, element_ids = plan_phase2_record_ids(
            elements_raw, field="requirement elements", fallback_prefix="E"
        )
    if set(requirement_ids) & set(element_ids):
        raise SerializationError("requirement and element source ids must be distinct")
    if set(requirement_ids.values()) & set(element_ids.values()):
        elements = [
            (record, source_id, f"element_{output_id}") for record, source_id, output_id in elements
        ]
        element_ids = {source_id: output_id for _, source_id, output_id in elements}
    return requirements, elements, {**requirement_ids, **element_ids}


def plan_usecase_records(
    ir: dict[str, Any],
) -> tuple[
    list[tuple[dict[str, Any], str, str]],
    list[tuple[dict[str, Any], str, str]],
    dict[str, str],
]:
    """Return the collision-free Actor/UseCase IDs used by the Flowchart fallback."""

    relations = ir.get("relations", [])
    if not isinstance(relations, list):
        raise SerializationError("use-case relations must be a list")
    if len(relations) > MAX_SCENE_RELATIONS:
        raise SerializationError("use-case relation count exceeds the Scene relation limit")
    raw_actors = ir.get("actors")
    raw_use_cases = ir.get("use_cases")
    if (
        isinstance(raw_actors, list)
        and isinstance(raw_use_cases, list)
        and len(raw_actors) + len(raw_use_cases) > MAX_SCENE_ELEMENTS
    ):
        raise SerializationError("use-case node count exceeds the Scene element limit")
    actors, actor_ids = plan_phase2_record_ids(
        ir.get("actors"), field="use-case actors", fallback_prefix="Actor"
    )
    use_cases, use_case_ids = plan_phase2_record_ids(
        ir.get("use_cases"), field="use-case cases", fallback_prefix="UseCase"
    )
    if set(actor_ids) & set(use_case_ids):
        raise SerializationError("use-case actor and case source ids must be distinct")
    if not set(actor_ids.values()) & set(use_case_ids.values()):
        return actors, use_cases, {**actor_ids, **use_case_ids}

    occupied_ids = set(actor_ids.values())
    remapped_use_cases: list[tuple[dict[str, Any], str, str]] = []
    remapped_use_case_ids: dict[str, str] = {}
    for record, source_id, output_id in use_cases:
        emitted_id = f"usecase_{output_id}"
        base = emitted_id
        suffix = 2
        while emitted_id in occupied_ids:
            emitted_id = f"{base}_{suffix}"
            suffix += 1
        occupied_ids.add(emitted_id)
        remapped_use_cases.append((record, source_id, emitted_id))
        remapped_use_case_ids.setdefault(source_id, emitted_id)
    return actors, remapped_use_cases, {**actor_ids, **remapped_use_case_ids}


def _resolve_relation(
    relation: dict[str, Any], id_map: dict[str, str], *, field: str
) -> tuple[str, str]:
    source_key = str(relation.get("source"))
    target_key = str(relation.get("target"))
    source = id_map.get(source_key)
    target = id_map.get(target_key)
    if source is None or target is None:
        raise SerializationError(
            f"{field} relation references unknown endpoint: {source_key!r} -> {target_key!r}"
        )
    return source, target


def serialize_requirement(ir: dict[str, Any], *, experimental: bool = False) -> Phase2Serialization:
    requirements, elements, id_map = plan_requirement_records(ir)
    lines = [
        "requirementDiagram",
        *_accessibility(ir, "requirement", experimental=experimental),
    ]
    direction = str(ir.get("direction") or "TB").upper()
    if direction in {"TB", "BT", "LR", "RL"}:
        lines.append(f"direction {direction}")
    for record, source_id, output_id in requirements:
        source_type = str(record.get("type") or "requirement").lower()
        req_type = REQUIREMENT_TYPE_TOKENS.get(source_type)
        if req_type is None:
            raise SerializationError(
                f"requirement {source_id!r} has unsupported type {source_type!r}"
            )
        risk = str(record.get("risk") or "medium").lower()
        verify_method = str(
            record.get("verify_method") or record.get("verifymethod") or "analysis"
        ).lower()
        if risk not in _RISKS:
            raise SerializationError(f"requirement {source_id!r} has unsupported risk {risk!r}")
        if verify_method not in _VERIFY_METHODS:
            raise SerializationError(
                f"requirement {source_id!r} has unsupported verify method {verify_method!r}"
            )
        text = record.get("text") or record.get("label")
        if not text:
            raise SerializationError(f"requirement {source_id!r} lacks text evidence")
        requirement_id = record.get("requirement_id") or source_id
        lines.extend(
            [
                f"{req_type} {output_id} {{",
                f'id: "{_text(requirement_id)}"',
                f'text: "{_text(text)}"',
                f"risk: {risk}",
                f"verifyMethod: {verify_method}",
                "}",
            ]
        )
    for record, source_id, output_id in elements:
        label_type = record.get("type") or record.get("label") or "element"
        lines.extend(
            [
                f"element {output_id} {{",
                f'type: "{_text(label_type)}"',
                f'docref: "{_text(record.get("docref") or source_id)}"',
                "}",
            ]
        )
    relations = ir.get("relations", [])
    if not isinstance(relations, list):
        raise SerializationError("requirement relations must be a list")
    for relation in relations:
        if not isinstance(relation, dict):
            raise SerializationError("requirement relations must be objects")
        source, target = _resolve_relation(relation, id_map, field="requirement")
        relation_type = str(relation.get("type") or "traces").lower()
        if relation_type not in _REQUIREMENT_RELATIONS:
            raise SerializationError(f"unsupported requirement relation {relation_type!r}")
        lines.append(f"{source} - {relation_type} -> {target}")
    return "\n".join(lines) + "\n", "requirement", None


def serialize_block(ir: dict[str, Any], *, experimental: bool = False) -> Phase2Serialization:
    del experimental  # See BLOCK_ACCESSIBILITY_LIMITATION.
    blocks, id_map = plan_phase2_record_ids(ir.get("blocks"), field="block IR", fallback_prefix="B")
    columns = ir.get("columns", "auto")
    if columns != "auto":
        try:
            columns = int(columns)
        except (TypeError, ValueError) as exc:
            raise SerializationError("block columns must be 'auto' or a positive integer") from exc
        if columns < 1:
            raise SerializationError("block columns must be 'auto' or a positive integer")
    lines = ["block-beta", f"columns {columns}"]
    shapes = {
        "rectangle": ('["', '"]'),
        "round": ('("', '")'),
        "stadium": ('(["', '"])'),
        "circle": ('(("', '"))'),
        "diamond": ('{"', '"}'),
        "hexagon": ('{{"', '"}}'),
        "cylinder": ('[("', '")]'),
        "subroutine": ('[["', '"]]'),
    }
    for record, source_id, output_id in blocks:
        shape = str(record.get("shape") or "rectangle").lower()
        if shape not in shapes:
            raise SerializationError(f"block {source_id!r} has unsupported shape {shape!r}")
        start, end = shapes[shape]
        label = _text(record.get("label") or record.get("text") or "[unreadable]")
        lines.append(f"{output_id}{start}{label}{end}")
    edges = ir.get("edges", [])
    if not isinstance(edges, list):
        raise SerializationError("block edges must be a list")
    for edge in edges:
        if not isinstance(edge, dict):
            raise SerializationError("block edges must be objects")
        source, target = _resolve_relation(edge, id_map, field="block")
        connector = "-.->" if edge.get("style") == "dashed" else "-->"
        if edge.get("bidirectional"):
            connector = "<-->"
        label = edge.get("label")
        if label:
            if connector != "-->":
                raise SerializationError(
                    "labeled dashed or bidirectional block edges are not emitted without "
                    "verified Mermaid syntax"
                )
            lines.append(f'{source} -- "{_text(label)}" --> {target}')
        else:
            lines.append(f"{source} {connector} {target}")
    return "\n".join(lines) + "\n", "block", None


def _c4_element_line(record: dict[str, Any], output_id: str) -> str:
    kind = str(record.get("kind") or record.get("type") or "system").lower()
    macro = _C4_KINDS.get(kind)
    if macro is None:
        raise SerializationError(f"unsupported C4 element kind {kind!r}")
    label = _text(record.get("label") or record.get("name") or output_id)
    description = _text(record.get("description") or "")
    if macro.startswith(("Container", "Component")):
        technology = _text(record.get("technology") or "")
        return f'{macro}({output_id}, "{label}", "{technology}", "{description}")'
    return f'{macro}({output_id}, "{label}", "{description}")'


def serialize_c4_native(ir: dict[str, Any], *, experimental: bool = False) -> Phase2Serialization:
    """Build native C4 source for trusted diagnostics.

    Mermaid 11.16 renders native C4 with a ``data:`` image referenced through
    an undeclared ``xlink`` prefix.  The source parses and renders in Mermaid,
    but its SVG cannot pass this project's strict XML/security inspection.
    ``serialize_phase2`` therefore selects the architecture fallback below.
    """

    level = str(ir.get("level") or "context").lower()
    header = _C4_HEADERS.get(level)
    if header is None:
        raise SerializationError("C4 level must be context, container, or component")
    elements, id_map = plan_phase2_record_ids(
        ir.get("elements"), field="C4 IR", fallback_prefix="C"
    )
    boundaries_raw = ir.get("boundaries", [])
    if not isinstance(boundaries_raw, list):
        raise SerializationError("C4 boundaries must be a list")
    boundary_map: dict[str, tuple[dict[str, Any], str]] = {}
    used_ids = set(id_map.values())
    for index, boundary in enumerate(boundaries_raw, start=1):
        if not isinstance(boundary, dict):
            raise SerializationError("C4 boundaries must be objects")
        source_id = str(boundary.get("id") or f"Boundary{index}")
        output_id = _identifier(source_id, f"Boundary{index}")
        suffix = 2
        base = output_id
        while output_id in used_ids:
            output_id = f"{base}_{suffix}"
            suffix += 1
        used_ids.add(output_id)
        boundary_map[source_id] = (boundary, output_id)
    lines = [header, *_accessibility(ir, "c4", experimental=experimental)]
    if ir.get("title"):
        lines.append(f"title {_text(ir['title'])}")
    grouped: dict[str, list[tuple[dict[str, Any], str, str]]] = {
        source_id: [] for source_id in boundary_map
    }
    ungrouped: list[tuple[dict[str, Any], str, str]] = []
    for item in elements:
        boundary_id = item[0].get("boundary")
        if boundary_id is None:
            ungrouped.append(item)
        elif str(boundary_id) not in grouped:
            raise SerializationError(f"C4 element references unknown boundary {boundary_id!r}")
        else:
            grouped[str(boundary_id)].append(item)
    for record, _, output_id in ungrouped:
        lines.append(_c4_element_line(record, output_id))
    for source_id, (boundary, output_id) in boundary_map.items():
        boundary_type = str(boundary.get("type") or "system").lower()
        macro = _C4_BOUNDARIES.get(boundary_type)
        if macro is None:
            raise SerializationError(f"unsupported C4 boundary type {boundary_type!r}")
        label = _text(boundary.get("label") or source_id)
        lines.append(f'{macro}({output_id}, "{label}") {{')
        for record, _, element_id in grouped[source_id]:
            lines.append(f"    {_c4_element_line(record, element_id)}")
        lines.append("}")
    relations = ir.get("relations", [])
    if not isinstance(relations, list):
        raise SerializationError("C4 relations must be a list")
    for relation in relations:
        if not isinstance(relation, dict):
            raise SerializationError("C4 relations must be objects")
        source, target = _resolve_relation(relation, id_map, field="C4")
        label = _text(relation.get("label") or "uses")
        technology = relation.get("technology")
        if technology:
            lines.append(f'Rel({source}, {target}, "{label}", "{_text(technology)}")')
        else:
            lines.append(f'Rel({source}, {target}, "{label}")')
    return "\n".join(lines) + "\n", "c4", None


def _architecture_fallback(
    requested_type: str,
    ir: dict[str, Any],
    records: Any,
    edges: Any,
    *,
    experimental: bool,
    native_runtime_valid: bool,
    limitation: str,
) -> Phase2Serialization:
    normalized, id_map = plan_phase2_record_ids(
        records, field=f"{requested_type} IR", fallback_prefix="S"
    )
    services: list[dict[str, Any]] = []
    for record, source_id, output_id in normalized:
        icon = str(record.get("icon") or "server").lower()
        if icon not in _ARCHITECTURE_ICONS:
            icon = "server"
        services.append(
            {
                "id": output_id,
                "label": record.get("label") or record.get("name") or source_id,
                "icon": icon,
                "group": record.get("group"),
            }
        )
    if not isinstance(edges, list):
        raise SerializationError(f"{requested_type} relations must be a list")
    architecture_edges: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            raise SerializationError(f"{requested_type} relations must be objects")
        source, target = _resolve_relation(edge, id_map, field=requested_type)
        architecture_edges.append(
            {
                "source": source,
                "target": target,
                "bidirectional": bool(edge.get("bidirectional")),
                "source_side": edge.get("source_side", "R"),
                "target_side": edge.get("target_side", "L"),
            }
        )
    architecture_ir = {**ir, "services": services, "edges": architecture_edges}
    if native_runtime_valid:
        code = serialize_architecture(architecture_ir, experimental=experimental)
        return code, "architecture", limitation
    code = serialize_architecture_flowchart_fallback(
        architecture_ir,
        experimental=experimental,
        accessibility_type=requested_type,
    )
    return (
        code,
        "flowchart",
        f"{limitation}; CandidateValidator rejected architecture-beta, so service/group "
        "labels and unlabeled endpoint topology were emitted as portable Flowchart",
    )


def serialize_c4(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> Phase2Serialization:
    level = str(ir.get("level") or "context").lower()
    if level not in _C4_HEADERS:
        raise SerializationError("C4 level must be context, container, or component")
    elements = ir.get("elements")
    if not isinstance(elements, list):
        raise SerializationError("C4 elements must be a list")
    boundaries = ir.get("boundaries", [])
    if not isinstance(boundaries, list):
        raise SerializationError("C4 boundaries must be a list")
    boundary_ids = {
        str(boundary.get("id"))
        for boundary in boundaries
        if isinstance(boundary, dict) and boundary.get("id") is not None
    }
    records: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            raise SerializationError("C4 elements must be objects")
        kind = str(element.get("kind") or element.get("type") or "system").lower()
        if kind not in _C4_KINDS:
            raise SerializationError(f"unsupported C4 element kind {kind!r}")
        boundary = element.get("boundary")
        if boundary is not None and str(boundary) not in boundary_ids:
            raise SerializationError(f"C4 element references unknown boundary {boundary!r}")
        if "database" in kind:
            icon = "database"
        elif "queue" in kind:
            icon = "disk"
        elif "person" in kind:
            icon = "internet"
        else:
            icon = "server"
        records.append({**element, "icon": icon, "group": boundary})
    groups: list[dict[str, Any]] = []
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            raise SerializationError("C4 boundaries must be objects")
        groups.append({**boundary, "icon": "cloud"})
    fallback_ir = {**ir, "groups": groups}
    return _architecture_fallback(
        "c4",
        fallback_ir,
        records,
        ir.get("relations", []),
        experimental=experimental,
        native_runtime_valid=native_runtime_valid,
        limitation=(
            "strict-safe architecture fallback from c4; native Mermaid 11.16 C4 embeds a "
            "data image through an undeclared xlink prefix, while technology, relationship "
            "labels, and exact C4 boundary notation remain in typed IR"
        ),
    )


def serialize_deployment(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> Phase2Serialization:
    records = ir.get("nodes")
    artifacts = ir.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise SerializationError("deployment artifacts must be a list")
    if not isinstance(records, list):
        raise SerializationError("deployment nodes must be a list")
    combined = [*records, *artifacts]
    return _architecture_fallback(
        "deployment",
        ir,
        combined,
        ir.get("links", ir.get("edges", [])),
        experimental=experimental,
        native_runtime_valid=native_runtime_valid,
        limitation=(
            "portable architecture fallback from deployment; artifact containment, "
            "stereotypes, and link labels remain in typed IR"
        ),
    )


def serialize_component(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> Phase2Serialization:
    components = ir.get("components")
    interfaces = ir.get("interfaces", [])
    if not isinstance(components, list):
        raise SerializationError("component components must be a list")
    if not isinstance(interfaces, list):
        raise SerializationError("component interfaces must be a list")
    records = [*components, *interfaces]
    return _architecture_fallback(
        "component",
        ir,
        records,
        ir.get("dependencies", ir.get("edges", [])),
        experimental=experimental,
        native_runtime_valid=native_runtime_valid,
        limitation=(
            "portable architecture fallback from component; provided/required interface "
            "notation and dependency labels remain in typed IR"
        ),
    )


def serialize_usecase(ir: dict[str, Any], *, experimental: bool = False) -> Phase2Serialization:
    actors, use_cases, id_map = plan_usecase_records(ir)
    nodes = [
        {
            "id": output_id,
            "label": record.get("label") or record.get("name") or source_id,
            "shape": "stadium",
        }
        for record, source_id, output_id in actors
    ]
    nodes.extend(
        {
            "id": output_id,
            "label": record.get("label") or record.get("name") or source_id,
            "shape": "round",
        }
        for record, source_id, output_id in use_cases
    )
    relations = ir.get("relations", [])
    if not isinstance(relations, list):
        raise SerializationError("use-case relations must be a list")
    edges: list[dict[str, Any]] = []
    for relation in relations:
        if not isinstance(relation, dict):
            raise SerializationError("use-case relations must be objects")
        source, target = _resolve_relation(relation, id_map, field="use-case")
        relation_type = relation.get("type") or relation.get("label")
        edges.append({"source": source, "target": target, "label": relation_type})
    code = serialize_flowchart(
        {**ir, "nodes": nodes, "edges": edges, "direction": ir.get("direction", "LR")},
        experimental=experimental,
    )
    return (
        code,
        "flowchart",
        "portable flowchart fallback from usecase; actor glyphs and UML system "
        "boundaries are not native Mermaid 11.16 syntax",
    )


PHASE2_SERIALIZERS: dict[str, Callable[..., Phase2Serialization]] = {
    "requirement": serialize_requirement,
    "block": serialize_block,
    "c4": serialize_c4,
    "deployment": serialize_deployment,
    "component": serialize_component,
    "usecase": serialize_usecase,
}


def serialize_phase2(
    diagram_type: str,
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> Phase2Serialization:
    """Serialize a Phase 2 IR and disclose the emitted Mermaid grammar."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    serializer = PHASE2_SERIALIZERS.get(diagram_type)
    if serializer is None:
        raise SerializationError(f"no Phase 2 typed serializer for {diagram_type}")
    if not native_runtime_valid:
        if diagram_type not in {"c4", "deployment", "component"}:
            raise SerializationError(
                f"no architecture runtime fallback for Phase 2 type {diagram_type}"
            )
        return serializer(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
    return serializer(ir, experimental=experimental)
