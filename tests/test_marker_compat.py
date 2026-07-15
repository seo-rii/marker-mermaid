from __future__ import annotations

import importlib.metadata
import json
import struct
import zlib
from io import BytesIO

import pytest
from PIL import Image

from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.models import MermaidCandidate, PromptBudgetNotice, ReconstructionResult
from marker_mermaid.pipeline import certify_publication_result
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.validation import CandidateValidator

_VALIDATED_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text>x</text></svg>'
)


def _seal_test_candidate(candidate: MermaidCandidate) -> MermaidCandidate:
    class Runtime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart-v2",
                svg=_VALIDATED_SVG,
                png=candidate.png,
            )

        def close(self):
            pass

    validator = CandidateValidator(Runtime(), SecurityProfile.STRICT)
    outcome = validator.validate(candidate.mermaid_code, 1)
    candidate.svg = outcome.runtime.svg
    candidate.png = outcome.runtime.png
    candidate.runtime_diagram_type = outcome.runtime.diagram_type
    validator.seal_candidate(candidate, outcome)
    return candidate


def _seal_test_result(result: ReconstructionResult) -> ReconstructionResult:
    assert result.selected is not None
    result.selected.scores = {"ocr_recall": 0.8}
    assert certify_publication_result(result, MermaidConfig())
    return result


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
def test_marker_1102_ollama_receives_fully_inlined_observation_schema(monkeypatch):
    if importlib.metadata.version("marker-pdf") != "1.10.2":
        pytest.skip("compatibility smoke is pinned to marker-pdf 1.10.2")
    from marker.services.ollama import OllamaService

    from marker_mermaid.engines import MarkerStructuredVLMEngine
    from marker_mermaid.protocols import SourceContext

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "prompt_eval_count": 1,
                "eval_count": 1,
                "response": json.dumps(
                    {
                        "prediction": {
                            "candidates": ["flowchart"],
                            "scores": [1.0],
                            "visual_signals": [],
                            "negative_signals": [],
                        },
                        "scene_ir": None,
                        "typed_candidates": [],
                        "direct_candidates": [],
                        "evidence": [],
                        "warnings": [],
                    }
                ),
            }

    def post(url, json, headers):
        captured.update(url=url, payload=json, headers=headers)
        return Response()

    monkeypatch.setattr("marker.services.ollama.requests.post", post)
    image = Image.new("RGB", (20, 20), "white")
    result = MarkerStructuredVLMEngine(
        OllamaService(),
        enabled_types={"flowchart"},
    ).observe(
        SourceContext(
            source_id="figure-1",
            source_block_ids=["/page/0/Figure/1"],
            source_image_name="figure.png",
            image=image,
            views={"original": image.copy()},
        )
    )

    format_schema = captured["payload"]["format"]
    schema_text = json.dumps(format_schema)
    assert '"$ref"' not in schema_text
    assert '"$defs"' not in schema_text
    assert format_schema["properties"]["prediction"]["properties"]["candidates"]
    assert result.prediction.candidates == ["flowchart"]


