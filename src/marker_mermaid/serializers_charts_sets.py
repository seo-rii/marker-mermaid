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
import sys
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from numbers import Real
from typing import Any, TypeAlias

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.flowchart_structure import (
    FlowchartStructureError,
    plan_flowchart_structure,
    portable_identifier,
)
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
VennNumber: TypeAlias = float | int

MAX_TREEMAP_NODES = min(2_000, MAX_SCENE_ELEMENTS)
MAX_TREEMAP_FLOWCHART_EDGES = 500
MAX_TREEMAP_OUTPUT_CHARS = 50_000
MAX_TREEMAP_OUTPUT_LINES = 5_000
MAX_VENN_AREAS = min(2_000, MAX_SCENE_ELEMENTS)
MAX_VENN_MEMBERSHIPS = MAX_SCENE_RELATIONS
MAX_VENN_FLOWCHART_EDGES = 500
MAX_VENN_NATIVE_VALUE_RATIO = 200
MAX_VENN_OUTPUT_CHARS = 50_000
MAX_VENN_OUTPUT_LINES = 5_000
_MAX_SAFE_JS_INTEGER = 2**53 - 1
_ZERO_WIDTH_SPACE = "\u200b"

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
VENN_NATIVE_TEXT_COMPATIBILITY_WARNING = (
    "Native Venn used visible compatibility glyphs for grammar-unsafe text; "
    "review the rendered title and labels."
)
VENN_FALLBACK_TEXT_COMPATIBILITY_WARNING = (
    "Venn Flowchart fallback used visible compatibility glyphs for grammar-unsafe "
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


@dataclass(frozen=True, slots=True)
class VennSetPlan:
    """One observed set and its native/fallback terminal projections."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    semantic_label: str
    native_source_label: str
    native_canvas_label: str
    fallback_source_label: str
    fallback_canvas_label: str
    value: VennNumber | None
    value_text: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VennIntersectionPlan:
    """One explicit intersection and its terminal-visible identity."""

    source_record: Mapping[str, Any]
    scene_id: str
    member_source_ids: tuple[str, ...]
    member_emitted_ids: tuple[str, ...]
    semantic_label: str | None
    native_source_label: str | None
    native_canvas_label: str | None
    fallback_source_label: str
    fallback_canvas_label: str
    value: VennNumber | None
    value_text: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VennMembershipPlan:
    """One set-to-intersection membership relation."""

    scene_id: str
    source_emitted_id: str
    target_scene_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VennPlan:
    """Validated Venn emission plan shared by serializer, Scene, and OCR."""

    sets: tuple[VennSetPlan, ...]
    intersections: tuple[VennIntersectionPlan, ...]
    memberships: tuple[VennMembershipPlan, ...]
    native_supported: bool
    flowchart_supported: bool
    native_limitations: tuple[str, ...]
    semantic_title: str | None
    native_source_title: str | None
    native_canvas_title: str | None
    native_compatibility_substitutions: bool
    fallback_compatibility_substitutions: bool


def _strict_chart_set_text(value: Any, *, context: str) -> str:
    """Validate one source-visible chart/set string before grammar adaptation."""

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


def _neutralize_chart_set_active_text(text: str) -> str:
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


def _chart_set_node_text(value: Any, *, context: str) -> tuple[str, str, str, str, str, bool, bool]:
    """Freeze semantic, native-source, and terminal-visible record text."""

    semantic = _strict_chart_set_text(value, context=context)
    native_source = (
        _neutralize_chart_set_active_text(semantic)
        .replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("#", f"#{_ZERO_WIDTH_SPACE}")
        .replace('"', "″")
    )
    native_canvas = native_source.replace(_ZERO_WIDTH_SPACE, "")
    fallback_source = _neutralize_chart_set_active_text(
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

    semantic = _strict_chart_set_text(value, context=context)
    source = (
        _neutralize_chart_set_active_text(semantic.replace("<", "＜").replace(">", "＞"))
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
        decimal = Decimal(value) if isinstance(value, int) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SerializationError(f"{context} requires a finite numeric value") from exc
    if not decimal.is_finite():
        raise SerializationError(f"{context} requires a finite numeric value")
    if decimal < 0 or (decimal == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise SerializationError(f"{context} requires a {qualifier} numeric value")
    return value


def _chart_set_number_text(value: TreemapNumber) -> str:
    """Return a fixed-point token that preserves the observed numeric value."""

    try:
        decimal = Decimal(value) if isinstance(value, int) else Decimal(str(value))
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


def _preflight_venn_code(code: str) -> str:
    if code.count("\n") + 1 > MAX_VENN_OUTPUT_LINES:
        raise SerializationError(
            f"Venn output exceeds source-line limit of {MAX_VENN_OUTPUT_LINES}"
        )
    if len(code) > MAX_VENN_OUTPUT_CHARS:
        raise SerializationError(
            f"Venn output exceeds source-character limit of {MAX_VENN_OUTPUT_CHARS}"
        )
    return code


def _native_binary64_number(
    value_text: str,
    *,
    max_value: Decimal | None,
    reject_subnormal: bool,
) -> float | None:
    """Return the exact JavaScript binary64 input, or ``None`` on value loss."""

    decimal = Decimal(value_text)
    if decimal <= 0 or (max_value is not None and decimal > max_value):
        return None
    try:
        number = float(value_text)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    if reject_subnormal and number < sys.float_info.min:
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
            ) = _chart_set_node_text(node.get("label", node.get("name")), context="treemap node")
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
            value_text = _chart_set_number_text(value) if value is not None else None
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
            native_value = _native_binary64_number(
                row["value_text"],
                max_value=Decimal(_MAX_SAFE_JS_INTEGER),
                reject_subnormal=False,
            )
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


def plan_venn_records(ir: Mapping[str, Any]) -> VennPlan:
    """Validate Venn evidence and freeze native/fallback terminal semantics."""

    raw_sets = ir.get("sets")
    raw_intersections = ir.get("intersections")
    if not isinstance(raw_sets, list) or len(raw_sets) < 2:
        raise SerializationError("venn IR requires at least two explicit sets")
    if not isinstance(raw_intersections, list) or not raw_intersections:
        raise SerializationError("venn IR requires explicit intersections")
    if len(raw_sets) + len(raw_intersections) > MAX_VENN_AREAS:
        raise SerializationError("venn area count exceeds the Scene element limit")

    seen_records: set[int] = set()
    id_map: dict[str, str] = {}
    emitted_ids: set[str] = set()
    set_values: dict[str, Decimal] = {}
    set_plans: list[VennSetPlan] = []
    native_limitations: list[str] = []
    native_compatibility_substitutions = False
    fallback_compatibility_substitutions = False

    for index, item in enumerate(raw_sets, start=1):
        if not isinstance(item, dict):
            raise SerializationError("venn sets must be objects")
        identity = id(item)
        if identity in seen_records:
            raise SerializationError("venn records cannot reuse one object")
        seen_records.add(identity)
        source_id_value = item.get("id")
        if type(source_id_value) is not str or not source_id_value:
            raise SerializationError(f"venn set {index} requires an id")
        source_id = source_id_value
        if source_id != source_id.strip() or len(source_id) > MAX_ID_CHARS:
            raise SerializationError(f"venn set {index} requires a bounded canonical id")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in source_id
        ):
            raise SerializationError(f"venn set {index} contains unsupported id text")
        try:
            source_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError(f"venn set {index} id is not valid UTF-8") from exc
        if source_id in id_map:
            raise SerializationError(f"duplicate venn set id: {source_id}")
        emitted_base = portable_identifier(source_id, f"S_{index}")
        emitted_id = emitted_base
        suffix = 2
        while emitted_id in emitted_ids:
            suffix_text = f"_{suffix}"
            emitted_id = f"{emitted_base[: MAX_ID_CHARS - len(suffix_text)]}{suffix_text}"
            suffix += 1
        if len(emitted_id) > MAX_ID_CHARS:
            raise SerializationError("venn emitted set id exceeds the identifier limit")
        emitted_ids.add(emitted_id)
        id_map[source_id] = emitted_id
        (
            semantic_label,
            native_source_label,
            native_canvas_label,
            fallback_source_label,
            fallback_canvas_label,
            native_substituted,
            fallback_substituted,
        ) = _chart_set_node_text(
            item.get("label", item.get("name")), context=f"venn set {source_id!r}"
        )
        native_compatibility_substitutions |= native_substituted
        fallback_compatibility_substitutions |= fallback_substituted
        if "value" in item:
            raw_value = item["value"]
            if type(raw_value) not in {int, float}:
                raise SerializationError(
                    f"venn set {source_id!r} requires an explicit numeric value"
                )
            if isinstance(raw_value, float) and not math.isfinite(raw_value):
                raise SerializationError(f"venn set {source_id!r} requires a finite numeric value")
            if raw_value < 0:
                raise SerializationError(
                    f"venn set {source_id!r} requires a non-negative numeric value"
                )
            value: VennNumber | None = raw_value
            value_text = _chart_set_number_text(value)
            set_values[source_id] = Decimal(value_text)
            native_value = (
                None
                if isinstance(value, int) and value > _MAX_SAFE_JS_INTEGER
                else _native_binary64_number(
                    value_text,
                    max_value=None,
                    reject_subnormal=True,
                )
            )
            if native_value is None:
                limitation = "a zero-sized or non-binary64-safe set"
                if limitation not in native_limitations:
                    native_limitations.append(limitation)
        else:
            value = None
            value_text = None
            limitation = "one or more set/intersection sizes were not observed"
            if limitation not in native_limitations:
                native_limitations.append(limitation)
        set_plans.append(
            VennSetPlan(
                source_record=item,
                source_id=source_id,
                emitted_id=emitted_id,
                semantic_label=semantic_label,
                native_source_label=native_source_label,
                native_canvas_label=native_canvas_label,
                fallback_source_label=fallback_source_label,
                fallback_canvas_label=fallback_canvas_label,
                value=value,
                value_text=value_text,
                evidence_ids=_safe_evidence_ids(item),
            )
        )

    order = {source_id: index for index, source_id in enumerate(id_map)}
    seen_intersections: set[tuple[str, ...]] = set()
    intersection_id_candidates: list[str | None] = []
    intersection_id_counts: dict[str, int] = {}
    for item in raw_intersections:
        candidate: str | None = None
        if isinstance(item, dict):
            raw_id = item.get("id")
            if (
                type(raw_id) is str
                and bool(raw_id)
                and raw_id == raw_id.strip()
                and len(raw_id) <= MAX_ID_CHARS
                and not any(
                    unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
                    for character in raw_id
                )
            ):
                try:
                    raw_id.encode("utf-8")
                except UnicodeEncodeError:
                    candidate = None
                else:
                    candidate = portable_identifier(raw_id, "intersection")
                    if len(candidate) > MAX_ID_CHARS:
                        candidate = None
        intersection_id_candidates.append(candidate)
        if candidate is not None:
            intersection_id_counts[candidate] = intersection_id_counts.get(candidate, 0) + 1
    reserved_explicit_ids = {
        candidate
        for candidate, count in intersection_id_counts.items()
        if count == 1 and candidate not in emitted_ids
    }
    reserved_scene_ids = set(emitted_ids)
    used_scene_ids: set[str] = set()
    intersection_plans: list[VennIntersectionPlan] = []
    membership_count = 0
    for index, item in enumerate(raw_intersections, start=1):
        if not isinstance(item, dict):
            raise SerializationError("venn intersections must be objects")
        identity = id(item)
        if identity in seen_records:
            raise SerializationError("venn records cannot reuse one object")
        seen_records.add(identity)
        members = item.get("sets")
        if not isinstance(members, list) or len(members) < 2:
            raise SerializationError(f"venn intersection {index} requires at least two sets")
        if not all(type(member) is str for member in members):
            raise SerializationError(f"venn intersection {index} members must be set ids")
        source_members = list(members)
        if len(source_members) != len(set(source_members)):
            raise SerializationError(f"venn intersection {index} repeats a set")
        unknown = [member for member in source_members if member not in id_map]
        if unknown:
            raise SerializationError(
                f"venn intersection {index} references unknown set {unknown[0]!r}"
            )
        canonical = tuple(sorted(source_members, key=order.__getitem__))
        if canonical in seen_intersections:
            raise SerializationError("duplicate venn intersection structure")
        seen_intersections.add(canonical)
        membership_count += len(canonical)
        if membership_count > MAX_VENN_MEMBERSHIPS:
            raise SerializationError("venn membership count exceeds the Scene relation limit")
        emitted_members = tuple(id_map[member] for member in canonical)
        label_value = item.get("label", item.get("name"))
        if label_value is None or label_value == "":
            semantic_label = None
            native_source_label = None
            native_canvas_label = None
            fallback_source_label = " ∩ ".join(emitted_members)
            fallback_canvas_label = fallback_source_label
        else:
            (
                semantic_label,
                native_source_label,
                native_canvas_label,
                fallback_source_label,
                fallback_canvas_label,
                native_substituted,
                fallback_substituted,
            ) = _chart_set_node_text(label_value, context=f"venn intersection {index}")
            native_compatibility_substitutions |= native_substituted
            fallback_compatibility_substitutions |= fallback_substituted
        if "value" in item:
            raw_value = item["value"]
            if type(raw_value) not in {int, float}:
                raise SerializationError(
                    f"venn intersection {index} requires an explicit numeric value"
                )
            if isinstance(raw_value, float) and not math.isfinite(raw_value):
                raise SerializationError(
                    f"venn intersection {index} requires a finite numeric value"
                )
            if raw_value < 0:
                raise SerializationError(
                    f"venn intersection {index} requires a non-negative numeric value"
                )
            value = raw_value
            value_text = _chart_set_number_text(value)
            observed_value = Decimal(value_text)
            for member in canonical:
                if member in set_values and observed_value > set_values[member]:
                    raise SerializationError(
                        f"venn intersection {index} exceeds observed size of set {member!r}"
                    )
                if member in set_values and observed_value == set_values[member]:
                    limitation = "an exact-containment intersection can exceed runtime budget"
                    if limitation not in native_limitations:
                        native_limitations.append(limitation)
            native_value = (
                None
                if isinstance(value, int) and value > _MAX_SAFE_JS_INTEGER
                else _native_binary64_number(
                    value_text,
                    max_value=None,
                    reject_subnormal=True,
                )
            )
            if native_value is None:
                limitation = "a zero-sized or non-binary64-safe intersection"
                if limitation not in native_limitations:
                    native_limitations.append(limitation)
        else:
            value = None
            value_text = None
            limitation = "one or more set/intersection sizes were not observed"
            if limitation not in native_limitations:
                native_limitations.append(limitation)
        explicit_scene_id = intersection_id_candidates[index - 1]
        if explicit_scene_id in reserved_explicit_ids:
            scene_id = explicit_scene_id
        else:
            base_id = f"intersection_{index}"
            scene_id = base_id
            suffix = 2
            while (
                scene_id in reserved_scene_ids
                or scene_id in reserved_explicit_ids
                or scene_id in used_scene_ids
            ):
                suffix_text = f"_{suffix}"
                scene_id = f"{base_id[: MAX_ID_CHARS - len(suffix_text)]}{suffix_text}"
                suffix += 1
        reserved_scene_ids.add(scene_id)
        used_scene_ids.add(scene_id)
        intersection_plans.append(
            VennIntersectionPlan(
                source_record=item,
                scene_id=scene_id,
                member_source_ids=canonical,
                member_emitted_ids=emitted_members,
                semantic_label=semantic_label,
                native_source_label=native_source_label,
                native_canvas_label=native_canvas_label,
                fallback_source_label=fallback_source_label,
                fallback_canvas_label=fallback_canvas_label,
                value=value,
                value_text=value_text,
                evidence_ids=_safe_evidence_ids(item),
            )
        )

    observed_intersections = [
        (frozenset(item.member_source_ids), Decimal(item.value_text), index)
        for index, item in enumerate(intersection_plans, start=1)
        if item.value_text is not None
    ]
    for superset_members, superset_value, superset_index in observed_intersections:
        for subset_members, subset_value, subset_index in observed_intersections:
            if subset_members < superset_members and superset_value > subset_value:
                raise SerializationError(
                    f"venn intersection {superset_index} exceeds observed size of "
                    f"intersection {subset_index}"
                )
            if subset_members < superset_members and superset_value == subset_value:
                limitation = "an exact-containment intersection can exceed runtime budget"
                if limitation not in native_limitations:
                    native_limitations.append(limitation)

    positive_area_values = [value for value in set_values.values() if value > 0]
    positive_area_values.extend(
        value for _members, value, _index in observed_intersections if value > 0
    )
    if set_values and positive_area_values:
        largest_set = max(set_values.values())
        smallest_area = min(positive_area_values)
        if largest_set > smallest_area * MAX_VENN_NATIVE_VALUE_RATIO:
            limitation = (
                f"native area dynamic range exceeds {MAX_VENN_NATIVE_VALUE_RATIO}:1 "
                "visibility limit"
            )
            if limitation not in native_limitations:
                native_limitations.append(limitation)

    pairwise_members = {
        frozenset(item.member_source_ids)
        for item in intersection_plans
        if len(item.member_source_ids) == 2
    }
    pairwise_complete = True
    for item in intersection_plans:
        members = item.member_source_ids
        if len(members) < 3:
            continue
        required_count = len(members) * (len(members) - 1) // 2
        if required_count > len(pairwise_members):
            pairwise_complete = False
            break
        for left_index in range(len(members) - 1):
            for right_index in range(left_index + 1, len(members)):
                if frozenset((members[left_index], members[right_index])) not in pairwise_members:
                    pairwise_complete = False
                    break
            if not pairwise_complete:
                break
        if not pairwise_complete:
            break
    if not pairwise_complete:
        limitation = "complete explicit pairwise intersections are required"
        if limitation not in native_limitations:
            native_limitations.append(limitation)

    memberships: list[VennMembershipPlan] = []
    relation_index = 1
    for intersection in intersection_plans:
        for member in intersection.member_emitted_ids:
            memberships.append(
                VennMembershipPlan(
                    scene_id=f"venn_relation_{relation_index}",
                    source_emitted_id=member,
                    target_scene_id=intersection.scene_id,
                    evidence_ids=intersection.evidence_ids,
                )
            )
            relation_index += 1

    semantic_title: str | None = None
    native_source_title: str | None = None
    native_canvas_title: str | None = None
    title_value = ir.get("title")
    if title_value is not None and title_value != "":
        semantic_title = _strict_chart_set_text(title_value, context="venn title")
        native_source_title = semantic_title.translate(
            str.maketrans({"<": "＜", ">": "＞", "#": "＃", ";": "；"})
        )
        native_source_title = _neutralize_chart_set_active_text(native_source_title).replace(
            "&", f"&{_ZERO_WIDTH_SPACE}"
        )
        native_canvas_title = native_source_title.replace(_ZERO_WIDTH_SPACE, "")
        native_compatibility_substitutions |= native_canvas_title != semantic_title

    return VennPlan(
        sets=tuple(set_plans),
        intersections=tuple(intersection_plans),
        memberships=tuple(memberships),
        native_supported=not native_limitations,
        flowchart_supported=len(memberships) <= MAX_VENN_FLOWCHART_EDGES,
        native_limitations=tuple(native_limitations),
        semantic_title=semantic_title,
        native_source_title=native_source_title,
        native_canvas_title=native_canvas_title,
        native_compatibility_substitutions=native_compatibility_substitutions,
        fallback_compatibility_substitutions=fallback_compatibility_substitutions,
    )


def _venn_flowchart_fallback(
    ir: Mapping[str, Any],
    plan: VennPlan,
    *,
    experimental: bool,
    reason: str,
) -> ChartSetSerialization:
    if not plan.flowchart_supported:
        raise SerializationError(
            f"Venn Flowchart fallback exceeds Mermaid edge limit of {MAX_VENN_FLOWCHART_EDGES}"
        )
    nodes = [
        {
            "id": item.emitted_id,
            "label": item.fallback_source_label
            + (f" (value: {item.value_text})" if item.value_text is not None else ""),
            "shape": "circle",
        }
        for item in plan.sets
    ]
    nodes.extend(
        {
            "id": item.scene_id,
            "label": item.fallback_source_label
            + (f" (value: {item.value_text})" if item.value_text is not None else ""),
            "shape": "round",
        }
        for item in plan.intersections
    )
    edges = [
        {
            "source": membership.source_emitted_id,
            "target": membership.target_scene_id,
            "label": "intersects",
        }
        for membership in plan.memberships
    ]
    accessibility = resolve_accessibility(dict(ir), "venn", experimental=experimental)
    (
        _semantic_accessible_title,
        _native_accessible_title,
        _native_accessible_title_canvas,
        fallback_accessible_title,
        _fallback_accessible_title_canvas,
        _native_title_substituted,
        fallback_title_substituted,
    ) = _chart_set_node_text(accessibility.title, context="venn fallback accessible title")
    (
        _semantic_accessible_description,
        _native_accessible_description,
        _native_accessible_description_canvas,
        fallback_accessible_description,
        _fallback_accessible_description_canvas,
        _native_description_substituted,
        fallback_description_substituted,
    ) = _chart_set_node_text(
        accessibility.description,
        context="venn fallback accessible description",
    )
    code = serialize_flowchart(
        {
            "acc_title": fallback_accessible_title,
            "acc_description": fallback_accessible_description,
            "nodes": nodes,
            "edges": edges,
            "direction": "LR",
        },
        experimental=experimental,
    )
    if (
        plan.fallback_compatibility_substitutions
        or fallback_title_substituted
        or fallback_description_substituted
    ):
        reason = f"{reason}; {VENN_FALLBACK_TEXT_COMPATIBILITY_WARNING}"
    return _preflight_venn_code(code), "flowchart", reason


def serialize_venn(
    ir: dict[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> ChartSetSerialization:
    """Serialize explicit sets and intersections without implicit area values."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_venn_records(ir)
    if not native_runtime_valid:
        return _venn_flowchart_fallback(
            ir,
            plan,
            experimental=experimental,
            reason=(
                "strict CandidateValidator rejected native venn; the explicit set graph "
                "was re-emitted as a portable Flowchart in the same candidate slot"
            ),
        )
    if not plan.native_supported:
        return _venn_flowchart_fallback(
            ir,
            plan,
            experimental=experimental,
            reason=(
                "flowchart fallback from venn; "
                + "; ".join(plan.native_limitations)
                + "; no numeric areas were synthesized"
            ),
        )

    lines = ["venn-beta"]
    if plan.native_source_title is not None:
        lines.append(f"    title {plan.native_source_title}")
    for item in plan.sets:
        assert item.value_text is not None
        lines.append(f'    set {item.emitted_id}["{item.native_source_label}"]: {item.value_text}')
    for item in plan.intersections:
        assert item.value_text is not None
        label_suffix = (
            f'["{item.native_source_label}"]' if item.native_source_label is not None else ""
        )
        lines.append(
            f"    union {','.join(item.member_emitted_ids)}{label_suffix}: {item.value_text}"
        )
    return _preflight_venn_code("\n".join(lines) + "\n"), "venn", None


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
