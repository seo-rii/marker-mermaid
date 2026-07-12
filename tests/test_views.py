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
