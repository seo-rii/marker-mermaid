"""Deterministic, Marker-independent diagram candidate discovery.

This module deliberately accepts Pillow images and plain bounding boxes instead of
Marker block objects.  Adapters can therefore use the same heuristics for Marker
blocks, page-level proposals, and user-selected regions.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from typing import Literal

from PIL import Image, ImageOps
from pydantic import BaseModel, Field, field_validator, model_validator

from marker_mermaid.models import BBox


class PanelRegion(BaseModel):
    """One proposed panel in source-image coordinates."""

    panel_id: str
    bbox: BBox
    confidence: float
    signals: list[str] = Field(default_factory=list)

    @field_validator("bbox")
    @classmethod
    def bbox_is_ordered(cls, value: BBox) -> BBox:
        if value[2] <= value[0] or value[3] <= value[1]:
            raise ValueError("panel bbox must have positive area")
        return value

    @field_validator("confidence")
    @classmethod
    def confidence_is_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class CompositePanelProposal(BaseModel):
    """A panel split proposal; the unsplit source remains a separate candidate."""

    panels: list[PanelRegion]
    separators: list[tuple[Literal["horizontal", "vertical"], int]] = Field(default_factory=list)
    score: float
    signals: list[str]
    component_backend: Literal["opencv", "python"]

    @model_validator(mode="after")
    def contains_multiple_panels(self) -> CompositePanelProposal:
        if len(self.panels) < 2:
            raise ValueError("a composite proposal requires at least two panels")
        if not 0 <= self.score <= 1:
            raise ValueError("score must be between 0 and 1")
        return self


class SourceFragment(BaseModel):
    """One page/block crop placed on a virtual source canvas."""

    fragment_id: str
    page_id: int
    source_block_ids: list[str] = Field(default_factory=list)
    page_bbox: BBox | None = None
    crop_bbox: BBox | None = None
    image_size: tuple[int, int]
    canvas_offset: tuple[float, float] = (0.0, 0.0)

    @model_validator(mode="after")
    def fields_are_valid(self) -> SourceFragment:
        if self.image_size[0] <= 0 or self.image_size[1] <= 0:
            raise ValueError("image size must have positive dimensions")
        for name, bbox in (("page", self.page_bbox), ("crop", self.crop_bbox)):
            if bbox is not None and (bbox[2] <= bbox[0] or bbox[3] <= bbox[1]):
                raise ValueError(f"{name} bbox must have positive area")
        if len(set(self.source_block_ids)) != len(self.source_block_ids):
            raise ValueError("source block ids must be unique")
        return self


class DiscoveredSource(BaseModel):
    """Serializable virtual-source registry entry used between discovery stages.

    Image bytes intentionally stay outside this model. A source contains one fragment
    for an original/panel and multiple positioned fragments for same- or multi-page
    merges, preserving every page bbox and canvas transform.
    """

    source_id: str
    anchor_block_id: str | None = None
    kind: Literal["original", "panel", "merged", "full_page", "page_proposal"]
    fragments: list[SourceFragment]
    signals: list[str] = Field(default_factory=list)
    confidence: float

    @model_validator(mode="after")
    def fields_are_valid(self) -> DiscoveredSource:
        if not self.fragments:
            raise ValueError("a discovered source requires at least one fragment")
        fragment_ids = [fragment.fragment_id for fragment in self.fragments]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("fragment ids must be unique")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return self


class FragmentCandidate(BaseModel):
    """Minimal fragment metadata used by the merge proposal heuristic."""

    block_id: str
    bbox: BBox
    page: int = 0
    page_size: tuple[float, float] | None = None
    caption: str | None = None
    continued: bool = False
    boundary_touches: set[Literal["top", "right", "bottom", "left"]] = Field(default_factory=set)

    @model_validator(mode="after")
    def geometry_is_valid(self) -> FragmentCandidate:
        x1, y1, x2, y2 = self.bbox
        if x2 <= x1 or y2 <= y1:
            raise ValueError("fragment bbox must have positive area")
        if self.page_size is not None:
            width, height = self.page_size
            if width <= 0 or height <= 0:
                raise ValueError("page size must have positive dimensions")
        return self


class FragmentMergeProposal(BaseModel):
    """A non-destructive proposal to merge two source fragments."""

    block_ids: tuple[str, str]
    bbox: BBox
    pages: tuple[int, int]
    score: float
    signals: list[str]


class FullPageCoverage(BaseModel):
    """Coverage measurements for a possible full-page image candidate."""

    is_full_page: bool
    area_ratio: float
    width_ratio: float
    height_ratio: float
    edge_distances: tuple[float, float, float, float]


def propose_composite_panels(
    image: Image.Image,
    *,
    min_panel_fraction: float = 0.08,
    min_gutter_fraction: float = 0.025,
    use_opencv: bool = True,
) -> CompositePanelProposal | None:
    """Propose a conservative panel split from gutters, separators, and components.

    Only the strongest horizontal and vertical split are used, limiting a proposal
    to four panels. A split is emitted only when every resulting panel contains a
    meaningful connected component. OpenCV accelerates component extraction when
    installed; the bounded pure-Python fallback has identical output semantics.
    """

    if image.width < 24 or image.height < 24:
        return None
    mask, scale = _foreground_mask(image)
    components, backend = _connected_components(mask, use_opencv=use_opencv)
    min_width = max(3, round(mask.width * min_gutter_fraction))
    min_height = max(3, round(mask.height * min_gutter_fraction))
    vertical_gutters = _projection_bands(mask, axis="vertical", maximum_density=0.008)
    horizontal_gutters = _projection_bands(mask, axis="horizontal", maximum_density=0.008)
    vertical_lines = _projection_bands(mask, axis="vertical", minimum_density=0.82)
    horizontal_lines = _projection_bands(mask, axis="horizontal", minimum_density=0.82)

    x_cut, x_kind = _best_cut(vertical_gutters, vertical_lines, mask.width, min_width)
    y_cut, y_kind = _best_cut(horizontal_gutters, horizontal_lines, mask.height, min_height)
    x_ranges = _ranges_from_cut(mask.width, x_cut)
    y_ranges = _ranges_from_cut(mask.height, y_cut)
    regions = [(left, top, right, bottom) for top, bottom in y_ranges for left, right in x_ranges]
    minimum_area = mask.width * mask.height * min_panel_fraction
    if len(regions) < 2 or any(_area(region) < minimum_area for region in regions):
        return None

    component_regions = [_components_in_region(components, region) for region in regions]
    if any(not region_components for region_components in component_regions):
        return None

    split_kinds = [kind for kind in (x_kind, y_kind) if kind]
    signals = sorted({f"{kind}_separator" for kind in split_kinds})
    signals.append("connected_component_groups")
    score = min(0.98, 0.52 + 0.16 * len(split_kinds) + 0.04 * len(regions))
    inv_scale = 1 / scale
    panels = [
        PanelRegion(
            panel_id=f"panel-{index + 1}",
            bbox=tuple(round(value * inv_scale, 3) for value in region),
            confidence=score,
            signals=signals.copy(),
        )
        for index, region in enumerate(regions)
    ]
    separators: list[tuple[Literal["horizontal", "vertical"], int]] = []
    if x_cut is not None:
        separators.append(("vertical", round(x_cut * inv_scale)))
    if y_cut is not None:
        separators.append(("horizontal", round(y_cut * inv_scale)))
    return CompositePanelProposal(
        panels=panels,
        separators=separators,
        score=score,
        signals=signals,
        component_backend=backend,
    )


def split_composite_figure(
    image: Image.Image,
    proposal: CompositePanelProposal | None = None,
) -> list[tuple[PanelRegion, Image.Image]]:
    """Crop a panel proposal while retaining each crop's source-coordinate mapping."""

    proposal = proposal or propose_composite_panels(image)
    if proposal is None:
        return []
    result: list[tuple[PanelRegion, Image.Image]] = []
    for panel in proposal.panels:
        left, top, right, bottom = panel.bbox
        box = (round(left), round(top), round(right), round(bottom))
        result.append((panel, image.crop(box)))
    return result


