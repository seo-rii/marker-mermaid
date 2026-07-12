"""Marker 1.10.2 processors, converter, renderer, and OCR provenance adapter."""

from __future__ import annotations

import re
from collections import defaultdict
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
from marker_mermaid.discovery import DiscoveredSource
from marker_mermaid.engines import MarkerStructuredVLMEngine
from marker_mermaid.geometry import GeometryEngine
from marker_mermaid.markdown import reconstruction_markdown
from marker_mermaid.marker_discovery import (
    discover_marker_sources,
    iter_marker_candidate_blocks,
)
from marker_mermaid.models import ReconstructionResult, VisualEvidence
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.source_assembly import SourceAssemblyMetadata, assemble_discovered_source
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime
from marker_mermaid.vector import VectorPrimitiveEngine

DEFAULT_BLOCK_TYPES = (BlockTypes.Figure, BlockTypes.Picture, BlockTypes.ComplexRegion)


def _source_sort_key(source: Any) -> tuple[int, str]:
    kind = getattr(source, "kind", None) or getattr(source, "source_kind", None)
    rank = {"original": 0, "full_page": 0, "panel": 1, "merged": 2}.get(kind, 3)
    return rank, str(getattr(source, "source_id", ""))


def _discovered_source_from_json(value: Any) -> DiscoveredSource:
    return value if isinstance(value, DiscoveredSource) else DiscoveredSource.model_validate(value)


def _result_summary(result: ReconstructionResult) -> dict[str, Any]:
    selected = result.selected
    return {
        "source_id": result.source_id,
        "source_kind": result.source_kind,
        "source_block_ids": result.source_block_ids,
        "page_ids": result.page_ids,
        "anchor_block_id": result.anchor_block_id,
        "status": result.status,
        "stability": "experimental" if result.grade in {"C", "D", "U"} else "stable",
        "diagram_type": selected.diagram_type if selected else None,
        "emitted_diagram_type": selected.emitted_diagram_type if selected else None,
        "fallback_chain": selected.fallback_chain if selected else [],
        "quality_score": selected.aggregate_score if selected else None,
        "selected_candidate_id": selected.candidate_id if selected else None,
        "sidecar_dir": result.sidecar_dir,
    }


def _bbox_intersection(left, right):
    box = (
        max(left[0], right[0]),
        max(left[1], right[1]),
        min(left[2], right[2]),
        min(left[3], right[3]),
    )
    return box if box[2] > box[0] and box[3] > box[1] else None


def _transform_bbox(box, transform):
    a, b, c, d, e, f = transform
    points = [
        (a * x + b * y + c, d * x + e * y + f)
        for x, y in (
            (box[0], box[1]),
            (box[2], box[1]),
            (box[2], box[3]),
            (box[0], box[3]),
        )
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _page_bbox_to_image_bbox(page_box, block_box, image_size):
    width = max(block_box[2] - block_box[0], 1)
    height = max(block_box[3] - block_box[1], 1)
    return (
        (page_box[0] - block_box[0]) / width * image_size[0],
        (page_box[1] - block_box[1]) / height * image_size[1],
        (page_box[2] - block_box[0]) / width * image_size[0],
        (page_box[3] - block_box[1]) / height * image_size[1],
    )


def _bbox_covers_image(box, image_size) -> bool:
    return box[0] <= 0 and box[1] <= 0 and box[2] >= image_size[0] and box[3] >= image_size[1]


class MermaidCandidateDiscoveryProcessor(BaseProcessor):
    block_types = DEFAULT_BLOCK_TYPES

    def __init__(self, config=None):
        super().__init__(config)
        self.mermaid_config = MermaidConfig.from_marker_config(config)

    def __call__(self, document: Document, *args, **kwargs):
        discovered = discover_marker_sources(document, self.mermaid_config)
        blocks = {str(block.id): block for block in iter_marker_candidate_blocks(document)}
        sources_by_anchor: dict[str, list[Any]] = defaultdict(list)
        for source in discovered.registry.values():
            if source.anchor_block_id:
                sources_by_anchor[source.anchor_block_id].append(source)
        for anchor_id, sources in sources_by_anchor.items():
            block = blocks.get(anchor_id)
            if block is None:
                continue
            ordered = sorted(sources, key=_source_sort_key)
            fragment_ids = {
                fragment.fragment_id for source in ordered for fragment in source.fragments
            }
            block.set_internal_metadata(
                "mermaid_candidate",
                {
                    "status": "discovered",
                    "anchor_block_id": anchor_id,
                    "source_ids": [source.source_id for source in ordered],
                    "sources": [source.model_dump(mode="json") for source in ordered],
                    "errors": [
                        error
                        for error in discovered.errors
                        if error.get("source_id") in {source.source_id for source in ordered}
                        or anchor_id in error.get("source_id", "")
                    ],
                },
            )
            block.set_internal_metadata(
                "mermaid_candidate_images",
                {fragment_id: discovered.images[fragment_id] for fragment_id in fragment_ids},
            )


class MermaidDiagramProcessor(BaseProcessor):
    """Configuration name intentionally matches the MMX-001 JSON prefix."""

    block_types = DEFAULT_BLOCK_TYPES

    def __init__(self, llm_service: Any = None, config=None, engines=None, runtime=None):
        super().__init__(config)
        self.mermaid_config = MermaidConfig.from_marker_config(config)
        selected_engines = engines
        if selected_engines is None:
            selected_engines = []
            if self.mermaid_config.use_vector_primitives:
                selected_engines.append(VectorPrimitiveEngine())
            if self.mermaid_config.enable_generic_scene_ir:
                selected_engines.append(GeometryEngine())
            if llm_service:
                selected_engines.append(MarkerStructuredVLMEngine(llm_service))
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
            blocks = {str(block.id): block for block in iter_marker_candidate_blocks(document)}
            for block in blocks.values():
                discovery = block.get_internal_metadata("mermaid_candidate")
                if not discovery:
                    continue
                fragment_images = block.get_internal_metadata("mermaid_candidate_images") or {}
                results: list[ReconstructionResult] = []
                source_images: dict[str, Any] = {}
                errors: list[dict[str, str]] = [
                    dict(error) for error in discovery.get("errors", [])
                ]
                sources = sorted(
                    (_discovered_source_from_json(item) for item in discovery.get("sources", [])),
                    key=_source_sort_key,
                )
                for source in sources:
                    try:
                        assembled = assemble_discovered_source(
                            source,
                            fragment_images,
                            max_output_size=(
                                self.mermaid_config.max_virtual_source_dimension,
                                self.mermaid_config.max_virtual_source_dimension,
                            ),
                            max_pixels=self.mermaid_config.max_virtual_source_pixels,
                        )
                        evidence, texts = self._ocr_evidence(
                            source,
                            assembled.metadata,
                            blocks,
                            document,
                        )
                        source_block_ids = list(
                            dict.fromkeys(
                                block_id
                                for fragment in source.fragments
                                for block_id in fragment.source_block_ids
                            )
                        )
                        page_ids = list(
                            dict.fromkeys(fragment.page_id for fragment in source.fragments)
                        )
                        image = assembled.image
                        source_id = source.source_id
                        extension = settings.OUTPUT_IMAGE_FORMAT.lower()
                        image_name = f"{source_id}.{extension}"
                        result = self.pipeline.reconstruct(
                            source_id,
                            image_name,
                            image,
                            source_block_ids=source_block_ids,
                            source_kind=source.kind,
                            page_ids=page_ids,
                            anchor_block_id=source.anchor_block_id,
                            source_mapping={
                                "source": source.model_dump(mode="json"),
                                "assembly": assembled.metadata.model_dump(),
                            },
                            evidence=evidence,
                            ocr_texts=texts,
                            source_block=block,
                            source_blocks=[
                                blocks[block_id]
                                for block_id in source_block_ids
                                if block_id in blocks
                            ],
                        )
                        results.append(result)
                        anchor_image_name = (
                            f"{block.id.to_path()}.{settings.OUTPUT_IMAGE_FORMAT.lower()}"
                        )
                        if image_name != anchor_image_name:
                            if image_name in source_images:
                                raise ValueError(f"duplicate virtual image name: {image_name}")
                            source_images[image_name] = image
                    except Exception as exc:
                        errors.append(
                            {
                                "source_id": source.source_id,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )

                rows = [_result_summary(result) for result in results]
                status = "success" if results and not errors else "partial"
                if not results:
                    status = "failed"
                block.set_internal_metadata(
                    "mermaid",
                    {
                        "anchor_block_id": discovery.get("anchor_block_id"),
                        "status": status,
                        "sources": rows,
                        "errors": errors,
                    },
                )
                block.set_internal_metadata("mermaid_results", results)
                block.set_internal_metadata("mermaid_source_images", source_images)
        finally:
            self.runtime.close()

    @staticmethod
    def _ocr_evidence(source, assembly: SourceAssemblyMetadata, blocks, document: Document):
        placements = assembly.placement_by_fragment_id()
        evidence: list[VisualEvidence] = []
        texts: list[str] = []
        for fragment in source.fragments:
            placement = placements[fragment.fragment_id]
            evidence.append(
                VisualEvidence(
                    id=f"source-{fragment.fragment_id}",
                    kind="source_crop",
                    bbox=placement.canvas_bbox,
                    score=1.0,
                    source_block_ids=fragment.source_block_ids,
                )
            )
            if not fragment.source_block_ids:
                continue
            block = blocks.get(fragment.source_block_ids[0])
            if block is None:
                continue
            block_box = tuple(block.polygon.bbox)
            crop = fragment.crop_bbox or (
                0.0,
                0.0,
                float(fragment.image_size[0]),
                float(fragment.image_size[1]),
            )
            text_blocks = block.contained_blocks(document, (BlockTypes.Span,))
            added_for_fragment = False
            for index, text_block in enumerate(text_blocks, start=1):
                text = getattr(text_block, "text", "").strip()
                if not text:
                    continue
                page_text_box = tuple(text_block.polygon.bbox)
                if fragment.page_bbox is not None and placement.page_to_canvas is not None:
                    clipped = _bbox_intersection(page_text_box, fragment.page_bbox)
                    if clipped is None:
                        continue
                    canvas_box = _transform_bbox(clipped, placement.page_to_canvas)
                else:
                    raw_box = _page_bbox_to_image_bbox(
                        page_text_box,
                        block_box,
                        fragment.image_size,
                    )
                    clipped = _bbox_intersection(raw_box, crop)
                    if clipped is None:
                        continue
                    canvas_box = _transform_bbox(clipped, placement.source_to_canvas)
                canvas_box = _bbox_intersection(canvas_box, placement.canvas_bbox)
                if canvas_box is None:
                    continue
                evidence.append(
                    VisualEvidence(
                        id=f"ocr-{fragment.fragment_id}-{index}",
                        kind="ocr_token",
                        bbox=canvas_box,
                        text=text,
                        score=1.0,
                        source_block_ids=[str(block.id), str(text_block.id)],
                    )
                )
                if text not in texts:
                    texts.append(text)
                added_for_fragment = True
            if not added_for_fragment and _bbox_covers_image(crop, fragment.image_size):
                raw_text_getter = getattr(block, "raw_text", None)
                raw_text = raw_text_getter(document).strip() if callable(raw_text_getter) else ""
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
        images = dict(rendered.images)
        reconstructions: list[ReconstructionResult] = []
        metadata_rows: list[dict[str, Any]] = []
        inserted: set[str] = set()
        collected: set[str] = set()
        for block in iter_marker_candidate_blocks(document):
            data = block.get_internal_metadata("mermaid")
            if not data:
                continue
            summary = dict(data)
            raw_results = block.get_internal_metadata("mermaid_results")
            source_images = block.get_internal_metadata("mermaid_source_images") or {}
            # Legacy single-source metadata remains readable for older callers.
            if raw_results is None:
                raw_results = data.get("results")
                source_images = data.get("source_images", source_images)
            if raw_results is None and isinstance(data.get("result"), ReconstructionResult):
                raw_results = [data["result"]]
            results = sorted(
                (
                    result
                    for result in (raw_results or [])
                    if isinstance(result, ReconstructionResult)
                ),
                key=_source_sort_key,
            )
            if data.get("errors"):
                metadata_rows.append(
                    {
                        "anchor_block_id": data.get("anchor_block_id"),
                        "status": data.get("status"),
                        "errors": data["errors"],
                    }
                )
            if not results:
                if not data.get("errors"):
                    metadata_rows.append(summary)
                continue
            for image_name, image in source_images.items():
                if image_name in images:
                    raise ValueError(f"duplicate output image name: {image_name}")
                images[image_name] = image
            anchor_image_name = f"{block.id.to_path()}.{settings.OUTPUT_IMAGE_FORMAT.lower()}"
            image_pattern = re.compile(rf"(!\[[^\]]*\]\()({re.escape(anchor_image_name)})(\))")
            match = image_pattern.search(markdown)
            fragments: list[str] = []
            pending: list[ReconstructionResult] = []
            for result in results:
                if result.source_id not in collected:
                    collected.add(result.source_id)
                    reconstructions.append(result)
                    metadata_rows.append(_result_summary(result))
                if result.source_id in inserted:
                    continue
                pending.append(result)
                if result.source_image_name != anchor_image_name:
                    fragments.append(
                        f"![원본 {result.source_kind} 다이어그램]"
                        f"(images/{result.source_image_name})"
                    )
                if self.include_mermaid_code:
                    reconstructed = reconstruction_markdown(
                        result,
                        show_score=self.show_quality_score,
                        show_warning=self.show_quality_warning,
                    )
                    if reconstructed:
                        fragments.append(reconstructed)
            if match and fragments:
                fragment = "\n\n" + "\n\n".join(fragments)
                markdown = markdown[: match.end()] + fragment + markdown[match.end() :]
                inserted.update(result.source_id for result in pending)
        markdown = re.sub(r"(!\[[^\]]*\]\()(_page_[^)]+)(\))", r"\1images/\2\3", markdown)
        metadata = dict(rendered.metadata)
        metadata["mermaid"] = metadata_rows
        return MermaidMarkdownOutput(
            markdown=markdown,
            images=images,
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
