from __future__ import annotations

from PIL import Image

from marker_mermaid.engines import SYSTEM_PROMPT, MarkerStructuredVLMEngine
from marker_mermaid.models import DiagramTypePrediction, EngineObservation, VisualEvidence
from marker_mermaid.protocols import SourceContext


def test_system_prompt_requires_exact_scene_ids_and_prior_evidence_for_flow_nodes():
    prompt = " ".join(SYSTEM_PROMPT.split())

    assert "For flowchart and generic_network typed candidates" in prompt
    assert "exact IDs of matching scene_ir.elements from this same response" in prompt
    assert "do not rename, normalize, or invent node IDs" in prompt
    assert (
        "Every semantic typed node must include evidence_ids copied from supplied Prior evidence"
        in prompt
    )
    assert "reuse at least one of the same Prior evidence IDs" in prompt
    assert "Never self-declare or synthesize evidence IDs" in prompt


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

    result = MarkerStructuredVLMEngine(
        service,
        enabled_types={"flowchart", "architecture"},
    ).observe(context)

    assert result.prediction.candidates == ["flowchart"]
    assert "geometry-contour-001" in captured["prompt"]
    assert "Label" in captured["prompt"]
    assert "- flowchart: nodes:list" in captured["prompt"]
    assert "- architecture: services:list" in captured["prompt"]
    assert "- packet:" not in captured["prompt"]
    assert '"name": "original"' in captured["prompt"]
    assert '"width": 20' in captured["prompt"]
    assert "exact IDs of matching\nscene_ir.elements from this same response" in captured["prompt"]
    assert "evidence_ids copied from supplied Prior evidence" in captured["prompt"]
    assert captured["image"] == [context.views["original"]]
