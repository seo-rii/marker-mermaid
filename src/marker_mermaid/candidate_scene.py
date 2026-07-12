"""Reconstruct the structural scene actually emitted by typed serializers.

This adapter is deliberately narrower than a Mermaid parser.  It covers typed IR
families whose serializers have deterministic node/edge semantics and returns
``None`` for unsupported data rather than guessing from raw Mermaid text.  Layout
coordinates are retained only when the IR explicitly carries a bbox; otherwise
nodes use a shared origin so layout scoring remains unavailable.
"""

from __future__ import annotations

from typing import Any

from marker_mermaid.models import DiagramSceneIR, SceneElement, SceneRelation


def typed_ir_to_scene(diagram_type: str, ir: dict[str, Any]) -> DiagramSceneIR | None:
    """Convert deterministic typed-IR node and relation fields into a scene."""

    node_records: list[dict[str, Any]] = []
    edge_records: list[dict[str, Any]] = []
    if diagram_type in {"flowchart", "generic_network"}:
        node_records = list(ir.get("nodes") or [])
        edge_records = list(ir.get("edges") or [])
    elif diagram_type in {"swimlane", "bpmn"}:
        node_records = [
            node
            for lane in ir.get("lanes") or []
            if isinstance(lane, dict)
            for node in lane.get("nodes") or []
        ]
        edge_records = list(ir.get("edges") or [])
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
    elif diagram_type == "sequence":
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
    elif diagram_type in {"mindmap", "treemap"} and isinstance(ir.get("root"), dict):
        pending = [(ir["root"], None, "root")]
        while pending:
            node, parent_id, fallback_id = pending.pop(0)
            node_id = str(node.get("id") or fallback_id)
            node_records.append({**node, "id": node_id})
            if parent_id is not None:
                edge_records.append({"source": parent_id, "target": node_id})
            for index, child in enumerate(node.get("children") or [], start=1):
                if isinstance(child, dict):
                    pending.append((child, node_id, f"{node_id}_{index}"))
    else:
        return None

    elements: list[SceneElement] = []
    known_ids: set[str] = set()
    for index, node in enumerate(node_records, start=1):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or f"N{index}")
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
    for index, edge in enumerate(edge_records, start=1):
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
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
    direction = ir.get("direction", "unknown")
    if direction not in {"TB", "BT", "LR", "RL", "radial", "timeline", "unknown"}:
        direction = "unknown"
    return DiagramSceneIR(
        elements=elements,
        relations=relations,
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
