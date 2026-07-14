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
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.models import MAX_ID_CHARS
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
_ISHIKAWA_HEADER = re.compile(r"^ishikawa(?:-beta)?(?=$|\W)", re.IGNORECASE)
_ENTITY_LITERAL = re.compile(
    r"&(?P<body>#[0-9]+|#x[0-9A-F]+|[A-Z][A-Z0-9]+);",
    re.IGNORECASE,
)
_FRAME_TYPES = {
    "command",
    "event",
    "readmodel",
    "processor",
    "ui",
    "unknown",
}
_MAX_PACKET_BIT = 4_095
_MAX_HIERARCHY_DEPTH = 64
_MAX_HIERARCHY_NODES = 2_000
_MAX_FLOWCHART_EDGES = 500
MAX_SPECIAL_OUTPUT_CHARS = 50_000
MAX_SPECIAL_OUTPUT_LINES = 5_000
_ZERO_WIDTH_SPACE = "\u200b"
_FLOWCHART_COMPATIBILITY_WARNING = (
    "Flowchart fallback replaced grammar-conflicting quote or backslash characters "
    "with visible compatibility glyphs."
)
_ENTITY_COMPATIBILITY_WARNING = (
    "Entity-like literal text uses visible fullwidth ampersand and number-sign glyphs "
    "(＆ and ＃) because Mermaid 11.16 cannot preserve every literal entity form."
)


@dataclass(frozen=True, slots=True)
class PacketFieldPlan:
    """One packet field whose identity and explicit bit evidence are preserved."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    label: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class PacketPlan:
    """Validated packet fields shared by native, fallback, and Scene consumers."""

    fields: tuple[PacketFieldPlan, ...]
    contiguous: bool


@dataclass(frozen=True, slots=True)
class SpecialHierarchyNodePlan:
    """One validated node in an Ishikawa or TreeView hierarchy."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    label: str
    depth: int
    parent_source_id: str | None
    parent_emitted_id: str | None


def _semantic_text(value: Any, *, context: str) -> str:
    if value is None:
        raise SerializationError(f"{context} requires a label")
    source = str(value)
    if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in source):
        raise SerializationError(f"{context} contains unsupported control characters")
    text = " ".join(source.split())
    if not text:
        raise SerializationError(f"{context} requires a label")
    return text


def _record_label(record: Mapping[str, Any], *, context: str) -> str:
    """Resolve the label/name compatibility alias without hiding conflicts."""

    label_present = "label" in record and record.get("label") is not None
    name_present = "name" in record and record.get("name") is not None
    if label_present and name_present:
        label = _semantic_text(record.get("label"), context=f"{context} label")
        name = _semantic_text(record.get("name"), context=f"{context} name")
        if label != name:
            raise SerializationError(f"{context} label and name aliases must agree")
        return label
    if label_present:
        return _semantic_text(record.get("label"), context=context)
    return _semantic_text(record.get("name"), context=context)


def _entity_compatibility_text(text: str) -> tuple[str, bool]:
    """Keep entity-like evidence visible instead of allowing SVG entity decoding."""

    substituted = _ENTITY_LITERAL.search(text) is not None
    return (
        _ENTITY_LITERAL.sub(
            lambda match: (
                f"＆＃{match.group('body')[1:]};"
                if match.group("body").startswith("#")
                else f"＆{match.group('body')};"
            ),
            text,
        ),
        substituted,
    )


def _special_directive_text(value: Any, *, context: str) -> str:
    text, _substituted = _entity_compatibility_text(_semantic_text(value, context=context))
    return _neutralize_active_text(text)


def _preflight_special_code(code: str, *, diagram_type: str) -> str:
    """Apply CandidateValidator's default source budgets before returning code."""

    if code.count("\n") + 1 > MAX_SPECIAL_OUTPUT_LINES:
        raise SerializationError(
            f"{diagram_type} output exceeds source-line limit of {MAX_SPECIAL_OUTPUT_LINES}"
        )
    if len(code) > MAX_SPECIAL_OUTPUT_CHARS:
        raise SerializationError(
            f"{diagram_type} output exceeds source-character limit of {MAX_SPECIAL_OUTPUT_CHARS}"
        )
    return code


