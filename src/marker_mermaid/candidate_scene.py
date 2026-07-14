"""Reconstruct the structural scene actually emitted by typed serializers.

This adapter is deliberately narrower than a Mermaid parser.  It covers typed IR
families whose serializers have deterministic node/edge semantics and returns
``None`` for unsupported data rather than guessing from raw Mermaid text.  Layout
coordinates are retained only when the IR explicitly carries a bbox; otherwise
nodes use a shared origin so layout scoring remains unavailable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

from marker_mermaid.flowchart_structure import (
    FlowchartStructureError,
    FlowchartStructurePlan,
    MindmapStructureError,
    SequenceStructureError,
    plan_flowchart_structure,
    plan_mindmap_nodes,
    plan_sequence_structure,
    prepare_swimlane_structure,
)
from marker_mermaid.models import DiagramSceneIR, SceneElement, SceneGroup, SceneRelation
from marker_mermaid.serializers import plan_architecture_structure
from marker_mermaid.serializers_experimental import plan_wardley_records, plan_zenuml_records
from marker_mermaid.serializers_phase2 import (
    REQUIREMENT_TYPE_TOKENS,
    plan_c4_architecture_fallback,
    plan_phase2_record_ids,
    plan_requirement_records,
    plan_usecase_records,
)
from marker_mermaid.serializers_special import (
    plan_eventmodeling_frames,
    plan_eventmodeling_relations,
)


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
        node_records = [
            {
                **node,
                "label": node.get("label") or node.get("text") or "[unreadable]",
            }
            if isinstance(node, dict)
            else node
            for node in ir.get("nodes") or []
        ]
        edge_records = list(ir.get("edges") or [])
        group_records = list(ir.get("groups") or [])
    elif diagram_type in {"swimlane", "bpmn"}:
        try:
            swimlane_structure = prepare_swimlane_structure(ir.get("lanes"))
        except FlowchartStructureError:
            return None
        node_records = [
            {
                **node,
                "label": node.get("label") or node.get("text") or "[unreadable]",
            }
            for node in swimlane_structure.nodes
        ]
        edge_records = list(ir.get("edges") or [])
        group_records = list(swimlane_structure.groups)
    elif diagram_type in {"architecture", "c4", "deployment", "component"}:
        architecture_ir = ir
        if diagram_type == "c4":
            try:
                architecture_structure = plan_c4_architecture_fallback(ir).structure
            except ValueError:
                return None
        else:
            if diagram_type in {"deployment", "component"}:
                if diagram_type == "deployment":
                    source_records = [*(ir.get("nodes") or []), *(ir.get("artifacts") or [])]
                    raw_edges = ir.get("links", ir.get("edges", []))
                else:
                    source_records = [*(ir.get("components") or []), *(ir.get("interfaces") or [])]
                    raw_edges = ir.get("dependencies", ir.get("edges", []))
                if not isinstance(raw_edges, list):
                    return None
                try:
                    records, id_map = plan_phase2_record_ids(
                        source_records,
                        field=f"{diagram_type} IR",
                        fallback_prefix="S",
                    )
                except ValueError:
                    return None
                services = [
                    {
                        **record,
                        "id": output_id,
                        "label": record.get("label") or record.get("name") or source_id,
                    }
                    for record, source_id, output_id in records
                ]
                architecture_edges = [
                    {
                        **edge,
                        "source": id_map.get(str(edge.get("source"))),
                        "target": id_map.get(str(edge.get("target"))),
                    }
                    for edge in raw_edges
                    if isinstance(edge, dict)
                ]
                architecture_ir = {
                    **ir,
                    "services": services,
                    "edges": architecture_edges,
                }
            try:
                architecture_structure = plan_architecture_structure(architecture_ir)
            except ValueError:
                return None
        node_records = [
            {
                "id": placement.emitted_id,
                "label": service.get("label") or service.get("name") or placement.source_id,
                "role": "node",
                "shape": None,
                "bbox": service.get("bbox"),
                "evidence_ids": list(service.get("evidence_ids") or []),
            }
            for service, placement in zip(
                architecture_structure.services,
                architecture_structure.nodes,
                strict=True,
            )
        ]
        emitted_node_by_source = {
            placement.source_id: placement.emitted_id for placement in architecture_structure.nodes
        }
        edge_records = [
            {
                "source": emitted_node_by_source[str(edge["source"])],
                "target": emitted_node_by_source[str(edge["target"])],
                "bidirectional": bool(edge.get("bidirectional")),
                "evidence_ids": list(edge.get("evidence_ids") or []),
            }
            for edge in architecture_structure.edges
        ]
        group_records = [
            {
                "id": placement.emitted_id,
                "label": placement.label,
                "member_ids": list(placement.member_emitted_ids),
                "bbox": group.get("bbox"),
            }
            for group, placement in zip(
                architecture_structure.groups,
                architecture_structure.group_placements,
                strict=True,
            )
        ]
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
    elif diagram_type == "usecase":
        try:
            actors, use_cases, id_map = plan_usecase_records(ir)
        except ValueError:
            return None
        node_records = [
            {
                "id": output_id,
                "label": record.get("label") or record.get("name") or source_id,
                "role": "node",
                "shape": "stadium",
                "bbox": record.get("bbox"),
                "evidence_ids": list(record.get("evidence_ids") or []),
            }
            for record, source_id, output_id in actors
        ]
        node_records.extend(
            {
                "id": output_id,
                "label": record.get("label") or record.get("name") or source_id,
                "role": "node",
                "shape": "round",
                "bbox": record.get("bbox"),
                "evidence_ids": list(record.get("evidence_ids") or []),
            }
            for record, source_id, output_id in use_cases
        )
        edge_records = [
            {
                "id": edge.get("id"),
                "source": id_map.get(str(edge.get("source"))),
                "target": id_map.get(str(edge.get("target"))),
                "label": edge.get("type") or edge.get("label"),
                "evidence_ids": list(edge.get("evidence_ids") or []),
            }
            for edge in ir.get("relations") or []
            if isinstance(edge, dict)
        ]
    elif diagram_type == "sankey":
        node_records = list(ir.get("nodes") or [])
        edge_records = list(ir.get("flows") or ir.get("links") or [])
    elif diagram_type == "zenuml":
        participants, messages = plan_zenuml_records(ir)
        source_participants = [
            participant
            for participant in ir.get("participants") or []
            if isinstance(participant, (Mapping, str))
        ]
        node_records = [
            {
                **(source if isinstance(source, Mapping) else {}),
                "id": participant["id"],
                "label": participant["label"],
            }
            for source, participant in zip(source_participants, participants, strict=True)
        ]
        source_messages = [
            message for message in ir.get("messages") or [] if isinstance(message, Mapping)
        ]
        edge_records = [
            {**source, **message} for source, message in zip(source_messages, messages, strict=True)
        ]
    elif diagram_type == "sequence":
        try:
            structure = plan_sequence_structure(
                ir.get("participants"),
                ir.get("messages", []),
            )
        except SequenceStructureError:
            return None
        source_participants = list(ir.get("participants") or [])
        node_records = [
            {
                **(source if isinstance(source, dict) else {}),
                "id": participant.emitted_id,
                "label": participant.label,
            }
            for source, participant in zip(
                source_participants,
                structure.participants,
                strict=True,
            )
        ]
        edge_records = [
            {
                **message.source,
                "id": message.emitted_id,
                "source": message.source_id,
                "target": message.target_id,
            }
            for message in structure.messages
        ]
    elif diagram_type == "mindmap":
        try:
            node_plan = plan_mindmap_nodes(ir.get("root"))
        except MindmapStructureError:
            return None
        node_records = [
            {**node.source, "id": node.emitted_id, "label": node.label} for node in node_plan
        ]
        edge_records = [
            {
                "source": node.parent_id,
                "target": node.emitted_id,
                "semantic_relation": "containment",
                "evidence_ids": list(node.source.get("evidence_ids") or []),
            }
            for node in node_plan
            if node.parent_id is not None
        ]
    elif diagram_type in {"treemap", "treeview", "organization"} and isinstance(
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
        lanes, frames, frame_map = plan_eventmodeling_frames(ir)
        planned_relations = plan_eventmodeling_relations(ir, frame_map)
        source_frames = [
            frame
            for lane in ir.get("lanes") or []
            if isinstance(lane, dict)
            for frame in lane.get("frames") or []
            if isinstance(frame, dict)
        ]
        node_records = [
            {
                **source,
                "id": frame["output_id"],
                "label": frame["semantic_label"],
            }
            for source, frame in zip(source_frames, frames, strict=True)
        ]
        source_relations = [
            relation for relation in ir.get("relations") or [] if isinstance(relation, dict)
        ]
        edge_records = [
            {
                **source,
                "source": relation["source"],
                "target": relation["target"],
                "label": relation["semantic_label"],
            }
            for source, relation in zip(source_relations, planned_relations, strict=True)
        ]
        source_lanes = [lane for lane in ir.get("lanes") or [] if isinstance(lane, dict)]
        group_records = [
            {
                **source,
                "id": lane["output_id"],
                "label": lane["semantic_label"],
                "role": "lane",
                "member_ids": lane["frame_ids"],
            }
            for source, lane in zip(source_lanes, lanes, strict=True)
        ]
    elif diagram_type == "wardley":
        _title, components, links = plan_wardley_records(ir)
        source_components = [
            component for component in ir.get("components") or [] if isinstance(component, Mapping)
        ]
        node_records = [
            {**source, "id": component["id"], "label": component["label"]}
            for source, component in zip(source_components, components, strict=True)
        ]
        source_links = [link for link in ir.get("links") or [] if isinstance(link, Mapping)]
        edge_records = [
            {
                **source,
                "source": link["source"],
                "target": link["target"],
                "label": link["label"],
            }
            for source, link in zip(source_links, links, strict=True)
        ]
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

    if diagram_type in {
        "flowchart",
        "generic_network",
        "swimlane",
        "bpmn",
        "eventmodeling",
    }:
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
        if diagram_type in {"architecture", "c4", "deployment", "component", "usecase"}:
            semantic_relation = "unknown"
            relation_type = "generated_connector"
        else:
            semantic_relation = str(edge.get("semantic_relation") or "unknown")
            if semantic_relation not in semantic_relations:
                semantic_relation = "unknown"
            relation_type = str(edge.get("relation_type") or "generated_connector")
        if diagram_type in {
            "flowchart",
            "generic_network",
            "swimlane",
            "bpmn",
            "architecture",
            "c4",
            "deployment",
            "component",
            "usecase",
        }:
            arrow_at_start = bool(edge.get("bidirectional"))
            arrow_at_end = True
        elif diagram_type in {"sequence", "zenuml"}:
            arrow_at_start = False
            arrow_at_end = True
        else:
            arrow_at_start = bool(edge.get("bidirectional") or edge.get("arrow_at_start"))
            arrow_at_end = bool(edge.get("arrow_at_end", diagram_type not in {"class", "er"}))
        if diagram_type in {"architecture", "c4", "deployment", "component"}:
            relation_label = None
        elif diagram_type == "usecase":
            visible_label = edge.get("type") or edge.get("label")
            relation_label = str(visible_label) if visible_label is not None else None
        else:
            relation_label = None if edge.get("label") is None else str(edge.get("label"))
        relations.append(
            SceneRelation(
                id=(
                    f"generated-relation-{index}"
                    if diagram_type in {"architecture", "c4", "deployment", "component", "usecase"}
                    else str(edge.get("id") or f"generated-relation-{index}")
                ),
                source_id=source,
                target_id=target,
                relation_type=relation_type,
                semantic_relation=semantic_relation,
                label=relation_label,
                arrow_at_start=arrow_at_start,
                arrow_at_end=arrow_at_end,
                line_style=(
                    None
                    if diagram_type in {"architecture", "c4", "deployment", "component", "usecase"}
                    else str(edge.get("style"))
                    if edge.get("style")
                    else None
                ),
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
    if diagram_type in {"architecture", "c4", "deployment", "component"}:
        for group_record in group_records:
            group_id = str(group_record["id"])
            member_ids = [str(member_id) for member_id in group_record["member_ids"]]
            explicit_bbox = group_record.get("bbox")
            if isinstance(explicit_bbox, list | tuple) and len(explicit_bbox) == 4:
                bbox = _bbox(explicit_bbox)
            elif member_ids:
                member_boxes = [elements_by_id[member_id].bbox for member_id in member_ids]
                bbox = (
                    min(item[0] for item in member_boxes),
                    min(item[1] for item in member_boxes),
                    max(item[2] for item in member_boxes),
                    max(item[3] for item in member_boxes),
                )
            else:
                bbox = (0.0, 0.0, 0.0, 0.0)
            groups.append(
                SceneGroup(
                    id=group_id,
                    role="group",
                    label=str(group_record["label"]),
                    bbox=bbox,
                    member_ids=member_ids,
                )
            )
            known_group_ids.add(group_id)
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
    if diagram_type == "eventmodeling":
        direction = "LR"
    elif diagram_type == "usecase":
        direction = ir.get("direction", "LR")
        if direction not in {"TB", "BT", "LR", "RL"}:
            direction = "TB"
    else:
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


def typed_ir_semantic_texts(
    diagram_type: str,
    ir: dict[str, Any],
    scene: DiagramSceneIR,
) -> Iterator[str]:
    """Project serializer-visible typed IR text without Mermaid identifiers.

    Structural scenes deliberately omit record details such as class members and ER
    attributes. OCR scoring needs those rendered labels while topology and textual
    projection remain separate concerns.
    """

    if diagram_type == "timeline":
        if ir.get("title"):
            yield str(ir["title"])
        for event in ir.get("events") or []:
            if not isinstance(event, dict):
                continue
            period = event.get("time") or event.get("period") or "[unreadable time]"
            yield str(period)
            labels = event.get("events") or [event.get("label") or "[unreadable]"]
            if isinstance(labels, list):
                for label in labels:
                    if label is not None and label != "":
                        yield str(label)
        return

    if diagram_type == "class":
        for class_item in ir.get("classes") or []:
            if not isinstance(class_item, dict):
                continue
            source_id = class_item.get("id")
            label = class_item.get("label") or source_id
            if label is not None and label != "":
                yield str(label)
            for member in class_item.get("members") or []:
                if not isinstance(member, dict):
                    continue
                name = member.get("name")
                if name is not None and name != "":
                    yield str(name)
                if member.get("kind", "field") == "method":
                    parameters = member.get("parameters")
                    if isinstance(parameters, list):
                        for value in parameters:
                            if value is not None and value != "":
                                yield str(value)
                    return_type = member.get("return_type") or member.get("type")
                    if return_type is not None and return_type != "":
                        yield str(return_type)
                else:
                    type_name = member.get("type")
                    if type_name is not None and type_name != "":
                        yield str(type_name)
        for relation in ir.get("relations") or []:
            if not isinstance(relation, dict):
                continue
            label = relation.get("label")
            if label is not None and label != "":
                yield str(label)
            for field in ("source_cardinality", "target_cardinality"):
                value = relation.get(field)
                if value is not None and value != "":
                    yield str(value)
        return
    if diagram_type == "er":
        for entity in ir.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            source_id = entity.get("id")
            label = entity.get("label") or source_id
            if label is not None and label != "":
                yield str(label)
            for attribute in entity.get("attributes") or []:
                if not isinstance(attribute, dict):
                    continue
                for field in ("type", "name", "comment"):
                    value = attribute.get(field)
                    if value is not None and value != "":
                        yield str(value)
                keys = attribute.get("keys")
                if isinstance(keys, list):
                    for value in keys:
                        if value is not None and value != "":
                            yield str(value)
        for relationship in ir.get("relationships") or []:
            if not isinstance(relationship, dict):
                continue
            label = relationship.get("label")
            if label is not None and label != "":
                yield str(label)
        return
    if diagram_type == "gantt":
        if ir.get("title"):
            yield str(ir["title"])
        for section in ir.get("sections") or []:
            if not isinstance(section, dict):
                continue
            yield str(section.get("title") or "Tasks")
            for task_index, task in enumerate(section.get("tasks") or [], start=1):
                if isinstance(task, dict):
                    yield str(task.get("label") or f"Task {task_index}")
        return
    if diagram_type == "c4":
        for group in scene.groups:
            if group.label:
                yield group.label
        for element in scene.elements:
            if element.text:
                yield element.text
        return
    if diagram_type == "requirement":
        requirements, elements, _id_map = plan_requirement_records(ir)
        for record, source_id, output_id in requirements:
            source_type = str(record.get("type") or "requirement").lower()
            type_token = REQUIREMENT_TYPE_TOKENS.get(source_type, "requirement")
            display_type = re.sub(r"(?<!^)(?=[A-Z])", " ", type_token).title()
            yield output_id
            yield display_type
            yield str(record.get("requirement_id") or source_id)
            yield str(record.get("text") or record.get("label"))
            yield str(record.get("risk") or "medium")
            yield str(record.get("verify_method") or record.get("verifymethod") or "analysis")
        for record, source_id, output_id in elements:
            yield output_id
            yield str(record.get("type") or record.get("label") or "element")
            yield str(record.get("docref") or source_id)
        for relation in ir.get("relations") or []:
            if isinstance(relation, dict):
                yield str(relation.get("type") or "traces")
        return
    if diagram_type == "eventmodeling":
        lanes, frames, frame_map = plan_eventmodeling_frames(ir)
        relations = plan_eventmodeling_relations(ir, frame_map)
        frames_by_id = {frame["output_id"]: frame for frame in frames}
        for lane in lanes:
            yield str(lane["semantic_label"])
            for frame_id in lane["frame_ids"]:
                yield str(frames_by_id[frame_id]["semantic_label"])
        for relation in relations:
            if relation["semantic_label"] is not None:
                yield str(relation["semantic_label"])
        return
    if diagram_type == "wardley":
        title, components, links = plan_wardley_records(ir)
        if title is not None:
            yield title
        for component in components:
            yield component["label"]
        for link in links:
            if link["label"] is not None:
                yield str(link["label"])
        return
    if diagram_type == "zenuml":
        participants, messages = plan_zenuml_records(ir)
        for participant in participants:
            yield participant["label"]
        for message in messages:
            yield message["label"]
        return

    for element in scene.elements:
        if element.text:
            yield element.text
    for relation in scene.relations:
        if relation.label:
            yield relation.label
    for group in scene.groups:
        if group.label:
            yield group.label


def _bbox(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, list | tuple) and len(value) == 4:
        try:
            bbox = tuple(float(item) for item in value)
        except (TypeError, ValueError):
            return (0.0, 0.0, 0.0, 0.0)
        if bbox[2] >= bbox[0] and bbox[3] >= bbox[1]:
            return bbox  # type: ignore[return-value]
    return (0.0, 0.0, 0.0, 0.0)
