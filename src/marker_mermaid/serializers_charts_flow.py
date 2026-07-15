"""Typed Sankey and radar serializers with explicit portable fallbacks.

Both native grammars are experimental in Mermaid 11.16.  This module only
selects them for the subset that can be represented without changing labels,
values, or topology.  Valid typed data outside that subset is retained in a
portable flowchart representation; incomplete or contradictory numeric data
raises :class:`SerializationError` instead of being guessed.
"""

from __future__ import annotations

import math
import re
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.config import SecurityProfile
from marker_mermaid.flowchart_structure import (
    FlowchartStructureError,
    plan_flowchart_structure,
)
from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_GROUPS,
    MAX_SCENE_RELATIONS,
    MAX_TEXT_CHARS,
)
from marker_mermaid.resource_limits import MAX_EVIDENCE_REFS
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError, serialize_flowchart

MAX_RADAR_TICKS = 100
MAX_RADAR_DIMENSIONS = 256
MAX_RADAR_SERIES = MAX_SCENE_GROUPS
MAX_RADAR_POINTS = MAX_SCENE_ELEMENTS
MAX_RADAR_NATIVE_SERIES = 12
MAX_RADAR_FLOWCHART_POINTS = 256
MAX_RADAR_OUTPUT_CHARS = 50_000
MAX_RADAR_OUTPUT_LINES = 5_000
RADAR_RENDER_RADIUS = 300.0
MAX_SANKEY_FLOWCHART_EDGES = 500
_MAX_EXACT_NATIVE_SANKEY_TOTAL = (2**53 - 1) / 100

SANKEY_ACCESSIBILITY_LIMITATION = (
    "Mermaid 11.16 Sankey grammar cannot encode title, accTitle, or accDescr; "
    "accessibility text remains in typed IR and review metadata."
)
SANKEY_FALLBACK_WARNING = (
    "Sankey was emitted as a weighted flowchart because its evidence cannot be "
    "represented by Mermaid 11.16 Sankey without loss."
)
SANKEY_RUNTIME_FALLBACK_WARNING = (
    "CandidateValidator rejected native Sankey; exact weighted flows were re-emitted "
    "as a portable Flowchart in the same candidate slot."
)
RADAR_FALLBACK_WARNING = (
    "Radar was emitted as a tabular flowchart because Mermaid 11.16 cannot represent "
    "its numeric domain or geometry without loss."
)
RADAR_RUNTIME_FALLBACK_WARNING = (
    "CandidateValidator rejected native Radar; exact dimension/value cells were "
    "re-emitted as a portable Flowchart in the same candidate slot."
)
RADAR_NATIVE_TEXT_COMPATIBILITY_WARNING = (
    "Radar canvas text used visible compatibility substitutions; semantic text remains "
    "in typed IR and review metadata."
)
RADAR_FALLBACK_TEXT_COMPATIBILITY_WARNING = (
    "Radar Flowchart text used visible compatibility substitutions; semantic text remains "
    "in typed IR and review metadata."
)

_RADAR_RESERVED_IDENTIFIERS = frozenset(
    {
        "axis",
        "circle",
        "class",
        "classdef",
        "click",
        "curve",
        "direction",
        "end",
        "false",
        "flowchart",
        "graph",
        "graticule",
        "linkstyle",
        "lr",
        "max",
        "min",
        "polygon",
        "rl",
        "showlegend",
        "style",
        "subgraph",
        "tb",
        "ticks",
        "title",
        "true",
        "bt",
    }
)
_ZERO_WIDTH_SPACE = "\u200b"


@dataclass(frozen=True, slots=True)
class SankeyNodePlan:
    """One source node and its terminal-specific visible identity/text."""

    source_record: Mapping[str, Any]
    source_id: str
    fallback_id: str
    label: str
    native_total_text: str | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SankeyFlowPlan:
    """One exact weighted relation shared by native, fallback, and Scene consumers."""

    source_record: Mapping[str, Any]
    scene_id: str
    source_id: str
    target_id: str
    source_fallback_id: str
    target_fallback_id: str
    value_text: str
    value: Decimal
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SankeyPlan:
    """Validated Sankey emission plan for both terminal grammars."""

    nodes: tuple[SankeyNodePlan, ...]
    flows: tuple[SankeyFlowPlan, ...]
    native_supported: bool
    flowchart_supported: bool
    fallback_direction: str


