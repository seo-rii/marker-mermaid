"""Deterministic serializers from typed/common IR to Mermaid text."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from marker_mermaid.models import DiagramSceneIR


class SerializationError(ValueError):
    """Raised when an IR cannot be represented without inventing information."""


def _identifier(value: str, fallback: str = "node") -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"n_{normalized}"
    return normalized


def _text(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', "&quot;").replace("\n", " ").strip()


def _accessibility(ir: dict[str, Any], experimental: bool = False) -> list[str]:
    title = ir.get("acc_title") or ir.get("title")
    description = ir.get("acc_description") or ir.get("description")
    if experimental and description:
        description = f"{description} This reconstruction is experimental."
    elif experimental:
        description = "This diagram is an experimental reconstruction and requires review."
    lines: list[str] = []
    if title:
        lines.append(f"    accTitle: {_text(title)}")
    if description:
        lines.append(f"    accDescr: {_text(description)}")
    return lines


def serialize_flowchart(ir: dict[str, Any], *, experimental: bool = False) -> str:
    nodes = ir.get("nodes")
    edges = ir.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        raise SerializationError("flowchart IR requires at least one node")
    direction = ir.get("direction", "TB")
    if direction not in {"TB", "BT", "LR", "RL"}:
        direction = "TB"
    lines = [f"flowchart {direction}", *_accessibility(ir, experimental)]
    ids: set[str] = set()
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
    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            raise SerializationError("flowchart nodes must be objects")
        source_id = str(node.get("id") or f"N{index}")
        node_id = _identifier(source_id, f"N{index}")
        suffix = 2
        base = node_id
        while node_id in ids:
            node_id = f"{base}_{suffix}"
            suffix += 1
        ids.add(node_id)
        id_map[source_id] = node_id
        label = _text(node.get("label") or node.get("text") or "[unreadable]")
        shape = str(node.get("shape") or "rectangle").lower()
        if shape == "round":
            start, end = shapes[shape]
        else:
            start = shapes.get(shape, '["')
            end = shape_ends.get(shape, '"]')
        lines.append(f"    {node_id}{start}{label}{end}")
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
    groups = ir.get("groups", [])
    if groups:
        lines.append("    %% Groups are retained in typed IR; nested layout requires review.")
    return "\n".join(lines) + "\n"


def serialize_swimlane(ir: dict[str, Any], *, experimental: bool = False) -> str:
    lanes = ir.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise SerializationError("swimlane IR requires lanes")
    flat_nodes: list[dict[str, Any]] = []
    for lane in lanes:
        flat_nodes.extend(lane.get("nodes", []))
    flow_ir = {**ir, "nodes": flat_nodes, "edges": ir.get("edges", [])}
    base = serialize_flowchart(flow_ir, experimental=experimental).splitlines()
    declaration = base[: 1 + len(_accessibility(ir, experimental))]
    node_lines = {
        re.match(r"\s+([A-Za-z0-9_]+)", line).group(1): line
        for line in base[len(declaration) :]
        if re.match(r"\s+([A-Za-z0-9_]+)[\[({]", line)
    }
    output = declaration
    for index, lane in enumerate(lanes, start=1):
        lane_id = _identifier(str(lane.get("id") or f"lane_{index}"))
        output.append(f'    subgraph {lane_id}["{_text(lane.get("label") or lane_id)}"]')
        for node in lane.get("nodes", []):
            node_id = _identifier(str(node.get("id")))
            if node_id in node_lines:
                output.append("    " + node_lines[node_id])
        output.append("    end")
    output.extend(line for line in base[len(declaration) :] if "-->" in line or "-.->" in line)
    return "\n".join(output) + "\n"


def serialize_sequence(ir: dict[str, Any], *, experimental: bool = False) -> str:
    participants = ir.get("participants")
    messages = ir.get("messages", [])
    if not isinstance(participants, list) or not participants:
        raise SerializationError("sequence IR requires participants")
    lines = ["sequenceDiagram", *_accessibility(ir, experimental)]
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
    lines = ["timeline", *_accessibility(ir, experimental)]
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
    lines = ["gantt", *_accessibility(ir, experimental)]
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
    lines = ["architecture-beta", *_accessibility(ir, experimental)]
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


def serialize_typed_ir(diagram_type: str, ir: dict[str, Any], *, experimental: bool = False) -> str:
    serializer = SERIALIZERS.get(diagram_type)
    if serializer is None:
        raise SerializationError(f"no typed serializer for {diagram_type}")
    return serializer(ir, experimental=experimental)


def scene_to_flowchart(scene: DiagramSceneIR, *, experimental: bool = False) -> str:
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
    return serialize_flowchart(
        {
            "nodes": nodes,
            "edges": edges,
            "direction": scene.reading_direction,
            "description": "A reconstruction generated from geometry-aware scene evidence.",
        },
        experimental=experimental,
    )
