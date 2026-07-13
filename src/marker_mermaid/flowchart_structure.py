"""Shared deterministic identity/group planning for serializer and Scene consumers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_IR_DEPTH,
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_GROUPS,
    MAX_SCENE_RELATIONS,
    MAX_TEXT_CHARS,
)


class FlowchartStructureError(ValueError):
    """Raised when flat Flowchart group membership cannot be emitted exactly."""


class SequenceStructureError(ValueError):
    """Raised when participant identity cannot be emitted without ambiguity."""


class MindmapStructureError(ValueError):
    """Raised when a recursive Mindmap cannot be planned within resource limits."""


@dataclass(frozen=True, slots=True)
class FlowchartNodePlacement:
    source_id: str
    emitted_id: str


@dataclass(frozen=True, slots=True)
class FlowchartGroupPlacement:
    source_id: str
    emitted_id: str
    label: str
    member_source_ids: tuple[str, ...]
    member_emitted_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FlowchartStructurePlan:
    nodes: tuple[FlowchartNodePlacement, ...]
    groups: tuple[FlowchartGroupPlacement, ...]


@dataclass(frozen=True, slots=True)
class SwimlaneStructureInput:
    nodes: tuple[dict[str, Any], ...]
    groups: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class SequenceParticipantPlacement:
    source_id: str
    emitted_id: str
    label: str


@dataclass(frozen=True, slots=True)
class SequenceMessagePlacement:
    source: dict[str, Any]
    emitted_id: str
    source_id: str
    target_id: str


@dataclass(frozen=True, slots=True)
class SequenceStructurePlan:
    participants: tuple[SequenceParticipantPlacement, ...]
    messages: tuple[SequenceMessagePlacement, ...]


@dataclass(frozen=True, slots=True)
class MindmapNodePlacement:
    source: dict[str, Any]
    emitted_id: str
    label: str
    depth: int
    parent_id: str | None


def portable_identifier(value: str, fallback: str = "node") -> str:
    """Return the portable identifier form shared by planning and serialization."""

    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = f"n_{normalized}"
    return normalized


def plan_sequence_structure(participants: Any, messages: Any) -> SequenceStructurePlan:
    """Resolve the exact bounded participants and messages emitted by Sequence consumers."""

    if not isinstance(participants, list) or not participants:
        raise SequenceStructureError("sequence IR requires participants")
    if len(participants) > MAX_SCENE_ELEMENTS:
        raise SequenceStructureError("sequence participant count exceeds the Scene element limit")
    placements: list[SequenceParticipantPlacement] = []
    source_ids: set[str] = set()
    emitted_ids: set[str] = set()
    next_suffix_by_base: dict[str, int] = {}
    for index, participant in enumerate(participants, start=1):
        if isinstance(participant, str):
            source_id = participant
            label = participant
        elif isinstance(participant, dict):
            source_id = str(participant.get("id") or f"P{index}")
            label = str(participant.get("label") or source_id)
        else:
            raise SequenceStructureError("sequence participants must be strings or objects")
        if not source_id or len(source_id) > MAX_ID_CHARS:
            raise SequenceStructureError(
                "sequence participant id must be non-empty and within the Scene identifier limit"
            )
        if len(label) > MAX_TEXT_CHARS:
            raise SequenceStructureError("sequence participant label exceeds the Scene text limit")
        if source_id in source_ids:
            raise SequenceStructureError("sequence participant ids must be unique")
        base = portable_identifier(source_id, f"P{index}")
        emitted_id = base
        suffix = next_suffix_by_base.get(base, 2)
        while emitted_id in emitted_ids:
            emitted_id = f"{base}_{suffix}"
            suffix += 1
        next_suffix_by_base[base] = suffix
        if len(emitted_id) > MAX_ID_CHARS:
            raise SequenceStructureError(
                "sequence emitted participant id exceeds the Scene identifier limit"
            )
        source_ids.add(source_id)
        emitted_ids.add(emitted_id)
        placements.append(SequenceParticipantPlacement(source_id, emitted_id, label))
    if not isinstance(messages, list):
        raise SequenceStructureError("sequence messages must be a list of objects")
    if len(messages) > MAX_SCENE_RELATIONS:
        raise SequenceStructureError("sequence message count exceeds the Scene relation limit")
    emitted_by_source = {
        participant.source_id: participant.emitted_id for participant in placements
    }
    message_placements: list[SequenceMessagePlacement] = []
    for message in messages:
        if not isinstance(message, dict):
            raise SequenceStructureError("sequence messages must be objects")
        for field in ("id", "source", "target", "label", "style"):
            value = message.get(field)
            if value is not None and not isinstance(value, str):
                raise SequenceStructureError(f"sequence message {field} must be a string")
        message_id = message.get("id")
        if message_id is not None and len(message_id) > MAX_ID_CHARS:
            raise SequenceStructureError("sequence message id exceeds the Scene identifier limit")
        label = message.get("label")
        if label is not None and len(label) > MAX_TEXT_CHARS:
            raise SequenceStructureError("sequence message label exceeds the Scene text limit")
        source_id = emitted_by_source.get(message.get("source"))
        target_id = emitted_by_source.get(message.get("target"))
        if source_id is None or target_id is None:
            continue
        emitted_id = f"generated-relation-{len(message_placements) + 1}"
        message_placements.append(
            SequenceMessagePlacement(message, emitted_id, source_id, target_id)
        )
    return SequenceStructurePlan(tuple(placements), tuple(message_placements))


def plan_mindmap_nodes(root: Any) -> tuple[MindmapNodePlacement, ...]:
    """Return the exact preorder IDs, labels, and parents emitted by Mindmap serialization."""

    if not isinstance(root, dict):
        raise MindmapStructureError("mindmap IR requires a root object")
    placements: list[MindmapNodePlacement] = []
    pending: list[tuple[dict[str, Any], int, str | None]] = [(root, 1, None)]
    while pending:
        node, depth, parent_id = pending.pop()
        if depth > MAX_IR_DEPTH:
            raise MindmapStructureError("mindmap hierarchy exceeds the nesting depth limit")
        node_number = len(placements) + 1
        if node_number > MAX_SCENE_ELEMENTS:
            raise MindmapStructureError("mindmap node count exceeds the Scene element limit")
        emitted_id = "root" if depth == 1 else f"node_{node_number}"
        label = str(node.get("label") or node.get("text") or "[unreadable]")
        if len(label) > MAX_TEXT_CHARS:
            raise MindmapStructureError("mindmap node label exceeds the Scene text limit")
        placements.append(MindmapNodePlacement(node, emitted_id, label, depth, parent_id))
        children = node.get("children", [])
        if not isinstance(children, list):
            raise MindmapStructureError("mindmap children must be a list of objects")
        if any(not isinstance(child, dict) for child in children):
            raise MindmapStructureError("mindmap children must be objects")
        pending.extend((child, depth + 1, emitted_id) for child in reversed(children))
    return tuple(placements)


def prepare_swimlane_structure(lanes: Any) -> SwimlaneStructureInput:
    """Flatten lanes while assigning the same deterministic node fallbacks everywhere."""

    if not isinstance(lanes, list) or not lanes:
        raise FlowchartStructureError("swimlane IR requires lanes")
    if len(lanes) > MAX_SCENE_GROUPS:
        raise FlowchartStructureError("swimlane lane count exceeds the Scene group limit")
    nodes: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    node_index = 0
    for lane_index, lane in enumerate(lanes, start=1):
        if not isinstance(lane, dict):
            raise FlowchartStructureError("swimlane lanes must be objects")
        if any(field in lane for field in ("parent", "parent_id")) or any(
            lane.get(field) for field in ("children", "groups", "lanes")
        ):
            raise FlowchartStructureError("nested swimlane lanes are not supported")
        lane_nodes = lane.get("nodes")
        if not isinstance(lane_nodes, list) or not lane_nodes:
            raise FlowchartStructureError("swimlane lane requires at least one node")
        member_ids: list[str] = []
        for node in lane_nodes:
            if not isinstance(node, dict):
                raise FlowchartStructureError("swimlane nodes must be objects")
            node_index += 1
            if node_index > MAX_SCENE_ELEMENTS:
                raise FlowchartStructureError("swimlane node count exceeds the Scene element limit")
            source_id = str(node.get("id") or f"N{node_index}")
            nodes.append({**node, "id": source_id})
            member_ids.append(source_id)
        groups.append(
            {
                "id": lane.get("id") or f"lane_{lane_index}",
                "label": (
                    lane.get("label") if "label" in lane else lane.get("id") or f"lane_{lane_index}"
                ),
                "member_ids": member_ids,
                "role": "lane",
                **({"bbox": lane["bbox"]} if "bbox" in lane else {}),
            }
        )
    return SwimlaneStructureInput(tuple(nodes), tuple(groups))


def plan_flowchart_structure(nodes: Any, groups: Any) -> FlowchartStructurePlan:
    """Plan flat, disjoint groups without guessing membership or emitted IDs."""

    if not isinstance(nodes, list) or not nodes:
        raise FlowchartStructureError("flowchart requires at least one node")
    if len(nodes) > MAX_SCENE_ELEMENTS:
        raise FlowchartStructureError("flowchart node count exceeds the Scene element limit")
    if not isinstance(groups, list):
        raise FlowchartStructureError("flowchart groups must be a list")
    if len(groups) > MAX_SCENE_GROUPS:
        raise FlowchartStructureError("flowchart group count exceeds the Scene group limit")

    node_placements: list[FlowchartNodePlacement] = []
    emitted_node_ids: set[str] = set()
    source_id_counts: dict[str, int] = {}
    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            raise FlowchartStructureError("flowchart nodes must be objects")
        source_id = str(node.get("id") or f"N{index}")
        if len(source_id) > MAX_ID_CHARS:
            raise FlowchartStructureError("flowchart node id exceeds the Scene identifier limit")
        emitted_id = portable_identifier(source_id, f"N{index}")
        suffix = 2
        base = emitted_id
        while emitted_id in emitted_node_ids:
            emitted_id = f"{base}_{suffix}"
            suffix += 1
        if len(emitted_id) > MAX_ID_CHARS:
            raise FlowchartStructureError(
                "flowchart emitted node id exceeds the Scene identifier limit"
            )
        emitted_node_ids.add(emitted_id)
        source_id_counts[source_id] = source_id_counts.get(source_id, 0) + 1
        node_placements.append(FlowchartNodePlacement(source_id, emitted_id))

    if groups and any(count != 1 for count in source_id_counts.values()):
        raise FlowchartStructureError("flowchart groups require unique node ids")
    emitted_by_source = {node.source_id: node.emitted_id for node in node_placements}

    group_placements: list[FlowchartGroupPlacement] = []
    emitted_group_ids: set[str] = set()
    grouped_source_ids: set[str] = set()
    for index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise FlowchartStructureError("flowchart groups must contain objects")
        if any(field in group for field in ("parent", "parent_id")) or any(
            group.get(field) for field in ("children", "groups")
        ):
            raise FlowchartStructureError("nested flowchart groups are not supported")
        source_group_id = str(group.get("id") or "").strip()
        if not source_group_id or len(source_group_id) > MAX_ID_CHARS:
            raise FlowchartStructureError("flowchart group requires a bounded non-empty id")
        emitted_group_id = portable_identifier(source_group_id, f"group_{index}")
        if len(emitted_group_id) > MAX_ID_CHARS:
            raise FlowchartStructureError(
                "flowchart emitted group id exceeds the Scene identifier limit"
            )
        if emitted_group_id in emitted_node_ids:
            raise FlowchartStructureError("flowchart group id collides with a node id")
        if emitted_group_id in emitted_group_ids:
            raise FlowchartStructureError("flowchart group ids must be unique after normalization")
        members = group.get("member_ids")
        if not isinstance(members, list) or not members:
            raise FlowchartStructureError("flowchart group requires at least one member")
        if len(members) > MAX_SCENE_ELEMENTS:
            raise FlowchartStructureError(
                "flowchart group membership exceeds the Scene member limit"
            )
        if any(not isinstance(member, str) or not member for member in members):
            raise FlowchartStructureError("flowchart group member ids must be non-empty strings")
        member_source_ids = tuple(members)
        if len(member_source_ids) != len(set(member_source_ids)):
            raise FlowchartStructureError("flowchart group member ids must be unique")
        unknown = [member for member in member_source_ids if member not in emitted_by_source]
        if unknown:
            raise FlowchartStructureError(f"flowchart group references unknown node: {unknown[0]}")
        overlap = grouped_source_ids.intersection(member_source_ids)
        if overlap:
            raise FlowchartStructureError(
                f"flowchart node belongs to multiple groups: {sorted(overlap)[0]}"
            )
        emitted_group_ids.add(emitted_group_id)
        grouped_source_ids.update(member_source_ids)
        raw_label = group.get("label")
        if raw_label is None or raw_label == "":
            label = source_group_id
        elif not isinstance(raw_label, str):
            raise FlowchartStructureError("flowchart group label must be a string")
        else:
            label = raw_label
        if len(label) > MAX_TEXT_CHARS:
            raise FlowchartStructureError("flowchart group label exceeds the Scene text limit")
        group_placements.append(
            FlowchartGroupPlacement(
                source_id=source_group_id,
                emitted_id=emitted_group_id,
                label=label,
                member_source_ids=member_source_ids,
                member_emitted_ids=tuple(emitted_by_source[member] for member in member_source_ids),
            )
        )
    return FlowchartStructurePlan(tuple(node_placements), tuple(group_placements))


def unique_portable_id_aliases(source_ids: list[str]) -> dict[str, str]:
    """Map collision-free emitted-style aliases back to their logical source IDs."""

    ambiguous_source_ids, ambiguous_emitted_ids = ambiguous_portable_ids(source_ids)
    aliases: dict[str, list[str]] = {}
    for source_id in source_ids:
        if source_id in ambiguous_source_ids:
            continue
        aliases.setdefault(portable_identifier(source_id), []).append(source_id)
    return {
        emitted_id: logical_ids[0]
        for emitted_id, logical_ids in aliases.items()
        if len(logical_ids) == 1 and emitted_id not in ambiguous_emitted_ids
    }


def ambiguous_portable_ids(source_ids: list[str]) -> tuple[set[str], set[str]]:
    """Return logical/emitted IDs that participate in normalization collisions."""

    placements: list[tuple[str, str]] = []
    occupied: set[str] = set()
    by_base: dict[str, list[str]] = {}
    for source_id in source_ids:
        base = portable_identifier(source_id)
        by_base.setdefault(base, []).append(source_id)
        emitted_id = base
        suffix = 2
        while emitted_id in occupied:
            emitted_id = f"{base}_{suffix}"
            suffix += 1
        occupied.add(emitted_id)
        placements.append((source_id, emitted_id))

    ambiguous_source_ids = {
        source_id
        for source_ids_for_base in by_base.values()
        if len(source_ids_for_base) > 1
        for source_id in source_ids_for_base
    }
    source_id_set = set(source_ids)
    changed = True
    ambiguous_emitted_ids: set[str] = set()
    while changed:
        changed = False
        for source_id, emitted_id in placements:
            if source_id not in ambiguous_source_ids:
                continue
            if emitted_id not in ambiguous_emitted_ids:
                ambiguous_emitted_ids.add(emitted_id)
                changed = True
            if emitted_id in source_id_set and emitted_id not in ambiguous_source_ids:
                ambiguous_source_ids.add(emitted_id)
                changed = True
    return ambiguous_source_ids, ambiguous_emitted_ids
