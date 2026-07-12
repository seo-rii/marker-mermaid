from __future__ import annotations

from PIL import Image, ImageDraw

import marker_mermaid.page_detector as detector
from marker_mermaid.page_detector import detect_page_diagram_regions, propose_page_diagrams


def _diagram_page() -> Image.Image:
    image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 100, 210, 180), outline="black", width=4)
    draw.rectangle((390, 100, 540, 180), outline="black", width=4)
    draw.rectangle((240, 290, 380, 370), outline="black", width=4)
    draw.line((210, 140, 390, 140), fill="black", width=4)
    draw.line((465, 180, 310, 290), fill="black", width=4)
    draw.polygon(((390, 140), (375, 132), (375, 148)), fill="black")
    draw.polygon(((310, 290), (315, 274), (326, 287)), fill="black")
    return image


def test_proposes_evidence_backed_region_in_page_coordinates() -> None:
    regions = propose_page_diagrams(_diagram_page(), use_opencv=False)

    assert len(regions) == 1
    region = regions[0]
    assert region.region_id == "page-diagram-001"
    assert region.bbox[0] <= 80 and region.bbox[1] <= 100
    assert region.bbox[2] >= 540 and region.bbox[3] >= 370
    assert region.confidence >= 0.6
    assert region.component_count >= 1
    assert 0 < region.edge_density < 0.2
    assert "multi_axis_structure" in region.signals


def test_occupied_block_prevents_duplicate_proposal() -> None:
    result = detect_page_diagram_regions(
        _diagram_page(),
        occupied_bboxes=[(70, 90, 550, 380)],
        use_opencv=False,
    )

    assert result.regions == []


def test_rejects_text_lines_and_busy_photo_like_noise() -> None:
    page = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(page)
    for y in range(40, 230, 18):
        draw.line((40, y, 350 + (y % 3) * 25, y), fill="black", width=2)
    for y in range(280, 430, 5):
        for x in range(390, 590, 5):
            color = "black" if (x // 5 + y // 5) % 2 else "white"
            draw.rectangle((x, y, x + 4, y + 4), fill=color)

    assert propose_page_diagrams(page, use_opencv=False) == []


def test_downsampling_restores_original_page_coordinates() -> None:
    page = _diagram_page().resize((1280, 960), Image.Resampling.NEAREST)

    region = detect_page_diagram_regions(
        page,
        use_opencv=False,
        max_processing_dimension=320,
    ).regions[0]

    assert region.bbox[0] <= 160
    assert region.bbox[2] >= 1080
    assert region.bbox[3] >= 740


def test_optional_opencv_import_failure_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(detector, "_load_opencv", lambda: None)

    result = detect_page_diagram_regions(_diagram_page(), use_opencv=True)

    assert result.edge_backend == "pillow"
    assert result.regions
    assert result.warnings == ["OpenCV is unavailable; used Pillow page detector"]


def test_output_is_deterministic_and_does_not_mutate_inputs() -> None:
    image = _diagram_page()
    before = image.tobytes()
    occupied = [(0.0, 0.0, 20.0, 20.0)]

    first = detect_page_diagram_regions(image, occupied, use_opencv=False)
    second = detect_page_diagram_regions(image, occupied, use_opencv=False)

    assert first == second
    assert image.tobytes() == before
    assert occupied == [(0.0, 0.0, 20.0, 20.0)]


def test_invalid_limits_are_rejected() -> None:
    image = Image.new("L", (100, 100), "white")

    for kwargs in (
        {"max_processing_dimension": 10},
        {"max_regions": 0},
        {"min_region_area_fraction": 0},
        {"merge_gap_fraction": 0.2},
        {"nms_iou_threshold": 1.2},
    ):
        try:
            detect_page_diagram_regions(image, use_opencv=False, **kwargs)
        except ValueError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError(f"expected ValueError for {kwargs}")