@pytest.mark.integration
def test_marker_renderer_keeps_original_before_one_mermaid_block(monkeypatch):
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer
    from marker.schema import BlockTypes

    from marker_mermaid.marker_integration import MermaidMarkdownRenderer

    candidate = _seal_test_candidate(
        MermaidCandidate(
            candidate_id="candidate-1",
            generation_method="typed_ir",
            diagram_type="flowchart",
            mermaid_code='flowchart LR\n    A["Start"] --> B["End"]\n',
            syntax_valid=True,
            render_valid=True,
            aggregate_score=0.8,
        )
    )
    result = _seal_test_result(
        ReconstructionResult(
            source_id="_page_0_ComplexRegion_1",
            source_image_name="_page_0_ComplexRegion_1.jpeg",
            selected=candidate,
            grade="B",
            publish=True,
            review_required=False,
            status="success",
        )
    )

    class Identifier:
        def to_path(self):
            return "_page_0_ComplexRegion_1"

    class Block:
        id = Identifier()
        block_type = "ComplexRegion"

        def get_internal_metadata(self, key):
            return {
                "mermaid": {
                    "source_id": result.source_id,
                    "status": "success",
                    "selected_candidate_id": candidate.candidate_id,
                    "result": result,
                }
            }.get(key)

    class Page:
        current_children = [Block()]

        def contained_blocks(self, document, block_types):
            return [self.current_children[0]]

    class Document:
        pages = [Page()]

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
def test_marker_renderer_optionally_emits_validated_png_preview(monkeypatch):
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer

    from marker_mermaid.marker_integration import MermaidMarkdownRenderer

    payload = BytesIO()
    Image.new("RGB", (4, 3), "white").save(payload, format="PNG")
    candidate = _seal_test_candidate(
        MermaidCandidate(
            candidate_id="candidate-1",
            generation_method="typed_ir",
            diagram_type="flowchart",
            mermaid_code="flowchart LR\nA --> B\n",
            syntax_valid=True,
            render_valid=True,
            aggregate_score=0.8,
            png=payload.getvalue(),
        )
    )
    result = _seal_test_result(
        ReconstructionResult(
            source_id="_page_0_Figure_1",
            source_image_name="_page_0_Figure_1.jpeg",
            selected=candidate,
            grade="B",
            publish=True,
            review_required=False,
            status="success",
        )
    )

    class Identifier:
        def to_path(self):
            return "_page_0_Figure_1"

    class Block:
        id = Identifier()
        block_type = "Figure"

        def get_internal_metadata(self, key):
            return {
                "mermaid": {"status": "success", "errors": []},
                "mermaid_results": [result],
            }.get(key)

    class Page:
        def contained_blocks(self, document, block_types):
            return [Block()]

    class Document:
        pages = [Page()]

    monkeypatch.setattr(
        MarkdownRenderer,
        "__call__",
        lambda self, document: MarkdownOutput(
            markdown="![](_page_0_Figure_1.jpeg)", images={}, metadata={}
        ),
    )
    renderer = MermaidMarkdownRenderer()
    renderer.include_rendered_preview = True

    rendered = renderer(Document())

    preview_name = "page_0_figure_1--mermaid-preview.png"
    assert f"images/{preview_name}" in rendered.markdown
    assert rendered.images[preview_name].size == (4, 3)

    replacement = BytesIO()
    Image.new("RGB", (2, 2), "red").save(replacement, format="PNG")
    result.selected.png = replacement.getvalue()
    swapped_rendered = renderer(Document())
    assert preview_name not in swapped_rendered.images
    assert f"images/{preview_name}" not in swapped_rendered.markdown
    assert swapped_rendered.markdown.count("```mermaid") == 1
    assert result.has_authorized_publication()

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    result.selected.png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 100_000, 100_000, 8, 2, 0, 0, 0))
        + chunk(b"IEND", b"")
    )
    _seal_test_candidate(result.selected)
    _seal_test_result(result)
    bomb_rendered = renderer(Document())
    assert preview_name not in bomb_rendered.images
    assert f"images/{preview_name}" not in bomb_rendered.markdown
    assert result.has_authorized_publication()


