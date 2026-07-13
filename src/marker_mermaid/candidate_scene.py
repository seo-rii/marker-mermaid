"""Reconstruct the structural scene actually emitted by typed serializers.

This adapter is deliberately narrower than a Mermaid parser.  It covers typed IR
families whose serializers have deterministic node/edge semantics and returns
``None`` for unsupported data rather than guessing from raw Mermaid text.  Layout
coordinates are retained only when the IR explicitly carries a bbox; otherwise
nodes use a shared origin so layout scoring remains unavailable.
"""

from __future__ import annotations

from typing import Any

from marker_mermaid.flowchart_structure import (
    FlowchartStructureError,
    FlowchartStructurePlan,
    plan_flowchart_structure,
    prepare_swimlane_structure,
)
from marker_mermaid.models import DiagramSceneIR, SceneElement, SceneGroup, SceneRelation


def _hierarchy_records(
    root: dict[str, Any], *, fallback_root_id: str = "root"
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    pending = [(root, None, fallback_root_id)]
    while pending:
        node, parent_id, fallback_id = pending.pop(0)
        node_id = str(node.get("id") or fallback_id)
        nodes.append({**node, "id": node_id})
        if parent_id is not None:
            edges.append(
                {
                    "source": parent_id,
                    "target": node_id,
                    "semantic_relation": "containment",
                    "evidence_ids": list(node.get("evidence_ids") or []),
                }
            )
        for index, child in enumerate(node.get("children") or [], start=1):
            if isinstance(child, dict):
                pending.append((child, node_id, f"{node_id}_{index}"))
    return nodes, edges


def _ordered_records(
    records: Any, *, prefix: str, label_field: str = "label"
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records or [], start=1):
        if not isinstance(record, dict):
            continue
        result.append(
            {
                **record,
                "id": record.get("id") or f"{prefix}{index}",
                "label": record.get(label_field)
                or record.get("text")
                or record.get("name")
                or record.get("time")
                or record.get("period"),
            }
        )
    return result


def typed_ir_to_scene(diagram_type: str, ir: dict[str, Any]) -> DiagramSceneIR | None:
    """Convert deterministic typed-IR node and relation fields into a scene."""

    node_records: list[dict[str, Any]] = []
    edge_records: list[dict[str, Any]] = []
    group_records: list[dict[str, Any]] = []
    flowchart_structure: FlowchartStructurePlan | None = None
    if diagram_type in {"flowchart", "generic_network"}:
        node_records = list(ir.get("nodes") or [])
        edge_records = list(ir.get("edges") or [])
        group_records = list(ir.get("groups") or [])
    elif diagram_type in {"swimlane", "bpmn"}:
        try:
            swimlane_structure = prepare_swimlane_structure(ir.get("lanes"))
        except FlowchartStructureError:
            return None
        node_records = list(swimlane_structure.nodes)
        edge_records = list(ir.get("edges") or [])
        group_records = list(swimlane_structure.groups)
    elif diagram_type == "architecture":
        node_records = list(ir.get("services") or [])
        edge_records = list(ir.get("edges") or [])
    elif diagram_type == "state":
        node_records = list(ir.get("states") or [])
        edge_records = [
            edge
            for edge in ir.get("transitions") or []
            if isinstance(edge, dict)
            and edge.get("source") != "[*]"
            and edge.get("target") != "[*]"
        ]
    elif diagram_type == "class":
        node_records = list(ir.get("classes") or [])
        edge_records = list(ir.get("relations") or [])
    elif diagram_type == "er":
        node_records = list(ir.get("entities") or [])
        edge_records = list(ir.get("relationships") or [])
    elif diagram_type == "requirement":
        node_records = [*(ir.get("requirements") or []), *(ir.get("elements") or [])]
        edge_records = list(ir.get("relations") or [])
    elif diagram_type == "block":
        node_records = list(ir.get("blocks") or [])
        edge_records = list(ir.get("edges") or [])
    elif diagram_type == "c4":
        node_records = list(ir.get("elements") or [])
        edge_records = list(ir.get("relations") or [])
    elif diagram_type == "deployment":
        node_records = [*(ir.get("nodes") or []), *(ir.get("artifacts") or [])]
        edge_records = list(ir.get("links") or ir.get("edges") or [])
    elif diagram_type == "component":
        node_records = [*(ir.get("components") or []), *(ir.get("interfaces") or [])]
        edge_records = list(ir.get("dependencies") or ir.get("edges") or [])
    elif diagram_type == "usecase":
        node_records = [*(ir.get("actors") or []), *(ir.get("use_cases") or [])]
        edge_records = list(ir.get("relations") or [])
    elif diagram_type == "sankey":
        node_records = list(ir.get("nodes") or [])
        edge_records = list(ir.get("flows") or ir.get("links") or [])
    elif diagram_type in {"sequence", "zenuml"}:
        for index, participant in enumerate(ir.get("participants") or [], start=1):
            if isinstance(participant, str):
                node_records.append({"id": participant, "label": participant})
            elif isinstance(participant, dict):
                node_records.append(
                    {
                        **participant,
                        "id": participant.get("id") or f"P{index}",
                    }
                )
        edge_records = list(ir.get("messages") or [])
    elif diagram_type in {"mindmap", "treemap", "treeview", "organization"} and isinstance(
        ir.get("root"), dict
    ):
        node_records, edge_records = _hierarchy_records(ir["root"])
    elif diagram_type == "ishikawa" and isinstance(ir.get("effect"), dict):
        root = {**ir["effect"], "children": list(ir.get("categories") or [])}
        node_records, edge_records = _hierarchy_records(root, fallback_root_id="effect")
    elif diagram_type == "timeline":
        node_records = _ordered_records(ir.get("events"), prefix="event_")
    elif diagram_type == "gantt":
        for section_index, section in enumerate(ir.get("sections") or [], start=1):
            if not isinstance(section, dict):
                continue
            member_ids: list[str] = []
            for task_index, task in enumerate(section.get("tasks") or [], start=1):
                if not isinstance(task, dict):
                    continue
                task_id = str(task.get("id") or f"section_{section_index}_task_{task_index}")
                node_records.append({**task, "id": task_id})
                member_ids.append(task_id)
            group_records.append(
                {
                    **section,
                    "id": section.get("id") or f"section_{section_index}",
                    "label": section.get("title") or "Tasks",
                    "member_ids": member_ids,
                }
            )
    elif diagram_type == "journey":
        for section_index, section in enumerate(ir.get("sections") or [], start=1):
            if not isinstance(section, dict):
                continue
            for task_index, task in enumerate(section.get("tasks") or [], start=1):
                if isinstance(task, dict):
                    node_records.append(
                        {
                            **task,
                            "id": task.get("id") or f"section_{section_index}_task_{task_index}",
                        }
                    )
    elif diagram_type == "kanban":
        columns = _ordered_records(ir.get("columns"), prefix="column_")
        cards = _ordered_records(ir.get("cards"), prefix="card_")
        node_records = [*columns, *cards]
        column_ids = {str(item["id"]) for item in columns}
        for card in cards:
            column_id = str(card.get("column_id") or "")
            if column_id in column_ids:
                edge_records.append(
                    {
                        "source": column_id,
                        "target": str(card["id"]),
                        "semantic_relation": "containment",
                        "evidence_ids": list(card.get("evidence_ids") or []),
                    }
                )
    elif diagram_type == "eventmodeling":
        node_records = [
            frame
            for lane in ir.get("lanes") or []
            if isinstance(lane, dict)
            for frame in lane.get("frames") or []
            if isinstance(frame, dict)
        ]
        edge_records = list(ir.get("relations") or [])
    elif diagram_type == "wardley":
        node_records = list(ir.get("components") or [])
        edge_records = list(ir.get("links") or [])
    elif diagram_type == "data_lineage":
        node_records = [*(ir.get("datasets") or []), *(ir.get("processes") or [])]
        edge_records = list(ir.get("relations") or [])
    elif diagram_type == "venn":
        node_records = list(ir.get("sets") or [])
        for index, intersection in enumerate(ir.get("intersections") or [], start=1):
            if not isinstance(intersection, dict):
                continue
            intersection_id = str(intersection.get("id") or f"intersection_{index}")
            node_records.append(
                {
                    **intersection,
                    "id": intersection_id,
                    "label": intersection.get("label") or intersection_id,
                }
            )
            for member in intersection.get("sets") or []:
                edge_records.append(
                    {
                        "source": str(member),
                        "target": intersection_id,
                        "semantic_relation": "containment",
                        "evidence_ids": list(intersection.get("evidence_ids") or []),
                    }
                )
    else:
        return None

    if diagram_type in {"flowchart", "generic_network", "swimlane", "bpmn"}:
        try:
            flowchart_structure = plan_flowchart_structure(node_records, group_records)
        except FlowchartStructureError:
            return None

    elements: list[SceneElement] = []
    known_ids: set[str] = set()
    for index, node in enumerate(node_records, start=1):
        if not isinstance(node, dict):
            continue
        node_id = (
            flowchart_structure.nodes[index - 1].emitted_id
            if flowchart_structure is not None
            else str(node.get("id") or f"N{index}")
        )
        if node_id in known_ids:
            continue
        bbox = _bbox(node.get("bbox"))
        elements.append(
            SceneElement(
                id=node_id,
                role=str(node.get("role") or "node"),
                text=str(node.get("label") or node.get("text") or node_id),
                bbox=bbox,
                shape=str(node.get("shape")) if node.get("shape") else None,
                confidence=1.0,
                evidence_ids=list(node.get("evidence_ids") or []),
            )
        )
        known_ids.add(node_id)
    if not elements:
        return None

    relations: list[SceneRelation] = []
    semantic_relations = {
        "sequence",
        "conditional",
        "causal",
        "dependency",
        "association",
        "containment",
        "message",
        "data_flow",
        "unknown",
    }
    emitted_id_by_source = (
        {node.source_id: node.emitted_id for node in flowchart_structure.nodes}
        if flowchart_structure is not None
        else {}
    )
    for index, edge in enumerate(edge_records, start=1):
        if not isinstance(edge, dict):
            continue
        raw_source = str(edge.get("source") or "")
        raw_target = str(edge.get("target") or "")
        source = emitted_id_by_source.get(raw_source, raw_source)
        target = emitted_id_by_source.get(raw_target, raw_target)
        if source not in known_ids or target not in known_ids:
            continue
        semantic_relation = str(edge.get("semantic_relation") or "unknown")
        if semantic_relation not in semantic_relations:
            semantic_relation = "unknown"
        relations.append(
            SceneRelation(
                id=str(edge.get("id") or f"generated-relation-{index}"),
                source_id=source,
                target_id=target,
                relation_type=str(edge.get("relation_type") or "generated_connector"),
                semantic_relation=semantic_relation,
                label=str(edge.get("label")) if edge.get("label") is not None else None,
                arrow_at_start=bool(edge.get("bidirectional") or edge.get("arrow_at_start")),
                arrow_at_end=bool(edge.get("arrow_at_end", diagram_type not in {"class", "er"})),
                line_style=str(edge.get("style")) if edge.get("style") else None,
                confidence=1.0,
                evidence_ids=list(edge.get("evidence_ids") or []),
            )
        )
    groups: list[SceneGroup] = []
    known_group_ids: set[str] = set()
    grouped_members: set[str] = set()
    elements_by_id = {element.id: element for element in elements}
    planned_groups = flowchart_structure.groups if flowchart_structure is not None else ()
    group_record_by_id = {
        str(group.get("id")): group
        for group in group_records
        if isinstance(group, dict) and group.get("id") is not None
    }
    for group in planned_groups:
        group_record = group_record_by_id.get(group.source_id, {})
        group_id = group.emitted_id
        member_ids = list(group.member_emitted_ids)
        if group_id in known_group_ids or grouped_members.intersection(member_ids):
            return None
        explicit_bbox = group_record.get("bbox")
        if isinstance(explicit_bbox, list | tuple) and len(explicit_bbox) == 4:
            bbox = _bbox(explicit_bbox)
        else:
            member_boxes = [elements_by_id[member_id].bbox for member_id in member_ids]
            bbox = (
                min(item[0] for item in member_boxes),
                min(item[1] for item in member_boxes),
                max(item[2] for item in member_boxes),
                max(item[3] for item in member_boxes),
            )
        groups.append(
            SceneGroup(
                id=group_id,
                role=str(group_record.get("role") or "subgraph"),
                label=str(group.label),
                bbox=bbox,
                member_ids=member_ids,
            )
        )
        known_group_ids.add(group_id)
        grouped_members.update(member_ids)
    if diagram_type == "gantt":
        for index, group_record in enumerate(group_records, start=1):
            group_id = str(group_record.get("id") or f"section_{index}")
            member_ids = [
                str(member_id)
                for member_id in group_record.get("member_ids") or []
                if str(member_id) in known_ids
            ]
            if not member_ids or group_id in known_group_ids:
                continue
            explicit_bbox = group_record.get("bbox")
            if isinstance(explicit_bbox, list | tuple) and len(explicit_bbox) == 4:
                bbox = _bbox(explicit_bbox)
            else:
                member_boxes = [elements_by_id[member_id].bbox for member_id in member_ids]
                bbox = (
                    min(item[0] for item in member_boxes),
                    min(item[1] for item in member_boxes),
                    max(item[2] for item in member_boxes),
                    max(item[3] for item in member_boxes),
                )
            groups.append(
                SceneGroup(
                    id=group_id,
                    role=str(group_record.get("role") or "section"),
                    label=str(group_record.get("label") or "Tasks"),
                    bbox=bbox,
                    member_ids=member_ids,
                )
            )
            known_group_ids.add(group_id)
    direction = ir.get("direction", "unknown")
    if direction not in {"TB", "BT", "LR", "RL", "radial", "timeline", "unknown"}:
        direction = "unknown"
    return DiagramSceneIR(
        elements=elements,
        relations=relations,
        groups=groups,
        reading_direction=direction,
        diagram_type_candidates=[diagram_type],
        coordinate_space="pixels",
    )


def _bbox(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, list | tuple) and len(value) == 4:
        try:
            bbox = tuple(float(item) for item in value)
        except (TypeError, ValueError):
            return (0.0, 0.0, 0.0, 0.0)
        if bbox[2] >= bbox[0] and bbox[3] >= bbox[1]:
            return bbox  # type: ignore[return-value]
    return (0.0, 0.0, 0.0, 0.0)
