"""Deterministic serializers from typed/common IR to Mermaid text."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from marker_mermaid.accessibility import (
    accessibility_limitation_warning,
    enrich_accessibility_ir,
    resolve_accessibility,
    supports_accessibility_directives,
)
from marker_mermaid.flowchart_structure import (
    FlowchartStructureError,
    plan_flowchart_structure,
    portable_identifier,
    prepare_swimlane_structure,
)
from marker_mermaid.models import DiagramSceneIR
from marker_mermaid.serialization import SerializationResult, registry_from_string_serializers


class SerializationError(ValueError):
    """Raised when an IR cannot be represented without inventing information."""


def _identifier(value: str, fallback: str = "node") -> str:
    return portable_identifier(value, fallback)


def _text(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', "&quot;").replace("\n", " ").strip()


def _accessibility(
    ir: dict[str, Any], experimental: bool = False, *, diagram_type: str
) -> list[str]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    return [
        f"    accTitle: {_text(resolved.title)}",
        f"    accDescr: {_text(resolved.description)}",
    ]


def serialize_flowchart(ir: dict[str, Any], *, experimental: bool = False) -> str:
    nodes = ir.get("nodes")
    edges = ir.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        raise SerializationError("flowchart IR requires at least one node")
    direction = ir.get("direction", "TB")
    if direction not in {"TB", "BT", "LR", "RL"}:
        direction = "TB"
    lines = [
        f"flowchart {direction}",
        *_accessibility(ir, experimental, diagram_type="flowchart"),
    ]
    groups = ir.get("groups", [])
    try:
        structure = plan_flowchart_structure(nodes, groups)
    except FlowchartStructureError as exc:
        raise SerializationError(str(exc)) from exc
    id_map: dict[str, str] = {}
    shapes = {
        "round": ('(["', '"])'),
        "stadium": '(["',
        "circle": '(("',
        "diamond": '{"',
        "hexagon": '{{"',
        "cylinder": '[("',
        "subroutine": '[["',
    }
    shape_ends = {
        "stadium": '"])',
        "circle": '"))',
        "diamond": '"}',
        "hexagon": '"}}',
        "cylinder": '")]',
        "subroutine": '"]]',
    }
    node_declarations: list[tuple[str, str, str]] = []
    for node, placement in zip(nodes, structure.nodes, strict=True):
        source_id = placement.source_id
        node_id = placement.emitted_id
        id_map[source_id] = node_id
        label = _text(node.get("label") or node.get("text") or "[unreadable]")
        shape = str(node.get("shape") or "rectangle").lower()
        if shape == "round":
            start, end = shapes[shape]
        else:
            start = shapes.get(shape, '["')
            end = shape_ends.get(shape, '"]')
        node_declarations.append(
            (source_id, node_id, f"    {node_id}{start}{label}{end}")
        )

    grouped_source_ids = {
        member for group in structure.groups for member in group.member_source_ids
    }

    declaration_by_source = {
        source_id: line for source_id, _node_id, line in node_declarations
    }
    for group in structure.groups:
        lines.append(f'    subgraph {group.emitted_id}["{_text(group.label)}"]')
        lines.extend(
            f"    {declaration_by_source[member]}" for member in group.member_source_ids
        )
        lines.append("    end")
    lines.extend(
        line
        for source_id, _node_id, line in node_declarations
        if source_id not in grouped_source_ids
    )
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = id_map.get(str(edge.get("source")))
        target = id_map.get(str(edge.get("target")))
        if source is None or target is None:
            continue
        arrow = "-.->" if edge.get("style") == "dashed" else "-->"
        if edge.get("bidirectional"):
            arrow = "<-->"
        label = edge.get("label")
        connector = f"-->|{_text(label)}|" if label else arrow
        lines.append(f"    {source} {connector} {target}")
    return "\n".join(lines) + "\n"


def serialize_swimlane(ir: dict[str, Any], *, experimental: bool = False) -> str:
    lanes = ir.get("lanes")
    try:
        structure = prepare_swimlane_structure(lanes)
    except FlowchartStructureError as exc:
        raise SerializationError(str(exc)) from exc
    accessibility = resolve_accessibility(ir, "swimlane", experimental=experimental)
    flow_ir = {
        **ir,
        "acc_title": accessibility.title,
        "acc_description": accessibility.description,
        "nodes": list(structure.nodes),
        "edges": ir.get("edges", []),
        "groups": list(structure.groups),
    }
    return serialize_flowchart(flow_ir, experimental=experimental)


def serialize_sequence(ir: dict[str, Any], *, experimental: bool = False) -> str:
    participants = ir.get("participants")
    messages = ir.get("messages", [])
    if not isinstance(participants, list) or not participants:
        raise SerializationError("sequence IR requires participants")
    lines = [
        "sequenceDiagram",
        *_accessibility(ir, experimental, diagram_type="sequence"),
    ]
    id_map: dict[str, str] = {}
    for index, participant in enumerate(participants, start=1):
        if isinstance(participant, str):
            source_id = participant
            label = participant
        else:
            source_id = str(participant.get("id") or f"P{index}")
            label = participant.get("label") or source_id
        participant_id = _identifier(source_id, f"P{index}")
        id_map[source_id] = participant_id
        lines.append(f"    participant {participant_id} as {_text(label)}")
    arrows = {
        "solid": "->>",
        "dotted": "-->>",
        "open": "->",
        "dotted_open": "-->",
        "cross": "-x",
    }
    for message in messages:
        source = id_map.get(str(message.get("source")))
        target = id_map.get(str(message.get("target")))
        if source is None or target is None:
            continue
        arrow = arrows.get(message.get("style"), "->>")
        lines.append(
            f"    {source}{arrow}{target}: {_text(message.get('label') or '[unreadable]')}"
        )
    return "\n".join(lines) + "\n"


def serialize_mindmap(ir: dict[str, Any], *, experimental: bool = False) -> str:
    root = ir.get("root")
    if not isinstance(root, dict):
        raise SerializationError("mindmap IR requires a root object")
    # Mermaid 11.16 parses accTitle/accDescr as additional roots in mindmaps.
    # Preserve the hard render gate and keep the requested text in typed IR until
    # upstream supports accessible directives for this grammar.
    lines = ["mindmap"]
    node_number = 0

    def append_branch(node: dict[str, Any], depth: int) -> None:
        nonlocal node_number
        node_number += 1
        label = _text(node.get("label") or node.get("text") or "[unreadable]")
        node_id = "root" if depth == 1 else f"node_{node_number}"
        if depth == 1:
            lines.append(f"{'    ' * depth}{node_id}(({label}))")
        else:
            lines.append(f'{"    " * depth}{node_id}["{label}"]')
        for child in node.get("children", []):
            if isinstance(child, dict):
                append_branch(child, depth + 1)

    append_branch(root, 1)
    return "\n".join(lines) + "\n"


def serialize_timeline(ir: dict[str, Any], *, experimental: bool = False) -> str:
    events = ir.get("events")
    if not isinstance(events, list) or not events:
        raise SerializationError("timeline IR requires events")
    lines = ["timeline"]
    if ir.get("title"):
        lines.append(f"    title {_text(ir['title'])}")
    for event in events:
        period = _text(event.get("time") or event.get("period") or "[unreadable time]")
        labels = event.get("events") or [event.get("label") or "[unreadable]"]
        lines.append(f"    {period} : " + " : ".join(_text(label) for label in labels))
    return "\n".join(lines) + "\n"


def serialize_gantt(ir: dict[str, Any], *, experimental: bool = False) -> str:
    sections = ir.get("sections")
    if not isinstance(sections, list) or not sections:
        raise SerializationError("gantt IR requires sections")
    lines = ["gantt", *_accessibility(ir, experimental, diagram_type="gantt")]
    if ir.get("title"):
        lines.append(f"    title {_text(ir['title'])}")
    if ir.get("date_format"):
        lines.append(f"    dateFormat {_text(ir['date_format'])}")
    for section in sections:
        lines.append(f"    section {_text(section.get('title') or 'Tasks')}")
        for index, task in enumerate(section.get("tasks", []), start=1):
            label = _text(task.get("label") or f"Task {index}")
            start = task.get("start")
            end = task.get("end") or task.get("duration")
            if not start or not end:
                raise SerializationError(
                    f"gantt task {label!r} lacks start and end/duration evidence"
                )
            fields = [task.get("status"), task.get("id"), start, end]
            values = [_text(value) for value in fields if value not in {None, ""}]
            lines.append(f"    {label} :{', '.join(values)}")
    return "\n".join(lines) + "\n"


def serialize_architecture(ir: dict[str, Any], *, experimental: bool = False) -> str:
    services = ir.get("services")
    if not isinstance(services, list) or not services:
        raise SerializationError("architecture IR requires services")
    lines = [
        "architecture-beta",
        *_accessibility(ir, experimental, diagram_type="architecture"),
    ]
    ids: set[str] = set()
    for index, group in enumerate(ir.get("groups", []), start=1):
        group_id = _identifier(str(group.get("id") or f"G{index}"))
        ids.add(group_id)
        icon = _identifier(str(group.get("icon") or "cloud"))
        lines.append(f'    group {group_id}({icon})["{_text(group.get("label") or group_id)}"]')
    id_map: dict[str, str] = {}
    for index, service in enumerate(services, start=1):
        source_id = str(service.get("id") or f"S{index}")
        service_id = _identifier(source_id, f"S{index}")
        id_map[source_id] = service_id
        ids.add(service_id)
        icon = _identifier(str(service.get("icon") or "server"))
        group = service.get("group")
        suffix = f" in {_identifier(str(group))}" if group else ""
        label = _text(service.get("label") or source_id)
        lines.append(f'    service {service_id}({icon})["{label}"]{suffix}')
    for edge in ir.get("edges", []):
        source = id_map.get(str(edge.get("source")))
        target = id_map.get(str(edge.get("target")))
        if source is None or target is None:
            continue
        source_side = edge.get("source_side", "R")
        target_side = edge.get("target_side", "L")
        if source_side not in {"L", "R", "T", "B"} or target_side not in {"L", "R", "T", "B"}:
            source_side, target_side = "R", "L"
        connector = "<-->" if edge.get("bidirectional") else "-->"
        lines.append(f"    {source}:{source_side} {connector} {target_side}:{target}")
    return "\n".join(lines) + "\n"


def serialize_architecture_flowchart_fallback(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    accessibility_type: str = "architecture",
) -> str:
    """Project validated Architecture evidence into the portable Flowchart subset.

    This fallback is built from typed IR rather than rejected Mermaid source.  It
    keeps service/group labels and endpoint topology while failing closed when a
    group or relation cannot be mapped without guessing.  Architecture icons,
    connector ports, and relation labels remain in typed IR and review metadata.
    """

    services = ir.get("services")
    if not isinstance(services, list) or not services:
        raise SerializationError("architecture IR requires services")
    raw_groups = ir.get("groups", [])
    if not isinstance(raw_groups, list):
        raise SerializationError("architecture groups must be a list")

    group_records: list[tuple[dict[str, Any], str]] = []
    group_members: dict[str, list[str]] = {}
    for index, group in enumerate(raw_groups, start=1):
        if not isinstance(group, dict):
            raise SerializationError("architecture groups must be objects")
        group_id = str(group.get("id") or f"G{index}")
        if group_id in group_members:
            raise SerializationError("architecture group ids must be unique")
        group_records.append((group, group_id))
        group_members[group_id] = []

    nodes: list[dict[str, Any]] = []
    service_ids: set[str] = set()
    for index, service in enumerate(services, start=1):
        if not isinstance(service, dict):
            raise SerializationError("architecture services must be objects")
        service_id = str(service.get("id") or f"S{index}")
        if service_id in service_ids:
            raise SerializationError("architecture service ids must be unique")
        service_ids.add(service_id)
        nodes.append(
            {
                **service,
                "id": service_id,
                "label": service.get("label") or service.get("name") or service_id,
            }
        )
        group_id = service.get("group")
        if group_id is not None and group_id != "":
            source_group_id = str(group_id)
            if source_group_id not in group_members:
                raise SerializationError(
                    f"architecture service references unknown group {source_group_id!r}"
                )
            group_members[source_group_id].append(service_id)

    groups: list[dict[str, Any]] = []
    for group, group_id in group_records:
        members = group_members[group_id]
        if not members:
            raise SerializationError(
                f"architecture group {group_id!r} has no services for Flowchart fallback"
            )
        groups.append(
            {
                "id": group_id,
                "label": group.get("label") or group_id,
                "member_ids": members,
            }
        )

    raw_edges = ir.get("edges", [])
    if not isinstance(raw_edges, list):
        raise SerializationError("architecture edges must be a list")
    edges: list[dict[str, Any]] = []
    for edge in raw_edges:
        if not isinstance(edge, dict):
            raise SerializationError("architecture edges must be objects")
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source not in service_ids or target not in service_ids:
            raise SerializationError(
                f"architecture edge references unknown endpoint: {source!r} -> {target!r}"
            )
        edges.append(
            {
                "source": source,
                "target": target,
                "bidirectional": bool(edge.get("bidirectional")),
            }
        )

    accessibility = resolve_accessibility(
        ir,
        accessibility_type,
        experimental=experimental,
    )
    direction = str(ir.get("direction") or "LR").upper()
    if direction not in {"TB", "BT", "LR", "RL"}:
        direction = "LR"
    return serialize_flowchart(
        {
            **ir,
            "acc_title": accessibility.title,
            "acc_description": accessibility.description,
            "direction": direction,
            "nodes": nodes,
            "groups": groups,
            "edges": edges,
        },
        experimental=experimental,
    )


SERIALIZERS: dict[str, Callable[..., str]] = {
    "flowchart": serialize_flowchart,
    "generic_network": serialize_flowchart,
    "bpmn": serialize_swimlane,
    "swimlane": serialize_swimlane,
    "sequence": serialize_sequence,
    "mindmap": serialize_mindmap,
    "timeline": serialize_timeline,
    "gantt": serialize_gantt,
    "architecture": serialize_architecture,
}

SERIALIZATION_REGISTRY = registry_from_string_serializers(
    SERIALIZERS,
    emitted_types={
        "generic_network": "flowchart",
        "swimlane": "flowchart",
        "bpmn": "flowchart",
    },
    fallback_paths={"bpmn": ("swimlane",)},
    warnings={
        "generic_network": ("Generic network was emitted as a portable flowchart.",),
        "swimlane": ("Swimlane was emitted as flowchart subgraphs.",),
        "bpmn": ("BPMN was emitted through swimlane flowchart subgraphs.",),
    },
    stabilities={
        "generic_network": "extended",
        "swimlane": "extended",
        "bpmn": "extended",
        "architecture": "extended",
    },
)
_EXTENDED_SERIALIZERS_REGISTERED = False


def _ensure_extended_serializers() -> None:
    global _EXTENDED_SERIALIZERS_REGISTERED
    if _EXTENDED_SERIALIZERS_REGISTERED:
        return
    from marker_mermaid.serializers_charts_core import serialize_chart_core
    from marker_mermaid.serializers_charts_flow import serialize_chart_flow
    from marker_mermaid.serializers_charts_sets import serialize_chart_set
    from marker_mermaid.serializers_experimental import serialize_experimental
    from marker_mermaid.serializers_phase2 import (
        BLOCK_ACCESSIBILITY_LIMITATION,
        serialize_phase2,
    )
    from marker_mermaid.serializers_planning import serialize_planning
    from marker_mermaid.serializers_special import serialize_special
    from marker_mermaid.serializers_uml import UML_SERIALIZERS

    for diagram_type, serializer in UML_SERIALIZERS.items():
        SERIALIZERS[diagram_type] = serializer
        SERIALIZATION_REGISTRY.register_string(diagram_type, serializer)

    stabilities = {
        "requirement": "extended",
        "block": "experimental",
        "c4": "experimental",
        "deployment": "extended",
        "component": "extended",
        "usecase": "experimental",
    }
    for requested_type in (
        "requirement",
        "block",
        "c4",
        "deployment",
        "component",
        "usecase",
    ):

        def serialize_result(
            ir: dict[str, Any],
            *,
            experimental: bool = False,
            _requested_type: str = requested_type,
        ) -> SerializationResult:
            code, emitted_type, fallback_reason = serialize_phase2(
                _requested_type,
                ir,
                experimental=experimental,
            )
            stability = stabilities[_requested_type]
            if emitted_type == _requested_type:
                return SerializationResult.native(
                    _requested_type,
                    code,
                    warnings=(
                        (BLOCK_ACCESSIBILITY_LIMITATION,) if _requested_type == "block" else ()
                    ),
                    stability=stability,
                )
            return SerializationResult.fallback(
                _requested_type,
                emitted_type,
                code,
                warnings=(fallback_reason or f"Portable fallback from {_requested_type}.",),
                stability=stability,
            )

        SERIALIZATION_REGISTRY.register_result(requested_type, serialize_result)

    chart_stabilities = {
        "pie": "extended",
        "xychart": "experimental",
        "quadrant": "experimental",
        "treemap": "experimental",
        "venn": "experimental",
    }
    for requested_type in ("pie", "xychart", "quadrant", "treemap", "venn"):

        def serialize_chart_result(
            ir: dict[str, Any],
            *,
            experimental: bool = False,
            _requested_type: str = requested_type,
        ) -> SerializationResult:
            if _requested_type in {"pie", "xychart", "quadrant"}:
                code, emitted_type, fallback_reason = serialize_chart_core(
                    _requested_type,
                    ir,
                    experimental=experimental,
                )
            else:
                code, emitted_type, fallback_reason = serialize_chart_set(
                    _requested_type,
                    ir,
                    experimental=experimental,
                )
            stability = chart_stabilities[_requested_type]
            if emitted_type == _requested_type:
                return SerializationResult.native(
                    _requested_type,
                    code,
                    stability=stability,
                )
            return SerializationResult.fallback(
                _requested_type,
                emitted_type,
                code,
                warnings=(fallback_reason or f"Portable fallback from {_requested_type}.",),
                stability=stability,
            )

        SERIALIZATION_REGISTRY.register_result(requested_type, serialize_chart_result)

    for requested_type in ("sankey", "radar"):

        def serialize_flow_chart_result(
            ir: dict[str, Any],
            *,
            experimental: bool = False,
            _requested_type: str = requested_type,
        ) -> SerializationResult:
            return serialize_chart_flow(
                _requested_type,
                ir,
                experimental=experimental,
            )

        SERIALIZATION_REGISTRY.register_result(
            requested_type,
            serialize_flow_chart_result,
        )

    for requested_type in ("journey", "kanban", "gitgraph"):

        def serialize_planning_result(
            ir: dict[str, Any],
            *,
            experimental: bool = False,
            _requested_type: str = requested_type,
        ) -> SerializationResult:
            return serialize_planning(
                _requested_type,
                ir,
                experimental=experimental,
            )

        SERIALIZATION_REGISTRY.register_result(requested_type, serialize_planning_result)

    for requested_type in ("packet", "ishikawa", "treeview", "eventmodeling"):

        def serialize_special_result(
            ir: dict[str, Any],
            *,
            experimental: bool = False,
            _requested_type: str = requested_type,
        ) -> SerializationResult:
            return serialize_special(
                _requested_type,
                ir,
                experimental=experimental,
            )

        SERIALIZATION_REGISTRY.register_result(requested_type, serialize_special_result)

    for requested_type in (
        "wardley",
        "cynefin",
        "railroad",
        "zenuml",
        "organization",
        "data_lineage",
    ):

        def serialize_experimental_result(
            ir: dict[str, Any],
            *,
            experimental: bool = False,
            _requested_type: str = requested_type,
        ) -> SerializationResult:
            return serialize_experimental(
                _requested_type,
                ir,
                experimental=experimental,
            )

        SERIALIZATION_REGISTRY.register_result(requested_type, serialize_experimental_result)
    _EXTENDED_SERIALIZERS_REGISTERED = True


def serialize_typed_ir_result(
    diagram_type: str,
    ir: dict[str, Any],
    *,
    experimental: bool = False,
) -> SerializationResult:
    """Serialize typed IR while retaining native/fallback grammar metadata."""

    _ensure_extended_serializers()
    enriched_ir = enrich_accessibility_ir(
        ir,
        diagram_type,
        experimental=experimental,
    )
    result = SERIALIZATION_REGISTRY.dispatch(
        diagram_type,
        enriched_ir,
        experimental=experimental,
    )
    if supports_accessibility_directives(result.emitted_type):
        return result
    warning = accessibility_limitation_warning(result.emitted_type)
    if warning in result.warnings:
        return result
    return replace(result, warnings=(*result.warnings, warning))


def serialize_runtime_fallback_result(
    diagram_type: str,
    ir: dict[str, Any],
    *,
    experimental: bool = False,
) -> SerializationResult | None:
    """Return a declared portable fallback after native runtime rejection.

    Only serializers that can preserve their typed evidence while changing grammar
    participate.  Returning ``None`` keeps unsupported native candidates invalid.
    """

    if diagram_type in {"architecture", "c4", "deployment", "component"}:
        initial = serialize_typed_ir_result(
            diagram_type,
            ir,
            experimental=experimental,
        )
        if initial.emitted_type != "architecture":
            return None
        if diagram_type == "architecture":
            code = serialize_architecture_flowchart_fallback(
                ir,
                experimental=experimental,
                accessibility_type=diagram_type,
            )
        else:
            from marker_mermaid.serializers_phase2 import serialize_phase2

            code, emitted_type, _reason = serialize_phase2(
                diagram_type,
                ir,
                experimental=experimental,
                native_runtime_valid=False,
            )
            if emitted_type != "flowchart":
                return None
        runtime_warning = (
            "CandidateValidator rejected architecture-beta; service/group labels and "
            "unlabeled endpoint topology were re-emitted as portable Flowchart while "
            "architecture icons, ports, and relation labels remain in typed IR."
        )
        return SerializationResult.fallback(
            diagram_type,
            "flowchart",
            code,
            via=initial.fallback_chain[1:],
            warnings=tuple(dict.fromkeys((*initial.warnings, runtime_warning))),
            stability=initial.stability,
        )
    if diagram_type in {"treemap", "venn"}:
        from marker_mermaid.serializers_charts_sets import serialize_chart_set

        code, emitted_type, reason = serialize_chart_set(
            diagram_type,
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        if emitted_type == diagram_type:
            return None
        return SerializationResult.fallback(
            diagram_type,
            emitted_type,
            code,
            warnings=(reason or f"CandidateValidator rejected native {diagram_type}.",),
            stability="experimental",
        )
    if diagram_type in {"packet", "ishikawa", "treeview"}:
        from marker_mermaid.serializers_special import serialize_special

        result = serialize_special(
            diagram_type,
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        return result if result.used_fallback else None
    if diagram_type == "organization":
        from marker_mermaid.serializers_experimental import serialize_organization

        result = serialize_organization(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        return result if result.emitted_type == "flowchart" else None
    return None


def serialize_typed_ir(diagram_type: str, ir: dict[str, Any], *, experimental: bool = False) -> str:
    try:
        return serialize_typed_ir_result(
            diagram_type,
            ir,
            experimental=experimental,
        ).code
    except ValueError as exc:
        if isinstance(exc, SerializationError):
            raise
        raise SerializationError(str(exc)) from exc


def scene_to_flowchart(
    scene: DiagramSceneIR,
    *,
    experimental: bool = False,
    accessibility_type: str = "flowchart",
) -> str:
    """Losslessly map resolvable scene nodes/edges to the portable flowchart subset."""

    nodes = [
        {"id": element.id, "label": element.text or "[unreadable]", "shape": element.shape}
        for element in scene.elements
    ]
    edges = [
        {
            "source": relation.source_id,
            "target": relation.target_id,
            "label": relation.label,
            "style": relation.line_style,
            "bidirectional": relation.arrow_at_start and relation.arrow_at_end,
        }
        for relation in scene.relations
        if relation.source_id is not None and relation.target_id is not None
    ]
    ir = enrich_accessibility_ir(
        {
            "nodes": nodes,
            "edges": edges,
            "direction": scene.reading_direction,
        },
        accessibility_type,
        experimental=experimental,
    )
    return serialize_flowchart(ir, experimental=experimental)