@pytest.mark.integration
def test_marker_renderer_emits_multiple_virtual_sources_after_anchor(monkeypatch):
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer

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
    original = ReconstructionResult(
        source_id="_page_0_Figure_1",
        source_image_name="_page_0_Figure_1.jpeg",
        source_block_ids=["/page/0/Figure/1"],
        page_ids=[0],
        selected=candidate,
        grade="B",
        publish=True,
        review_required=False,
        status="success",
    )
    original.selected.mermaid_code = f'flowchart LR\n    A["{original.source_id}"]\n'
    _seal_test_candidate(original.selected)
    _seal_test_result(original)
    panel_1 = original.model_copy(deep=True)
    panel_1.source_id = "_page_0_Figure_1--panel-1"
    panel_1.source_image_name = f"{panel_1.source_id}.jpeg"
    panel_1.source_kind = "panel"
    panel_1.selected.mermaid_code = f'flowchart LR\n    A["{panel_1.source_id}"]\n'
    _seal_test_candidate(panel_1.selected)
    _seal_test_result(panel_1)
    panel_2 = original.model_copy(deep=True)
    panel_2.source_id = "_page_0_Figure_1--panel-2"
    panel_2.source_image_name = f"{panel_2.source_id}.jpeg"
    panel_2.source_kind = "panel"
    panel_2.selected.mermaid_code = f'flowchart LR\n    A["{panel_2.source_id}"]\n'
    _seal_test_candidate(panel_2.selected)
    _seal_test_result(panel_2)
    merged = original.model_copy(deep=True)
    merged.source_id = "_page_0_Figure_1--merged"
    merged.source_image_name = f"{merged.source_id}.jpeg"
    merged.source_kind = "merged"
    merged.selected.mermaid_code = f'flowchart LR\n    A["{merged.source_id}"]\n'
    _seal_test_candidate(merged.selected)
    _seal_test_result(merged)
    runtime_metadata = {
        "mermaid": {
            "status": "success",
            "sources": [],
            "errors": [],
        },
        "mermaid_results": [merged, panel_2, original, panel_1],
        "mermaid_source_images": {
            item.source_image_name: Image.new("RGB", (20, 20), "white")
            for item in (merged, panel_2, panel_1)
        },
    }

    class Identifier:
        def to_path(self):
            return "_page_0_Figure_1"

    class Block:
        id = Identifier()
        block_type = "Figure"

        def get_internal_metadata(self, key):
            return runtime_metadata.get(key)

    class Page:
        current_children = [Block()]

        def contained_blocks(self, document, block_types):
            return [self.current_children[0]]

    class Document:
        pages = [Page()]

    monkeypatch.setattr(
        MarkdownRenderer,
        "__call__",
        lambda self, document: MarkdownOutput(
            markdown="![](_page_0_Figure_1.jpeg)", images={}, metadata={}
        ),
    )

    rendered = MermaidMarkdownRenderer()(Document())

    original_index = rendered.markdown.index("images/_page_0_Figure_1.jpeg")
    original_code = rendered.markdown.index(original.source_id, original_index)
    panel_1_image = rendered.markdown.index(f"images/{panel_1.source_image_name}")
    panel_1_code = rendered.markdown.index(panel_1.source_id, panel_1_image)
    panel_2_image = rendered.markdown.index(f"images/{panel_2.source_image_name}")
    panel_2_code = rendered.markdown.index(panel_2.source_id, panel_2_image)
    merged_image = rendered.markdown.index(f"images/{merged.source_image_name}")
    merged_code = rendered.markdown.index(merged.source_id, merged_image)
    assert (
        original_index
        < original_code
        < panel_1_image
        < panel_1_code
        < panel_2_image
        < panel_2_code
        < merged_image
        < merged_code
    )
    assert rendered.markdown.count("```mermaid") == 4
    assert json.dumps(runtime_metadata["mermaid"])
    assert all(item.source_image_name in rendered.images for item in (panel_1, panel_2, merged))
    assert [item.source_id for item in rendered.reconstructions] == [
        original.source_id,
        panel_1.source_id,
        panel_2.source_id,
        merged.source_id,
    ]


@pytest.mark.integration
def test_marker_processor_uses_vector_and_geometry_without_an_llm(fake_runtime):
    from marker_mermaid.marker_integration import MermaidDiagramProcessor

    processor = MermaidDiagramProcessor(llm_service=None, runtime=fake_runtime)

    assert [engine.name for engine in processor.pipeline.engines] == [
        "vector_primitives",
        "geometry",
    ]
    assert processor.pipeline.repair_engine.name == "evidence_backed_flowchart_repair"