@dataclass(frozen=True, slots=True)
class RadarDimensionPlan:
    """One emitted radar axis and its terminal-visible label evidence."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    label: str
    native_source_label: str
    native_canvas_label: str
    fallback_source_label: str
    fallback_canvas_label: str
    normalized_point: tuple[float, float]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RadarPointPlan:
    """One exact series value bound to an emitted dimension."""

    scene_id: str
    dimension_source_id: str
    dimension_emitted_id: str
    dimension_label: str
    fallback_source_label: str
    fallback_canvas_label: str
    value_text: str
    value: Decimal
    native_value: float | None
    normalized_point: tuple[float, float] | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RadarSeriesPlan:
    """One curve/portable subgraph and its ordered values."""

    source_record: Mapping[str, Any]
    source_id: str
    emitted_id: str
    label: str
    native_source_label: str
    native_canvas_label: str
    fallback_source_label: str
    fallback_canvas_label: str
    points: tuple[RadarPointPlan, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RadarPlan:
    """Bounded terminal plan shared by Radar serialization, Scene, and OCR."""

    dimensions: tuple[RadarDimensionPlan, ...]
    series: tuple[RadarSeriesPlan, ...]
    native_supported: bool
    flowchart_supported: bool
    native_limitations: tuple[str, ...]
    minimum_text: str | None
    maximum_text: str | None
    ticks: int
    ticks_explicit: bool
    show_legend: bool
    show_legend_explicit: bool
    graticule: str
    graticule_explicit: bool
    semantic_title: str | None
    native_source_title: str | None
    native_canvas_title: str | None
    native_compatibility_substitutions: bool
    fallback_compatibility_substitutions: bool


def _required_records(value: Any, *, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise SerializationError(f"{field} requires a non-empty list")
    records: list[Mapping[str, Any]] = []
    for record in value:
        if not isinstance(record, Mapping):
            raise SerializationError(f"{field} entries must be objects")
        records.append(record)
    return records


def _explicit_id(record: Mapping[str, Any], *, field: str) -> str:
    value = record.get("id")
    if not isinstance(value, str) or not value.strip():
        raise SerializationError(f"{field} requires an explicit non-empty id")
    return value.strip()


def _label(record: Mapping[str, Any], source_id: str, *, field: str) -> str:
    value = record.get("label", source_id)
    if not isinstance(value, str) or not value.strip():
        raise SerializationError(f"{field} {source_id!r} has an empty label")
    return value.strip()


def _finite_number(value: Any, *, field: str) -> tuple[str, Decimal]:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise SerializationError(f"{field} requires an explicit finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise SerializationError(f"{field} requires an explicit finite number")
    try:
        decimal = Decimal(value) if isinstance(value, int | Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SerializationError(f"{field} requires an explicit finite number") from exc
    if not decimal.is_finite():
        raise SerializationError(f"{field} requires an explicit finite number")
    if decimal == 0:
        return "0", Decimal(0)
    if abs(decimal.adjusted()) >= MAX_TEXT_CHARS:
        raise SerializationError(f"{field} numeric token exceeds the source text limit")
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if len(rendered) > MAX_TEXT_CHARS:
        raise SerializationError(f"{field} numeric token exceeds the source text limit")
    return rendered, decimal


def _flow_text(value: str) -> str:
    return (
        value.replace("%%", f"%{_ZERO_WIDTH_SPACE}%")
        .replace("//", f"/{_ZERO_WIDTH_SPACE}/")
        .replace("<", f"<{_ZERO_WIDTH_SPACE}")
        .replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _radar_string(value: str) -> str:
    return (
        value.replace("%%", f"%{_ZERO_WIDTH_SPACE}%")
        .replace("//", f"/{_ZERO_WIDTH_SPACE}/")
        .replace("<", f"<{_ZERO_WIDTH_SPACE}")
        .replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _plain_text(value: Any) -> str:
    return (
        str(value)
        .replace("%%", f"%{_ZERO_WIDTH_SPACE}%")
        .replace("//", f"/{_ZERO_WIDTH_SPACE}/")
        .replace("<", f"<{_ZERO_WIDTH_SPACE}")
        .replace("&", f"&{_ZERO_WIDTH_SPACE}")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _strict_source(code: str) -> str:
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)
    if not report.safe:
        rules = ", ".join(sorted({finding.rule for finding in report.findings}))
        raise SerializationError(f"chart text violates the strict security profile: {rules}")
    return code


def _safe_evidence_ids(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Return one complete bounded evidence tuple or quarantine the record locally."""

    raw_evidence_ids = record.get("evidence_ids")
    if raw_evidence_ids is None:
        return ()
    if (
        not isinstance(raw_evidence_ids, list)
        or len(raw_evidence_ids) > MAX_EVIDENCE_REFS
        or not all(
            type(evidence_id) is str and bool(evidence_id) and len(evidence_id) <= MAX_ID_CHARS
            for evidence_id in raw_evidence_ids
        )
    ):
        return ()
    try:
        for evidence_id in raw_evidence_ids:
            evidence_id.encode("utf-8")
    except UnicodeEncodeError:
        return ()
    return tuple(raw_evidence_ids)


