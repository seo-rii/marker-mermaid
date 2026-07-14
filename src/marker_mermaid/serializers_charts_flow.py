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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from marker_mermaid.accessibility import resolve_accessibility
from marker_mermaid.config import SecurityProfile
from marker_mermaid.flowchart_structure import (
    FlowchartStructureError,
    plan_flowchart_structure,
)
from marker_mermaid.models import MAX_ID_CHARS, MAX_SCENE_RELATIONS, MAX_TEXT_CHARS
from marker_mermaid.resource_limits import MAX_EVIDENCE_REFS
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serialization import SerializationResult
from marker_mermaid.serializers import SerializationError, serialize_flowchart

RadarDimension = tuple[str, str]
RadarSeries = tuple[str, str, tuple[str, ...], tuple[Decimal, ...]]
MAX_RADAR_TICKS = 100
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
    "Radar was emitted as a tabular flowchart because its numeric domain cannot "
    "be represented by Mermaid 11.16 radar syntax without loss."
)


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
    decimal = Decimal(str(value))
    if not decimal.is_finite():
        raise SerializationError(f"{field} requires an explicit finite number")
    if decimal == 0:
        return "0", Decimal(0)
    rendered = format(decimal, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered, decimal


def _safe_identifier(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", value).strip("_-")
    if not normalized:
        normalized = fallback
    if not re.match(r"[A-Za-z_]", normalized):
        normalized = f"n_{normalized}"
    return normalized


def _unique_output_ids(source_ids: Sequence[str], *, prefix: str) -> dict[str, str]:
    used: set[str] = set()
    output: dict[str, str] = {}
    for index, source_id in enumerate(source_ids, start=1):
        base = _safe_identifier(source_id, f"{prefix}{index}")
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        output[source_id] = candidate
    return output


def _flow_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _radar_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _plain_text(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def _strict_source(code: str) -> str:
    report = MermaidSecurityScanner(SecurityProfile.STRICT).scan(code)
    if not report.safe:
        rules = ", ".join(sorted({finding.rule for finding in report.findings}))
        raise SerializationError(f"chart text violates the strict security profile: {rules}")
    return code


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
        raw_evidence_ids = record.get("evidence_ids")
        evidence_ids: tuple[str, ...] = ()
        if (
            isinstance(raw_evidence_ids, list)
            and len(raw_evidence_ids) <= MAX_EVIDENCE_REFS
            and all(
                type(evidence_id) is str and bool(evidence_id) and len(evidence_id) <= MAX_ID_CHARS
                for evidence_id in raw_evidence_ids
            )
        ):
            try:
                for evidence_id in raw_evidence_ids:
                    evidence_id.encode("utf-8")
            except UnicodeEncodeError:
                pass
            else:
                evidence_ids = tuple(raw_evidence_ids)
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
        raw_evidence_ids = record.get("evidence_ids")
        evidence_ids = ()
        if (
            isinstance(raw_evidence_ids, list)
            and len(raw_evidence_ids) <= MAX_EVIDENCE_REFS
            and all(
                type(evidence_id) is str and bool(evidence_id) and len(evidence_id) <= MAX_ID_CHARS
                for evidence_id in raw_evidence_ids
            )
        ):
            try:
                for evidence_id in raw_evidence_ids:
                    evidence_id.encode("utf-8")
            except UnicodeEncodeError:
                pass
            else:
                evidence_ids = tuple(raw_evidence_ids)
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


def _radar_data(
    ir: Mapping[str, Any],
) -> tuple[list[RadarDimension], list[RadarSeries], dict[str, tuple[str, Decimal] | Any]]:
    raw_dimensions = ir.get("dimensions", ir.get("axes"))
    dimension_records = _required_records(raw_dimensions, field="Radar dimensions")
    if len(dimension_records) < 3:
        raise SerializationError("Radar requires at least three dimensions")
    dimensions: list[RadarDimension] = []
    seen_dimensions: set[str] = set()
    for record in dimension_records:
        source_id = _explicit_id(record, field="Radar dimension")
        if source_id in seen_dimensions:
            raise SerializationError(f"Radar dimension id {source_id!r} is duplicated")
        seen_dimensions.add(source_id)
        dimensions.append((source_id, _label(record, source_id, field="Radar dimension")))

    series_records = _required_records(ir.get("series"), field="Radar series")
    series: list[RadarSeries] = []
    seen_series: set[str] = set()
    for record in series_records:
        source_id = _explicit_id(record, field="Radar series")
        if source_id in seen_series:
            raise SerializationError(f"Radar series id {source_id!r} is duplicated")
        seen_series.add(source_id)
        values = record.get("values")
        if not isinstance(values, list):
            raise SerializationError(f"Radar series {source_id!r} requires a values list")
        if len(values) != len(dimensions):
            raise SerializationError(
                f"Radar series {source_id!r} has {len(values)} values for "
                f"{len(dimensions)} dimensions"
            )
        rendered_values: list[str] = []
        decimals: list[Decimal] = []
        for index, value in enumerate(values, start=1):
            rendered, decimal = _finite_number(
                value, field=f"Radar series {source_id!r} value {index}"
            )
            rendered_values.append(rendered)
            decimals.append(decimal)
        series.append(
            (
                source_id,
                _label(record, source_id, field="Radar series"),
                tuple(rendered_values),
                tuple(decimals),
            )
        )

    options: dict[str, tuple[str, Decimal] | Any] = {}
    for field in ("min", "max"):
        if field in ir and ir[field] is not None:
            options[field] = _finite_number(ir[field], field=f"Radar {field}")
    if (
        "min" in options and "max" in options and options["min"][1] >= options["max"][1]  # type: ignore[index]
    ):
        raise SerializationError("Radar min must be smaller than max")
    all_values = [value for _, _, _, values in series for value in values]
    if "min" in options and any(value < options["min"][1] for value in all_values):  # type: ignore[index]
        raise SerializationError("Radar values must not be smaller than explicit min")
    if "max" in options and any(value > options["max"][1] for value in all_values):  # type: ignore[index]
        raise SerializationError("Radar values must not exceed explicit max")

    if "ticks" in ir and ir["ticks"] is not None:
        ticks = ir["ticks"]
        if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks < 1:
            raise SerializationError("Radar ticks must be a positive integer")
        if ticks > MAX_RADAR_TICKS:
            raise SerializationError(f"Radar ticks must not exceed {MAX_RADAR_TICKS}")
        options["ticks"] = ticks
    if "show_legend" in ir and not isinstance(ir["show_legend"], bool):
        raise SerializationError("Radar show_legend must be boolean")
    if "show_legend" in ir:
        options["show_legend"] = ir["show_legend"]
    graticule = ir.get("graticule")
    if graticule is not None:
        if graticule not in {"circle", "polygon"}:
            raise SerializationError("Radar graticule must be circle or polygon")
        options["graticule"] = graticule
    return dimensions, series, options


def _radar_native_supported(
    series: Sequence[RadarSeries], options: Mapping[str, tuple[str, Decimal] | Any]
) -> bool:
    values = [value for _, _, _, numbers in series for value in numbers]
    bounds = [options[field][1] for field in ("min", "max") if field in options]
    return all(value >= 0 for value in (*values, *bounds))


def _radar_flowchart(
    ir: Mapping[str, Any],
    dimensions: Sequence[RadarDimension],
    series: Sequence[RadarSeries],
    *,
    experimental: bool,
) -> str:
    dimension_labels = [label for _, label in dimensions]
    series_ids = _unique_output_ids([source_id for source_id, _, _, _ in series], prefix="S")
    lines = ["flowchart TB"]
    accessibility = resolve_accessibility(ir, "radar", experimental=experimental)
    lines.append(f"    accTitle: {_flow_text(accessibility.title)}")
    suffix = "This radar reconstruction uses a tabular flowchart fallback."
    description = f"{accessibility.description} {suffix}"
    lines.append(f"    accDescr: {_flow_text(description)}")
    for source_id, label, rendered_values, _ in series:
        output_id = series_ids[source_id]
        lines.append(f'    subgraph {output_id}["{_flow_text(label)}"]')
        for index, (dimension, value) in enumerate(
            zip(dimension_labels, rendered_values, strict=True), start=1
        ):
            lines.append(
                f'        {output_id}_{index}["{_flow_text(dimension)}: {_flow_text(value)}"]'
            )
        lines.append("    end")
    return "\n".join(lines) + "\n"


def serialize_radar(ir: Mapping[str, Any], *, experimental: bool = False) -> SerializationResult:
    """Serialize dimension-aligned series without inventing absent values."""

    dimensions, series, options = _radar_data(ir)
    if not _radar_native_supported(series, options):
        return SerializationResult.fallback(
            "radar",
            "flowchart",
            _strict_source(_radar_flowchart(ir, dimensions, series, experimental=experimental)),
            warnings=(RADAR_FALLBACK_WARNING,),
            stability="experimental",
        )

    dimension_ids = _unique_output_ids([source_id for source_id, _ in dimensions], prefix="D")
    series_ids = _unique_output_ids([source_id for source_id, _, _, _ in series], prefix="S")
    lines = ["radar-beta"]
    if ir.get("title"):
        lines.append(f"title {_plain_text(ir['title'])}")
    accessibility = resolve_accessibility(ir, "radar", experimental=experimental)
    lines.append(f"accTitle: {_plain_text(accessibility.title)}")
    lines.append(f"accDescr: {_plain_text(accessibility.description)}")
    lines.append(
        "axis "
        + ", ".join(
            f'{dimension_ids[source_id]}["{_radar_string(label)}"]'
            for source_id, label in dimensions
        )
    )
    for source_id, label, values, _ in series:
        lines.append(
            f'curve {series_ids[source_id]}["{_radar_string(label)}"]{{{", ".join(values)}}}'
        )
    if "show_legend" in options:
        lines.append(f"showLegend {str(options['show_legend']).lower()}")
    if "ticks" in options:
        lines.append(f"ticks {options['ticks']}")
    for field in ("max", "min"):
        if field in options:
            lines.append(f"{field} {options[field][0]}")  # type: ignore[index]
    if "graticule" in options:
        lines.append(f"graticule {options['graticule']}")
    code = _strict_source("\n".join(lines) + "\n")
    return SerializationResult.native("radar", code, stability="experimental")


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