@pytest.mark.integration
def test_marker_processor_passes_structured_vlm_prompt_budgets(fake_runtime):
    from marker_mermaid.marker_integration import MermaidDiagramProcessor

    processor = MermaidDiagramProcessor(
        llm_service=object(),
        config={
            "MermaidDiagramProcessor_max_vlm_prompt_chars": 32_768,
            "MermaidDiagramProcessor_max_vlm_evidence_items": 32,
            "MermaidDiagramProcessor_max_vlm_ocr_items": 64,
            "MermaidDiagramProcessor_max_views": 10,
        },
        runtime=fake_runtime,
    )
    engine = next(
        item for item in processor.pipeline.engines if item.name == "marker_structured_vlm"
    )

    assert engine.max_prompt_chars == 32_768
    assert engine.max_evidence_items == 32
    assert engine.max_ocr_items == 64
    assert engine.max_views == 10


@pytest.mark.integration
def test_marker_result_summary_revalidates_prompt_budget_notices():
    from marker_mermaid.marker_integration import _result_summary

    notice = PromptBudgetNotice(
        engine="marker_structured_vlm",
        selection_profile="structural-quota-v1",
        prompt_chars=10_000,
        max_prompt_chars=100_000,
        schema_reserve_chars=14_753,
        max_evidence_items=1,
        max_ocr_items=0,
        evidence_total=1,
        evidence_considered=1,
        evidence_included=1,
        ocr_total=0,
        ocr_considered=0,
        ocr_included=0,
        selected_evidence_sha256="0" * 64,
    )
    result = ReconstructionResult(
        source_id="source",
        source_image_name="source.png",
        prompt_budget_notices=[notice],
    )
    result.prompt_budget_notices[0].prompt_chars = 100_000

    with pytest.raises(ValueError):
        _result_summary(result, publication_snapshot=None)


@pytest.mark.integration
@pytest.mark.parametrize(
    "serialization_stability",
    ["stable", "extended", "experimental"],
)
def test_marker_result_summary_uses_authorized_serialization_stability(
    serialization_stability: str,
):
    from marker_mermaid.marker_integration import _result_summary

    candidate = _seal_test_candidate(
        MermaidCandidate(
            candidate_id="candidate-1",
            generation_method="typed_ir",
            diagram_type="flowchart",
            mermaid_code="flowchart LR\nA --> B\n",
            syntax_valid=True,
            render_valid=True,
            aggregate_score=0.9,
            serialization_stability=serialization_stability,
        )
    )
    result = _seal_test_result(
        ReconstructionResult(
            source_id="source",
            source_image_name="source.png",
            selected=candidate,
            grade="A",
            publish=True,
            review_required=False,
            status="success",
        )
    )

    assert _result_summary(
        result,
        publication_snapshot=result.authorized_publication_snapshot(),
    )["stability"] == serialization_stability


@pytest.mark.integration
def test_marker_result_summary_does_not_trust_unsealed_published_stability() -> None:
    from marker_mermaid.marker_integration import _result_summary

    candidate = _seal_test_candidate(
        MermaidCandidate(
            candidate_id="candidate-1",
            generation_method="typed_ir",
            diagram_type="flowchart",
            mermaid_code="flowchart LR\nA --> B\n",
            syntax_valid=True,
            render_valid=True,
            aggregate_score=0.9,
            serialization_stability="stable",
        )
    )
    result = _seal_test_result(
        ReconstructionResult(
            source_id="source",
            source_image_name="source.png",
            selected=candidate,
            grade="A",
            publish=True,
            review_required=False,
            status="success",
        )
    )
    assert result.selected is not None
    result.selected.warnings.append("post-certification mutation")

    assert result.authorized_publication_snapshot() is None
    assert _result_summary(result, publication_snapshot=None)["stability"] == "experimental"


