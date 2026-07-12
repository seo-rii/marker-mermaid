"""Build type-aware visual priors without changing source semantics."""

from __future__ import annotations

from collections.abc import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from marker_mermaid.config import MermaidConfig
from marker_mermaid.models import VisualEvidence


def build_visual_priors(
    image: Image.Image,
    evidence: Iterable[VisualEvidence],
    config: MermaidConfig,
) -> tuple[dict[str, Image.Image], list[str]]:
    source = ImageOps.exif_transpose(image).convert("RGB")
    original = source.copy()
    original.thumbnail((config.max_image_dimension, config.max_image_dimension))
    scale_x = original.width / source.width
    scale_y = original.height / source.height
    views: dict[str, Image.Image] = {"original": original}
    warnings: list[str] = []

    thumbnail = original.copy()
    thumbnail.thumbnail((768, 768))
    views["global_thumbnail"] = thumbnail

    if config.use_canny_edge_map:
        try:
            import cv2
            import numpy as np

            grayscale = cv2.cvtColor(np.asarray(original), cv2.COLOR_RGB2GRAY)
            canny = cv2.Canny(grayscale, 60, 180)
            views["edge_map"] = Image.fromarray(canny).convert("RGB")
        except ImportError:
            grayscale = ImageOps.grayscale(original)
            views["edge_map"] = ImageOps.autocontrast(
                grayscale.filter(ImageFilter.FIND_EDGES)
            ).convert("RGB")
            warnings.append("OpenCV is unavailable; edge prior uses Pillow FIND_EDGES")

    if config.use_ocr_overlay:
        overlay = original.copy()
        draw = ImageDraw.Draw(overlay)
        for item in evidence:
            if item.kind not in {"ocr_token", "vector_text"} or item.bbox is None:
                continue
            scaled_box = (
                item.bbox[0] * scale_x,
                item.bbox[1] * scale_y,
                item.bbox[2] * scale_x,
                item.bbox[3] * scale_y,
            )
            draw.rectangle(scaled_box, outline=(40, 120, 255), width=2)
            if item.text:
                draw.text(
                    (scaled_box[0], max(0, scaled_box[1] - 12)),
                    item.text[:40],
                    fill=(20, 80, 220),
                )
        views["ocr_overlay"] = overlay

    if config.use_hough_line_map or config.use_arrow_overlay:
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
            line_map = np.full_like(array, 255)
            arrow_overlay = array.copy()
            if lines is not None:
                for x1, y1, x2, y2 in lines[:, 0]:
                    cv2.line(line_map, (x1, y1), (x2, y2), (0, 0, 0), 2)
                    cv2.arrowedLine(
                        arrow_overlay, (x1, y1), (x2, y2), (255, 80, 30), 2, tipLength=0.08
                    )
            if config.use_hough_line_map:
                views["hough_line_map"] = Image.fromarray(line_map)
            if config.use_arrow_overlay:
                views["arrow_overlay"] = Image.fromarray(arrow_overlay)
        except ImportError:
            warnings.append("OpenCV is unavailable; Hough and arrow priors were omitted")

    if config.use_tiled_images and max(original.size) > config.tile_size:
        step = config.tile_size - config.tile_overlap
        tile_number = 0
        for top in range(0, original.height, step):
            for left in range(0, original.width, step):
                if len(views) >= config.max_views:
                    break
                right = min(original.width, left + config.tile_size)
                bottom = min(original.height, top + config.tile_size)
                if right - left < 64 or bottom - top < 64:
                    continue
                tile_number += 1
                views[f"tile_{tile_number}"] = original.crop((left, top, right, bottom))
            if len(views) >= config.max_views:
                break

    return dict(list(views.items())[: config.max_views]), warnings
