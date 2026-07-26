"""Shared bounds and validation for generated render artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image

MAX_RENDER_BYTES = 16_000_000
MAX_PREVIEW_DIMENSION = 8_192
MAX_PREVIEW_PIXELS = 50_000_000
MAX_RENDERED_SVG_NODES = 20_000
MAX_RENDERED_SVG_TEXT_CHARS = 1_000_000
MAX_RENDERED_SVG_PATHS = 10_000
MAX_RENDERED_SVG_PATH_DATA_CHARS = 4_000_000
MAX_RENDER_BASE64_CHARS = 4 * ((MAX_RENDER_BYTES + 2) // 3)


@dataclass(frozen=True, slots=True)
class RenderArtifactLimits:
    """Per-request browser limits, bounded by the publication artifact policy."""

    max_svg_bytes: int = MAX_RENDER_BYTES
    max_png_bytes: int = MAX_RENDER_BYTES
    max_dimension: int = MAX_PREVIEW_DIMENSION
    max_pixels: int = MAX_PREVIEW_PIXELS
    max_svg_nodes: int = MAX_RENDERED_SVG_NODES
    max_svg_text_chars: int = MAX_RENDERED_SVG_TEXT_CHARS
    max_svg_paths: int = MAX_RENDERED_SVG_PATHS
    max_svg_path_data_chars: int = MAX_RENDERED_SVG_PATH_DATA_CHARS

    def __post_init__(self) -> None:
        ceilings = {
            "max_svg_bytes": MAX_RENDER_BYTES,
            "max_png_bytes": MAX_RENDER_BYTES,
            "max_dimension": MAX_PREVIEW_DIMENSION,
            "max_pixels": MAX_PREVIEW_PIXELS,
            "max_svg_nodes": MAX_RENDERED_SVG_NODES,
            "max_svg_text_chars": MAX_RENDERED_SVG_TEXT_CHARS,
            "max_svg_paths": MAX_RENDERED_SVG_PATHS,
            "max_svg_path_data_chars": MAX_RENDERED_SVG_PATH_DATA_CHARS,
        }
        for field_name, ceiling in ceilings.items():
            value = getattr(self, field_name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{field_name} must be between 1 and {ceiling}")

    def worker_payload(self) -> dict[str, int]:
        """Return the exact JSON protocol object consumed by the browser worker."""

        return {
            "maxSvgBytes": self.max_svg_bytes,
            "maxPngBytes": self.max_png_bytes,
            "maxDimension": self.max_dimension,
            "maxPixels": self.max_pixels,
            "maxSvgNodes": self.max_svg_nodes,
            "maxSvgTextChars": self.max_svg_text_chars,
            "maxSvgPaths": self.max_svg_paths,
            "maxSvgPathDataChars": self.max_svg_path_data_chars,
        }


def png_inspection_error(payload: bytes) -> str | None:
    """Return a fail-closed diagnostic for an unusable generated PNG."""

    if type(payload) is not bytes:
        return "PNG artifact is not plain bytes"
    if not payload or len(payload) > MAX_RENDER_BYTES:
        return "PNG artifact exceeds the byte limit or is empty"
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "rendered preview is not a PNG artifact"
    try:
        with Image.open(BytesIO(payload)) as preview:
            if preview.format != "PNG":
                return "rendered preview is not PNG encoded"
            if (
                max(preview.size) > MAX_PREVIEW_DIMENSION
                or preview.width * preview.height > MAX_PREVIEW_PIXELS
            ):
                return "preview dimensions exceed the pixel budget"
            preview.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        return f"PNG inspection failed: {type(exc).__name__}: {exc}"
    return None
