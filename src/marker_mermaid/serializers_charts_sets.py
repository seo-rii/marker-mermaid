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
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from numbers import Real
from typing import Any, TypeAlias

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.flowchart_structure import FlowchartStructureError, plan_flowchart_structure
from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_IR_DEPTH,
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_RELATIONS,
    MAX_TEXT_CHARS,
)
from marker_mermaid.resource_limits import MAX_EVIDENCE_REFS
from marker_mermaid.serializers import SerializationError, serialize_flowchart

ChartSetSerialization: TypeAlias = tuple[str, str, str | None]
TreemapNumber: TypeAlias = float | int | Decimal

MAX_TREEMAP_NODES = min(2_000, MAX_SCENE_ELEMENTS)
MAX_TREEMAP_FLOWCHART_EDGES = 500
MAX_TREEMAP_OUTPUT_CHARS = 50_000
MAX_TREEMAP_OUTPUT_LINES = 5_000
_MAX_SAFE_JS_INTEGER = 2**53 - 1
_ZERO_WIDTH_SPACE = "\u200b"

_DANGEROUS_SCHEME = re.compile(r"\b(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_REMOTE_ICON = re.compile(r"\b(?:iconify|fa|logos):", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"@import\b", re.IGNORECASE)
_ACTIVE_CALLBACK = re.compile(r"\b(?:call|callback)\s*\(", re.IGNORECASE)
_TREEMAP_DANGEROUS_SCHEME = re.compile(r"(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_TREEMAP_REMOTE_ICON = re.compile(r"iconify|fa:|logos:", re.IGNORECASE)
_TREEMAP_SEGMENT_CONTROL = re.compile(
    r"(?P<prefix>;\s*)(?P<word>click|style|classDef|linkStyle)\b", re.IGNORECASE
)

TREEMAP_NATIVE_TEXT_COMPATIBILITY_WARNING = (
    "Native Treemap used visible compatibility glyphs for grammar-unsafe text; "
    "review the rendered text and accessibility metadata."
)
TREEMAP_FALLBACK_TEXT_COMPATIBILITY_WARNING = (
    "Treemap Flowchart fallback used visible compatibility glyphs for grammar-unsafe "
    "quote, backslash, angle, or hash text."
)


@dataclass(frozen=True, slots=True)
class TreemapNodePlan:
    """One source hierarchy node and both terminal-visible projections."""

    source_record: Mapping[str, Any]
    scene_id: str
    fallback_id: str
    semantic_label: str
    native_source_label: str
    native_canvas_label: str
    fallback_source_label: str
    fallback_canvas_label: str
    explicit_value: TreemapNumber | None
    value_text: str | None
    native_total_text: str | None
    depth: int
    parent_scene_id: str | None
    parent_fallback_id: str | None
    is_leaf: bool
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TreemapRelationPlan:
    """One logical parent/child relation in terminal identity space."""

    scene_id: str
    source_scene_id: str
    target_scene_id: str
    source_fallback_id: str
    target_fallback_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TreemapPlan:
    """Validated Treemap emission plan shared by serializer, Scene, and OCR."""

    nodes: tuple[TreemapNodePlan, ...]
    relations: tuple[TreemapRelationPlan, ...]
    native_supported: bool
    flowchart_supported: bool
    semantic_title: str | None
    native_source_title: str | None
    native_canvas_title: str | None
    native_compatibility_substitutions: bool
    fallback_compatibility_substitutions: bool


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


def _strict_treemap_text(value: Any, *, context: str) -> str:
    """Validate one source-visible Treemap string before grammar adaptation."""

    if value is None:
        raise SerializationError(f"{context} requires a label")
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) > MAX_TEXT_CHARS:
        raise SerializationError(f"{context} exceeds the Scene text limit")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in text):
        raise SerializationError(f"{context} contains unsupported control text")
    text = " ".join(text.split())
    if not text:
        raise SerializationError(f"{context} requires a label")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SerializationError(f"{context} is not valid UTF-8 text") from exc
    return text


