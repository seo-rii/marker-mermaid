"""Deterministic serializers from typed/common IR to Mermaid text."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from marker_mermaid.accessibility import (
    accessibility_limitation_warning,
    enrich_accessibility_ir,
    resolve_accessibility,
    supports_accessibility_directives,
)
from marker_mermaid.flowchart_structure import (
    FlowchartGroupPlacement,
    FlowchartNodePlacement,
    FlowchartStructureError,
    MindmapStructureError,
    SequenceStructureError,
    plan_flowchart_structure,
    plan_mindmap_nodes,
    plan_sequence_structure,
    portable_identifier,
    prepare_swimlane_structure,
)
from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_GROUPS,
    MAX_SCENE_RELATIONS,
    MAX_TEXT_CHARS,
    DiagramSceneIR,
)
from marker_mermaid.serialization import SerializationResult, registry_from_string_serializers


class SerializationError(ValueError):
    """Raised when an IR cannot be represented without inventing information."""


@dataclass(frozen=True, slots=True)
class ArchitectureStructurePlan:
    """Canonical service/group identities shared by both Architecture grammars."""

    services: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...]
    edges: tuple[dict[str, Any], ...]
    nodes: tuple[FlowchartNodePlacement, ...]
    group_placements: tuple[FlowchartGroupPlacement, ...]


def plan_architecture_structure(ir: dict[str, Any]) -> ArchitectureStructurePlan:
    """Plan the exact bounded identities visible in native and fallback output."""

    raw_services = ir.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise SerializationError("architecture IR requires services")
    raw_groups = ir.get("groups", [])
    if not isinstance(raw_groups, list):
        raise SerializationError("architecture groups must be a list")
    if len(raw_groups) > MAX_SCENE_GROUPS:
        raise SerializationError("architecture group count exceeds the Scene group limit")

    group_records: list[dict[str, Any]] = []
    group_members: dict[str, list[str]] = {}
    for index, group in enumerate(raw_groups, start=1):
        if not isinstance(group, dict):
            raise SerializationError("architecture groups must be objects")
        group_id = str(group.get("id") or f"G{index}")
        if group_id in group_members:
            raise SerializationError("architecture group ids must be unique")
        emitted_group_id = portable_identifier(group_id, f"G{index}")
        label = group.get("label") or emitted_group_id
        if not isinstance(label, str) or len(label) > MAX_TEXT_CHARS:
            raise SerializationError("architecture group label must be a bounded string")
        group_records.append(
            {
                **group,
                "id": group_id,
                "label": label,
                "member_ids": group_members.setdefault(group_id, []),
            }
        )

    services: list[dict[str, Any]] = []
    service_ids: set[str] = set()
    for index, service in enumerate(raw_services, start=1):
        if not isinstance(service, dict):
            raise SerializationError("architecture services must be objects")
        service_id = str(service.get("id") or f"S{index}")
        if service_id in service_ids:
            raise SerializationError("architecture service ids must be unique")
        service_ids.add(service_id)
        services.append(
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

    nonempty_groups = [
        {
            "id": group["id"],
            "label": group["label"],
            "member_ids": list(group["member_ids"]),
        }
        for group in group_records
        if group["member_ids"]
    ]
    try:
        flowchart_plan = plan_flowchart_structure(services, nonempty_groups)
    except FlowchartStructureError as exc:
        raise SerializationError(str(exc)) from exc
    nonempty_placements = {group.source_id: group for group in flowchart_plan.groups}
    occupied_ids = {node.emitted_id for node in flowchart_plan.nodes}
    occupied_group_ids: set[str] = set()
    group_placements: list[FlowchartGroupPlacement] = []
    emitted_node_by_source = {node.source_id: node.emitted_id for node in flowchart_plan.nodes}
    for index, group in enumerate(group_records, start=1):
        group_id = str(group["id"])
        placement = nonempty_placements.get(group_id)
        if placement is None:
            emitted_group_id = portable_identifier(group_id, f"G{index}")
            if not group_id or len(group_id) > MAX_ID_CHARS:
                raise SerializationError("architecture group requires a bounded non-empty id")
            if len(emitted_group_id) > MAX_ID_CHARS:
                raise SerializationError(
                    "architecture emitted group id exceeds the identifier limit"
                )
            placement = FlowchartGroupPlacement(
                source_id=group_id,
                emitted_id=emitted_group_id,
                label=str(group["label"]),
                member_source_ids=(),
                member_emitted_ids=(),
            )
        if placement.emitted_id in occupied_ids:
            raise SerializationError("architecture group id collides with a service id")
        if placement.emitted_id in occupied_group_ids:
            raise SerializationError("architecture group ids must be unique after normalization")
        occupied_group_ids.add(placement.emitted_id)
        group_placements.append(placement)

    raw_edges = ir.get("edges", [])
    if not isinstance(raw_edges, list):
        raise SerializationError("architecture edges must be a list")
    if len(raw_edges) > MAX_SCENE_RELATIONS:
        raise SerializationError("architecture edge count exceeds the Scene relation limit")
    edges: list[dict[str, Any]] = []
    for edge in raw_edges:
        if not isinstance(edge, dict):
            raise SerializationError("architecture edges must be objects")
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source not in emitted_node_by_source or target not in emitted_node_by_source:
            raise SerializationError(
                f"architecture edge references unknown endpoint: {source!r} -> {target!r}"
            )
        edges.append({**edge, "source": source, "target": target})

    return ArchitectureStructurePlan(
        tuple(services),
        tuple(group_records),
        tuple(edges),
        flowchart_plan.nodes,
        tuple(group_placements),
    )


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
        "round": ('("', '")'),
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
        node_declarations.append((source_id, node_id, f"    {node_id}{start}{label}{end}"))

    grouped_source_ids = {
        member for group in structure.groups for member in group.member_source_ids
    }

    declaration_by_source = {source_id: line for source_id, _node_id, line in node_declarations}
    for group in structure.groups:
        lines.append(f'    subgraph {group.emitted_id}["{_text(group.label)}"]')
        lines.extend(f"    {declaration_by_source[member]}" for member in group.member_source_ids)
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
        connector = f"{arrow}|{_text(label)}|" if label else arrow
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
    try:
        structure = plan_sequence_structure(participants, messages)
    except SequenceStructureError as exc:
        raise SerializationError(str(exc)) from exc
    lines = [
        "sequenceDiagram",
        *_accessibility(ir, experimental, diagram_type="sequence"),
    ]
    for participant in structure.participants:
        lines.append(f"    participant {participant.emitted_id} as {_text(participant.label)}")
    arrows = {
        "solid": "->>",
        "dotted": "-->>",
        "open": "->",
        "dotted_open": "-->",
        "cross": "-x",
    }
    for message in structure.messages:
        arrow = arrows.get(message.source.get("style"), "->>")
        lines.append(
            f"    {message.source_id}{arrow}{message.target_id}: "
            f"{_text(message.source.get('label') or '[unreadable]')}"
        )
    return "\n".join(lines) + "\n"


def serialize_mindmap(ir: dict[str, Any], *, experimental: bool = False) -> str:
    root = ir.get("root")
    try:
        node_plan = plan_mindmap_nodes(root)
    except MindmapStructureError as exc:
        raise SerializationError(str(exc)) from exc
    # Mermaid 11.16 parses accTitle/accDescr as additional roots in mindmaps.
    # Preserve the hard render gate and keep the requested text in typed IR until
    # upstream supports accessible directives for this grammar.
    lines = ["mindmap"]
    for node in node_plan:
        label = _text(node.label)
        if node.depth == 1:
            lines.append(f"{'    ' * node.depth}{node.emitted_id}(({label}))")
        else:
            lines.append(f'{"    " * node.depth}{node.emitted_id}["{label}"]')
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


_GANTT_ZERO_WIDTH_SPACE = "\u200b"
_GANTT_METADATA_FIELDS = (
    "title",
    "description",
    "acc_title",
    "acc_description",
    "date_format",
)
_GANTT_ACCESSIBILITY_FIELDS = (
    "title",
    "description",
    "acc_title",
    "acc_description",
)
_GANTT_NUMERIC_ENTITY = re.compile(r"&(?P<body>#[0-9]+|#x[0-9A-F]+);", re.IGNORECASE)
_GANTT_DIRECTIVE_OPEN = re.compile(r"%%(?P<gap>\s*)\{")
_GANTT_DANGEROUS_SCHEME = re.compile(r"(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_GANTT_REMOTE_ICON = re.compile(r"iconify|fa:|logos:", re.IGNORECASE)
_GANTT_CALLBACK = re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE)
_GANTT_CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)
_GANTT_CONFIG = re.compile(r"\bconfig\s*:", re.IGNORECASE)
_GANTT_CONTROL_WORD = re.compile(
    r"\b(?:accDescription|accDescr|accTitle|axisFormat|call|callback|classDef|click|"
    r"dateFormat|excludes|gantt|href|includes|inclusiveEndDates|linkStyle|section|"
    r"style|tickInterval|title|todayMarker|topAxis|weekday|weekend)\b",
    re.IGNORECASE,
)
_GANTT_DATE_PREFIX = re.compile(r"^(?P<first>[0-9])(?=[0-9]{3}-[0-9]{2}-[0-9]{2}\b)")
_GANTT_STATUS_TOKENS = frozenset({"active", "crit", "done", "milestone"})
_GANTT_RUNTIME_TASK_TAGS = frozenset({"active", "done", "crit", "milestone", "vert"})
_GANTT_RESERVED_TASK_IDS = frozenset({"__proto__"})
_GANTT_DURATION = re.compile(r"(?P<value>[0-9]+(?:\.[0-9]+)?)(?P<unit>ms|[Mdhmswy])")
_GANTT_DURATION_MS_PER_UNIT = {
    "ms": Decimal(1),
    "s": Decimal(1_000),
    "m": Decimal(60_000),
    "h": Decimal(3_600_000),
    "d": Decimal(86_400_000),
    "w": Decimal(604_800_000),
    "M": Decimal(2_678_400_000),
    "y": Decimal(31_622_400_000),
}
_GANTT_INTEGER_DURATION_UNITS = frozenset({"ms", "d", "w", "M", "y"})
_GANTT_MAX_DURATION_MILLISECONDS = Decimal(86_400_000_000_000)
_GANTT_MAX_EPOCH_MILLISECONDS = Decimal(8_640_000_000_000_000)
_GANTT_AFTER_DEPENDENCY = re.compile(r"after (?P<ids>[A-Za-z0-9_-]+(?: [A-Za-z0-9_-]+)*)")
_GANTT_DEPENDENCY_PREFIX = re.compile(r"(?:after|until)\b", re.IGNORECASE)
_GANTT_DATE_RUNS = frozenset({"Y", "M", "D", "H", "h", "m", "s", "S", "Z"})

GANTT_TEXT_COMPATIBILITY_WARNING = (
    "Gantt text uses visible ratio-colon (∶), fullwidth percent (％), or single-angle "
    "(‹) glyphs where Mermaid 11.16 cannot preserve task, title, or accessibility "
    "text literally; semantic text remains in typed IR."
)


@dataclass(frozen=True, slots=True)
class GanttTextPlan:
    semantic: str
    source: str
    canvas: str
    compatibility_substitutions: bool


def validated_gantt_metadata_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw Gantt metadata before generic accessibility enrichment."""

    for field in _GANTT_METADATA_FIELDS:
        value = ir.get(field)
        if value is None:
            continue
        if type(value) is not str:
            raise SerializationError(f"gantt {field} must be text when provided")
        if value == "":
            continue
        if len(value) > MAX_TEXT_CHARS:
            raise SerializationError(f"gantt {field} must be bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value
        ):
            raise SerializationError(f"gantt {field} contains unsupported text")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > MAX_TEXT_CHARS:
            raise SerializationError(f"gantt {field} must be bounded non-empty text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
            raise SerializationError(f"gantt {field} is not valid UTF-8 text") from exc
    date_format = ir.get("date_format")
    if date_format is not None and date_format != "":
        _gantt_python_date_format(date_format, field="gantt date_format")
    return {
        key: value for key, value in ir.items() if key not in _GANTT_METADATA_FIELDS or value != ""
    }


def _gantt_semantic_text(value: Any, *, field: str) -> str:
    if type(value) is not str:
        raise SerializationError(f"{field} must be text")
    if len(value) > MAX_TEXT_CHARS:
        raise SerializationError(f"{field} must be bounded non-empty text")
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cf", "Cs"} or (category == "Cc" and not character.isspace()):
            raise SerializationError(f"{field} contains unsupported text")
    semantic = " ".join(value.split())
    if not semantic or len(semantic) > MAX_TEXT_CHARS:
        raise SerializationError(f"{field} must be bounded non-empty text")
    try:
        semantic.encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
        raise SerializationError(f"{field} is not valid UTF-8 text") from exc
    return semantic


def _split_gantt_numeric_entity(match: re.Match[str]) -> str:
    body = match.group("body")
    if body[:2].casefold() == "#x":
        return f"&{body[:2]}{_GANTT_ZERO_WIDTH_SPACE}{body[2:]};"
    return f"&#{_GANTT_ZERO_WIDTH_SPACE}{body[1:]};"


def _neutralize_gantt_source(text: str, *, task_label: bool) -> str:
    source = _GANTT_NUMERIC_ENTITY.sub(_split_gantt_numeric_entity, text)
    source = _GANTT_DIRECTIVE_OPEN.sub(
        lambda match: f"%{_GANTT_ZERO_WIDTH_SPACE}%{match.group('gap')}{{",
        source,
    )
    source = source.replace("//", f"/{_GANTT_ZERO_WIDTH_SPACE}/")
    source = source.replace("<", f"<{_GANTT_ZERO_WIDTH_SPACE}")
    source = _GANTT_CSS_IMPORT.sub(
        lambda match: f"{match.group(0)[:1]}{_GANTT_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
        source,
    )
    source = _GANTT_DANGEROUS_SCHEME.sub(
        lambda match: f"{match.group(0)[:-1]}{_GANTT_ZERO_WIDTH_SPACE}:",
        source,
    )
    source = _GANTT_REMOTE_ICON.sub(
        lambda match: (
            f"{match.group(0)[:4]}{_GANTT_ZERO_WIDTH_SPACE}{match.group(0)[4:]}"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", f"{_GANTT_ZERO_WIDTH_SPACE}:")
        ),
        source,
    )
    source = _GANTT_CALLBACK.sub(
        lambda match: match.group(0).replace("(", f"{_GANTT_ZERO_WIDTH_SPACE}("),
        source,
    )
    source = _GANTT_CONTROL_WORD.sub(
        lambda match: f"{match.group(0)[0]}{_GANTT_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
        source,
    )
    source = _GANTT_CONFIG.sub(
        lambda match: match.group(0).replace(":", f"{_GANTT_ZERO_WIDTH_SPACE}:"),
        source,
    )
    source = source.replace("---", f"-{_GANTT_ZERO_WIDTH_SPACE}--")
    if task_label:
        source = _GANTT_DATE_PREFIX.sub(
            lambda match: f"{match.group('first')}{_GANTT_ZERO_WIDTH_SPACE}",
            source,
        )
    return source


def _plan_gantt_text(value: Any, *, field: str, role: str) -> GanttTextPlan:
    semantic = _gantt_semantic_text(value, field=field)
    canvas = semantic
    if role == "task":
        canvas = canvas.replace(":", "∶").replace("%", "％")
    elif role in {"title", "accessibility"}:
        canvas = canvas.replace("<", "‹")
    source = _neutralize_gantt_source(canvas, task_label=role == "task")
    terminal_canvas = source.replace(_GANTT_ZERO_WIDTH_SPACE, "")
    return GanttTextPlan(
        semantic=semantic,
        source=source,
        canvas=terminal_canvas,
        compatibility_substitutions=terminal_canvas != semantic,
    )


def _gantt_optional_id(value: Any, *, fallback: str, field: str) -> str:
    if value is None or (type(value) is str and value == ""):
        return fallback
    text = _gantt_semantic_text(value, field=field)
    if len(text) > MAX_ID_CHARS:
        raise SerializationError(f"{field} must be a bounded identifier")
    return text


def _gantt_schedule_text(value: Any, *, field: str, identifier: bool = False) -> str:
    text = _gantt_semantic_text(value, field=field)
    if any(character in text for character in (",", "#", ";")):
        raise SerializationError(f"{field} contains unsupported schedule syntax")
    if identifier and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", text) is None:
        raise SerializationError(f"{field} cannot be represented as a Gantt task id")
    return text


def _gantt_python_date_format(value: Any, *, field: str) -> tuple[str, str, str]:
    """Compile the supported numeric Day.js date subset to ``strptime`` syntax."""

    text = _gantt_schedule_text(value, field=field)
    if text == "X":
        raise SerializationError(
            f"{field} token X is not supported because Mermaid 11.16 parses its start and end "
            "values with different units"
        )
    if text == "x":
        return text, text, r"(?:0|[1-9][0-9]*)"
    output: list[str] = []
    shape: list[str] = []
    has_year = False
    has_month = False
    has_day = False
    uses_12_hour = False
    uses_meridiem = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == "[":
            closing = text.find("]", index + 1)
            if closing < 0:
                raise SerializationError(f"{field} contains an unterminated literal")
            literal = text[index + 1 : closing]
            output.append(literal.replace("%", "%%"))
            shape.append(re.escape(literal))
            index = closing + 1
            continue
        if character in _GANTT_DATE_RUNS:
            end = index + 1
            while end < len(text) and text[end] == character:
                end += 1
            token = text[index:end]
            token_map = {
                "YY": "%y",
                "YYYY": "%Y",
                "M": "%m",
                "MM": "%m",
                "D": "%d",
                "DD": "%d",
                "H": "%H",
                "HH": "%H",
                "h": "%I",
                "hh": "%I",
                "m": "%M",
                "mm": "%M",
                "s": "%S",
                "ss": "%S",
                "SSS": "%f",
            }
            token_shape = {
                "YY": r"[0-9]{2}",
                "YYYY": r"[0-9]{4}",
                "M": r"(?:[1-9]|1[0-2])",
                "MM": r"[0-9]{2}",
                "D": r"(?:[1-9]|[12][0-9]|3[01])",
                "DD": r"[0-9]{2}",
                "H": r"(?:[0-9]|1[0-9]|2[0-3])",
                "HH": r"[0-9]{2}",
                "h": r"(?:[1-9]|1[0-2])",
                "hh": r"[0-9]{2}",
                "m": r"(?:[0-9]|[1-5][0-9])",
                "mm": r"[0-9]{2}",
                "s": r"(?:[0-9]|[1-5][0-9])",
                "ss": r"[0-9]{2}",
                "SSS": r"[0-9]{3}",
            }
            converted = token_map.get(token)
            converted_shape = token_shape.get(token)
            if converted is None or converted_shape is None:
                raise SerializationError(f"{field} uses an unsupported date token: {token}")
            output.append(converted)
            shape.append(converted_shape)
            has_year |= character == "Y"
            has_month |= character == "M"
            has_day |= character == "D"
            uses_12_hour |= character == "h"
            index = end
            continue
        if character in {"A", "a"}:
            output.append("%p")
            shape.append(r"[AP]M" if character == "A" else r"[ap]m")
            uses_meridiem = True
        elif character == "T":
            output.append("T")
            shape.append("T")
        elif character.isalpha():
            raise SerializationError(f"{field} uses an unsupported date token: {character}")
        else:
            output.append("%%" if character == "%" else character)
            shape.append(re.escape(character))
        index += 1
    if not (has_year and has_month and has_day):
        raise SerializationError(f"{field} must include year, month, and day tokens")
    if uses_12_hour != uses_meridiem:
        raise SerializationError(f"{field} must pair h/hh with A/a")
    return text, "".join(output), "".join(shape)


def _gantt_status_fields(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None or (type(value) is str and value == ""):
        return ()
    text = _gantt_semantic_text(value, field=field)
    raw_tokens = text.split(",")
    tokens = tuple(token.strip().casefold() for token in raw_tokens)
    if (
        any(not token for token in tokens)
        or any(token not in _GANTT_STATUS_TOKENS for token in tokens)
        or len(tokens) != len(set(tokens))
    ):
        raise SerializationError(f"{field} contains unsupported status tokens")
    if "active" in tokens and "done" in tokens:
        raise SerializationError(f"{field} cannot combine active and done")
    return tokens


@dataclass(frozen=True, slots=True)
class GanttTaskPlan:
    source_record: dict[str, Any]
    scene_id: str
    semantic_label: str
    visible_label: str
    code_label: str
    fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GanttSectionPlan:
    source_record: dict[str, Any]
    scene_id: str
    semantic_label: str
    visible_label: str
    code_label: str
    tasks: tuple[GanttTaskPlan, ...]


@dataclass(frozen=True, slots=True)
class GanttPlan:
    sections: tuple[GanttSectionPlan, ...]
    title: GanttTextPlan | None
    compatibility_substitutions: bool


@dataclass(frozen=True, slots=True)
class GanttAccessibilityPlan:
    title_semantic: str
    title_source: str
    title_canvas: str
    description_semantic: str
    description_source: str
    description_canvas: str
    compatibility_substitutions: bool


def plan_gantt_records(ir: dict[str, Any]) -> GanttPlan:
    """Freeze Gantt terminal text and allocate collision-free Scene identities."""

    validated_ir = validated_gantt_metadata_ir(ir)
    sections = validated_ir.get("sections")
    if not isinstance(sections, list) or not sections:
        raise SerializationError("gantt IR requires sections")
    if len(sections) > MAX_SCENE_GROUPS:
        raise SerializationError("gantt section count exceeds the Scene group limit")
    raw_date_format = validated_ir.get("date_format")
    _, python_date_format, date_value_shape = _gantt_python_date_format(
        "YYYY-MM-DD" if raw_date_format is None else raw_date_format,
        field="gantt date_format",
    )
    task_ids_by_position: dict[tuple[int, int], str] = {}
    task_order_by_id: dict[str, int] = {}
    all_task_ids: set[str] = set()
    task_count = 0
    for section_index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise SerializationError("gantt sections must be objects")
        tasks = section.get("tasks", [])
        if not isinstance(tasks, list):
            raise SerializationError("gantt section tasks must be a list")
        task_count += len(tasks)
        if task_count > MAX_SCENE_ELEMENTS:
            raise SerializationError("gantt task count exceeds the Scene element limit")
        for task_index, task in enumerate(tasks, start=1):
            if not isinstance(task, dict):
                raise SerializationError("gantt tasks must be objects")
            task_id = _gantt_optional_id(
                task.get("id"),
                fallback=f"section_{section_index}_task_{task_index}",
                field=f"gantt section {section_index} task {task_index} id",
            )
            task_id = _gantt_schedule_text(
                task_id,
                field=f"gantt section {section_index} task {task_index} id",
                identifier=True,
            )
            folded_task_id = task_id.casefold()
            if (
                folded_task_id in _GANTT_RUNTIME_TASK_TAGS
                or folded_task_id in _GANTT_RESERVED_TASK_IDS
                or "iconify" in folded_task_id
            ):
                raise SerializationError(
                    f"gantt section {section_index} task {task_index} task id is reserved"
                )
            if task_id in all_task_ids:
                raise SerializationError(f"duplicate gantt task id: {task_id}")
            all_task_ids.add(task_id)
            task_order_by_id[task_id] = len(task_order_by_id)
            task_ids_by_position[(section_index, task_index)] = task_id
    raw_title = validated_ir.get("title")
    title_plan = (
        _plan_gantt_text(raw_title, field="gantt title", role="title")
        if raw_title is not None
        else None
    )
    used_scene_ids: set[str] = set()
    planned_sections: list[GanttSectionPlan] = []
    task_end_milliseconds_by_id: dict[str, Decimal] = {}
    task_count = 0
    compatibility_substitutions = bool(
        title_plan is not None and title_plan.compatibility_substitutions
    )
    for section_index, section in enumerate(sections, start=1):
        tasks = section.get("tasks", [])
        if not tasks:
            continue
        preferred_section_id = _gantt_optional_id(
            section.get("id"),
            fallback=f"section_{section_index}",
            field=f"gantt section {section_index} id",
        )
        section_scene_id = preferred_section_id
        if (
            not section_scene_id
            or len(section_scene_id) > MAX_ID_CHARS
            or section_scene_id in used_scene_ids
        ):
            section_scene_id = f"gantt_section_{section_index}"
            suffix = 2
            while section_scene_id in used_scene_ids:
                section_scene_id = f"gantt_section_{section_index}_{suffix}"
                suffix += 1
        used_scene_ids.add(section_scene_id)
        raw_section_label = section.get("title")
        section_label = (
            "Tasks"
            if raw_section_label is None
            or (type(raw_section_label) is str and raw_section_label == "")
            else raw_section_label
        )
        section_text = _plan_gantt_text(
            section_label,
            field=f"gantt section {section_index} title",
            role="section",
        )
        compatibility_substitutions |= section_text.compatibility_substitutions
        planned_tasks: list[GanttTaskPlan] = []
        for task_index, task in enumerate(tasks, start=1):
            raw_task_label = task.get("label")
            task_label = (
                f"Task {task_index}"
                if raw_task_label is None or (type(raw_task_label) is str and raw_task_label == "")
                else raw_task_label
            )
            task_text = _plan_gantt_text(
                task_label,
                field=f"gantt section {section_index} task {task_index} label",
                role="task",
            )
            compatibility_substitutions |= task_text.compatibility_substitutions
            task_id = task_ids_by_position[(section_index, task_index)]
            status_fields = _gantt_status_fields(
                task.get("status"),
                field=f"gantt section {section_index} task {task_index} status",
            )
            start = _gantt_schedule_text(
                task.get("start"),
                field=f"gantt section {section_index} task {task_index} start",
            )
            after_match = _GANTT_AFTER_DEPENDENCY.fullmatch(start)
            start_dependencies: tuple[str, ...] = ()
            start_date: datetime | Decimal | None = None
            if after_match is not None:
                start_dependencies = tuple(after_match.group("ids").split(" "))
                if len(start_dependencies) != len(set(start_dependencies)):
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} dependency repeats "
                        "a target"
                    )
                missing_dependencies = [
                    target for target in start_dependencies if target not in all_task_ids
                ]
                if missing_dependencies:
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} dependency target "
                        "does not exist"
                    )
                if any(
                    task_order_by_id[target] >= task_order_by_id[task_id]
                    for target in start_dependencies
                ):
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} dependency must "
                        "reference a prior task"
                    )
                if python_date_format == "x":
                    start_date = max(
                        task_end_milliseconds_by_id[target] for target in start_dependencies
                    )
            else:
                if _GANTT_DEPENDENCY_PREFIX.match(start):
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} dependency syntax "
                        "is unsupported"
                    )
                try:
                    if python_date_format == "x":
                        if re.fullmatch(date_value_shape, start) is None:
                            raise ValueError
                        start_date = Decimal(start)
                        if start_date > _GANTT_MAX_EPOCH_MILLISECONDS:
                            raise ValueError
                    else:
                        if re.fullmatch(date_value_shape, start) is None:
                            raise ValueError
                        start_date = datetime.strptime(start, python_date_format)
                except (ValueError, ArithmeticError) as exc:
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} start date is invalid"
                    ) from exc
            raw_end = task.get("end")
            raw_duration = task.get("duration")
            has_end = raw_end is not None and not (type(raw_end) is str and raw_end == "")
            has_duration = raw_duration is not None and not (
                type(raw_duration) is str and raw_duration == ""
            )
            if has_end == has_duration:
                raise SerializationError(
                    f"gantt section {section_index} task {task_index} requires exactly one of "
                    "end or duration"
                )
            end = _gantt_schedule_text(
                raw_end if has_end else raw_duration,
                field=(
                    f"gantt section {section_index} task {task_index} "
                    f"{'end' if has_end else 'duration'}"
                ),
            )
            duration_milliseconds: Decimal | None = None
            end_date: datetime | Decimal | None = None
            if has_duration:
                duration_match = _GANTT_DURATION.fullmatch(end)
                if duration_match is None:
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} duration is invalid"
                    )
                duration_value = Decimal(duration_match.group("value"))
                duration_unit = duration_match.group("unit")
                if (
                    duration_unit in _GANTT_INTEGER_DURATION_UNITS
                    and duration_value != duration_value.to_integral_value()
                ):
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} duration would be "
                        "rounded by Mermaid"
                    )
                duration_milliseconds = duration_value * _GANTT_DURATION_MS_PER_UNIT[duration_unit]
                if duration_milliseconds != duration_milliseconds.to_integral_value():
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} duration is below "
                        "Mermaid's millisecond precision"
                    )
                if duration_milliseconds > _GANTT_MAX_DURATION_MILLISECONDS:
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} duration exceeds "
                        "the bounded runtime range"
                    )
                if duration_milliseconds == 0 and "milestone" not in status_fields:
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} duration must be positive"
                    )
            else:
                if _GANTT_DEPENDENCY_PREFIX.match(end):
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} until dependency is not "
                        "supported without relation attribution"
                    )
                if start_dependencies:
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} end date cannot be "
                        "validated with an after dependency; use duration"
                    )
                try:
                    if python_date_format == "x":
                        if re.fullmatch(date_value_shape, end) is None:
                            raise ValueError
                        end_date = Decimal(end)
                        if end_date > _GANTT_MAX_EPOCH_MILLISECONDS:
                            raise ValueError
                    else:
                        if re.fullmatch(date_value_shape, end) is None:
                            raise ValueError
                        end_date = datetime.strptime(end, python_date_format)
                except (ValueError, ArithmeticError) as exc:
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} end date is invalid"
                    ) from exc
                if start_date is None:  # pragma: no cover - guarded by start_dependencies above
                    raise SerializationError("gantt task start date could not be validated")
                if end_date < start_date or (
                    end_date == start_date and "milestone" not in status_fields
                ):
                    raise SerializationError(
                        f"gantt section {section_index} task {task_index} end date must follow "
                        "start"
                    )
            if python_date_format == "x":
                if not isinstance(start_date, Decimal):  # pragma: no cover - internal invariant
                    raise SerializationError("gantt task start date could not be resolved")
                if has_duration:
                    if duration_milliseconds is None:  # pragma: no cover - internal invariant
                        raise SerializationError("gantt task duration could not be resolved")
                    resolved_end = start_date + duration_milliseconds
                    if resolved_end > _GANTT_MAX_EPOCH_MILLISECONDS:
                        raise SerializationError(
                            f"gantt section {section_index} task {task_index} resolved end "
                            "exceeds the ECMAScript Date range"
                        )
                else:
                    if not isinstance(end_date, Decimal):  # pragma: no cover - invariant
                        raise SerializationError("gantt task end date could not be resolved")
                    resolved_end = end_date
                task_end_milliseconds_by_id[task_id] = resolved_end
            task_scene_id = task_id
            if (
                not task_scene_id
                or len(task_scene_id) > MAX_ID_CHARS
                or task_scene_id in used_scene_ids
            ):
                task_scene_id = f"gantt_task_{section_index}_{task_index}"
                suffix = 2
                while task_scene_id in used_scene_ids:
                    task_scene_id = f"gantt_task_{section_index}_{task_index}_{suffix}"
                    suffix += 1
            used_scene_ids.add(task_scene_id)
            planned_tasks.append(
                GanttTaskPlan(
                    source_record=task,
                    scene_id=task_scene_id,
                    semantic_label=task_text.semantic,
                    visible_label=task_text.canvas,
                    code_label=task_text.source,
                    fields=(*status_fields, task_id, start, end),
                )
            )
        planned_sections.append(
            GanttSectionPlan(
                source_record=section,
                scene_id=section_scene_id,
                semantic_label=section_text.semantic,
                visible_label=section_text.canvas,
                code_label=section_text.source,
                tasks=tuple(planned_tasks),
            )
        )
    if not planned_sections:
        raise SerializationError("gantt IR requires at least one renderable task")
    return GanttPlan(
        sections=tuple(planned_sections),
        title=title_plan,
        compatibility_substitutions=compatibility_substitutions,
    )


