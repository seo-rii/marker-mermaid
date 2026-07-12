"""Evidence-strict serializers for selected specialized diagram families.

Packet, Ishikawa, and TreeView have deterministic grammars in the pinned
Mermaid 11.16 runtime.  Each native serializer can still be asked for a
portable flowchart after runtime validation fails.  Event Modeling currently
renders unreliably in that runtime, so it always uses a loss-disclosed,
lane-aware flowchart representation.

The serializers accept only explicit structure.  Packet bit ranges are never
derived from neighboring fields, hierarchy identifiers are checked for
ambiguity, and every relation endpoint must resolve before Mermaid is emitted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError, serialize_flowchart

SPECIAL_TYPES = ("packet", "ishikawa", "treeview", "eventmodeling")

_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_DANGEROUS_SCHEME = re.compile(r"\b(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_REMOTE_ICON = re.compile(r"\b(?:fa|logos):|\biconify\b", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)
_ACTIVE_CALLBACK = re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE)
_CONTROL_WORD = re.compile(r"\b(?:click|style|classDef|linkStyle)\b", re.IGNORECASE)
_CONFIG = re.compile(r"\bconfig\s*:", re.IGNORECASE)
_FRAME_TYPES = {
    "command",
    "event",
    "readmodel",
    "processor",
    "ui",
    "unknown",
}
_MAX_PACKET_BIT = 4_095


def _text(value: Any, *, context: str) -> str:
    if value is None:
        raise SerializationError(f"{context} requires a label")
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        raise SerializationError(f"{context} requires a label")
    text = text.replace("\\", "\\\\").replace('"', "&quot;")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("%%{", "%%&#123;").replace("//", "/ /")
    text = _CSS_IMPORT.sub("@ import", text)
    text = _DANGEROUS_SCHEME.sub(lambda match: match.group(0)[:-1] + "&#58;", text)
    text = _REMOTE_ICON.sub(
        lambda match: (
            "icon ify"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", "&#58;")
        ),
        text,
    )
    text = _ACTIVE_CALLBACK.sub(lambda match: match.group(0).replace("(", "&#40;"), text)
    text = _CONTROL_WORD.sub(lambda match: match.group(0)[0] + "&#8203;" + match.group(0)[1:], text)
    text = _CONFIG.sub(lambda match: match.group(0).replace(":", "&#58;"), text)
    text = text.replace("---", "&#45;&#45;&#45;")
    return text.replace("#", "&#35;").replace(";", "&#59;")


def _source_id(value: Any, *, fallback: str, context: str) -> str:
    source_id = fallback if value is None else str(value)
    if source_id != source_id.strip() or not _ID.fullmatch(source_id):
        raise SerializationError(
            f"{context} id must match [A-Za-z_][A-Za-z0-9_-]* without surrounding whitespace"
        )
    return source_id


def _output_id(source_id: str) -> str:
    return source_id.replace("-", "_")


def _register_id(
    source_id: str, source_ids: set[str], output_ids: set[str], *, context: str
) -> str:
    output_id = _output_id(source_id)
    if source_id in source_ids:
        raise SerializationError(f"duplicate {context} id {source_id!r}")
    if output_id in output_ids:
        raise SerializationError(
            f"{context} ids are ambiguous after Mermaid normalization: {source_id!r}"
        )
    source_ids.add(source_id)
    output_ids.add(output_id)
    return output_id


def _accessibility(ir: dict[str, Any], *, experimental: bool) -> list[str]:
    title = ir.get("acc_title") or ir.get("title")
    description = ir.get("acc_description") or ir.get("description")
    if experimental:
        suffix = "This reconstruction is experimental and requires review."
        description = f"{description} {suffix}" if description else suffix
    lines: list[str] = []
    if title:
        lines.append(f"    accTitle: {_text(title, context='accessible title')}")
    if description:
        lines.append(f"    accDescr: {_text(description, context='accessible description')}")
    return lines


def _integer(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SerializationError(f"{context} requires an explicit non-negative integer")
    return value


def _edge_text(value: Any, *, context: str) -> str:
    return _text(value, context=context).replace("|", "&#124;")


def _packet_fields(ir: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    fields = ir.get("fields")
    if not isinstance(fields, list) or not fields:
        raise SerializationError("packet IR requires a non-empty fields list")
    normalized: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    output_ids: set[str] = set()
    previous_end = -1
    contiguous = True
    for index, field in enumerate(fields, start=1):
        if not isinstance(field, dict):
            raise SerializationError("packet fields must be objects")
        source_id = _source_id(
            field.get("id"), fallback=f"field_{index}", context=f"packet field {index}"
        )
        output_id = _register_id(source_id, source_ids, output_ids, context="packet field")
        if "start" not in field or "end" not in field:
            raise SerializationError(
                f"packet field {source_id!r} requires explicit start and end bit evidence"
            )
        start = _integer(field["start"], context=f"packet field {source_id!r} start")
        end = _integer(field["end"], context=f"packet field {source_id!r} end")
        if end < start:
            raise SerializationError(f"packet field {source_id!r} end precedes start")
        if end > _MAX_PACKET_BIT:
            raise SerializationError(
                f"packet field {source_id!r} exceeds the deterministic bit-range limit"
            )
        if start <= previous_end:
            raise SerializationError(f"packet field {source_id!r} overlaps or is out of order")
        if start != previous_end + 1:
            contiguous = False
        previous_end = end
        normalized.append(
            {
                "source_id": source_id,
                "output_id": output_id,
                "label": _text(field.get("label"), context=f"packet field {source_id!r}"),
                "start": start,
                "end": end,
            }
        )
    return normalized, contiguous


def _packet_fallback(
    ir: dict[str, Any],
    fields: list[dict[str, Any]],
    *,
    experimental: bool,
    reason: str,
) -> SerializationResult:
    nodes = [
        {
            "id": field["output_id"],
            "label": f"{field['start']}-{field['end']}: {field['label']}",
        }
        for field in fields
    ]
    edges = [
        {"source": left["output_id"], "target": right["output_id"]}
        for left, right in zip(fields, fields[1:], strict=False)
    ]
    code = serialize_flowchart(
        {**ir, "nodes": nodes, "edges": edges, "direction": "LR"},
        experimental=experimental,
    )
    return SerializationResult.fallback(
        "packet",
        "flowchart",
        code,
        warnings=(
            reason,
            "Packet bit-cell widths and grid layout were reduced to ordered range labels.",
        ),
        stability="experimental",
    )


def _serialize_packet(
    ir: dict[str, Any], *, experimental: bool, native_runtime_valid: bool
) -> SerializationResult:
    fields, contiguous = _packet_fields(ir)
    if not contiguous:
        return _packet_fallback(
            ir,
            fields,
            experimental=experimental,
            reason="Native Mermaid packet fields must be contiguous and start at bit zero.",
        )
    if not native_runtime_valid:
        return _packet_fallback(
            ir,
            fields,
            experimental=experimental,
            reason="CandidateValidator rejected the native Mermaid packet candidate.",
        )
    lines = ["packet-beta", *_accessibility(ir, experimental=experimental)]
    if ir.get("title"):
        lines.append(f"    title {_text(ir['title'], context='packet title')}")
    lines.extend(f'{field["start"]}-{field["end"]}: "{field["label"]}"' for field in fields)
    return SerializationResult.native("packet", "\n".join(lines) + "\n", stability="experimental")


def _hierarchy(
    root: Any,
    *,
    context: str,
    child_field: str = "children",
) -> list[tuple[dict[str, Any], str, str, str, int, str | None]]:
    if not isinstance(root, dict):
        raise SerializationError(f"{context} requires a root object")
    rows: list[tuple[dict[str, Any], str, str, str, int, str | None]] = []
    active: set[int] = set()
    source_ids: set[str] = set()
    output_ids: set[str] = set()

    def visit(node: dict[str, Any], depth: int, parent_id: str | None) -> None:
        if depth > 64 or len(rows) >= 2_000:
            raise SerializationError(f"{context} hierarchy exceeds deterministic resource limits")
        identity = id(node)
        if identity in active:
            raise SerializationError(f"{context} hierarchy contains a cycle")
        active.add(identity)
        try:
            index = len(rows) + 1
            source_id = _source_id(
                node.get("id"), fallback=f"node_{index}", context=f"{context} node {index}"
            )
            output_id = _register_id(source_id, source_ids, output_ids, context=f"{context} node")
            label = _text(node.get("label", node.get("name")), context=f"{context} node")
            rows.append((node, source_id, output_id, label, depth, parent_id))
            children = node.get(child_field, [])
            if not isinstance(children, list):
                raise SerializationError(f"{context} node {source_id!r} children must be a list")
            for child in children:
                if not isinstance(child, dict):
                    raise SerializationError(f"{context} children must be objects")
                visit(child, depth + 1, output_id)
        finally:
            active.remove(identity)

    visit(root, 0, None)
    return rows


def _hierarchy_flowchart(
    requested_type: str,
    ir: dict[str, Any],
    rows: Iterable[tuple[dict[str, Any], str, str, str, int, str | None]],
    *,
    experimental: bool,
    warnings: tuple[str, ...],
) -> SerializationResult:
    materialized = list(rows)
    nodes = [{"id": output_id, "label": label} for _, _, output_id, label, _, _ in materialized]
    edges = [
        {"source": parent_id, "target": output_id}
        for _, _, output_id, _, _, parent_id in materialized
        if parent_id is not None
    ]
    code = serialize_flowchart(
        {**ir, "nodes": nodes, "edges": edges, "direction": "LR"},
        experimental=experimental,
    )
    return SerializationResult.fallback(
        requested_type,
        "flowchart",
        code,
        warnings=warnings,
        stability="experimental",
    )


def _serialize_ishikawa(
    ir: dict[str, Any], *, experimental: bool, native_runtime_valid: bool
) -> SerializationResult:
    effect = ir.get("effect")
    if not isinstance(effect, dict):
        raise SerializationError("ishikawa IR requires an effect object")
    categories = ir.get("categories")
    if not isinstance(categories, list) or not categories:
        raise SerializationError("ishikawa IR requires non-empty categories")
    root = {**effect, "children": categories}
    rows = _hierarchy(root, context="ishikawa", child_field="children")
    if not native_runtime_valid:
        return _hierarchy_flowchart(
            "ishikawa",
            ir,
            rows,
            experimental=experimental,
            warnings=(
                "CandidateValidator rejected the native Mermaid Ishikawa candidate.",
                "Fishbone placement was reduced to directed cause containment.",
            ),
        )
    lines = ["ishikawa-beta"]
    lines.extend(f"{'  ' * depth}{label}" for _, _, _, label, depth, _ in rows)
    warnings = (
        "Mermaid 11.16 Ishikawa accessibility directives are not emitted because the grammar "
        "can interpret them as cause nodes; accessibility text remains in typed IR.",
    )
    return SerializationResult.native(
        "ishikawa", "\n".join(lines) + "\n", warnings=warnings, stability="experimental"
    )


def _serialize_treeview(
    ir: dict[str, Any], *, experimental: bool, native_runtime_valid: bool
) -> SerializationResult:
    rows = _hierarchy(ir.get("root"), context="treeview")
    if len(rows) < 2:
        raise SerializationError("treeview requires an explicit hierarchy below the root")
    if not native_runtime_valid:
        return _hierarchy_flowchart(
            "treeview",
            ir,
            rows,
            experimental=experimental,
            warnings=(
                "CandidateValidator rejected the native Mermaid TreeView candidate.",
                "Tree indentation was reduced to directed hierarchy edges.",
            ),
        )
    lines = ["treeView-beta", *_accessibility(ir, experimental=experimental)]
    lines.extend(f'{"  " * depth}"{label}"' for _, _, _, label, depth, _ in rows)
    return SerializationResult.native("treeview", "\n".join(lines) + "\n", stability="experimental")


def _eventmodeling_frames(
    ir: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    lanes = ir.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise SerializationError("eventmodeling IR requires non-empty lanes")
    normalized_lanes: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    output_ids: set[str] = set()
    frame_map: dict[str, str] = {}
    lane_ids: set[str] = set()
    lane_output_ids: set[str] = set()
    all_mermaid_ids: set[str] = set()
    for lane_index, lane in enumerate(lanes, start=1):
        if lane_index > 128:
            raise SerializationError("eventmodeling lanes exceed deterministic resource limits")
        if not isinstance(lane, dict):
            raise SerializationError("eventmodeling lanes must be objects")
        lane_id = _source_id(
            lane.get("id"),
            fallback=f"lane_{lane_index}",
            context=f"eventmodeling lane {lane_index}",
        )
        lane_output_id = _register_id(
            lane_id, lane_ids, lane_output_ids, context="eventmodeling lane"
        )
        lane_output_id = f"lane_{lane_output_id}"
        if lane_output_id in all_mermaid_ids:
            raise SerializationError(
                f"eventmodeling lane id collides after Mermaid normalization: {lane_id!r}"
            )
        all_mermaid_ids.add(lane_output_id)
        lane_frames = lane.get("frames")
        if not isinstance(lane_frames, list) or not lane_frames:
            raise SerializationError(f"eventmodeling lane {lane_id!r} requires non-empty frames")
        normalized_lane = {
            "id": lane_id,
            "output_id": lane_output_id,
            "label": _text(lane.get("label", lane_id), context=f"eventmodeling lane {lane_id!r}"),
            "frame_ids": [],
        }
        for frame_index, frame in enumerate(lane_frames, start=1):
            if len(frames) >= 2_000:
                raise SerializationError(
                    "eventmodeling frames exceed deterministic resource limits"
                )
            if not isinstance(frame, dict):
                raise SerializationError("eventmodeling frames must be objects")
            frame_id = _source_id(
                frame.get("id"),
                fallback=f"frame_{lane_index}_{frame_index}",
                context=f"eventmodeling frame {frame_index}",
            )
            output_id = _register_id(
                frame_id, source_ids, output_ids, context="eventmodeling frame"
            )
            if output_id in all_mermaid_ids:
                raise SerializationError(
                    f"eventmodeling frame id collides after Mermaid normalization: {frame_id!r}"
                )
            all_mermaid_ids.add(output_id)
            frame_type = str(frame.get("type") or "unknown").casefold()
            if frame_type not in _FRAME_TYPES:
                raise SerializationError(
                    f"eventmodeling frame {frame_id!r} has unsupported type {frame_type!r}"
                )
            label = _text(frame.get("label"), context=f"eventmodeling frame {frame_id!r}")
            time = frame.get("time")
            safe_time = (
                _text(time, context=f"eventmodeling frame {frame_id!r} time")
                if time is not None
                else None
            )
            rendered_label = f"[{frame_type}] {label}"
            if safe_time is not None:
                rendered_label = f"{safe_time} — {rendered_label}"
            normalized = {
                "source_id": frame_id,
                "output_id": output_id,
                "label": rendered_label,
            }
            frame_map[frame_id] = output_id
            frames.append(normalized)
            normalized_lane["frame_ids"].append(output_id)
        normalized_lanes.append(normalized_lane)
    return normalized_lanes, frames, frame_map


def _serialize_eventmodeling(ir: dict[str, Any], *, experimental: bool) -> SerializationResult:
    lanes, frames, frame_map = _eventmodeling_frames(ir)
    relations = ir.get("relations", [])
    if not isinstance(relations, list):
        raise SerializationError("eventmodeling relations must be a list")
    edge_lines: list[str] = []
    for relation in relations:
        if not isinstance(relation, dict):
            raise SerializationError("eventmodeling relations must be objects")
        source_key = str(relation.get("source"))
        target_key = str(relation.get("target"))
        source = frame_map.get(source_key)
        target = frame_map.get(target_key)
        if source is None or target is None:
            raise SerializationError(
                "eventmodeling relation references unknown endpoint: "
                f"{source_key!r} -> {target_key!r}"
            )
        label = relation.get("label")
        connector = "-->"
        if label is not None:
            connector = f"-->|{_edge_text(label, context='eventmodeling relation')}|"
        edge_lines.append(f"    {source} {connector} {target}")

    lines = ["flowchart LR", *_accessibility(ir, experimental=experimental)]
    frames_by_id = {frame["output_id"]: frame for frame in frames}
    for lane in lanes:
        lines.append(f'    subgraph {lane["output_id"]}["{lane["label"]}"]')
        for frame_id in lane["frame_ids"]:
            frame = frames_by_id[frame_id]
            lines.append(f'        {frame_id}["{frame["label"]}"]')
        lines.append("    end")
    lines.extend(edge_lines)
    return SerializationResult.fallback(
        "eventmodeling",
        "flowchart",
        "\n".join(lines) + "\n",
        warnings=(
            "Mermaid 11.16 Event Modeling rendering is not reliable in the pinned runtime.",
            "Time/reset-frame notation was reduced to lane subgraphs, typed labels, and "
            "observed relations.",
        ),
        stability="experimental",
    )


def serialize_special(
    diagram_type: str,
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> SerializationResult:
    """Serialize one supported special type with complete fallback metadata."""

    if not isinstance(ir, dict):
        raise SerializationError(f"{diagram_type} IR must be an object")
    if diagram_type == "packet":
        return _serialize_packet(
            ir,
            experimental=experimental,
            native_runtime_valid=native_runtime_valid,
        )
    if diagram_type == "ishikawa":
        return _serialize_ishikawa(
            ir,
            experimental=experimental,
            native_runtime_valid=native_runtime_valid,
        )
    if diagram_type == "treeview":
        return _serialize_treeview(
            ir,
            experimental=experimental,
            native_runtime_valid=native_runtime_valid,
        )
    if diagram_type == "eventmodeling":
        return _serialize_eventmodeling(ir, experimental=experimental)
    raise SerializationError(f"no special serializer for {diagram_type!r}")
