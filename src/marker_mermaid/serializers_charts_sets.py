"""Evidence-strict serializers for hierarchy-value and set-intersection charts.

Mermaid 11.16 provides experimental native grammars for treemaps and Venn
diagrams.  The serializers below use those grammars only for structures that
they can represent without synthesizing sizes.  Callers can set
``native_runtime_valid=False`` after a strict :class:`CandidateValidator`
failure to obtain a deterministic flowchart fallback for the same typed IR.

The public return contract is ``(code, emitted_type, fallback_reason)``.  A
``None`` reason means that the requested native grammar was emitted.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from numbers import Real
from typing import Any, TypeAlias

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.serializers import SerializationError, serialize_flowchart

ChartSetSerialization: TypeAlias = tuple[str, str, str | None]

_DANGEROUS_SCHEME = re.compile(r"\b(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_REMOTE_ICON = re.compile(r"\b(?:iconify|fa|logos):", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)


def _text(value: Any, *, context: str) -> str:
    """Return non-empty, single-line text that passes the strict scanner.

    The replacements preserve visible evidence while preventing a label from
    being interpreted as an active Mermaid feature.  This is intentionally
    stricter than Mermaid's quoted-string escaping because candidate security
    scanning happens before parsing.
    """

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
    text = _REMOTE_ICON.sub(lambda match: match.group(0)[:-1] + "&#58;", text)
    # Venn titles stop at '#' and ';'. Encoding both keeps all title text in a
    # single statement and is harmless in treemap and flowchart labels.
    return text.replace("#", "&#35;").replace(";", "&#59;")


def _number(value: Any, *, context: str, allow_zero: bool) -> float | int:
    """Validate an observed numeric value without coercing or inventing one."""

    if isinstance(value, bool) or not isinstance(value, Real):
        raise SerializationError(f"{context} requires an explicit numeric value")
    number = float(value)
    if not math.isfinite(number):
        raise SerializationError(f"{context} requires a finite numeric value")
    if number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise SerializationError(f"{context} requires a {qualifier} numeric value")
    return value


def _format_number(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)
    return format(float(value), ".15g")


def _accessibility(ir: dict[str, Any], *, experimental: bool) -> list[str]:
    resolved = resolve_accessibility(ir, "treemap", experimental=experimental)
    return [
        f"    accTitle: {_text(resolved.title, context='treemap accessible title')}",
        f"    accDescr: {_text(resolved.description, context='treemap accessible description')}",
    ]


def _treemap_structure(
    ir: dict[str, Any],
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], int, str, float | int | None]], bool]:
    root = ir.get("root")
    if not isinstance(root, dict):
        raise SerializationError("treemap IR requires a root object")

    rows: list[tuple[dict[str, Any], int, str, float | int | None]] = []
    active: set[int] = set()
    internal_values = False

    def visit(node: dict[str, Any], depth: int) -> None:
        nonlocal internal_values
        if depth > 128 or len(rows) >= 2_000:
            raise SerializationError("treemap hierarchy exceeds deterministic resource limits")
        identity = id(node)
        if identity in active:
            raise SerializationError("treemap hierarchy contains a cycle")
        active.add(identity)
        try:
            label = _text(node.get("label", node.get("name")), context="treemap node")
            children_value = node.get("children")
            if children_value is None:
                if "value" not in node:
                    raise SerializationError(
                        f"treemap leaf {label!r} requires an explicit numeric value"
                    )
                value: float | int | None = _number(
                    node["value"], context=f"treemap leaf {label!r}", allow_zero=False
                )
            else:
                if not isinstance(children_value, list) or not children_value:
                    raise SerializationError(
                        f"treemap internal node {label!r} requires non-empty children"
                    )
                if "value" in node:
                    value = _number(
                        node["value"],
                        context=f"treemap internal node {label!r}",
                        allow_zero=False,
                    )
                    internal_values = True
                else:
                    value = None
            rows.append((node, depth, label, value))
            if children_value is not None:
                for child in children_value:
                    if not isinstance(child, dict):
                        raise SerializationError("treemap children must be objects")
                    visit(child, depth + 1)
        finally:
            active.remove(identity)

    visit(root, 0)
    if root.get("children") is None:
        raise SerializationError("treemap requires an explicit hierarchy below the root")
    return root, rows, internal_values


def _treemap_flowchart_fallback(
    ir: dict[str, Any],
    rows: list[tuple[dict[str, Any], int, str, float | int | None]],
    *,
    experimental: bool,
    reason: str,
) -> ChartSetSerialization:
    node_ids = {id(node): f"N{index}" for index, (node, _, _, _) in enumerate(rows, start=1)}
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for node, _, label, value in rows:
        rendered_label = label
        if value is not None:
            rendered_label += f" (value: {_format_number(value)})"
        nodes.append({"id": node_ids[id(node)], "label": rendered_label})
        children = node.get("children")
        if isinstance(children, list):
            edges.extend(
                {"source": node_ids[id(node)], "target": node_ids[id(child)]} for child in children
            )
    code = serialize_flowchart(
        {**ir, "nodes": nodes, "edges": edges, "direction": "TB"},
        experimental=experimental,
    )
    return code, "flowchart", reason


def serialize_treemap(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> ChartSetSerialization:
    """Serialize a hierarchy whose leaves all have observed positive values.

    Native treemap sections cannot carry an explicitly observed value.  When
    such a value exists, the fallback labels it instead of silently discarding
    it or replacing it with the sum calculated by Mermaid.
    """

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    _, rows, internal_values = _treemap_structure(ir)
    if not native_runtime_valid:
        return _treemap_flowchart_fallback(
            ir,
            rows,
            experimental=experimental,
            reason=(
                "strict CandidateValidator rejected native treemap; emitted an "
                "evidence-preserving flowchart hierarchy"
            ),
        )
    if internal_values:
        return _treemap_flowchart_fallback(
            ir,
            rows,
            experimental=experimental,
            reason=(
                "flowchart fallback from treemap; Mermaid 11.16 native treemap cannot "
                "represent explicitly observed values on non-leaf hierarchy nodes"
            ),
        )

    lines = ["treemap-beta", *_accessibility(ir, experimental=experimental)]
    if ir.get("title"):
        lines.append(f"    title {_text(ir['title'], context='treemap title')}")
    for _, depth, label, value in rows:
        indent = "    " * (depth + 1)
        suffix = f": {_format_number(value)}" if value is not None else ""
        lines.append(f'{indent}"{label}"{suffix}')
    return "\n".join(lines) + "\n", "treemap", None


def _venn_structure(
    ir: dict[str, Any],
) -> tuple[
    list[tuple[dict[str, Any], str, str, float | int | None]],
    list[tuple[dict[str, Any], tuple[str, ...], str | None, float | int | None]],
    bool,
]:
    sets = ir.get("sets")
    intersections = ir.get("intersections")
    if not isinstance(sets, list) or len(sets) < 2:
        raise SerializationError("venn IR requires at least two explicit sets")
    if not isinstance(intersections, list) or not intersections:
        raise SerializationError("venn IR requires explicit intersections")

    normalized_sets: list[tuple[dict[str, Any], str, str, float | int | None]] = []
    id_map: dict[str, str] = {}
    rendered_ids: set[str] = set()
    source_values: dict[str, float | int] = {}
    has_all_values = True
    for index, item in enumerate(sets, start=1):
        if not isinstance(item, dict):
            raise SerializationError("venn sets must be objects")
        source_id = str(item.get("id") or "").strip()
        if not source_id:
            raise SerializationError(f"venn set {index} requires an id")
        if source_id in id_map:
            raise SerializationError(f"duplicate venn set id: {source_id}")
        output_id = re.sub(r"[^A-Za-z0-9_-]", "_", source_id).strip("_-")
        if not output_id or not re.match(r"^[A-Za-z_]", output_id):
            output_id = f"S_{output_id or index}"
        if output_id in rendered_ids:
            raise SerializationError("venn set ids collide after Mermaid normalization")
        rendered_ids.add(output_id)
        id_map[source_id] = output_id
        label = _text(item.get("label", item.get("name")), context=f"venn set {source_id!r}")
        if "value" in item:
            value: float | int | None = _number(
                item["value"], context=f"venn set {source_id!r}", allow_zero=True
            )
            source_values[source_id] = value
        else:
            value = None
            has_all_values = False
        normalized_sets.append((item, output_id, label, value))

    order = {source_id: index for index, source_id in enumerate(id_map)}
    normalized_intersections: list[
        tuple[dict[str, Any], tuple[str, ...], str | None, float | int | None]
    ] = []
    seen: set[tuple[str, ...]] = set()
    for index, item in enumerate(intersections, start=1):
        if not isinstance(item, dict):
            raise SerializationError("venn intersections must be objects")
        members = item.get("sets")
        if not isinstance(members, list) or len(members) < 2:
            raise SerializationError(f"venn intersection {index} requires at least two sets")
        source_members = [str(member) for member in members]
        if len(source_members) != len(set(source_members)):
            raise SerializationError(f"venn intersection {index} repeats a set")
        unknown = [member for member in source_members if member not in id_map]
        if unknown:
            raise SerializationError(
                f"venn intersection {index} references unknown set {unknown[0]!r}"
            )
        canonical = tuple(sorted(source_members, key=order.__getitem__))
        if canonical in seen:
            raise SerializationError("duplicate venn intersection structure")
        seen.add(canonical)
        output_members = tuple(id_map[member] for member in canonical)
        label_value = item.get("label", item.get("name"))
        label = (
            _text(label_value, context=f"venn intersection {index}")
            if label_value not in {None, ""}
            else None
        )
        if "value" in item:
            value = _number(item["value"], context=f"venn intersection {index}", allow_zero=True)
            for member in canonical:
                if member in source_values and float(value) > float(source_values[member]):
                    raise SerializationError(
                        f"venn intersection {index} exceeds observed size of set {member!r}"
                    )
        else:
            value = None
            has_all_values = False
        normalized_intersections.append((item, output_members, label, value))
    return normalized_sets, normalized_intersections, has_all_values


def _venn_flowchart_fallback(
    ir: dict[str, Any],
    sets: list[tuple[dict[str, Any], str, str, float | int | None]],
    intersections: list[tuple[dict[str, Any], tuple[str, ...], str | None, float | int | None]],
    *,
    experimental: bool,
    reason: str,
) -> ChartSetSerialization:
    nodes = [
        {
            "id": output_id,
            "label": label + (f" (value: {_format_number(value)})" if value is not None else ""),
            "shape": "circle",
        }
        for _, output_id, label, value in sets
    ]
    edges: list[dict[str, Any]] = []
    reserved_ids = {output_id for _, output_id, _, _ in sets}
    for index, (_, members, label, value) in enumerate(intersections, start=1):
        base_id = f"intersection_{index}"
        intersection_id = base_id
        suffix = 2
        while intersection_id in reserved_ids:
            intersection_id = f"{base_id}_{suffix}"
            suffix += 1
        reserved_ids.add(intersection_id)
        rendered_label = label or " ∩ ".join(members)
        if value is not None:
            rendered_label += f" (value: {_format_number(value)})"
        nodes.append({"id": intersection_id, "label": rendered_label, "shape": "round"})
        edges.extend(
            {"source": member, "target": intersection_id, "label": "intersects"}
            for member in members
        )
    code = serialize_flowchart(
        {**ir, "nodes": nodes, "edges": edges, "direction": "LR"},
        experimental=experimental,
    )
    return code, "flowchart", reason


def serialize_venn(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> ChartSetSerialization:
    """Serialize explicit sets and intersections without implicit area values."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    sets, intersections, has_all_values = _venn_structure(ir)
    if not native_runtime_valid:
        return _venn_flowchart_fallback(
            ir,
            sets,
            intersections,
            experimental=experimental,
            reason=(
                "strict CandidateValidator rejected native venn; emitted an "
                "evidence-preserving flowchart set graph"
            ),
        )
    if not has_all_values:
        return _venn_flowchart_fallback(
            ir,
            sets,
            intersections,
            experimental=experimental,
            reason=(
                "flowchart fallback from venn; one or more set/intersection sizes were "
                "not observed, so no numeric areas were synthesized"
            ),
        )

    lines = ["venn-beta"]
    if ir.get("title"):
        lines.append(f"    title {_text(ir['title'], context='venn title')}")
    for _, output_id, label, value in sets:
        assert value is not None
        lines.append(f'    set {output_id}["{label}"]: {_format_number(value)}')
    for _, members, label, value in intersections:
        assert value is not None
        label_suffix = f'["{label}"]' if label else ""
        lines.append(f"    union {','.join(members)}{label_suffix}: {_format_number(value)}")
    return "\n".join(lines) + "\n", "venn", None


CHART_SET_SERIALIZERS: dict[str, Callable[..., ChartSetSerialization]] = {
    "treemap": serialize_treemap,
    "venn": serialize_venn,
}


def serialize_chart_set(
    diagram_type: str,
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> ChartSetSerialization:
    """Dispatch a Phase 3 hierarchy/set chart and disclose its emitted grammar."""

    serializer = CHART_SET_SERIALIZERS.get(diagram_type)
    if serializer is None:
        raise SerializationError(f"no hierarchy/set chart serializer for {diagram_type}")
    return serializer(
        ir,
        experimental=experimental,
        native_runtime_valid=native_runtime_valid,
    )