def propose_fragment_merges(
    fragments: Sequence[FragmentCandidate],
    *,
    max_gap_ratio: float = 0.04,
    boundary_tolerance: float = 0.02,
) -> list[FragmentMergeProposal]:
    """Return deterministic pairwise merge proposals from spatial and semantic signals."""

    proposals: list[FragmentMergeProposal] = []
    ordered = sorted(
        fragments,
        key=lambda item: (item.page, item.bbox[1], item.bbox[0], item.block_id),
    )
    for index, first in enumerate(ordered):
        for second in ordered[index + 1 :]:
            if second.page - first.page > 1:
                break
            signals: list[str] = []
            same_page = first.page == second.page
            if same_page and _are_adjacent(first, second, max_gap_ratio):
                signals.append("adjacent_bbox")
            caption_a = _normalized_caption(first.caption)
            caption_b = _normalized_caption(second.caption)
            if caption_a and caption_a == caption_b:
                signals.append("shared_caption")
            if first.continued or second.continued or _caption_says_continued(first, second):
                signals.append("continued")
            touches_a = _boundary_touches(first, boundary_tolerance)
            touches_b = _boundary_touches(second, boundary_tolerance)
            if _compatible_boundary_touch(first, second, touches_a, touches_b):
                signals.append("boundary_touch")

            spatial = "adjacent_bbox" in signals or "boundary_touch" in signals
            semantic = "shared_caption" in signals or "continued" in signals
            if not spatial or (not semantic and "boundary_touch" not in signals):
                continue
            if not same_page and not semantic:
                continue
            score = min(
                0.98,
                0.34
                + 0.24 * ("adjacent_bbox" in signals)
                + 0.18 * ("boundary_touch" in signals)
                + 0.14 * ("shared_caption" in signals)
                + 0.16 * ("continued" in signals),
            )
            bbox = _union_bbox(first.bbox, second.bbox) if same_page else first.bbox
            proposals.append(
                FragmentMergeProposal(
                    block_ids=(first.block_id, second.block_id),
                    bbox=bbox,
                    pages=(first.page, second.page),
                    score=score,
                    signals=signals,
                )
            )
    return proposals


