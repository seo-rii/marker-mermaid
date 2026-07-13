"""Narrow semantic repairs backed by source text and connector evidence."""

from __future__ import annotations

import copy
import math
import unicodedata
from difflib import SequenceMatcher

from marker_mermaid.accessibility import (
    EXPERIMENTAL_NOTICE,
    enrich_accessibility_ir,
    resolve_accessibility,
)
from marker_mermaid.models import (
    MAX_SCENE_RELATIONS,
    MermaidCandidate,
    SceneRelation,
    VisualEvidence,
)
from marker_mermaid.protocols import RepairProposal, SourceContext
from marker_mermaid.serializers import serialize_typed_ir_result


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _supported_label_evidence(
    evidence_ids: list[str],
    text: str,
    evidence_by_id: dict[str, VisualEvidence],
    trusted_evidence_ids: set[str],
    source_bbox: tuple[float, float, float, float],
    source_block_ids: set[str],
) -> list[str]:
    normalized = _normalized(text)
    result: list[str] = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if (
            evidence is None
            or evidence_id not in trusted_evidence_ids
            or evidence.bbox is None
            or not evidence.text
            or _normalized(evidence.text) != normalized
            or not source_block_ids.intersection(evidence.source_block_ids)
        ):
            continue
        center_x = (evidence.bbox[0] + evidence.bbox[2]) / 2
        center_y = (evidence.bbox[1] + evidence.bbox[3]) / 2
        if not (
            source_bbox[0] <= center_x <= source_bbox[2]
            and source_bbox[1] <= center_y <= source_bbox[3]
        ):
            continue
        if evidence.kind == "vector_text" or (
            evidence.kind == "ocr_token" and evidence.score is not None and evidence.score >= 0.8
        ):
            result.append(evidence_id)
    return result


class EvidenceBackedLabelRepair:
    """Correct exact-ID flowchart labels only when OCR/vector evidence agrees."""

    name = "evidence_backed_label_repair"

    def repair(
        self,
        context: SourceContext,
        candidate: MermaidCandidate,
    ) -> RepairProposal | None:
        if (
            candidate.generation_method != "typed_ir"
            or candidate.diagram_type not in {"flowchart", "generic_network"}
            or candidate.typed_ir is None
            or candidate.scene_ir is None
            or any(
                event.operation == "recover_style" and event.accepted
                for event in candidate.repair_history
            )
        ):
            return None
        nodes = candidate.typed_ir.get("nodes")
        if not isinstance(nodes, list):
            return None
        source_by_id = {element.id: element for element in candidate.scene_ir.elements}
        evidence_by_id = {item.id: item for item in context.evidence}
        updated_ir = copy.deepcopy(candidate.typed_ir)
        updated_nodes = updated_ir.get("nodes")
        if not isinstance(updated_nodes, list):
            return None
        corrections: list[dict[str, object]] = []
        for node in updated_nodes:
            if not isinstance(node, dict) or node.get("id") is None:
                continue
            node_id = str(node["id"])
            source = source_by_id.get(node_id)
            if source is None or not source.text or not source.text.strip():
                continue
            before = str(node.get("label") or node.get("text") or "").strip()
            after = source.text.strip()
            if not before or _normalized(before) == _normalized(after):
                continue
            supporting_ids = _supported_label_evidence(
                source.evidence_ids,
                after,
                evidence_by_id,
                context.trusted_label_evidence_ids,
                source.bbox,
                set(context.source_block_ids),
            )
            if not supporting_ids:
                continue
            node["label"] = after
            if "text" in node:
                node["text"] = after
            node["evidence_ids"] = list(
                dict.fromkeys([*(node.get("evidence_ids") or []), *supporting_ids])
            )
            corrections.append(
                {
                    "node_id": node_id,
                    "before": before,
                    "after": after,
                    "evidence_ids": supporting_ids,
                }
            )
        if not corrections:
            return None

        experimental = EXPERIMENTAL_NOTICE in str(candidate.typed_ir.get("acc_description") or "")
        previous_accessibility = resolve_accessibility(
            {
                key: value
                for key, value in candidate.typed_ir.items()
                if key not in {"acc_title", "acc_description"}
            },
            candidate.diagram_type,
            experimental=experimental,
        )
        if candidate.typed_ir.get("acc_title") == previous_accessibility.title:
            updated_ir.pop("acc_title", None)
        if candidate.typed_ir.get("acc_description") == previous_accessibility.description:
            updated_ir.pop("acc_description", None)
        updated_ir = enrich_accessibility_ir(
            updated_ir,
            candidate.diagram_type,
            experimental=experimental,
        )
        serialized = serialize_typed_ir_result(
            candidate.diagram_type,
            updated_ir,
            experimental=experimental,
        )
        if serialized.emitted_type != candidate.emitted_diagram_type:
            return None
        return RepairProposal(
            code=serialized.code,
            operation="repair_evidence_backed_labels",
            typed_ir=updated_ir,
            details={"corrections": corrections},
        )


