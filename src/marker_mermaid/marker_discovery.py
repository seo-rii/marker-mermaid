"""Duck-typed Marker document adapter for source discovery.

The deterministic heuristics in :mod:`marker_mermaid.discovery` deliberately know
nothing about Marker.  This module is the small integration boundary that walks a
Marker 1.10.2-like document, obtains Pillow crops, and creates a serializable
``DiscoveredSource`` registry.  Pillow images stay in a separate mapping so registry
JSON never accidentally embeds source pixels.

Marker is an optional dependency.  Imports of Marker types happen only while
discovering block-type tokens; every other interaction uses the public shape of
``Document``, ``Page``, and ``Block`` objects.  This also makes the adapter useful in
tests and for compatible downstream document implementations.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from typing import Any

from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.discovery import (
    DiscoveredSource,
    FragmentCandidate,
    FragmentMergeProposal,
    SourceFragment,
    assess_full_page_coverage,
    propose_composite_panels,
    propose_fragment_merges,
)
from marker_mermaid.models import BBox
from marker_mermaid.page_detector import PageDiagramDetection, detect_page_diagram_regions

_TARGET_BLOCK_NAMES = frozenset({"figure", "picture", "complexregion"})
_SAME_PAGE_MERGE_SCALE_REL_TOLERANCE = 1e-3
_SAME_PAGE_MERGE_SCALE_ABS_TOLERANCE = 1e-6


@dataclass(slots=True)
class MarkerDiscoveryResult:
    """Registry metadata and its separately held source images.

    ``registry`` uses ``DiscoveredSource.source_id`` keys, while ``images`` uses the
    ``SourceFragment.fragment_id`` contract consumed by source assembly.  Callers may
    safely serialize the registry with Pydantic while retaining or writing source
    pixels separately.
    """

    registry: dict[str, DiscoveredSource]
    images: dict[str, Image.Image]
    errors: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class _BlockSource:
    block: Any
    block_id: str
    source_id: str
    page_id: int
    page_bbox: BBox
    page_size: tuple[float, float]
    image: Image.Image
    caption: str | None

    @property
    def fragment(self) -> SourceFragment:
        return SourceFragment(
            fragment_id=f"{self.source_id}--fragment",
            page_id=self.page_id,
            source_block_ids=[self.block_id],
            page_bbox=self.page_bbox,
            crop_bbox=(0.0, 0.0, float(self.image.width), float(self.image.height)),
            image_size=self.image.size,
        )


class MarkerSourceDiscovery:
    """Build reconstructable sources from a Marker-compatible document.

    Original block crops are always retained.  Full-page blocks are classified as
    ``full_page`` rather than duplicated.  Composite panels and fragment merges are
    additive candidates governed by :class:`MermaidConfig` switches.
    """

    def __init__(
        self,
        config: MermaidConfig | Mapping[str, Any] | None = None,
        *,
        page_region_detector: Callable[..., PageDiagramDetection] | None = None,
    ):
        self.config = (
            config
            if isinstance(config, MermaidConfig)
            else MermaidConfig.from_marker_config(config)
        )
        self.page_region_detector = page_region_detector or detect_page_diagram_regions

    def discover(self, document: Any) -> MarkerDiscoveryResult:
        registry: dict[str, DiscoveredSource] = {}
        source_images: dict[str, Image.Image] = {}
        block_sources: dict[str, _BlockSource] = {}
        signatures: set[tuple[Any, ...]] = set()
        errors: list[dict[str, str]] = []

        for page_index, page in enumerate(_iter_pages(document)):
            page_bbox = _object_bbox(page)
            page_size = _bbox_size(page_bbox)
            page_sources: list[_BlockSource] = []
            for block in _iter_candidate_blocks(page, document):
                block_id = _block_id(block)
                if block_id in block_sources:
                    continue
                block_bbox = _object_bbox(block)
                image = _block_image(block, document)
                if image is None:
                    continue
                signature = (
                    _page_id(block, page, page_index),
                    tuple(round(value, 3) for value in block_bbox),
                    image.size,
                    sha256(image.convert("RGB").tobytes()).digest(),
                )
                if signature in signatures:
                    continue
                signatures.add(signature)
                source_id = _unique_source_id(_block_source_id(block, block_id), registry)
                source = _BlockSource(
                    block=block,
                    block_id=block_id,
                    source_id=source_id,
                    page_id=_page_id(block, page, page_index),
                    page_bbox=block_bbox,
                    page_size=page_size,
                    image=image,
                    caption=_caption_text(block, document),
                )
                block_sources[block_id] = source
                page_sources.append(source)
                kind = "original"
                signals = ["marker_block"]
                if self.config.enable_page_detector:
                    coverage = assess_full_page_coverage(block_bbox, page_bbox)
                    if coverage.is_full_page:
                        kind = "full_page"
                        signals.append("full_page_coverage")
                fragment = source.fragment
                registry[source_id] = DiscoveredSource(
                    source_id=source_id,
                    anchor_block_id=block_id,
                    kind=kind,
                    fragments=[fragment],
                    signals=signals,
                    confidence=1.0,
                )
                source_images[fragment.fragment_id] = image.copy()

                if self.config.split_composite_figures:
                    try:
                        self._add_panels(source, registry, source_images)
                    except Exception as exc:
                        errors.append(
                            {
                                "stage": "panel_split",
                                "source_id": source_id,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )

            if self.config.enable_page_detector:
                try:
                    self._add_page_proposals(
                        page,
                        document,
                        page_index,
                        page_bbox,
                        page_sources,
                        registry,
                        source_images,
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "stage": "page_detector",
                            "source_id": f"page-{_page_id(page, page, page_index)}",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        self._add_merges(block_sources, registry, errors)
        return MarkerDiscoveryResult(
            registry=registry,
            images=source_images,
            errors=errors,
        )

    def _add_page_proposals(
        self,
        page: Any,
        document: Any,
        page_index: int,
        page_bbox: BBox,
        page_sources: Sequence[_BlockSource],
        registry: dict[str, DiscoveredSource],
        source_images: dict[str, Image.Image],
    ) -> None:
        page_image = _block_image(page, document)
        if page_image is None:
            return
        page_width, page_height = _bbox_size(page_bbox)
        scale_x = page_image.width / page_width
        scale_y = page_image.height / page_height
        occupied = [
            (
                (source.page_bbox[0] - page_bbox[0]) * scale_x,
                (source.page_bbox[1] - page_bbox[1]) * scale_y,
                (source.page_bbox[2] - page_bbox[0]) * scale_x,
                (source.page_bbox[3] - page_bbox[1]) * scale_y,
            )
            for source in page_sources
        ]
        detection = self.page_region_detector(
            page_image,
            occupied,
            use_opencv=self.config.use_canny_edge_map,
        )
        page_id = _page_id(page, page, page_index)
        for region in detection.regions:
            crop_left, crop_top, crop_right, crop_bottom = region.bbox
            pixel_crop = (
                max(0, int(crop_left)),
                max(0, int(crop_top)),
                min(page_image.width, int(crop_right + 0.999999)),
                min(page_image.height, int(crop_bottom + 0.999999)),
            )
            proposal_image = page_image.crop(pixel_crop)
            if proposal_image.width == 0 or proposal_image.height == 0:
                continue
            proposal_page_bbox = (
                page_bbox[0] + crop_left / scale_x,
                page_bbox[1] + crop_top / scale_y,
                page_bbox[0] + crop_right / scale_x,
                page_bbox[1] + crop_bottom / scale_y,
            )
            anchor = _nearest_page_anchor(proposal_page_bbox, page_sources)
            source_id = _unique_source_id(
                f"page_{page_id}_{region.region_id.replace('-', '_')}", registry
            )
            fragment = SourceFragment(
                fragment_id=f"{source_id}--fragment",
                page_id=page_id,
                source_block_ids=[],
                page_bbox=proposal_page_bbox,
                crop_bbox=(0.0, 0.0, float(proposal_image.width), float(proposal_image.height)),
                image_size=proposal_image.size,
            )
            registry[source_id] = DiscoveredSource(
                source_id=source_id,
                anchor_block_id=anchor.block_id if anchor is not None else None,
                kind="page_proposal",
                fragments=[fragment],
                signals=list(
                    dict.fromkeys(
                        [
                            "page_level_detector",
                            f"{detection.edge_backend}_edge_backend",
                            *region.signals,
                        ]
                    )
                ),
                confidence=region.confidence,
            )
            source_images[fragment.fragment_id] = proposal_image

    @staticmethod
    def _add_panels(
        source: _BlockSource,
        registry: dict[str, DiscoveredSource],
        source_images: dict[str, Image.Image],
    ) -> None:
        proposal = propose_composite_panels(source.image)
        if proposal is None:
            return
        block_left, block_top, block_right, block_bottom = source.page_bbox
        scale_x = (block_right - block_left) / source.image.width
        scale_y = (block_bottom - block_top) / source.image.height
        for panel in proposal.panels:
            crop_left, crop_top, crop_right, crop_bottom = panel.bbox
            page_bbox = (
                block_left + crop_left * scale_x,
                block_top + crop_top * scale_y,
                block_left + crop_right * scale_x,
                block_top + crop_bottom * scale_y,
            )
            source_id = _unique_source_id(f"{source.source_id}--{panel.panel_id}", registry)
            fragment = SourceFragment(
                fragment_id=f"{source_id}--fragment",
                page_id=source.page_id,
                source_block_ids=[source.block_id],
                page_bbox=page_bbox,
                crop_bbox=panel.bbox,
                image_size=source.image.size,
            )
            registry[source_id] = DiscoveredSource(
                source_id=source_id,
                anchor_block_id=source.block_id,
                kind="panel",
                fragments=[fragment],
                signals=list(dict.fromkeys(proposal.signals + panel.signals)),
                confidence=min(proposal.score, panel.confidence),
            )
            source_images[fragment.fragment_id] = source.image.copy()

    def _add_merges(
        self,
        block_sources: Mapping[str, _BlockSource],
        registry: dict[str, DiscoveredSource],
        errors: list[dict[str, str]],
    ) -> None:
        if not (self.config.merge_adjacent_fragments or self.config.detect_multi_page_diagrams):
            return
        # An absent caption is never treated as merge evidence.  In particular,
        # boundary-touching figures without captions remain independent candidates.
        captioned = [source for source in block_sources.values() if source.caption]
        candidates = [
            FragmentCandidate(
                block_id=source.block_id,
                bbox=source.page_bbox,
                page=source.page_id,
                page_size=source.page_size,
                caption=source.caption,
            )
            for source in captioned
        ]
        fragments = {source.block_id: source.fragment for source in captioned}
        for proposal in propose_fragment_merges(candidates):
            same_page = proposal.pages[0] == proposal.pages[1]
            if same_page and not self.config.merge_adjacent_fragments:
                continue
            if not same_page and not self.config.detect_multi_page_diagrams:
                continue
            # Spatial boundary evidence alone is not a caption relationship.  Require
            # the proposal to have derived a shared/continued semantic signal too.
            if not ({"shared_caption", "continued"} & set(proposal.signals)):
                continue
            try:
                merged_source = merge_proposal_to_source(
                    proposal,
                    fragments=fragments,
                    existing_source_ids=registry,
                )
                registry[merged_source.source_id] = merged_source
            except Exception as exc:
                errors.append(
                    {
                        "stage": "fragment_merge",
                        "source_id": "+".join(proposal.block_ids),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )


def discover_marker_sources(
    document: Any,
    config: MermaidConfig | Mapping[str, Any] | None = None,
) -> MarkerDiscoveryResult:
    """Convenience entry point for one-shot Marker source discovery."""

    return MarkerSourceDiscovery(config).discover(document)


def iter_marker_candidate_blocks(document: Any) -> Iterable[Any]:
    """Yield every unique Marker candidate block in document reading order.

    This is the shared traversal boundary for discovery, reconstruction, and
    rendering.  Marker blocks that exist only in ``current_children`` must not
    disappear after discovery merely because ``Document.contained_blocks()`` does
    not return them.
    """

    seen: set[str] = set()
    for page in _iter_pages(document):
        for block in _iter_candidate_blocks(page, document):
            block_id = _block_id(block)
            if block_id in seen:
                continue
            seen.add(block_id)
            yield block


def merge_proposal_to_source(
    proposal: FragmentMergeProposal,
    *,
    fragments: Mapping[str, SourceFragment],
    existing_source_ids: Mapping[str, object] | Iterable[str] = (),
) -> DiscoveredSource:
    """Convert one merge proposal into positioned registry metadata.

    Same-page fragments use their page bounding boxes to retain relative placement.
    They must also share one pixel-to-page scale: merged sources reuse original raw
    fragments, so accepting incompatible scales would place unscaled pixels in one
    corrupt coordinate system. Consecutive-page fragments are stacked in reading
    order because page coordinate systems reset at each page boundary. The
    fragment-id image registry remains unchanged.
    """

    block_ids = proposal.block_ids
    try:
        ordered = sorted(
            ((block_id, fragments[block_id]) for block_id in block_ids),
            key=lambda item: (
                item[1].page_id,
                (item[1].page_bbox or (0, 0, 0, 0))[1],
                (item[1].page_bbox or (0, 0, 0, 0))[0],
                item[0],
            ),
        )
    except KeyError as exc:
        raise ValueError(f"missing merge fragment for block {exc.args[0]!r}") from exc

    anchor_id = ordered[0][0]
    base_id = f"{_source_id_part(anchor_id)}--merged--{_source_id_part(ordered[1][0])}"
    source_id = _unique_source_id(base_id, existing_source_ids)
    same_page = ordered[0][1].page_id == ordered[1][1].page_id
    positioned: list[SourceFragment] = []
    offsets: list[tuple[float, float]] = []

    if same_page:
        page_boxes = [item[1].page_bbox for item in ordered]
        if any(box is None for box in page_boxes):
            raise ValueError("same-page merge requires page bboxes for every fragment")
        first_box = page_boxes[0]
        assert first_box is not None
        first_fragment = ordered[0][1]
        first_crop = first_fragment.crop_bbox or (
            0.0,
            0.0,
            float(first_fragment.image_size[0]),
            float(first_fragment.image_size[1]),
        )
        scale_x = (first_crop[2] - first_crop[0]) / (first_box[2] - first_box[0])
        scale_y = (first_crop[3] - first_crop[1]) / (first_box[3] - first_box[1])
        for (_, fragment), page_box in zip(ordered[1:], page_boxes[1:], strict=True):
            assert page_box is not None
            crop = fragment.crop_bbox or (
                0.0,
                0.0,
                float(fragment.image_size[0]),
                float(fragment.image_size[1]),
            )
            fragment_scale_x = (crop[2] - crop[0]) / (page_box[2] - page_box[0])
            fragment_scale_y = (crop[3] - crop[1]) / (page_box[3] - page_box[1])
            if not (
                math.isclose(
                    fragment_scale_x,
                    scale_x,
                    rel_tol=_SAME_PAGE_MERGE_SCALE_REL_TOLERANCE,
                    abs_tol=_SAME_PAGE_MERGE_SCALE_ABS_TOLERANCE,
                )
                and math.isclose(
                    fragment_scale_y,
                    scale_y,
                    rel_tol=_SAME_PAGE_MERGE_SCALE_REL_TOLERANCE,
                    abs_tol=_SAME_PAGE_MERGE_SCALE_ABS_TOLERANCE,
                )
            ):
                raise ValueError("same-page merge fragments use incompatible pixel scales")
        left = min(box[0] for box in page_boxes if box is not None)
        top = min(box[1] for box in page_boxes if box is not None)
        offsets = [
            ((box[0] - left) * scale_x, (box[1] - top) * scale_y)
            for box in page_boxes
            if box is not None
        ]
    else:
        cursor_y = 0.0
        for _, fragment in ordered:
            offsets.append((0.0, cursor_y))
            crop = fragment.crop_bbox or (
                0.0,
                0.0,
                float(fragment.image_size[0]),
                float(fragment.image_size[1]),
            )
            cursor_y += crop[3] - crop[1]

    for (_, fragment), offset in zip(ordered, offsets, strict=True):
        positioned.append(fragment.model_copy(update={"canvas_offset": offset}))

    source = DiscoveredSource(
        source_id=source_id,
        anchor_block_id=anchor_id,
        kind="merged",
        fragments=positioned,
        signals=proposal.signals,
        confidence=proposal.score,
    )
    return source


def _iter_pages(document: Any) -> Iterable[Any]:
    pages = getattr(document, "pages", ())
    return pages() if callable(pages) else pages


def _iter_candidate_blocks(page: Any, document: Any) -> Iterable[Any]:
    """Yield resolved candidate blocks, including loose ``current_children``."""

    seen: set[str] = set()
    contained = getattr(page, "contained_blocks", None)
    if callable(contained):
        block_types = _marker_block_types()
        for arguments in ((document, block_types), (document,), ()):
            try:
                values = contained(*arguments)
            except TypeError:
                continue
            if values is not None:
                for block in values:
                    if _is_target_block(block):
                        block_id = _block_id(block)
                        if block_id not in seen:
                            seen.add(block_id)
                            yield block
                break

    queue = list(_children_of(page))
    visited_objects: set[int] = set()
    while queue:
        child = queue.pop(0)
        block = _resolve_block(child, page, document)
        if block is None or id(block) in visited_objects:
            continue
        visited_objects.add(id(block))
        if _is_target_block(block):
            block_id = _block_id(block)
            if block_id not in seen:
                seen.add(block_id)
                yield block
        queue.extend(_children_of(block))


def _marker_block_types() -> tuple[Any, ...]:
    try:
        from marker.schema import BlockTypes
    except ImportError:
        return ("Figure", "Picture", "ComplexRegion")
    return (BlockTypes.Figure, BlockTypes.Picture, BlockTypes.ComplexRegion)


def _caption_block_types() -> tuple[Any, ...]:
    try:
        from marker.schema import BlockTypes
    except ImportError:
        return ("Caption",)
    caption = getattr(BlockTypes, "Caption", None)
    return (caption,) if caption is not None else ("Caption",)


def _is_target_block(block: Any) -> bool:
    return _type_name(getattr(block, "block_type", None)) in _TARGET_BLOCK_NAMES


def _type_name(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    if name:
        return str(name).replace("_", "").casefold()
    text = str(value).rsplit(".", maxsplit=1)[-1]
    return text.replace("_", "").casefold()


def _children_of(value: Any) -> Sequence[Any]:
    result: list[Any] = []
    seen: set[tuple[type[Any], str]] = set()
    for attribute in ("current_children", "children", "structure"):
        children = getattr(value, attribute, None)
        if children:
            for child in children() if callable(children) else children:
                key = (type(child), str(child))
                if key not in seen:
                    seen.add(key)
                    result.append(child)
    return result


def _resolve_block(reference: Any, page: Any, document: Any) -> Any | None:
    if hasattr(reference, "block_type") or hasattr(reference, "polygon"):
        return reference
    for owner, argument_sets in (
        (document, ((reference,), (str(reference),))),
        (page, ((document, reference), (reference,), (document, str(reference)))),
    ):
        getter = getattr(owner, "get_block", None)
        if not callable(getter):
            continue
        for arguments in argument_sets:
            try:
                block = getter(*arguments)
            except (KeyError, TypeError, ValueError):
                continue
            if block is not None:
                return block
    return None


def _object_bbox(value: Any) -> BBox:
    polygon = getattr(value, "polygon", None)
    raw = getattr(polygon, "bbox", None) if polygon is not None else None
    raw = raw or getattr(value, "bbox", None)
    if callable(raw):
        raw = raw()
    if raw is None or len(raw) != 4:
        raise ValueError(f"{type(value).__name__} does not expose a four-value bbox")
    bbox = tuple(float(coordinate) for coordinate in raw)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError("object bbox must have positive area")
    return bbox  # type: ignore[return-value]


def _bbox_size(bbox: BBox) -> tuple[float, float]:
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _nearest_page_anchor(bbox: BBox, sources: Sequence[_BlockSource]) -> _BlockSource | None:
    if not sources:
        return None
    center_x = (bbox[0] + bbox[2]) / 2
    center_y = (bbox[1] + bbox[3]) / 2
    return min(
        sources,
        key=lambda source: (
            ((source.page_bbox[0] + source.page_bbox[2]) / 2 - center_x) ** 2
            + ((source.page_bbox[1] + source.page_bbox[3]) / 2 - center_y) ** 2,
            source.page_bbox[1],
            source.page_bbox[0],
            source.block_id,
        ),
    )


def _block_id(block: Any) -> str:
    identifier = getattr(block, "id", None)
    if identifier is None:
        raise ValueError(f"{type(block).__name__} does not expose an id")
    return str(identifier)


def _block_source_id(block: Any, fallback: str) -> str:
    identifier = getattr(block, "id", None)
    to_path = getattr(identifier, "to_path", None)
    return str(to_path()) if callable(to_path) else _source_id_part(fallback)


def _source_id_part(value: str) -> str:
    normalized = value.strip().strip("/").replace("/", "_").replace(" ", "_")
    return normalized or "source"


def _unique_source_id(base: str, existing: Mapping[str, object] | Iterable[str]) -> str:
    keys = set(existing) if not isinstance(existing, Mapping) else set(existing.keys())
    if base not in keys:
        return base
    suffix = 2
    while f"{base}-{suffix}" in keys:
        suffix += 1
    return f"{base}-{suffix}"


def _page_id(block: Any, page: Any, fallback: int) -> int:
    for value in (getattr(block, "page_id", None), getattr(page, "page_id", None)):
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return fallback


def _block_image(block: Any, document: Any) -> Image.Image | None:
    getter = getattr(block, "get_image", None)
    values: list[Any] = []
    if callable(getter):
        for arguments, keywords in (((document,), {"highres": True}), ((document,), {}), ((), {})):
            try:
                values.append(getter(*arguments, **keywords))
                break
            except TypeError:
                continue
    values.append(getattr(block, "image", None))
    for value in values:
        if isinstance(value, Image.Image):
            return value.copy()
        if isinstance(value, bytes | bytearray | memoryview):
            with Image.open(BytesIO(bytes(value))) as opened:
                opened.load()
                return opened.copy()
    return None


def _caption_text(block: Any, document: Any) -> str | None:
    for attribute in ("caption", "caption_text"):
        value = getattr(block, attribute, None)
        text = _text_from_value(value, document)
        if text:
            return text
        values = value if isinstance(value, Sequence) else (value,)
        for reference in values:
            caption_block = _resolve_block(reference, block, document)
            text = _text_from_value(caption_block, document)
            if text:
                return text

    contained = getattr(block, "contained_blocks", None)
    if callable(contained):
        for arguments in ((document, _caption_block_types()), (document,), ()):
            try:
                children = contained(*arguments)
            except TypeError:
                continue
            for child in children or ():
                if _type_name(getattr(child, "block_type", None)) == "caption":
                    text = _text_from_value(child, document)
                    if text:
                        return text
            break

    for child_ref in _children_of(block):
        child = _resolve_block(child_ref, block, document)
        if child is not None and _type_name(getattr(child, "block_type", None)) == "caption":
            text = _text_from_value(child, document)
            if text:
                return text
    return None


def _text_from_value(value: Any, document: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return " ".join(value.split()) or None
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        parts = [_text_from_value(item, document) for item in value]
        joined = " ".join(part for part in parts if part)
        return joined or None
    text = getattr(value, "text", None)
    if isinstance(text, str) and text.strip():
        return " ".join(text.split())
    raw_text = getattr(value, "raw_text", None)
    if callable(raw_text):
        try:
            text = raw_text(document)
        except TypeError:
            text = raw_text()
        if isinstance(text, str) and text.strip():
            return " ".join(text.split())
    return None
