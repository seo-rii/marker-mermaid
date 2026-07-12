"""Build bounded, type-aware visual priors without changing source semantics."""

from __future__ import annotations

from collections.abc import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from marker_mermaid.config import MermaidConfig
from marker_mermaid.models import VisualEvidence

_FLOW_TYPES = {
    "flowchart",
    "swimlane",
    "bpmn",
    "state",
    "generic_network",
    "eventmodeling",
}
_ARCHITECTURE_TYPES = {
    "architecture",
    "c4",
    "deployment",
    "component",
    "requirement",
    "block",
    "data_lineage",
}
_CHART_TYPES = {"pie", "xychart", "quadrant", "sankey", "radar", "treemap", "venn"}
_TREE_TYPES = {"mindmap", "treeview", "organization", "ishikawa", "railroad"}
_TIME_TYPES = {"timeline", "gantt", "journey", "kanban", "gitgraph"}

_PROFILES = {
    "flow": (
        "edge_map",
        "arrow_overlay",
        "ocr_overlay",
        "contour_overlay",
        "vector_overlay",
        "adaptive_threshold",
        "hough_line_map",
        "grayscale",
        "color_cluster_map",
    ),
    "architecture": (
        "contour_overlay",
        "ocr_overlay",
        "vector_overlay",
        "color_cluster_map",
        "edge_map",
        "adaptive_threshold",
        "hough_line_map",
        "grayscale",
        "arrow_overlay",
    ),
    "chart": (
        "ocr_overlay",
        "hough_line_map",
        "adaptive_threshold",
        "grayscale",
        "color_cluster_map",
        "vector_overlay",
        "edge_map",
        "contour_overlay",
        "arrow_overlay",
    ),
    "tree": (
        "contour_overlay",
        "edge_map",
        "ocr_overlay",
        "adaptive_threshold",
        "grayscale",
        "vector_overlay",
        "color_cluster_map",
        "hough_line_map",
        "arrow_overlay",
    ),
    "time": (
        "ocr_overlay",
        "hough_line_map",
        "adaptive_threshold",
        "grayscale",
        "vector_overlay",
        "color_cluster_map",
        "edge_map",
        "contour_overlay",
        "arrow_overlay",
    ),
    "packet": (
        "ocr_overlay",
        "hough_line_map",
        "adaptive_threshold",
        "grayscale",
        "vector_overlay",
        "edge_map",
        "contour_overlay",
        "color_cluster_map",
        "arrow_overlay",
    ),
    "general": (
        "ocr_overlay",
        "vector_overlay",
        "arrow_overlay",
        "contour_overlay",
        "edge_map",
        "adaptive_threshold",
        "color_cluster_map",
        "hough_line_map",
        "grayscale",
    ),
}


def _profile_name(diagram_type: str) -> str:
    if diagram_type in _FLOW_TYPES:
        return "flow"
    if diagram_type in _ARCHITECTURE_TYPES:
        return "architecture"
    if diagram_type in _CHART_TYPES:
        return "chart"
    if diagram_type in _TREE_TYPES:
        return "tree"
    if diagram_type in _TIME_TYPES:
        return "time"
    if diagram_type == "packet":
        return "packet"
    return "general"


def _view_priority(diagram_types: Iterable[str]) -> tuple[str, ...]:
    ordered_profiles = list(dict.fromkeys(_profile_name(item) for item in diagram_types))
    if not ordered_profiles:
        ordered_profiles = ["general"]
    result: list[str] = []
    for profile in ordered_profiles:
        for name in _PROFILES[profile]:
            if name not in result:
                result.append(name)
    for name in _PROFILES["general"]:
        if name not in result:
            result.append(name)
    return tuple(result)


def _scaled_box(
    box: tuple[float, float, float, float], scale_x: float, scale_y: float
) -> tuple[float, float, float, float]:
    return box[0] * scale_x, box[1] * scale_y, box[2] * scale_x, box[3] * scale_y


def _tile_starts(length: int, tile_size: int, overlap: int) -> list[int]:
    if length <= tile_size:
        return [0]
    step = tile_size - overlap
    starts = list(range(0, length - tile_size + 1, step))
    final = length - tile_size
    if starts[-1] != final:
        starts.append(final)
    return starts


def _high_resolution_tiles(
    source: Image.Image, config: MermaidConfig, limit: int
) -> list[tuple[str, Image.Image]]:
    if not config.use_tiled_images or limit <= 0 or max(source.size) <= config.tile_size:
        return []
    result: list[tuple[str, Image.Image]] = []
    for top in _tile_starts(source.height, config.tile_size, config.tile_overlap):
        for left in _tile_starts(source.width, config.tile_size, config.tile_overlap):
            right = min(source.width, left + config.tile_size)
            bottom = min(source.height, top + config.tile_size)
            name = f"tile_{len(result) + 1}_x{left}_y{top}_x{right}_y{bottom}"
            result.append((name, source.crop((left, top, right, bottom))))
            if len(result) >= limit:
                return result
    return result


