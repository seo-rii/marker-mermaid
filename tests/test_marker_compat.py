from __future__ import annotations

import importlib.metadata

import pytest

from marker_mermaid.models import MermaidCandidate, ReconstructionResult


@pytest.mark.integration
def test_marker_1102_processor_order():
    if importlib.metadata.version("marker-pdf") != "1.10.2":
        pytest.skip("compatibility smoke is pinned to marker-pdf 1.10.2")
    from marker.processors.blank_page import BlankPageProcessor
    from marker.processors.reference import ReferenceProcessor

    from marker_mermaid.marker_integration import (
        MarkerMermaidPdfConverter,
        MermaidCandidateDiscoveryProcessor,
        MermaidDiagramProcessor,
    )

    processors = MarkerMermaidPdfConverter.default_processors
    assert processors.index(ReferenceProcessor) < processors.index(
        MermaidCandidateDiscoveryProcessor
    )
    assert processors.index(MermaidCandidateDiscoveryProcessor) < processors.index(
        MermaidDiagramProcessor
    )
    assert processors.index(MermaidDiagramProcessor) < processors.index(BlankPageProcessor)


@pytest.mark.integration
def test_marker_renderer_keeps_original_before_one_mermaid_block(monkeypatch):
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer
    from marker.schema import BlockTypes

    from marker_mermaid.marker_integration import MermaidMarkdownRenderer

    candidate = MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type="flowchart",
        mermaid_code='flowchart LR\n    A["Start"] --> B["End"]\n',
        syntax_valid=True,
        render_valid=True,
        aggregate_score=0.8,
    )
    result = ReconstructionResult(
        source_id="_page_0_ComplexRegion_1",
        source_image_name="_page_0_ComplexRegion_1.jpeg",
        selected=candidate,
        grade="B",
        publish=True,
        review_required=False,
        status="success",
    )

    class Block:
        def get_internal_metadata(self, key):
            return {
                "source_id": result.source_id,
                "status": "success",
                "selected_candidate_id": candidate.candidate_id,
                "result": result,
            }

    class Document:
        def contained_blocks(self, block_types):
            return [Block()]

    monkeypatch.setattr(
        MarkdownRenderer,
        "__call__",
        lambda self, document: MarkdownOutput(
            markdown="![](_page_0_ComplexRegion_1.jpeg)", images={}, metadata={}
        ),
    )
    renderer = MermaidMarkdownRenderer()
    rendered = renderer(Document())
    assert BlockTypes.ComplexRegion in renderer.image_blocks
    assert rendered.markdown.index("images/_page_0_ComplexRegion_1.jpeg") < (
        rendered.markdown.index("```mermaid")
    )
    assert rendered.markdown.count("```mermaid") == 1


@pytest.mark.integration
def test_marker_processor_uses_geometry_without_an_llm(fake_runtime):
    from marker_mermaid.marker_integration import MermaidDiagramProcessor

    processor = MermaidDiagramProcessor(llm_service=None, runtime=fake_runtime)

    assert [engine.name for engine in processor.pipeline.engines] == ["geometry"]


@pytest.mark.integration
def test_marker_ocr_evidence_uses_exact_block_crop_coordinates():
    from marker_mermaid.marker_integration import MermaidDiagramProcessor

    class Identifier:
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return self.value

        def to_path(self):
            return self.value.replace("/", "_")

    class Polygon:
        def __init__(self, bbox):
            self.bbox = bbox

    class Span:
        id = Identifier("/page/0/Span/1")
        polygon = Polygon((20, 40, 60, 80))
        text = "Label"

    class Block:
        id = Identifier("/page/0/Figure/1")
        polygon = Polygon((10, 20, 110, 220))

        def contained_blocks(self, document, block_types):
            return [Span()]

        def raw_text(self, document):
            return "Label"

    evidence, texts = MermaidDiagramProcessor._ocr_evidence(Block(), object(), (200, 400))

    assert evidence[1].bbox == (20.0, 40.0, 100.0, 120.0)
    assert texts == ["Label"]