def _neutralize_treemap_active_text(text: str) -> str:
    """Insert renderer-invisible separators only into scanner-active tokens."""

    text = text.replace("%%", f"%{_ZERO_WIDTH_SPACE}%").replace("//", f"/{_ZERO_WIDTH_SPACE}/")
    text = text.replace("<", f"<{_ZERO_WIDTH_SPACE}")
    text = _CSS_IMPORT.sub(
        lambda match: f"{match.group(0)[:3]}{_ZERO_WIDTH_SPACE}{match.group(0)[3:]}",
        text,
    )
    text = _TREEMAP_DANGEROUS_SCHEME.sub(
        lambda match: f"{match.group(0)[:-1]}{_ZERO_WIDTH_SPACE}:", text
    )
    text = _TREEMAP_REMOTE_ICON.sub(
        lambda match: (
            f"{match.group(0)[:4]}{_ZERO_WIDTH_SPACE}{match.group(0)[4:]}"
            if match.group(0).casefold() == "iconify"
            else match.group(0).replace(":", f"{_ZERO_WIDTH_SPACE}:")
        ),
        text,
    )
    text = _ACTIVE_CALLBACK.sub(
        lambda match: match.group(0).replace("(", f"{_ZERO_WIDTH_SPACE}("), text
    )
    return _TREEMAP_SEGMENT_CONTROL.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('word')[0]}{_ZERO_WIDTH_SPACE}"
            f"{match.group('word')[1:]}"
        ),
        text,
    )


def _treemap_node_text(value: Any, *, context: str) -> tuple[str, str, str, str, str, bool, bool]:
    """Freeze semantic, native-source, and terminal-visible node text."""

    semantic = _strict_treemap_text(value, context=context)
    native_source = (
        _neutralize_treemap_active_text(semantic)
        .replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("#", f"#{_ZERO_WIDTH_SPACE}")
        .replace('"', "″")
    )
    native_canvas = native_source.replace(_ZERO_WIDTH_SPACE, "")
    fallback_source = _neutralize_treemap_active_text(
        semantic.replace("<", "＜").replace(">", "＞")
    )
    fallback_source = (
        fallback_source.replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("#", "＃")
        .replace('"', "″")
        .replace("\\", "∖")
    )
    fallback_canvas = fallback_source.replace(_ZERO_WIDTH_SPACE, "")
    return (
        semantic,
        native_source,
        native_canvas,
        fallback_source,
        fallback_canvas,
        native_canvas != semantic,
        fallback_canvas != semantic,
    )


def _treemap_directive_text(value: Any, *, context: str) -> tuple[str, str, str]:
    """Freeze source and canvas text for Treemap title-like directives."""

    semantic = _strict_treemap_text(value, context=context)
    source = (
        _neutralize_treemap_active_text(semantic.replace("<", "＜").replace(">", "＞"))
        .replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("#", f"#{_ZERO_WIDTH_SPACE}")
    )
    return semantic, source, source.replace(_ZERO_WIDTH_SPACE, "")


def _safe_evidence_ids(record: Mapping[str, Any]) -> tuple[str, ...]:
    raw = record.get("evidence_ids")
    if not isinstance(raw, list) or len(raw) > MAX_EVIDENCE_REFS:
        return ()
    if not all(type(value) is str and bool(value) and len(value) <= MAX_ID_CHARS for value in raw):
        return ()
    try:
        for value in raw:
            value.encode("utf-8")
    except UnicodeEncodeError:
        return ()
    return tuple(raw)