def _neutralize_active_text(text: str) -> str:
    text = text.replace("%%", f"%{_ZERO_WIDTH_SPACE}%").replace("//", f"/{_ZERO_WIDTH_SPACE}/")
    text = text.replace("<", f"<{_ZERO_WIDTH_SPACE}")
    text = _CSS_IMPORT.sub(
        lambda match: f"{match.group(0)[:3]}{_ZERO_WIDTH_SPACE}{match.group(0)[3:]}", text
    )
    text = _DANGEROUS_SCHEME.sub(lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:", text)
    text = _REMOTE_ICON.sub(
        lambda match: (
            f"icon{_ZERO_WIDTH_SPACE}ify"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", f"{_ZERO_WIDTH_SPACE}:")
        ),
        text,
    )
    text = _ACTIVE_CALLBACK.sub(
        lambda match: match.group(0).replace("(", f"{_ZERO_WIDTH_SPACE}("), text
    )
    text = _CONTROL_WORD.sub(
        lambda match: f"{match.group(0)[0]}{_ZERO_WIDTH_SPACE}{match.group(0)[1:]}", text
    )
    text = _CONFIG.sub(lambda match: match.group(0).replace(":", f"{_ZERO_WIDTH_SPACE}:"), text)
    text = text.replace("---", f"-{_ZERO_WIDTH_SPACE}--")
    return text


def _directive_text(value: Any, *, context: str) -> str:
    return _neutralize_active_text(_semantic_text(value, context=context))


def _flowchart_source_text(value: Any, *, context: str) -> str:
    text = _directive_text(value, context=context)
    return (
        text.replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("#", f"{_ZERO_WIDTH_SPACE}#")
        .replace('"', "″")
        .replace("\\", "∖")
    )


def _flowchart_code_text(value: Any, *, context: str) -> str:
    return _flowchart_source_text(value, context=context)


def _packet_text(value: Any, *, context: str) -> str:
    return _special_directive_text(value, context=context).replace("\\", "\\\\").replace('"', '\\"')


def _ishikawa_text(value: Any, *, context: str) -> str:
    text = _special_directive_text(value, context=context)
    if _ISHIKAWA_HEADER.match(text):
        text = f"{text[0]}{_ZERO_WIDTH_SPACE}{text[1:]}"
    return text


def _source_id(value: Any, *, fallback: str, context: str) -> str:
    source_id = fallback if value is None else str(value)
    if source_id != source_id.strip() or not _ID.fullmatch(source_id):
        raise SerializationError(
            f"{context} id must match [A-Za-z_][A-Za-z0-9_-]* without surrounding whitespace"
        )
    if len(source_id) > MAX_ID_CHARS:
        raise SerializationError(f"{context} id exceeds source identifier limit of {MAX_ID_CHARS}")
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
    if len(output_id) > MAX_ID_CHARS:
        raise SerializationError(
            f"{context} id exceeds normalized identifier limit of {MAX_ID_CHARS}"
        )
    source_ids.add(source_id)
    output_ids.add(output_id)
    return output_id


def _register_prefixed_id(
    source_id: str,
    prefix: str,
    source_ids: set[str],
    emitted_ids: set[str],
    *,
    context: str,
) -> str:
    normalized_id = _output_id(source_id)
    emitted_id = f"{prefix}{normalized_id}"
    if source_id in source_ids:
        raise SerializationError(f"duplicate {context} id {source_id!r}")
    if emitted_id in emitted_ids:
        raise SerializationError(
            f"{context} ids are ambiguous after Mermaid normalization: {source_id!r}"
        )
    if len(normalized_id) > MAX_ID_CHARS:
        raise SerializationError(
            f"{context} id exceeds normalized identifier limit of {MAX_ID_CHARS}"
        )
    if len(emitted_id) > MAX_ID_CHARS:
        raise SerializationError(f"{context} id exceeds emitted identifier limit of {MAX_ID_CHARS}")
    source_ids.add(source_id)
    emitted_ids.add(emitted_id)
    return emitted_id


def _safe_accessibility_values(
    ir: dict[str, Any], diagram_type: str, *, experimental: bool
) -> tuple[str, str, tuple[str, ...]]:
    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    title = _semantic_text(resolved.title, context="accessible title")
    description = _semantic_text(resolved.description, context="accessible description")
    warnings: list[str] = []
    display_name = diagram_type.replace("_", " ").title()
    if _neutralize_active_text(title) != title:
        title = f"{display_name} reconstruction"
        warnings.append(
            "Unsafe accessible title remained in typed IR and was replaced by a generic SVG title."
        )
    if _neutralize_active_text(description) != description:
        description = (
            f"{display_name} reconstruction from extracted visual evidence; "
            "original accessibility text remains in review metadata."
        )
        warnings.append(
            "Unsafe accessible description remained in typed IR and was replaced by a generic "
            "SVG description."
        )
    if diagram_type in {"packet", "ishikawa", "treeview"}:
        title, title_substituted = _entity_compatibility_text(title)
        description, description_substituted = _entity_compatibility_text(description)
        if title_substituted or description_substituted:
            warnings.append(_ENTITY_COMPATIBILITY_WARNING)
    return title, description, tuple(warnings)


def _accessibility(
    ir: dict[str, Any], diagram_type: str, *, experimental: bool
) -> tuple[list[str], tuple[str, ...]]:
    title, description, warnings = _safe_accessibility_values(
        ir, diagram_type, experimental=experimental
    )
    return (
        [
            f"    accTitle: {title}",
            f"    accDescr: {description}",
        ],
        warnings,
    )


def _integer(value: Any, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SerializationError(f"{context} requires an explicit non-negative integer")
    return value


def _edge_text(value: Any, *, context: str) -> str:
    return _flowchart_code_text(value, context=context).replace("|", "∣")


def _flowchart_compatibility_warnings(texts: Iterable[Any]) -> tuple[str, ...]:
    return (
        (_FLOWCHART_COMPATIBILITY_WARNING,)
        if any(char in str(text) for text in texts for char in '"\\')
        else ()
    )


def _serialize_flowchart_fallback(
    ir: dict[str, Any],
    diagram_type: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    experimental: bool,
) -> tuple[str, tuple[str, ...]]:
    title, description, accessibility_warnings = _safe_accessibility_values(
        ir, diagram_type, experimental=experimental
    )
    compatibility_texts = [
        title,
        description,
        *(str(node.get("label") or "") for node in nodes),
        *(str(edge.get("label") or "") for edge in edges),
    ]
    compatibility_warnings = _flowchart_compatibility_warnings(compatibility_texts)
    use_entity_compatibility = diagram_type in {"packet", "ishikawa", "treeview"}
    entity_substituted = use_entity_compatibility and any(
        _ENTITY_LITERAL.search(str(text)) for text in compatibility_texts
    )

    def safe_fallback_text(value: Any, *, context: str) -> str:
        semantic = _semantic_text(value, context=context)
        if use_entity_compatibility:
            semantic, _substituted = _entity_compatibility_text(semantic)
        return _flowchart_source_text(semantic, context=context)

    safe_nodes = [
        {
            **node,
            "label": safe_fallback_text(node.get("label"), context=f"{diagram_type} fallback node"),
        }
        for node in nodes
    ]
    safe_edges = [
        {
            **edge,
            **(
                {
                    "label": safe_fallback_text(
                        edge["label"], context=f"{diagram_type} fallback edge"
                    ).replace("|", "∣")
                }
                if edge.get("label") is not None
                else {}
            ),
        }
        for edge in edges
    ]
    return (
        serialize_flowchart(
            {
                "nodes": safe_nodes,
                "edges": safe_edges,
                "direction": "LR",
                "acc_title": _flowchart_source_text(title, context="accessible title"),
                "acc_description": _flowchart_source_text(
                    description, context="accessible description"
                ),
            },
            experimental=experimental,
        ),
        tuple(
            dict.fromkeys(
                (
                    *accessibility_warnings,
                    *compatibility_warnings,
                    *((_ENTITY_COMPATIBILITY_WARNING,) if entity_substituted else ()),
                )
            )
        ),
    )


def plan_packet_fields(ir: dict[str, Any]) -> PacketPlan:
    """Validate packet records without deriving missing bit ranges or identities."""

    fields = ir.get("fields")
    if not isinstance(fields, list) or not fields:
        raise SerializationError("packet IR requires a non-empty fields list")
    if len(fields) > _MAX_PACKET_BIT + 1:
        raise SerializationError("packet fields exceed deterministic resource limits")
    normalized: list[PacketFieldPlan] = []
    source_ids: set[str] = set()
    emitted_ids: set[str] = set()
    previous_end = -1
    contiguous = True
    for index, field in enumerate(fields, start=1):
        if not isinstance(field, dict):
            raise SerializationError("packet fields must be objects")
        source_id = _source_id(
            field.get("id"), fallback=f"field_{index}", context=f"packet field {index}"
        )
        emitted_id = _register_prefixed_id(
            source_id,
            "packet_field_",
            source_ids,
            emitted_ids,
            context="packet field",
        )
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
            PacketFieldPlan(
                source_record=field,
                source_id=source_id,
                emitted_id=emitted_id,
                label=_record_label(field, context=f"packet field {source_id!r}"),
                start=start,
                end=end,
            )
        )
    return PacketPlan(tuple(normalized), contiguous)


def _packet_fallback(
    ir: dict[str, Any],
    fields: tuple[PacketFieldPlan, ...],
    *,
    experimental: bool,
    reason: str,
) -> SerializationResult:
    nodes = [
        {
            "id": field.emitted_id,
            "label": f"{field.start}-{field.end}: {field.label}",
        }
        for field in fields
    ]
    code, accessibility_warnings = _serialize_flowchart_fallback(
        ir,
        "packet",
        nodes,
        [],
        experimental=experimental,
    )
    return SerializationResult.fallback(
        "packet",
        "flowchart",
        code,
        warnings=(
            reason,
            "Packet bit-cell widths and grid layout were reduced to disconnected ordered "
            "range labels without inferred relationships.",
            *accessibility_warnings,
        ),
        stability="experimental",
    )


def _serialize_packet(
    ir: dict[str, Any], *, experimental: bool, native_runtime_valid: bool
) -> SerializationResult:
    plan = plan_packet_fields(ir)
    if not plan.contiguous:
        return _packet_fallback(
            ir,
            plan.fields,
            experimental=experimental,
            reason="Native Mermaid packet fields must be contiguous and start at bit zero.",
        )
    if not native_runtime_valid:
        return _packet_fallback(
            ir,
            plan.fields,
            experimental=experimental,
            reason="CandidateValidator rejected the native Mermaid packet candidate.",
        )
    accessibility_lines, accessibility_warnings = _accessibility(
        ir, "packet", experimental=experimental
    )
    lines = ["packet-beta", *accessibility_lines]
    if ir.get("title"):
        lines.append(f"    title {_special_directive_text(ir['title'], context='packet title')}")
    lines.extend(
        f'{field.start}-{field.end}: "{_packet_text(field.label, context="packet field label")}"'
        for field in plan.fields
    )
    entity_warning = (
        (_ENTITY_COMPATIBILITY_WARNING,)
        if any(_ENTITY_LITERAL.search(field.label) for field in plan.fields)
        or (ir.get("title") is not None and _ENTITY_LITERAL.search(str(ir["title"])))
        else ()
    )
    return SerializationResult.native(
        "packet",
        "\n".join(lines) + "\n",
        warnings=tuple(dict.fromkeys((*accessibility_warnings, *entity_warning))),
        stability="experimental",
    )


def _plan_special_hierarchy(
    root: Any,
    *,
    context: str,
    emitted_prefix: str,
    root_children: list[Any] | None = None,
) -> tuple[SpecialHierarchyNodePlan, ...]:
    if not isinstance(root, dict):
        raise SerializationError(f"{context} requires a root object")
    rows: list[SpecialHierarchyNodePlan] = []
    active: set[int] = set()
    seen: set[int] = set()
    source_ids: set[str] = set()
    emitted_ids: set[str] = set()

    def visit(
        node: dict[str, Any],
        depth: int,
        parent_source_id: str | None,
        parent_emitted_id: str | None,
    ) -> None:
        if depth > _MAX_HIERARCHY_DEPTH or len(rows) >= _MAX_HIERARCHY_NODES:
            raise SerializationError(f"{context} hierarchy exceeds deterministic resource limits")
        identity = id(node)
        if identity in active:
            raise SerializationError(f"{context} hierarchy contains a cycle")
        if identity in seen:
            raise SerializationError(f"{context} hierarchy reuses a node object")
        seen.add(identity)
        active.add(identity)
        try:
            index = len(rows) + 1
            source_id = _source_id(
                node.get("id"), fallback=f"node_{index}", context=f"{context} node {index}"
            )
            emitted_id = _register_prefixed_id(
                source_id,
                emitted_prefix,
                source_ids,
                emitted_ids,
                context=f"{context} node",
            )
            label = _record_label(node, context=f"{context} node")
            rows.append(
                SpecialHierarchyNodePlan(
                    source_record=node,
                    source_id=source_id,
                    emitted_id=emitted_id,
                    label=label,
                    depth=depth,
                    parent_source_id=parent_source_id,
                    parent_emitted_id=parent_emitted_id,
                )
            )
            children = (
                root_children
                if depth == 0 and root_children is not None
                else node.get("children", [])
            )
            if not isinstance(children, list):
                raise SerializationError(f"{context} node {source_id!r} children must be a list")
            for child in children:
                if not isinstance(child, dict):
                    raise SerializationError(f"{context} children must be objects")
                visit(child, depth + 1, source_id, emitted_id)
        finally:
            active.remove(identity)

    visit(root, 0, None, None)
    return tuple(rows)


def plan_ishikawa_hierarchy(ir: dict[str, Any]) -> tuple[SpecialHierarchyNodePlan, ...]:
    """Validate the effect/category/cause tree shared by every Ishikawa output."""

    effect = ir.get("effect")
    if not isinstance(effect, dict):
        raise SerializationError("ishikawa IR requires an effect object")
    if "children" in effect:
        raise SerializationError(
            "ishikawa effect.children is not part of the effect/category contract"
        )
    categories = ir.get("categories")
    if not isinstance(categories, list) or not categories:
        raise SerializationError("ishikawa IR requires non-empty categories")
    return _plan_special_hierarchy(
        effect,
        context="ishikawa",
        emitted_prefix="ishikawa_node_",
        root_children=categories,
    )


def plan_treeview_hierarchy(ir: dict[str, Any]) -> tuple[SpecialHierarchyNodePlan, ...]:
    """Validate one rooted TreeView hierarchy with deterministic emitted IDs."""

    rows = _plan_special_hierarchy(
        ir.get("root"),
        context="treeview",
        emitted_prefix="treeview_node_",
    )
    if len(rows) < 2:
        raise SerializationError("treeview requires an explicit hierarchy below the root")
    return rows


def _hierarchy_flowchart(
    requested_type: str,
    ir: dict[str, Any],
    rows: Iterable[SpecialHierarchyNodePlan],
    *,
    experimental: bool,
    warnings: tuple[str, ...],
) -> SerializationResult:
    materialized = list(rows)
    if max(0, len(materialized) - 1) > _MAX_FLOWCHART_EDGES:
        raise SerializationError(
            f"{requested_type} portable fallback exceeds Mermaid edge limit of "
            f"{_MAX_FLOWCHART_EDGES}"
        )
    nodes = [{"id": node.emitted_id, "label": node.label} for node in materialized]
    edges = [
        {"source": node.parent_emitted_id, "target": node.emitted_id}
        for node in materialized
        if node.parent_emitted_id is not None
    ]
    code, accessibility_warnings = _serialize_flowchart_fallback(
        ir,
        requested_type,
        nodes,
        edges,
        experimental=experimental,
    )
    return SerializationResult.fallback(
        requested_type,
        "flowchart",
        code,
        warnings=(*warnings, *accessibility_warnings),
        stability="experimental",
    )


def _serialize_ishikawa(
    ir: dict[str, Any], *, experimental: bool, native_runtime_valid: bool
) -> SerializationResult:
    rows = plan_ishikawa_hierarchy(ir)
    native_label_loss = any(any(char in node.label for char in "&<>") for node in rows)
    if not native_runtime_valid or native_label_loss:
        reason = (
            "Native Mermaid Ishikawa cannot preserve one or more observed label characters."
            if native_label_loss
            else "CandidateValidator rejected the native Mermaid Ishikawa candidate."
        )
        return _hierarchy_flowchart(
            "ishikawa",
            ir,
            rows,
            experimental=experimental,
            warnings=(
                reason,
                "Fishbone placement was reduced to directed cause containment.",
            ),
        )
    lines = ["ishikawa-beta"]
    lines.extend(
        f"{'  ' * node.depth}{_ishikawa_text(node.label, context='ishikawa label')}"
        for node in rows
    )
    warnings = [
        "Mermaid 11.16 Ishikawa accessibility directives are not emitted because the grammar "
        "can interpret them as cause nodes; accessibility text remains in typed IR.",
    ]
    if any(_ENTITY_LITERAL.search(node.label) for node in rows):
        warnings.append(_ENTITY_COMPATIBILITY_WARNING)
    return SerializationResult.native(
        "ishikawa",
        "\n".join(lines) + "\n",
        warnings=tuple(warnings),
        stability="experimental",
    )


def _serialize_treeview(
    ir: dict[str, Any], *, experimental: bool, native_runtime_valid: bool
) -> SerializationResult:
    rows = plan_treeview_hierarchy(ir)
    native_label_loss = any(any(char in node.label for char in '"\\') for node in rows)
    if not native_runtime_valid or native_label_loss:
        reason = (
            "Native Mermaid TreeView cannot preserve one or more observed label characters."
            if native_label_loss
            else "CandidateValidator rejected the native Mermaid TreeView candidate."
        )
        return _hierarchy_flowchart(
            "treeview",
            ir,
            rows,
            experimental=experimental,
            warnings=(
                reason,
                "Tree indentation was reduced to directed hierarchy edges.",
            ),
        )
    accessibility_lines, accessibility_warnings = _accessibility(
        ir, "treeview", experimental=experimental
    )
    lines = ["treeView-beta", *accessibility_lines]
    lines.extend(
        f'{"  " * node.depth}"{_special_directive_text(node.label, context="treeview label")}"'
        for node in rows
    )
    entity_warning = (
        (_ENTITY_COMPATIBILITY_WARNING,)
        if any(_ENTITY_LITERAL.search(node.label) for node in rows)
        else ()
    )
    return SerializationResult.native(
        "treeview",
        "\n".join(lines) + "\n",
        warnings=tuple(dict.fromkeys((*accessibility_warnings, *entity_warning))),
        stability="experimental",
    )


def plan_eventmodeling_frames(
    ir: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Validate and normalize the lane/frame records emitted by the fallback."""

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
            "label": _flowchart_code_text(
                lane.get("label", lane_id), context=f"eventmodeling lane {lane_id!r}"
            ),
            "semantic_label": _semantic_text(
                lane.get("label", lane_id), context=f"eventmodeling lane {lane_id!r}"
            ),
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
            label = _flowchart_code_text(
                frame.get("label"), context=f"eventmodeling frame {frame_id!r}"
            )
            semantic_label = _semantic_text(
                frame.get("label"), context=f"eventmodeling frame {frame_id!r}"
            )
            time = frame.get("time")
            safe_time = (
                _flowchart_code_text(time, context=f"eventmodeling frame {frame_id!r} time")
                if time is not None
                else None
            )
            semantic_time = (
                _semantic_text(time, context=f"eventmodeling frame {frame_id!r} time")
                if time is not None
                else None
            )
            rendered_label = f"[{frame_type}] {label}"
            semantic_rendered_label = f"[{frame_type}] {semantic_label}"
            if safe_time is not None:
                rendered_label = f"{safe_time} — {rendered_label}"
            if semantic_time is not None:
                semantic_rendered_label = f"{semantic_time} — {semantic_rendered_label}"
            normalized = {
                "source_id": frame_id,
                "output_id": output_id,
                "label": rendered_label,
                "semantic_label": semantic_rendered_label,
            }
            frame_map[frame_id] = output_id
            frames.append(normalized)
            normalized_lane["frame_ids"].append(output_id)
        normalized_lanes.append(normalized_lane)
    return normalized_lanes, frames, frame_map


def plan_eventmodeling_relations(
    ir: dict[str, Any], frame_map: dict[str, str]
) -> list[dict[str, str | None]]:
    """Validate and normalize the relations emitted by the fallback."""

    relations = ir.get("relations", [])
    if not isinstance(relations, list):
        raise SerializationError("eventmodeling relations must be a list")
    normalized: list[dict[str, str | None]] = []
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
        normalized.append(
            {
                "source": source,
                "target": target,
                "label": (
                    _edge_text(label, context="eventmodeling relation")
                    if label is not None
                    else None
                ),
                "semantic_label": (
                    _semantic_text(label, context="eventmodeling relation")
                    if label is not None
                    else None
                ),
            }
        )
    return normalized


def _serialize_eventmodeling(ir: dict[str, Any], *, experimental: bool) -> SerializationResult:
    lanes, frames, frame_map = plan_eventmodeling_frames(ir)
    relations = plan_eventmodeling_relations(ir, frame_map)
    edge_lines: list[str] = []
    for relation in relations:
        source = relation["source"]
        target = relation["target"]
        label = relation["label"]
        connector = "-->"
        if label is not None:
            connector = f"-->|{label}|"
        edge_lines.append(f"    {source} {connector} {target}")

    title, description, accessibility_warnings = _safe_accessibility_values(
        ir, "eventmodeling", experimental=experimental
    )
    compatibility_warnings = _flowchart_compatibility_warnings(
        (
            title,
            description,
            *(lane["semantic_label"] for lane in lanes),
            *(frame["semantic_label"] for frame in frames),
            *(
                relation["semantic_label"]
                for relation in relations
                if relation["semantic_label"] is not None
            ),
        )
    )
    lines = [
        "flowchart LR",
        f"    accTitle: {_flowchart_source_text(title, context='accessible title')}",
        f"    accDescr: {_flowchart_source_text(description, context='accessible description')}",
    ]
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
            *accessibility_warnings,
            *compatibility_warnings,
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
        result = _serialize_packet(
            ir,
            experimental=experimental,
            native_runtime_valid=native_runtime_valid,
        )
    elif diagram_type == "ishikawa":
        result = _serialize_ishikawa(
            ir,
            experimental=experimental,
            native_runtime_valid=native_runtime_valid,
        )
    elif diagram_type == "treeview":
        result = _serialize_treeview(
            ir,
            experimental=experimental,
            native_runtime_valid=native_runtime_valid,
        )
    elif diagram_type == "eventmodeling":
        result = _serialize_eventmodeling(ir, experimental=experimental)
    else:
        raise SerializationError(f"no special serializer for {diagram_type!r}")
    _preflight_special_code(result.code, diagram_type=diagram_type)
    return result
