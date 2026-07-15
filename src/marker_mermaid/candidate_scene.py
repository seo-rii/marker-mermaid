"""Reconstruct the structural scene actually emitted by typed serializers.

This adapter is deliberately narrower than a Mermaid parser.  It covers typed IR
families whose serializers have deterministic node/edge semantics and returns
``None`` for unsupported data rather than guessing from raw Mermaid text.  Layout
coordinates are retained when the IR explicitly carries a bbox or the serializer
grammar has an explicit position such as Wardley; otherwise nodes use a shared
origin so layout scoring remains unavailable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
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
from marker_mermaid.serializers import (
    SerializationError,
    plan_architecture_structure,
    plan_gantt_records,
)
from marker_mermaid.serializers_charts_flow import plan_radar_records, plan_sankey_records
from marker_mermaid.serializers_charts_sets import plan_treemap_records, plan_venn_records
from marker_mermaid.serializers_experimental import (
    CYNEFIN_DOMAIN_LABELS,
    CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS,
    plan_cynefin_records,
    plan_cynefin_runtime_items,
    plan_data_lineage_records,
    plan_organization_hierarchy,
    plan_railroad_records,
    plan_wardley_records,
    plan_zenuml_structure,
)
from marker_mermaid.serializers_phase2 import (
    REQUIREMENT_TYPE_TOKENS,
    plan_c4_architecture_fallback,
    plan_phase2_record_ids,
    plan_requirement_records,
    plan_usecase_fallback,
)
from marker_mermaid.serializers_planning import plan_gitgraph_records, plan_kanban_records
from marker_mermaid.serializers_special import (
    packet_native_title_text,
    plan_eventmodeling_records,
    plan_ishikawa_hierarchy,
    plan_packet_fields,
    plan_treeview_hierarchy,
)
from marker_mermaid.serializers_uml import plan_state_records


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


def _planned_hierarchy_records(
    records: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [
        {
            **record.source_record,
            "id": record.emitted_id,
            "label": record.label,
        }
        for record in records
    ]
    edges = [
        {
            "source": record.parent_emitted_id,
            "target": record.emitted_id,
            "semantic_relation": "containment",
            "evidence_ids": list(record.source_record.get("evidence_ids") or []),
        }
        for record in records
        if record.parent_emitted_id is not None
    ]
    return nodes, edges


def typed_ir_to_scene(
    diagram_type: str,
    ir: dict[str, Any],
    *,
    emitted_diagram_type: str | None = None,
) -> DiagramSceneIR | None:
    """Convert deterministic typed IR into the terminal grammar's visible scene."""

    node_records: list[dict[str, Any]] = []
    edge_records: list[dict[str, Any]] = []
    group_records: list[dict[str, Any]] = []
    flowchart_structure: FlowchartStructurePlan | None = None
    scene_direction_override: str | None = None
    coordinate_space = "pixels"
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
        try:
            state_plan = plan_state_records(ir)
        except SerializationError:
            return None
        node_records = [
            {
                **state.source_record,
                "id": state.emitted_id,
                "label": state.visible_label,
                "kind": state.kind,
            }
            for state in state_plan.nodes
        ]
        edge_records = [
            {
                **transition.source_record,
                "id": transition.emitted_id,
                "source": transition.source_id,
                "target": transition.target_id,
                "label": transition.visible_label,
            }
            for transition in state_plan.transitions
            if transition.source_id != "[*]" and transition.target_id != "[*]"
        ]
        scene_direction_override = state_plan.direction
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
        try:
            block_records, block_id_map = plan_phase2_record_ids(
                ir.get("blocks"), field="block IR", fallback_prefix="B"
            )
        except SerializationError:
            return None
        node_records = [
            {
                **record,
                "id": output_id,
                "label": record.get("label") or record.get("text") or "[unreadable]",
            }
            for record, _source_id, output_id in block_records
        ]
        raw_block_edges = ir.get("edges", [])
        if not isinstance(raw_block_edges, list):
            return None
        for edge in raw_block_edges:
            if not isinstance(edge, dict):
                return None
            source = block_id_map.get(str(edge.get("source") or ""))
            target = block_id_map.get(str(edge.get("target") or ""))
            if source is None or target is None:
                return None
            edge_records.append({**edge, "source": source, "target": target})
    elif diagram_type == "usecase":
        try:
            usecase_plan = plan_usecase_fallback(ir)
        except ValueError:
            return None
        node_records = list(usecase_plan.nodes)
        edge_records = list(usecase_plan.edges)
        group_records = []
    elif diagram_type == "packet":
        try:
            packet_plan = plan_packet_fields(ir)
        except SerializationError:
            return None
        node_records = [
            {
                **field.source_record,
                "id": field.emitted_id,
                "label": field.label,
                "role": "field",
            }
            for field in packet_plan.fields
        ]
        scene_direction_override = "LR"
    elif diagram_type == "radar":
        try:
            radar_plan = plan_radar_records(ir)
        except SerializationError:
            return None
        radar_uses_flowchart = (
            not radar_plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if radar_uses_flowchart:
            if not radar_plan.flowchart_supported:
                return None
            elements = [
                SceneElement(
                    id=point.scene_id,
                    role="data_point",
                    text=point.fallback_canvas_label,
                    bbox=(0.0, 0.0, 0.0, 0.0),
                    shape="rectangle",
                    confidence=1.0,
                    evidence_ids=list(point.evidence_ids),
                )
                for series in radar_plan.series
                for point in series.points
            ]
            groups = [
                SceneGroup(
                    id=series.emitted_id,
                    role="series",
                    label=series.fallback_canvas_label,
                    bbox=(0.0, 0.0, 0.0, 0.0),
                    member_ids=[point.scene_id for point in series.points],
                )
                for series in radar_plan.series
            ]
            return DiagramSceneIR(
                elements=elements,
                relations=[],
                groups=groups,
                reading_direction="TB",
                diagram_type_candidates=["radar"],
                coordinate_space="pixels",
            )
        if not radar_plan.native_supported:
            return None
        elements = [
            SceneElement(
                id=dimension.emitted_id,
                role="axis",
                text=dimension.native_canvas_label,
                bbox=(*dimension.normalized_point, *dimension.normalized_point),
                shape=None,
                confidence=1.0,
                evidence_ids=list(dimension.evidence_ids),
            )
            for dimension in radar_plan.dimensions
        ]
        series_bboxes: list[tuple[float, float, float, float]] = []
        for series in radar_plan.series:
            positions: list[tuple[float, float]] = []
            for point in series.points:
                if point.normalized_point is None:
                    return None
                positions.append(point.normalized_point)
            x_positions = [position[0] for position in positions]
            y_positions = [position[1] for position in positions]
            series_bboxes.append(
                (
                    min(x_positions),
                    min(y_positions),
                    max(x_positions),
                    max(y_positions),
                )
            )
        elements.extend(
            SceneElement(
                id=series.emitted_id,
                role="series",
                text=series.native_canvas_label if radar_plan.show_legend else None,
                bbox=bbox,
                shape=None,
                confidence=1.0,
                evidence_ids=list(series.evidence_ids),
            )
            for series, bbox in zip(radar_plan.series, series_bboxes, strict=True)
        )
        for series in radar_plan.series:
            for point in series.points:
                if point.normalized_point is None:
                    return None
                elements.append(
                    SceneElement(
                        id=point.scene_id,
                        role="data_point",
                        text=None,
                        bbox=(*point.normalized_point, *point.normalized_point),
                        shape=None,
                        confidence=1.0,
                        evidence_ids=list(point.evidence_ids),
                    )
                )
        relations = []
        for series_index, series in enumerate(radar_plan.series, start=1):
            for point_index, point in enumerate(series.points, start=1):
                next_point = series.points[point_index % len(series.points)]
                relations.append(
                    SceneRelation(
                        id=f"radar_curve_{series_index}_{point_index}",
                        source_id=point.scene_id,
                        target_id=next_point.scene_id,
                        relation_type="series_curve",
                        semantic_relation="association",
                        label=None,
                        polyline=[],
                        arrow_at_start=False,
                        arrow_at_end=False,
                        line_style=None,
                        confidence=1.0,
                        evidence_ids=list(series.evidence_ids),
                    )
                )
        return DiagramSceneIR(
            elements=elements,
            relations=relations,
            groups=[],
            reading_direction="radial",
            diagram_type_candidates=["radar"],
            coordinate_space="normalized",
        )
    elif diagram_type == "sankey":
        try:
            sankey_plan = plan_sankey_records(ir)
        except SerializationError:
            return None
        sankey_uses_flowchart = (
            not sankey_plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if not sankey_uses_flowchart and any(
            node.native_total_text is None for node in sankey_plan.nodes
        ):
            return None
        if sankey_uses_flowchart and not sankey_plan.flowchart_supported:
            return None
        node_records = [
            {
                "id": node.fallback_id if sankey_uses_flowchart else node.source_id,
                "label": node.label,
                "role": "node",
                "bbox": node.source_record.get("bbox"),
                "evidence_ids": list(node.evidence_ids),
            }
            for node in sankey_plan.nodes
        ]
        edge_records = [
            {
                "id": flow.scene_id,
                "source": (flow.source_fallback_id if sankey_uses_flowchart else flow.source_id),
                "target": (flow.target_fallback_id if sankey_uses_flowchart else flow.target_id),
                "label": flow.value_text if sankey_uses_flowchart else None,
                "semantic_relation": "data_flow",
                "arrow_at_start": False,
                "arrow_at_end": sankey_uses_flowchart,
                "evidence_ids": list(flow.evidence_ids),
            }
            for flow in sankey_plan.flows
        ]
        scene_direction_override = sankey_plan.fallback_direction if sankey_uses_flowchart else "LR"
    elif diagram_type == "zenuml":
        try:
            zenuml_plan = plan_zenuml_structure(ir)
        except SerializationError:
            return None
        node_records = [
            {
                "id": participant.emitted_id,
                "label": participant.label,
                "role": "participant",
                "evidence_ids": list(
                    participant.source_record.get("evidence_ids") or []
                    if participant.source_record is not None
                    else []
                ),
            }
            for participant in zenuml_plan.participants
        ]
        edge_records = [
            {
                "id": message.emitted_id,
                "source": message.source_emitted_id,
                "target": message.target_emitted_id,
                "label": message.label,
                "relation_type": "message",
                "semantic_relation": "message",
                "arrow_at_start": False,
                "arrow_at_end": True,
                "evidence_ids": list(message.source_record.get("evidence_ids") or []),
            }
            for message in zenuml_plan.messages
        ]
        scene_direction_override = "LR"
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
                "label": message.source.get("label") or "[unreadable]",
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
    elif diagram_type == "treemap" and isinstance(ir.get("root"), dict):
        try:
            treemap_plan = plan_treemap_records(ir)
        except SerializationError:
            return None
        treemap_uses_flowchart = (
            not treemap_plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if treemap_uses_flowchart and not treemap_plan.flowchart_supported:
            return None
        node_records = [
            {
                **node.source_record,
                "id": node.fallback_id if treemap_uses_flowchart else node.scene_id,
                "label": (
                    node.fallback_canvas_label
                    + (f" (value: {node.value_text})" if node.value_text is not None else "")
                    if treemap_uses_flowchart
                    else node.native_canvas_label
                ),
                "role": "node" if treemap_uses_flowchart else "leaf" if node.is_leaf else "section",
                "shape": "rectangle" if treemap_uses_flowchart else None,
                "bbox": [0, 0, 0, 0],
                "evidence_ids": list(node.evidence_ids),
            }
            for node in treemap_plan.nodes
        ]
        edge_records = [
            {
                "id": relation.scene_id,
                "source": (
                    relation.source_fallback_id
                    if treemap_uses_flowchart
                    else relation.source_scene_id
                ),
                "target": (
                    relation.target_fallback_id
                    if treemap_uses_flowchart
                    else relation.target_scene_id
                ),
                "relation_type": (
                    "generated_connector" if treemap_uses_flowchart else "logical_containment"
                ),
                "semantic_relation": "containment",
                "arrow_at_start": False,
                "arrow_at_end": treemap_uses_flowchart,
                "evidence_ids": list(relation.evidence_ids),
            }
            for relation in treemap_plan.relations
        ]
        scene_direction_override = "TB" if treemap_uses_flowchart else "unknown"
    elif diagram_type == "treeview":
        try:
            treeview_plan = plan_treeview_hierarchy(ir)
        except SerializationError:
            return None
        node_records, edge_records = _planned_hierarchy_records(treeview_plan)
    elif diagram_type == "organization":
        try:
            organization_plan = plan_organization_hierarchy(ir)
        except SerializationError:
            return None
        if emitted_diagram_type is None:
            organization_uses_flowchart = any(
                any(character in node.semantic_label for character in '"\\')
                for node in organization_plan.nodes
            )
        else:
            organization_uses_flowchart = emitted_diagram_type.casefold().startswith("flowchart")
        node_records = [
            {
                "id": node.emitted_id,
                "label": node.label,
                "role": "node",
                "shape": "rectangle" if organization_uses_flowchart else None,
                "evidence_ids": list(node.source_record.get("evidence_ids") or []),
            }
            for node in organization_plan.nodes
        ]
        edge_records = [
            {
                "id": relation.emitted_id,
                "source": relation.source_emitted_id,
                "target": relation.target_emitted_id,
                "relation_type": "generated_connector",
                "semantic_relation": "containment",
                "arrow_at_start": False,
                "arrow_at_end": organization_uses_flowchart,
                "evidence_ids": list(relation.source_record.get("evidence_ids") or []),
            }
            for relation in organization_plan.relations
        ]
        scene_direction_override = organization_plan.direction
    elif diagram_type == "ishikawa":
        try:
            ishikawa_plan = plan_ishikawa_hierarchy(ir)
        except SerializationError:
            return None
        node_records, edge_records = _planned_hierarchy_records(ishikawa_plan)
    elif diagram_type == "timeline":
        node_records = _ordered_records(ir.get("events"), prefix="event_")
    elif diagram_type == "gantt":
        try:
            gantt_plan = plan_gantt_records(ir)
        except SerializationError:
            return None
        for section in gantt_plan.sections:
            member_ids = []
            for task in section.tasks:
                node_records.append(
                    {
                        **task.source_record,
                        "id": task.scene_id,
                        "label": task.visible_label,
                    }
                )
                member_ids.append(task.scene_id)
            group_records.append(
                {
                    **section.source_record,
                    "id": section.scene_id,
                    "label": section.visible_label,
                    "member_ids": member_ids,
                }
            )
    elif diagram_type == "journey":
        for section_index, section in enumerate(ir.get("sections") or [], start=1):
            if not isinstance(section, dict):
                continue
            member_ids: list[str] = []
            for task_index, task in enumerate(section.get("tasks") or [], start=1):
                if isinstance(task, dict):
                    task_id = str(task.get("id") or f"section_{section_index}_task_{task_index}")
                    node_records.append(
                        {
                            **task,
                            "id": task_id,
                            "label": task.get("label") or task.get("text"),
                        }
                    )
                    member_ids.append(task_id)
            group_records.append(
                {
                    **section,
                    "id": f"journey_section_{section_index}",
                    "label": section.get("title") or section.get("label"),
                    "role": "section",
                    "member_ids": member_ids,
                }
            )
    elif diagram_type == "kanban":
        try:
            kanban_plan = plan_kanban_records(ir)
        except ValueError:
            return None
        node_records = [
            {
                **column.source_record,
                "id": column.emitted_id,
                "label": column.label,
                "role": "column",
            }
            for column in kanban_plan.columns
        ]
        node_records.extend(
            {
                **card.source_record,
                "id": card.emitted_id,
                "label": card.label,
                "role": "card",
            }
            for card in kanban_plan.cards
        )
        edge_records = [
            {
                "source": card.column_emitted_id,
                "target": card.emitted_id,
                "semantic_relation": "containment",
                "evidence_ids": list(card.source_record.get("evidence_ids") or []),
            }
            for card in kanban_plan.cards
        ]
        scene_direction_override = "LR"
    elif diagram_type == "gitgraph":
        try:
            gitgraph_plan = plan_gitgraph_records(ir)
        except ValueError:
            return None
        node_records = [
            {
                **commit.source_record,
                "id": commit.element_id,
                "label": commit.semantic_id,
                "role": "commit",
            }
            for commit in gitgraph_plan.commits
        ]
        relation_index = 0
        for commit in gitgraph_plan.commits:
            for parent_id in commit.parent_element_ids:
                relation_index += 1
                edge_records.append(
                    {
                        "id": f"git_relation_{relation_index}",
                        "source": parent_id,
                        "target": commit.element_id,
                        "semantic_relation": "sequence",
                        "arrow_at_end": False,
                        "evidence_ids": list(commit.source_record.get("evidence_ids") or []),
                    }
                )
        group_records = [
            {
                **(branch.source_record or {}),
                "id": f"git_branch_{index}",
                "label": branch.source_id,
                "role": "branch",
                "member_ids": list(branch.member_element_ids),
            }
            for index, branch in enumerate(gitgraph_plan.branches, start=1)
            if branch.member_element_ids
        ]
        scene_direction_override = gitgraph_plan.direction or "LR"
    elif diagram_type == "eventmodeling":
        try:
            eventmodeling_plan = plan_eventmodeling_records(ir)
        except SerializationError:
            return None
        node_records = [
            {
                "id": frame.emitted_id,
                "label": frame.rendered_label,
                "role": "node",
                "evidence_ids": list(frame.source_record.get("evidence_ids") or []),
            }
            for frame in eventmodeling_plan.frames
        ]
        edge_records = [
            {
                "id": relation.emitted_id,
                "source": relation.source_emitted_id,
                "target": relation.target_emitted_id,
                "label": relation.label,
                "relation_type": "generated_connector",
                "semantic_relation": "sequence",
                "arrow_at_start": False,
                "arrow_at_end": True,
                "evidence_ids": list(relation.source_record.get("evidence_ids") or []),
            }
            for relation in eventmodeling_plan.relations
        ]
        group_records = [
            {
                "id": lane.emitted_id,
                "label": lane.label,
                "role": "lane",
                "member_ids": [frame.emitted_id for frame in lane.frames],
            }
            for lane in eventmodeling_plan.lanes
        ]
        scene_direction_override = "LR"
    elif diagram_type == "railroad":
        try:
            railroad_plan = plan_railroad_records(ir)
            for record in (
                *(rule.source_record for rule in railroad_plan.rules),
                *(expression.source_record for expression in railroad_plan.expressions),
            ):
                evidence_ids = record.get("evidence_ids")
                if evidence_ids is not None and (
                    not isinstance(evidence_ids, list)
                    or any(not isinstance(evidence_id, str) for evidence_id in evidence_ids)
                ):
                    raise SerializationError("railroad Scene evidence_ids must be a list or null")
            elements = [
                SceneElement(
                    id=rule.emitted_id,
                    role="rule",
                    text=rule.label,
                    bbox=(0.0, 0.0, 0.0, 0.0),
                    shape=None,
                    confidence=1.0,
                    evidence_ids=list(rule.source_record.get("evidence_ids") or []),
                )
                for rule in railroad_plan.rules
            ]
            expression_shapes = {
                "terminal": "round",
                "nonterminal": "rectangle",
                "special": "rectangle",
            }
            elements.extend(
                SceneElement(
                    id=expression.emitted_id,
                    role=expression.kind,
                    text=expression.label,
                    bbox=(0.0, 0.0, 0.0, 0.0),
                    shape=expression_shapes.get(expression.kind),
                    confidence=1.0,
                    evidence_ids=list(expression.source_record.get("evidence_ids") or []),
                )
                for expression in railroad_plan.expressions
            )
            element_ids = {element.id for element in elements}
            relations = [
                SceneRelation(
                    id=relation.emitted_id,
                    source_id=relation.source_emitted_id,
                    target_id=relation.target_emitted_id,
                    relation_type="generated_connector",
                    semantic_relation="containment",
                    label=None,
                    polyline=[],
                    arrow_at_start=False,
                    arrow_at_end=False,
                    line_style=None,
                    confidence=1.0,
                    evidence_ids=list(relation.source_record.get("evidence_ids") or []),
                )
                for relation in railroad_plan.relations
                if relation.semantic_relation == "containment"
                and relation.source_emitted_id in element_ids
                and relation.target_emitted_id in element_ids
            ]
            if len(relations) != len(railroad_plan.relations):
                return None
            return DiagramSceneIR(
                elements=elements,
                relations=relations,
                groups=[],
                reading_direction="LR",
                diagram_type_candidates=["railroad"],
                coordinate_space="pixels",
            )
        except (SerializationError, ValueError):
            return None
    elif diagram_type == "wardley":
        try:
            wardley_plan = plan_wardley_records(ir)
        except SerializationError:
            return None
        wardley_uses_flowchart = bool(
            emitted_diagram_type is not None
            and emitted_diagram_type.casefold().startswith("flowchart")
        )
        node_records = [
            {
                "id": component.emitted_id if wardley_uses_flowchart else component.source_id,
                "label": (component.fallback_label if wardley_uses_flowchart else component.label),
                "role": "node" if wardley_uses_flowchart else component.kind,
                "shape": "rectangle" if wardley_uses_flowchart else None,
                "bbox": (
                    (0.0, 0.0, 0.0, 0.0)
                    if wardley_uses_flowchart
                    else (component.x, 1 - component.y, component.x, 1 - component.y)
                ),
                "evidence_ids": list(component.source_record.get("evidence_ids") or []),
            }
            for component in wardley_plan.components
        ]
        edge_records = [
            {
                "id": link.emitted_id,
                "source": (link.source_emitted_id if wardley_uses_flowchart else link.source_id),
                "target": (link.target_emitted_id if wardley_uses_flowchart else link.target_id),
                "label": (
                    link.fallback_label
                    if wardley_uses_flowchart and link.fallback_label is not None
                    else link.label
                ),
                "arrow_at_start": False,
                "arrow_at_end": False,
                "evidence_ids": list(link.source_record.get("evidence_ids") or []),
            }
            for link in wardley_plan.links
        ]
        coordinate_space = "pixels" if wardley_uses_flowchart else "normalized"
        scene_direction_override = "LR" if wardley_uses_flowchart else "unknown"
    elif diagram_type == "cynefin":
        try:
            cynefin_plan = plan_cynefin_records(ir)
        except SerializationError:
            return None
        cynefin_uses_flowchart = (
            emitted_diagram_type is not None
            and emitted_diagram_type.casefold().startswith("flowchart")
        )
        if cynefin_uses_flowchart:
            for domain in cynefin_plan.domains:
                node_records.append(
                    {
                        "id": domain.emitted_id,
                        "label": CYNEFIN_DOMAIN_LABELS[domain.name],
                        "role": "domain",
                        "bbox": (0.0, 0.0, 0.0, 0.0),
                        "evidence_ids": list(domain.source_record.get("evidence_ids") or []),
                    }
                )
                member_ids: list[str] = []
                for item in domain.items:
                    source_record = item.source_record if item.source_record is not None else {}
                    node_records.append(
                        {
                            "id": item.emitted_id,
                            "label": item.fallback_label,
                            "role": "item",
                            "shape": "rectangle",
                            "bbox": (0.0, 0.0, 0.0, 0.0),
                            "evidence_ids": list(source_record.get("evidence_ids") or []),
                        }
                    )
                    member_ids.append(item.emitted_id)
                group_records.append(
                    {
                        "id": domain.emitted_id,
                        "label": CYNEFIN_DOMAIN_LABELS[domain.name],
                        "role": "domain",
                        "member_ids": member_ids,
                        "bbox": (0.0, 0.0, 0.0, 0.0),
                    }
                )
            scene_direction_override = "LR"
        else:
            explicit_domains = {domain.emitted_id: domain for domain in cynefin_plan.domains}
            runtime_items = plan_cynefin_runtime_items(cynefin_plan)
            node_records.extend(
                {
                    "id": emitted_id,
                    "label": label,
                    "role": role,
                    "bbox": (0.0, 0.0, 0.0, 0.0),
                    "evidence_ids": list(
                        explicit_domains[emitted_id].source_record.get("evidence_ids") or []
                    )
                    if emitted_id in explicit_domains
                    else [],
                }
                for emitted_id, role, label in CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS
            )
            domain_labels = {
                emitted_id: label
                for emitted_id, role, label in CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS
                if role == "domain"
            }
            for domain in cynefin_plan.domains:
                member_ids = []
                for item in runtime_items[domain.emitted_id]:
                    source_record = item.source_record if item.source_record is not None else {}
                    node_records.append(
                        {
                            "id": item.emitted_id,
                            "label": item.label,
                            "role": "runtime_template" if item.implicit else "item",
                            "bbox": (0.0, 0.0, 0.0, 0.0),
                            "evidence_ids": list(source_record.get("evidence_ids") or []),
                        }
                    )
                    member_ids.append(item.emitted_id)
                group_records.append(
                    {
                        "id": domain.group_id,
                        "label": domain_labels[domain.emitted_id],
                        "role": "domain",
                        "member_ids": member_ids,
                        "bbox": (0.0, 0.0, 0.0, 0.0),
                    }
                )
        edge_records = [
            {
                "id": transition.emitted_id,
                "source": transition.source_emitted_id,
                "target": transition.target_emitted_id,
                "label": (
                    transition.fallback_label if cynefin_uses_flowchart else transition.label
                ),
                "arrow_at_start": False,
                "arrow_at_end": True,
                "evidence_ids": list(transition.source_record.get("evidence_ids") or []),
            }
            for transition in cynefin_plan.transitions
        ]
        if not cynefin_uses_flowchart:
            scene_direction_override = "unknown"
    elif diagram_type == "data_lineage":
        try:
            data_lineage_plan = plan_data_lineage_records(ir)
        except SerializationError:
            return None
        node_records = [
            {
                "id": node.emitted_id,
                "label": node.label,
                "role": node.kind,
                "shape": node.shape,
                "evidence_ids": list(node.source_record.get("evidence_ids") or []),
            }
            for node in data_lineage_plan.nodes
        ]
        edge_records = [
            {
                "id": relation.emitted_id,
                "source": relation.source_emitted_id,
                "target": relation.target_emitted_id,
                "label": relation.label,
                "relation_type": "generated_connector",
                "semantic_relation": "data_flow",
                "arrow_at_start": False,
                "arrow_at_end": True,
                "evidence_ids": list(relation.source_record.get("evidence_ids") or []),
            }
            for relation in data_lineage_plan.relations
        ]
        scene_direction_override = data_lineage_plan.direction
    elif diagram_type == "venn":
        try:
            venn_plan = plan_venn_records(ir)
        except SerializationError:
            return None
        venn_uses_flowchart = (
            not venn_plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if venn_uses_flowchart and not venn_plan.flowchart_supported:
            return None
        node_records = [
            {
                "id": item.emitted_id,
                "label": (
                    item.fallback_canvas_label
                    + (f" (value: {item.value_text})" if item.value_text is not None else "")
                    if venn_uses_flowchart
                    else item.native_canvas_label
                ),
                "role": "set",
                "shape": "circle",
                "bbox": [0, 0, 0, 0],
                "evidence_ids": list(item.evidence_ids),
                "_text_is_explicit": True,
            }
            for item in venn_plan.sets
        ]
        node_records.extend(
            {
                "id": item.scene_id,
                "label": (
                    item.fallback_canvas_label
                    + (f" (value: {item.value_text})" if item.value_text is not None else "")
                    if venn_uses_flowchart
                    else item.native_canvas_label
                ),
                "role": "intersection",
                "shape": "round" if venn_uses_flowchart else None,
                "bbox": [0, 0, 0, 0],
                "evidence_ids": list(item.evidence_ids),
                "_text_is_explicit": True,
            }
            for item in venn_plan.intersections
        )
        edge_records = [
            {
                "id": membership.scene_id,
                "source": membership.source_emitted_id,
                "target": membership.target_scene_id,
                "label": "intersects" if venn_uses_flowchart else None,
                "relation_type": (
                    "generated_connector" if venn_uses_flowchart else "logical_membership"
                ),
                "semantic_relation": "containment",
                "arrow_at_start": False,
                "arrow_at_end": venn_uses_flowchart,
                "evidence_ids": list(membership.evidence_ids),
            }
            for membership in venn_plan.memberships
        ]
        scene_direction_override = "LR" if venn_uses_flowchart else "unknown"
    else:
        return None

    if diagram_type == "journey":
        attribution_ids = [
            str(node.get("id") or f"N{index}")
            for index, node in enumerate(node_records, start=1)
            if isinstance(node, dict)
        ]
        if len(attribution_ids) != len(set(attribution_ids)):
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
        if diagram_type == "state":
            scene_text = (
                node.get("label") or node_id
                if str(node.get("kind") or "state").lower() == "state"
                else None
            )
        elif diagram_type == "venn" and node.get("_text_is_explicit"):
            scene_text = node.get("label")
        else:
            scene_text = node.get("label") or node.get("text") or node_id
        elements.append(
            SceneElement(
                id=node_id,
                role=str(node.get("role") or "node"),
                text=None if scene_text is None else str(scene_text),
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
    if diagram_type in {"gantt", "journey"}:
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
                    label=str(
                        group_record.get("label")
                        or ("Tasks" if diagram_type == "gantt" else "[unreadable section]")
                    ),
                    bbox=bbox,
                    member_ids=member_ids,
                )
            )
            known_group_ids.add(group_id)
    if diagram_type == "gitgraph":
        for group_record in group_records:
            group_id = str(group_record["id"])
            member_ids = [
                str(member_id)
                for member_id in group_record.get("member_ids") or []
                if str(member_id) in known_ids
            ]
            if not member_ids or group_id in known_group_ids:
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
                    role="branch",
                    label=str(group_record["label"]),
                    bbox=bbox,
                    member_ids=member_ids,
                )
            )
            known_group_ids.add(group_id)
    if diagram_type == "cynefin":
        for group_record in group_records:
            group_id = str(group_record["id"])
            member_ids = [str(member_id) for member_id in group_record["member_ids"]]
            if (
                group_id in known_group_ids
                or not member_ids
                or any(member_id not in known_ids for member_id in member_ids)
            ):
                return None
            groups.append(
                SceneGroup(
                    id=group_id,
                    role="domain",
                    label=str(group_record["label"]),
                    bbox=(0.0, 0.0, 0.0, 0.0),
                    member_ids=member_ids,
                )
            )
            known_group_ids.add(group_id)
    if diagram_type == "eventmodeling":
        direction = "LR"
    elif diagram_type == "journey":
        direction = "timeline"
    elif diagram_type == "usecase":
        direction = ir.get("direction", "LR")
        if direction not in {"TB", "BT", "LR", "RL"}:
            direction = "TB"
    elif scene_direction_override is not None:
        direction = scene_direction_override
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
        coordinate_space=coordinate_space,
    )


def typed_ir_semantic_texts(
    diagram_type: str,
    ir: dict[str, Any],
    scene: DiagramSceneIR,
    *,
    emitted_diagram_type: str | None = None,
) -> Iterator[str]:
    """Project terminal-serializer-visible typed IR text without Mermaid identifiers.

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

    if diagram_type == "state":
        plan = plan_state_records(ir)
        for state in plan.nodes:
            if state.kind == "state":
                yield state.visible_label
        for transition in plan.transitions:
            if transition.visible_label:
                yield transition.visible_label
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
        plan = plan_gantt_records(ir)
        for section in plan.sections:
            yield section.visible_label
            for task in section.tasks:
                yield task.visible_label
        return
    if diagram_type == "journey":
        if ir.get("title"):
            yield str(ir["title"])
        for section in ir.get("sections") or []:
            if not isinstance(section, dict):
                continue
            section_title = section.get("title") or section.get("label")
            if section_title is not None and section_title != "":
                yield str(section_title)
            for task in section.get("tasks") or []:
                if not isinstance(task, dict):
                    continue
                task_label = task.get("label") or task.get("text")
                if task_label is not None and task_label != "":
                    yield str(task_label)
                score = task.get("score")
                if score is not None:
                    yield f"Score {score}"
                actors = task.get("actors")
                if isinstance(actors, list) and actors:
                    yield "Actors " + ", ".join(str(actor) for actor in actors)
        return
    if diagram_type == "gitgraph":
        plan = plan_gitgraph_records(ir)
        for branch in plan.branches:
            yield branch.source_id
        for commit in plan.commits:
            if commit.semantic_id is not None:
                yield commit.semantic_id
            if commit.tag is not None:
                yield commit.tag
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
        plan = plan_eventmodeling_records(ir)
        for lane in plan.lanes:
            yield lane.label
            for frame in lane.frames:
                yield frame.rendered_label
        for relation in plan.relations:
            if relation.label is not None:
                yield relation.label
        return
    if diagram_type == "packet":
        plan = plan_packet_fields(ir)
        terminal_type = (emitted_diagram_type or diagram_type).casefold()
        if terminal_type.startswith("packet"):
            native_title = packet_native_title_text(ir)
            if native_title is not None:
                yield native_title
        for field in plan.fields:
            yield field.label
        return
    if diagram_type == "radar":
        plan = plan_radar_records(ir)
        radar_uses_flowchart = (
            not plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if radar_uses_flowchart:
            if not plan.flowchart_supported:
                raise SerializationError(
                    "Radar Flowchart projection exceeds the runtime point limit"
                )
            for series in plan.series:
                yield series.fallback_canvas_label
                for point in series.points:
                    yield point.fallback_canvas_label
        else:
            if not plan.native_supported:
                raise SerializationError("native Radar projection is not lossless")
            if plan.native_canvas_title is not None:
                yield plan.native_canvas_title
            for dimension in plan.dimensions:
                yield dimension.native_canvas_label
            if plan.show_legend:
                for series in plan.series:
                    yield series.native_canvas_label
        return
    if diagram_type == "venn":
        plan = plan_venn_records(ir)
        venn_uses_flowchart = (
            not plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if venn_uses_flowchart:
            if not plan.flowchart_supported:
                raise SerializationError("Venn Flowchart projection exceeds the runtime edge limit")
            for item in plan.sets:
                label = item.fallback_canvas_label
                if item.value_text is not None:
                    label += f" (value: {item.value_text})"
                yield label
            for item in plan.intersections:
                label = item.fallback_canvas_label
                if item.value_text is not None:
                    label += f" (value: {item.value_text})"
                yield label
            for _membership in plan.memberships:
                yield "intersects"
        else:
            if plan.native_canvas_title is not None:
                yield plan.native_canvas_title
            for item in plan.sets:
                yield item.native_canvas_label
            for item in plan.intersections:
                if item.native_canvas_label is not None:
                    yield item.native_canvas_label
        return
    if diagram_type == "treemap":
        plan = plan_treemap_records(ir)
        treemap_uses_flowchart = (
            not plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if treemap_uses_flowchart:
            if not plan.flowchart_supported:
                raise SerializationError(
                    "Treemap Flowchart projection exceeds the runtime edge limit"
                )
            for node in plan.nodes:
                label = node.fallback_canvas_label
                if node.value_text is not None:
                    label += f" (value: {node.value_text})"
                yield label
        else:
            if plan.native_canvas_title is not None:
                yield plan.native_canvas_title
            for node in plan.nodes:
                if node.native_total_text is None:
                    raise SerializationError("native Treemap total cannot be reproduced safely")
                yield node.native_canvas_label
                yield node.native_total_text
        return
    if diagram_type == "sankey":
        plan = plan_sankey_records(ir)
        sankey_uses_flowchart = (
            not plan.native_supported
            if emitted_diagram_type is None
            else emitted_diagram_type.casefold().startswith("flowchart")
        )
        if sankey_uses_flowchart:
            if not plan.flowchart_supported:
                raise SerializationError(
                    "Sankey Flowchart projection exceeds the runtime edge limit"
                )
            for node in plan.nodes:
                yield node.label
            for flow in plan.flows:
                yield flow.value_text
        else:
            for node in plan.nodes:
                if node.native_total_text is None:
                    raise SerializationError("native Sankey node total cannot be reproduced safely")
                yield node.label
                yield node.native_total_text
        return
    if diagram_type == "railroad":
        plan = plan_railroad_records(ir)
        for rule in plan.rules:
            yield rule.label
        for expression in plan.expressions:
            if expression.label is not None:
                yield expression.label
        return
    if diagram_type == "wardley":
        plan = plan_wardley_records(ir)
        wardley_uses_flowchart = scene.coordinate_space == "pixels"
        if plan.title is not None and not wardley_uses_flowchart:
            yield plan.title
        if wardley_uses_flowchart:
            yield from (element.text for element in scene.elements if element.text is not None)
            yield from (
                relation.label for relation in scene.relations if relation.label is not None
            )
        else:
            for component in plan.components:
                yield component.label
            for link in plan.links:
                if link.label is not None:
                    yield link.label
        return
    if diagram_type == "cynefin":
        plan = plan_cynefin_records(ir)
        cynefin_uses_flowchart = not any(
            element.role == "runtime_template" for element in scene.elements
        )
        if cynefin_uses_flowchart:
            for domain in plan.domains:
                yield CYNEFIN_DOMAIN_LABELS[domain.name]
                for item in domain.items:
                    yield item.fallback_label
            for transition in plan.transitions:
                if transition.fallback_label is not None:
                    yield transition.fallback_label
            return
        runtime_items = plan_cynefin_runtime_items(plan)
        for _emitted_id, _role, label in CYNEFIN_RUNTIME_TEMPLATE_ELEMENTS:
            yield label
        for domain in plan.domains:
            for item in runtime_items[domain.emitted_id]:
                yield item.label
        for transition in plan.transitions:
            if transition.label is not None:
                yield transition.label
        return
    if diagram_type == "zenuml":
        plan = plan_zenuml_structure(ir)
        for participant in plan.participants:
            yield participant.label
        for message in plan.messages:
            yield message.label
        return
    if diagram_type == "organization":
        plan = plan_organization_hierarchy(ir)
        for node in plan.nodes:
            yield node.label
        return
    if diagram_type == "data_lineage":
        plan = plan_data_lineage_records(ir)
        for node in plan.nodes:
            yield node.label
        for relation in plan.relations:
            if relation.label is not None:
                yield relation.label
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