def plan_gantt_accessibility(
    ir: dict[str, Any],
    *,
    experimental: bool,
    gantt_plan: GanttPlan | None = None,
) -> GanttAccessibilityPlan:
    validated_ir = validated_gantt_metadata_ir(ir)
    if gantt_plan is None:
        gantt_plan = plan_gantt_records(validated_ir)
    accessibility_ir = {
        field: validated_ir[field] for field in _GANTT_ACCESSIBILITY_FIELDS if field in validated_ir
    }
    accessibility_ir["sections"] = [
        {
            "id": section.scene_id,
            "label": section.semantic_label,
            "tasks": [
                {"id": task.scene_id, "label": task.semantic_label} for task in section.tasks
            ],
        }
        for section in gantt_plan.sections
    ]
    resolved = resolve_accessibility(accessibility_ir, "gantt", experimental=experimental)
    title = _plan_gantt_text(
        resolved.title,
        field="gantt accessible title",
        role="accessibility",
    )
    description = _plan_gantt_text(
        resolved.description,
        field="gantt accessible description",
        role="accessibility",
    )
    return GanttAccessibilityPlan(
        title_semantic=title.semantic,
        title_source=title.source,
        title_canvas=title.canvas,
        description_semantic=description.semantic,
        description_source=description.source,
        description_canvas=description.canvas,
        compatibility_substitutions=(
            title.compatibility_substitutions or description.compatibility_substitutions
        ),
    )