def assess_full_page_coverage(
    candidate_bbox: BBox,
    page_bbox: BBox,
    *,
    minimum_area_ratio: float = 0.90,
    edge_tolerance: float = 0.04,
) -> FullPageCoverage:
    """Measure whether a candidate covers a page, clipping overscan to page bounds."""

    px1, py1, px2, py2 = page_bbox
    if px2 <= px1 or py2 <= py1:
        raise ValueError("page bbox must have positive area")
    cx1, cy1, cx2, cy2 = candidate_bbox
    if cx2 <= cx1 or cy2 <= cy1:
        raise ValueError("candidate bbox must have positive area")
    page_width, page_height = px2 - px1, py2 - py1
    intersection_width = max(0.0, min(cx2, px2) - max(cx1, px1))
    intersection_height = max(0.0, min(cy2, py2) - max(cy1, py1))
    width_ratio = min(1.0, intersection_width / page_width)
    height_ratio = min(1.0, intersection_height / page_height)
    area_ratio = width_ratio * height_ratio
    distances = (
        abs(cx1 - px1) / page_width,
        abs(cy1 - py1) / page_height,
        abs(px2 - cx2) / page_width,
        abs(py2 - cy2) / page_height,
    )
    edges_close = all(distance <= edge_tolerance for distance in distances)
    return FullPageCoverage(
        is_full_page=area_ratio >= minimum_area_ratio and edges_close,
        area_ratio=area_ratio,
        width_ratio=width_ratio,
        height_ratio=height_ratio,
        edge_distances=distances,
    )


