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