def _valid_treemap_scene_id(value: Any) -> str | None:
    if type(value) is not str or not value or value != value.strip():
        return None
    if len(value) > MAX_ID_CHARS:
        return None
    if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def _treemap_number(value: Any, *, context: str, allow_zero: bool) -> TreemapNumber:
    """Validate an observed numeric value without coercing or inventing one."""

    if isinstance(value, bool) or not isinstance(value, Real | Decimal):
        raise SerializationError(f"{context} requires an explicit numeric value")
    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise SerializationError(f"{context} requires a finite numeric value") from exc
    if not decimal.is_finite():
        raise SerializationError(f"{context} requires a finite numeric value")
    if decimal < 0 or (decimal == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise SerializationError(f"{context} requires a {qualifier} numeric value")
    return value


def _treemap_number_text(value: TreemapNumber) -> str:
    """Return a fixed-point token that preserves the observed numeric value."""

    try:
        decimal = Decimal(str(value))
    except InvalidOperation as exc:
        raise SerializationError("chart value cannot be represented exactly") from exc
    if not decimal.is_finite():
        raise SerializationError("chart value must be finite")
    if decimal == 0:
        return "0"
    if abs(decimal.adjusted()) >= MAX_TEXT_CHARS:
        raise SerializationError("chart numeric token exceeds the source text limit")
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if len(rendered) > MAX_TEXT_CHARS:
        raise SerializationError("chart numeric token exceeds the source text limit")
    return rendered


def _number(value: Any, *, context: str, allow_zero: bool) -> float | int:
    """Validate a legacy Venn numeric value without expanding its contract."""

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


def _native_treemap_number(value_text: str) -> float | None:
    """Return the exact JavaScript binary64 input, or ``None`` on value loss."""

    decimal = Decimal(value_text)
    if decimal <= 0 or decimal > _MAX_SAFE_JS_INTEGER:
        return None
    try:
        number = float(value_text)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    if Decimal(str(number)) != decimal:
        return None
    return number


def _d3_comma_text(value: float) -> str | None:
    """Mirror d3-format's Mermaid 11.16 default ``format(',')`` output."""

    if not math.isfinite(value) or value < 0 or value > _MAX_SAFE_JS_INTEGER:
        return None
    if value == 0:
        return "0"
    decimal = Decimal.from_float(value)
    exponent = decimal.adjusted()
    quantum = Decimal(1).scaleb(exponent - 11)
    with localcontext() as context:
        context.prec = max(64, len(decimal.as_tuple().digits))
        rounded = decimal.quantize(quantum, rounding=ROUND_HALF_UP)
    exponent = rounded.adjusted()
    if exponent < -6 or exponent >= 12:
        digits = "".join(str(digit) for digit in rounded.as_tuple().digits).rstrip("0")
        coefficient = digits[0]
        if len(digits) > 1:
            coefficient += f".{digits[1:]}"
        sign = "+" if exponent >= 0 else "-"
        return f"{coefficient}e{sign}{abs(exponent)}"
    fixed = format(rounded, "f")
    integer, separator, fraction = fixed.partition(".")
    fraction = fraction.rstrip("0")
    grouped = format(int(integer), ",")
    return f"{grouped}.{fraction}" if separator and fraction else grouped


def _preflight_treemap_code(code: str) -> str:
    if code.count("\n") + 1 > MAX_TREEMAP_OUTPUT_LINES:
        raise SerializationError(
            f"Treemap output exceeds source-line limit of {MAX_TREEMAP_OUTPUT_LINES}"
        )
    if len(code) > MAX_TREEMAP_OUTPUT_CHARS:
        raise SerializationError(
            f"Treemap output exceeds source-character limit of {MAX_TREEMAP_OUTPUT_CHARS}"
        )
    return code


def _accessibility(ir: dict[str, Any], *, experimental: bool) -> list[str]:
    resolved = resolve_accessibility(ir, "treemap", experimental=experimental)
    _, title_source, _ = _treemap_directive_text(resolved.title, context="treemap accessible title")
    _, description_source, _ = _treemap_directive_text(
        resolved.description, context="treemap accessible description"
    )
    return [
        f"    accTitle: {title_source}",
        f"    accDescr: {description_source}",
    ]


def plan_treemap_records(ir: Mapping[str, Any]) -> TreemapPlan:
    """Validate Treemap evidence and freeze native/fallback terminal semantics."""

    root = ir.get("root")
    if not isinstance(root, dict):
        raise SerializationError("treemap IR requires a root object")

    rows: list[dict[str, Any]] = []
    active: set[int] = set()
    seen: set[int] = set()
    internal_values = False
    native_compatibility_substitutions = False
    fallback_compatibility_substitutions = False

    def visit(node: dict[str, Any], depth: int, parent_index: int | None) -> int:
        nonlocal fallback_compatibility_substitutions, internal_values
        nonlocal native_compatibility_substitutions
        if depth > MAX_IR_DEPTH or len(rows) >= MAX_TREEMAP_NODES:
            raise SerializationError("treemap hierarchy exceeds deterministic resource limits")
        identity = id(node)
        if identity in active:
            raise SerializationError("treemap hierarchy contains a cycle")
        if identity in seen:
            raise SerializationError("treemap hierarchy reuses one node object")
        active.add(identity)
        seen.add(identity)
        try:
            (
                semantic_label,
                native_source_label,
                native_canvas_label,
                fallback_source_label,
                fallback_canvas_label,
                native_substituted,
                fallback_substituted,
            ) = _treemap_node_text(node.get("label", node.get("name")), context="treemap node")
            native_compatibility_substitutions = (
                native_compatibility_substitutions or native_substituted
            )
            fallback_compatibility_substitutions = (
                fallback_compatibility_substitutions or fallback_substituted
            )
            children_value = node.get("children")
            if children_value is None:
                if "value" not in node:
                    raise SerializationError(
                        f"treemap leaf {semantic_label!r} requires an explicit numeric value"
                    )
                value: TreemapNumber | None = _treemap_number(
                    node["value"],
                    context=f"treemap leaf {semantic_label!r}",
                    allow_zero=False,
                )
            else:
                if not isinstance(children_value, list) or not children_value:
                    raise SerializationError(
                        f"treemap internal node {semantic_label!r} requires non-empty children"
                    )
                if "value" in node:
                    value = _treemap_number(
                        node["value"],
                        context=f"treemap internal node {semantic_label!r}",
                        allow_zero=False,
                    )
                    internal_values = True
                else:
                    value = None
            value_text = _treemap_number_text(value) if value is not None else None
            row_index = len(rows)
            rows.append(
                {
                    "source": node,
                    "depth": depth,
                    "parent_index": parent_index,
                    "child_indices": [],
                    "semantic_label": semantic_label,
                    "native_source_label": native_source_label,
                    "native_canvas_label": native_canvas_label,
                    "fallback_source_label": fallback_source_label,
                    "fallback_canvas_label": fallback_canvas_label,
                    "value": value,
                    "value_text": value_text,
                    "is_leaf": children_value is None,
                    "evidence_ids": _safe_evidence_ids(node),
                }
            )
            if children_value is not None:
                for child in children_value:
                    if not isinstance(child, dict):
                        raise SerializationError("treemap children must be objects")
                    child_index = visit(child, depth + 1, row_index)
                    rows[row_index]["child_indices"].append(child_index)
            return row_index
        finally:
            active.remove(identity)

    visit(root, 0, None)
    if root.get("children") is None:
        raise SerializationError("treemap requires an explicit hierarchy below the root")

    explicit_ids = [_valid_treemap_scene_id(row["source"].get("id")) for row in rows]
    id_counts: dict[str, int] = {}
    for source_id in explicit_ids:
        if source_id is not None:
            id_counts[source_id] = id_counts.get(source_id, 0) + 1
    reserved_ids = {source_id for source_id, count in id_counts.items() if count == 1}
    used_scene_ids: set[str] = set()
    scene_ids: list[str] = []
    for index, source_id in enumerate(explicit_ids, start=1):
        if source_id is not None and id_counts[source_id] == 1:
            scene_id = source_id
        else:
            base = f"treemap_node_{index}"
            scene_id = base
            suffix = 2
            while scene_id in reserved_ids or scene_id in used_scene_ids:
                suffix_text = f"_{suffix}"
                scene_id = f"{base[: MAX_ID_CHARS - len(suffix_text)]}{suffix_text}"
                suffix += 1
        used_scene_ids.add(scene_id)
        scene_ids.append(scene_id)

    try:
        fallback_structure = plan_flowchart_structure(
            [{"id": f"N{index}"} for index in range(1, len(rows) + 1)], []
        )
    except FlowchartStructureError as exc:
        raise SerializationError(str(exc)) from exc
    fallback_ids = [placement.emitted_id for placement in fallback_structure.nodes]

    native_values: list[float | None] = [None] * len(rows)
    native_total_texts: list[str | None] = [None] * len(rows)
    numeric_native_supported = True
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        child_indices: list[int] = row["child_indices"]
        if row["is_leaf"]:
            native_value = _native_treemap_number(row["value_text"])
        else:
            native_value = 0.0
            for child_index in reversed(child_indices):
                child_value = native_values[child_index]
                if child_value is None:
                    native_value = None
                    break
                native_value += child_value
            if native_value is not None and (
                not math.isfinite(native_value) or native_value > _MAX_SAFE_JS_INTEGER
            ):
                native_value = None
        native_values[index] = native_value
        native_total_texts[index] = (
            _d3_comma_text(native_value) if native_value is not None else None
        )
        numeric_native_supported = (
            numeric_native_supported and native_total_texts[index] is not None
        )

    nodes = tuple(
        TreemapNodePlan(
            source_record=row["source"],
            scene_id=scene_ids[index],
            fallback_id=fallback_ids[index],
            semantic_label=row["semantic_label"],
            native_source_label=row["native_source_label"],
            native_canvas_label=row["native_canvas_label"],
            fallback_source_label=row["fallback_source_label"],
            fallback_canvas_label=row["fallback_canvas_label"],
            explicit_value=row["value"],
            value_text=row["value_text"],
            native_total_text=native_total_texts[index],
            depth=row["depth"],
            parent_scene_id=(
                scene_ids[row["parent_index"]] if row["parent_index"] is not None else None
            ),
            parent_fallback_id=(
                fallback_ids[row["parent_index"]] if row["parent_index"] is not None else None
            ),
            is_leaf=row["is_leaf"],
            evidence_ids=row["evidence_ids"],
        )
        for index, row in enumerate(rows)
    )
    relations = tuple(
        TreemapRelationPlan(
            scene_id=f"treemap_relation_{index}",
            source_scene_id=node.parent_scene_id,
            target_scene_id=node.scene_id,
            source_fallback_id=node.parent_fallback_id,
            target_fallback_id=node.fallback_id,
            evidence_ids=node.evidence_ids,
        )
        for index, node in enumerate(nodes[1:], start=2)
        if node.parent_scene_id is not None and node.parent_fallback_id is not None
    )
    if len(relations) > MAX_SCENE_RELATIONS:
        raise SerializationError("treemap relation count exceeds the Scene relation limit")

    semantic_title: str | None = None
    native_source_title: str | None = None
    native_canvas_title: str | None = None
    if ir.get("title"):
        semantic_title, native_source_title, native_canvas_title = _treemap_directive_text(
            ir["title"], context="treemap title"
        )
        native_compatibility_substitutions = native_compatibility_substitutions or (
            semantic_title != native_canvas_title
        )
        fallback_compatibility_substitutions = fallback_compatibility_substitutions or (
            semantic_title != native_canvas_title
        )
    resolved_accessibility = resolve_accessibility(dict(ir), "treemap", experimental=False)
    for value, context in (
        (resolved_accessibility.title, "treemap accessible title"),
        (resolved_accessibility.description, "treemap accessible description"),
    ):
        semantic_accessibility, _source_accessibility, canvas_accessibility = (
            _treemap_directive_text(value, context=context)
        )
        accessibility_substituted = semantic_accessibility != canvas_accessibility
        native_compatibility_substitutions = (
            native_compatibility_substitutions or accessibility_substituted
        )
        fallback_compatibility_substitutions = (
            fallback_compatibility_substitutions or accessibility_substituted
        )

    return TreemapPlan(
        nodes=nodes,
        relations=relations,
        native_supported=not internal_values and numeric_native_supported,
        flowchart_supported=len(relations) <= MAX_TREEMAP_FLOWCHART_EDGES,
        semantic_title=semantic_title,
        native_source_title=native_source_title,
        native_canvas_title=native_canvas_title,
        native_compatibility_substitutions=native_compatibility_substitutions,
        fallback_compatibility_substitutions=fallback_compatibility_substitutions,
    )


def _treemap_flowchart_fallback(
    ir: Mapping[str, Any],
    plan: TreemapPlan,
    *,
    experimental: bool,
    reason: str,
) -> ChartSetSerialization:
    if not plan.flowchart_supported:
        raise SerializationError(
            f"Treemap portable fallback exceeds Mermaid edge limit of {MAX_TREEMAP_FLOWCHART_EDGES}"
        )
    if plan.fallback_compatibility_substitutions:
        reason = f"{reason}; {TREEMAP_FALLBACK_TEXT_COMPATIBILITY_WARNING}"
    accessibility = resolve_accessibility(dict(ir), "treemap", experimental=experimental)
    _, acc_title_source, _ = _treemap_directive_text(
        accessibility.title, context="treemap accessible title"
    )
    _, acc_description_source, _ = _treemap_directive_text(
        accessibility.description, context="treemap accessible description"
    )
    nodes: list[dict[str, Any]] = []
    for node in plan.nodes:
        rendered_label = node.fallback_source_label
        if node.value_text is not None:
            rendered_label += f" (value: {node.value_text})"
        nodes.append({"id": node.fallback_id, "label": rendered_label})
    edges = [
        {"source": relation.source_fallback_id, "target": relation.target_fallback_id}
        for relation in plan.relations
    ]
    code = _preflight_treemap_code(
        serialize_flowchart(
            {
                "acc_title": acc_title_source,
                "acc_description": acc_description_source,
                "nodes": nodes,
                "edges": edges,
                "direction": "TB",
            },
            experimental=experimental,
        )
    )
    return code, "flowchart", reason


def serialize_treemap(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> ChartSetSerialization:
    """Serialize a hierarchy whose leaves all have observed positive values."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_treemap_records(ir)
    if not native_runtime_valid:
        return _treemap_flowchart_fallback(
            ir,
            plan,
            experimental=experimental,
            reason=(
                "strict CandidateValidator rejected native treemap; the exact hierarchy "
                "was re-emitted as a portable Flowchart in the same candidate slot"
            ),
        )
    if not plan.native_supported:
        return _treemap_flowchart_fallback(
            ir,
            plan,
            experimental=experimental,
            reason=(
                "flowchart fallback from treemap; Mermaid 11.16 native treemap cannot "
                "reproduce one or more observed hierarchy values without loss, including "
                "an explicit non-leaf value or a non-binary64-safe numeric token"
            ),
        )

    lines = ["treemap-beta", *_accessibility(dict(ir), experimental=experimental)]
    if plan.native_source_title is not None:
        lines.append(f"    title {plan.native_source_title}")
    for node in plan.nodes:
        indent = "    " * (node.depth + 1)
        suffix = f": {node.value_text}" if node.value_text is not None else ""
        lines.append(f'{indent}"{node.native_source_label}"{suffix}')
    return _preflight_treemap_code("\n".join(lines) + "\n"), "treemap", None


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
