"""Shared trust checks for provenance-backed fused node ID mappings."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marker_mermaid.models import BBox, DiagramSceneIR, VisualEvidence


def normalize_scene_bbox(
    bbox: BBox,
    scene: DiagramSceneIR,
    *,
    trusted_canvas_size: tuple[float, float] | None = None,
) -> BBox | None:
    """Return a bounded unit bbox only when the Scene declares its coordinate space."""

    if scene.coordinate_space == "normalized":
        normalized = bbox
    elif scene.canvas_size is not None:
        if trusted_canvas_size is not None and scene.canvas_size != trusted_canvas_size:
            return None
        width, height = trusted_canvas_size or scene.canvas_size
        normalized = (
            bbox[0] / width,
            bbox[1] / height,
            bbox[2] / width,
            bbox[3] / height,
        )
    else:
        return None
    if not all(0 <= value <= 1 for value in normalized):
        return None
    return normalized


def bbox_iou(left: BBox, right: BBox) -> float:
    """Compute intersection-over-union for two ordered boxes."""

    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def source_evidence_matches(
    evidence: VisualEvidence,
    scene: DiagramSceneIR,
    source_bbox: BBox,
    source_text: str | None,
    *,
    trusted_canvas_size: tuple[float, float] | None = None,
) -> bool:
    """Require source evidence to be spatially and, for text, semantically local."""

    if evidence.bbox is None:
        return False
    evidence_bbox = normalize_scene_bbox(
        evidence.bbox,
        scene,
        trusted_canvas_size=trusted_canvas_size,
    )
    if evidence_bbox is None:
        return False
    center = (
        (evidence_bbox[0] + evidence_bbox[2]) / 2,
        (evidence_bbox[1] + evidence_bbox[3]) / 2,
    )
    if not (
        source_bbox[0] <= center[0] <= source_bbox[2]
        and source_bbox[1] <= center[1] <= source_bbox[3]
    ):
        return False
    if evidence.kind not in {"ocr_token", "vector_text"}:
        return True
    if not source_text or not evidence.text:
        return False
    normalized_source = " ".join(unicodedata.normalize("NFKC", source_text).casefold().split())
    normalized_evidence = " ".join(unicodedata.normalize("NFKC", evidence.text).casefold().split())
    return bool(normalized_source and normalized_evidence) and (
        normalized_source == normalized_evidence
        or normalized_source in normalized_evidence
        or normalized_evidence in normalized_source
    )


def authority_evidence_matches(
    evidence: VisualEvidence,
    scene: DiagramSceneIR,
    authority_bbox: BBox,
    minimum_iou: float,
    *,
    trusted_canvas_size: tuple[float, float] | None = None,
) -> bool:
    """Require owner-local contour evidence to cover its authority node."""

    if evidence.kind != "contour" or evidence.bbox is None:
        return False
    evidence_bbox = normalize_scene_bbox(
        evidence.bbox,
        scene,
        trusted_canvas_size=trusted_canvas_size,
    )
    return evidence_bbox is not None and bbox_iou(authority_bbox, evidence_bbox) >= minimum_iou
