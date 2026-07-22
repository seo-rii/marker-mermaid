from __future__ import annotations

import math
from dataclasses import asdict

import pytest
from PIL import Image

from marker_mermaid.discovery import DiscoveredSource, SourceFragment
from marker_mermaid.source_assembly import SourceAssemblyError, assemble_discovered_source


def _source(
    *fragments: SourceFragment,
    source_id: str = "source-1",
    kind: str = "merged",
) -> DiscoveredSource:
    return DiscoveredSource(
        source_id=source_id,
        kind=kind,
        fragments=list(fragments),
        confidence=0.9,
    )


def _fragment(
    fragment_id: str,
    size: tuple[int, int],
    *,
    page_id: int = 1,
    page_bbox: tuple[float, float, float, float] | None = None,
    crop_bbox: tuple[float, float, float, float] | None = None,
    offset: tuple[float, float] = (0, 0),
) -> SourceFragment:
    return SourceFragment(
        fragment_id=fragment_id,
        page_id=page_id,
        source_block_ids=[f"block-{fragment_id}"],
        page_bbox=page_bbox,
        crop_bbox=crop_bbox,
        image_size=size,
        canvas_offset=offset,
    )


def test_original_source_is_normalized_to_rgb_without_storing_image_in_metadata():
    fragment = _fragment("original", (3, 2))
    source = _source(fragment, kind="original")
    rgba = Image.new("RGBA", (3, 2), (255, 0, 0, 128))

    result = assemble_discovered_source(source, {"original": rgba})

    assert result.image.mode == "RGB"
    assert result.image.size == (3, 2)
    assert result.image.getpixel((1, 1)) == (255, 127, 127)
    assert result.metadata.canvas_size == (3, 2)
    assert "image" not in asdict(result.metadata)
    assert "bytes" not in source.model_dump_json()


def test_panel_crop_uses_outward_rasterization_and_returns_affine_metadata():
    image = Image.new("RGB", (8, 6), "white")
    for x in range(8):
        for y in range(6):
            image.putpixel((x, y), (x * 20, y * 20, 0))
    fragment = _fragment("panel", image.size, crop_bbox=(1.2, 2.0, 5.1, 5.0))

    result = assemble_discovered_source(_source(fragment, kind="panel"), {"panel": image})
    placement = result.metadata.placement_by_fragment_id()["panel"]

    assert result.image.size == (5, 3)
    assert result.image.getpixel((0, 0)) == image.getpixel((1, 2))
    assert result.image.getpixel((4, 2)) == image.getpixel((5, 4))
    assert placement.source_crop_bbox == (1, 2, 6, 5)
    assert placement.canvas_bbox == (0, 0, 5, 3)
    assert placement.source_to_canvas == (1.0, 0.0, -1.0, 0.0, 1.0, -2.0)


def test_page_to_canvas_affine_is_serialized_for_attribution():
    fragment = _fragment(
        "panel",
        (200, 100),
        page_bbox=(50, 10, 100, 60),
        crop_bbox=(100, 0, 200, 100),
    )

    result = assemble_discovered_source(
        _source(fragment, kind="panel"),
        {"panel": Image.new("RGB", (200, 100), "white")},
    )
    placement = result.metadata.placement_by_fragment_id()["panel"]

    assert placement.page_to_canvas == (2.0, 0.0, -100.0, 0.0, 2.0, -20.0)
    dumped = result.metadata.model_dump()
    assert dumped["placements"][0]["page_bbox"] == [50.0, 10.0, 100.0, 60.0]
    assert dumped["placements"][0]["page_to_canvas"] == [
        2.0,
        0.0,
        -100.0,
        0.0,
        2.0,
        -20.0,
    ]


@pytest.mark.parametrize("non_finite", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["page_bbox", "crop_bbox"])
def test_source_fragment_rejects_non_finite_bboxes(field, non_finite):
    kwargs = {field: (0, 0, non_finite, 10)}

    with pytest.raises(ValueError, match="finite coordinates"):
        _fragment("invalid", (10, 10), **kwargs)