@pytest.mark.integration
def test_marker_result_summary_preserves_review_and_failure_stability() -> None:
    from marker_mermaid.marker_integration import _result_summary

    review_candidate = MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type="flowchart",
        serialization_stability="extended",
    )
    review = ReconstructionResult(
        source_id="review",
        source_image_name="review.png",
        selected=review_candidate,
        grade="B",
        publish=False,
        review_required=True,
        status="review_required",
    )
    low_quality = review.model_copy(deep=True)
    assert low_quality.selected is not None
    low_quality.selected.serialization_stability = "stable"
    low_quality.grade = "C"
    failed = ReconstructionResult(
        source_id="failed",
        source_image_name="failed.png",
        grade="U",
        publish=False,
        review_required=True,
        status="failed",
    )

    assert _result_summary(review, publication_snapshot=None)["stability"] == "extended"
    assert _result_summary(low_quality, publication_snapshot=None)["stability"] == "experimental"
    assert _result_summary(failed, publication_snapshot=None)["stability"] == "experimental"


@pytest.mark.integration
def test_marker_processors_keep_json_summaries_separate_from_runtime_payloads(fake_runtime):
    from marker_mermaid.marker_integration import (
        MermaidCandidateDiscoveryProcessor,
        MermaidDiagramProcessor,
    )

    class Identifier:
        def __str__(self):
            return "/page/0/Figure/1"

        def to_path(self):
            return "_page_0_Figure_1"

    class Polygon:
        def __init__(self, bbox):
            self.bbox = bbox

    class Block:
        id = Identifier()
        block_type = "Figure"
        page_id = 0
        polygon = Polygon((10, 10, 110, 70))
        current_children = []

        def __init__(self):
            self.metadata = {}

        def get_image(self, document, highres=True):
            return Image.new("RGB", (200, 120), "white")

        def contained_blocks(self, document, block_types):
            return []

        def raw_text(self, document):
            return ""

        def set_internal_metadata(self, key, value):
            self.metadata[key] = value

        def get_internal_metadata(self, key):
            return self.metadata.get(key)

    block = Block()

    class Page:
        page_id = 0
        polygon = Polygon((0, 0, 200, 100))
        current_children = [block]

        def contained_blocks(self, document, block_types):
            return [block]

    class Document:
        pages = [Page()]

    config = {
        "MermaidDiagramProcessor_split_composite_figures": False,
        "MermaidDiagramProcessor_merge_adjacent_fragments": False,
        "MermaidDiagramProcessor_detect_multi_page_diagrams": False,
    }
    MermaidCandidateDiscoveryProcessor(config)(Document())
    candidate_summary = block.get_internal_metadata("mermaid_candidate")
    candidate_images = block.get_internal_metadata("mermaid_candidate_images")

    assert json.dumps(candidate_summary)
    assert candidate_summary["source_ids"] == ["_page_0_Figure_1"]
    assert all(isinstance(image, Image.Image) for image in candidate_images.values())

    MermaidDiagramProcessor(
        config=config,
        engines=[],
        runtime=fake_runtime,
    )(Document())
    summary = block.get_internal_metadata("mermaid")
    results = block.get_internal_metadata("mermaid_results")
    source_images = block.get_internal_metadata("mermaid_source_images")

    assert json.dumps(summary)
    assert len(results) == 1
    assert results[0].source_mapping["assembly"]["canvas_size"] == [200, 120]
    assert source_images == {}