def enrich_gantt_accessibility_ir(
    ir: dict[str, Any],
    *,
    experimental: bool,
    gantt_plan: GanttPlan | None = None,
) -> dict[str, Any]:
    """Return a raw-validated Gantt snapshot with hook-free resolved metadata."""

    validated_ir = validated_gantt_metadata_ir(ir)
    if gantt_plan is None:
        gantt_plan = plan_gantt_records(validated_ir)
    accessibility = plan_gantt_accessibility(
        validated_ir,
        experimental=experimental,
        gantt_plan=gantt_plan,
    )
    return {
        **validated_ir,
        "acc_title": accessibility.title_semantic,
        "acc_description": accessibility.description_semantic,
    }


def serialize_gantt(ir: dict[str, Any], *, experimental: bool = False) -> str:
    validated_ir = validated_gantt_metadata_ir(ir)
    plan = plan_gantt_records(validated_ir)
    accessibility = plan_gantt_accessibility(
        validated_ir,
        experimental=experimental,
        gantt_plan=plan,
    )
    lines = [
        "gantt",
        f"    accTitle: {accessibility.title_source}",
        f"    accDescr: {accessibility.description_source}",
    ]
    if plan.title is not None:
        lines.append(f"    title {plan.title.source}")
    date_format = validated_ir.get("date_format")
    if date_format is not None:
        date_format = _gantt_schedule_text(date_format, field="gantt date_format")
        lines.append(f"    dateFormat {date_format}")
    for section in plan.sections:
        lines.append(f"    section {section.code_label}")
        for task in section.tasks:
            lines.append(f"    {task.code_label} :{', '.join(task.fields)}")
    return "\n".join(lines) + "\n"