def test_merged_source_uses_input_order_as_z_order_and_alpha_composites():
    back = Image.new("RGB", (4, 3), "red")
    front = Image.new("RGBA", (3, 2), (0, 0, 255, 128))
    source = _source(
        _fragment("back", back.size),
        _fragment("front", front.size, offset=(2, 1)),
    )

    result = assemble_discovered_source(source, {"back": back, "front": front})

    assert result.image.size == (5, 3)
    assert result.image.getpixel((0, 0)) == (255, 0, 0)
    assert result.image.getpixel((2, 1)) == (127, 0, 128)
    assert result.image.getpixel((4, 2)) == (127, 127, 255)
    assert [item.z_index for item in result.metadata.placements] == [0, 1]


def test_multi_page_source_preserves_white_gap_and_page_placements():
    top = Image.new("RGB", (4, 2), "black")
    bottom = Image.new("RGB", (4, 3), "blue")
    source = _source(
        _fragment("page-1", top.size, page_id=1),
        _fragment("page-2", bottom.size, page_id=2, offset=(0, 4)),
        source_id="continued",
    )

    result = assemble_discovered_source(source, {"page-1": top, "page-2": bottom})

    assert result.image.size == (4, 7)
    assert result.image.getpixel((1, 2)) == (255, 255, 255)
    assert result.image.getpixel((1, 4)) == (0, 0, 255)
    assert result.metadata.placements[1].page_id == 2
    assert result.metadata.placements[1].canvas_bbox == (0, 4, 4, 7)


def test_negative_offset_expands_bounds_and_updates_source_to_canvas_transform():
    left = Image.new("RGB", (2, 2), "green")
    right = Image.new("RGB", (2, 2), "yellow")
    source = _source(
        _fragment("left", left.size, offset=(-1.5, -0.5)),
        _fragment("right", right.size, offset=(1.5, 0.5)),
    )

    result = assemble_discovered_source(source, {"left": left, "right": right})
    placements = result.metadata.placement_by_fragment_id()

    assert result.metadata.virtual_bounds == (-2, -1, 4, 3)
    assert result.image.size == (6, 4)
    assert placements["left"].canvas_bbox == (0, 0, 2, 2)
    assert placements["right"].canvas_bbox == (4, 2, 6, 4)
    assert placements["right"].source_to_canvas == (1.0, 0.0, 4.0, 0.0, 1.0, 2.0)


@pytest.mark.parametrize(
    ("max_output_size", "max_pixels", "message"),
    [
        ((9, 20), 1_000, "exceeds maximum dimensions"),
        ((20, 20), 99, "above budget"),
    ],
)
def test_output_limits_are_checked_before_canvas_allocation(
    max_output_size: tuple[int, int], max_pixels: int, message: str
):
    fragment = _fragment("large", (10, 10))

    with pytest.raises(SourceAssemblyError, match=message):
        assemble_discovered_source(
            _source(fragment),
            {"large": Image.new("RGB", (10, 10))},
            max_output_size=max_output_size,
            max_pixels=max_pixels,
        )


def test_missing_or_mismatched_fragment_image_is_rejected():
    fragment = _fragment("required", (3, 2))
    source = _source(fragment)

    with pytest.raises(SourceAssemblyError, match="missing image"):
        assemble_discovered_source(source, {})
    with pytest.raises(SourceAssemblyError, match="image size mismatch"):
        assemble_discovered_source(source, {"required": Image.new("RGB", (2, 2))})


def test_crop_must_stay_within_the_registered_source_image():
    fragment = _fragment("panel", (4, 4), crop_bbox=(-1, 0, 3, 3))

    with pytest.raises(SourceAssemblyError, match="outside its image"):
        assemble_discovered_source(_source(fragment), {"panel": Image.new("RGB", (4, 4))})