def _foreground_mask(image: Image.Image, maximum_dimension: int = 512) -> tuple[Image.Image, float]:
    gray = ImageOps.grayscale(ImageOps.exif_transpose(image))
    scale = min(1.0, maximum_dimension / max(gray.size))
    if scale < 1:
        gray = gray.resize(
            (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
            Image.Resampling.BILINEAR,
        )
    gray = ImageOps.autocontrast(gray)
    return gray.point(lambda value: 255 if value < 220 else 0, mode="1"), scale


def _projection_bands(
    mask: Image.Image,
    *,
    axis: Literal["horizontal", "vertical"],
    maximum_density: float | None = None,
    minimum_density: float | None = None,
) -> list[tuple[int, int]]:
    pixels = mask.load()
    length = mask.width if axis == "vertical" else mask.height
    cross_length = mask.height if axis == "vertical" else mask.width
    selected: list[bool] = []
    for position in range(length):
        if axis == "vertical":
            foreground = sum(bool(pixels[position, offset]) for offset in range(cross_length))
        else:
            foreground = sum(bool(pixels[offset, position]) for offset in range(cross_length))
        density = foreground / cross_length
        selected.append(
            (maximum_density is not None and density <= maximum_density)
            or (minimum_density is not None and density >= minimum_density)
        )
    return _true_runs(selected)


def _true_runs(values: Iterable[bool]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(values):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, index + 1))
    return runs


def _best_cut(
    gutters: Sequence[tuple[int, int]],
    lines: Sequence[tuple[int, int]],
    length: int,
    minimum_gutter: int,
) -> tuple[int | None, str | None]:
    margin = length * 0.12
    options: list[tuple[float, int, str]] = []
    for start, end in gutters:
        center = (start + end) // 2
        if end - start >= minimum_gutter and margin <= center <= length - margin:
            balance = min(center, length - center) / max(center, length - center)
            options.append(((end - start) / length + balance, center, "whitespace"))
    for start, end in lines:
        center = (start + end) // 2
        if end - start <= max(6, length * 0.02) and margin <= center <= length - margin:
            balance = min(center, length - center) / max(center, length - center)
            options.append((0.15 + balance, center, "line"))
    if not options:
        return None, None
    _, center, kind = max(options, key=lambda item: (item[0], -abs(item[1] - length / 2)))
    return center, kind


def _ranges_from_cut(length: int, cut: int | None) -> list[tuple[int, int]]:
    return [(0, length)] if cut is None else [(0, cut), (cut, length)]


def _connected_components(
    mask: Image.Image,
    *,
    use_opencv: bool,
) -> tuple[list[tuple[int, int, int, int]], Literal["opencv", "python"]]:
    if use_opencv:
        try:
            import cv2
            import numpy as np

            count, _, stats, _ = cv2.connectedComponentsWithStats(
                np.asarray(mask, dtype="uint8"), connectivity=8
            )
            components = [
                tuple(int(value) for value in stats[index, :4]) for index in range(1, count)
            ]
            boxes = [(x, y, x + width, y + height) for x, y, width, height in components]
            return _meaningful_components(boxes, mask.size), "opencv"
        except ImportError:
            pass
    return _python_connected_components(mask), "python"


def _python_connected_components(mask: Image.Image) -> list[tuple[int, int, int, int]]:
    width, height = mask.size
    pixels = mask.load()
    seen = bytearray(width * height)
    boxes: list[tuple[int, int, int, int]] = []
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if seen[offset] or not pixels[x, y]:
                continue
            seen[offset] = 1
            queue = deque([(x, y)])
            left = right = x
            top = bottom = y
            while queue:
                current_x, current_y = queue.popleft()
                left, right = min(left, current_x), max(right, current_x)
                top, bottom = min(top, current_y), max(bottom, current_y)
                for next_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                    for next_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                        next_offset = next_y * width + next_x
                        if not seen[next_offset] and pixels[next_x, next_y]:
                            seen[next_offset] = 1
                            queue.append((next_x, next_y))
            boxes.append((left, top, right + 1, bottom + 1))
    return _meaningful_components(boxes, mask.size)