def _is_dag(node_ids: Sequence[str], flows: Sequence[tuple[str, str]]) -> bool:
    adjacency = {node_id: [] for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    for source, target in flows:
        adjacency[source].append(target)
        indegree[target] += 1
    pending = [node_id for node_id in node_ids if indegree[node_id] == 0]
    visited = 0
    while pending:
        source = pending.pop()
        visited += 1
        for target in adjacency[source]:
            indegree[target] -= 1
            if indegree[target] == 0:
                pending.append(target)
    return visited == len(node_ids)


def _native_sankey_total_text(total: float) -> str | None:
    """Mirror Mermaid's positive ``Math.round(value * 100) / 100`` label."""

    if not math.isfinite(total) or total < 0 or total > _MAX_EXACT_NATIVE_SANKEY_TOTAL:
        return None
    scaled = total * 100
    if not math.isfinite(scaled) or scaled > 2**53 - 1:
        return None
    lower_units = math.floor(scaled)
    rounded_units = lower_units + int(scaled - lower_units >= 0.5)
    if rounded_units == 0:
        return "0"
    rendered = repr(rounded_units / 100)
    return rendered.removesuffix(".0")


def plan_sankey_records(ir: Mapping[str, Any]) -> SankeyPlan:
    """Validate Sankey records and freeze native/fallback visible semantics."""

    records = _required_records(ir.get("nodes"), field="Sankey nodes")
    node_rows: list[tuple[Mapping[str, Any], str, str, tuple[str, ...]]] = []
    seen_ids: set[str] = set()
    for record in records:
        source_id = _explicit_id(record, field="Sankey node")
        if source_id in seen_ids:
            raise SerializationError(f"Sankey node id {source_id!r} is duplicated")
        seen_ids.add(source_id)
        label = _label(record, source_id, field="Sankey node")
        if len(label) > MAX_TEXT_CHARS:
            raise SerializationError("Sankey node label exceeds the Scene text limit")
        evidence_ids = _safe_evidence_ids(record)
        node_rows.append((record, source_id, label, evidence_ids))

    try:
        fallback_structure = plan_flowchart_structure(
            [
                {"id": source_id, "label": label}
                for _record, source_id, label, _evidence_ids in node_rows
            ],
            [],
        )
    except FlowchartStructureError as exc:
        raise SerializationError(str(exc)) from exc
    fallback_id_by_source = {node.source_id: node.emitted_id for node in fallback_structure.nodes}

    raw_flows = ir.get("flows", ir.get("links"))
    flow_records = _required_records(raw_flows, field="Sankey flows")
    if len(flow_records) > MAX_SCENE_RELATIONS:
        raise SerializationError("Sankey flow count exceeds the Scene relation limit")
    flow_rows: list[tuple[Mapping[str, Any], str, str, str, Decimal, tuple[str, ...]]] = []
    for index, record in enumerate(flow_records, start=1):
        source = record.get("source")
        target = record.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise SerializationError(f"Sankey flow {index} requires source and target ids")
        if source not in seen_ids or target not in seen_ids:
            raise SerializationError(
                f"Sankey flow {index} references unknown endpoint: {source!r} -> {target!r}"
            )
        if "value" not in record:
            raise SerializationError(f"Sankey flow {index} lacks explicit numeric value evidence")
        rendered, decimal = _finite_number(record["value"], field=f"Sankey flow {index} value")
        evidence_ids = _safe_evidence_ids(record)
        flow_rows.append((record, source, target, rendered, decimal, evidence_ids))

    incoming: dict[str, list[float]] = {source_id: [] for source_id in seen_ids}
    outgoing: dict[str, list[float]] = {source_id: [] for source_id in seen_ids}
    numeric_values_safe = True
    for _record, source, target, _rendered, decimal, _evidence_ids in flow_rows:
        try:
            numeric_value = float(decimal)
        except (OverflowError, ValueError):
            numeric_values_safe = False
            numeric_value = math.inf
        if (
            not math.isfinite(numeric_value)
            or (decimal > 0 and numeric_value <= 0)
            or (math.isfinite(numeric_value) and Decimal(str(numeric_value)) != decimal)
        ):
            numeric_values_safe = False
        outgoing[source].append(numeric_value)
        incoming[target].append(numeric_value)

    native_total_by_source: dict[str, str | None] = {}
    for source_id in seen_ids:
        if not numeric_values_safe:
            native_total_by_source[source_id] = None
            continue
        incoming_total = 0.0
        for numeric_value in incoming[source_id]:
            incoming_total += numeric_value
        outgoing_total = 0.0
        for numeric_value in outgoing[source_id]:
            outgoing_total += numeric_value
        native_total_by_source[source_id] = _native_sankey_total_text(
            max(incoming_total, outgoing_total)
        )

    nodes = tuple(
        SankeyNodePlan(
            source_record=record,
            source_id=source_id,
            fallback_id=fallback_id_by_source[source_id],
            label=label,
            native_total_text=native_total_by_source[source_id],
            evidence_ids=evidence_ids,
        )
        for record, source_id, label, evidence_ids in node_rows
    )
    used_scene_ids: set[str] = set()
    flows: list[SankeyFlowPlan] = []
    for index, (record, source, target, rendered, decimal, evidence_ids) in enumerate(
        flow_rows, start=1
    ):
        raw_scene_id = record.get("id")
        if type(raw_scene_id) is str and raw_scene_id and len(raw_scene_id) <= MAX_ID_CHARS:
            try:
                raw_scene_id.encode("utf-8")
            except UnicodeEncodeError:
                base_scene_id = f"sankey_flow_{index}"
            else:
                base_scene_id = raw_scene_id
        else:
            base_scene_id = f"sankey_flow_{index}"
        scene_id = base_scene_id
        suffix = 2
        while scene_id in used_scene_ids:
            suffix_text = f"_{suffix}"
            scene_id = f"{base_scene_id[: MAX_ID_CHARS - len(suffix_text)]}{suffix_text}"
            suffix += 1
        used_scene_ids.add(scene_id)
        flows.append(
            SankeyFlowPlan(
                source_record=record,
                scene_id=scene_id,
                source_id=source,
                target_id=target,
                source_fallback_id=fallback_id_by_source[source],
                target_fallback_id=fallback_id_by_source[target],
                value_text=rendered,
                value=decimal,
                evidence_ids=evidence_ids,
            )
        )

    labels = [node.label for node in nodes]
    linked_ids = {endpoint for flow in flows for endpoint in (flow.source_id, flow.target_id)}
    native_supported = (
        numeric_values_safe
        and len(labels) == len(set(labels))
        and all(all(0x20 <= ord(character) <= 0x7E for character in label) for label in labels)
        and all(flow.value > 0 for flow in flows)
        and linked_ids == seen_ids
        and _is_dag(
            [node.source_id for node in nodes],
            [(flow.source_id, flow.target_id) for flow in flows],
        )
        and all(node.native_total_text is not None for node in nodes)
    )
    raw_direction = ir.get("direction", "LR")
    fallback_direction = (
        raw_direction
        if isinstance(raw_direction, str) and raw_direction in {"TB", "BT", "LR", "RL"}
        else "TB"
    )
    return SankeyPlan(
        nodes,
        tuple(flows),
        native_supported,
        len(flows) <= MAX_SANKEY_FLOWCHART_EDGES,
        fallback_direction,
    )


def _csv_field(value: str) -> str:
    if any(character in value for character in ',"\r\n'):
        return f'"{value.replace(chr(34), chr(34) * 2)}"'
    return value


def _sankey_flowchart(
    ir: Mapping[str, Any],
    plan: SankeyPlan,
    *,
    experimental: bool,
) -> str:
    if not plan.flowchart_supported:
        raise SerializationError(
            f"Sankey portable fallback exceeds Mermaid edge limit of {MAX_SANKEY_FLOWCHART_EDGES}"
        )
    accessibility = resolve_accessibility(ir, "sankey", experimental=experimental)
    return serialize_flowchart(
        {
            "acc_title": accessibility.title,
            "acc_description": accessibility.description,
            "direction": plan.fallback_direction,
            "nodes": [{"id": node.source_id, "label": node.label} for node in plan.nodes],
            "edges": [
                {
                    "source": flow.source_id,
                    "target": flow.target_id,
                    "label": flow.value_text,
                }
                for flow in plan.flows
            ],
        },
        experimental=experimental,
    )


def serialize_sankey(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> SerializationResult:
    """Serialize weighted flows, falling back when native Sankey would lose data."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_sankey_records(ir)
    if not native_runtime_valid or not plan.native_supported:
        code = _strict_source(_sankey_flowchart(ir, plan, experimental=experimental))
        return SerializationResult.fallback(
            "sankey",
            "flowchart",
            code,
            warnings=(
                SANKEY_RUNTIME_FALLBACK_WARNING
                if not native_runtime_valid
                else SANKEY_FALLBACK_WARNING,
            ),
            stability="experimental",
        )

    node_labels = {node.source_id: node.label for node in plan.nodes}
    code = "sankey-beta\n" + "".join(
        f"{_csv_field(node_labels[flow.source_id])},"
        f"{_csv_field(node_labels[flow.target_id])},{flow.value_text}\n"
        for flow in plan.flows
    )
    return SerializationResult.native(
        "sankey",
        _strict_source(code),
        warnings=(SANKEY_ACCESSIBILITY_LIMITATION,),
        stability="experimental",
    )


def _preflight_radar_code(code: str) -> str:
    if code.count("\n") + 1 > MAX_RADAR_OUTPUT_LINES:
        raise SerializationError(
            f"Radar output exceeds source-line limit of {MAX_RADAR_OUTPUT_LINES}"
        )
    if len(code) > MAX_RADAR_OUTPUT_CHARS:
        raise SerializationError(
            f"Radar output exceeds source-character limit of {MAX_RADAR_OUTPUT_CHARS}"
        )
    return code


def plan_radar_records(ir: Mapping[str, Any]) -> RadarPlan:
    """Validate Radar evidence and freeze native/fallback terminal semantics."""

    raw_dimensions = ir.get("dimensions", ir.get("axes"))
    dimension_records = _required_records(raw_dimensions, field="Radar dimensions")
    if len(dimension_records) < 3:
        raise SerializationError("Radar requires at least three dimensions")
    if len(dimension_records) > MAX_RADAR_DIMENSIONS:
        raise SerializationError(f"Radar dimension count must not exceed {MAX_RADAR_DIMENSIONS}")
    series_records = _required_records(ir.get("series"), field="Radar series")
    if len(series_records) > MAX_RADAR_SERIES:
        raise SerializationError(f"Radar series count must not exceed {MAX_RADAR_SERIES}")
    point_count = len(dimension_records) * len(series_records)
    if point_count > MAX_RADAR_POINTS:
        raise SerializationError(f"Radar point count must not exceed {MAX_RADAR_POINTS}")
    if point_count + len(dimension_records) + len(series_records) > MAX_SCENE_ELEMENTS:
        raise SerializationError("Radar terminal elements exceed the Scene element limit")

    seen_records: set[int] = set()
    seen_dimension_ids: set[str] = set()
    used_terminal_ids: set[str] = set()
    native_compatibility_substitutions = False
    fallback_compatibility_substitutions = False
    dimensions: list[RadarDimensionPlan] = []
    native_label_translation = str.maketrans({"<": "＜", ">": "＞", "#": "＃"})
    fallback_label_translation = str.maketrans({"<": "＜", ">": "＞", "#": "＃"})

    for index, record in enumerate(dimension_records, start=1):
        identity = id(record)
        if identity in seen_records:
            raise SerializationError("Radar records cannot reuse one object")
        seen_records.add(identity)
        raw_source_id = record.get("id")
        if type(raw_source_id) is not str or not raw_source_id:
            raise SerializationError("Radar dimension requires an explicit non-empty id")
        source_id = raw_source_id
        if source_id != source_id.strip() or len(source_id) > MAX_ID_CHARS:
            raise SerializationError("Radar dimension requires a bounded canonical id")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in source_id
        ):
            raise SerializationError("Radar dimension id contains unsupported text")
        try:
            source_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("Radar dimension id is not valid UTF-8") from exc
        if source_id in seen_dimension_ids:
            raise SerializationError(f"Radar dimension id {source_id!r} is duplicated")
        seen_dimension_ids.add(source_id)
        emitted_base = re.sub(r"[^A-Za-z0-9_-]", "_", source_id).strip("_-")
        if not emitted_base:
            emitted_base = f"radar_axis_{index}"
        if not re.match(r"[A-Za-z_]", emitted_base):
            emitted_base = f"n_{emitted_base}"
        if emitted_base.casefold() in _RADAR_RESERVED_IDENTIFIERS:
            emitted_base = f"radar_{emitted_base}"
        emitted_base = emitted_base[:MAX_ID_CHARS]
        emitted_id = emitted_base
        suffix = 2
        while emitted_id in used_terminal_ids:
            suffix_text = f"_{suffix}"
            emitted_id = f"{emitted_base[: MAX_ID_CHARS - len(suffix_text)]}{suffix_text}"
            suffix += 1
        used_terminal_ids.add(emitted_id)

        raw_label = _label(record, source_id, field="Radar dimension")
        label = " ".join(raw_label.split())
        if not label or len(label) > MAX_TEXT_CHARS:
            raise SerializationError("Radar dimension label must be non-empty and bounded")
        if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in label):
            raise SerializationError("Radar dimension label contains unsupported text")
        try:
            label.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("Radar dimension label is not valid UTF-8") from exc
        native_canvas_label = label.translate(native_label_translation)
        fallback_canvas_label = (
            label.translate(fallback_label_translation).replace('"', "″").replace("\\", "∖")
        )
        native_compatibility_substitutions |= native_canvas_label != raw_label
        fallback_compatibility_substitutions |= fallback_canvas_label != raw_label
        angle = 2 * (index - 1) * math.pi / len(dimension_records) - math.pi / 2
        dimensions.append(
            RadarDimensionPlan(
                source_record=record,
                source_id=source_id,
                emitted_id=emitted_id,
                label=label,
                native_source_label=_radar_string(native_canvas_label),
                native_canvas_label=native_canvas_label,
                fallback_source_label=_flow_text(fallback_canvas_label),
                fallback_canvas_label=fallback_canvas_label,
                normalized_point=(
                    0.5 + 0.5 * math.cos(angle),
                    0.5 + 0.5 * math.sin(angle),
                ),
                evidence_ids=_safe_evidence_ids(record),
            )
        )

    series_rows: list[dict[str, Any]] = []
    all_values: list[Decimal] = []
    all_native_values: list[float | None] = []
    series_emitted_ids: list[str] = []
    series_source_ids: list[str] = []
    reserved_series_source_ids: set[str] = set()
    for series_index, record in enumerate(series_records, start=1):
        identity = id(record)
        if identity in seen_records:
            raise SerializationError("Radar records cannot reuse one object")
        seen_records.add(identity)
        raw_source_id = record.get("id")
        if type(raw_source_id) is not str or not raw_source_id:
            raise SerializationError("Radar series requires an explicit non-empty id")
        source_id = raw_source_id
        if source_id != source_id.strip() or len(source_id) > MAX_ID_CHARS:
            raise SerializationError("Radar series requires a bounded canonical id")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in source_id
        ):
            raise SerializationError("Radar series id contains unsupported text")
        try:
            source_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("Radar series id is not valid UTF-8") from exc
        if source_id in reserved_series_source_ids:
            raise SerializationError(f"Radar series id {source_id!r} is duplicated")
        reserved_series_source_ids.add(source_id)
        series_source_ids.append(source_id)
        emitted_base = re.sub(r"[^A-Za-z0-9_-]", "_", source_id).strip("_-")
        if not emitted_base:
            emitted_base = f"radar_series_{series_index}"
        if not re.match(r"[A-Za-z_]", emitted_base):
            emitted_base = f"n_{emitted_base}"
        if emitted_base.casefold() in _RADAR_RESERVED_IDENTIFIERS:
            emitted_base = f"radar_{emitted_base}"
        emitted_base = emitted_base[:MAX_ID_CHARS]
        emitted_id = emitted_base
        suffix = 2
        while emitted_id in used_terminal_ids:
            suffix_text = f"_{suffix}"
            emitted_id = f"{emitted_base[: MAX_ID_CHARS - len(suffix_text)]}{suffix_text}"
            suffix += 1
        used_terminal_ids.add(emitted_id)
        series_emitted_ids.append(emitted_id)
    for series_index, record in enumerate(series_records, start=1):
        source_id = series_source_ids[series_index - 1]
        emitted_id = series_emitted_ids[series_index - 1]

        raw_label = _label(record, source_id, field="Radar series")
        label = " ".join(raw_label.split())
        if not label or len(label) > MAX_TEXT_CHARS:
            raise SerializationError("Radar series label must be non-empty and bounded")
        if any(unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"} for character in label):
            raise SerializationError("Radar series label contains unsupported text")
        try:
            label.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("Radar series label is not valid UTF-8") from exc
        native_canvas_label = label.translate(native_label_translation)
        fallback_canvas_label = (
            label.translate(fallback_label_translation).replace('"', "″").replace("\\", "∖")
        )
        native_compatibility_substitutions |= native_canvas_label != raw_label
        fallback_compatibility_substitutions |= fallback_canvas_label != raw_label
        values = record.get("values")
        if not isinstance(values, list):
            raise SerializationError(f"Radar series {source_id!r} requires a values list")
        if len(values) != len(dimensions):
            raise SerializationError(
                f"Radar series {source_id!r} has {len(values)} values for "
                f"{len(dimensions)} dimensions"
            )
        evidence_ids = _safe_evidence_ids(record)
        point_rows: list[dict[str, Any]] = []
        for point_index, (dimension, raw_value) in enumerate(
            zip(dimensions, values, strict=True), start=1
        ):
            value_text, value = _finite_number(
                raw_value,
                field=f"Radar series {source_id!r} value {point_index}",
            )
            try:
                native_value = float(value_text)
            except (OverflowError, ValueError):
                native_value = None
            if native_value is not None and (
                not math.isfinite(native_value)
                or (value != 0 and native_value == 0)
                or (native_value != 0 and abs(native_value) < sys.float_info.min)
                or Decimal(str(native_value)) != value
            ):
                native_value = None
            cell_base = f"{emitted_id}_{point_index}"
            cell_base = cell_base[:MAX_ID_CHARS]
            scene_id = cell_base
            suffix = 2
            while scene_id in used_terminal_ids:
                suffix_text = f"_{suffix}"
                scene_id = f"{cell_base[: MAX_ID_CHARS - len(suffix_text)]}{suffix_text}"
                suffix += 1
            used_terminal_ids.add(scene_id)
            combined_evidence = tuple(dict.fromkeys((*dimension.evidence_ids, *evidence_ids)))
            if len(combined_evidence) > MAX_EVIDENCE_REFS:
                combined_evidence = ()
            fallback_canvas_value = f"{dimension.fallback_canvas_label}: {value_text}"
            point_rows.append(
                {
                    "scene_id": scene_id,
                    "dimension": dimension,
                    "fallback_source_label": (
                        f"{dimension.fallback_source_label}: {_flow_text(value_text)}"
                    ),
                    "fallback_canvas_label": fallback_canvas_value,
                    "value_text": value_text,
                    "value": value,
                    "native_value": native_value,
                    "evidence_ids": combined_evidence,
                }
            )
            all_values.append(value)
            all_native_values.append(native_value)
        series_rows.append(
            {
                "source_record": record,
                "source_id": source_id,
                "emitted_id": emitted_id,
                "label": label,
                "native_source_label": _radar_string(native_canvas_label),
                "native_canvas_label": native_canvas_label,
                "fallback_source_label": _flow_text(fallback_canvas_label),
                "fallback_canvas_label": fallback_canvas_label,
                "point_rows": point_rows,
                "evidence_ids": evidence_ids,
            }
        )

    minimum_text: str | None = None
    minimum: Decimal | None = None
    minimum_native: float | None = None
    maximum_text: str | None = None
    maximum: Decimal | None = None
    maximum_native: float | None = None
    for field in ("min", "max"):
        if field not in ir or ir[field] is None:
            continue
        value_text, value = _finite_number(ir[field], field=f"Radar {field}")
        try:
            native_value = float(value_text)
        except (OverflowError, ValueError):
            native_value = None
        if native_value is not None and (
            not math.isfinite(native_value)
            or (value != 0 and native_value == 0)
            or (native_value != 0 and abs(native_value) < sys.float_info.min)
            or Decimal(str(native_value)) != value
        ):
            native_value = None
        if field == "min":
            minimum_text, minimum, minimum_native = value_text, value, native_value
        else:
            maximum_text, maximum, maximum_native = value_text, value, native_value
    if minimum is not None and maximum is not None and minimum >= maximum:
        raise SerializationError("Radar min must be smaller than max")
    if minimum is not None and any(value < minimum for value in all_values):
        raise SerializationError("Radar values must not be smaller than explicit min")
    if maximum is not None and any(value > maximum for value in all_values):
        raise SerializationError("Radar values must not exceed explicit max")

    ticks_explicit = ir.get("ticks") is not None
    ticks = ir.get("ticks") if ticks_explicit else 5
    if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 1:
        raise SerializationError("Radar ticks must be a positive integer")
    if ticks > MAX_RADAR_TICKS:
        raise SerializationError(f"Radar ticks must not exceed {MAX_RADAR_TICKS}")
    show_legend_explicit = ir.get("show_legend") is not None
    show_legend = ir.get("show_legend") if show_legend_explicit else True
    if not isinstance(show_legend, bool):
        raise SerializationError("Radar show_legend must be boolean")
    graticule_explicit = ir.get("graticule") is not None
    graticule = ir.get("graticule") if graticule_explicit else "circle"
    if graticule not in {"circle", "polygon"}:
        raise SerializationError("Radar graticule must be circle or polygon")

    native_limitations: list[str] = []
    if len(series_rows) > MAX_RADAR_NATIVE_SERIES:
        native_limitations.append(
            f"more than {MAX_RADAR_NATIVE_SERIES} series exceed the pinned Radar theme capacity"
        )
    if any(value < 0 for value in all_values) or (minimum is not None and minimum < 0):
        native_limitations.append("negative values or bounds are not native-safe")
    if (
        any(value is None for value in all_native_values)
        or (minimum is not None and minimum_native is None)
        or (maximum is not None and maximum_native is None)
    ):
        native_limitations.append("one or more values are not normal binary64 round-trip safe")

    effective_minimum = minimum if minimum is not None else Decimal(0)
    effective_maximum = maximum if maximum is not None else max(all_values)
    if effective_maximum <= effective_minimum:
        native_limitations.append("the effective native scale has zero or negative span")
    effective_minimum_native = minimum_native if minimum is not None else 0.0
    effective_maximum_native = (
        maximum_native
        if maximum is not None
        else (
            max(value for value in all_native_values if value is not None)
            if all(value is not None for value in all_native_values)
            else None
        )
    )
    native_span: float | None = None
    if effective_minimum_native is not None and effective_maximum_native is not None:
        native_span = effective_maximum_native - effective_minimum_native
        if not math.isfinite(native_span) or native_span <= 0:
            limitation = "the effective binary64 scale has zero, negative, or non-finite span"
            if limitation not in native_limitations:
                native_limitations.append(limitation)
        elif any(
            native_value is None
            or not math.isfinite(RADAR_RENDER_RADIUS * (native_value - effective_minimum_native))
            for native_value in all_native_values
        ):
            native_limitations.append("the pinned renderer would overflow Radar curve coordinates")

    native_supported = not native_limitations
    series: list[RadarSeriesPlan] = []
    for series_row in series_rows:
        points: list[RadarPointPlan] = []
        for point_index, point_row in enumerate(series_row["point_rows"], start=1):
            dimension = point_row["dimension"]
            normalized_point: tuple[float, float] | None = None
            native_value = point_row["native_value"]
            if native_supported and native_span is not None and native_value is not None:
                angle = 2 * (point_index - 1) * math.pi / len(dimensions) - math.pi / 2
                radius = (
                    RADAR_RENDER_RADIUS
                    * (native_value - effective_minimum_native)
                    / native_span
                    / (2 * RADAR_RENDER_RADIUS)
                )
                normalized_point = (
                    0.5 + radius * math.cos(angle),
                    0.5 + radius * math.sin(angle),
                )
            points.append(
                RadarPointPlan(
                    scene_id=point_row["scene_id"],
                    dimension_source_id=dimension.source_id,
                    dimension_emitted_id=dimension.emitted_id,
                    dimension_label=dimension.label,
                    fallback_source_label=point_row["fallback_source_label"],
                    fallback_canvas_label=point_row["fallback_canvas_label"],
                    value_text=point_row["value_text"],
                    value=point_row["value"],
                    native_value=native_value,
                    normalized_point=normalized_point,
                    evidence_ids=point_row["evidence_ids"],
                )
            )
        series.append(
            RadarSeriesPlan(
                source_record=series_row["source_record"],
                source_id=series_row["source_id"],
                emitted_id=series_row["emitted_id"],
                label=series_row["label"],
                native_source_label=series_row["native_source_label"],
                native_canvas_label=series_row["native_canvas_label"],
                fallback_source_label=series_row["fallback_source_label"],
                fallback_canvas_label=series_row["fallback_canvas_label"],
                points=tuple(points),
                evidence_ids=series_row["evidence_ids"],
            )
        )

    semantic_title: str | None = None
    native_source_title: str | None = None
    native_canvas_title: str | None = None
    raw_title = ir.get("title")
    if raw_title is not None and raw_title != "":
        if type(raw_title) is not str:
            raise SerializationError("Radar title must be text")
        semantic_title = " ".join(raw_title.split())
        if not semantic_title or len(semantic_title) > MAX_TEXT_CHARS:
            raise SerializationError("Radar title must be non-empty and bounded")
        if any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            for character in semantic_title
        ):
            raise SerializationError("Radar title contains unsupported text")
        try:
            semantic_title.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SerializationError("Radar title is not valid UTF-8") from exc
        native_canvas_title = semantic_title.translate(
            str.maketrans({"<": "＜", ">": "＞", "#": "＃", ";": "；"})
        )
        native_source_title = _plain_text(native_canvas_title)
        native_compatibility_substitutions |= native_canvas_title != raw_title

    flowchart_line_count = 3 + 2 * len(series) + point_count
    return RadarPlan(
        dimensions=tuple(dimensions),
        series=tuple(series),
        native_supported=native_supported,
        flowchart_supported=(
            point_count <= MAX_RADAR_FLOWCHART_POINTS
            and flowchart_line_count + 1 <= MAX_RADAR_OUTPUT_LINES
        ),
        native_limitations=tuple(native_limitations),
        minimum_text=minimum_text,
        maximum_text=maximum_text,
        ticks=ticks,
        ticks_explicit=ticks_explicit,
        show_legend=show_legend,
        show_legend_explicit=show_legend_explicit,
        graticule=graticule,
        graticule_explicit=graticule_explicit,
        semantic_title=semantic_title,
        native_source_title=native_source_title,
        native_canvas_title=native_canvas_title,
        native_compatibility_substitutions=native_compatibility_substitutions,
        fallback_compatibility_substitutions=fallback_compatibility_substitutions,
    )


def _radar_flowchart(
    ir: Mapping[str, Any],
    plan: RadarPlan,
    *,
    experimental: bool,
) -> str:
    if not plan.flowchart_supported:
        raise SerializationError(
            f"Radar Flowchart fallback exceeds the {MAX_RADAR_FLOWCHART_POINTS}-point runtime limit"
        )
    lines = ["flowchart TB"]
    accessibility = resolve_accessibility(ir, "radar", experimental=experimental)
    lines.append(f"    accTitle: {_plain_text(accessibility.title)}")
    suffix = "This radar reconstruction uses a tabular flowchart fallback."
    description = f"{accessibility.description} {suffix}"
    lines.append(f"    accDescr: {_plain_text(description)}")
    for series in plan.series:
        lines.append(f'    subgraph {series.emitted_id}["{series.fallback_source_label}"]')
        for point in series.points:
            lines.append(f'        {point.scene_id}["{point.fallback_source_label}"]')
        lines.append("    end")
    return _preflight_radar_code("\n".join(lines) + "\n")


def serialize_radar(
    ir: Mapping[str, Any],
    *,
    experimental: bool = False,
    native_runtime_valid: bool = True,
) -> SerializationResult:
    """Serialize dimension-aligned series without inventing absent values."""

    if not isinstance(native_runtime_valid, bool):
        raise SerializationError("native_runtime_valid must be a boolean")
    plan = plan_radar_records(ir)
    if not native_runtime_valid or not plan.native_supported:
        warnings = [
            RADAR_RUNTIME_FALLBACK_WARNING
            if not native_runtime_valid
            else f"{RADAR_FALLBACK_WARNING} {'; '.join(plan.native_limitations)}"
        ]
        if plan.fallback_compatibility_substitutions:
            warnings.append(RADAR_FALLBACK_TEXT_COMPATIBILITY_WARNING)
        return SerializationResult.fallback(
            "radar",
            "flowchart",
            _strict_source(_radar_flowchart(ir, plan, experimental=experimental)),
            warnings=tuple(warnings),
            stability="experimental",
        )

    lines = ["radar-beta"]
    if plan.native_source_title is not None:
        lines.append(f"title {plan.native_source_title}")
    accessibility = resolve_accessibility(ir, "radar", experimental=experimental)
    lines.append(f"accTitle: {_plain_text(accessibility.title)}")
    lines.append(f"accDescr: {_plain_text(accessibility.description)}")
    lines.append(
        "axis "
        + ", ".join(
            f'{dimension.emitted_id}["{dimension.native_source_label}"]'
            for dimension in plan.dimensions
        )
    )
    for series in plan.series:
        lines.append(
            f'curve {series.emitted_id}["{series.native_source_label}"]'
            f"{{{', '.join(point.value_text for point in series.points)}}}"
        )
    if plan.show_legend_explicit:
        lines.append(f"showLegend {str(plan.show_legend).lower()}")
    if plan.ticks_explicit:
        lines.append(f"ticks {plan.ticks}")
    if plan.maximum_text is not None:
        lines.append(f"max {plan.maximum_text}")
    if plan.minimum_text is not None:
        lines.append(f"min {plan.minimum_text}")
    if plan.graticule_explicit:
        lines.append(f"graticule {plan.graticule}")
    code = _strict_source(_preflight_radar_code("\n".join(lines) + "\n"))
    warnings = (
        (RADAR_NATIVE_TEXT_COMPATIBILITY_WARNING,)
        if plan.native_compatibility_substitutions
        else ()
    )
    return SerializationResult.native("radar", code, warnings=warnings, stability="experimental")


CHART_FLOW_SERIALIZERS: dict[str, Callable[[Mapping[str, Any]], SerializationResult]] = {
    "sankey": serialize_sankey,
    "radar": serialize_radar,
}


def serialize_chart_flow(
    diagram_type: str, ir: Mapping[str, Any], *, experimental: bool = False
) -> SerializationResult:
    """Dispatch the chart-flow serializer family without a silent type change."""

    serializer = CHART_FLOW_SERIALIZERS.get(diagram_type)
    if serializer is None:
        raise SerializationError(f"unsupported chart-flow diagram type: {diagram_type}")
    return serializer(ir, experimental=experimental)
