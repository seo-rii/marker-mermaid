"""Atomic, traversal-safe sidecar bundle writer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from marker_mermaid.mapping_validation import (
    authority_evidence_matches,
    bbox_iou,
    normalize_scene_bbox,
    source_evidence_matches,
)
from marker_mermaid.models import (
    NODE_ID_MAPPING_MIN_IOU,
    MermaidCandidate,
    ReconstructionResult,
    VisualEvidence,
)

SCHEMA_VERSION = "mmx-sidecar-0.4"


def _safe_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-").lower()
    if not component or component in {".", ".."}:
        raise ValueError(f"unsafe sidecar component: {value!r}")
    return component


def safe_artifact_component(value: str) -> str:
    """Return the canonical filesystem component used by sidecar artifacts."""

    return _safe_component(value)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def _candidate_json(candidate: MermaidCandidate) -> dict[str, Any]:
    return candidate.model_dump(mode="json", exclude={"svg", "png"})


def _write(path: Path, data: bytes | str) -> str:
    payload = data.encode() if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


class SidecarStore:
    def __init__(
        self,
        output_root: str | Path,
        *,
        write_ir: bool = True,
        write_svg: bool = True,
        write_png: bool = True,
        write_alternatives: bool = True,
        write_provenance: bool = True,
    ):
        self.output_root = Path(output_root).resolve()
        self.write_ir = write_ir
        self.write_svg = write_svg
        self.write_png = write_png
        self.write_alternatives = write_alternatives
        self.write_provenance = write_provenance

    def write(self, result: ReconstructionResult) -> str:
        name = _safe_component(result.source_id)
        relative = PurePosixPath("diagrams") / name
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("sidecar path must remain inside the output root")
        diagrams = self.output_root / "diagrams"
        diagrams.mkdir(parents=True, exist_ok=True)
        target = diagrams / name
        if target.exists():
            raise FileExistsError(f"sidecar bundle already exists: {target}")
        temporary = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=diagrams))
        hashes: dict[str, str] = {}
        mapping_requires_provenance = False
        try:
            selected = result.selected
            if selected is not None:
                if selected.node_id_mappings and not selected._has_valid_node_id_mapping_seal():
                    raise ValueError(
                        "node id mappings must retain their trusted pipeline certification seal"
                    )
                selected = MermaidCandidate.model_validate(selected.model_dump(mode="python"))
                validated_evidence = [
                    VisualEvidence.model_validate(item.model_dump(mode="python"))
                    for item in result.evidence
                ]
                if selected.mermaid_code:
                    hashes["final.mmd"] = _write(temporary / "final.mmd", selected.mermaid_code)
                if self.write_svg and selected.svg:
                    hashes["final.svg"] = _write(temporary / "final.svg", selected.svg)
                if self.write_png and selected.png:
                    hashes["final.png"] = _write(temporary / "final.png", selected.png)
                if self.write_ir and selected.scene_ir is not None:
                    hashes["scene-ir.json"] = _write(
                        temporary / "scene-ir.json",
                        _json_bytes(selected.scene_ir.model_dump(mode="json")),
                    )
                if self.write_ir and selected.generated_scene_ir is not None:
                    hashes["generated-scene-ir.json"] = _write(
                        temporary / "generated-scene-ir.json",
                        _json_bytes(selected.generated_scene_ir.model_dump(mode="json")),
                    )
                if self.write_ir and selected.typed_ir is not None:
                    hashes["typed-ir.json"] = _write(
                        temporary / "typed-ir.json", _json_bytes(selected.typed_ir)
                    )
                if selected.node_id_mappings:
                    mapping_requires_provenance = True
                    evidence_counts = Counter(item.id for item in validated_evidence)
                    evidence_by_id = {item.id: item for item in validated_evidence}
                    mapping_evidence_references = [
                        evidence_id
                        for mapping in selected.node_id_mappings
                        for evidence_id in (
                            *mapping.source_evidence_ids,
                            *mapping.authority_evidence_ids,
                        )
                    ]
                    mapping_reference_counts = Counter(mapping_evidence_references)
                    mapping_evidence_ids = set(mapping_evidence_references)
                    invalid_evidence_ids = sorted(
                        evidence_id
                        for evidence_id in mapping_evidence_ids
                        if evidence_counts[evidence_id] != 1
                        or mapping_reference_counts[evidence_id] != 1
                    )
                    if invalid_evidence_ids:
                        raise ValueError(
                            "node id mapping evidence must occur exactly once in provenance: "
                            f"{invalid_evidence_ids}"
                        )
                    assert selected.scene_ir is not None
                    scene_elements = {element.id: element for element in selected.scene_ir.elements}
                    for mapping in selected.node_id_mappings:
                        source_evidence = [
                            evidence_by_id[evidence_id]
                            for evidence_id in mapping.source_evidence_ids
                        ]
                        authority_evidence = [
                            evidence_by_id[evidence_id]
                            for evidence_id in mapping.authority_evidence_ids
                        ]
                        has_source_text_evidence = any(
                            item.kind in {"ocr_token", "vector_text"} for item in source_evidence
                        )
                        fused_bbox = normalize_scene_bbox(
                            scene_elements[mapping.fused_id].bbox,
                            selected.scene_ir,
                        )
                        fused_evidence_ids = set(scene_elements[mapping.fused_id].evidence_ids)
                        if (
                            (mapping.source_text is not None) != has_source_text_evidence
                            or not all(
                                source_evidence_matches(
                                    item,
                                    selected.scene_ir,
                                    mapping.source_bbox,
                                    mapping.source_text,
                                )
                                for item in source_evidence
                            )
                            or not all(
                                authority_evidence_matches(
                                    item,
                                    selected.scene_ir,
                                    mapping.authority_bbox,
                                    NODE_ID_MAPPING_MIN_IOU,
                                )
                                for item in authority_evidence
                            )
                            or (
                                fused_bbox is None
                                or bbox_iou(fused_bbox, mapping.authority_bbox)
                                < NODE_ID_MAPPING_MIN_IOU
                            )
                            or not set(
                                (
                                    *mapping.source_evidence_ids,
                                    *mapping.authority_evidence_ids,
                                )
                            ).issubset(fused_evidence_ids)
                        ):
                            raise ValueError(
                                "node id mapping evidence must remain spatially/text aligned "
                                f"with source and authority boxes: {mapping.source_id!r}"
                            )
                        source_blocks = {
                            block_id
                            for item in source_evidence
                            for block_id in item.source_block_ids
                        }
                        authority_blocks = {
                            block_id
                            for item in authority_evidence
                            for block_id in item.source_block_ids
                        }
                        shared_blocks = source_blocks.intersection(authority_blocks)
                        result_blocks = set(result.source_block_ids)
                        if (
                            not source_blocks
                            or not authority_blocks
                            or not shared_blocks
                            or not result_blocks
                            or shared_blocks.isdisjoint(result_blocks)
                        ):
                            raise ValueError(
                                "node id mapping source and authority evidence must share "
                                "a source block"
                            )
                    hashes["node-id-map.json"] = _write(
                        temporary / "node-id-map.json",
                        _json_bytes(
                            [item.model_dump(mode="json") for item in selected.node_id_mappings]
                        ),
                    )
                hashes["scores.json"] = _write(
                    temporary / "scores.json",
                    _json_bytes(
                        {
                            "aggregate_score": selected.aggregate_score,
                            "grade": result.grade,
                            "metrics": selected.scores,
                            "warnings": selected.warnings,
                        }
                    ),
                )
            if selected is None:
                validated_evidence = [
                    VisualEvidence.model_validate(item.model_dump(mode="python"))
                    for item in result.evidence
                ]
            if (self.write_provenance or mapping_requires_provenance) and validated_evidence:
                hashes["provenance.json"] = _write(
                    temporary / "provenance.json",
                    _json_bytes([item.model_dump(mode="json") for item in validated_evidence]),
                )
            if result.source_mapping is not None:
                hashes["source-map.json"] = _write(
                    temporary / "source-map.json", _json_bytes(result.source_mapping)
                )
            hashes["review-history.json"] = _write(temporary / "review-history.json", b"[]\n")
            if self.write_alternatives:
                for candidate in result.alternatives:
                    filename = f"alternatives/{_safe_component(candidate.candidate_id)}.json"
                    hashes[filename] = _write(
                        temporary / filename, _json_bytes(_candidate_json(candidate))
                    )
                    if candidate.mermaid_code:
                        mmd_name = f"alternatives/{_safe_component(candidate.candidate_id)}.mmd"
                        hashes[mmd_name] = _write(temporary / mmd_name, candidate.mermaid_code)
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "source_id": result.source_id,
                "source_image": f"images/{Path(result.source_image_name).name}",
                "source_kind": result.source_kind,
                "source_block_ids": result.source_block_ids,
                "page_ids": result.page_ids,
                "anchor_block_id": result.anchor_block_id,
                "status": result.status,
                "grade": result.grade,
                "publish": result.publish,
                "review_required": result.review_required,
                "selected_candidate_id": selected.candidate_id if selected else None,
                "requested_diagram_type": selected.diagram_type if selected else None,
                "emitted_diagram_type": selected.emitted_diagram_type if selected else None,
                "runtime_diagram_type": selected.runtime_diagram_type if selected else None,
                "fallback_chain": selected.fallback_chain if selected else [],
                "serialization_stability": (selected.serialization_stability if selected else None),
                "files": hashes,
                "failures": [item.model_dump(mode="json") for item in result.failures],
            }
            _write(temporary / "manifest.json", _json_bytes(manifest))
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        result.sidecar_dir = relative.as_posix()
        return relative.as_posix()
