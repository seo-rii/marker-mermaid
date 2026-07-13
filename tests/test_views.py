from __future__ import annotations

from PIL import Image, ImageDraw

from marker_mermaid.config import MermaidConfig
from marker_mermaid.models import VisualEvidence
from marker_mermaid.views import build_visual_priors


def test_ocr_overlay_rescales_evidence_with_large_source():
    image = Image.new("RGB", (400, 200), "white")
    evidence = [VisualEvidence(id="ocr-1", kind="ocr_token", bbox=(200, 100, 300, 150), text="X")]
    config = MermaidConfig(
        max_image_dimension=100,
        use_canny_edge_map=False,
        use_hough_line_map=False,
        use_arrow_overlay=False,
        use_tiled_images=False,
    )
    views, warnings = build_visual_priors(image, evidence, config)
    assert warnings == []
    assert views["original"].size == (100, 50)
    assert views["ocr_overlay"].getpixel((50, 25)) == (40, 120, 255)


def test_arrow_and_vector_overlays_use_detected_evidence_only():
    image = Image.new("RGB", (100, 60), "white")
    evidence = [
        VisualEvidence(
            id="vector-text-1",
            kind="vector_text",
            bbox=(10, 10, 30, 20),
            text="API",
        ),
        VisualEvidence(
            id="geometry-arrowhead-1",
            kind="arrowhead",
            bbox=(70, 20, 80, 30),
        ),
    ]
    config = MermaidConfig(
        use_canny_edge_map=False,
        use_hough_line_map=False,
        use_tiled_images=False,
        max_views=8,
    )

    views, _warnings = build_visual_priors(image, evidence, config)

    assert views["vector_overlay"].getpixel((10, 10)) == (135, 65, 190)
    assert views["arrow_overlay"].getpixel((70, 20)) == (255, 80, 30)
    assert views["arrow_overlay"].getpixel((10, 10)) == (255, 255, 255)
    assert "color_cluster_map" in views


def test_generator_evidence_builds_all_non_empty_overlays_and_omits_empty_ones():
    image = Image.new("RGB", (120, 80), "white")
    evidence = (
        item
        for item in [
            VisualEvidence(id="ocr-1", kind="ocr_token", bbox=(5, 5, 20, 15), text="A"),
            VisualEvidence(id="geometry-contour-1", kind="contour", bbox=(30, 10, 60, 40)),
            VisualEvidence(id="arrow-1", kind="arrowhead", bbox=(80, 20, 90, 30)),
        ]
    )
    config = MermaidConfig(use_hough_line_map=False, use_tiled_images=False, max_views=10)

    views, _warnings = build_visual_priors(image, evidence, config, diagram_types=["flowchart"])

    assert "ocr_overlay" in views
    assert "arrow_overlay" in views
    assert "contour_overlay" in views
    assert "vector_overlay" not in views


def test_type_profile_changes_evidence_priority_deterministically():
    image = Image.new("RGB", (120, 80), "white")
    evidence = [
        VisualEvidence(id="ocr-1", kind="ocr_token", bbox=(5, 5, 20, 15), text="A"),
        VisualEvidence(id="shape-1", kind="contour", bbox=(30, 10, 60, 40)),
    ]
    config = MermaidConfig(
        use_canny_edge_map=False,
        use_hough_line_map=False,
        use_tiled_images=False,
        max_views=3,
    )

    architecture, _ = build_visual_priors(image, evidence, config, diagram_types=["architecture"])
    chart, _ = build_visual_priors(image, evidence, config, diagram_types=["xychart"])

    assert list(architecture) == ["original", "global_thumbnail", "contour_overlay"]
    assert list(chart) == ["original", "global_thumbnail", "ocr_overlay"]


def test_tiles_are_reserved_and_cropped_from_true_source_resolution():
    image = Image.new("RGB", (512, 256), "white")
    draw = ImageDraw.Draw(image)
    for x in range(image.width):
        draw.line((x, 0, x, image.height), fill=(x % 256, (x // 2) % 256, 0))
    config = MermaidConfig(
        max_image_dimension=64,
        tile_size=128,
        tile_overlap=32,
        max_views=8,
        use_canny_edge_map=False,
        use_hough_line_map=False,
        use_ocr_overlay=False,
        use_arrow_overlay=False,
        use_color_group_map=False,
        use_vector_primitives=False,
    )

    views, _warnings = build_visual_priors(image, [], config, diagram_types=["timeline"])

    tile_names = [name for name in views if name.startswith("tile_")]
    assert len(views) <= config.max_views
    assert tile_names == ["tile_1_x0_y0_x128_y128", "tile_2_x96_y0_x224_y128"]
    assert views[tile_names[0]].size == (128, 128)
    assert views[tile_names[0]].getpixel((100, 20)) == image.getpixel((100, 20))
    assert views["original"].size == (64, 32)


def test_grayscale_and_adaptive_threshold_are_available_as_explicit_priors():
    image = Image.new("RGB", (100, 60), (180, 120, 60))
    config = MermaidConfig(
        use_canny_edge_map=False,
        use_hough_line_map=False,
        use_ocr_overlay=False,
        use_arrow_overlay=False,
        use_color_group_map=False,
        use_vector_primitives=False,
        use_tiled_images=False,
        max_views=6,
    )

    views, _warnings = build_visual_priors(image, [], config, diagram_types=["packet"])

    assert "adaptive_threshold" in views
    assert "grayscale" in views
    red, green, blue = views["grayscale"].getpixel((10, 10))
    assert red == green == blue


def test_visual_priors_omit_optional_views_at_aggregate_pixel_budget(monkeypatch):
    monkeypatch.setattr("marker_mermaid.views.MAX_VLM_TOTAL_VIEW_PIXELS", 20_000)
    image = Image.new("RGB", (100, 100), "white")
    config = MermaidConfig(use_tiled_images=False, max_views=8)

    views, warnings = build_visual_priors(image, [], config)

    assert list(views) == ["original", "global_thumbnail"]
    assert sum(view.width * view.height for view in views.values()) == 20_000
    assert warnings == ["visual prior pixel budget omitted one or more optional views"]


def test_visual_priors_share_engine_view_dimension_and_pixel_boundaries():
    image = Image.new("RGB", (4_100, 10), "white")
    config = MermaidConfig(
        max_image_dimension=4_096,
        tile_size=4_096,
        tile_overlap=128,
        max_views=4,
    )

    views, _warnings = build_visual_priors(image, [], config)

    assert len(views) <= config.max_views
    assert all(view.width <= 4_096 and view.height <= 4_096 for view in views.values())
    assert all(view.width * view.height <= 16_777_216 for view in views.values())
    assert sum(view.width * view.height for view in views.values()) <= 33_554_432