@pytest.mark.integration
def test_unanchored_page_proposal_reaches_sidecar_output_without_markdown_anchor(
    monkeypatch, fake_runtime
):
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer

    import marker_mermaid.marker_integration as integration
    from marker_mermaid.discovery import DiscoveredSource, SourceFragment
    from marker_mermaid.marker_discovery import MarkerDiscoveryResult

    fragment = SourceFragment(
        fragment_id="page_0_page_diagram_001--fragment",
        page_id=0,
        source_block_ids=[],
        page_bbox=(10, 10, 90, 90),
        crop_bbox=(0, 0, 80, 80),
        image_size=(80, 80),
    )
    source = DiscoveredSource(
        source_id="page_0_page_diagram_001",
        anchor_block_id=None,
        kind="page_proposal",
        fragments=[fragment],
        confidence=0.8,
    )
    discovered = MarkerDiscoveryResult(
        registry={source.source_id: source},
        images={fragment.fragment_id: Image.new("RGB", (80, 80), "white")},
    )
    monkeypatch.setattr(integration, "discover_marker_sources", lambda document, config: discovered)

    class Page:
        page_id = 0
        current_children = []

        def __init__(self):
            self.metadata = {}

        def contained_blocks(self, document, block_types):
            return []

        def set_internal_metadata(self, key, value):
            self.metadata[key] = value

        def get_internal_metadata(self, key):
            return self.metadata.get(key)

    page = Page()

    class Document:
        pages = [page]

    document = Document()
    integration.MermaidCandidateDiscoveryProcessor({})(document)
    assert page.get_internal_metadata("mermaid_unanchored_candidate")["source_ids"] == [
        source.source_id
    ]

    integration.MermaidDiagramProcessor(
        config={},
        engines=[],
        runtime=fake_runtime,
    )(document)
    [result] = page.get_internal_metadata("mermaid_unanchored_results")
    assert result.source_id == source.source_id
    assert result.anchor_block_id is None

    monkeypatch.setattr(
        MarkdownRenderer,
        "__call__",
        lambda self, document: MarkdownOutput(markdown="body", images={}, metadata={}),
    )
    rendered = integration.MermaidMarkdownRenderer()(document)
    assert rendered.markdown == "body"
    assert rendered.reconstructions == [result]
    assert result.source_image_name in rendered.images


@pytest.mark.integration
def test_marker_ocr_evidence_uses_exact_block_crop_coordinates():
    from marker_mermaid.discovery import DiscoveredSource, SourceFragment
    from marker_mermaid.marker_integration import MermaidDiagramProcessor
    from marker_mermaid.source_assembly import assemble_discovered_source

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

    block = Block()
    source = DiscoveredSource(
        source_id="source",
        anchor_block_id=str(block.id),
        kind="original",
        fragments=[
            SourceFragment(
                fragment_id="fragment",
                page_id=0,
                source_block_ids=[str(block.id)],
                page_bbox=tuple(block.polygon.bbox),
                crop_bbox=(0, 0, 200, 400),
                image_size=(200, 400),
            )
        ],
        confidence=1,
    )
    assembled = assemble_discovered_source(
        source, {"fragment": Image.new("RGB", (200, 400), "white")}
    )
    evidence, texts = MermaidDiagramProcessor._ocr_evidence(
        source,
        assembled.metadata,
        {str(block.id): block},
        object(),
    )

    assert evidence[1].bbox == (20.0, 40.0, 100.0, 120.0)
    assert texts == ["Label"]


@pytest.mark.integration
def test_marker_ocr_evidence_filters_panels_and_offsets_continued_pages():
    from marker_mermaid.discovery import DiscoveredSource, SourceFragment
    from marker_mermaid.marker_integration import MermaidDiagramProcessor
    from marker_mermaid.source_assembly import assemble_discovered_source

    class Identifier:
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return self.value

    class Polygon:
        def __init__(self, bbox):
            self.bbox = bbox

    class Span:
        def __init__(self, name, bbox, text):
            self.id = Identifier(name)
            self.polygon = Polygon(bbox)
            self.text = text

    class Block:
        def __init__(self, name, spans):
            self.id = Identifier(name)
            self.polygon = Polygon((0, 0, 200, 100))
            self.spans = spans

        def contained_blocks(self, document, block_types):
            return self.spans

        def raw_text(self, document):
            return " ".join(span.text for span in self.spans)

    first = Block(
        "/page/1/Figure/1",
        [
            Span("/page/1/Span/1", (10, 10, 40, 30), "Left"),
            Span("/page/1/Span/2", (120, 10, 180, 30), "Right"),
        ],
    )
    second = Block(
        "/page/2/Figure/1",
        [Span("/page/2/Span/1", (20, 10, 80, 30), "Continued")],
    )
    source = DiscoveredSource(
        source_id="continued",
        anchor_block_id=str(first.id),
        kind="merged",
        fragments=[
            SourceFragment(
                fragment_id="right-panel",
                page_id=1,
                source_block_ids=[str(first.id)],
                page_bbox=(100, 0, 200, 100),
                crop_bbox=(200, 0, 400, 200),
                image_size=(400, 200),
            ),
            SourceFragment(
                fragment_id="page-2",
                page_id=2,
                source_block_ids=[str(second.id)],
                page_bbox=(0, 0, 200, 100),
                crop_bbox=(0, 0, 400, 200),
                image_size=(400, 200),
                canvas_offset=(0, 200),
            ),
        ],
        confidence=0.9,
    )
    assembled = assemble_discovered_source(
        source,
        {
            "right-panel": Image.new("RGB", (400, 200), "white"),
            "page-2": Image.new("RGB", (400, 200), "white"),
        },
    )

    evidence, texts = MermaidDiagramProcessor._ocr_evidence(
        source,
        assembled.metadata,
        {str(first.id): first, str(second.id): second},
        object(),
    )

    tokens = {item.text: item.bbox for item in evidence if item.kind == "ocr_token"}
    assert texts == ["Right", "Continued"]
    assert "Left" not in tokens
    assert tokens["Right"] == (40.0, 20.0, 160.0, 60.0)
    assert tokens["Continued"] == (40.0, 220.0, 160.0, 260.0)
    assert len({item.id for item in evidence}) == len(evidence)


