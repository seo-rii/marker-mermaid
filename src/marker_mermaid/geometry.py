"""Conservative OpenCV geometry observations for diagram reconstruction.

The geometry engine is deliberately evidence-only: it never invents text or
semantic edge types.  OpenCV is an optional dependency and is imported only
when an image is observed.  If it is unavailable, the engine returns a valid
empty observation with a warning so another candidate engine can continue.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps

from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    SceneRelation,
    VisualEvidence,
)
from marker_mermaid.protocols import SourceContext

BBox = tuple[float, float, float, float]
Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class ContourObservation:
    """A closed shape proposed by a geometry detector.

    Only rectangle-like contours currently become scene nodes.  Other shapes
    can still be retained as contour evidence by future detectors.
    """

    bbox: BBox
    polygon: tuple[Point, ...] = ()
    shape: str = "rectangle"
    confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class LineObservation:
    """A straight connector candidate, usually produced by HoughLinesP."""

    start: Point
    end: Point
    confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class ArrowheadObservation:
    """A triangular arrowhead candidate and its estimated tip."""

    bbox: BBox
    tip: Point
    polygon: tuple[Point, ...] = ()
    confidence: float = 0.5


@dataclass(frozen=True, slots=True)
class GeometryObservation:
    """Raw deterministic geometry observations in source-image pixels."""

    canvas_size: tuple[int, int]
    contours: tuple[ContourObservation, ...] = ()
    lines: tuple[LineObservation, ...] = ()
    arrowheads: tuple[ArrowheadObservation, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_engine_observation(
        self,
        source_block_ids: Iterable[str],
        *,
        endpoint_tolerance: float = 12.0,
    ) -> EngineObservation:
        """Convert high-confidence geometry into evidence-backed Scene IR.

        A line becomes a relation only when each endpoint has one unambiguous,
        distinct contour match.  Labels remain ``None`` because geometry does
        not provide textual evidence.
        """

        block_ids = list(dict.fromkeys(source_block_ids))
        contours = _deduplicate_contours(self.contours)
        lines = sorted((_canonical_line(line) for line in self.lines), key=_line_sort_key)
        arrowheads = sorted(self.arrowheads, key=_arrow_sort_key)

        evidence: list[VisualEvidence] = []
        elements: list[SceneElement] = []
        contour_records: list[tuple[ContourObservation, str, str]] = []
        for index, contour in enumerate(contours, 1):
            evidence_id = f"geometry-contour-{index:03d}"
            element_id = f"geometry-node-{index:03d}"
            evidence.append(
                VisualEvidence(
                    id=evidence_id,
                    kind="contour",
                    bbox=contour.bbox,
                    score=_probability(contour.confidence),
                    source_block_ids=block_ids,
                )
            )
            elements.append(
                SceneElement(
                    id=element_id,
                    role="unknown",
                    text=None,
                    bbox=contour.bbox,
                    polygon=list(contour.polygon) or None,
                    shape=contour.shape,
                    confidence=_probability(contour.confidence),
                    evidence_ids=[evidence_id],
                )
            )
            contour_records.append((contour, element_id, evidence_id))

        line_evidence_ids: list[str] = []
        for index, line in enumerate(lines, 1):
            evidence_id = f"geometry-line-{index:03d}"
            line_evidence_ids.append(evidence_id)
            evidence.append(
                VisualEvidence(
                    id=evidence_id,
                    kind="line_segment",
                    bbox=_points_bbox((line.start, line.end)),
                    score=_probability(line.confidence),
                    source_block_ids=block_ids,
                )
            )

        arrow_evidence_ids: list[str] = []
        for index, arrowhead in enumerate(arrowheads, 1):
            evidence_id = f"geometry-arrowhead-{index:03d}"
            arrow_evidence_ids.append(evidence_id)
            evidence.append(
                VisualEvidence(
                    id=evidence_id,
                    kind="arrowhead",
                    bbox=arrowhead.bbox,
                    score=_probability(arrowhead.confidence),
                    source_block_ids=block_ids,
                )
            )

        relations: list[SceneRelation] = []
        relation_keys: set[tuple[str, str, bool, bool]] = set()
        for line, line_evidence_id in zip(lines, line_evidence_ids, strict=True):
            start_id = _unique_endpoint_match(line.start, contour_records, endpoint_tolerance)
            end_id = _unique_endpoint_match(line.end, contour_records, endpoint_tolerance)
            if start_id is None or end_id is None or start_id == end_id:
                continue

            arrow_at_start, start_arrow_id = _endpoint_arrow(
                line.start,
                arrowheads,
                arrow_evidence_ids,
                endpoint_tolerance,
            )
            arrow_at_end, end_arrow_id = _endpoint_arrow(
                line.end,
                arrowheads,
                arrow_evidence_ids,
                endpoint_tolerance,
            )
            evidence_ids = [line_evidence_id]
            evidence_ids.extend(item for item in (start_arrow_id, end_arrow_id) if item is not None)

            polyline = [line.start, line.end]
            source_id, target_id = start_id, end_id
            relation_arrow_start = arrow_at_start
            relation_arrow_end = arrow_at_end
            if arrow_at_start and not arrow_at_end:
                # Canonicalize directed relations to source -> target so downstream
                # serializers do not need to reason about reversed line coordinates.
                source_id, target_id = end_id, start_id
                polyline.reverse()
                relation_arrow_start = False
                relation_arrow_end = True

            key = (source_id, target_id, relation_arrow_start, relation_arrow_end)
            if key in relation_keys:
                continue
            relation_keys.add(key)
            relations.append(
                SceneRelation(
                    id=f"geometry-relation-{len(relations) + 1:03d}",
                    source_id=source_id,
                    target_id=target_id,
                    relation_type="directed_connector"
                    if relation_arrow_start or relation_arrow_end
                    else "connector",
                    semantic_relation="unknown"
                    if relation_arrow_start or relation_arrow_end
                    else "association",
                    label=None,
                    polyline=polyline,
                    arrow_at_start=relation_arrow_start,
                    arrow_at_end=relation_arrow_end,
                    confidence=_relation_confidence(
                        line,
                        arrowheads,
                        arrow_at_start or arrow_at_end,
                    ),
                    evidence_ids=evidence_ids,
                )
            )

        warnings = list(self.warnings)
        if not elements:
            warnings.append("geometry engine found no conservative node contours")
        prediction = _prediction(elements, relations)
        scene_ir = None
        if elements:
            scene_ir = DiagramSceneIR(
                elements=elements,
                relations=relations,
                reading_direction=_reading_direction(elements, relations),
                diagram_type_candidates=prediction.candidates,
                coordinate_space="pixels",
                canvas_size=(float(self.canvas_size[0]), float(self.canvas_size[1])),
            )
        return EngineObservation(
            prediction=prediction,
            scene_ir=scene_ir,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
        )


class GeometryEngine:
    """Optional OpenCV CandidateEngine that emits conservative Scene IR."""

    name = "geometry"

    def __init__(
        self,
        *,
        detector: Callable[[Image.Image], GeometryObservation] | None = None,
        endpoint_tolerance: float = 12.0,
        min_node_dimension: int = 12,
        max_observations: int = 512,
    ):
        if endpoint_tolerance < 0:
            raise ValueError("endpoint_tolerance must be non-negative")
        if min_node_dimension < 2:
            raise ValueError("min_node_dimension must be at least 2")
        if max_observations < 1:
            raise ValueError("max_observations must be positive")
        self.detector = detector
        self.endpoint_tolerance = endpoint_tolerance
        self.min_node_dimension = min_node_dimension
        self.max_observations = max_observations

    def observe(self, context: SourceContext) -> EngineObservation:
        geometry = (
            self.detector(context.image)
            if self.detector is not None
            else self._detect(context.image)
        )
        return geometry.to_engine_observation(
            context.source_block_ids,
            endpoint_tolerance=self.endpoint_tolerance,
        )

    def _detect(self, image: Image.Image) -> GeometryObservation:
        loaded = _load_opencv()
        source = ImageOps.exif_transpose(image).convert("RGB")
        if loaded is None:
            return GeometryObservation(
                canvas_size=source.size,
                warnings=(
                    "OpenCV is unavailable; geometry contour, Hough line, and arrowhead "
                    "detection was omitted",
                ),
            )
        cv2, np = loaded
        return self._detect_with_opencv(source, cv2, np)

    def _detect_with_opencv(self, image: Image.Image, cv2: Any, np: Any) -> GeometryObservation:
        array = np.asarray(image)
        gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        _threshold, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        contours, _hierarchy = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        node_contours: list[ContourObservation] = []
        arrowheads: list[ArrowheadObservation] = []
        image_area = image.width * image.height
        for raw_contour in contours:
            perimeter = float(cv2.arcLength(raw_contour, True))
            if perimeter <= 0:
                continue
            approximation = cv2.approxPolyDP(raw_contour, 0.03 * perimeter, True)
            points = tuple((float(point[0][0]), float(point[0][1])) for point in approximation)
            x, y, width, height = cv2.boundingRect(raw_contour)
            area = float(abs(cv2.contourArea(raw_contour)))
            bbox = (float(x), float(y), float(x + width), float(y + height))
            if len(points) == 3 and 6 <= area <= max(64, image_area * 0.01):
                if 3 <= width <= image.width * 0.15 and 3 <= height <= image.height * 0.15:
                    arrowheads.append(
                        ArrowheadObservation(
                            bbox=bbox,
                            tip=_triangle_tip(points),
                            polygon=points,
                            confidence=0.65,
                        )
                    )
                continue
            if len(points) != 4 or not cv2.isContourConvex(approximation):
                continue
            if width < self.min_node_dimension or height < self.min_node_dimension:
                continue
            bbox_area = width * height
            if bbox_area <= 0 or bbox_area >= image_area * 0.95:
                continue
            rectangularity = min(1.0, area / bbox_area)
            if rectangularity < 0.45:
                continue
            node_contours.append(
                ContourObservation(
                    bbox=bbox,
                    polygon=points,
                    shape="rectangle",
                    confidence=min(0.9, 0.5 + rectangularity * 0.4),
                )
            )

        edges = cv2.Canny(gray, 60, 180)
        raw_lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=max(20, min(image.size) // 16),
            minLineLength=max(12, min(image.size) // 25),
            maxLineGap=max(6, min(image.size) // 80),
        )
        lines: list[LineObservation] = []
        if raw_lines is not None:
            for x1, y1, x2, y2 in raw_lines[:, 0]:
                start = (float(x1), float(y1))
                end = (float(x2), float(y2))
                if start == end:
                    continue
                lines.append(LineObservation(start=start, end=end, confidence=0.6))

        limited_contours = _deduplicate_contours(node_contours)[: self.max_observations]
        limited_lines = _deduplicate_lines(lines)[: self.max_observations]
        limited_arrows = _deduplicate_arrowheads(arrowheads)[: self.max_observations]
        warnings: tuple[str, ...] = ()
        if any(len(items) > self.max_observations for items in (node_contours, lines, arrowheads)):
            warnings = ("geometry observations were truncated to the configured budget",)
        return GeometryObservation(
            canvas_size=image.size,
            contours=tuple(limited_contours),
            lines=tuple(limited_lines),
            arrowheads=tuple(limited_arrows),
            warnings=warnings,
        )


def _load_opencv() -> tuple[Any, Any] | None:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    return cv2, np


def _probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _points_bbox(points: Iterable[Point]) -> BBox:
    materialized = tuple(points)
    xs = [point[0] for point in materialized]
    ys = [point[1] for point in materialized]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_iou(left: BBox, right: BBox) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _deduplicate_contours(
    contours: Iterable[ContourObservation],
) -> list[ContourObservation]:
    ordered = sorted(
        contours,
        key=lambda item: (-_probability(item.confidence), item.bbox, item.shape, item.polygon),
    )
    selected: list[ContourObservation] = []
    for contour in ordered:
        if contour.bbox[2] <= contour.bbox[0] or contour.bbox[3] <= contour.bbox[1]:
            continue
        if any(_bbox_iou(contour.bbox, existing.bbox) >= 0.8 for existing in selected):
            continue
        selected.append(contour)
    return sorted(selected, key=lambda item: (item.bbox, item.shape, item.polygon))


def _canonical_line(line: LineObservation) -> LineObservation:
    if line.start <= line.end:
        return line
    return LineObservation(start=line.end, end=line.start, confidence=line.confidence)


def _line_sort_key(line: LineObservation) -> tuple[Point, Point, float]:
    canonical = _canonical_line(line)
    return canonical.start, canonical.end, -_probability(canonical.confidence)


def _deduplicate_lines(lines: Iterable[LineObservation]) -> list[LineObservation]:
    selected: dict[tuple[Point, Point], LineObservation] = {}
    for line in sorted(lines, key=_line_sort_key):
        canonical = _canonical_line(line)
        key = canonical.start, canonical.end
        if key not in selected or canonical.confidence > selected[key].confidence:
            selected[key] = canonical
    return list(selected.values())


def _arrow_sort_key(arrowhead: ArrowheadObservation) -> tuple[Point, BBox, float]:
    return arrowhead.tip, arrowhead.bbox, -_probability(arrowhead.confidence)


def _deduplicate_arrowheads(
    arrowheads: Iterable[ArrowheadObservation],
) -> list[ArrowheadObservation]:
    selected: list[ArrowheadObservation] = []
    for arrowhead in sorted(arrowheads, key=_arrow_sort_key):
        if arrowhead.bbox[2] <= arrowhead.bbox[0] or arrowhead.bbox[3] <= arrowhead.bbox[1]:
            continue
        if any(_bbox_iou(arrowhead.bbox, existing.bbox) >= 0.7 for existing in selected):
            continue
        selected.append(arrowhead)
    return selected


def _point_bbox_distance(point: Point, bbox: BBox) -> float:
    dx = max(bbox[0] - point[0], 0.0, point[0] - bbox[2])
    dy = max(bbox[1] - point[1], 0.0, point[1] - bbox[3])
    return math.hypot(dx, dy)


def _unique_endpoint_match(
    point: Point,
    contours: list[tuple[ContourObservation, str, str]],
    tolerance: float,
) -> str | None:
    matches = sorted(
        (
            (_point_bbox_distance(point, contour.bbox), element_id)
            for contour, element_id, _evidence_id in contours
        ),
        key=lambda item: (item[0], item[1]),
    )
    if not matches or matches[0][0] > tolerance:
        return None
    if len(matches) > 1 and math.isclose(matches[0][0], matches[1][0], abs_tol=1e-6):
        return None
    return matches[0][1]


def _endpoint_arrow(
    point: Point,
    arrowheads: list[ArrowheadObservation],
    evidence_ids: list[str],
    tolerance: float,
) -> tuple[bool, str | None]:
    matches = sorted(
        (
            (math.dist(point, arrowhead.tip), evidence_id)
            for arrowhead, evidence_id in zip(arrowheads, evidence_ids, strict=True)
        ),
        key=lambda item: (item[0], item[1]),
    )
    if not matches or matches[0][0] > tolerance:
        return False, None
    if len(matches) > 1 and math.isclose(matches[0][0], matches[1][0], abs_tol=1e-6):
        return False, None
    return True, matches[0][1]


def _relation_confidence(
    line: LineObservation,
    arrowheads: list[ArrowheadObservation],
    has_arrow: bool,
) -> float:
    base = _probability(line.confidence)
    if not has_arrow or not arrowheads:
        return base
    return min(1.0, base * 0.7 + max(_probability(item.confidence) for item in arrowheads) * 0.3)


def _prediction(
    elements: list[SceneElement], relations: list[SceneRelation]
) -> DiagramTypePrediction:
    if not elements:
        return DiagramTypePrediction(
            candidates=["unknown"],
            scores=[1.0],
            negative_signals=["no conservative node contours"],
        )
    directed = any(item.arrow_at_start or item.arrow_at_end for item in relations)
    if len(elements) >= 2 and relations:
        return DiagramTypePrediction(
            candidates=["flowchart", "generic_network"],
            scores=[0.65 if directed else 0.55, 0.35 if directed else 0.45],
            visual_signals=["closed node contours", "connector segments"]
            + (["arrowhead candidates"] if directed else []),
        )
    return DiagramTypePrediction(
        candidates=["generic_network", "flowchart"],
        scores=[0.55, 0.45],
        visual_signals=["closed node contours"],
        negative_signals=["insufficient unambiguous connectors"],
    )


def _reading_direction(elements: list[SceneElement], relations: list[SceneRelation]) -> str:
    by_id = {element.id: element for element in elements}
    deltas: list[tuple[float, float]] = []
    for relation in relations:
        if not relation.arrow_at_start and not relation.arrow_at_end:
            continue
        source = by_id.get(relation.source_id or "")
        target = by_id.get(relation.target_id or "")
        if source is None or target is None:
            continue
        source_center = (
            (source.bbox[0] + source.bbox[2]) / 2,
            (source.bbox[1] + source.bbox[3]) / 2,
        )
        target_center = (
            (target.bbox[0] + target.bbox[2]) / 2,
            (target.bbox[1] + target.bbox[3]) / 2,
        )
        deltas.append((target_center[0] - source_center[0], target_center[1] - source_center[1]))
    if not deltas:
        return "unknown"
    dx = sum(item[0] for item in deltas)
    dy = sum(item[1] for item in deltas)
    if abs(dx) >= abs(dy):
        return "LR" if dx >= 0 else "RL"
    return "TB" if dy >= 0 else "BT"


def _triangle_tip(points: tuple[Point, ...]) -> Point:
    """Return the triangle vertex with the smallest interior angle."""

    def angle(index: int) -> float:
        center = points[index]
        left = points[(index - 1) % 3]
        right = points[(index + 1) % 3]
        vector_left = (left[0] - center[0], left[1] - center[1])
        vector_right = (right[0] - center[0], right[1] - center[1])
        denominator = math.hypot(*vector_left) * math.hypot(*vector_right)
        if denominator == 0:
            return math.pi
        cosine = max(
            -1.0,
            min(
                1.0,
                (vector_left[0] * vector_right[0] + vector_left[1] * vector_right[1]) / denominator,
            ),
        )
        return math.acos(cosine)

    tip_index = min(range(3), key=lambda index: (angle(index), points[index]))
    return points[tip_index]
