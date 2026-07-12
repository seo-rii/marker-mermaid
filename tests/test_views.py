from __future__ import annotations

from PIL import Image

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
