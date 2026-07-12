from __future__ import annotations

from PIL import Image

from marker_mermaid.engines import MarkerStructuredVLMEngine
from marker_mermaid.models import DiagramTypePrediction, EngineObservation, VisualEvidence
from marker_mermaid.protocols import SourceContext


def test_marker_vlm_prompt_receives_prior_geometry_evidence():
    captured = {}

    def service(**kwargs):
        captured.update(kwargs)
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
        ).model_dump(mode="json")

    context = SourceContext(
        source_id="figure-1",
        source_block_ids=["/page/0/Figure/1"],
        source_image_name="figure.png",
        image=Image.new("RGB", (20, 20), "white"),
        views={"original": Image.new("RGB", (20, 20), "white")},
        evidence=[
            VisualEvidence(
                id="geometry-contour-001",
                kind="contour",
                bbox=(1, 1, 10, 10),
                score=0.9,
            )
        ],
        ocr_texts=["Label"],
    )

    result = MarkerStructuredVLMEngine(service).observe(context)

    assert result.prediction.candidates == ["flowchart"]
    assert "geometry-contour-001" in captured["prompt"]
    assert "Label" in captured["prompt"]
    assert captured["image"] == [context.views["original"]]