def build_visual_priors(
    image: Image.Image,
    evidence: Iterable[VisualEvidence],
    config: MermaidConfig,
    *,
    diagram_types: Iterable[str] = (),
) -> tuple[dict[str, Image.Image], list[str]]:
    """Build deterministic priors with slots reserved for true source-resolution tiles."""

    source = ImageOps.exif_transpose(image).convert("RGB")
    evidence_items = list(evidence)
    original = source.copy()
    original.thumbnail((config.max_image_dimension, config.max_image_dimension))
    scale_x = original.width / source.width
    scale_y = original.height / source.height
    warnings: list[str] = []

    requested_tile_slots = 0
    if config.use_tiled_images and max(source.size) > config.tile_size and config.max_views > 2:
        requested_tile_slots = min(2, max(1, config.max_views // 4))
    tiles = _high_resolution_tiles(source, config, requested_tile_slots)
    non_tile_limit = config.max_views - len(tiles)
    views: dict[str, Image.Image] = {"original": original}
    if len(views) < non_tile_limit:
        thumbnail = source.copy()
        thumbnail.thumbnail((768, 768))
        views["global_thumbnail"] = thumbnail

    ocr_items = [
        item
        for item in evidence_items
        if item.kind in {"ocr_token", "vector_text"} and item.bbox is not None
    ]
    vector_items = [
        item
        for item in evidence_items
        if item.bbox is not None and (item.kind == "vector_text" or item.id.startswith("vector-"))
    ]
    arrow_items = [
        item for item in evidence_items if item.kind == "arrowhead" and item.bbox is not None
    ]
    contour_items = [
        item for item in evidence_items if item.kind == "contour" and item.bbox is not None
    ]

    def build(name: str) -> Image.Image | None:
        if name == "grayscale":
            return ImageOps.grayscale(original).convert("RGB")
        if name == "adaptive_threshold":
            gray = ImageOps.autocontrast(ImageOps.grayscale(original))
            local_mean = gray.filter(ImageFilter.BoxBlur(max(2, min(original.size) // 80)))
            delta = ImageChops.subtract(local_mean, gray, scale=1.0, offset=128)
            return delta.point(lambda value: 0 if value > 136 else 255).convert("RGB")
        if name == "edge_map" and config.use_canny_edge_map:
            try:
                import cv2
                import numpy as np

                grayscale = cv2.cvtColor(np.asarray(original), cv2.COLOR_RGB2GRAY)
                canny = cv2.Canny(grayscale, 60, 180)
                return Image.fromarray(canny).convert("RGB")
            except Exception as exc:
                warnings.append(
                    f"OpenCV edge prior failed; used Pillow FIND_EDGES: {type(exc).__name__}"
                )
                return ImageOps.autocontrast(
                    ImageOps.grayscale(original).filter(ImageFilter.FIND_EDGES)
                ).convert("RGB")
        if name == "ocr_overlay" and config.use_ocr_overlay and ocr_items:
            overlay = original.copy()
            draw = ImageDraw.Draw(overlay)
            for item in ocr_items:
                box = _scaled_box(item.bbox, scale_x, scale_y)  # type: ignore[arg-type]
                draw.rectangle(box, outline=(40, 120, 255), width=2)
                if item.text:
                    draw.text((box[0], max(0, box[1] - 12)), item.text[:40], fill=(20, 80, 220))
            return overlay
        if name == "vector_overlay" and config.use_vector_primitives and vector_items:
            overlay = original.copy()
            draw = ImageDraw.Draw(overlay)
            for item in vector_items:
                draw.rectangle(
                    _scaled_box(item.bbox, scale_x, scale_y),  # type: ignore[arg-type]
                    outline=(135, 65, 190),
                    width=2,
                )
            return overlay
        if name == "arrow_overlay" and config.use_arrow_overlay and arrow_items:
            overlay = original.copy()
            draw = ImageDraw.Draw(overlay)
            for item in arrow_items:
                box = _scaled_box(item.bbox, scale_x, scale_y)  # type: ignore[arg-type]
                draw.rectangle(box, outline=(255, 80, 30), width=3)
                center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                draw.ellipse(
                    (center[0] - 3, center[1] - 3, center[0] + 3, center[1] + 3),
                    fill=(255, 80, 30),
                )
            return overlay
        if name == "contour_overlay" and contour_items:
            overlay = original.copy()
            draw = ImageDraw.Draw(overlay)
            for item in contour_items:
                draw.rectangle(
                    _scaled_box(item.bbox, scale_x, scale_y),  # type: ignore[arg-type]
                    outline=(35, 170, 90),
                    width=3,
                )
            return overlay
        if name == "color_cluster_map" and config.use_color_group_map:
            return original.quantize(colors=8, method=Image.Quantize.MEDIANCUT).convert("RGB")
        if name == "hough_line_map" and config.use_hough_line_map:
            try:
                import cv2
                import numpy as np

                array = np.asarray(original)
                gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
                edges = cv2.Canny(gray, 60, 180)
                lines = cv2.HoughLinesP(
                    edges,
                    1,
                    np.pi / 180,
                    threshold=40,
                    minLineLength=max(20, min(original.size) // 20),
                    maxLineGap=12,
                )
                if lines is None:
                    return None
                line_map = np.full_like(array, 255)
                for x1, y1, x2, y2 in lines[:, 0]:
                    cv2.line(line_map, (x1, y1), (x2, y2), (0, 0, 0), 2)
                return Image.fromarray(line_map)
            except Exception as exc:
                warnings.append(f"OpenCV Hough prior failed and was omitted: {type(exc).__name__}")
        return None

    for name in _view_priority(diagram_types):
        if len(views) >= non_tile_limit:
            break
        view = build(name)
        if view is not None:
            views[name] = view
    for name, tile in tiles:
        views[name] = tile
    return views, list(dict.fromkeys(warnings))
