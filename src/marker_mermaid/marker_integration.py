"""Marker 1.10.2 processors, converter, renderer, and OCR provenance adapter."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from marker.converters.pdf import PdfConverter
from marker.processors import BaseProcessor
from marker.processors.blank_page import BlankPageProcessor
from marker.processors.reference import ReferenceProcessor
from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer
from marker.schema import BlockTypes
from marker.schema.document import Document
from marker.settings import settings

from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import MarkerStructuredVLMEngine
from marker_mermaid.models import ReconstructionResult, VisualEvidence
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

DEFAULT_BLOCK_TYPES = (BlockTypes.Figure, BlockTypes.Picture, BlockTypes.ComplexRegion)


class MermaidCandidateDiscoveryProcessor(BaseProcessor):
    block_types = DEFAULT_BLOCK_TYPES

    def __call__(self, document: Document, *args, **kwargs):
        seen: set[str] = set()
        for page in document.pages:
            blocks = list(page.contained_blocks(document, self.block_types))
            blocks.extend(
                block for block in page.current_children if block.block_type in self.block_types
            )
            for block in blocks:
                source_id = block.id.to_path()
                if source_id in seen:
                    continue
                seen.add(source_id)
                block.set_internal_metadata(
                    "mermaid_candidate",
                    {
                        "status": "discovered",
                        "source_id": source_id,
                        "source_block_ids": [str(block.id)],
                        "page_id": block.page_id,
                        "bbox": list(block.polygon.bbox),
                    },
                )


class MermaidDiagramProcessor(BaseProcessor):
    """Configuration name intentionally matches the MMX-001 JSON prefix."""

    block_types = DEFAULT_BLOCK_TYPES

    def __init__(self, llm_service: Any = None, config=None, engines=None, runtime=None):
        super().__init__(config)
        self.mermaid_config = MermaidConfig.from_marker_config(config)
        selected_engines = engines
        if selected_engines is None:
            selected_engines = [MarkerStructuredVLMEngine(llm_service)] if llm_service else []
        selected_runtime = runtime or NodeMermaidRuntime(self.mermaid_config.runtime_dir)
        self.runtime = selected_runtime
        self.pipeline = ReconstructionPipeline(
            self.mermaid_config,
            selected_engines,
            CandidateValidator(
                selected_runtime,
                self.mermaid_config.security_profile,
                max_chars=self.mermaid_config.max_mermaid_chars,
                max_lines=self.mermaid_config.max_mermaid_lines,
            ),
        )

    def __call__(self, document: Document, *args, **kwargs):
        try:
            for block in document.contained_blocks(self.block_types):
                discovery = block.get_internal_metadata("mermaid_candidate")
                if not discovery:
                    continue
                try:
                    image = block.get_image(document, highres=True, expansion=(0.01, 0.01))
                    if image is None:
                        raise ValueError("no image")
                    evidence, texts = self._ocr_evidence(block, document, image.size)
                    source_id = discovery["source_id"]
                    extension = settings.OUTPUT_IMAGE_FORMAT.lower()
                    image_name = f"{source_id}.{extension}"
                    result = self.pipeline.reconstruct(
                        source_id,
                        image_name,
                        image,
                        source_block_ids=discovery["source_block_ids"],
                        evidence=evidence,
                        ocr_texts=texts,
                        source_block=block,
                    )
                    selected = result.selected
                    block.set_internal_metadata(
                        "mermaid",
                        {
                            "source_id": source_id,
                            "status": result.status,
                            "stability": (
                                "experimental" if result.grade in {"C", "D", "U"} else "stable"
                            ),
                            "diagram_type": selected.diagram_type if selected else None,
                            "quality_score": selected.aggregate_score if selected else None,
                            "selected_candidate_id": (selected.candidate_id if selected else None),
                            "mermaid_code": selected.mermaid_code if selected else None,
                            "sidecar_dir": result.sidecar_dir,
                            "result": result,
                        },
                    )
                except Exception as exc:
                    block.set_internal_metadata(
                        "mermaid",
                        {
                            "source_id": discovery["source_id"],
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
        finally:
            self.runtime.close()

    @staticmethod
    def _ocr_evidence(block, document: Document, image_size: tuple[int, int]):
        evidence = [
            VisualEvidence(
                id=f"source-{block.id.to_path()}",
                kind="source_crop",
                bbox=(0, 0, image_size[0], image_size[1]),
                score=1.0,
                source_block_ids=[str(block.id)],
            )
        ]
        texts: list[str] = []
        block_box = block.polygon.bbox
        width = max(block_box[2] - block_box[0], 1)
        height = max(block_box[3] - block_box[1], 1)
        text_blocks = block.contained_blocks(document, (BlockTypes.Span,))
        for index, text_block in enumerate(text_blocks, start=1):
            text = getattr(text_block, "text", "").strip()
            if not text:
                continue
            box = text_block.polygon.bbox
            crop_box = (
                (box[0] - block_box[0]) / width * image_size[0],
                (box[1] - block_box[1]) / height * image_size[1],
                (box[2] - block_box[0]) / width * image_size[0],
                (box[3] - block_box[1]) / height * image_size[1],
            )
            evidence.append(
                VisualEvidence(
                    id=f"ocr-{block.id.to_path()}-{index}",
                    kind="ocr_token",
                    bbox=crop_box,
                    text=text,
                    score=1.0,
                    source_block_ids=[str(block.id), str(text_block.id)],
                )
            )
            texts.append(text)
        raw_text = block.raw_text(document).strip()
        if raw_text and raw_text not in texts:
            texts.append(raw_text)
        return evidence, texts


MermaidReconstructionProcessor = MermaidDiagramProcessor


class MermaidMarkdownOutput(MarkdownOutput):
    reconstructions: list[ReconstructionResult]


class MermaidMarkdownRenderer(MarkdownRenderer):
    image_blocks = DEFAULT_BLOCK_TYPES
    extract_images: Literal[True] = True
    include_original_image: Literal[True] = True
    include_mermaid_code: Annotated[bool, "Include validated Mermaid code."] = True
    include_rendered_preview: Annotated[bool, "Include final SVG preview."] = False
    show_quality_warning: Annotated[bool, "Show B/C grade warnings."] = True
    show_quality_score: Annotated[bool, "Show aggregate score in Markdown."] = False

    def __init__(self, config=None):
        if isinstance(config, dict) and (
            config.get("extract_images") is False
            or config.get("MermaidMarkdownRenderer_include_original_image") is False
        ):
            raise ValueError("marker-mermaid always preserves original images")
        super().__init__(config)
        self.extract_images = True
        self.include_original_image = True

    def __call__(self, document: Document) -> MermaidMarkdownOutput:
        rendered = super().__call__(document)
        markdown = rendered.markdown
        reconstructions: list[ReconstructionResult] = []
        metadata_rows: list[dict[str, Any]] = []
        inserted: set[str] = set()
        for block in document.contained_blocks(DEFAULT_BLOCK_TYPES):
            data = block.get_internal_metadata("mermaid")
            if not data:
                continue
            metadata_rows.append({key: value for key, value in data.items() if key != "result"})
            if not isinstance(data.get("result"), ReconstructionResult):
                continue
            result = data["result"]
            reconstructions.append(result)
            if not self.include_mermaid_code or not result.publish or result.source_id in inserted:
                continue
            selected = result.selected
            if selected is None or not selected.mermaid_code:
                continue
            image_name = result.source_image_name
            image_pattern = re.compile(rf"(!\[[^\]]*\]\()({re.escape(image_name)})(\))")
            match = image_pattern.search(markdown)
            if not match:
                continue
            warning = ""
            if self.show_quality_warning and result.grade in {"B", "C"}:
                warning = "\n\n> **Experimental reconstruction:** 원본과 대조해 주세요."
                if self.show_quality_score and selected.aggregate_score is not None:
                    warning += f"\n> Quality score: {selected.aggregate_score:.2f}"
            fragment = f"{warning}\n\n```mermaid\n{selected.mermaid_code.rstrip()}\n```"
            markdown = markdown[: match.end()] + fragment + markdown[match.end() :]
            inserted.add(result.source_id)
        markdown = re.sub(r"(!\[[^\]]*\]\()(_page_[^)]+)(\))", r"\1images/\2\3", markdown)
        metadata = dict(rendered.metadata)
        metadata["mermaid"] = metadata_rows
        return MermaidMarkdownOutput(
            markdown=markdown,
            images=rendered.images,
            metadata=metadata,
            reconstructions=reconstructions,
        )


class MarkerMermaidPdfConverter(PdfConverter):
    default_processors = list(PdfConverter.default_processors)
    _reference_index = default_processors.index(ReferenceProcessor)
    default_processors[_reference_index + 1 : _reference_index + 1] = [
        MermaidCandidateDiscoveryProcessor,
        MermaidDiagramProcessor,
    ]
    default_processors = tuple(default_processors)

    def __init__(self, *args, renderer=None, **kwargs):
        super().__init__(
            *args,
            renderer=renderer or "marker_mermaid.marker_integration.MermaidMarkdownRenderer",
            **kwargs,
        )


assert MarkerMermaidPdfConverter.default_processors.index(ReferenceProcessor) < (
    MarkerMermaidPdfConverter.default_processors.index(MermaidCandidateDiscoveryProcessor)
)
assert MarkerMermaidPdfConverter.default_processors.index(MermaidDiagramProcessor) < (
    MarkerMermaidPdfConverter.default_processors.index(BlankPageProcessor)
)