class EvidenceBackedFlowchartRepair:
    """Repair labels and unambiguous directed edges from strong source evidence."""

    name = "evidence_backed_flowchart_repair"

    def repair(
        self,
        context: SourceContext,
        candidate: MermaidCandidate,
    ) -> RepairProposal | None:
        if (
            candidate.generation_method != "typed_ir"
            or candidate.diagram_type not in {"flowchart", "generic_network"}
            or candidate.typed_ir is None
            or candidate.scene_ir is None
            or any(
                event.operation == "recover_style" and event.accepted
                for event in candidate.repair_history
            )
        ):
            return None

        label_proposal = EvidenceBackedLabelRepair().repair(context, candidate)
        updated_ir = copy.deepcopy(
            label_proposal.typed_ir if label_proposal is not None else candidate.typed_ir
        )
        nodes = updated_ir.get("nodes")
        edges = updated_ir.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            return label_proposal

        node_ids: list[str] = []
        for node in nodes:
            if not isinstance(node, dict) or node.get("id") is None:
                return label_proposal
            node_ids.append(str(node["id"]))
        if len(node_ids) != len(set(node_ids)):
            return label_proposal
        known_node_ids = set(node_ids)
        typed_nodes_by_id = {str(node["id"]): node for node in nodes}
        source_elements_by_id = {element.id: element for element in candidate.scene_ir.elements}

        edges_by_pair: dict[frozenset[str], list[tuple[str, str, dict[str, object]]]] = {}
        for edge in edges:
            if (
                not isinstance(edge, dict)
                or edge.get("source") is None
                or edge.get("target") is None
            ):
                return label_proposal
            source_id = str(edge["source"])
            target_id = str(edge["target"])
            if source_id not in known_node_ids or target_id not in known_node_ids:
                return label_proposal
            pair = frozenset({source_id, target_id})
            edges_by_pair.setdefault(pair, []).append((source_id, target_id, edge))

        evidence_by_id = {item.id: item for item in context.evidence}
        context_blocks = set(context.source_block_ids)
        trusted_relations_by_pair: dict[frozenset[str], list[tuple[str, str, frozenset[str]]]] = {}
        for source_id, target_id, evidence_ids in context.trusted_connector_relations:
            pair = frozenset({source_id, target_id})
            trusted_relations_by_pair.setdefault(pair, []).append(
                (source_id, target_id, evidence_ids)
            )
        relations_by_pair: dict[frozenset[str], list[SceneRelation]] = {}
        for relation in candidate.scene_ir.relations:
            if (
                relation.source_id is None
                or relation.target_id is None
                or relation.source_id == relation.target_id
                or relation.source_id not in known_node_ids
                or relation.target_id not in known_node_ids
                or relation.source_id not in source_elements_by_id
                or relation.target_id not in source_elements_by_id
            ):
                continue
            key = frozenset({relation.source_id, relation.target_id})
            relations_by_pair.setdefault(key, []).append(relation)

        relation_evidence_use_count: dict[str, int] = {}
        for relation in candidate.scene_ir.relations:
            for evidence_id in set(relation.evidence_ids):
                relation_evidence_use_count[evidence_id] = (
                    relation_evidence_use_count.get(evidence_id, 0) + 1
                )

        trusted_connector_support_by_pair: dict[
            frozenset[str], tuple[list[str], set[str], list[VisualEvidence]]
        ] = {}
        trusted_connector_segments_by_pair: dict[
            frozenset[str],
            list[tuple[tuple[float, float], tuple[float, float]]],
        ] = {}
        for pair, relations in relations_by_pair.items():
            if len(relations) != 1 or pair in context.conflicted_connector_pairs:
                continue
            relation = relations[0]
            relation_evidence = set(relation.evidence_ids)
            trusted_relations = trusted_relations_by_pair.get(pair, [])
            if len(trusted_relations) != 1:
                continue
            trusted_source, trusted_target, trusted_evidence_ids = trusted_relations[0]
            if (
                trusted_source != relation.source_id
                or trusted_target != relation.target_id
                or not trusted_evidence_ids.issubset(relation_evidence)
            ):
                continue
            line_evidence: list[VisualEvidence] = []
            arrow_evidence: list[VisualEvidence] = []
            for evidence_id in sorted(trusted_evidence_ids):
                evidence = evidence_by_id.get(evidence_id)
                if (
                    evidence is None
                    or evidence_id not in context.trusted_connector_evidence_ids
                    or evidence.bbox is None
                    or evidence.score is None
                    or evidence.score < 0.6
                    or not evidence.source_block_ids
                    or not context_blocks.intersection(evidence.source_block_ids)
                ):
                    continue
                if evidence.kind == "line_segment":
                    line_evidence.append(evidence)
                elif evidence.kind == "arrowhead":
                    arrow_evidence.append(evidence)
            line_blocks = {
                block_id
                for evidence in line_evidence
                for block_id in evidence.source_block_ids
                if block_id in context_blocks
            }
            arrow_blocks = {
                block_id
                for evidence in arrow_evidence
                for block_id in evidence.source_block_ids
                if block_id in context_blocks
            }
            shared_blocks = line_blocks.intersection(arrow_blocks)
            if not line_evidence or not arrow_evidence or not shared_blocks:
                continue
            shared_lines = [
                evidence
                for evidence in line_evidence
                if shared_blocks.intersection(evidence.source_block_ids)
            ]
            supporting_ids = [
                evidence.id
                for evidence in [*line_evidence, *arrow_evidence]
                if shared_blocks.intersection(evidence.source_block_ids)
            ]
            trusted_connector_support_by_pair[pair] = (
                supporting_ids,
                shared_blocks,
                shared_lines,
            )
            trusted_connector_segments_by_pair[pair] = [
                (start, end)
                for start, end in zip(
                    relation.polyline,
                    relation.polyline[1:],
                    strict=False,
                )
                if start != end
            ]

        edge_label_corrections: list[dict[str, object]] = []
        for pair, relations in relations_by_pair.items():
            if len(relations) != 1:
                continue
            relation = relations[0]
            relation_type = relation.relation_type.casefold()
            relation_type_tokens = set(
                relation_type.replace("-", "_").replace("/", "_").replace(" ", "_").split("_")
            )
            if relation.semantic_relation != "conditional" and (
                relation.semantic_relation != "unknown"
                or relation_type_tokens.intersection({"unconditional", "nonconditional"})
                or not relation_type_tokens.intersection(
                    {"branch", "conditional", "decision", "gateway"}
                )
            ):
                continue
            after = relation.label.strip() if relation.label else ""
            if (
                not after
                or relation.arrow_at_start
                or not relation.arrow_at_end
                or relation.confidence < 0.6
            ):
                continue
            connector_support = trusted_connector_support_by_pair.get(pair)
            if connector_support is None:
                continue
            connector_ids, connector_blocks, _ = connector_support
            pair_edges = edges_by_pair.get(pair, [])
            exact_edges = [
                edge
                for edge_source, edge_target, edge in pair_edges
                if edge_source == relation.source_id and edge_target == relation.target_id
            ]
            if len(pair_edges) != 1 or len(exact_edges) != 1:
                continue
            edge = exact_edges[0]
            if any(
                field in edge and not isinstance(edge[field], bool)
                for field in ("bidirectional", "arrow_at_start", "arrow_at_end")
            ):
                continue
            if (
                edge.get("bidirectional")
                or edge.get("arrow_at_start")
                or edge.get("arrow_at_end") is False
            ):
                continue
            typed_semantic = edge.get("semantic_relation")
            if typed_semantic is not None and not isinstance(typed_semantic, str):
                continue
            if typed_semantic not in {None, "", "unknown", "conditional"}:
                continue
            raw_before = edge.get("label")
            if raw_before is not None and not isinstance(raw_before, str):
                continue
            before = raw_before.strip() if isinstance(raw_before, str) else None
            before = before or None
            normalized_after = _normalized(after)
            if before:
                normalized_before = _normalized(before)
                if normalized_before == normalized_after:
                    continue
                if (
                    not normalized_before
                    or SequenceMatcher(
                        None,
                        normalized_before,
                        normalized_after,
                    ).ratio()
                    < 0.6
                ):
                    continue
            existing_evidence_ids = edge.get("evidence_ids", [])
            if not isinstance(existing_evidence_ids, list) or not all(
                isinstance(item, str) for item in existing_evidence_ids
            ):
                continue
            label_evidence_ids: list[str] = []
            for evidence_id in relation.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if (
                    evidence is None
                    or evidence_id not in context.trusted_label_evidence_ids
                    or relation_evidence_use_count.get(evidence_id) != 1
                    or evidence.bbox is None
                    or not evidence.text
                    or _normalized(evidence.text) != normalized_after
                    or not connector_blocks.intersection(evidence.source_block_ids)
                ):
                    continue
                if evidence.kind == "ocr_token":
                    if evidence.score is None or evidence.score < 0.8:
                        continue
                elif evidence.kind != "vector_text":
                    continue
                width = evidence.bbox[2] - evidence.bbox[0]
                height = evidence.bbox[3] - evidence.bbox[1]
                thickness = min(width, height)
                if thickness <= 0:
                    continue
                center = (
                    (evidence.bbox[0] + evidence.bbox[2]) / 2,
                    (evidence.bbox[1] + evidence.bbox[3]) / 2,
                )
                if any(
                    element.bbox[0] <= center[0] <= element.bbox[2]
                    and element.bbox[1] <= center[1] <= element.bbox[3]
                    for element in candidate.scene_ir.elements
                ):
                    continue
                max_distance = 2.0 * thickness
                spatially_supported_pairs: set[frozenset[str]] = set()
                for (
                    support_pair,
                    (_, support_blocks, support_lines),
                ) in trusted_connector_support_by_pair.items():
                    if not support_blocks.intersection(evidence.source_block_ids):
                        continue
                    if not any(
                        line.bbox is not None
                        and min(line.bbox[0], line.bbox[2]) - max_distance
                        <= center[0]
                        <= max(line.bbox[0], line.bbox[2]) + max_distance
                        and min(line.bbox[1], line.bbox[3]) - max_distance
                        <= center[1]
                        <= max(line.bbox[1], line.bbox[3]) + max_distance
                        for line in support_lines
                    ):
                        continue
                    support_segments = trusted_connector_segments_by_pair[support_pair]
                    segment_distances: list[float] = []
                    for start, end in support_segments:
                        dx = end[0] - start[0]
                        dy = end[1] - start[1]
                        length_squared = dx * dx + dy * dy
                        if length_squared == 0:
                            distance = math.hypot(
                                center[0] - start[0],
                                center[1] - start[1],
                            )
                        else:
                            ratio = max(
                                0.0,
                                min(
                                    1.0,
                                    ((center[0] - start[0]) * dx + (center[1] - start[1]) * dy)
                                    / length_squared,
                                ),
                            )
                            distance = math.hypot(
                                center[0] - (start[0] + ratio * dx),
                                center[1] - (start[1] + ratio * dy),
                            )
                        segment_distances.append(distance)
                    if segment_distances and min(segment_distances) <= max_distance:
                        spatially_supported_pairs.add(support_pair)
                if spatially_supported_pairs != {pair}:
                    continue
                label_evidence_ids.append(evidence_id)
            label_evidence_ids = list(dict.fromkeys(label_evidence_ids))
            if not label_evidence_ids:
                continue
            edge["label"] = after
            edge["evidence_ids"] = list(
                dict.fromkeys([*existing_evidence_ids, *label_evidence_ids, *connector_ids])
            )
            edge_label_corrections.append(
                {
                    "operation": "relabel_conditional_edge",
                    "edge_id": edge.get("id"),
                    "relation_id": relation.id,
                    "source": relation.source_id,
                    "target": relation.target_id,
                    "before": before,
                    "after": after,
                    "label_evidence_ids": label_evidence_ids,
                    "connector_evidence_ids": connector_ids,
                }
            )

        strong_relations: list[tuple[SceneRelation, list[str]]] = []
        for pair, relations in relations_by_pair.items():
            relation = relations[0] if len(relations) == 1 else None
            if relation is None:
                continue
            relation_type = relation.relation_type.casefold()
            source_element = source_elements_by_id[relation.source_id]
            typed_source = typed_nodes_by_id[relation.source_id]
            source_node_signals = {
                str(source_element.role or "").casefold(),
                str(source_element.shape or "").casefold(),
                str(typed_source.get("role") or "").casefold(),
                str(typed_source.get("shape") or "").casefold(),
                str(typed_source.get("type") or "").casefold(),
            }
            if (
                relation.arrow_at_start
                or not relation.arrow_at_end
                or relation.confidence < 0.6
                or relation.label is not None
                or relation.semantic_relation == "conditional"
                or any(
                    marker in relation_type
                    for marker in ("branch", "conditional", "decision", "gateway")
                )
                or any(
                    marker in signal
                    for signal in source_node_signals
                    for marker in ("decision", "diamond", "gateway")
                )
            ):
                continue
            connector_support = trusted_connector_support_by_pair.get(pair)
            if connector_support is None:
                continue
            supporting_ids, _, _ = connector_support
            strong_relations.append((relation, supporting_ids))

        structural_corrections: list[dict[str, object]] = []
        existing_edge_ids = {
            str(edge["id"])
            for pair_edges in edges_by_pair.values()
            for _, _, edge in pair_edges
            if edge.get("id") is not None
        }
        next_edge_suffix = 1
        while f"repair_edge_{next_edge_suffix}" in existing_edge_ids:
            next_edge_suffix += 1
        for relation, supporting_ids in strong_relations:
            source_id = relation.source_id
            target_id = relation.target_id
            assert source_id is not None and target_id is not None
            pair = frozenset({source_id, target_id})
            pair_edges = edges_by_pair.get(pair, [])
            exact = [
                edge
                for edge_source, edge_target, edge in pair_edges
                if edge_source == source_id and edge_target == target_id
            ]
            reversed_edges = [
                edge
                for edge_source, edge_target, edge in pair_edges
                if edge_source == target_id and edge_target == source_id
            ]
            if len(exact) == 1:
                continue
            if exact or len(reversed_edges) > 1:
                continue
            if len(reversed_edges) == 1:
                edge = reversed_edges[0]
                edge_type = str(edge.get("relation_type") or "").casefold()
                if (
                    edge.get("bidirectional")
                    or edge.get("arrow_at_start")
                    or edge.get("label") is not None
                    or edge.get("semantic_relation") == "conditional"
                    or any(
                        marker in edge_type
                        for marker in ("branch", "conditional", "decision", "gateway")
                    )
                ):
                    continue
                before = {"source": target_id, "target": source_id}
                edge["source"] = source_id
                edge["target"] = target_id
                edge["arrow_at_start"] = False
                edge["arrow_at_end"] = True
                edge["evidence_ids"] = list(
                    dict.fromkeys([*(edge.get("evidence_ids") or []), *supporting_ids])
                )
                structural_corrections.append(
                    {
                        "operation": "reverse_edge",
                        "before": before,
                        "after": {"source": source_id, "target": target_id},
                        "evidence_ids": supporting_ids,
                    }
                )
                continue
            if len(edges) >= MAX_SCENE_RELATIONS:
                continue
            edge_id = f"repair_edge_{next_edge_suffix}"
            next_edge_suffix += 1
            while f"repair_edge_{next_edge_suffix}" in existing_edge_ids:
                next_edge_suffix += 1
            existing_edge_ids.add(edge_id)
            added_edge: dict[str, object] = {
                "id": edge_id,
                "source": source_id,
                "target": target_id,
                "relation_type": relation.relation_type,
                "semantic_relation": relation.semantic_relation,
                "arrow_at_start": False,
                "arrow_at_end": True,
                "evidence_ids": supporting_ids,
            }
            edges.append(added_edge)
            edges_by_pair.setdefault(pair, []).append((source_id, target_id, added_edge))
            structural_corrections.append(
                {
                    "operation": "add_edge",
                    "after": {"source": source_id, "target": target_id, "id": edge_id},
                    "evidence_ids": supporting_ids,
                }
            )

        if not structural_corrections and not edge_label_corrections:
            return label_proposal

        experimental = EXPERIMENTAL_NOTICE in str(candidate.typed_ir.get("acc_description") or "")
        previous_accessibility = resolve_accessibility(
            {
                key: value
                for key, value in candidate.typed_ir.items()
                if key not in {"acc_title", "acc_description"}
            },
            candidate.diagram_type,
            experimental=experimental,
        )
        if candidate.typed_ir.get("acc_title") == previous_accessibility.title:
            updated_ir.pop("acc_title", None)
        if candidate.typed_ir.get("acc_description") == previous_accessibility.description:
            updated_ir.pop("acc_description", None)
        updated_ir = enrich_accessibility_ir(
            updated_ir,
            candidate.diagram_type,
            experimental=experimental,
        )
        serialized = serialize_typed_ir_result(
            candidate.diagram_type,
            updated_ir,
            experimental=experimental,
        )
        if serialized.emitted_type != candidate.emitted_diagram_type:
            return label_proposal
        label_corrections = (
            label_proposal.details.get("corrections", []) if label_proposal is not None else []
        )
        return RepairProposal(
            code=serialized.code,
            operation="repair_evidence_backed_flowchart",
            typed_ir=updated_ir,
            details={
                "label_corrections": label_corrections,
                "edge_label_corrections": edge_label_corrections,
                "structural_corrections": structural_corrections,
            },
        )
