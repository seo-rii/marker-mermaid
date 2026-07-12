"""Assemble discovered source fragments into a deterministic virtual canvas.

Discovery registry models contain only coordinates and identifiers.  Image objects
remain in a caller-owned mapping and are joined with that metadata here, immediately
before visual processing.  This keeps registry JSON small and prevents accidental
serialization of source bytes.

Coordinates are rasterized deliberately: crop edges use ``floor`` for the leading
edge and ``ceil`` for the trailing edge, while canvas offsets are rounded to the
nearest integer with ties away from zero.  Returned affine transforms describe the
resulting source-image-to-output-canvas mapping, not the pre-rasterized floats.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PIL import Image

from marker_mermaid.discovery import DiscoveredSource, SourceFragment

IntBBox = tuple[int, int, int, int]
AffineTransform = tuple[float, float, float, float, float, float]

DEFAULT_MAX_OUTPUT_SIZE = (32_768, 32_768)
DEFAULT_MAX_PIXELS = 100_000_000


class SourceAssemblyError(ValueError):
    """Raised when source metadata cannot be assembled safely."""


@dataclass(frozen=True, slots=True)
class FragmentPlacement:
    """Serializable placement and provenance metadata for one fragment.

    ``source_to_canvas`` uses the six-coefficient convention::

        canvas_x = a * source_x + b * source_y + c
        canvas_y = d * source_x + e * source_y + f

    The transform is translation-only today, but the affine representation leaves
    room for source rotation or scaling in later discovery stages.
    """

    fragment_id: str
    page_id: int
    source_block_ids: tuple[str, ...]
    source_crop_bbox: IntBBox
    canvas_bbox: IntBBox
    source_to_canvas: AffineTransform
    page_bbox: tuple[float, float, float, float] | None
    page_to_canvas: AffineTransform | None
    z_index: int


@dataclass(frozen=True, slots=True)
class SourceAssemblyMetadata:
    """Image-free metadata describing a completed canvas assembly."""

    source_id: str
    canvas_size: tuple[int, int]
    virtual_bounds: IntBBox
    background: str
    output_mode: str
    placements: tuple[FragmentPlacement, ...]

    def placement_by_fragment_id(self) -> dict[str, FragmentPlacement]:
        """Return placements keyed by the registry fragment identifier."""

        return {placement.fragment_id: placement for placement in self.placements}

    def model_dump(self) -> dict[str, Any]:
        """Return a JSON-safe representation without serializing image pixels."""

        return {
            "source_id": self.source_id,
            "canvas_size": list(self.canvas_size),
            "virtual_bounds": list(self.virtual_bounds),
            "background": self.background,
            "output_mode": self.output_mode,
            "placements": [
                {
                    "fragment_id": placement.fragment_id,
                    "page_id": placement.page_id,
                    "source_block_ids": list(placement.source_block_ids),
                    "source_crop_bbox": list(placement.source_crop_bbox),
                    "canvas_bbox": list(placement.canvas_bbox),
                    "source_to_canvas": list(placement.source_to_canvas),
                    "page_bbox": list(placement.page_bbox) if placement.page_bbox else None,
                    "page_to_canvas": (
                        list(placement.page_to_canvas) if placement.page_to_canvas else None
                    ),
                    "z_index": placement.z_index,
                }
                for placement in self.placements
            ],
        }


@dataclass(frozen=True, slots=True)
class AssembledSource:
    """A rendered RGB canvas and its separately serializable metadata."""

    image: Image.Image
    metadata: SourceAssemblyMetadata


@dataclass(frozen=True, slots=True)
class _PreparedFragment:
    fragment: SourceFragment
    image: Image.Image
    crop_bbox: IntBBox
    virtual_offset: tuple[int, int]
    z_index: int


def assemble_discovered_source(
    source: DiscoveredSource,
    images: Mapping[str, Image.Image],
    *,
    max_output_size: tuple[int, int] = DEFAULT_MAX_OUTPUT_SIZE,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> AssembledSource:
    """Assemble ``source`` fragments on an RGB canvas in registry order.

    ``images`` is an external fragment-id registry.  Every discovered fragment must
    have one matching Pillow image whose dimensions equal ``SourceFragment.image_size``;
    extra images are harmless.  ``crop_bbox`` is applied in source-image coordinates,
    then the crop is placed at ``canvas_offset``.  Later entries are composited over
    earlier entries, making input order the deterministic z-order.

    The output canvas includes the virtual origin, so positive offsets retain their
    leading white margin.  Negative offsets expand the virtual bounds and are shifted
    into non-negative output coordinates.  Allocation occurs only after dimension and
    pixel-budget validation.
    """

    _validate_limits(max_output_size, max_pixels)
    prepared = tuple(
        _prepare_fragment(fragment, images, z_index=index)
        for index, fragment in enumerate(source.fragments)
    )
    virtual_bounds = _virtual_bounds(prepared)
    width = virtual_bounds[2] - virtual_bounds[0]
    height = virtual_bounds[3] - virtual_bounds[1]
    _check_output_budget(
        source_id=source.source_id,
        output_size=(width, height),
        max_output_size=max_output_size,
        max_pixels=max_pixels,
    )

    # An RGBA working canvas preserves correct alpha blending at overlaps.  The final
    # conversion guarantees a stable RGB result and a white background for downstream
    # CV engines regardless of the input modes.
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    placements: list[FragmentPlacement] = []
    origin_x, origin_y = virtual_bounds[0], virtual_bounds[1]
    for item in prepared:
        virtual_x, virtual_y = item.virtual_offset
        output_x, output_y = virtual_x - origin_x, virtual_y - origin_y
        layer = item.image.crop(item.crop_bbox).convert("RGBA")
        canvas.alpha_composite(layer, (output_x, output_y))
        crop_left, crop_top, _, _ = item.crop_bbox
        canvas_bbox = (
            output_x,
            output_y,
            output_x + layer.width,
            output_y + layer.height,
        )
        placements.append(
            FragmentPlacement(
                fragment_id=item.fragment.fragment_id,
                page_id=item.fragment.page_id,
                source_block_ids=tuple(item.fragment.source_block_ids),
                source_crop_bbox=item.crop_bbox,
                canvas_bbox=canvas_bbox,
                source_to_canvas=(
                    1.0,
                    0.0,
                    float(output_x - crop_left),
                    0.0,
                    1.0,
                    float(output_y - crop_top),
                ),
                page_bbox=item.fragment.page_bbox,
                page_to_canvas=_page_to_canvas(item.fragment, canvas_bbox),
                z_index=item.z_index,
            )
        )

    metadata = SourceAssemblyMetadata(
        source_id=source.source_id,
        canvas_size=(width, height),
        virtual_bounds=virtual_bounds,
        background="#ffffff",
        output_mode="RGB",
        placements=tuple(placements),
    )
    return AssembledSource(image=canvas.convert("RGB"), metadata=metadata)


def _page_to_canvas(
    fragment: SourceFragment,
    canvas_bbox: IntBBox,
) -> AffineTransform | None:
    page_bbox = fragment.page_bbox
    if page_bbox is None:
        return None
    page_width = page_bbox[2] - page_bbox[0]
    page_height = page_bbox[3] - page_bbox[1]
    scale_x = (canvas_bbox[2] - canvas_bbox[0]) / page_width
    scale_y = (canvas_bbox[3] - canvas_bbox[1]) / page_height
    return (
        scale_x,
        0.0,
        canvas_bbox[0] - scale_x * page_bbox[0],
        0.0,
        scale_y,
        canvas_bbox[1] - scale_y * page_bbox[1],
    )


def _prepare_fragment(
    fragment: SourceFragment,
    images: Mapping[str, Image.Image],
    *,
    z_index: int,
) -> _PreparedFragment:
    try:
        image = images[fragment.fragment_id]
    except KeyError as error:
        raise SourceAssemblyError(f"missing image for fragment {fragment.fragment_id!r}") from error
    if not isinstance(image, Image.Image):
        raise SourceAssemblyError(
            f"image for fragment {fragment.fragment_id!r} is not a Pillow image"
        )
    if image.size != fragment.image_size:
        raise SourceAssemblyError(
            f"image size mismatch for fragment {fragment.fragment_id!r}: "
            f"registry={fragment.image_size}, actual={image.size}"
        )

    crop_bbox = _raster_crop_bbox(fragment, image.size)
    virtual_offset = tuple(_round_offset(value) for value in fragment.canvas_offset)
    return _PreparedFragment(
        fragment=fragment,
        image=image,
        crop_bbox=crop_bbox,
        virtual_offset=virtual_offset,
        z_index=z_index,
    )


def _raster_crop_bbox(fragment: SourceFragment, image_size: tuple[int, int]) -> IntBBox:
    bbox = fragment.crop_bbox or (0.0, 0.0, float(image_size[0]), float(image_size[1]))
    if not all(math.isfinite(value) for value in bbox):
        raise SourceAssemblyError(f"crop bbox for fragment {fragment.fragment_id!r} must be finite")
    left = math.floor(bbox[0])
    top = math.floor(bbox[1])
    right = math.ceil(bbox[2])
    bottom = math.ceil(bbox[3])
    if left < 0 or top < 0 or right > image_size[0] or bottom > image_size[1]:
        raise SourceAssemblyError(
            f"crop bbox for fragment {fragment.fragment_id!r} is outside its image: "
            f"{(left, top, right, bottom)} not within {image_size}"
        )
    if right <= left or bottom <= top:
        raise SourceAssemblyError(
            f"rasterized crop for fragment {fragment.fragment_id!r} has no area"
        )
    return left, top, right, bottom


def _round_offset(value: float) -> int:
    if not math.isfinite(value):
        raise SourceAssemblyError("canvas offsets must be finite")
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def _virtual_bounds(prepared: tuple[_PreparedFragment, ...]) -> IntBBox:
    # DiscoveredSource guarantees at least one fragment. Keep (0, 0) inside the
    # virtual canvas to preserve intentional margins from positive canvas offsets.
    left = min(0, *(item.virtual_offset[0] for item in prepared))
    top = min(0, *(item.virtual_offset[1] for item in prepared))
    right = max(
        0,
        *(item.virtual_offset[0] + item.crop_bbox[2] - item.crop_bbox[0] for item in prepared),
    )
    bottom = max(
        0,
        *(item.virtual_offset[1] + item.crop_bbox[3] - item.crop_bbox[1] for item in prepared),
    )
    return left, top, right, bottom


def _validate_limits(max_output_size: tuple[int, int], max_pixels: int) -> None:
    if len(max_output_size) != 2 or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in max_output_size
    ):
        raise ValueError("max_output_size must contain two positive integers")
    if isinstance(max_pixels, bool) or not isinstance(max_pixels, int) or max_pixels <= 0:
        raise ValueError("max_pixels must be a positive integer")


def _check_output_budget(
    *,
    source_id: str,
    output_size: tuple[int, int],
    max_output_size: tuple[int, int],
    max_pixels: int,
) -> None:
    width, height = output_size
    if width <= 0 or height <= 0:
        raise SourceAssemblyError(f"source {source_id!r} produced an empty canvas")
    if width > max_output_size[0] or height > max_output_size[1]:
        raise SourceAssemblyError(
            f"source {source_id!r} output {output_size} exceeds maximum dimensions "
            f"{max_output_size}"
        )
    pixels = width * height
    if pixels > max_pixels:
        raise SourceAssemblyError(
            f"source {source_id!r} output requires {pixels} pixels, above budget {max_pixels}"
        )
