"""Conservative page-level proposals for diagrams missed by Marker blocks.

The detector is deliberately independent from Marker: callers provide a Pillow
page image and the page-coordinate bounding boxes already occupied by Marker
blocks.  It returns evidence-only regions and never registers or mutates block
types.  OpenCV is an optional acceleration path; a bounded Pillow implementation
is always available and is used automatically when importing or running OpenCV
fails.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from PIL import Image, ImageFilter, ImageOps, ImageStat
from pydantic import BaseModel, Field, field_validator, model_validator

from marker_mermaid.models import BBox


class DiagramRegion(BaseModel):
    """One missed-diagram proposal expressed in original page coordinates."""

    region_id: str
    bbox: BBox
    confidence: float
    signals: list[str] = Field(default_factory=list)
    component_count: int
    edge_density: float

    @model_validator(mode="after")
    def fields_are_valid(self) -> DiagramRegion:
        x1, y1, x2, y2 = self.bbox
        if x2 <= x1 or y2 <= y1:
            raise ValueError("diagram region bbox must have positive area")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.component_count < 1:
            raise ValueError("component_count must be positive")
        if not 0 <= self.edge_density <= 1:
            raise ValueError("edge_density must be between 0 and 1")
        return self


class PageDiagramDetection(BaseModel):
    """Result envelope retaining backend and graceful-fallback diagnostics."""

    page_size: tuple[int, int]
    regions: list[DiagramRegion] = Field(default_factory=list)
    edge_backend: Literal["opencv", "pillow"]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("page_size")
    @classmethod
    def page_size_is_positive(cls, value: tuple[int, int]) -> tuple[int, int]:
        if value[0] <= 0 or value[1] <= 0:
            raise ValueError("page size must have positive dimensions")
        return value


def propose_page_diagrams(
    page_image: Image.Image,
    occupied_bboxes: Iterable[BBox] = (),
    **kwargs: Any,
) -> list[DiagramRegion]:
    """Return only missed-diagram regions; see :func:`detect_page_diagram_regions`."""

    return detect_page_diagram_regions(page_image, occupied_bboxes, **kwargs).regions


def detect_page_diagram_regions(
    page_image: Image.Image,
    occupied_bboxes: Iterable[BBox] = (),
    *,
    use_opencv: bool = True,
    max_processing_dimension: int = 900,
    max_regions: int = 8,
    min_region_area_fraction: float = 0.006,
    merge_gap_fraction: float = 0.025,
    nms_iou_threshold: float = 0.35,
) -> PageDiagramDetection:
    """Propose structural regions not substantially covered by existing blocks.

    Work is bounded by ``max_processing_dimension`` and ``max_regions``.  The
    heuristic requires evidence on both image axes (box/grid-like structure),
    rejects very dense photographic regions and text-line-shaped regions, and
    merges components only when they overlap on one axis and have a small gap on
    the other.  The original image is never modified.
    """

    if page_image.width <= 0 or page_image.height <= 0:
        raise ValueError("page image must have positive dimensions")
    if max_processing_dimension < 64:
        raise ValueError("max_processing_dimension must be at least 64")
    if max_regions < 1:
        raise ValueError("max_regions must be positive")
    if not 0 < min_region_area_fraction < 1:
        raise ValueError("min_region_area_fraction must be between 0 and 1")
    if not 0 <= merge_gap_fraction <= 0.1:
        raise ValueError("merge_gap_fraction must be between 0 and 0.1")
    if not 0 <= nms_iou_threshold <= 1:
        raise ValueError("nms_iou_threshold must be between 0 and 1")

    occupied = _normalize_occupied(occupied_bboxes, page_image.size)
    scale = min(1.0, max_processing_dimension / max(page_image.size))
    processing_size = (
        max(1, round(page_image.width * scale)),
        max(1, round(page_image.height * scale)),
    )
    gray = ImageOps.grayscale(page_image)
    if gray.size != processing_size:
        gray = gray.resize(processing_size, Image.Resampling.BILINEAR)

    edge, backend, warnings = _edge_mask(gray, use_opencv=use_opencv)
    # Pillow's FIND_EDGES treats the image boundary as an edge. Clearing the
    # outer samples also prevents scanner borders from becoming page-sized
    # proposals in either backend.
    edge_draw = edge.load()
    for x in range(edge.width):
        edge_draw[x, 0] = 0
        edge_draw[x, edge.height - 1] = 0
    for y in range(edge.height):
        edge_draw[0, y] = 0
        edge_draw[edge.width - 1, y] = 0
    # A one-pixel closing joins small anti-aliasing gaps without joining text
    # lines separated by normal leading.
    connected_mask = edge.filter(ImageFilter.MaxFilter(3))
    components = _connected_components(connected_mask)
    meaningful = _meaningful_components(components, processing_size)
    clusters = _merge_components(
        meaningful,
        processing_size,
        maximum_gap=max(2, round(max(processing_size) * merge_gap_fraction)),
    )

    raw_candidates: list[DiagramRegion] = []
    page_area = processing_size[0] * processing_size[1]
    occupied_scaled = [_scale_bbox(box, scale) for box in occupied]
    for cluster in clusters:
        bbox = _pad_bbox(_union_bbox(cluster), processing_size, padding=3)
        if _area(bbox) < page_area * min_region_area_fraction:
            continue
        if any(_intersection_area(bbox, box) / _area(bbox) > 0.01 for box in occupied_scaled):
            continue
        assessment = _assess_region(gray, edge, bbox, cluster)
        if assessment is None:
            continue
        confidence, signals, edge_density = assessment
        page_bbox = tuple(round(value / scale, 3) for value in bbox)
        raw_candidates.append(
            DiagramRegion(
                region_id="pending",
                bbox=page_bbox,
                confidence=confidence,
                signals=signals,
                component_count=len(cluster),
                edge_density=edge_density,
            )
        )

    selected = _non_maximum_suppression(raw_candidates, nms_iou_threshold, max_regions)
    selected.sort(key=lambda item: (item.bbox[1], item.bbox[0], -item.confidence, item.bbox))
    regions = [
        item.model_copy(update={"region_id": f"page-diagram-{index:03d}"})
        for index, item in enumerate(selected, 1)
    ]
    return PageDiagramDetection(
        page_size=page_image.size,
        regions=regions,
        edge_backend=backend,
        warnings=warnings,
    )


def _edge_mask(gray: Image.Image, *, use_opencv: bool) -> tuple[Image.Image, str, list[str]]:
    warnings: list[str] = []
    if use_opencv:
        loaded = _load_opencv()
        if loaded is not None:
            cv2, np = loaded
            try:
                array = np.asarray(gray)
                blurred = cv2.GaussianBlur(array, (3, 3), 0)
                edges = cv2.Canny(blurred, 50, 150)
                return Image.fromarray(edges).convert("L"), "opencv", warnings
            except Exception as exc:  # pragma: no cover - backend-specific failures
                warnings.append(f"OpenCV page detector failed; used Pillow fallback: {exc}")
        else:
            warnings.append("OpenCV is unavailable; used Pillow page detector")

    enhanced = ImageOps.autocontrast(gray)
    found = enhanced.filter(ImageFilter.FIND_EDGES)
    histogram = found.histogram()
    total = found.width * found.height
    target = max(1, round(total * 0.10))
    cumulative = 0
    threshold = 48
    for value in range(255, 15, -1):
        cumulative += histogram[value]
        if cumulative >= target:
            threshold = max(32, min(112, value))
            break
    return found.point(lambda value: 255 if value >= threshold else 0), "pillow", warnings


def _load_opencv() -> tuple[Any, Any] | None:
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np
    except Exception:
        return None
    return cv2, np


def _connected_components(mask: Image.Image) -> list[BBox]:
    width, height = mask.size
    pixels = mask.load()
    visited = bytearray(width * height)
    components: list[BBox] = []
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if visited[offset] or not pixels[x, y]:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[offset] = 1
            left = right = x
            top = bottom = y
            count = 0
            while queue:
                current_x, current_y = queue.popleft()
                count += 1
                left = min(left, current_x)
                right = max(right, current_x)
                top = min(top, current_y)
                bottom = max(bottom, current_y)
                for next_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                    for next_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                        next_offset = next_y * width + next_x
                        if visited[next_offset] or not pixels[next_x, next_y]:
                            continue
                        visited[next_offset] = 1
                        queue.append((next_x, next_y))
            if count >= 6:
                components.append((float(left), float(top), float(right + 1), float(bottom + 1)))
    return sorted(components, key=lambda box: (box[1], box[0], box[3], box[2]))


def _meaningful_components(components: Sequence[BBox], size: tuple[int, int]) -> list[BBox]:
    width, height = size
    minimum_area = max(16.0, width * height * 0.000025)
    return [
        box
        for box in components
        if _area(box) >= minimum_area
        and box[2] - box[0] >= 4
        and box[3] - box[1] >= 4
        and not (
            box[3] - box[1] < max(6, height * 0.012) and box[2] - box[0] > 8 * (box[3] - box[1])
        )
    ]


def _merge_components(
    components: Sequence[BBox],
    size: tuple[int, int],
    *,
    maximum_gap: int,
) -> list[list[BBox]]:
    """Merge aligned nearby components with a bounded union-find pass."""

    parents = list(range(len(components)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    # Limiting pair comparisons protects pathological noisy pages. The largest
    # components are the only useful structural evidence in that situation.
    bounded = sorted(components, key=lambda box: (-_area(box), box))[:256]
    parents = list(range(len(bounded)))
    page_area = size[0] * size[1]
    for left_index, left in enumerate(bounded):
        for right_index in range(left_index + 1, len(bounded)):
            right = bounded[right_index]
            horizontal_gap = max(0.0, max(left[0], right[0]) - min(left[2], right[2]))
            vertical_gap = max(0.0, max(left[1], right[1]) - min(left[3], right[3]))
            x_overlap = _axis_overlap(left[0], left[2], right[0], right[2])
            y_overlap = _axis_overlap(left[1], left[3], right[1], right[3])
            contained = _contains(left, right) or _contains(right, left)
            aligned_nearby = (horizontal_gap <= maximum_gap and y_overlap >= 0.22) or (
                vertical_gap <= maximum_gap and x_overlap >= 0.22
            )
            if not (contained or aligned_nearby):
                continue
            combined = _union_bbox((left, right))
            if _area(combined) <= page_area * 0.72:
                union(left_index, right_index)

    grouped: dict[int, list[BBox]] = {}
    for index, component in enumerate(bounded):
        grouped.setdefault(find(index), []).append(component)
    return sorted(
        grouped.values(),
        key=lambda group: (_union_bbox(group)[1], _union_bbox(group)[0]),
    )


def _assess_region(
    gray: Image.Image,
    edge: Image.Image,
    bbox: BBox,
    cluster: Sequence[BBox],
) -> tuple[float, list[str], float] | None:
    left, top, right, bottom = (round(value) for value in bbox)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    aspect = max(width / height, height / width)
    edge_crop = edge.crop((left, top, right, bottom))
    edge_histogram = edge_crop.histogram()
    edge_pixels = sum(edge_histogram[128:])
    edge_density = edge_pixels / (width * height)
    gray_crop = gray.crop((left, top, right, bottom))
    dark_pixels = sum(gray_crop.histogram()[:176])
    dark_density = dark_pixels / (width * height)
    deviation = ImageStat.Stat(gray_crop).stddev[0]

    pixels = edge_crop.load()
    maximum_row = max(sum(bool(pixels[x, y]) for x in range(width)) for y in range(height))
    maximum_column = max(sum(bool(pixels[x, y]) for y in range(height)) for x in range(width))
    long_horizontal = maximum_row / width >= 0.42
    long_vertical = maximum_column / height >= 0.28

    # Repeated text strokes tend to be wide and shallow without a supporting
    # vertical edge. Busy photographic/noise crops have excessive edge or ink.
    if aspect > 10 or (aspect > 5 and not (long_horizontal and long_vertical)):
        return None
    if not 0.006 <= edge_density <= 0.24:
        return None
    if dark_density > 0.26 or (deviation > 67 and dark_density > 0.13):
        return None
    if not (long_horizontal and long_vertical):
        return None

    signals = ["long_horizontal_edges", "long_vertical_edges", "multi_axis_structure"]
    if len(cluster) >= 2:
        signals.append("bounded_component_group")
    if edge_density <= 0.12:
        signals.append("sparse_structural_edges")
    if dark_density <= 0.12:
        signals.append("low_ink_density")
    confidence = 0.50
    confidence += 0.11 if edge_density <= 0.12 else 0.04
    confidence += 0.08 if dark_density <= 0.12 else 0.02
    confidence += min(0.10, 0.025 * (len(cluster) - 1))
    confidence += 0.08 if aspect <= 4 else 0.02
    return round(min(0.92, confidence), 4), signals, round(edge_density, 6)


def _non_maximum_suppression(
    candidates: Sequence[DiagramRegion], threshold: float, limit: int
) -> list[DiagramRegion]:
    ordered = sorted(
        candidates,
        key=lambda item: (-item.confidence, -_area(item.bbox), item.bbox, tuple(item.signals)),
    )
    selected: list[DiagramRegion] = []
    for candidate in ordered:
        if any(_iou(candidate.bbox, prior.bbox) > threshold for prior in selected):
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _normalize_occupied(boxes: Iterable[BBox], size: tuple[int, int]) -> list[BBox]:
    normalized: list[BBox] = []
    for raw in boxes:
        if len(raw) != 4:
            raise ValueError("occupied bbox must have four coordinates")
        left = max(0.0, min(float(raw[0]), float(size[0])))
        top = max(0.0, min(float(raw[1]), float(size[1])))
        right = max(0.0, min(float(raw[2]), float(size[0])))
        bottom = max(0.0, min(float(raw[3]), float(size[1])))
        if right > left and bottom > top:
            normalized.append((left, top, right, bottom))
    return sorted(normalized)


def _scale_bbox(bbox: BBox, scale: float) -> BBox:
    return tuple(value * scale for value in bbox)


def _pad_bbox(bbox: BBox, size: tuple[int, int], *, padding: int) -> BBox:
    return (
        max(0.0, bbox[0] - padding),
        max(0.0, bbox[1] - padding),
        min(float(size[0]), bbox[2] + padding),
        min(float(size[1]), bbox[3] + padding),
    )


def _union_bbox(boxes: Sequence[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _intersection_area(left: BBox, right: BBox) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _iou(left: BBox, right: BBox) -> float:
    intersection = _intersection_area(left, right)
    union = _area(left) + _area(right) - intersection
    return intersection / union if union else 0.0


def _axis_overlap(left1: float, left2: float, right1: float, right2: float) -> float:
    intersection = max(0.0, min(left2, right2) - max(left1, right1))
    minimum = min(left2 - left1, right2 - right1)
    return intersection / minimum if minimum > 0 else 0.0


def _contains(outer: BBox, inner: BBox) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )
