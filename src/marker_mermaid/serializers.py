"""Deterministic serializers from typed/common IR to Mermaid text."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping
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
    MAX_EVIDENCE_REFS,
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


def _architecture_source_id(value: Any, *, fallback: str, context: str) -> str:
    if value is None or (type(value) is str and value == ""):
        source_id = fallback
    elif type(value) is str:
        source_id = value
    else:
        raise SerializationError(f"{context} must be a string")
    if not source_id.strip() or len(source_id) > MAX_ID_CHARS:
        raise SerializationError(f"{context} must be a bounded non-empty string")
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in source_id
    ):
        raise SerializationError(f"{context} contains unsupported text")
    try:
        source_id.encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
        raise SerializationError(f"{context} is not valid UTF-8 text") from exc
    return source_id


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
        group_id = _architecture_source_id(
            group.get("id"),
            fallback=f"G{index}",
            context="architecture group id",
        )
        if group_id in group_members:
            raise SerializationError("architecture group ids must be unique")
        emitted_group_id = portable_identifier(group_id, f"G{index}")
        raw_label = group.get("label")
        if raw_label is None or (type(raw_label) is str and raw_label == ""):
            label = emitted_group_id
        else:
            label = raw_label
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
        service_id = _architecture_source_id(
            service.get("id"),
            fallback=f"S{index}",
            context="architecture service id",
        )
        if service_id in service_ids:
            raise SerializationError("architecture service ids must be unique")
        service_ids.add(service_id)
        services.append(
            {
                **service,
                "id": service_id,
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
    for index, edge in enumerate(raw_edges, start=1):
        if not isinstance(edge, dict):
            raise SerializationError("architecture edges must be objects")
        source_value = edge.get("source")
        target_value = edge.get("target")
        if type(source_value) is not str or type(target_value) is not str:
            raise SerializationError(
                f"architecture edge {index} requires source and target strings"
            )
        source = source_value
        target = target_value
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
    text = (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
    source = _SEQUENCE_DIRECTIVE_OPEN.sub(
        lambda match: f"%{_SEQUENCE_ZERO_WIDTH_SPACE}%{match.group('gap')}{{",
        text,
    )
    source = source.replace("//", f"/{_SEQUENCE_ZERO_WIDTH_SPACE}/")
    source = source.replace("<", f"<{_SEQUENCE_ZERO_WIDTH_SPACE}")
    source = _SEQUENCE_CSS_IMPORT.sub(
        lambda match: f"{match.group(0)[:3]}{_SEQUENCE_ZERO_WIDTH_SPACE}{match.group(0)[3:]}",
        source,
    )
    source = _SEQUENCE_DANGEROUS_SCHEME.sub(
        lambda match: f"{match.group(0)[:-1]}{_SEQUENCE_ZERO_WIDTH_SPACE}:",
        source,
    )
    source = _SEQUENCE_REMOTE_ICON.sub(
        lambda match: (
            f"{match.group(0)[:4]}{_SEQUENCE_ZERO_WIDTH_SPACE}{match.group(0)[4:]}"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", f"{_SEQUENCE_ZERO_WIDTH_SPACE}:")
        ),
        source,
    )
    source = _SEQUENCE_CALLBACK.sub(
        lambda match: match.group(0).replace(
            "(",
            f"{_SEQUENCE_ZERO_WIDTH_SPACE}(",
        ),
        source,
    )
    return source


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
    if not isinstance(edges, list):
        raise SerializationError("flowchart edges must be a list")
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
        if source_id in id_map:
            raise SerializationError("flowchart node ids must be unique")
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
    for index, edge in enumerate(edges, start=1):
        if not isinstance(edge, dict):
            raise SerializationError("flowchart edges must be objects")
        source = id_map.get(str(edge.get("source")))
        target = id_map.get(str(edge.get("target")))
        if source is None or target is None:
            raise SerializationError(f"flowchart edge {index} references an unknown endpoint")
        arrow = "-.->" if edge.get("style") == "dashed" else "-->"
        if edge.get("bidirectional"):
            arrow = "<-->"
        label = edge.get("label")
        label_text = _text(label).replace("|", "∣") if label is not None else ""
        if label_text and (
            _SEQUENCE_ZERO_WIDTH_SPACE in label_text
            or any(character in label_text for character in "()[]{};<>\\")
        ):
            connector = f'{arrow}|"{label_text}"|'
        else:
            connector = f"{arrow}|{label_text}|" if label_text else arrow
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


_SEQUENCE_ZERO_WIDTH_SPACE = "\u200b"
_SEQUENCE_METADATA_FIELDS = ("title", "description", "acc_title", "acc_description")
_SEQUENCE_MAX_OUTPUT_CHARS = 50_000
_SEQUENCE_MAX_OUTPUT_LINES = 5_000
_SEQUENCE_DIRECTIVE_OPEN = re.compile(r"%%(?P<gap>\s*)\{")
_SEQUENCE_DANGEROUS_SCHEME = re.compile(r"(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_SEQUENCE_REMOTE_ICON = re.compile(r"iconify|fa:|logos:", re.IGNORECASE)
_SEQUENCE_CALLBACK = re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE)
_SEQUENCE_CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)
_SEQUENCE_CONFIG = re.compile(r"\bconfig\s*:", re.IGNORECASE)
_SEQUENCE_CONTROL_WORD = re.compile(
    r"\b(?:accDescr|accTitle|activate|actor|alt|and|autonumber|box|break|call|callback|"
    r"classDef|click|create|critical|deactivate|destroy|details|else|end|href|links|"
    r"linkStyle|loop|note|off|opt|option|over|par|par_over|participant|properties|"
    r"rect|sequenceDiagram|style)\b",
    re.IGNORECASE,
)
_SEQUENCE_STYLE_PLAN = {
    "solid": ("->>", True, "solid"),
    "dotted": ("-->>", True, "dotted"),
    "open": ("->", False, "solid"),
    "dotted_open": ("-->", False, "dotted"),
    "cross": ("-x", True, "solid"),
}

SEQUENCE_TEXT_COMPATIBILITY_WARNING = (
    "Sequence accessibility text uses visible angle glyphs (〈 and 〉) where Mermaid "
    "11.16 cannot preserve literal less-than or greater-than text; semantic text remains "
    "in typed IR."
)


@dataclass(frozen=True, slots=True)
class SequenceTextPlan:
    """One Sequence terminal across semantic IR, source, and SVG canvas text."""

    semantic: str
    source: str
    canvas: str
    compatibility_substitutions: bool


@dataclass(frozen=True, slots=True)
class SequenceParticipantPlan:
    """One participant with source identity and an isolated Mermaid identity."""

    source_record: dict[str, Any] | None
    source_id: str
    emitted_id: str
    semantic_label: str
    source_label: str
    canvas_label: str


@dataclass(frozen=True, slots=True)
class SequenceMessagePlan:
    """One ordered message resolved to exact emitted participant endpoints."""

    source_record: dict[str, Any]
    scene_id: str
    source_id: str
    target_id: str
    source_emitted_id: str
    target_emitted_id: str
    semantic_label: str
    source_label: str
    canvas_label: str
    style: str
    arrow_token: str
    arrow_at_end: bool
    line_style: str


@dataclass(frozen=True, slots=True)
class SequencePlan:
    """Canonical Sequence records shared by serializer, Scene, OCR, and repair."""

    participants: tuple[SequenceParticipantPlan, ...]
    messages: tuple[SequenceMessagePlan, ...]
    compatibility_substitutions: bool


@dataclass(frozen=True, slots=True)
class SequenceAccessibilityPlan:
    """Resolved Sequence accessibility terminals with exact canvas projections."""

    title_semantic: str
    title_source: str
    title_canvas: str
    description_semantic: str
    description_source: str
    description_canvas: str
    compatibility_substitutions: bool


def validated_sequence_accessibility_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw Sequence metadata and treat exact-empty fields as omitted."""

    for field in _SEQUENCE_METADATA_FIELDS:
        value = ir.get(field)
        if value is None:
            continue
        if type(value) is not str:
            raise SerializationError(f"sequence {field} must be text when provided")
        if value == "":
            continue
        if len(value) > MAX_TEXT_CHARS:
            raise SerializationError(f"sequence {field} must be bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value
        ):
            raise SerializationError(f"sequence {field} contains unsupported text")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > MAX_TEXT_CHARS:
            raise SerializationError(f"sequence {field} must be bounded non-empty text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
            raise SerializationError(f"sequence {field} is not valid UTF-8 text") from exc
    return {
        key: value
        for key, value in ir.items()
        if key not in _SEQUENCE_METADATA_FIELDS or value != ""
    }


def _semantic_terminal_text(value: Any, *, context: str) -> str:
    if type(value) is not str:
        raise SerializationError(f"{context} must be text")
    if len(value) > MAX_TEXT_CHARS:
        raise SerializationError(f"{context} must be bounded non-empty text")
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cf", "Cs", "Zl", "Zp"} or (category == "Cc" and not character.isspace()):
            raise SerializationError(f"{context} contains unsupported text")
    semantic = " ".join(value.split())
    if not semantic or len(semantic) > MAX_TEXT_CHARS:
        raise SerializationError(f"{context} must be bounded non-empty text")
    try:
        semantic.encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
        raise SerializationError(f"{context} is not valid UTF-8 text") from exc
    return semantic


def _neutralize_active_terminal_source(text: str) -> str:
    source = _SEQUENCE_DIRECTIVE_OPEN.sub(
        lambda match: f"%{_SEQUENCE_ZERO_WIDTH_SPACE}%{match.group('gap')}{{",
        text,
    )
    source = source.replace("//", f"/{_SEQUENCE_ZERO_WIDTH_SPACE}/")
    source = source.replace("<", f"<{_SEQUENCE_ZERO_WIDTH_SPACE}")
    source = _SEQUENCE_CSS_IMPORT.sub(
        lambda match: f"{match.group(0)[:3]}{_SEQUENCE_ZERO_WIDTH_SPACE}{match.group(0)[3:]}",
        source,
    )
    source = _SEQUENCE_DANGEROUS_SCHEME.sub(
        lambda match: f"{match.group(0)[:-1]}{_SEQUENCE_ZERO_WIDTH_SPACE}:",
        source,
    )
    source = _SEQUENCE_REMOTE_ICON.sub(
        lambda match: (
            f"{match.group(0)[:4]}{_SEQUENCE_ZERO_WIDTH_SPACE}{match.group(0)[4:]}"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", f"{_SEQUENCE_ZERO_WIDTH_SPACE}:")
        ),
        source,
    )
    source = _SEQUENCE_CALLBACK.sub(
        lambda match: match.group(0).replace("(", f"{_SEQUENCE_ZERO_WIDTH_SPACE}("),
        source,
    )
    source = _SEQUENCE_CONTROL_WORD.sub(
        lambda match: f"{match.group(0)[0]}{_SEQUENCE_ZERO_WIDTH_SPACE}{match.group(0)[1:]}",
        source,
    )
    source = _SEQUENCE_CONFIG.sub(
        lambda match: match.group(0).replace(":", f"{_SEQUENCE_ZERO_WIDTH_SPACE}:"),
        source,
    )
    return source.replace("---", f"-{_SEQUENCE_ZERO_WIDTH_SPACE}--")


def _sequence_source_escape(text: str) -> str:
    """Encode statement delimiters without changing Mermaid 11.16 canvas text."""

    return "".join(
        "#35;" if character == "#" else "#59;" if character == ";" else character
        for character in text
    )


def _mermaid_utf16_units(text: str) -> int:
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:  # pragma: no cover - semantic text rejects surrogates
        raise SerializationError("Mermaid output is not valid UTF-16") from exc


def _plan_sequence_text(
    value: Any,
    *,
    context: str,
    accessibility: bool,
) -> SequenceTextPlan:
    semantic = _semantic_terminal_text(value, context=context)
    canvas = semantic.replace("<", "〈").replace(">", "〉") if accessibility else semantic
    source = _neutralize_active_terminal_source(_sequence_source_escape(canvas))
    return SequenceTextPlan(
        semantic=semantic,
        source=source,
        canvas=canvas,
        compatibility_substitutions=canvas != semantic,
    )


def _sequence_source_id(value: Any, *, fallback: str, context: str) -> str:
    if value is None or (type(value) is str and value == ""):
        return fallback
    if type(value) is not str:
        raise SerializationError(f"{context} must be a string")
    if len(value) > MAX_ID_CHARS:
        raise SerializationError(f"{context} exceeds the Scene identifier limit")
    if not value or not value.strip():
        raise SerializationError(f"{context} must be a bounded non-empty identifier")
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value
    ):
        raise SerializationError(f"{context} contains unsupported text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
        raise SerializationError(f"{context} is not valid UTF-8 text") from exc
    return value


def plan_sequence_records(ir: Mapping[str, Any]) -> SequencePlan:
    """Freeze Sequence identities, labels, endpoint semantics, and arrow styles."""

    validated_ir = validated_sequence_accessibility_ir(ir)
    participants = validated_ir.get("participants")
    messages = validated_ir.get("messages", [])
    if not isinstance(participants, list) or not participants:
        raise SerializationError("sequence IR requires participants")
    if len(participants) > MAX_SCENE_ELEMENTS:
        raise SerializationError("sequence participant count exceeds the Scene element limit")
    if not isinstance(messages, list):
        raise SerializationError("sequence messages must be a list of objects")
    if len(messages) > MAX_SCENE_RELATIONS:
        raise SerializationError("sequence message count exceeds the Scene relation limit")

    canonical_participants: list[dict[str, str]] = []
    source_records: list[dict[str, Any] | None] = []
    participant_texts: list[SequenceTextPlan] = []
    for index, participant in enumerate(participants, start=1):
        if type(participant) is str:
            source_id = _sequence_source_id(
                participant,
                fallback=f"P{index}",
                context=f"sequence participant {index} id",
            )
            raw_label: Any = participant
            source_record = None
        elif type(participant) is dict:
            source_id = _sequence_source_id(
                participant.get("id"),
                fallback=f"P{index}",
                context=f"sequence participant {index} id",
            )
            candidate_label = participant.get("label")
            raw_label = (
                source_id
                if candidate_label is None
                or (type(candidate_label) is str and candidate_label == "")
                else candidate_label
            )
            source_record = participant
        else:
            raise SerializationError("sequence participants must be strings or objects")
        if len(source_id) > MAX_ID_CHARS:
            raise SerializationError("sequence participant id exceeds the Scene identifier limit")
        if type(raw_label) is str and len(raw_label) > MAX_TEXT_CHARS:
            raise SerializationError("sequence participant label exceeds the Scene text limit")
        text_plan = _plan_sequence_text(
            raw_label,
            context=f"sequence participant {index} label",
            accessibility=False,
        )
        canonical_participants.append({"id": source_id, "label": text_plan.semantic})
        source_records.append(source_record)
        participant_texts.append(text_plan)

    canonical_messages: list[dict[str, str]] = []
    message_records: list[dict[str, Any]] = []
    message_texts: list[SequenceTextPlan] = []
    message_styles: list[str] = []
    for index, message in enumerate(messages, start=1):
        if type(message) is not dict:
            raise SerializationError("sequence messages must be objects")
        raw_message_id = message.get("id")
        if raw_message_id is not None:
            if type(raw_message_id) is not str:
                raise SerializationError("sequence message id must be a string")
            if len(raw_message_id) > MAX_ID_CHARS:
                raise SerializationError("sequence message id exceeds the Scene identifier limit")
        source_id = message.get("source")
        target_id = message.get("target")
        if type(source_id) is not str or type(target_id) is not str:
            raise SerializationError(f"sequence message {index} references an unknown participant")
        raw_label = message.get("label")
        if raw_label is None or (type(raw_label) is str and raw_label == ""):
            raw_label = "[unreadable]"
        text_plan = _plan_sequence_text(
            raw_label,
            context=f"sequence message {index} label",
            accessibility=False,
        )
        raw_style = message.get("style")
        if raw_style is None or (type(raw_style) is str and raw_style == ""):
            style = "solid"
        elif type(raw_style) is str and raw_style in _SEQUENCE_STYLE_PLAN:
            style = raw_style
        else:
            raise SerializationError(f"sequence message {index} uses an unsupported style")
        canonical_messages.append(
            {
                "source": source_id,
                "target": target_id,
                "label": text_plan.semantic,
                "style": style,
            }
        )
        message_records.append(message)
        message_texts.append(text_plan)
        message_styles.append(style)

    try:
        structure = plan_sequence_structure(canonical_participants, canonical_messages)
    except SequenceStructureError as exc:
        raise SerializationError(str(exc)) from exc

    planned_participants = tuple(
        SequenceParticipantPlan(
            source_record=source_record,
            source_id=placement.source_id,
            emitted_id=placement.emitted_id,
            semantic_label=text_plan.semantic,
            source_label=text_plan.source,
            canvas_label=text_plan.canvas,
        )
        for source_record, text_plan, placement in zip(
            source_records,
            participant_texts,
            structure.participants,
            strict=True,
        )
    )
    source_id_by_emitted = {
        participant.emitted_id: participant.source_id for participant in planned_participants
    }
    planned_messages: list[SequenceMessagePlan] = []
    for record, text_plan, style, placement in zip(
        message_records,
        message_texts,
        message_styles,
        structure.messages,
        strict=True,
    ):
        arrow_token, arrow_at_end, line_style = _SEQUENCE_STYLE_PLAN[style]
        planned_messages.append(
            SequenceMessagePlan(
                source_record=record,
                scene_id=placement.emitted_id,
                source_id=source_id_by_emitted[placement.source_id],
                target_id=source_id_by_emitted[placement.target_id],
                source_emitted_id=placement.source_id,
                target_emitted_id=placement.target_id,
                semantic_label=text_plan.semantic,
                source_label=text_plan.source,
                canvas_label=text_plan.canvas,
                style=style,
                arrow_token=arrow_token,
                arrow_at_end=arrow_at_end,
                line_style=line_style,
            )
        )
    minimum_source_lines = [
        "sequenceDiagram",
        "    accTitle: ",
        "    accDescr: ",
        *(
            f"    participant {participant.emitted_id} as {participant.source_label}"
            for participant in planned_participants
        ),
        *(
            f"    {message.source_emitted_id}{message.arrow_token}"
            f"{message.target_emitted_id}: {message.source_label}"
            for message in planned_messages
        ),
    ]
    if len(minimum_source_lines) + 1 > _SEQUENCE_MAX_OUTPUT_LINES:
        raise SerializationError(
            f"sequence output exceeds source-line limit of {_SEQUENCE_MAX_OUTPUT_LINES}"
        )
    if _mermaid_utf16_units("\n".join(minimum_source_lines)) + 1 > _SEQUENCE_MAX_OUTPUT_CHARS:
        raise SerializationError(
            f"sequence output exceeds UTF-16 source-character limit of {_SEQUENCE_MAX_OUTPUT_CHARS}"
        )
    return SequencePlan(
        participants=planned_participants,
        messages=tuple(planned_messages),
        compatibility_substitutions=any(
            text.compatibility_substitutions for text in (*participant_texts, *message_texts)
        ),
    )


def plan_sequence_accessibility(
    ir: Mapping[str, Any],
    *,
    experimental: bool,
    sequence_plan: SequencePlan | None = None,
) -> SequenceAccessibilityPlan:
    """Resolve accessibility only from validated metadata and planned semantics."""

    validated_ir = validated_sequence_accessibility_ir(ir)
    plan = sequence_plan or plan_sequence_records(validated_ir)
    semantic_ir: dict[str, Any] = {
        key: validated_ir[key] for key in _SEQUENCE_METADATA_FIELDS if key in validated_ir
    }
    semantic_ir["participants"] = [
        {"id": participant.source_id, "label": participant.semantic_label}
        for participant in plan.participants
    ]
    semantic_ir["messages"] = [
        {"source": message.source_id, "target": message.target_id} for message in plan.messages
    ]
    resolved = resolve_accessibility(semantic_ir, "sequence", experimental=experimental)
    title = _plan_sequence_text(
        resolved.title,
        context="sequence accessible title",
        accessibility=True,
    )
    description = _plan_sequence_text(
        resolved.description,
        context="sequence accessible description",
        accessibility=True,
    )
    return SequenceAccessibilityPlan(
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


def enrich_sequence_accessibility_ir(
    ir: Mapping[str, Any],
    *,
    experimental: bool,
    sequence_plan: SequencePlan | None = None,
) -> dict[str, Any]:
    """Return a copy with current semantic Sequence accessibility text."""

    validated_ir = validated_sequence_accessibility_ir(ir)
    accessibility = plan_sequence_accessibility(
        validated_ir,
        experimental=experimental,
        sequence_plan=sequence_plan,
    )
    return {
        **validated_ir,
        "acc_title": accessibility.title_semantic,
        "acc_description": accessibility.description_semantic,
    }


def serialize_sequence(ir: dict[str, Any], *, experimental: bool = False) -> str:
    sequence_plan = plan_sequence_records(ir)
    accessibility = plan_sequence_accessibility(
        ir,
        experimental=experimental,
        sequence_plan=sequence_plan,
    )
    lines = [
        "sequenceDiagram",
        f"    accTitle: {accessibility.title_source}",
        f"    accDescr: {accessibility.description_source}",
    ]
    for participant in sequence_plan.participants:
        lines.append(f"    participant {participant.emitted_id} as {participant.source_label}")
    for message in sequence_plan.messages:
        lines.append(
            f"    {message.source_emitted_id}{message.arrow_token}"
            f"{message.target_emitted_id}: {message.source_label}"
        )
    code = "\n".join(lines) + "\n"
    if _mermaid_utf16_units(code) > _SEQUENCE_MAX_OUTPUT_CHARS:
        raise SerializationError(
            f"sequence output exceeds UTF-16 source-character limit of {_SEQUENCE_MAX_OUTPUT_CHARS}"
        )
    if code.count("\n") + 1 > _SEQUENCE_MAX_OUTPUT_LINES:
        raise SerializationError(
            f"sequence output exceeds source-line limit of {_SEQUENCE_MAX_OUTPUT_LINES}"
        )
    return code


_MINDMAP_METADATA_FIELDS = ("title", "description", "acc_title", "acc_description")
_MINDMAP_MAX_OUTPUT_CHARS = 50_000
_MINDMAP_MAX_OUTPUT_LINES = 5_000
_MARKDOWN_LABEL_ENTITY_LITERAL = re.compile(
    r"&(?P<body>#[A-Za-z0-9][^;\s]*|[A-Za-z][A-Za-z0-9]*);"
    r"|(?<!&)#(?P<standalone>[A-Za-z0-9][^;\s]*);"
)
_MARKDOWN_LABEL_NAMED_ENTITY_PREFIX = re.compile(r"&(?=[A-Za-z][A-Za-z0-9]*;)")

MINDMAP_TEXT_COMPATIBILITY_WARNING = (
    "Mindmap text uses visible compatibility glyphs for quotes, Markdown emphasis/code "
    "delimiters, and numeric entity-like literals that Mermaid 11.16 cannot preserve "
    "verbatim; semantic text remains in typed IR."
)


@dataclass(frozen=True, slots=True)
class MarkdownLabelTextPlan:
    """One quoted Markdown label across semantic IR, source, and SVG canvas text."""

    semantic: str
    source: str
    canvas: str
    compatibility_substitutions: bool


# Kept as a public compatibility alias for callers that adopted the v0.3
# Mindmap plan before Architecture began sharing the same Mermaid label grammar.
MindmapTextPlan = MarkdownLabelTextPlan


@dataclass(frozen=True, slots=True)
class MindmapNodePlan:
    """One preorder Mindmap node with isolated serializer and Scene identity."""

    source_record: dict[str, Any]
    source_id: str
    emitted_id: str
    depth: int
    parent_id: str | None
    text: MindmapTextPlan


@dataclass(frozen=True, slots=True)
class MindmapPlan:
    """Canonical recursive Mindmap records shared by every terminal consumer."""

    nodes: tuple[MindmapNodePlan, ...]
    compatibility_substitutions: bool


def validated_mindmap_accessibility_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw Mindmap metadata and treat exact-empty fields as omitted."""

    for field in _MINDMAP_METADATA_FIELDS:
        value = ir.get(field)
        if value is None:
            continue
        if type(value) is not str:
            raise SerializationError(f"mindmap {field} must be text when provided")
        if value == "":
            continue
        if len(value) > MAX_TEXT_CHARS:
            raise SerializationError(f"mindmap {field} must be bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value
        ):
            raise SerializationError(f"mindmap {field} contains unsupported text")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > MAX_TEXT_CHARS:
            raise SerializationError(f"mindmap {field} must be bounded non-empty text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
            raise SerializationError(f"mindmap {field} is not valid UTF-8 text") from exc
    return {
        key: value
        for key, value in ir.items()
        if key not in _MINDMAP_METADATA_FIELDS or value != ""
    }


def _markdown_label_canvas_text(semantic: str) -> str:
    def replace_entity(match: re.Match[str]) -> str:
        body = match.group("body")
        if body is None:
            return f"＃{match.group('standalone')};"
        if body.startswith("#"):
            return f"＆＃{body[1:]};"
        return f"&{body};"

    return (
        _MARKDOWN_LABEL_ENTITY_LITERAL.sub(replace_entity, semantic)
        .replace('"', "″")
        .replace("*", "＊")
        .replace("`", "ˋ")
        .replace("~", "～")
    )


def _markdown_label_source_text(canvas: str) -> str:
    # Split source-authored named entities before adding XML entities for actual
    # angle brackets. This distinction keeps literal ``&amp;`` visible as text while
    # still allowing ``<`` and ``>`` to round-trip through Mermaid's SVG renderer.
    source = _MARKDOWN_LABEL_NAMED_ENTITY_PREFIX.sub(
        f"&{_SEQUENCE_ZERO_WIDTH_SPACE}",
        canvas,
    )
    source = source.replace("<", "&lt;").replace(">", "&gt;")
    source = _neutralize_active_terminal_source(source)
    # Mermaid Mindmap applies Markdown inside quoted node shapes. Source-only
    # sentinels stop escape, emphasis, link, and shape delimiters becoming syntax.
    for character in ("\\", "_", "[", "]", "(", ")"):
        source = source.replace(character, f"{character}{_SEQUENCE_ZERO_WIDTH_SPACE}")
    # A lexer sentinel between two ordinary spaces prevents source-active words
    # from joining in the SVG text layout. SVG whitespace normalization then
    # restores the single semantic space on the visible canvas.
    source = source.replace(
        " ",
        f" {_SEQUENCE_ZERO_WIDTH_SPACE} ",
    )
    return f"{_SEQUENCE_ZERO_WIDTH_SPACE}{source}"


def _plan_markdown_label_text(value: Any, *, context: str) -> MarkdownLabelTextPlan:
    semantic = _semantic_terminal_text(value, context=context)
    canvas = _markdown_label_canvas_text(semantic)
    return MarkdownLabelTextPlan(
        semantic=semantic,
        source=_markdown_label_source_text(canvas),
        canvas=canvas,
        compatibility_substitutions=canvas != semantic,
    )


def _plan_mindmap_text(value: Any, *, context: str) -> MindmapTextPlan:
    """Compatibility wrapper for the shared quoted-Markdown label planner."""

    return _plan_markdown_label_text(value, context=context)


_ARCHITECTURE_METADATA_FIELDS = ("title", "description", "acc_title", "acc_description")
_ARCHITECTURE_MAX_OUTPUT_CHARS = 50_000
_ARCHITECTURE_MAX_OUTPUT_LINES = 5_000
_ARCHITECTURE_ICONS = frozenset({"cloud", "database", "disk", "internet", "server"})

ARCHITECTURE_TEXT_COMPATIBILITY_WARNING = (
    "Architecture labels use visible compatibility glyphs for quotes, Markdown "
    "emphasis/code delimiters, and numeric entity-like literals that Mermaid 11.16 "
    "cannot preserve verbatim; semantic text remains in typed IR."
)


@dataclass(frozen=True, slots=True)
class ArchitectureAccessibilityPlan:
    """Exact Architecture accessibility text across semantics, source, and SVG metadata."""

    title_semantic: str
    title_source: str
    title_canvas: str
    description_semantic: str
    description_source: str
    description_canvas: str


@dataclass(frozen=True, slots=True)
class ArchitectureServicePlan:
    """One Architecture service with a shared native/fallback terminal identity."""

    source_record: dict[str, Any]
    source_id: str
    emitted_id: str
    text: MarkdownLabelTextPlan
    icon: str
    group_source_id: str | None
    group_emitted_id: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchitectureGroupPlan:
    """One Architecture group and its exact terminal-visible membership."""

    source_record: dict[str, Any]
    source_id: str
    emitted_id: str
    text: MarkdownLabelTextPlan
    icon: str
    member_source_ids: tuple[str, ...]
    member_emitted_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchitectureRelationPlan:
    """One unlabeled Architecture connector shared by both emitted grammars."""

    source_record: dict[str, Any]
    scene_id: str
    source_id: str
    target_id: str
    source_emitted_id: str
    target_emitted_id: str
    source_side: str
    target_side: str
    bidirectional: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArchitecturePlan:
    """Canonical Architecture records for serialization, Scene, OCR, and repair."""

    services: tuple[ArchitectureServicePlan, ...]
    groups: tuple[ArchitectureGroupPlan, ...]
    relations: tuple[ArchitectureRelationPlan, ...]
    accessibility: ArchitectureAccessibilityPlan
    fallback_direction: str
    compatibility_substitutions: bool


def validated_architecture_accessibility_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw Architecture metadata before accessibility enrichment."""

    for field in _ARCHITECTURE_METADATA_FIELDS:
        value = ir.get(field)
        if value is None:
            continue
        if type(value) is not str:
            raise SerializationError(f"architecture {field} must be text when provided")
        if value == "":
            continue
        _semantic_terminal_text(value, context=f"architecture {field}")
    return {
        key: value
        for key, value in ir.items()
        if key not in _ARCHITECTURE_METADATA_FIELDS or value != ""
    }


def _architecture_record_label(
    record: Mapping[str, Any],
    *,
    fields: tuple[str, ...],
    fallback: str,
    context: str,
) -> MarkdownLabelTextPlan:
    selected: Any = fallback
    for field in fields:
        value = record.get(field)
        if value is None or (type(value) is str and value == ""):
            continue
        selected = value
        break
    planned = _plan_markdown_label_text(selected, context=context)
    plain_candidate = (
        planned.source.removeprefix(_SEQUENCE_ZERO_WIDTH_SPACE)
        .replace(f" {_SEQUENCE_ZERO_WIDTH_SPACE} ", " ")
        .replace(f"_{_SEQUENCE_ZERO_WIDTH_SPACE}", "_")
    )
    underscores_are_internal = all(
        0 < index < len(planned.semantic) - 1
        and planned.semantic[index - 1].isalnum()
        and planned.semantic[index + 1].isalnum()
        for index, character in enumerate(planned.semantic)
        if character == "_"
    )
    characters_are_plain = all(
        character.isalnum() or character in {" ", "_", "-", ".", ",", "/"}
        for character in planned.semantic
    )
    if (
        plain_candidate != planned.semantic
        or not underscores_are_internal
        or not characters_are_plain
    ):
        return planned
    # Plain labels do not need the Mindmap root sentinel or Markdown whitespace
    # sentinels in either Architecture grammar. Keeping their source literal also
    # preserves the long-standing readable sidecar form.
    return replace(planned, source=planned.semantic)


def _architecture_icon(value: Any, *, fallback: str, context: str) -> str:
    if value is None or (type(value) is str and value == ""):
        return fallback
    if type(value) is not str:
        raise SerializationError(f"{context} must be text")
    normalized = value.casefold()
    return normalized if normalized in _ARCHITECTURE_ICONS else fallback


def _architecture_evidence_ids(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Keep valid record-local provenance without coercing malformed containers."""

    values = record.get("evidence_ids")
    if values is None:
        return ()
    if type(values) is not list or len(values) > MAX_EVIDENCE_REFS:
        return ()
    if any(
        type(value) is not str
        or not value
        or len(value) > MAX_ID_CHARS
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        for value in values
    ):
        return ()
    return tuple(values)


def _architecture_numeric_entity(character: str) -> str:
    return f"#{ord(character)};"


def _architecture_accessibility_source(text: str) -> str:
    """Encode active source spellings while preserving exact SVG metadata text.

    Mermaid's accessibility grammar decodes numeric entities that omit the usual
    leading ampersand. Encode literal entity introducers first, then split only
    scanner-active source spellings. This keeps source-authored ``&amp;`` distinct
    from a literal ampersand and introduces no zero-width canvas characters.
    """

    source = "".join(
        _architecture_numeric_entity(character) if character in {"&", "#", "<", ">"} else character
        for character in text
    )
    # Architecture's accessibility lexer stops at any raw ``%%`` pair, not only
    # a complete directive opening.
    source = source.replace("%%", f"%{_architecture_numeric_entity('%')}")
    source = source.replace("//", f"/{_architecture_numeric_entity('/')}")
    source = _SEQUENCE_CSS_IMPORT.sub(
        lambda match: (
            f"{match.group(0)[:3]}"
            f"{_architecture_numeric_entity(match.group(0)[3])}"
            f"{match.group(0)[4:]}"
        ),
        source,
    )
    source = _SEQUENCE_DANGEROUS_SCHEME.sub(
        lambda match: f"{match.group(0)[:-1]}{_architecture_numeric_entity(':')}",
        source,
    )
    source = _SEQUENCE_REMOTE_ICON.sub(
        lambda match: (
            f"{match.group(0)[:4]}"
            f"{_architecture_numeric_entity(match.group(0)[4])}"
            f"{match.group(0)[5:]}"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", _architecture_numeric_entity(":"))
        ),
        source,
    )
    source = _SEQUENCE_CALLBACK.sub(
        lambda match: f"{match.group(0)[:-1]}{_architecture_numeric_entity('(')}",
        source,
    )
    source = _SEQUENCE_CONTROL_WORD.sub(
        lambda match: (
            f"{match.group(0)[0]}"
            f"{_architecture_numeric_entity(match.group(0)[1])}"
            f"{match.group(0)[2:]}"
        ),
        source,
    )
    source = _SEQUENCE_CONFIG.sub(
        lambda match: f"{match.group(0)[:-1]}{_architecture_numeric_entity(':')}",
        source,
    )
    return source.replace("---", f"-{_architecture_numeric_entity('-')}-")


def _architecture_accessibility_plan(
    ir: Mapping[str, Any],
    *,
    services: tuple[ArchitectureServicePlan, ...],
    relations: tuple[ArchitectureRelationPlan, ...],
    accessibility_type: str,
    experimental: bool,
) -> ArchitectureAccessibilityPlan:
    semantic_ir: dict[str, Any] = {
        key: ir[key] for key in _ARCHITECTURE_METADATA_FIELDS if key in ir
    }
    semantic_ir["services"] = [
        {"id": service.source_id, "label": service.text.semantic} for service in services
    ]
    semantic_ir["edges"] = [
        {"source": relation.source_id, "target": relation.target_id} for relation in relations
    ]
    resolved = resolve_accessibility(
        semantic_ir,
        accessibility_type,
        experimental=experimental,
    )
    title = _semantic_terminal_text(resolved.title, context="architecture resolved title")
    description = _semantic_terminal_text(
        resolved.description,
        context="architecture resolved description",
    )
    return ArchitectureAccessibilityPlan(
        title_semantic=title,
        title_source=_architecture_accessibility_source(title),
        title_canvas=title,
        description_semantic=description,
        description_source=_architecture_accessibility_source(description),
        description_canvas=description,
    )


def _architecture_native_lines(plan: ArchitecturePlan) -> Iterator[str]:
    yield "architecture-beta"
    yield f"    accTitle: {plan.accessibility.title_source}"
    yield f"    accDescr: {plan.accessibility.description_source}"
    for group in plan.groups:
        yield f'    group {group.emitted_id}({group.icon})["{group.text.source}"]'
    for service in plan.services:
        suffix = f" in {service.group_emitted_id}" if service.group_emitted_id is not None else ""
        yield (f'    service {service.emitted_id}({service.icon})["{service.text.source}"]{suffix}')
    for relation in plan.relations:
        connector = "<-->" if relation.bidirectional else "-->"
        yield (
            f"    {relation.source_emitted_id}:{relation.source_side} {connector} "
            f"{relation.target_side}:{relation.target_emitted_id}"
        )


def _architecture_fallback_lines(plan: ArchitecturePlan) -> Iterator[str]:
    yield f"flowchart {plan.fallback_direction}"
    yield f"    accTitle: {plan.accessibility.title_source}"
    yield f"    accDescr: {plan.accessibility.description_source}"
    services_by_source = {service.source_id: service for service in plan.services}
    grouped_source_ids: set[str] = set()
    for group in plan.groups:
        if not group.member_source_ids:
            raise SerializationError(
                f"architecture group {group.source_id!r} has no services for Flowchart fallback"
            )
        yield f'    subgraph {group.emitted_id}["{group.text.source}"]'
        for source_id in group.member_source_ids:
            service = services_by_source[source_id]
            yield f'        {service.emitted_id}["{service.text.source}"]'
            grouped_source_ids.add(source_id)
        yield "    end"
    for service in plan.services:
        if service.source_id not in grouped_source_ids:
            yield f'    {service.emitted_id}["{service.text.source}"]'
    for relation in plan.relations:
        connector = "<-->" if relation.bidirectional else "-->"
        yield f"    {relation.source_emitted_id} {connector} {relation.target_emitted_id}"


def _bounded_architecture_source(lines: Iterable[str], *, terminal: str) -> str:
    parts: list[str] = []
    utf16_units = 0
    for line_count, line in enumerate(lines, start=1):
        # Mermaid source ends in a newline, so its split-line representation also
        # contains one trailing empty line. Enforce that budget before retaining
        # another generated statement.
        if line_count + 1 > _ARCHITECTURE_MAX_OUTPUT_LINES:
            raise SerializationError(
                f"{terminal} output exceeds source-line limit of {_ARCHITECTURE_MAX_OUTPUT_LINES}"
            )
        line_units = _mermaid_utf16_units(line) + 1
        if utf16_units + line_units > _ARCHITECTURE_MAX_OUTPUT_CHARS:
            raise SerializationError(
                f"{terminal} output exceeds UTF-16 source-character limit of "
                f"{_ARCHITECTURE_MAX_OUTPUT_CHARS}"
            )
        utf16_units += line_units
        parts.append(f"{line}\n")
    return "".join(parts)


def validate_architecture_terminal_plan(
    plan: ArchitecturePlan,
    *,
    emitted_type: str,
) -> None:
    """Preflight the exact Architecture-family terminal selected by runtime."""

    is_fallback = emitted_type.casefold().startswith("flowchart")
    source_line_count = (
        3 + (2 * len(plan.groups)) + len(plan.services) + len(plan.relations)
        if is_fallback
        else 3 + len(plan.groups) + len(plan.services) + len(plan.relations)
    )
    if source_line_count + 1 > _ARCHITECTURE_MAX_OUTPUT_LINES:
        terminal = "architecture Flowchart fallback" if is_fallback else "architecture"
        raise SerializationError(
            f"{terminal} output exceeds source-line limit of {_ARCHITECTURE_MAX_OUTPUT_LINES}"
        )
    if is_fallback:
        _bounded_architecture_source(
            _architecture_fallback_lines(plan),
            terminal="architecture Flowchart fallback",
        )
        return
    _bounded_architecture_source(
        _architecture_native_lines(plan),
        terminal="architecture",
    )


def plan_architecture_records(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    accessibility_type: str = "architecture",
) -> ArchitecturePlan:
    """Freeze Architecture identities, terminal labels, topology, and accessibility."""

    validated_ir = validated_architecture_accessibility_ir(ir)
    structure = plan_architecture_structure(dict(validated_ir))
    emitted_group_by_source = {
        placement.source_id: placement.emitted_id for placement in structure.group_placements
    }
    services: list[ArchitectureServicePlan] = []
    for index, (record, placement) in enumerate(
        zip(structure.services, structure.nodes, strict=True),
        start=1,
    ):
        group_value = record.get("group")
        group_source_id = (
            str(group_value) if group_value is not None and group_value != "" else None
        )
        services.append(
            ArchitectureServicePlan(
                source_record=record,
                source_id=placement.source_id,
                emitted_id=placement.emitted_id,
                text=_architecture_record_label(
                    record,
                    fields=("label", "name"),
                    fallback=placement.source_id,
                    context=f"architecture service {index} label",
                ),
                icon=_architecture_icon(
                    record.get("icon"),
                    fallback="server",
                    context=f"architecture service {index} icon",
                ),
                group_source_id=group_source_id,
                group_emitted_id=(
                    emitted_group_by_source[group_source_id]
                    if group_source_id is not None
                    else None
                ),
                evidence_ids=_architecture_evidence_ids(record),
            )
        )
    groups = tuple(
        ArchitectureGroupPlan(
            source_record=record,
            source_id=placement.source_id,
            emitted_id=placement.emitted_id,
            text=_architecture_record_label(
                record,
                fields=("label",),
                fallback=placement.emitted_id,
                context=f"architecture group {index} label",
            ),
            icon=_architecture_icon(
                record.get("icon"),
                fallback="cloud",
                context=f"architecture group {index} icon",
            ),
            member_source_ids=placement.member_source_ids,
            member_emitted_ids=placement.member_emitted_ids,
        )
        for index, (record, placement) in enumerate(
            zip(structure.groups, structure.group_placements, strict=True),
            start=1,
        )
    )
    emitted_service_by_source = {service.source_id: service.emitted_id for service in services}
    relations: list[ArchitectureRelationPlan] = []
    for index, edge in enumerate(structure.edges, start=1):
        source_side_value = edge.get("source_side")
        target_side_value = edge.get("target_side")
        source_side = (
            "R" if source_side_value is None or source_side_value == "" else source_side_value
        )
        target_side = (
            "L" if target_side_value is None or target_side_value == "" else target_side_value
        )
        if type(source_side) is not str or source_side not in {"L", "R", "T", "B"}:
            raise SerializationError(f"architecture edge {index} uses an unsupported source_side")
        if type(target_side) is not str or target_side not in {"L", "R", "T", "B"}:
            raise SerializationError(f"architecture edge {index} uses an unsupported target_side")
        bidirectional_value = edge.get("bidirectional")
        if bidirectional_value is None:
            bidirectional = False
        elif type(bidirectional_value) is bool:
            bidirectional = bidirectional_value
        else:
            raise SerializationError(f"architecture edge {index} bidirectional must be a boolean")
        relations.append(
            ArchitectureRelationPlan(
                source_record=edge,
                scene_id=f"generated-relation-{index}",
                source_id=edge["source"],
                target_id=edge["target"],
                source_emitted_id=emitted_service_by_source[edge["source"]],
                target_emitted_id=emitted_service_by_source[edge["target"]],
                source_side=source_side,
                target_side=target_side,
                bidirectional=bidirectional,
                evidence_ids=_architecture_evidence_ids(edge),
            )
        )
    direction_value = validated_ir.get("direction")
    fallback_direction = (
        direction_value.upper()
        if type(direction_value) is str and direction_value.upper() in {"TB", "BT", "LR", "RL"}
        else "LR"
    )
    services_tuple = tuple(services)
    relations_tuple = tuple(relations)
    accessibility = _architecture_accessibility_plan(
        validated_ir,
        services=services_tuple,
        relations=relations_tuple,
        accessibility_type=accessibility_type,
        experimental=experimental,
    )
    plan = ArchitecturePlan(
        services=services_tuple,
        groups=groups,
        relations=relations_tuple,
        accessibility=accessibility,
        fallback_direction=fallback_direction,
        compatibility_substitutions=any(
            item.text.compatibility_substitutions for item in (*services_tuple, *groups)
        ),
    )
    # The native grammar is the first terminal attempted for every Architecture
    # family. Reject an oversized plan before Scene/OCR can claim content that no
    # candidate is allowed to emit.
    validate_architecture_terminal_plan(plan, emitted_type="architecture")
    return plan


def enrich_architecture_accessibility_ir(
    ir: Mapping[str, Any],
    *,
    experimental: bool,
    architecture_plan: ArchitecturePlan | None = None,
) -> dict[str, Any]:
    """Return raw Architecture metadata plus accessibility from planned semantics."""

    validated_ir = validated_architecture_accessibility_ir(ir)
    plan = architecture_plan or plan_architecture_records(
        validated_ir,
        experimental=experimental,
    )
    accessibility = _architecture_accessibility_plan(
        validated_ir,
        services=plan.services,
        relations=plan.relations,
        accessibility_type="architecture",
        experimental=experimental,
    )
    return {
        **validated_ir,
        "acc_title": accessibility.title_semantic,
        "acc_description": accessibility.description_semantic,
    }


def _mindmap_source_line(node: MindmapNodePlan) -> str:
    indentation = "    " * node.depth
    if node.depth == 1:
        return f'{indentation}{node.emitted_id}(("{node.text.source}"))\n'
    return f'{indentation}{node.emitted_id}["{node.text.source}"]\n'


def plan_mindmap_records(ir: Mapping[str, Any]) -> MindmapPlan:
    """Freeze recursive identities and terminal text before any consumer runs."""

    validated_ir = validated_mindmap_accessibility_ir(ir)
    try:
        placements = plan_mindmap_nodes(validated_ir.get("root"))
    except MindmapStructureError as exc:
        raise SerializationError(str(exc)) from exc
    if len(placements) + 2 > _MINDMAP_MAX_OUTPUT_LINES:
        raise SerializationError(
            f"mindmap output exceeds source-line limit of {_MINDMAP_MAX_OUTPUT_LINES}"
        )
    source_units = _mermaid_utf16_units("mindmap\n")
    nodes: list[MindmapNodePlan] = []
    compatibility_substitutions = False
    for index, placement in enumerate(placements, start=1):
        raw_source_id = placement.source.get("id")
        source_id = (
            raw_source_id
            if type(raw_source_id) is str and raw_source_id != ""
            else f"mindmap-node-{index}"
        )
        text = _plan_mindmap_text(
            placement.label,
            context=f"mindmap node {index} label",
        )
        node = MindmapNodePlan(
            source_record=placement.source,
            source_id=source_id,
            emitted_id=placement.emitted_id,
            depth=placement.depth,
            parent_id=placement.parent_id,
            text=text,
        )
        source_units += _mermaid_utf16_units(_mindmap_source_line(node))
        if source_units > _MINDMAP_MAX_OUTPUT_CHARS:
            raise SerializationError(
                "mindmap output exceeds UTF-16 source-character limit of "
                f"{_MINDMAP_MAX_OUTPUT_CHARS}"
            )
        compatibility_substitutions |= text.compatibility_substitutions
        nodes.append(node)
    return MindmapPlan(
        nodes=tuple(nodes),
        compatibility_substitutions=compatibility_substitutions,
    )


def enrich_mindmap_accessibility_ir(
    ir: Mapping[str, Any],
    *,
    experimental: bool,
    mindmap_plan: MindmapPlan | None = None,
) -> dict[str, Any]:
    """Return raw Mindmap metadata plus accessibility derived from planned semantics."""

    validated_ir = validated_mindmap_accessibility_ir(ir)
    plan = mindmap_plan or plan_mindmap_records(validated_ir)
    semantic_ir: dict[str, Any] = {
        key: validated_ir[key] for key in _MINDMAP_METADATA_FIELDS if key in validated_ir
    }
    semantic_ir["nodes"] = [
        {"id": node.emitted_id, "label": node.text.semantic} for node in plan.nodes
    ]
    resolved = resolve_accessibility(semantic_ir, "mindmap", experimental=experimental)
    return {
        **validated_ir,
        "acc_title": resolved.title,
        "acc_description": resolved.description,
    }


def serialize_mindmap(ir: dict[str, Any], *, experimental: bool = False) -> str:
    del experimental
    plan = plan_mindmap_records(ir)
    # Mermaid 11.16 parses accTitle/accDescr as additional roots in mindmaps.
    # Preserve the hard render gate and keep resolved accessibility in typed IR.
    return "mindmap\n" + "".join(_mindmap_source_line(node) for node in plan.nodes)


_TIMELINE_SOURCE_SENTINEL = "\u200b"
_TIMELINE_METADATA_FIELDS = ("title", "description", "acc_title", "acc_description")
_TIMELINE_MAX_OUTPUT_CHARS = 50_000
_TIMELINE_MAX_OUTPUT_LINES = 5_000
_TIMELINE_MAX_VISIBLE_LABELS = MAX_SCENE_RELATIONS


@dataclass(frozen=True, slots=True)
class TimelineTextPlan:
    """One Timeline terminal across semantic IR, source, and SVG canvas text."""

    semantic: str
    source: str
    canvas: str


@dataclass(frozen=True, slots=True)
class TimelineEventPlan:
    """One ordered Timeline period and all of its visible event labels."""

    source_record: dict[str, Any]
    source_id: str
    scene_id: str
    period: TimelineTextPlan
    labels: tuple[TimelineTextPlan, ...]


@dataclass(frozen=True, slots=True)
class TimelinePlan:
    """Canonical Timeline records shared by serializer, Scene, OCR, and repair."""

    title: TimelineTextPlan | None
    events: tuple[TimelineEventPlan, ...]


def validated_timeline_accessibility_ir(ir: Mapping[str, Any]) -> dict[str, Any]:
    """Validate raw Timeline metadata and treat exact-empty fields as omitted."""

    for field in _TIMELINE_METADATA_FIELDS:
        value = ir.get(field)
        if value is None:
            continue
        if type(value) is not str:
            raise SerializationError(f"timeline {field} must be text when provided")
        if value == "":
            continue
        if len(value) > MAX_TEXT_CHARS:
            raise SerializationError(f"timeline {field} must be bounded non-empty text")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value
        ):
            raise SerializationError(f"timeline {field} contains unsupported text")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > MAX_TEXT_CHARS:
            raise SerializationError(f"timeline {field} must be bounded non-empty text")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - Cs is rejected above
            raise SerializationError(f"timeline {field} is not valid UTF-8 text") from exc
    return {
        key: value
        for key, value in ir.items()
        if key not in _TIMELINE_METADATA_FIELDS or value != ""
    }


def _plan_timeline_text(value: Any, *, context: str) -> TimelineTextPlan:
    semantic = _semantic_terminal_text(value, context=context)
    # Timeline's lexer treats title/section/comment tokens and ':' as grammar,
    # while its renderer decodes Mermaid's numeric entities in every terminal.
    # A generated leading sentinel keeps an encoded first ASCII character out of
    # the comment state. Encoding every ASCII code point also preserves spaces
    # around entity-like literals, which the native renderer otherwise drops.
    source = _TIMELINE_SOURCE_SENTINEL + "".join(
        f"#{ord(character)};" if ord(character) < 128 else character for character in semantic
    )
    return TimelineTextPlan(semantic=semantic, source=source, canvas=semantic)


def _timeline_alias_text(
    record: Mapping[str, Any],
    primary: str,
    secondary: str,
    *,
    fallback: str,
    context: str,
) -> TimelineTextPlan:
    planned: list[TimelineTextPlan] = []
    for field in (primary, secondary):
        value = record.get(field)
        if value is None or (type(value) is str and value == ""):
            continue
        planned.append(_plan_timeline_text(value, context=f"{context}.{field}"))
    if len(planned) == 2 and planned[0].semantic != planned[1].semantic:
        raise SerializationError(f"{context} has conflicting {primary}/{secondary} text")
    return planned[0] if planned else _plan_timeline_text(fallback, context=context)


def plan_timeline_records(ir: Mapping[str, Any]) -> TimelinePlan:
    """Freeze Timeline identities and terminal text before any consumer runs."""

    validated_ir = validated_timeline_accessibility_ir(ir)
    raw_events = validated_ir.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise SerializationError("timeline IR requires a non-empty events list")
    if len(raw_events) > MAX_SCENE_ELEMENTS:
        raise SerializationError("timeline event count exceeds the Scene element limit")

    raw_title = validated_ir.get("title")
    title = (
        _plan_timeline_text(raw_title, context="timeline title") if raw_title is not None else None
    )
    source_line_count = 1 + (1 if title is not None else 0) + len(raw_events)
    if source_line_count + 1 > _TIMELINE_MAX_OUTPUT_LINES:
        raise SerializationError(
            f"timeline output exceeds source-line limit of {_TIMELINE_MAX_OUTPUT_LINES}"
        )
    source_units = _mermaid_utf16_units("timeline\n")
    if title is not None:
        source_units += _mermaid_utf16_units(f"    title {title.source}\n")
        if source_units > _TIMELINE_MAX_OUTPUT_CHARS:
            raise SerializationError(
                "timeline output exceeds UTF-16 source-character limit of "
                f"{_TIMELINE_MAX_OUTPUT_CHARS}"
            )
    planned_events: list[TimelineEventPlan] = []
    source_ids: set[str] = set()
    visible_label_count = 0
    for index, event in enumerate(raw_events, start=1):
        if type(event) is not dict:
            raise SerializationError("timeline events must be objects")
        source_id = _sequence_source_id(
            event.get("id"),
            fallback=f"event-{index}",
            context=f"timeline event {index} id",
        )
        if source_id in source_ids:
            raise SerializationError("timeline event ids must be unique")
        source_ids.add(source_id)
        period = _timeline_alias_text(
            event,
            "time",
            "period",
            fallback="[unreadable time]",
            context=f"timeline event {index}",
        )

        raw_label = event.get("label")
        label_alias = None
        if raw_label is not None and not (type(raw_label) is str and raw_label == ""):
            label_alias = _plan_timeline_text(
                raw_label,
                context=f"timeline event {index}.label",
            )
        raw_labels = event.get("events")
        if raw_labels is not None and not isinstance(raw_labels, list):
            raise SerializationError(f"timeline event {index}.events must be a list")
        if raw_labels:
            if visible_label_count + len(raw_labels) > _TIMELINE_MAX_VISIBLE_LABELS:
                raise SerializationError("timeline visible event label count exceeds the limit")
            labels = tuple(
                _plan_timeline_text(
                    "[unreadable]" if type(value) is str and value == "" else value,
                    context=f"timeline event {index}.events[{label_index}]",
                )
                for label_index, value in enumerate(raw_labels, start=1)
            )
            if label_alias is not None and label_alias.semantic != labels[0].semantic:
                raise SerializationError(
                    f"timeline event {index} has conflicting label/events text"
                )
        else:
            labels = (
                label_alias
                if label_alias is not None
                else _plan_timeline_text(
                    "[unreadable]",
                    context=f"timeline event {index} label",
                ),
            )
        visible_label_count += len(labels)
        if visible_label_count > _TIMELINE_MAX_VISIBLE_LABELS:
            raise SerializationError("timeline visible event label count exceeds the limit")
        source_line = (
            f"    {period.source} : " + " : ".join(label.source for label in labels) + "\n"
        )
        source_units += _mermaid_utf16_units(source_line)
        if source_units > _TIMELINE_MAX_OUTPUT_CHARS:
            raise SerializationError(
                "timeline output exceeds UTF-16 source-character limit of "
                f"{_TIMELINE_MAX_OUTPUT_CHARS}"
            )
        planned_events.append(
            TimelineEventPlan(
                source_record=event,
                source_id=source_id,
                scene_id=f"timeline_event_{index}",
                period=period,
                labels=labels,
            )
        )

    return TimelinePlan(title=title, events=tuple(planned_events))


def enrich_timeline_accessibility_ir(
    ir: Mapping[str, Any],
    *,
    experimental: bool,
    timeline_plan: TimelinePlan | None = None,
) -> dict[str, Any]:
    """Return raw Timeline metadata plus accessibility derived from planned semantics."""

    validated_ir = validated_timeline_accessibility_ir(ir)
    plan = timeline_plan or plan_timeline_records(validated_ir)
    semantic_ir: dict[str, Any] = {
        key: validated_ir[key] for key in _TIMELINE_METADATA_FIELDS if key in validated_ir
    }
    semantic_ir["events"] = [
        {
            "id": event.source_id,
            "label": (
                f"{event.period.semantic}: " + "; ".join(label.semantic for label in event.labels)
            ),
        }
        for event in plan.events
    ]
    resolved = resolve_accessibility(semantic_ir, "timeline", experimental=experimental)
    return {
        **validated_ir,
        "acc_title": resolved.title,
        "acc_description": resolved.description,
    }


def serialize_timeline(ir: dict[str, Any], *, experimental: bool = False) -> str:
    del experimental
    plan = plan_timeline_records(ir)
    lines = ["timeline"]
    if plan.title is not None:
        lines.append(f"    title {plan.title.source}")
    lines.extend(
        f"    {event.period.source} : " + " : ".join(label.source for label in event.labels)
        for event in plan.events
    )
    code = "\n".join(lines) + "\n"
    if _mermaid_utf16_units(code) > _TIMELINE_MAX_OUTPUT_CHARS:
        raise SerializationError(
            f"timeline output exceeds UTF-16 source-character limit of {_TIMELINE_MAX_OUTPUT_CHARS}"
        )
    if code.count("\n") + 1 > _TIMELINE_MAX_OUTPUT_LINES:
        raise SerializationError(
            f"timeline output exceeds source-line limit of {_TIMELINE_MAX_OUTPUT_LINES}"
        )
    return code


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


def serialize_architecture(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    accessibility_type: str = "architecture",
) -> str:
    plan = plan_architecture_records(
        ir,
        experimental=experimental,
        accessibility_type=accessibility_type,
    )
    return _bounded_architecture_source(
        _architecture_native_lines(plan),
        terminal="architecture",
    )


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

    plan = plan_architecture_records(
        ir,
        experimental=experimental,
        accessibility_type=accessibility_type,
    )
    return _bounded_architecture_source(
        _architecture_fallback_lines(plan),
        terminal="architecture Flowchart fallback",
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
        plan_phase2_architecture_ir,
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
            architecture_compatibility_substitutions = False
            if _requested_type in {"c4", "deployment", "component"}:
                phase2_architecture_ir = plan_phase2_architecture_ir(_requested_type, ir)
                architecture_compatibility_substitutions = plan_architecture_records(
                    phase2_architecture_ir,
                    experimental=experimental,
                    accessibility_type=_requested_type,
                ).compatibility_substitutions
            code, emitted_type, fallback_reason = serialize_phase2(
                _requested_type,
                ir,
                experimental=experimental,
            )
            stability = stabilities[_requested_type]
            if emitted_type == _requested_type:
                native_warnings = (
                    (ARCHITECTURE_TEXT_COMPATIBILITY_WARNING,)
                    if architecture_compatibility_substitutions
                    else ()
                )
                return SerializationResult.native(
                    _requested_type,
                    code,
                    warnings=(
                        (BLOCK_ACCESSIBILITY_LIMITATION,)
                        if _requested_type == "block"
                        else native_warnings
                    ),
                    stability=stability,
                )
            fallback_warnings = [fallback_reason or f"Portable fallback from {_requested_type}."]
            if architecture_compatibility_substitutions:
                fallback_warnings.append(ARCHITECTURE_TEXT_COMPATIBILITY_WARNING)
            return SerializationResult.fallback(
                _requested_type,
                emitted_type,
                code,
                warnings=tuple(fallback_warnings),
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
    er_record_compatibility_substitutions = False
    er_plan = None
    state_record_compatibility_substitutions = False
    state_plan = None
    architecture_plan = None
    gantt_plan = None
    mindmap_plan = None
    sequence_plan = None
    timeline_plan = None
    if diagram_type == "pie":
        _validate_pie_explicit_accessibility_fields(ir)
    elif diagram_type == "xychart":
        _validate_xychart_explicit_accessibility_fields(ir)
    elif diagram_type == "quadrant":
        _validate_quadrant_explicit_accessibility_fields(ir)
    elif diagram_type == "sankey":
        _validate_sankey_explicit_accessibility_fields(ir)
    elif diagram_type == "architecture":
        accessibility_source_ir = validated_architecture_accessibility_ir(ir)
        architecture_plan = plan_architecture_records(
            accessibility_source_ir,
            experimental=experimental,
        )
    elif diagram_type in {"c4", "deployment", "component"}:
        accessibility_source_ir = validated_architecture_accessibility_ir(ir)
    elif diagram_type in {"treemap", "venn"}:
        accessibility_source_ir = _validated_chart_set_accessibility_ir(diagram_type, ir)
    elif diagram_type == "gantt":
        accessibility_source_ir = validated_gantt_metadata_ir(ir)
        gantt_plan = plan_gantt_records(accessibility_source_ir)
    elif diagram_type == "mindmap":
        accessibility_source_ir = validated_mindmap_accessibility_ir(ir)
        mindmap_plan = plan_mindmap_records(accessibility_source_ir)
    elif diagram_type == "sequence":
        accessibility_source_ir = validated_sequence_accessibility_ir(ir)
        sequence_plan = plan_sequence_records(accessibility_source_ir)
    elif diagram_type == "timeline":
        accessibility_source_ir = validated_timeline_accessibility_ir(ir)
        timeline_plan = plan_timeline_records(accessibility_source_ir)
    elif diagram_type == "er":
        from marker_mermaid.serializers_uml import (
            plan_er_records,
            validated_er_accessibility_ir,
        )

        accessibility_source_ir = validated_er_accessibility_ir(ir)
        er_plan = plan_er_records(accessibility_source_ir)
        er_record_compatibility_substitutions = er_plan.compatibility_substitutions
    elif diagram_type == "state":
        from marker_mermaid.serializers_uml import (
            plan_state_records,
            validated_state_accessibility_ir,
        )

        accessibility_source_ir = validated_state_accessibility_ir(ir)
        state_plan = plan_state_records(accessibility_source_ir)
        state_record_compatibility_substitutions = state_plan.compatibility_substitutions
    _ensure_extended_serializers()
    if diagram_type == "architecture":
        enriched_ir = enrich_architecture_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            architecture_plan=architecture_plan,
        )
    elif diagram_type == "gantt":
        enriched_ir = enrich_gantt_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            gantt_plan=gantt_plan,
        )
    elif diagram_type == "mindmap":
        enriched_ir = enrich_mindmap_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            mindmap_plan=mindmap_plan,
        )
    elif diagram_type == "sequence":
        enriched_ir = enrich_sequence_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            sequence_plan=sequence_plan,
        )
    elif diagram_type == "timeline":
        enriched_ir = enrich_timeline_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            timeline_plan=timeline_plan,
        )
    elif diagram_type == "er":
        from marker_mermaid.serializers_uml import enrich_er_accessibility_ir

        enriched_ir = enrich_er_accessibility_ir(
            accessibility_source_ir,
            experimental=experimental,
            er_plan=er_plan,
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
    if diagram_type == "architecture" and architecture_plan.compatibility_substitutions:
        if ARCHITECTURE_TEXT_COMPATIBILITY_WARNING not in result.warnings:
            result = replace(
                result,
                warnings=(*result.warnings, ARCHITECTURE_TEXT_COMPATIBILITY_WARNING),
            )
    elif diagram_type == "er":
        from marker_mermaid.serializers_uml import (
            ER_TEXT_COMPATIBILITY_WARNING,
            plan_er_accessibility,
        )

        if (
            er_record_compatibility_substitutions
            or plan_er_accessibility(
                enriched_ir,
                experimental=experimental,
                er_plan=er_plan,
            ).compatibility_substitutions
        ) and ER_TEXT_COMPATIBILITY_WARNING not in result.warnings:
            result = replace(
                result,
                warnings=(*result.warnings, ER_TEXT_COMPATIBILITY_WARNING),
            )
    elif diagram_type == "state":
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
    elif diagram_type == "sequence" and (
        sequence_plan.compatibility_substitutions
        or plan_sequence_accessibility(
            enriched_ir,
            experimental=experimental,
            sequence_plan=sequence_plan,
        ).compatibility_substitutions
    ):
        if SEQUENCE_TEXT_COMPATIBILITY_WARNING not in result.warnings:
            result = replace(
                result,
                warnings=(*result.warnings, SEQUENCE_TEXT_COMPATIBILITY_WARNING),
            )
    elif (
        diagram_type == "mindmap"
        and mindmap_plan.compatibility_substitutions
        and MINDMAP_TEXT_COMPATIBILITY_WARNING not in result.warnings
    ):
        result = replace(
            result,
            warnings=(*result.warnings, MINDMAP_TEXT_COMPATIBILITY_WARNING),
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
    edges: list[dict[str, Any]] = []
    for relation in scene.relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        source = relation.source_id
        target = relation.target_id
        if relation.arrow_at_start and not relation.arrow_at_end:
            source, target = target, source
        edges.append(
            {
                "source": source,
                "target": target,
                "label": relation.label,
                "style": relation.line_style,
                "bidirectional": relation.arrow_at_start and relation.arrow_at_end,
            }
        )
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
