"""Shared bounds and validation for generated render artifacts."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

MAX_RENDER_BYTES = 16_000_000
MAX_PREVIEW_DIMENSION = 8_192
MAX_PREVIEW_PIXELS = 50_000_000


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