def _meaningful_components(
    boxes: Iterable[tuple[int, int, int, int]],
    size: tuple[int, int],
) -> list[tuple[int, int, int, int]]:
    minimum_area = max(4, size[0] * size[1] * 0.00008)
    return [box for box in boxes if _area(box) >= minimum_area]


def _components_in_region(
    components: Sequence[tuple[int, int, int, int]], region: tuple[int, int, int, int]
) -> list[tuple[int, int, int, int]]:
    left, top, right, bottom = region
    return [
        box
        for box in components
        if left <= (box[0] + box[2]) / 2 < right and top <= (box[1] + box[3]) / 2 < bottom
    ]


def _area(bbox: tuple[int, int, int, int]) -> int:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _are_adjacent(
    first: FragmentCandidate, second: FragmentCandidate, max_gap_ratio: float
) -> bool:
    page_width, page_height = first.page_size or second.page_size or _union_size(first, second)
    horizontal_gap = max(
        0.0, max(first.bbox[0], second.bbox[0]) - min(first.bbox[2], second.bbox[2])
    )
    vertical_gap = max(0.0, max(first.bbox[1], second.bbox[1]) - min(first.bbox[3], second.bbox[3]))
    horizontal_overlap = _overlap(first.bbox[1], first.bbox[3], second.bbox[1], second.bbox[3])
    vertical_overlap = _overlap(first.bbox[0], first.bbox[2], second.bbox[0], second.bbox[2])
    return (horizontal_gap <= page_width * max_gap_ratio and horizontal_overlap > 0) or (
        vertical_gap <= page_height * max_gap_ratio and vertical_overlap > 0
    )


def _union_size(first: FragmentCandidate, second: FragmentCandidate) -> tuple[float, float]:
    bbox = _union_bbox(first.bbox, second.bbox)
    return max(1.0, bbox[2] - bbox[0]), max(1.0, bbox[3] - bbox[1])


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _normalized_caption(caption: str | None) -> str:
    if not caption:
        return ""
    lowered = caption.casefold().replace("continued", "").replace("계속", "")
    return " ".join(lowered.rstrip(".:-–—() ").split())


def _caption_says_continued(first: FragmentCandidate, second: FragmentCandidate) -> bool:
    text = f"{first.caption or ''} {second.caption or ''}".casefold()
    return "continued" in text or "계속" in text


def _boundary_touches(fragment: FragmentCandidate, tolerance: float) -> set[str]:
    touches = set(fragment.boundary_touches)
    if fragment.page_size is None:
        return touches
    width, height = fragment.page_size
    left, top, right, bottom = fragment.bbox
    if left <= width * tolerance:
        touches.add("left")
    if top <= height * tolerance:
        touches.add("top")
    if width - right <= width * tolerance:
        touches.add("right")
    if height - bottom <= height * tolerance:
        touches.add("bottom")
    return touches


def _compatible_boundary_touch(
    first: FragmentCandidate,
    second: FragmentCandidate,
    first_touches: set[str],
    second_touches: set[str],
) -> bool:
    if first.page == second.page:
        first_left = first.bbox[0] <= second.bbox[0]
        first_above = first.bbox[1] <= second.bbox[1]
        return (first_left and "right" in first_touches and "left" in second_touches) or (
            first_above and "bottom" in first_touches and "top" in second_touches
        )
    return first.page + 1 == second.page and "bottom" in first_touches and "top" in second_touches


def _union_bbox(first: BBox, second: BBox) -> BBox:
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )
