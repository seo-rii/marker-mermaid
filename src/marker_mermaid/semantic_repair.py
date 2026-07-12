"""Narrow, evidence-backed semantic repairs that cannot change topology."""

from __future__ import annotations

import copy
import unicodedata

from marker_mermaid.accessibility import (
    EXPERIMENTAL_NOTICE,
    enrich_accessibility_ir,
    resolve_accessibility,
)
from marker_mermaid.models import MermaidCandidate, VisualEvidence
from marker_mermaid.protocols import RepairProposal, SourceContext
from marker_mermaid.serializers import serialize_typed_ir_result


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _supported_label_evidence(
    evidence_ids: list[str],
    text: str,
    evidence_by_id: dict[str, VisualEvidence],
) -> list[str]:
    normalized = _normalized(text)
    result: list[str] = []
    for evidence_id in evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or not evidence.text or _normalized(evidence.text) != normalized:
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
