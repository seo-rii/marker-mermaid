"""Marker-optional extraction of PDF vector primitives into scene evidence.

The adapter intentionally uses duck typing instead of importing Marker or
PyMuPDF.  It understands common ``get_drawings()``/``get_text("dict")`` page
APIs as well as simple ``vector_primitives`` and ``vector_texts`` attributes,
which keeps it usable with Marker blocks, page wrappers, and deterministic
test fixtures.

Only closed rectangle, ellipse, and polygon primitives become scene nodes.
Open paths become relations only when both endpoints have an unambiguous node
match.  Missing or malformed vector data therefore fails closed: callers get
an ``unknown`` observation rather than speculative structure.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    SceneRelation,
    VisualEvidence,
)
from marker_mermaid.protocols import SourceContext

BBox = tuple[float, float, float, float]
Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class VectorText:
    """Text and position read directly from a vector PDF text layer."""

    text: str
    bbox: BBox
    color: str | None = None
    confidence: float = 0.99


@dataclass(frozen=True, slots=True)
class VectorPrimitive:
    """A normalized drawing primitive in source-image pixel coordinates."""

    kind: str
    bbox: BBox
    points: tuple[Point, ...] = ()
    fill_color: str | None = None
    stroke_color: str | None = None
    line_style: str | None = None
    closed: bool = False
    arrow_at_start: bool = False
    arrow_at_end: bool = False
    confidence: float = 0.95


@dataclass(frozen=True, slots=True)
class VectorObservation:
    """Normalized PDF vector observations in source-image coordinates."""

    canvas_size: tuple[int, int]
    texts: tuple[VectorText, ...] = ()
    primitives: tuple[VectorPrimitive, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_engine_observation(
        self,
        source_block_ids: Iterable[str],
        *,
        endpoint_tolerance: float = 8.0,
    ) -> EngineObservation:
        """Build conservative, evidence-backed Scene IR from primitives."""

        block_ids = list(dict.fromkeys(source_block_ids))
        primitives = _deduplicate_primitives(self.primitives)
        texts = _deduplicate_texts(self.texts)
        evidence: list[VisualEvidence] = []
        elements: list[SceneElement] = []
        records: list[tuple[VectorPrimitive, str]] = []

        for primitive in primitives:
            if not _is_node_primitive(primitive):
                continue
            evidence_id = f"vector-shape-{len(elements) + 1:03d}"
            element_id = f"vector-node-{len(elements) + 1:03d}"
            evidence.append(
                VisualEvidence(
                    id=evidence_id,
                    kind="contour",
                    bbox=primitive.bbox,
                    score=_probability(primitive.confidence),
                    source_block_ids=block_ids,
                )
            )
            elements.append(
                SceneElement(
                    id=element_id,
                    role="unknown",
                    bbox=primitive.bbox,
                    polygon=list(primitive.points) or None,
                    shape=_scene_shape(primitive.kind),
                    fill_color=primitive.fill_color,
                    border_color=primitive.stroke_color,
                    border_style=primitive.line_style,
                    confidence=_probability(primitive.confidence),
                    evidence_ids=[evidence_id],
                )
            )
            records.append((primitive, element_id))

        text_assignments: dict[str, list[tuple[VectorText, str]]] = {}
        for index, text_item in enumerate(texts, 1):
            evidence_id = f"vector-text-{index:03d}"
            evidence.append(
                VisualEvidence(
                    id=evidence_id,
                    kind="vector_text",
                    bbox=text_item.bbox,
                    text=text_item.text,
                    score=_probability(text_item.confidence),
                    source_block_ids=block_ids,
                )
            )
            containing = [
                element_id
                for primitive, element_id in records
                if _bbox_contains_point(primitive.bbox, _bbox_center(text_item.bbox))
            ]
            if len(containing) == 1:
                text_assignments.setdefault(containing[0], []).append((text_item, evidence_id))

        by_id = {element.id: element for element in elements}
        for element_id, assigned in text_assignments.items():
            assigned.sort(key=lambda item: (item[0].bbox[1], item[0].bbox[0], item[0].text))
            element = by_id[element_id]
            element.text = " ".join(item.text for item, _evidence_id in assigned)
            element.evidence_ids.extend(evidence_id for _item, evidence_id in assigned)

        relations: list[SceneRelation] = []
        relation_keys: set[tuple[str, str, bool, bool]] = set()
        open_primitives = [item for item in primitives if _is_open_path(item)]
        for index, primitive in enumerate(open_primitives, 1):
            line_evidence_id = f"vector-line-{index:03d}"
            evidence.append(
                VisualEvidence(
                    id=line_evidence_id,
                    kind="line_segment",
                    bbox=primitive.bbox,
                    score=_probability(primitive.confidence),
                    source_block_ids=block_ids,
                )
            )
            if len(primitive.points) < 2:
                continue
            source_id = _unique_endpoint_match(primitive.points[0], records, endpoint_tolerance)
            target_id = _unique_endpoint_match(primitive.points[-1], records, endpoint_tolerance)
            if source_id is None or target_id is None or source_id == target_id:
                continue
            points = list(primitive.points)
            arrow_start = primitive.arrow_at_start
            arrow_end = primitive.arrow_at_end
            if arrow_start and not arrow_end:
                source_id, target_id = target_id, source_id
                points.reverse()
                arrow_start, arrow_end = False, True
            key = (source_id, target_id, arrow_start, arrow_end)
            if key in relation_keys:
                continue
            relation_keys.add(key)
            relations.append(
                SceneRelation(
                    id=f"vector-relation-{len(relations) + 1:03d}",
                    source_id=source_id,
                    target_id=target_id,
                    relation_type="directed_connector" if arrow_start or arrow_end else "connector",
                    semantic_relation="unknown" if arrow_start or arrow_end else "association",
                    polyline=points,
                    arrow_at_start=arrow_start,
                    arrow_at_end=arrow_end,
                    line_style=primitive.line_style,
                    confidence=_probability(primitive.confidence),
                    evidence_ids=[line_evidence_id],
                )
            )

        warnings = list(self.warnings)
        if not primitives and not texts:
            warnings.append("vector engine found no PDF vector primitives or text")
        elif not elements:
            warnings.append("vector engine found no conservative closed node primitives")
        prediction = _prediction(elements, relations)
        scene_ir = None
        if elements:
            scene_ir = DiagramSceneIR(
                elements=elements,
                relations=relations,
                reading_direction=_reading_direction(elements, relations),
                diagram_type_candidates=prediction.candidates,
                coordinate_space="pixels",
                canvas_size=(float(self.canvas_size[0]), float(self.canvas_size[1])),
            )
        return EngineObservation(
            prediction=prediction,
            scene_ir=scene_ir,
            evidence=evidence,
            warnings=list(dict.fromkeys(warnings)),
        )


class VectorPrimitiveEngine:
    """Candidate engine for page-backed PDF drawing and text primitives.

    ``extractor`` is primarily an integration/testing seam.  The default
    extractor probes ``context.source_block`` and a page-like object reachable
    through its ``page``, ``document_page``, or ``page_ref`` attribute.
    """

    name = "vector_primitives"
    fusion_source = "vector"

    def __init__(
        self,
        *,
        extractor: Callable[[Any, tuple[int, int]], VectorObservation] | None = None,
        endpoint_tolerance: float = 8.0,
        max_primitives: int = 2048,
    ):
        if endpoint_tolerance < 0:
            raise ValueError("endpoint_tolerance must be non-negative")
        if max_primitives < 1:
            raise ValueError("max_primitives must be positive")
        self.extractor = extractor
        self.endpoint_tolerance = endpoint_tolerance
        self.max_primitives = max_primitives

    def observe(self, context: SourceContext) -> EngineObservation:
        sources = _context_sources(context)
        observations = [
            self.extractor(source, context.image.size)
            if self.extractor is not None
            else extract_vector_observation(
                source,
                context.image.size,
                max_primitives=self.max_primitives,
                source_mapping=context.source_mapping,
            )
            for source in sources
        ]
        observation = _combine_observations(observations, context.image.size)
        return observation.to_engine_observation(
            context.source_block_ids,
            endpoint_tolerance=self.endpoint_tolerance,
        )


def _context_sources(context: SourceContext) -> list[Any]:
    """Prefer all assembled-source blocks while retaining old contexts."""

    source_blocks = getattr(context, "source_blocks", None)
    if source_blocks:
        return list(source_blocks)
    return [context.source_block]


def _combine_observations(
    observations: Iterable[VectorObservation],
    canvas_size: tuple[int, int],
) -> VectorObservation:
    materialized = list(observations)
    return VectorObservation(
        canvas_size=canvas_size,
        texts=tuple(item for observation in materialized for item in observation.texts),
        primitives=tuple(item for observation in materialized for item in observation.primitives),
        warnings=tuple(item for observation in materialized for item in observation.warnings),
    )


def extract_vector_observation(
    source: Any,
    canvas_size: tuple[int, int],
    *,
    max_primitives: int = 2048,
    source_mapping: Mapping[str, Any] | None = None,
) -> VectorObservation:
    """Extract normalized vector records from a block or page-like provider.

    When vectors live on a nested page object, page coordinates are clipped
    and mapped through the source block bbox into ``canvas_size``.  Records
    exposed directly by the source are assumed to already use image-local
    coordinates unless ``vector_coordinate_space == "page"`` is set.
    """

    if source is None:
        return VectorObservation(
            canvas_size=canvas_size,
            warnings=("vector source block is unavailable",),
        )
    providers = _providers(source)
    texts: list[VectorText] = []
    primitives: list[VectorPrimitive] = []
    malformed = 0
    used_fallback_transform = False
    for provider, nested in providers:
        transform, used_fallback = _provider_transform(
            source,
            provider,
            canvas_size,
            nested,
            source_mapping,
        )
        used_fallback_transform = used_fallback_transform or used_fallback
        raw_texts = _extract_raw_texts(provider)
        raw_drawings = _extract_raw_drawings(provider)
        for raw_text in raw_texts:
            try:
                item = _parse_text(raw_text)
                mapped = _map_text(item, transform)
                if mapped is not None:
                    texts.append(mapped)
            except (TypeError, ValueError, AttributeError):
                malformed += 1
        for raw_drawing in raw_drawings:
            parsed, failures = _parse_drawing(raw_drawing)
            malformed += failures
            for item in parsed:
                mapped = _map_primitive(item, transform)
                if mapped is not None:
                    primitives.append(mapped)

    warnings: list[str] = []
    if malformed:
        warnings.append(f"ignored {malformed} malformed vector record(s)")
    if used_fallback_transform:
        warnings.append("vector source mapping unavailable; used block/page bbox fallback")
    if len(primitives) > max_primitives:
        primitives = primitives[:max_primitives]
        warnings.append("vector primitives were truncated to the configured budget")
    return VectorObservation(
        canvas_size=canvas_size,
        texts=tuple(_deduplicate_texts(texts)),
        primitives=tuple(_deduplicate_primitives(primitives)),
        warnings=tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class _Transform:
    crop: BBox | None
    scale_x: float = 1.0
    scale_y: float = 1.0
    affine: tuple[float, float, float, float, float, float] | None = None

    def point(self, point: Point) -> Point:
        if self.affine is not None:
            a, b, c, d, e, f = self.affine
            return a * point[0] + b * point[1] + c, d * point[0] + e * point[1] + f
        if self.crop is None:
            return point
        return (
            (point[0] - self.crop[0]) * self.scale_x,
            (point[1] - self.crop[1]) * self.scale_y,
        )

    def bbox(self, bbox: BBox) -> BBox:
        if self.crop is None:
            return bbox
        left, top = self.point((bbox[0], bbox[1]))
        right, bottom = self.point((bbox[2], bbox[3]))
        return left, top, right, bottom

    def intersects(self, bbox: BBox) -> bool:
        if self.crop is None:
            return True
        return not (
            bbox[2] < self.crop[0]
            or bbox[0] > self.crop[2]
            or bbox[3] < self.crop[1]
            or bbox[1] > self.crop[3]
        )


def _providers(source: Any) -> list[tuple[Any, bool]]:
    result = [(source, False)]
    seen = {id(source)}
    for attribute in ("page", "document_page", "page_ref"):
        provider = _safe_attr(source, attribute)
        if provider is not None and id(provider) not in seen:
            seen.add(id(provider))
            result.append((provider, True))
    return result


def _provider_transform(
    source: Any,
    provider: Any,
    canvas_size: tuple[int, int],
    nested: bool,
    source_mapping: Mapping[str, Any] | None,
) -> tuple[_Transform, bool]:
    coordinate_space = str(_safe_attr(provider, "vector_coordinate_space") or "").lower()
    page_coordinates = nested or coordinate_space == "page"
    if not page_coordinates:
        return _Transform(None), False
    mapped = _mapping_transform(source_mapping, source)
    if mapped is not None:
        return mapped, False
    bbox = _source_bbox(source)
    if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return _Transform(None), True
    return (
        _Transform(
            bbox,
            canvas_size[0] / (bbox[2] - bbox[0]),
            canvas_size[1] / (bbox[3] - bbox[1]),
        ),
        True,
    )


def _mapping_transform(
    source_mapping: Mapping[str, Any] | None,
    source: Any,
) -> _Transform | None:
    if not isinstance(source_mapping, Mapping):
        return None
    assembly = source_mapping.get("assembly")
    if not isinstance(assembly, Mapping):
        return None
    placements = assembly.get("placements")
    if not isinstance(placements, Sequence) or isinstance(placements, str | bytes):
        return None
    block_id = _source_block_id(source)
    page_id = _source_page_id(source)
    candidates = [item for item in placements if isinstance(item, Mapping)]
    by_block = [
        item
        for item in candidates
        if block_id is not None
        and block_id in {str(value) for value in item.get("source_block_ids", ())}
    ]
    if by_block:
        candidates = by_block
    elif page_id is not None:
        by_page = [item for item in candidates if item.get("page_id") == page_id]
        if by_page:
            candidates = by_page
    if len(candidates) != 1:
        return None
    placement = candidates[0]
    affine = _as_affine(placement.get("page_to_canvas"))
    page_bbox = _as_bbox(placement.get("page_bbox"))
    if affine is None:
        return None
    return _Transform(page_bbox, affine=affine)


def _source_block_id(source: Any) -> str | None:
    identifier = _safe_attr(source, "id")
    if identifier is None:
        return None
    return str(identifier)


def _source_page_id(source: Any) -> int | None:
    value = _safe_attr(source, "page_id")
    if value is None:
        value = _safe_attr(source, "page_index")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_affine(value: Any) -> tuple[float, float, float, float, float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) != 6:
        return None
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in result):
        return None
    return result  # type: ignore[return-value]


def _source_bbox(source: Any) -> BBox | None:
    bbox = _as_bbox(_safe_attr(source, "bbox"))
    if bbox is not None:
        return bbox
    polygon = _safe_attr(source, "polygon")
    return _as_bbox(_safe_attr(polygon, "bbox"))


def _extract_raw_texts(provider: Any) -> list[Any]:
    for attribute in ("vector_texts", "text_spans"):
        value = _materialize(_safe_attr(provider, attribute))
        if value is not None:
            return value
    get_text = _safe_attr(provider, "get_text")
    if callable(get_text):
        try:
            payload = get_text("dict")
        except (TypeError, ValueError, RuntimeError):
            payload = None
        spans = _dict_text_spans(payload)
        if spans:
            return spans
        try:
            words = get_text("words")
        except (TypeError, ValueError, RuntimeError):
            words = None
        if isinstance(words, Sequence) and not isinstance(words, str | bytes):
            return [
                {"bbox": word[:4], "text": word[4]}
                for word in words
                if isinstance(word, Sequence) and len(word) >= 5
            ]
    return []


def _dict_text_spans(payload: Any) -> list[Any]:
    if not isinstance(payload, Mapping):
        return []
    spans: list[Any] = []
    for block in payload.get("blocks", ()):
        if not isinstance(block, Mapping):
            continue
        for line in block.get("lines", ()):
            if isinstance(line, Mapping):
                spans.extend(item for item in line.get("spans", ()) if isinstance(item, Mapping))
    return spans


def _extract_raw_drawings(provider: Any) -> list[Any]:
    for attribute in ("vector_primitives", "drawings", "paths"):
        value = _materialize(_safe_attr(provider, attribute))
        if value is not None:
            return value
    get_drawings = _safe_attr(provider, "get_drawings")
    if callable(get_drawings):
        try:
            value = get_drawings()
        except (TypeError, ValueError, RuntimeError):
            return []
        materialized = _materialize(value)
        return materialized or []
    return []


def _materialize(value: Any) -> list[Any] | None:
    if value is None or isinstance(value, str | bytes | Mapping):
        return None
    if callable(value):
        try:
            value = value()
        except (TypeError, ValueError, RuntimeError):
            return None
    if isinstance(value, Iterable):
        return list(value)
    return None


def _parse_text(raw: Any) -> VectorText:
    text = _get(raw, "text")
    bbox = _as_bbox(_get(raw, "bbox"))
    if not isinstance(text, str) or not text.strip() or bbox is None:
        raise ValueError("text and bbox are required")
    return VectorText(
        text=text.strip(),
        bbox=bbox,
        color=_as_color(_get(raw, "color")),
        confidence=_as_confidence(_get(raw, "confidence"), 0.99),
    )


def _parse_drawing(raw: Any) -> tuple[list[VectorPrimitive], int]:
    if isinstance(raw, Mapping) and isinstance(raw.get("items"), Iterable):
        style = {
            "fill_color": _as_color(raw.get("fill")),
            "stroke_color": _as_color(raw.get("color")),
            "line_style": _line_style(raw),
            "confidence": _as_confidence(raw.get("confidence"), 0.98),
        }
        parsed: list[VectorPrimitive] = []
        failures = 0
        for item in raw["items"]:
            try:
                parsed.append(_parse_pymupdf_item(item, style))
            except (TypeError, ValueError, AttributeError, IndexError):
                failures += 1
        return parsed, failures
    try:
        kind = str(_get(raw, "kind") or _get(raw, "type") or "").lower()
        bbox = _as_bbox(_get(raw, "bbox") or _get(raw, "rect"))
        points = _as_points(_get(raw, "points") or _get(raw, "polyline") or ())
        if bbox is None and points:
            bbox = _points_bbox(points)
        if not kind or bbox is None:
            raise ValueError("drawing kind and bbox/points are required")
        return [
            VectorPrimitive(
                kind=kind,
                bbox=bbox,
                points=points,
                fill_color=_as_color(_get(raw, "fill_color") or _get(raw, "fill")),
                stroke_color=_as_color(_get(raw, "stroke_color") or _get(raw, "color")),
                line_style=_line_style(raw),
                closed=bool(_get(raw, "closed"))
                or kind in {"rectangle", "rect", "ellipse", "polygon"},
                arrow_at_start=bool(_get(raw, "arrow_at_start")),
                arrow_at_end=bool(_get(raw, "arrow_at_end")),
                confidence=_as_confidence(_get(raw, "confidence"), 0.95),
            )
        ], 0
    except (TypeError, ValueError, AttributeError):
        return [], 1


def _parse_pymupdf_item(item: Any, style: dict[str, Any]) -> VectorPrimitive:
    if not isinstance(item, Sequence) or isinstance(item, str | bytes) or not item:
        raise ValueError("drawing item must be a command sequence")
    command = str(item[0]).lower()
    if command == "l" and len(item) >= 3:
        points = (_as_point(item[1]), _as_point(item[2]))
        return VectorPrimitive(kind="line", bbox=_points_bbox(points), points=points, **style)
    if command == "re" and len(item) >= 2:
        bbox = _as_bbox(item[1])
        if bbox is None:
            raise ValueError("rectangle bbox is missing")
        points = _bbox_polygon(bbox)
        return VectorPrimitive(kind="rectangle", bbox=bbox, points=points, closed=True, **style)
    if command == "qu" and len(item) >= 2:
        points = _quad_points(item[1])
        return VectorPrimitive(
            kind="polygon",
            bbox=_points_bbox(points),
            points=points,
            closed=True,
            **style,
        )
    if command == "c" and len(item) >= 5:
        points = tuple(_as_point(value) for value in item[1:5])
        return VectorPrimitive(kind="path", bbox=_points_bbox(points), points=points, **style)
    raise ValueError(f"unsupported drawing command: {command}")


def _map_text(item: VectorText, transform: _Transform) -> VectorText | None:
    if not transform.intersects(item.bbox):
        return None
    return VectorText(
        text=item.text,
        bbox=transform.bbox(item.bbox),
        color=item.color,
        confidence=item.confidence,
    )


def _map_primitive(item: VectorPrimitive, transform: _Transform) -> VectorPrimitive | None:
    if not transform.intersects(item.bbox):
        return None
    return VectorPrimitive(
        kind=item.kind,
        bbox=transform.bbox(item.bbox),
        points=tuple(transform.point(point) for point in item.points),
        fill_color=item.fill_color,
        stroke_color=item.stroke_color,
        line_style=item.line_style,
        closed=item.closed,
        arrow_at_start=item.arrow_at_start,
        arrow_at_end=item.arrow_at_end,
        confidence=item.confidence,
    )


def _safe_attr(value: Any, name: str) -> Any:
    try:
        return getattr(value, name, None)
    except (AttributeError, RuntimeError, ValueError):
        return None


def _get(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return _safe_attr(value, name)


def _as_bbox(value: Any) -> BBox | None:
    if value is None:
        return None
    if all(hasattr(value, name) for name in ("x0", "y0", "x1", "y1")):
        result = tuple(float(getattr(value, name)) for name in ("x0", "y0", "x1", "y1"))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes) and len(value) >= 4:
        result = tuple(float(item) for item in value[:4])
    else:
        return None
    if (
        not all(math.isfinite(item) for item in result)
        or result[2] < result[0]
        or result[3] < result[1]
    ):
        return None
    return result  # type: ignore[return-value]


def _as_point(value: Any) -> Point:
    if hasattr(value, "x") and hasattr(value, "y"):
        point = float(value.x), float(value.y)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes) and len(value) >= 2:
        point = float(value[0]), float(value[1])
    else:
        raise ValueError("point must have x/y coordinates")
    if not all(math.isfinite(item) for item in point):
        raise ValueError("point must be finite")
    return point


def _as_points(value: Any) -> tuple[Point, ...]:
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | Mapping):
        return ()
    return tuple(_as_point(item) for item in value)


def _quad_points(value: Any) -> tuple[Point, ...]:
    for attributes in (("ul", "ur", "lr", "ll"), ("p1", "p2", "p3", "p4")):
        if all(hasattr(value, name) for name in attributes):
            return tuple(_as_point(getattr(value, name)) for name in attributes)
    points = _as_points(value)
    if len(points) < 4:
        raise ValueError("quad must contain four points")
    return points[:4]


def _as_color(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip().lower()
        return stripped or None
    if isinstance(value, int) and 0 <= value <= 0xFFFFFF:
        return f"#{value:06x}"
    if isinstance(value, Sequence) and not isinstance(value, str | bytes) and len(value) >= 3:
        channels = [float(item) for item in value[:3]]
        if all(0 <= item <= 1 for item in channels):
            channels = [round(item * 255) for item in channels]
        if all(0 <= item <= 255 for item in channels):
            return "#" + "".join(f"{round(item):02x}" for item in channels)
    return None


def _line_style(value: Any) -> str | None:
    dashes = _get(value, "dashes") or _get(value, "dash")
    if dashes and str(dashes).strip() not in {"[] 0", "[]", "0"}:
        return "dashed"
    width = _get(value, "width") or _get(value, "stroke_width")
    try:
        if width is not None and float(width) >= 2.0:
            return "thick"
    except (TypeError, ValueError):
        pass
    return "solid" if width is not None or _get(value, "color") is not None else None


def _as_confidence(value: Any, default: float) -> float:
    try:
        return _probability(float(value)) if value is not None else default
    except (TypeError, ValueError):
        return default


def _probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _bbox_polygon(bbox: BBox) -> tuple[Point, ...]:
    return ((bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[2], bbox[3]), (bbox[0], bbox[3]))


def _points_bbox(points: Iterable[Point]) -> BBox:
    materialized = tuple(points)
    if not materialized:
        raise ValueError("points must not be empty")
    xs = [point[0] for point in materialized]
    ys = [point[1] for point in materialized]
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_center(bbox: BBox) -> Point:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def _bbox_contains_point(bbox: BBox, point: Point) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _bbox_iou(left: BBox, right: BBox) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = width * height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _deduplicate_texts(texts: Iterable[VectorText]) -> list[VectorText]:
    selected: dict[tuple[str, BBox], VectorText] = {}
    for item in texts:
        if item.bbox[2] <= item.bbox[0] or item.bbox[3] <= item.bbox[1]:
            continue
        key = item.text, item.bbox
        if key not in selected or item.confidence > selected[key].confidence:
            selected[key] = item
    return sorted(selected.values(), key=lambda item: (item.bbox, item.text))


def _deduplicate_primitives(primitives: Iterable[VectorPrimitive]) -> list[VectorPrimitive]:
    ordered = sorted(
        primitives,
        key=lambda item: (item.bbox, item.kind, item.points, -_probability(item.confidence)),
    )
    selected: list[VectorPrimitive] = []
    for item in ordered:
        if item.bbox[2] < item.bbox[0] or item.bbox[3] < item.bbox[1]:
            continue
        if any(
            item.kind == existing.kind
            and item.points == existing.points
            and _bbox_iou(item.bbox, existing.bbox) >= 0.98
            for existing in selected
        ):
            continue
        selected.append(item)
    return selected


def _is_node_primitive(item: VectorPrimitive) -> bool:
    return (
        item.closed
        and item.kind.lower() in {"rectangle", "rect", "ellipse", "polygon"}
        and (item.bbox[2] > item.bbox[0] and item.bbox[3] > item.bbox[1])
    )


def _scene_shape(kind: str) -> str:
    return "rectangle" if kind.lower() == "rect" else kind.lower()


def _is_open_path(item: VectorPrimitive) -> bool:
    return not item.closed and item.kind.lower() in {"line", "path", "polyline", "connector"}


def _point_bbox_distance(point: Point, bbox: BBox) -> float:
    dx = max(bbox[0] - point[0], 0.0, point[0] - bbox[2])
    dy = max(bbox[1] - point[1], 0.0, point[1] - bbox[3])
    return math.hypot(dx, dy)


def _unique_endpoint_match(
    point: Point,
    records: list[tuple[VectorPrimitive, str]],
    tolerance: float,
) -> str | None:
    matches = sorted(
        (
            (_point_bbox_distance(point, primitive.bbox), element_id)
            for primitive, element_id in records
        ),
        key=lambda item: (item[0], item[1]),
    )
    if not matches or matches[0][0] > tolerance:
        return None
    if len(matches) > 1 and math.isclose(matches[0][0], matches[1][0], abs_tol=1e-6):
        return None
    return matches[0][1]


def _prediction(
    elements: list[SceneElement], relations: list[SceneRelation]
) -> DiagramTypePrediction:
    if not elements:
        return DiagramTypePrediction(
            candidates=["unknown"],
            scores=[1.0],
            negative_signals=["no conservative closed vector nodes"],
        )
    if relations:
        return DiagramTypePrediction(
            candidates=["flowchart", "generic_network"],
            scores=[0.65, 0.35],
            visual_signals=["PDF vector node shapes", "PDF vector connector paths"],
        )
    return DiagramTypePrediction(
        candidates=["generic_network", "flowchart"],
        scores=[0.55, 0.45],
        visual_signals=["PDF vector node shapes"],
        negative_signals=["no unambiguous vector connectors"],
    )


def _reading_direction(elements: list[SceneElement], relations: list[SceneRelation]) -> str:
    by_id = {item.id: item for item in elements}
    deltas: list[Point] = []
    for relation in relations:
        source = by_id.get(relation.source_id or "")
        target = by_id.get(relation.target_id or "")
        if source is None or target is None:
            continue
        source_center = _bbox_center(source.bbox)
        target_center = _bbox_center(target.bbox)
        deltas.append((target_center[0] - source_center[0], target_center[1] - source_center[1]))
    if not deltas:
        return "unknown"
    dx = sum(item[0] for item in deltas)
    dy = sum(item[1] for item in deltas)
    if abs(dx) >= abs(dy):
        return "LR" if dx >= 0 else "RL"
    return "TB" if dy >= 0 else "BT"