def serialize_architecture(ir: dict[str, Any], *, experimental: bool = False) -> str:
    structure = plan_architecture_structure(ir)
    lines = [
        "architecture-beta",
        *_accessibility(ir, experimental, diagram_type="architecture"),
    ]
    emitted_group_by_source = {
        group.source_id: group.emitted_id for group in structure.group_placements
    }
    for group, placement in zip(
        structure.groups,
        structure.group_placements,
        strict=True,
    ):
        icon = _identifier(str(group.get("icon") or "cloud"))
        lines.append(f'    group {placement.emitted_id}({icon})["{_text(placement.label)}"]')
    emitted_service_by_source = {node.source_id: node.emitted_id for node in structure.nodes}
    for service, placement in zip(structure.services, structure.nodes, strict=True):
        source_id = placement.source_id
        icon = _identifier(str(service.get("icon") or "server"))
        group = service.get("group")
        suffix = (
            f" in {emitted_group_by_source[str(group)]}"
            if group is not None and group != ""
            else ""
        )
        label = _text(service.get("label") or service.get("name") or source_id)
        lines.append(f'    service {placement.emitted_id}({icon})["{label}"]{suffix}')
    for edge in structure.edges:
        source = emitted_service_by_source[str(edge["source"])]
        target = emitted_service_by_source[str(edge["target"])]
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

    structure = plan_architecture_structure(ir)
    groups: list[dict[str, Any]] = []
    for placement in structure.group_placements:
        if not placement.member_source_ids:
            raise SerializationError(
                f"architecture group {placement.source_id!r} has no services for Flowchart fallback"
            )
        groups.append(
            {
                "id": placement.source_id,
                "label": placement.label,
                "member_ids": list(placement.member_source_ids),
            }
        )
    edges = [
        {
            "source": edge["source"],
            "target": edge["target"],
            "bidirectional": bool(edge.get("bidirectional")),
        }
        for edge in structure.edges
    ]

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
            "nodes": [
                {"id": placement.source_id, "label": service["label"]}
                for service, placement in zip(
                    structure.services,
                    structure.nodes,
                    strict=True,
                )
            ],
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
    from marker_mermaid.serializers_charts_core import (
        PIE_FALLBACK_TEXT_COMPATIBILITY_WARNING,
        PIE_NATIVE_TEXT_COMPATIBILITY_WARNING,
        QUADRANT_FALLBACK_TEXT_COMPATIBILITY_WARNING,
        QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING,
        QUADRANT_NATIVE_TEXT_COMPATIBILITY_WARNING,
        XY_FALLBACK_TEXT_COMPATIBILITY_WARNING,
        XY_NATIVE_TEXT_COMPATIBILITY_WARNING,
        plan_pie_records,
        plan_quadrant_records,
        plan_xychart_records,
        serialize_chart_core,
    )
    from marker_mermaid.serializers_charts_flow import serialize_chart_flow
    from marker_mermaid.serializers_charts_sets import (
        TREEMAP_NATIVE_TEXT_COMPATIBILITY_WARNING,
        VENN_NATIVE_TEXT_COMPATIBILITY_WARNING,
        plan_treemap_records,
        plan_venn_records,
        serialize_chart_set,
    )
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
                native_warnings: tuple[str, ...] = ()
                if (
                    _requested_type == "pie"
                    and plan_pie_records(ir).native_compatibility_substitutions
                ):
                    native_warnings = (PIE_NATIVE_TEXT_COMPATIBILITY_WARNING,)
                elif (
                    _requested_type == "xychart"
                    and plan_xychart_records(ir).native_compatibility_substitutions
                ):
                    native_warnings = (XY_NATIVE_TEXT_COMPATIBILITY_WARNING,)
                elif _requested_type == "quadrant":
                    native_warnings = (QUADRANT_NATIVE_PAINT_COMPATIBILITY_WARNING,)
                    if plan_quadrant_records(ir).native_compatibility_substitutions:
                        native_warnings = (
                            QUADRANT_NATIVE_TEXT_COMPATIBILITY_WARNING,
                            *native_warnings,
                        )
                elif (
                    _requested_type == "treemap"
                    and plan_treemap_records(ir).native_compatibility_substitutions
                ):
                    native_warnings = (TREEMAP_NATIVE_TEXT_COMPATIBILITY_WARNING,)
                elif (
                    _requested_type == "venn"
                    and plan_venn_records(ir).native_compatibility_substitutions
                ):
                    native_warnings = (VENN_NATIVE_TEXT_COMPATIBILITY_WARNING,)
                return SerializationResult.native(
                    _requested_type,
                    code,
                    warnings=native_warnings,
                    stability=stability,
                )
            fallback_warnings = [fallback_reason or f"Portable fallback from {_requested_type}."]
            if (
                _requested_type == "pie"
                and plan_pie_records(ir).fallback_compatibility_substitutions
            ):
                fallback_warnings.append(PIE_FALLBACK_TEXT_COMPATIBILITY_WARNING)
            elif (
                _requested_type == "xychart"
                and plan_xychart_records(ir).fallback_compatibility_substitutions
            ):
                fallback_warnings.append(XY_FALLBACK_TEXT_COMPATIBILITY_WARNING)
            elif (
                _requested_type == "quadrant"
                and plan_quadrant_records(ir).fallback_compatibility_substitutions
            ):
                fallback_warnings.append(QUADRANT_FALLBACK_TEXT_COMPATIBILITY_WARNING)
            return SerializationResult.fallback(
                _requested_type,
                emitted_type,
                code,
                warnings=tuple(fallback_warnings),
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


def _validate_pie_explicit_accessibility_fields(ir: dict[str, Any]) -> None:
    """Keep public Pie serialization from stringifying malformed explicit metadata."""

    from marker_mermaid.serializers_charts_core import validate_pie_explicit_metadata

    validate_pie_explicit_metadata(ir)


def _validate_xychart_explicit_accessibility_fields(ir: dict[str, Any]) -> None:
    """Keep public XY serialization from stringifying malformed explicit metadata."""

    from marker_mermaid.serializers_charts_core import validate_xychart_explicit_metadata

    validate_xychart_explicit_metadata(ir)


def _validate_quadrant_explicit_accessibility_fields(ir: dict[str, Any]) -> None:
    """Keep public Quadrant serialization from stringifying malformed metadata."""

    from marker_mermaid.serializers_charts_core import validate_quadrant_explicit_metadata

    validate_quadrant_explicit_metadata(ir)


def _validate_sankey_explicit_accessibility_fields(ir: dict[str, Any]) -> None:
    """Keep public Sankey serialization from stringifying malformed metadata."""

    from marker_mermaid.serializers_charts_flow import validate_sankey_explicit_metadata

    validate_sankey_explicit_metadata(ir)


def _validated_chart_set_accessibility_ir(
    diagram_type: str,
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Validate chart-set metadata and preserve exact-empty omitted semantics."""

    from marker_mermaid.serializers_charts_sets import (
        validated_treemap_accessibility_ir,
        validated_venn_accessibility_ir,
    )

    if diagram_type == "treemap":
        return validated_treemap_accessibility_ir(ir)
    return validated_venn_accessibility_ir(ir)


def serialize_typed_ir_result(
    diagram_type: str,
    ir: dict[str, Any],
    *,
    experimental: bool = False,
) -> SerializationResult:
    """Serialize typed IR while retaining native/fallback grammar metadata."""

    accessibility_source_ir = ir
    state_record_compatibility_substitutions = False
    state_plan = None
    gantt_plan = None
    if diagram_type == "pie":
        _validate_pie_explicit_accessibility_fields(ir)
    elif diagram_type == "xychart":
        _validate_xychart_explicit_accessibility_fields(ir)
    elif diagram_type == "quadrant":
        _validate_quadrant_explicit_accessibility_fields(ir)
    elif diagram_type == "sankey":
        _validate_sankey_explicit_accessibility_fields(ir)
    elif diagram_type in {"treemap", "venn"}:
        accessibility_source_ir = _validated_chart_set_accessibility_ir(diagram_type, ir)
    elif diagram_type == "gantt":
        accessibility_source_ir = validated_gantt_metadata_ir(ir)
        gantt_plan = plan_gantt_records(accessibility_source_ir)
    elif diagram_type == "state":
        from marker_mermaid.serializers_uml import (
            plan_state_records,
            validated_state_accessibility_ir,
        )

        accessibility_source_ir = validated_state_accessibility_ir(ir)
        state_plan = plan_state_records(accessibility_source_ir)
        state_record_compatibility_substitutions = state_plan.compatibility_substitutions
    _ensure_extended_serializers()
    if diagram_type == "gantt":
        enriched_ir = enrich_gantt_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            gantt_plan=gantt_plan,
        )
    elif diagram_type == "state":
        from marker_mermaid.serializers_uml import enrich_state_accessibility_ir

        enriched_ir = enrich_state_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            state_plan=state_plan,
        )
    else:
        enriched_ir = enrich_accessibility_ir(
            accessibility_source_ir,
            diagram_type,
            experimental=experimental,
        )
    result = SERIALIZATION_REGISTRY.dispatch(
        diagram_type,
        enriched_ir,
        experimental=experimental,
    )
    if diagram_type == "state":
        from marker_mermaid.serializers_uml import (
            STATE_TEXT_COMPATIBILITY_WARNING,
            plan_state_accessibility,
        )

        if (
            state_record_compatibility_substitutions
            or plan_state_accessibility(
                enriched_ir,
                experimental=experimental,
                state_plan=state_plan,
            ).compatibility_substitutions
        ) and STATE_TEXT_COMPATIBILITY_WARNING not in result.warnings:
            result = replace(
                result,
                warnings=(*result.warnings, STATE_TEXT_COMPATIBILITY_WARNING),
            )
    elif diagram_type == "gantt" and (
        gantt_plan.compatibility_substitutions
        or plan_gantt_accessibility(
            enriched_ir,
            experimental=experimental,
            gantt_plan=gantt_plan,
        ).compatibility_substitutions
    ):
        if GANTT_TEXT_COMPATIBILITY_WARNING not in result.warnings:
            result = replace(
                result,
                warnings=(*result.warnings, GANTT_TEXT_COMPATIBILITY_WARNING),
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

    if diagram_type == "pie":
        _validate_pie_explicit_accessibility_fields(ir)
    elif diagram_type == "xychart":
        _validate_xychart_explicit_accessibility_fields(ir)
    elif diagram_type == "quadrant":
        _validate_quadrant_explicit_accessibility_fields(ir)
    elif diagram_type == "sankey":
        _validate_sankey_explicit_accessibility_fields(ir)
    elif diagram_type in {"treemap", "venn"}:
        ir = _validated_chart_set_accessibility_ir(diagram_type, ir)
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
    if diagram_type == "pie":
        from marker_mermaid.serializers_charts_core import (
            PIE_FALLBACK_TEXT_COMPATIBILITY_WARNING,
            plan_pie_records,
            serialize_pie,
        )

        code, emitted_type, reason = serialize_pie(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        if emitted_type == "pie":
            return None
        warnings = [reason or "CandidateValidator rejected native Pie."]
        if plan_pie_records(ir).fallback_compatibility_substitutions:
            warnings.append(PIE_FALLBACK_TEXT_COMPATIBILITY_WARNING)
        return SerializationResult.fallback(
            "pie",
            emitted_type,
            code,
            warnings=tuple(warnings),
            stability="extended",
        )
    if diagram_type == "xychart":
        from marker_mermaid.serializers_charts_core import (
            XY_FALLBACK_TEXT_COMPATIBILITY_WARNING,
            plan_xychart_records,
            serialize_xychart,
        )

        code, emitted_type, reason = serialize_xychart(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        if emitted_type == "xychart":
            return None
        warnings = [reason or "CandidateValidator rejected native XY Chart."]
        if plan_xychart_records(ir).fallback_compatibility_substitutions:
            warnings.append(XY_FALLBACK_TEXT_COMPATIBILITY_WARNING)
        return SerializationResult.fallback(
            "xychart",
            emitted_type,
            code,
            warnings=tuple(warnings),
            stability="experimental",
        )
    if diagram_type == "quadrant":
        from marker_mermaid.serializers_charts_core import (
            QUADRANT_FALLBACK_TEXT_COMPATIBILITY_WARNING,
            plan_quadrant_records,
            serialize_quadrant,
        )

        code, emitted_type, reason = serialize_quadrant(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        if emitted_type == "quadrant":
            return None
        warnings = [reason or "CandidateValidator rejected native Quadrant."]
        if plan_quadrant_records(ir).fallback_compatibility_substitutions:
            warnings.append(QUADRANT_FALLBACK_TEXT_COMPATIBILITY_WARNING)
        return SerializationResult.fallback(
            "quadrant",
            emitted_type,
            code,
            warnings=tuple(warnings),
            stability="experimental",
        )
    if diagram_type == "sankey":
        from marker_mermaid.serializers_charts_flow import serialize_sankey

        result = serialize_sankey(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        return result if result.used_fallback else None
    if diagram_type == "radar":
        from marker_mermaid.serializers_charts_flow import serialize_radar

        result = serialize_radar(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        return result if result.used_fallback else None
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
    if diagram_type in {"kanban", "gitgraph"}:
        from marker_mermaid.serializers_planning import serialize_gitgraph, serialize_kanban

        serializer = serialize_kanban if diagram_type == "kanban" else serialize_gitgraph
        result = serializer(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        return result if result.used_fallback else None
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
    if diagram_type == "wardley":
        from marker_mermaid.serializers_experimental import serialize_wardley

        result = serialize_wardley(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        return result if result.used_fallback else None
    if diagram_type == "cynefin":
        from marker_mermaid.serializers_experimental import serialize_cynefin

        result = serialize_cynefin(
            ir,
            experimental=experimental,
            native_runtime_valid=False,
        )
        return result if result.used_fallback else None
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