@pytest.mark.integration
@pytest.mark.parametrize(("reference_limit", "overflows"), [(3, False), (2, True)])
def test_marker_ocr_evidence_enforces_exact_aggregate_provenance_budget(
    monkeypatch,
    reference_limit,
    overflows,
):
    import marker_mermaid.models as models_module
    from marker_mermaid.discovery import DiscoveredSource, SourceFragment
    from marker_mermaid.marker_integration import MermaidDiagramProcessor
    from marker_mermaid.source_assembly import assemble_discovered_source

    class Identifier:
        def __init__(self, value):
            self.value = value

        def __str__(self):
            return self.value

    class Polygon:
        def __init__(self, bbox):
            self.bbox = bbox

    class Span:
        id = Identifier("/page/0/Span/1")
        polygon = Polygon((10, 10, 40, 30))
        text = "Label"

    class Block:
        id = Identifier("/page/0/Figure/1")
        polygon = Polygon((0, 0, 100, 100))

        def contained_blocks(self, document, block_types):
            return [Span()]

    block = Block()
    source = DiscoveredSource(
        source_id="source",
        anchor_block_id=str(block.id),
        kind="original",
        fragments=[
            SourceFragment(
                fragment_id="fragment",
                page_id=0,
                source_block_ids=[str(block.id)],
                page_bbox=(0, 0, 100, 100),
                crop_bbox=(0, 0, 100, 100),
                image_size=(100, 100),
            )
        ],
        confidence=1,
    )
    assembled = assemble_discovered_source(
        source,
        {"fragment": Image.new("RGB", (100, 100), "white")},
    )
    monkeypatch.setattr(
        models_module,
        "MAX_EVIDENCE_SOURCE_BLOCK_REFS",
        reference_limit,
    )

    if overflows:
        with pytest.raises(ValueError, match="source-block references exceed"):
            MermaidDiagramProcessor._ocr_evidence(
                source,
                assembled.metadata,
                {str(block.id): block},
                object(),
            )
        return

    evidence, texts = MermaidDiagramProcessor._ocr_evidence(
        source,
        assembled.metadata,
        {str(block.id): block},
        object(),
    )

    assert sum(len(item.source_block_ids) for item in evidence) == reference_limit
    assert [item.kind for item in evidence] == ["source_crop", "ocr_token"]
    assert texts == ["Label"]


