"""Deterministic accessible text derived only from typed IR evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

EXPERIMENTAL_NOTICE = "This reconstruction is experimental and requires review."
MAX_DERIVED_LABELS = 5
MAX_DERIVED_TEXT = 1_000
ACCESSIBILITY_UNSUPPORTED_GRAMMARS = frozenset(
    {"block", "ishikawa", "kanban", "mindmap", "sankey", "timeline", "venn"}
)

_DISPLAY_NAMES = {
    "bpmn": "BPMN process",
    "c4": "C4 model",
    "cynefin": "Cynefin diagram",
    "data_lineage": "Data lineage diagram",
    "er": "Entity relationship diagram",
    "eventmodeling": "Event model",
    "generic_network": "Network diagram",
    "gitgraph": "Git graph",
    "ishikawa": "Ishikawa diagram",
    "organization": "Organization chart",
    "quadrant": "Quadrant chart",
    "railroad": "Railroad diagram",
    "treeview": "Tree view",
    "usecase": "Use-case diagram",
    "wardley": "Wardley map",
    "xychart": "XY chart",
    "zenuml": "Sequence diagram",
}

_NODE_FIELDS = (
    "nodes",
    "services",
    "participants",
    "states",
    "classes",
    "entities",
    "elements",
    "requirements",
    "components",
    "datasets",
    "processes",
    "columns",
    "cards",
    "sets",
    "slices",
    "points",
    "series",
    "dimensions",
    "fields",
    "domains",
    "rules",
    "events",
)
_EDGE_FIELDS = ("edges", "messages", "transitions", "relations", "flows", "links")
_GRAMMAR_HEADERS = {
    "architecture": ("architecture-beta",),
    "c4": ("c4context", "c4container", "c4component"),
    "class": ("classdiagram",),
    "cynefin": ("cynefin-beta",),
    "er": ("erdiagram",),
    "flowchart": ("flowchart", "graph"),
    "gantt": ("gantt",),
    "gitgraph": ("gitgraph",),
    "packet": ("packet-beta",),
    "pie": ("pie",),
    "quadrant": ("quadrantchart",),
    "radar": ("radar-beta",),
    "railroad": ("railroad-beta",),
    "requirement": ("requirementdiagram",),
    "sequence": ("sequencediagram",),
    "state": ("statediagram", "statediagram-v2"),
    "treemap": ("treemap-beta",),
    "treeview": ("treeview-beta",),
    "wardley": ("wardley-beta",),
    "xychart": ("xychart-beta",),
}


@dataclass(frozen=True, slots=True)
class AccessibilityText:
    title: str
    description: str
    generated_title: bool
    generated_description: bool


def supports_accessibility_directives(emitted_type: str) -> bool:
    """Return whether pinned Mermaid 11.16 produces real SVG accessibility metadata."""

    normalized = emitted_type.casefold()
    normalized = {
        "flowchart-v2": "flowchart",
        "quadrantchart": "quadrant",
        "statediagram": "state",
    }.get(normalized, normalized)
    return normalized not in ACCESSIBILITY_UNSUPPORTED_GRAMMARS


def accessibility_limitation_warning(emitted_type: str) -> str:
    return (
        f"Mermaid 11.16 {emitted_type} grammar cannot safely emit accTitle/accDescr; "
        "resolved accessibility text remains in typed IR and review metadata."
    )


def _plain(value: Any) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _display_name(diagram_type: str) -> str:
    return _DISPLAY_NAMES.get(diagram_type, diagram_type.replace("_", " ").title())


def _record_label(record: Any) -> str | None:
    if isinstance(record, str):
        return _plain(record) or None
    if not isinstance(record, Mapping):
        return None
    for field in ("label", "name", "text", "time", "period", "id"):
        value = record.get(field)
        if value is not None and (text := _plain(value)):
            return text
    return None


def _structural_records(ir: Mapping[str, Any]) -> list[Any]:
    records: list[Any] = []
    for field in _NODE_FIELDS:
        value = ir.get(field)
        if isinstance(value, list):
            records.extend(value)
    root = ir.get("root")
    if isinstance(root, Mapping):
        pending = [root]
        visited: set[int] = set()
        while pending and len(records) < 500:
            item = pending.pop(0)
            if id(item) in visited:
                continue
            visited.add(id(item))
            records.append(item)
            children = item.get("children")
            if isinstance(children, list):
                pending.extend(child for child in children if isinstance(child, Mapping))
    for section in ir.get("sections", []) if isinstance(ir.get("sections"), list) else []:
        if not isinstance(section, Mapping):
            continue
        records.append(section)
        for field in ("tasks", "events"):
            children = section.get(field)
            if isinstance(children, list):
                records.extend(children)
    for lane in ir.get("lanes", []) if isinstance(ir.get("lanes"), list) else []:
        if not isinstance(lane, Mapping):
            continue
        records.append(lane)
        for field in ("nodes", "frames"):
            children = lane.get(field)
            if isinstance(children, list):
                records.extend(children)
    return records[:500]


def _labels(records: list[Any]) -> list[str]:
    result: list[str] = []
    for record in records:
        label = _record_label(record)
        if label and label not in result:
            result.append(label)
            if len(result) >= MAX_DERIVED_LABELS:
                break
    return result


def _unique_path_endpoints(ir: Mapping[str, Any], records: list[Any]) -> tuple[str, str] | None:
    labels_by_id: dict[str, str] = {}
    for record in records:
        if not isinstance(record, Mapping) or record.get("id") is None:
            continue
        source_id = _plain(record["id"])
        label = _record_label(record)
        if source_id and label:
            labels_by_id[source_id] = label
    if len(labels_by_id) < 2:
        return None
    edges: list[Mapping[str, Any]] = []
    for field in _EDGE_FIELDS:
        value = ir.get(field)
        if isinstance(value, list):
            edges.extend(item for item in value if isinstance(item, Mapping))
    incoming = {node_id: 0 for node_id in labels_by_id}
    outgoing = {node_id: 0 for node_id in labels_by_id}
    for edge in edges:
        source = _plain(edge.get("source", ""))
        target = _plain(edge.get("target", ""))
        if source in outgoing and target in incoming and source != target:
            outgoing[source] += 1
            incoming[target] += 1
    starts = [node_id for node_id in labels_by_id if incoming[node_id] == 0 and outgoing[node_id]]
    ends = [node_id for node_id in labels_by_id if outgoing[node_id] == 0 and incoming[node_id]]
    if len(starts) == len(ends) == 1:
        return labels_by_id[starts[0]], labels_by_id[ends[0]]
    return None


def resolve_accessibility(
    ir: Mapping[str, Any],
    diagram_type: str,
    *,
    experimental: bool,
) -> AccessibilityText:
    """Resolve explicit text or derive a bounded structural summary without guessing semantics."""

    display_name = _display_name(diagram_type)
    explicit_title = ir.get("acc_title") or ir.get("title")
    explicit_description = ir.get("acc_description") or ir.get("description")
    title = (
        _plain(explicit_title) if explicit_title is not None else f"{display_name} reconstruction"
    )
    records = _structural_records(ir)
    if explicit_description is not None:
        description = _plain(explicit_description)
    else:
        labels = _labels(records)
        if labels:
            description = f"{display_name} reconstruction containing {', '.join(labels)}."
        else:
            description = f"{display_name} reconstruction based on extracted visual evidence."
        endpoints = _unique_path_endpoints(ir, records)
        if endpoints is not None:
            description += (
                f" The directed structure starts at {endpoints[0]} and ends at {endpoints[1]}."
            )
        description = description[:MAX_DERIVED_TEXT].rstrip()
    if experimental and EXPERIMENTAL_NOTICE not in description:
        description = f"{description} {EXPERIMENTAL_NOTICE}".strip()
    return AccessibilityText(
        title=title,
        description=description,
        generated_title=explicit_title is None,
        generated_description=explicit_description is None,
    )


def enrich_accessibility_ir(
    ir: Mapping[str, Any],
    diagram_type: str,
    *,
    experimental: bool,
) -> dict[str, Any]:
    """Return a shallow IR copy carrying resolved accessible text for serializers/sidecars."""

    resolved = resolve_accessibility(ir, diagram_type, experimental=experimental)
    return {
        **ir,
        "acc_title": resolved.title,
        "acc_description": resolved.description,
    }


def augment_accessibility_directives(
    code: str,
    emitted_type: str,
    ir: Mapping[str, Any],
    *,
    semantic_type: str,
    experimental: bool,
) -> str | None:
    """Insert missing directives for a validated grammar; callers must revalidate the result."""

    if not supports_accessibility_directives(emitted_type):
        return None
    existing = {line.strip().split(":", 1)[0].rstrip() for line in code.splitlines()}
    if {"accTitle", "accDescr"} <= existing:
        return None
    resolved = resolve_accessibility(ir, semantic_type, experimental=experimental)

    def escape(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace('"', "&quot;")
            .replace("\r", " ")
            .replace("\n", " ")
            .strip()
        )

    additions: list[str] = []
    if "accTitle" not in existing:
        additions.append(f"    accTitle: {escape(resolved.title)}")
    if "accDescr" not in existing:
        additions.append(f"    accDescr: {escape(resolved.description)}")
    lines = code.rstrip("\n").splitlines()
    if not lines:
        return None
    normalized_type = emitted_type.casefold()
    normalized_type = {
        "flowchart-v2": "flowchart",
        "quadrantchart": "quadrant",
        "statediagram": "state",
    }.get(normalized_type, normalized_type)
    headers = _GRAMMAR_HEADERS.get(normalized_type)
    first_line = lines[0].lstrip().casefold()
    if headers is None or not any(
        first_line == header or first_line.startswith(f"{header} ") for header in headers
    ):
        return None
    return "\n".join([lines[0], *additions, *lines[1:]]) + "\n"