@pytest.mark.integration
def test_marker_ocr_rejects_mutated_source_block_fanout_before_evidence_construction(
    monkeypatch,
):
    import marker_mermaid.marker_integration as integration
    from marker_mermaid.discovery import DiscoveredSource, SourceFragment
    from marker_mermaid.resource_limits import MAX_EVIDENCE_REFS
    from marker_mermaid.source_assembly import assemble_discovered_source

    source = DiscoveredSource(
        source_id="source",
        kind="page_proposal",
        fragments=[
            SourceFragment(
                fragment_id="fragment",
                page_id=0,
                source_block_ids=[],
                image_size=(10, 10),
            )
        ],
        confidence=1,
    )
    assembled = assemble_discovered_source(
        source,
        {"fragment": Image.new("RGB", (10, 10), "white")},
    )
    source.fragments[0].source_block_ids = [
        f"source-{index}" for index in range(MAX_EVIDENCE_REFS + 1)
    ]
    constructed = False

    def forbidden_evidence_construction(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("oversized provenance must fail before VisualEvidence construction")

    monkeypatch.setattr(integration, "VisualEvidence", forbidden_evidence_construction)

    with pytest.raises(ValueError, match="exceeds its reference limit"):
        integration.MermaidDiagramProcessor._ocr_evidence(
            source,
            assembled.metadata,
            {},
            object(),
        )

    assert not constructed


@pytest.mark.integration
def test_marker_processor_isolates_ocr_provenance_overflow_and_continues_source(
    monkeypatch,
    fake_runtime,
):
    import marker_mermaid.models as models_module
    from marker_mermaid.marker_integration import (
        MermaidCandidateDiscoveryProcessor,
        MermaidDiagramProcessor,
    )

    class Identifier:
        def __str__(self):
            return "/page/0/Figure/1"

        def to_path(self):
            return "_page_0_Figure_1"

    class Polygon:
        def __init__(self, bbox):
            self.bbox = bbox

    class Span:
        id = "/page/0/Span/1"
        polygon = Polygon((10, 10, 40, 30))
        text = "must not survive as a partial OCR prefix"

    class Block:
        id = Identifier()
        block_type = "Figure"
        page_id = 0
        polygon = Polygon((0, 0, 100, 100))
        current_children = []

        def __init__(self):
            self.metadata = {}

        def get_image(self, document, highres=True):
            return Image.new("RGB", (100, 100), "white")

        def contained_blocks(self, document, block_types):
            return [Span()]

        def raw_text(self, document):
            return Span.text

        def set_internal_metadata(self, key, value):
            self.metadata[key] = value

        def get_internal_metadata(self, key):
            return self.metadata.get(key)

    block = Block()

    class Page:
        page_id = 0
        polygon = Polygon((0, 0, 100, 100))
        current_children = [block]

        def contained_blocks(self, document, block_types):
            return [block]

    class Document:
        pages = [Page()]

    document = Document()
    config = {
        "MermaidDiagramProcessor_split_composite_figures": False,
        "MermaidDiagramProcessor_merge_adjacent_fragments": False,
        "MermaidDiagramProcessor_detect_multi_page_diagrams": False,
        "MermaidDiagramProcessor_enable_page_detector": False,
    }
    MermaidCandidateDiscoveryProcessor(config)(document)
    monkeypatch.setattr(models_module, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 0)
    processor = MermaidDiagramProcessor(
        config=config,
        engines=[],
        runtime=fake_runtime,
    )
    captured = {}
    reconstruct = processor.pipeline.reconstruct

    def capture_reconstruction(*args, **kwargs):
        captured["evidence"] = kwargs["evidence"]
        captured["ocr_texts"] = kwargs["ocr_texts"]
        return reconstruct(*args, **kwargs)

    processor.pipeline.reconstruct = capture_reconstruction
    processor(document)

    [result] = block.get_internal_metadata("mermaid_results")
    metadata = block.get_internal_metadata("mermaid")
    assert captured == {"evidence": [], "ocr_texts": []}
    assert result.evidence == []
    assert metadata["status"] == "partial"
    assert metadata["errors"] == [
        {
            "source_id": "_page_0_Figure_1",
            "error": (
                "MarkerOCREvidenceError: invalid or oversized OCR evidence was isolated "
                "atomically (ValueError)"
            ),
        }
    ]
