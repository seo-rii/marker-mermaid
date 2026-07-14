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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from itertools import chain
from typing import Any, Literal, TypeVar

from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    MAX_ID_CHARS,
    MAX_OBSERVATION_EVIDENCE,
    MAX_OBSERVATION_WARNINGS,
    MAX_SCENE_ELEMENTS,
    MAX_TEXT_CHARS,
    MAX_WARNING_CHARS,
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    SceneRelation,
    VisualEvidence,
)
from marker_mermaid.protocols import SourceContext
from marker_mermaid.resource_limits import MAX_EVIDENCE_INPUT_CHARS

BBox = tuple[float, float, float, float]
Point = tuple[float, float]
_T = TypeVar("_T")

DEFAULT_MAX_VECTOR_PRIMITIVES = 2048
DEFAULT_MAX_VECTOR_TEXTS = MAX_SCENE_ELEMENTS
DEFAULT_MAX_VECTOR_TEXT_CHARS = MAX_EVIDENCE_INPUT_CHARS
MAX_VECTOR_SOURCES = MAX_EVIDENCE_REFS
MAX_VECTOR_POLYGON_POINTS = 256
MAX_VECTOR_POLYLINE_POINTS = 512
MAX_VECTOR_TOTAL_POINTS = 100_000
MAX_VECTOR_TOKEN_CHARS = 256
MAX_VECTOR_DEDUP_COMPARISONS = 250_000
MAX_VECTOR_TEXT_MATCH_COMPARISONS = 1_000_000
MAX_VECTOR_ENDPOINT_MATCH_COMPARISONS = 1_000_000


def _bounded_items(
    values: Iterable[_T],
    limit: int,
    *,
    char_counter: Callable[[_T], int] | None = None,
    max_chars: int | None = None,
) -> tuple[list[_T], bool, int]:
    """Consume at most one item beyond the configured count/character prefix."""

    iterator = iter(values)
    selected: list[_T] = []
    char_count = 0
    while len(selected) < limit:
        try:
            item = next(iterator)
        except StopIteration:
            return selected, False, char_count
        item_chars = char_counter(item) if char_counter is not None else 0
        if item_chars < 0:
            item_chars = 0
        if max_chars is not None and char_count + item_chars > max_chars:
            return selected, True, char_count
        selected.append(item)
        char_count += item_chars
    try:
        next(iterator)
    except StopIteration:
        return selected, False, char_count
    return selected, True, char_count


def _bounded_primitives(
    values: Iterable[object],
    max_records: int,
    max_points: int,
) -> tuple[list[object], bool, bool, int, int]:
    """Bound raw primitive visits while allowing later point-free records."""

    iterator = iter(values)
    selected: list[object] = []
    records_seen = 0
    points_seen = 0
    point_truncated = False
    while records_seen < max_records:
        try:
            item = next(iterator)
        except StopIteration:
            return selected, False, point_truncated, points_seen, records_seen
        records_seen += 1
        point_count = _vector_primitive_points(item)
        if points_seen + point_count > max_points:
            point_truncated = True
            continue
        selected.append(item)
        points_seen += point_count
    try:
        next(iterator)
    except StopIteration:
        return selected, False, point_truncated, points_seen, records_seen
    return selected, True, point_truncated, points_seen, records_seen


def _validate_vector_budgets(
    max_primitives: int,
    max_texts: int,
    max_text_chars: int,
    max_points: int = MAX_VECTOR_TOTAL_POINTS,
) -> None:
    if type(max_primitives) is not int or max_primitives < 0:
        raise ValueError("max_primitives must be a non-negative integer")
    if max_primitives > MAX_SCENE_ELEMENTS:
        raise ValueError("max_primitives cannot exceed the scene element limit")
    if type(max_texts) is not int or max_texts < 0:
        raise ValueError("max_texts must be a non-negative integer")
    if max_primitives + max_texts > MAX_OBSERVATION_EVIDENCE:
        raise ValueError("combined vector budgets cannot exceed the observation evidence limit")
    if (
        type(max_text_chars) is not int
        or max_text_chars < 0
        or max_text_chars > MAX_EVIDENCE_INPUT_CHARS
    ):
        raise ValueError("max_text_chars must be between zero and the evidence character limit")
    if type(max_points) is not int or not 0 <= max_points <= MAX_VECTOR_TOTAL_POINTS:
        raise ValueError("max_points must be between zero and the vector point limit")


def _bounded_work_count(
    reported: object,
    retained: int,
    limit: int,
) -> tuple[int, bool]:
    """Return a fail-closed raw-work count and whether metadata was invalid."""

    invalid = False
    if reported is None:
        value = retained
    elif type(reported) is int and reported >= 0:
        value = max(reported, retained)
    else:
        value = retained
        invalid = True
    if value > limit:
        value = limit
        invalid = True
    return value, invalid


def _valid_canvas_size(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 2
        and all(
            type(item) is int and _finite_number(item) is not None and item > 0 for item in value
        )
    )


@dataclass(frozen=True, slots=True)
class VectorText:
    """Text and position read directly from a vector PDF text layer."""

    text: str
    bbox: BBox
    color: str | None = None
    confidence: float = 0.99
    font_weight: Literal["normal", "bold"] | None = None


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
    primitive_records_seen: int | None = None
    primitive_points_seen: int | None = None
    text_records_seen: int | None = None
    text_chars_seen: int | None = None
    primitive_budget_exhausted: bool = False
    primitive_point_budget_exhausted: bool = False
    text_count_budget_exhausted: bool = False
    text_char_budget_exhausted: bool = False

    def to_engine_observation(
        self,
        source_block_ids: Iterable[str],
        *,
        endpoint_tolerance: float = 8.0,
        max_primitives: int = DEFAULT_MAX_VECTOR_PRIMITIVES,
        max_texts: int = DEFAULT_MAX_VECTOR_TEXTS,
        max_text_chars: int = DEFAULT_MAX_VECTOR_TEXT_CHARS,
        max_points: int = MAX_VECTOR_TOTAL_POINTS,
    ) -> EngineObservation:
        """Build conservative, evidence-backed Scene IR from primitives."""

        _validate_vector_budgets(
            max_primitives,
            max_texts,
            max_text_chars,
            max_points,
        )
        if not _valid_canvas_size(self.canvas_size):
            return EngineObservation(
                prediction=_prediction([], []),
                warnings=("invalid vector canvas size was isolated",),
            )
        finite_endpoint_tolerance = _finite_number(endpoint_tolerance)
        if finite_endpoint_tolerance is None or finite_endpoint_tolerance < 0:
            return EngineObservation(
                prediction=_prediction([], []),
                warnings=("invalid vector endpoint tolerance was isolated",),
            )
        endpoint_tolerance = finite_endpoint_tolerance
        boundary_warnings: list[str] = []
        try:
            block_id_inputs, block_ids_truncated, _block_chars = _bounded_items(
                source_block_ids,
                MAX_EVIDENCE_REFS,
            )
        except TypeError:
            block_id_inputs = []
            block_ids_truncated = False
            boundary_warnings.append("invalid vector source-block collection was omitted")
        invalid_block_ids = False
        for block_id in block_id_inputs:
            if type(block_id) is not str or not block_id or len(block_id) > MAX_ID_CHARS:
                invalid_block_ids = True
                break
            try:
                block_id.encode("utf-8")
            except UnicodeEncodeError:
                invalid_block_ids = True
                break
        block_ids = (
            [] if block_ids_truncated or invalid_block_ids else list(dict.fromkeys(block_id_inputs))
        )
        if max_primitives > 0:
            try:
                (
                    primitive_inputs,
                    primitive_count_truncated,
                    primitive_point_truncated,
                    _primitive_points,
                    _primitive_records,
                ) = _bounded_primitives(
                    self.primitives,
                    max_primitives,
                    max_points,
                )
            except TypeError:
                primitive_inputs = []
                primitive_count_truncated = False
                primitive_point_truncated = False
                boundary_warnings.append("invalid vector primitive collection was omitted")
        else:
            primitive_inputs = []
            primitive_count_truncated = False
            primitive_point_truncated = False
        if max_texts > 0 and max_text_chars > 0:
            try:
                text_inputs, texts_truncated, _text_chars = _bounded_items(
                    self.texts,
                    max_texts,
                    char_counter=_vector_text_chars,
                    max_chars=max_text_chars,
                )
            except TypeError:
                text_inputs = []
                texts_truncated = False
                boundary_warnings.append("invalid vector text collection was omitted")
        else:
            text_inputs = []
            texts_truncated = False
        budget_warnings = list(boundary_warnings)
        if block_ids_truncated:
            budget_warnings.append(
                "vector source-block provenance exceeded its input budget and was omitted"
            )
        elif invalid_block_ids:
            budget_warnings.append("invalid vector source-block provenance was omitted atomically")
        if primitive_count_truncated:
            budget_warnings.append(
                "vector primitive input budget reached; remaining primitives were omitted"
            )
        if primitive_point_truncated:
            budget_warnings.append(
                "vector aggregate point budget reached; remaining point geometry was omitted"
            )
        if texts_truncated:
            if len(text_inputs) >= max_texts:
                budget_warnings.append(
                    "vector text count budget reached; remaining spans were omitted"
                )
            else:
                budget_warnings.append(
                    "vector text character budget reached; remaining spans were omitted"
                )
        valid_primitive_inputs: list[VectorPrimitive] = []
        invalid_primitive = False
        for item in primitive_inputs:
            if type(item) is not VectorPrimitive or not _valid_vector_primitive(item):
                invalid_primitive = True
                continue
            valid_primitive_inputs.append(item)
        primitives, primitive_dedupe_limited = _deduplicate_primitives(valid_primitive_inputs)
        valid_text_inputs: list[VectorText] = []
        invalid_text = False
        for item in text_inputs:
            if type(item) is not VectorText or not _valid_vector_text(item):
                invalid_text = True
                continue
            valid_text_inputs.append(item)
        if invalid_primitive:
            budget_warnings.append("invalid vector primitive records were omitted")
        if primitive_dedupe_limited:
            budget_warnings.append(
                "vector approximate-duplicate comparison budget reached; remaining "
                "primitives kept without approximate deduplication"
            )
        if invalid_text:
            budget_warnings.append("invalid or oversized vector text records were omitted")
        texts, dedupe_warnings = _deduplicate_texts(valid_text_inputs)
        evidence: list[VisualEvidence] = []
        elements: list[SceneElement] = []
        records: list[tuple[VectorPrimitive, str]] = []
        text_warnings: list[str] = []

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
        text_match_comparisons = 0
        text_matching_limited = False
        for index, text_item in enumerate(texts, 1):
            evidence_id = f"vector-text-{index:03d}"
            evidence.append(
                VisualEvidence(
                    id=evidence_id,
                    kind="vector_text",
                    bbox=text_item.bbox,
                    text=text_item.text,
                    font_weight=text_item.font_weight,
                    score=_probability(text_item.confidence),
                    source_block_ids=block_ids,
                )
            )
            containing: list[str] = []
            if not text_matching_limited:
                text_center = _bbox_center(text_item.bbox)
                for primitive, element_id in records:
                    if text_match_comparisons >= MAX_VECTOR_TEXT_MATCH_COMPARISONS:
                        text_matching_limited = True
                        containing = []
                        break
                    text_match_comparisons += 1
                    if _bbox_contains_point(primitive.bbox, text_center):
                        containing.append(element_id)
            if len(containing) == 1:
                text_assignments.setdefault(containing[0], []).append((text_item, evidence_id))

        if text_matching_limited:
            text_warnings.append(
                "vector text-to-node comparison budget reached; remaining labels stayed unassigned"
            )

        by_id = {element.id: (index, element) for index, element in enumerate(elements)}
        for element_id, assigned in text_assignments.items():
            assigned.sort(key=lambda item: (item[0].bbox[1], item[0].bbox[0], item[0].text))
            element_index, element = by_id[element_id]
            weights = {item.font_weight for item, _evidence_id in assigned}
            font_weight = None
            mixed_font_weight = False
            if weights == {"bold"}:
                font_weight = "bold"
            elif weights == {"normal"}:
                font_weight = "normal"
            elif weights - {None}:
                mixed_font_weight = True

            enriched = element.model_dump(mode="python")
            enriched["text"] = " ".join(item.text for item, _evidence_id in assigned)
            enriched["evidence_ids"] = [
                *element.evidence_ids,
                *(evidence_id for _item, evidence_id in assigned),
            ]
            enriched["font_weight"] = font_weight
            try:
                updated = SceneElement.model_validate(enriched)
            except ValueError:
                text_warnings.append(
                    f"vector text enrichment exceeded Scene field or reference limits for "
                    f"{element_id}; label and emphasis omitted"
                )
                continue
            elements[element_index] = updated
            by_id[element_id] = (element_index, updated)
            if mixed_font_weight:
                text_warnings.append(
                    f"vector text weight was mixed or partial for {element_id}; emphasis omitted"
                )

        relations: list[SceneRelation] = []
        relation_keys: set[tuple[str, str, bool, bool]] = set()
        open_primitives = [item for item in primitives if _is_open_path(item)]
        endpoint_match_comparisons = 0
        endpoint_matching_limited = False
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
            if endpoint_matching_limited:
                continue
            source_id, comparisons_used, comparison_limit_reached = _unique_endpoint_match(
                primitive.points[0],
                records,
                endpoint_tolerance,
                max_comparisons=(
                    MAX_VECTOR_ENDPOINT_MATCH_COMPARISONS - endpoint_match_comparisons
                ),
            )
            endpoint_match_comparisons += comparisons_used
            if comparison_limit_reached:
                endpoint_matching_limited = True
                continue
            target_id, comparisons_used, comparison_limit_reached = _unique_endpoint_match(
                primitive.points[-1],
                records,
                endpoint_tolerance,
                max_comparisons=(
                    MAX_VECTOR_ENDPOINT_MATCH_COMPARISONS - endpoint_match_comparisons
                ),
            )
            endpoint_match_comparisons += comparisons_used
            if comparison_limit_reached:
                endpoint_matching_limited = True
                continue
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
                    line_color=primitive.stroke_color,
                    line_style=primitive.line_style,
                    confidence=_probability(primitive.confidence),
                    evidence_ids=[line_evidence_id],
                )
            )
        if endpoint_matching_limited:
            text_warnings.append(
                "vector endpoint comparison budget reached; remaining connectors stayed unresolved"
            )

        terminal_warnings: list[str] = []
        if not primitives and not texts:
            terminal_warnings.append("vector engine found no PDF vector primitives or text")
        elif not elements:
            terminal_warnings.append("vector engine found no conservative closed node primitives")
        try:
            warning_inputs, warnings_truncated, _warning_chars = _bounded_items(
                chain(
                    self.warnings,
                    budget_warnings,
                    dedupe_warnings,
                    text_warnings,
                    terminal_warnings,
                ),
                MAX_OBSERVATION_WARNINGS,
            )
        except TypeError:
            budget_warnings.append("invalid vector warning collection was omitted")
            warning_inputs, warnings_truncated, _warning_chars = _bounded_items(
                chain(
                    budget_warnings,
                    dedupe_warnings,
                    text_warnings,
                    terminal_warnings,
                ),
                MAX_OBSERVATION_WARNINGS,
            )
        warnings: list[str] = []
        invalid_warning = False
        for warning in warning_inputs:
            if type(warning) is not str or not warning or len(warning) > MAX_WARNING_CHARS:
                invalid_warning = True
                continue
            try:
                warning.encode("utf-8")
            except UnicodeEncodeError:
                invalid_warning = True
                continue
            if warning not in warnings:
                warnings.append(warning)
        if warnings_truncated or invalid_warning:
            truncation_warning = "vector warnings were truncated to the observation budget"
            if len(warnings) >= MAX_OBSERVATION_WARNINGS:
                warnings[-1] = truncation_warning
            elif truncation_warning not in warnings:
                warnings.append(truncation_warning)
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
            warnings=warnings,
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
        max_primitives: int = DEFAULT_MAX_VECTOR_PRIMITIVES,
        max_texts: int = DEFAULT_MAX_VECTOR_TEXTS,
        max_text_chars: int = DEFAULT_MAX_VECTOR_TEXT_CHARS,
        max_points: int = MAX_VECTOR_TOTAL_POINTS,
    ):
        finite_endpoint_tolerance = _finite_number(endpoint_tolerance)
        if finite_endpoint_tolerance is None or finite_endpoint_tolerance < 0:
            raise ValueError("endpoint_tolerance must be non-negative")
        _validate_vector_budgets(
            max_primitives,
            max_texts,
            max_text_chars,
            max_points,
        )
        if max_primitives < 1:
            raise ValueError("max_primitives must be positive")
        self.extractor = extractor
        self.endpoint_tolerance = finite_endpoint_tolerance
        self.max_primitives = max_primitives
        self.max_texts = max_texts
        self.max_text_chars = max_text_chars
        self.max_points = max_points

    def observe(self, context: SourceContext) -> EngineObservation:
        sources, source_warnings = _context_sources(context)
        primitives: list[VectorPrimitive] = []
        texts: list[VectorText] = []
        warnings = list(source_warnings)
        primitive_work = 0
        primitive_point_work = 0
        text_work = 0
        text_char_work = 0
        primitive_exhausted = self.max_primitives == 0
        primitive_point_exhausted = self.max_points == 0
        text_count_exhausted = self.max_texts == 0
        text_char_exhausted = self.max_text_chars == 0
        for source_index, source in enumerate(sources):
            remaining_primitives = max(0, self.max_primitives - primitive_work)
            remaining_points = max(0, self.max_points - primitive_point_work)
            remaining_texts = max(0, self.max_texts - text_work)
            remaining_text_chars = max(0, self.max_text_chars - text_char_work)
            if primitive_exhausted and (text_count_exhausted or text_char_exhausted):
                warnings.append(
                    "vector source collection was truncated at the reconstruction-global "
                    "record budget"
                )
                break
            if self.extractor is not None:
                raw_observation = self.extractor(source, context.image.size)
            else:
                raw_observation = extract_vector_observation(
                    source,
                    context.image.size,
                    max_primitives=remaining_primitives,
                    max_texts=(remaining_texts if remaining_text_chars > 0 else 0),
                    max_text_chars=remaining_text_chars,
                    max_points=remaining_points,
                    source_mapping=context.source_mapping,
                )
            bounded = _combine_observations(
                (raw_observation,),
                context.image.size,
                max_primitives=remaining_primitives,
                max_texts=(remaining_texts if remaining_text_chars > 0 else 0),
                max_text_chars=remaining_text_chars,
                max_points=remaining_points,
            )
            primitives.extend(bounded.primitives)
            texts.extend(bounded.texts)
            primitive_increment, invalid_primitive_work = _bounded_work_count(
                bounded.primitive_records_seen,
                len(bounded.primitives),
                remaining_primitives,
            )
            retained_points = sum(_vector_primitive_points(item) for item in bounded.primitives)
            point_increment, invalid_point_work = _bounded_work_count(
                bounded.primitive_points_seen,
                retained_points,
                remaining_points,
            )
            text_increment, invalid_text_work = _bounded_work_count(
                bounded.text_records_seen,
                len(bounded.texts),
                remaining_texts,
            )
            retained_text_chars = sum(_vector_text_chars(item) for item in bounded.texts)
            text_char_increment, invalid_text_char_work = _bounded_work_count(
                bounded.text_chars_seen,
                retained_text_chars,
                remaining_text_chars,
            )
            primitive_work += primitive_increment
            primitive_point_work += point_increment
            text_work += text_increment
            text_char_work += text_char_increment
            if (
                invalid_primitive_work
                or invalid_point_work
                or invalid_text_work
                or invalid_text_char_work
            ):
                warnings.append(
                    "invalid vector work metadata was clamped to the reconstruction budget"
                )
            primitive_exhausted = (
                bounded.primitive_budget_exhausted or primitive_work >= self.max_primitives
            )
            primitive_point_exhausted = (
                bounded.primitive_point_budget_exhausted or primitive_point_work >= self.max_points
            )
            text_count_exhausted = (
                bounded.text_count_budget_exhausted or text_work >= self.max_texts
            )
            text_char_exhausted = (
                bounded.text_char_budget_exhausted or text_char_work >= self.max_text_chars
            )
            if bounded.primitive_budget_exhausted:
                primitive_work = self.max_primitives
            if bounded.primitive_point_budget_exhausted:
                primitive_point_work = self.max_points
            if bounded.text_count_budget_exhausted:
                text_work = self.max_texts
            if bounded.text_char_budget_exhausted:
                text_char_work = self.max_text_chars
            warning_chunk, warnings_truncated, _warning_chars = _bounded_items(
                bounded.warnings,
                max(0, MAX_OBSERVATION_WARNINGS - len(warnings)),
            )
            warnings.extend(warning_chunk)
            if warnings_truncated:
                truncation_warning = "vector warnings were truncated to the observation budget"
                if len(warnings) >= MAX_OBSERVATION_WARNINGS:
                    warnings[-1] = truncation_warning
                elif truncation_warning not in warnings:
                    warnings.append(truncation_warning)
            if (
                source_index + 1 < len(sources)
                and primitive_exhausted
                and (text_count_exhausted or text_char_exhausted)
            ):
                warnings.append(
                    "vector source collection was truncated at the reconstruction-global "
                    "record budget"
                )
                break
        observation = VectorObservation(
            canvas_size=context.image.size,
            texts=tuple(texts),
            primitives=tuple(primitives),
            warnings=tuple(warnings),
            primitive_records_seen=primitive_work,
            primitive_points_seen=primitive_point_work,
            text_records_seen=text_work,
            text_chars_seen=text_char_work,
            primitive_budget_exhausted=primitive_exhausted,
            primitive_point_budget_exhausted=primitive_point_exhausted,
            text_count_budget_exhausted=text_count_exhausted,
            text_char_budget_exhausted=text_char_exhausted,
        )
        return observation.to_engine_observation(
            context.source_block_ids,
            endpoint_tolerance=self.endpoint_tolerance,
            max_primitives=self.max_primitives,
            max_texts=self.max_texts,
            max_text_chars=self.max_text_chars,
            max_points=self.max_points,
        )


def _context_sources(context: SourceContext) -> tuple[list[Any], list[str]]:
    """Prefer all assembled-source blocks while retaining old contexts."""

    warnings: list[str] = []
    for attribute in ("vector_sources", "source_blocks"):
        values = getattr(context, attribute, None)
        if values is None or isinstance(values, str | bytes | Mapping):
            continue
        try:
            sources, truncated, _chars = _bounded_items(values, MAX_VECTOR_SOURCES)
        except TypeError:
            continue
        if not sources:
            continue
        if truncated:
            warnings.append("vector source count budget reached; remaining sources were omitted")
        return sources, warnings
    return [context.source_block], warnings


def _combine_observations(
    observations: Iterable[VectorObservation],
    canvas_size: tuple[int, int],
    *,
    max_primitives: int = DEFAULT_MAX_VECTOR_PRIMITIVES,
    max_texts: int = DEFAULT_MAX_VECTOR_TEXTS,
    max_text_chars: int = DEFAULT_MAX_VECTOR_TEXT_CHARS,
    max_points: int = MAX_VECTOR_TOTAL_POINTS,
) -> VectorObservation:
    _validate_vector_budgets(
        max_primitives,
        max_texts,
        max_text_chars,
        max_points,
    )
    if not _valid_canvas_size(canvas_size):
        raise ValueError("canvas_size must be a positive integer pair")
    primitives: list[VectorPrimitive] = []
    texts: list[VectorText] = []
    warnings: list[str] = []
    primitive_work = 0
    primitive_point_work = 0
    text_work = 0
    text_char_work = 0
    primitive_exhausted = max_primitives == 0
    primitive_point_exhausted = max_points == 0
    text_count_exhausted = max_texts == 0
    text_char_exhausted = max_text_chars == 0
    iterator = iter(observations)
    observation_count = 0
    while observation_count < MAX_VECTOR_SOURCES:
        if primitive_exhausted and (text_count_exhausted or text_char_exhausted):
            try:
                next(iterator)
            except StopIteration:
                break
            warnings.append(
                "vector observation collection was truncated at the reconstruction-global "
                "record budget"
            )
            break
        try:
            observation = next(iterator)
        except StopIteration:
            break
        observation_count += 1
        if type(observation) is not VectorObservation:
            warnings.append("invalid vector observation records were omitted")
            continue
        remaining_primitives = max(0, max_primitives - primitive_work)
        remaining_points = max(0, max_points - primitive_point_work)
        if remaining_primitives > 0:
            try:
                (
                    primitive_chunk,
                    primitive_count_truncated,
                    primitive_point_truncated,
                    chunk_points,
                    chunk_records,
                ) = _bounded_primitives(
                    observation.primitives,
                    remaining_primitives,
                    remaining_points,
                )
            except TypeError:
                primitive_chunk = []
                primitive_count_truncated = False
                primitive_point_truncated = False
                chunk_points = 0
                chunk_records = 0
                warnings.append("invalid vector primitive collection was omitted")
        else:
            primitive_chunk = []
            primitive_count_truncated = False
            primitive_point_truncated = False
            chunk_points = 0
            chunk_records = 0
        primitives.extend(primitive_chunk)
        primitive_increment, invalid_primitive_work = _bounded_work_count(
            observation.primitive_records_seen,
            chunk_records,
            remaining_primitives,
        )
        point_increment, invalid_point_work = _bounded_work_count(
            observation.primitive_points_seen,
            chunk_points,
            remaining_points,
        )
        primitive_flag = observation.primitive_budget_exhausted
        primitive_point_flag = observation.primitive_point_budget_exhausted
        if type(primitive_flag) is not bool:
            primitive_flag = False
            invalid_primitive_work = True
        if type(primitive_point_flag) is not bool:
            primitive_point_flag = False
            invalid_point_work = True
        if primitive_count_truncated or primitive_flag:
            primitive_increment = remaining_primitives
        if primitive_point_truncated or primitive_point_flag:
            point_increment = remaining_points
        primitive_work += primitive_increment
        primitive_point_work += point_increment
        primitive_exhausted = (
            primitive_count_truncated or primitive_flag or primitive_work >= max_primitives
        )
        primitive_point_exhausted = (
            primitive_point_truncated or primitive_point_flag or primitive_point_work >= max_points
        )
        if primitive_count_truncated:
            warnings.append(
                "vector primitive input budget reached; remaining primitives were omitted"
            )
        if primitive_point_truncated:
            warnings.append(
                "vector aggregate point budget reached; remaining point geometry was omitted"
            )
        remaining_texts = max(0, max_texts - text_work)
        remaining_chars = max(0, max_text_chars - text_char_work)
        if remaining_texts > 0 and remaining_chars > 0:
            try:
                text_chunk, text_truncated, chunk_chars = _bounded_items(
                    observation.texts,
                    remaining_texts,
                    char_counter=_vector_text_chars,
                    max_chars=remaining_chars,
                )
            except TypeError:
                text_chunk = []
                text_truncated = False
                chunk_chars = 0
                warnings.append("invalid vector text collection was omitted")
        else:
            text_chunk = []
            text_truncated = False
            chunk_chars = 0
        texts.extend(text_chunk)
        text_increment, invalid_text_work = _bounded_work_count(
            observation.text_records_seen,
            len(text_chunk),
            remaining_texts,
        )
        text_char_increment, invalid_text_char_work = _bounded_work_count(
            observation.text_chars_seen,
            chunk_chars,
            remaining_chars,
        )
        text_count_flag = observation.text_count_budget_exhausted
        text_char_flag = observation.text_char_budget_exhausted
        if type(text_count_flag) is not bool:
            text_count_flag = False
            invalid_text_work = True
        if type(text_char_flag) is not bool:
            text_char_flag = False
            invalid_text_char_work = True
        count_truncated = text_truncated and len(text_chunk) >= remaining_texts
        char_truncated = text_truncated and not count_truncated
        if count_truncated or text_count_flag:
            text_increment = remaining_texts
        if char_truncated or text_char_flag:
            text_char_increment = remaining_chars
        text_work += text_increment
        text_char_work += text_char_increment
        text_count_exhausted = count_truncated or text_count_flag or text_work >= max_texts
        text_char_exhausted = char_truncated or text_char_flag or text_char_work >= max_text_chars
        if text_truncated:
            if count_truncated:
                warnings.append("vector text count budget reached; remaining spans were omitted")
            else:
                warnings.append(
                    "vector text character budget reached; remaining spans were omitted"
                )
        if (
            invalid_primitive_work
            or invalid_point_work
            or invalid_text_work
            or invalid_text_char_work
        ):
            warnings.append("invalid vector work metadata was clamped to the reconstruction budget")
        try:
            warning_chunk, warning_truncated, _warning_chars = _bounded_items(
                observation.warnings,
                max(0, MAX_OBSERVATION_WARNINGS - len(warnings)),
            )
        except TypeError:
            warning_chunk = []
            warning_truncated = False
            warnings.append("invalid vector warning collection was omitted")
        warnings.extend(warning_chunk)
        if warning_truncated:
            warnings.append("vector warnings were truncated to the observation budget")
            break
    else:
        try:
            next(iterator)
        except StopIteration:
            pass
        else:
            warnings.append("vector observation source count budget reached")
    return VectorObservation(
        canvas_size=canvas_size,
        texts=tuple(texts),
        primitives=tuple(primitives),
        warnings=tuple(warnings),
        primitive_records_seen=primitive_work,
        primitive_points_seen=primitive_point_work,
        text_records_seen=text_work,
        text_chars_seen=text_char_work,
        primitive_budget_exhausted=primitive_exhausted,
        primitive_point_budget_exhausted=primitive_point_exhausted,
        text_count_budget_exhausted=text_count_exhausted,
        text_char_budget_exhausted=text_char_exhausted,
    )


@dataclass(frozen=True, slots=True)
class _RawTextBatch:
    items: tuple[Any, ...] = ()
    records_seen: int = 0
    chars_seen: int = 0
    count_exhausted: bool = False
    char_exhausted: bool = False


def extract_vector_observation(
    source: Any,
    canvas_size: tuple[int, int],
    *,
    max_primitives: int = DEFAULT_MAX_VECTOR_PRIMITIVES,
    max_texts: int = DEFAULT_MAX_VECTOR_TEXTS,
    max_text_chars: int = DEFAULT_MAX_VECTOR_TEXT_CHARS,
    max_points: int = MAX_VECTOR_TOTAL_POINTS,
    source_mapping: Mapping[str, Any] | None = None,
) -> VectorObservation:
    """Extract normalized vector records from a block or page-like provider.

    When vectors live on a nested page object, page coordinates are clipped
    and mapped through the source block bbox into ``canvas_size``.  Records
    exposed directly by the source are assumed to already use image-local
    coordinates unless ``vector_coordinate_space == "page"`` is set.
    """

    _validate_vector_budgets(
        max_primitives,
        max_texts,
        max_text_chars,
        max_points,
    )
    if not _valid_canvas_size(canvas_size):
        raise ValueError("canvas_size must be a positive integer pair")
    if source is None:
        return VectorObservation(
            canvas_size=canvas_size,
            warnings=("vector source block is unavailable",),
        )
    providers: list[tuple[Any, bool]] = []
    mapping_required = False
    for provider, nested in _providers(source):
        coordinate_value = _safe_attr(provider, "vector_coordinate_space")
        coordinate_space = (
            coordinate_value.casefold()
            if type(coordinate_value) is str and _valid_bounded_optional_text(coordinate_value)
            else ""
        )
        page_coordinates = nested or coordinate_space == "page"
        mapping_required = mapping_required or page_coordinates
        providers.append((provider, page_coordinates))
    mapped_source_transform = (
        _mapping_transform(source_mapping, source) if mapping_required else None
    )
    texts: list[VectorText] = []
    primitives: list[VectorPrimitive] = []
    malformed = 0
    used_fallback_transform = False
    text_records = 0
    text_chars = 0
    primitive_records = 0
    primitive_points = 0
    text_count_truncated = False
    text_chars_truncated = False
    primitives_truncated = False
    primitive_points_truncated = False
    text_enabled = max_texts > 0 and max_text_chars > 0
    primitives_enabled = max_primitives > 0
    text_probe_done = False
    primitive_probe_done = False
    for provider, page_coordinates in providers:
        transform, used_fallback = _provider_transform(
            source,
            canvas_size,
            page_coordinates,
            mapped_source_transform,
        )
        used_fallback_transform = used_fallback_transform or used_fallback
        remaining_texts = max_texts - text_records
        remaining_text_chars = max_text_chars - text_chars
        if not text_enabled:
            pass
        elif remaining_texts > 0 and remaining_text_chars > 0:
            raw_text_batch = _extract_raw_texts(
                provider,
                max_items=remaining_texts,
                max_chars=remaining_text_chars,
            )
            text_records += raw_text_batch.records_seen
            text_chars += raw_text_batch.chars_seen
            if raw_text_batch.count_exhausted:
                text_records = max_texts
                text_count_truncated = True
                text_probe_done = True
            if raw_text_batch.char_exhausted:
                text_chars = max_text_chars
                text_chars_truncated = True
                text_probe_done = True
            for raw_text in raw_text_batch.items:
                try:
                    item = _parse_text(raw_text)
                    mapped = _map_text(item, transform)
                    if mapped is not None:
                        texts.append(mapped)
                except (TypeError, ValueError, AttributeError, UnicodeError):
                    malformed += 1
        elif not text_probe_done:
            probe = _extract_raw_texts(
                provider,
                max_items=0,
                max_chars=0,
            )
            text_probe_done = True
            if probe.count_exhausted or probe.char_exhausted:
                if remaining_texts <= 0:
                    text_count_truncated = True
                else:
                    text_chars_truncated = True
        remaining_primitives = max_primitives - primitive_records
        if not primitives_enabled:
            continue
        raw_drawings = _extract_raw_drawings(provider)
        if raw_drawings is None:
            continue
        drawing_iterator = iter(raw_drawings)
        if remaining_primitives <= 0:
            if not primitive_probe_done:
                try:
                    next(drawing_iterator)
                except StopIteration:
                    pass
                else:
                    primitive_probe_done = True
                    primitives_truncated = True
            continue
        while remaining_primitives > 0:
            try:
                raw_drawing = next(drawing_iterator)
            except StopIteration:
                break
            remaining_primitives = max_primitives - primitive_records
            parsed, failures, consumed, drawing_truncated = _parse_drawing(
                raw_drawing,
                max_items=remaining_primitives,
            )
            primitive_records += min(remaining_primitives, max(1, consumed))
            malformed += failures
            primitives_truncated = primitives_truncated or drawing_truncated
            for item in parsed:
                point_count = _vector_primitive_points(item)
                if primitive_points + point_count > max_points:
                    primitive_points_truncated = True
                    continue
                primitive_points += point_count
                mapped = _map_primitive(item, transform)
                if mapped is not None and _valid_vector_primitive(mapped):
                    primitives.append(mapped)
                elif mapped is not None:
                    malformed += 1
            if drawing_truncated:
                primitive_records = max_primitives
                primitive_probe_done = True
                break
            remaining_primitives = max_primitives - primitive_records
        if primitive_records >= max_primitives and not primitive_probe_done:
            try:
                next(drawing_iterator)
            except StopIteration:
                pass
            else:
                primitive_probe_done = True
                primitives_truncated = True

    warnings: list[str] = []
    if malformed:
        warnings.append(f"ignored {malformed} malformed vector record(s)")
    if used_fallback_transform:
        warnings.append("vector source mapping unavailable; used block/page bbox fallback")
    if primitives_truncated:
        warnings.append(
            "vector primitive input budget was reached; vector primitives were truncated "
            "to the configured budget"
        )
    if primitive_points_truncated:
        warnings.append(
            "vector aggregate point budget reached; remaining point geometry was omitted"
        )
    if text_count_truncated:
        warnings.append("vector text count budget reached; remaining spans were omitted")
    if text_chars_truncated:
        warnings.append("vector text character budget reached; remaining spans were omitted")
    deduplicated_texts, text_warnings = _deduplicate_texts(texts)
    warnings.extend(text_warnings)
    deduplicated_primitives, primitive_dedupe_limited = _deduplicate_primitives(primitives)
    if primitive_dedupe_limited:
        warnings.append(
            "vector approximate-duplicate comparison budget reached; remaining primitives "
            "kept without approximate deduplication"
        )
    return VectorObservation(
        canvas_size=canvas_size,
        texts=tuple(deduplicated_texts),
        primitives=tuple(deduplicated_primitives),
        warnings=tuple(warnings),
        primitive_records_seen=primitive_records,
        primitive_points_seen=(max_points if primitive_points_truncated else primitive_points),
        text_records_seen=text_records,
        text_chars_seen=text_chars,
        primitive_budget_exhausted=primitive_records >= max_primitives,
        primitive_point_budget_exhausted=(
            primitive_points_truncated or primitive_points >= max_points
        ),
        text_count_budget_exhausted=text_records >= max_texts,
        text_char_budget_exhausted=text_chars >= max_text_chars,
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
    canvas_size: tuple[int, int],
    page_coordinates: bool,
    mapped_source_transform: _Transform | None,
) -> tuple[_Transform, bool]:
    if not page_coordinates:
        return _Transform(None), False
    if mapped_source_transform is not None:
        return mapped_source_transform, False
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
    if type(source_mapping) is not dict:
        return None
    assembly = source_mapping.get("assembly")
    if type(assembly) is not dict:
        return None
    placements = assembly.get("placements")
    if type(placements) not in {list, tuple}:
        return None
    placement_inputs, placements_truncated, _placement_chars = _bounded_items(
        placements,
        MAX_VECTOR_SOURCES,
    )
    if placements_truncated:
        return None
    block_id = _source_block_id(source)
    page_id, invalid_page_id = _source_page_id(source)
    if invalid_page_id:
        return None
    candidates = [item for item in placement_inputs if type(item) is dict]
    if page_id is not None:
        candidates = [
            item
            for item in candidates
            if _valid_bounded_int(candidate_page_id := item.get("page_id"))
            and candidate_page_id == page_id
        ]
    by_block: list[dict[str, Any]] = []
    if block_id is not None:
        for item in candidates:
            source_ids = item.get("source_block_ids", ())
            if type(source_ids) not in {list, tuple}:
                continue
            bounded_ids, ids_truncated, _id_chars = _bounded_items(
                source_ids,
                MAX_EVIDENCE_REFS,
            )
            if ids_truncated:
                continue
            if any(type(value) is str and value == block_id for value in bounded_ids):
                by_block.append(item)
    if by_block:
        candidates = by_block
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
    if type(identifier) is str and identifier and _valid_bounded_optional_text(identifier):
        return identifier
    if _valid_bounded_int(identifier):
        identifier_text = str(identifier)
        return identifier_text if _valid_bounded_optional_text(identifier_text) else None
    identifier_type = type(identifier)
    if (
        identifier_type.__module__ == "marker.schema.blocks.base"
        and identifier_type.__name__ == "BlockId"
    ):
        page_id = _safe_attr(identifier, "page_id")
        block_id = _safe_attr(identifier, "block_id")
        block_type = _safe_attr(identifier, "block_type")
        if not _valid_bounded_int(page_id):
            return None
        if block_id is None or block_type is None:
            identifier_text = f"/page/{page_id}"
            return identifier_text if _valid_bounded_optional_text(identifier_text) else None
        block_type_name = _safe_attr(block_type, "name")
        if (
            not _valid_bounded_int(block_id)
            or type(block_type_name) is not str
            or not block_type_name
            or not _valid_bounded_optional_text(block_type_name)
        ):
            return None
        identifier_text = f"/page/{page_id}/{block_type_name}/{block_id}"
        return identifier_text if _valid_bounded_optional_text(identifier_text) else None
    return None


def _source_page_id(source: Any) -> tuple[int | None, bool]:
    value = _safe_attr(source, "page_id")
    if value is None:
        value = _safe_attr(source, "page_index")
    if value is None:
        return None, False
    if _valid_bounded_int(value):
        return value, False
    return None, True


def _as_affine(value: Any) -> tuple[float, float, float, float, float, float] | None:
    if type(value) not in {list, tuple} or len(value) != 6:
        return None
    numbers = tuple(_finite_number(value[index]) for index in range(6))
    if any(item is None for item in numbers):
        return None
    return numbers  # type: ignore[return-value]


def _source_bbox(source: Any) -> BBox | None:
    bbox = _as_bbox(_safe_attr(source, "bbox"))
    if bbox is not None:
        return bbox
    polygon = _safe_attr(source, "polygon")
    return _as_bbox(_safe_attr(polygon, "bbox"))


def _extract_raw_texts(
    provider: Any,
    *,
    max_items: int,
    max_chars: int,
) -> _RawTextBatch:
    for attribute in ("vector_texts", "text_spans"):
        value = _safe_attr(provider, attribute)
        if value is None or isinstance(value, str | bytes | Mapping):
            continue
        if callable(value):
            try:
                value = value()
            except (TypeError, ValueError, RuntimeError):
                continue
        if isinstance(value, str | bytes | Mapping) or not isinstance(value, Iterable):
            continue
        snapshots, truncated, char_count = _bounded_items(
            (_snapshot_vector_text_record(raw) for raw in value),
            max_items,
            char_counter=_vector_text_chars,
            max_chars=max_chars,
        )
        count_exhausted = truncated and len(snapshots) >= max_items
        char_exhausted = truncated and not count_exhausted
        records_seen = len(snapshots)
        if char_exhausted:
            records_seen = min(max_items, records_seen + 1)
        return _RawTextBatch(
            items=tuple(snapshots),
            records_seen=records_seen,
            chars_seen=char_count,
            count_exhausted=count_exhausted,
            char_exhausted=char_exhausted,
        )
    get_text = _safe_attr(provider, "get_text")
    if callable(get_text):
        try:
            payload = get_text("dict")
        except (TypeError, ValueError, RuntimeError):
            payload = None
        spans, spans_truncated, span_chars = _bounded_items(
            _dict_text_spans(payload),
            max_items,
            char_counter=_vector_text_chars,
            max_chars=max_chars,
        )
        span_count_exhausted = spans_truncated and len(spans) >= max_items
        span_char_exhausted = spans_truncated and not span_count_exhausted
        span_records_seen = len(spans)
        if span_char_exhausted:
            span_records_seen = min(max_items, span_records_seen + 1)
        if any(type(item) is dict for item in spans) or spans_truncated:
            return _RawTextBatch(
                items=tuple(spans),
                records_seen=span_records_seen,
                chars_seen=span_chars,
                count_exhausted=span_count_exhausted,
                char_exhausted=span_char_exhausted,
            )
        try:
            words = get_text("words")
        except (TypeError, ValueError, RuntimeError):
            words = None
        if isinstance(words, Iterable) and not isinstance(words, str | bytes | Mapping):
            remaining_items = max(0, max_items - span_records_seen)
            remaining_chars = max(0, max_chars - span_chars)
            raw_words, words_truncated, word_chars = _bounded_items(
                (_snapshot_vector_text_record(word) for word in words),
                remaining_items,
                char_counter=_vector_text_chars,
                max_chars=remaining_chars,
            )
            word_count_exhausted = words_truncated and len(raw_words) >= remaining_items
            word_char_exhausted = words_truncated and not word_count_exhausted
            word_records_seen = len(raw_words)
            if word_char_exhausted:
                word_records_seen = min(remaining_items, word_records_seen + 1)
            return _RawTextBatch(
                items=tuple([*spans, *raw_words]),
                records_seen=span_records_seen + word_records_seen,
                chars_seen=span_chars + word_chars,
                count_exhausted=word_count_exhausted,
                char_exhausted=word_char_exhausted,
            )
        if spans:
            return _RawTextBatch(
                items=tuple(spans),
                records_seen=span_records_seen,
                chars_seen=span_chars,
            )
    return _RawTextBatch()


def _dict_text_spans(payload: Any) -> Iterable[Any]:
    if type(payload) is not dict:
        return
    blocks = payload.get("blocks", ())
    if not isinstance(blocks, Iterable) or isinstance(blocks, str | bytes | Mapping):
        return
    for block in blocks:
        if type(block) is not dict:
            yield None
            continue
        lines = block.get("lines", ())
        if not isinstance(lines, Iterable) or isinstance(lines, str | bytes | Mapping):
            yield None
            continue
        found_line = False
        for line in lines:
            found_line = True
            if type(line) is not dict:
                yield None
                continue
            spans = line.get("spans", ())
            if not isinstance(spans, Iterable) or isinstance(spans, str | bytes | Mapping):
                yield None
                continue
            found_span = False
            for item in spans:
                found_span = True
                yield _snapshot_vector_text_record(item)
            if not found_span:
                yield None
        if not found_line:
            yield None


def _snapshot_vector_text_record(raw: Any) -> dict[str, Any]:
    if type(raw) is dict:
        return {
            "text": raw.get("text"),
            "bbox": raw.get("bbox"),
            "color": raw.get("color"),
            "font_weight": raw.get("font_weight"),
            "flags": raw.get("flags"),
            "confidence": raw.get("confidence"),
        }
    if type(raw) is VectorText:
        return {
            "text": raw.text,
            "bbox": raw.bbox,
            "color": raw.color,
            "font_weight": raw.font_weight,
            "flags": None,
            "confidence": raw.confidence,
        }
    if type(raw) in {list, tuple} and len(raw) >= 5:
        return {
            "text": raw[4],
            "bbox": tuple(raw[:4]),
            "color": None,
            "font_weight": None,
            "flags": None,
            "confidence": None,
        }
    return {
        "text": _safe_attr(raw, "text"),
        "bbox": _safe_attr(raw, "bbox"),
        "color": _safe_attr(raw, "color"),
        "font_weight": _safe_attr(raw, "font_weight"),
        "flags": _safe_attr(raw, "flags"),
        "confidence": _safe_attr(raw, "confidence"),
    }


def _extract_raw_drawings(provider: Any) -> Iterable[Any] | None:
    for attribute in ("vector_primitives", "drawings", "paths"):
        value = _safe_attr(provider, attribute)
        if callable(value):
            try:
                value = value()
            except (TypeError, ValueError, RuntimeError):
                value = None
        if isinstance(value, Iterable) and not isinstance(value, str | bytes | Mapping):
            return value
    get_drawings = _safe_attr(provider, "get_drawings")
    if callable(get_drawings):
        try:
            value = get_drawings()
        except (TypeError, ValueError, RuntimeError):
            return None
        if isinstance(value, Iterable) and not isinstance(value, str | bytes | Mapping):
            return value
    return None


def _vector_text_chars(item: object) -> int:
    if type(item) is VectorText and type(item.text) is str:
        return len(item.text)
    if type(item) is dict:
        text = item.get("text")
        return len(text) if type(text) is str else 0
    if type(item) in {list, tuple} and len(item) >= 5:
        text = item[4]
        return len(text) if type(text) is str else 0
    text = _safe_attr(item, "text")
    return len(text) if type(text) is str else 0


def _vector_primitive_points(item: object) -> int:
    if type(item) is VectorPrimitive and type(item.points) is tuple:
        return len(item.points)
    return 0


def _valid_bounded_optional_text(value: object) -> bool:
    if value is None:
        return True
    if type(value) is not str or len(value) > MAX_VECTOR_TOKEN_CHARS:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_bounded_int(value: object) -> bool:
    return type(value) is int and value.bit_length() <= MAX_VECTOR_TOKEN_CHARS * 4


def _valid_vector_bbox(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 4
        and all(_finite_number(item) is not None for item in value)
        and value[2] >= value[0]
        and value[3] >= value[1]
    )


def _valid_vector_text(item: VectorText) -> bool:
    if (
        type(item.text) is not str
        or not item.text
        or len(item.text) > MAX_TEXT_CHARS
        or not item.text.strip()
    ):
        return False
    try:
        item.text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return (
        _valid_vector_bbox(item.bbox)
        and _valid_bounded_optional_text(item.color)
        and _finite_number(item.confidence) is not None
        and (
            item.font_weight is None
            or (type(item.font_weight) is str and item.font_weight in {"normal", "bold"})
        )
    )


def _valid_vector_primitive(item: VectorPrimitive) -> bool:
    if type(item.kind) is not str or not item.kind or not _valid_bounded_optional_text(item.kind):
        return False
    point_limit = (
        MAX_VECTOR_POLYGON_POINTS
        if item.closed and item.kind.casefold() in {"rectangle", "rect", "ellipse", "polygon"}
        else MAX_VECTOR_POLYLINE_POINTS
    )
    return (
        _valid_vector_bbox(item.bbox)
        and type(item.points) is tuple
        and len(item.points) <= point_limit
        and all(
            type(point) is tuple
            and len(point) == 2
            and all(_finite_number(value) is not None for value in point)
            for point in item.points
        )
        and _valid_bounded_optional_text(item.fill_color)
        and _valid_bounded_optional_text(item.stroke_color)
        and _valid_bounded_optional_text(item.line_style)
        and type(item.closed) is bool
        and type(item.arrow_at_start) is bool
        and type(item.arrow_at_end) is bool
        and _finite_number(item.confidence) is not None
    )


def _parse_text(raw: Any) -> VectorText:
    text = _get(raw, "text")
    bbox = _as_bbox(_get(raw, "bbox"))
    if type(text) is not str or not text or len(text) > MAX_TEXT_CHARS or bbox is None:
        raise ValueError("text and bbox are required")
    text = text.strip()
    if not text:
        raise ValueError("text and bbox are required")
    result = VectorText(
        text=text,
        bbox=bbox,
        color=_as_color(_get(raw, "color")),
        font_weight=_font_weight(raw),
        confidence=_as_confidence(_get(raw, "confidence"), 0.99),
    )
    if not _valid_vector_text(result):
        raise ValueError("vector text exceeds its field limits")
    return result


def _font_weight(raw: Any) -> Literal["normal", "bold"] | None:
    explicit = _get(raw, "font_weight")
    if type(explicit) is str and explicit in {"normal", "bold"}:
        return explicit
    flags = _get(raw, "flags")
    if type(flags) is int:
        return "bold" if flags & 16 else "normal"
    return None


def _parse_drawing(
    raw: Any,
    *,
    max_items: int,
) -> tuple[list[VectorPrimitive], int, int, bool]:
    if (
        type(raw) is dict
        and isinstance(raw.get("items"), Iterable)
        and not isinstance(raw.get("items"), str | bytes | Mapping)
    ):
        style = {
            "fill_color": _as_color(raw.get("fill")),
            "stroke_color": _as_color(raw.get("color")),
            "line_style": _line_style(raw),
            "confidence": _as_confidence(raw.get("confidence"), 0.98),
        }
        parsed: list[VectorPrimitive] = []
        failures = 0
        items, truncated, _chars = _bounded_items(raw["items"], max_items)
        for item in items:
            try:
                parsed.append(_parse_pymupdf_item(item, style))
            except (TypeError, ValueError, AttributeError, IndexError):
                failures += 1
        return parsed, failures, len(items), truncated
    if max_items <= 0:
        return [], 0, 0, True
    try:
        kind_value = _get(raw, "kind")
        if kind_value is None:
            kind_value = _get(raw, "type")
        if (
            type(kind_value) is not str
            or not kind_value
            or not _valid_bounded_optional_text(kind_value)
        ):
            raise ValueError("drawing kind is invalid")
        kind = kind_value.casefold()
        bbox_value = _get(raw, "bbox")
        if bbox_value is None:
            bbox_value = _get(raw, "rect")
        bbox = _as_bbox(bbox_value)
        point_limit = (
            MAX_VECTOR_POLYGON_POINTS
            if kind in {"rectangle", "rect", "ellipse", "polygon"}
            else MAX_VECTOR_POLYLINE_POINTS
        )
        point_values = _get(raw, "points")
        if point_values is None:
            point_values = _get(raw, "polyline")
        points = _as_points(
            point_values if point_values is not None else (), max_points=point_limit
        )
        if bbox is None and points:
            bbox = _points_bbox(points)
        if not kind or bbox is None:
            raise ValueError("drawing kind and bbox/points are required")
        fill_value = _get(raw, "fill_color")
        if fill_value is None:
            fill_value = _get(raw, "fill")
        stroke_value = _get(raw, "stroke_color")
        if stroke_value is None:
            stroke_value = _get(raw, "color")
        closed_value = _get(raw, "closed")
        arrow_start_value = _get(raw, "arrow_at_start")
        arrow_end_value = _get(raw, "arrow_at_end")
        return (
            [
                VectorPrimitive(
                    kind=kind,
                    bbox=bbox,
                    points=points,
                    fill_color=_as_color(fill_value),
                    stroke_color=_as_color(stroke_value),
                    line_style=_line_style(raw),
                    closed=(closed_value if type(closed_value) is bool else False)
                    or kind in {"rectangle", "rect", "ellipse", "polygon"},
                    arrow_at_start=(
                        arrow_start_value if type(arrow_start_value) is bool else False
                    ),
                    arrow_at_end=(arrow_end_value if type(arrow_end_value) is bool else False),
                    confidence=_as_confidence(_get(raw, "confidence"), 0.95),
                )
            ],
            0,
            1,
            False,
        )
    except (TypeError, ValueError, AttributeError):
        return [], 1, 1, False


def _parse_pymupdf_item(item: Any, style: dict[str, Any]) -> VectorPrimitive:
    if type(item) not in {list, tuple} or not item:
        raise ValueError("drawing item must be a command sequence")
    command_value = item[0]
    if (
        type(command_value) is not str
        or not command_value
        or not _valid_bounded_optional_text(command_value)
    ):
        raise ValueError("drawing command is invalid")
    command = command_value.casefold()
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
        points = tuple(_as_point(item[index]) for index in range(1, 5))
        return VectorPrimitive(kind="path", bbox=_points_bbox(points), points=points, **style)
    raise ValueError(f"unsupported drawing command: {command}")


def _map_text(item: VectorText, transform: _Transform) -> VectorText | None:
    if not transform.intersects(item.bbox):
        return None
    return VectorText(
        text=item.text,
        bbox=transform.bbox(item.bbox),
        color=item.color,
        font_weight=item.font_weight,
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


def _finite_number(value: object) -> float | None:
    if type(value) not in {int, float}:
        return None
    try:
        if not math.isfinite(value):
            return None
        return float(value)
    except (OverflowError, ValueError):
        return None


def _get(value: Any, name: str) -> Any:
    if type(value) is dict:
        return value.get(name)
    return _safe_attr(value, name)


def _as_bbox(value: Any) -> BBox | None:
    if value is None:
        return None
    if all(hasattr(value, name) for name in ("x0", "y0", "x1", "y1")):
        result = tuple(_finite_number(_safe_attr(value, name)) for name in ("x0", "y0", "x1", "y1"))
    elif type(value) in {list, tuple} and len(value) >= 4:
        result = tuple(_finite_number(value[index]) for index in range(4))
    else:
        return None
    if any(item is None for item in result) or result[2] < result[0] or result[3] < result[1]:
        return None
    return result  # type: ignore[return-value]


def _as_point(value: Any) -> Point:
    if hasattr(value, "x") and hasattr(value, "y"):
        point = _finite_number(_safe_attr(value, "x")), _finite_number(_safe_attr(value, "y"))
    elif type(value) in {list, tuple} and len(value) >= 2:
        point = _finite_number(value[0]), _finite_number(value[1])
    else:
        raise ValueError("point must have x/y coordinates")
    if any(item is None for item in point):
        raise ValueError("point must be finite")
    return point  # type: ignore[return-value]


def _as_points(
    value: Any,
    *,
    max_points: int = MAX_VECTOR_POLYLINE_POINTS,
) -> tuple[Point, ...]:
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | Mapping):
        return ()
    items, truncated, _chars = _bounded_items(value, max_points)
    if truncated:
        raise ValueError("vector point collection exceeds its per-primitive limit")
    return tuple(_as_point(item) for item in items)


def _quad_points(value: Any) -> tuple[Point, ...]:
    for attributes in (("ul", "ur", "lr", "ll"), ("p1", "p2", "p3", "p4")):
        if all(hasattr(value, name) for name in attributes):
            return tuple(_as_point(getattr(value, name)) for name in attributes)
    points = _as_points(value, max_points=4)
    if len(points) < 4:
        raise ValueError("quad must contain four points")
    return points[:4]


def _as_color(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if type(value) is not str or not _valid_bounded_optional_text(value):
            return value
        stripped = value.strip().lower()
        return stripped or None
    if type(value) is int and 0 <= value <= 0xFFFFFF:
        return f"#{value:06x}"
    if type(value) in {list, tuple} and len(value) >= 3:
        channels = [_finite_number(value[index]) for index in range(3)]
        if any(item is None for item in channels):
            return None
        if all(0 <= item <= 1 for item in channels):
            channels = [round(item * 255) for item in channels]
        if all(0 <= item <= 255 for item in channels):
            return "#" + "".join(f"{round(item):02x}" for item in channels)
    return None


def _line_style(value: Any) -> str | None:
    dashes = _get(value, "dashes")
    if dashes is None:
        dashes = _get(value, "dash")
    if (
        type(dashes) is str
        and _valid_bounded_optional_text(dashes)
        and dashes.strip() not in {"", "[] 0", "[]", "0"}
    ) or (type(dashes) in {list, tuple} and 0 < len(dashes) <= 256):
        return "dashed"
    width = _get(value, "width")
    if width is None:
        width = _get(value, "stroke_width")
    numeric_width = _finite_number(width)
    if numeric_width is not None and numeric_width >= 2.0:
        return "thick"
    return "solid" if width is not None or _get(value, "color") is not None else None


def _as_confidence(value: Any, default: float) -> float:
    number = _finite_number(value)
    return _probability(number) if number is not None else default


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


def _deduplicate_texts(texts: Iterable[VectorText]) -> tuple[list[VectorText], list[str]]:
    selected: dict[tuple[str, BBox], VectorText] = {}
    conflicting_weights: set[tuple[str, BBox]] = set()
    for item in texts:
        if item.bbox[2] <= item.bbox[0] or item.bbox[3] <= item.bbox[1]:
            continue
        key = item.text, item.bbox
        existing = selected.get(key)
        if existing is None:
            selected[key] = item
            continue
        preferred = item if item.confidence > existing.confidence else existing
        if existing.font_weight != item.font_weight or key in conflicting_weights:
            conflicting_weights.add(key)
            preferred = replace(preferred, font_weight=None)
        selected[key] = preferred
    warnings = (
        [
            "vector text weight conflicted for "
            f"{len(conflicting_weights)} duplicate span(s); emphasis omitted"
        ]
        if conflicting_weights
        else []
    )
    ordered = sorted(selected.values(), key=lambda item: (item.bbox, item.text))
    return ordered, warnings


def _deduplicate_primitives(
    primitives: Iterable[VectorPrimitive],
) -> tuple[list[VectorPrimitive], bool]:
    ordered = sorted(
        primitives,
        key=lambda item: (item.bbox, item.kind, -_probability(item.confidence)),
    )
    selected: list[VectorPrimitive] = []
    exact_keys: set[tuple[str, tuple[Point, ...], BBox]] = set()
    by_shape: dict[tuple[str, tuple[Point, ...]], list[VectorPrimitive]] = {}
    comparisons = 0
    comparison_limit_reached = False
    for item in ordered:
        if item.bbox[2] < item.bbox[0] or item.bbox[3] < item.bbox[1]:
            continue
        exact_key = item.kind, item.points, item.bbox
        if exact_key in exact_keys:
            continue
        exact_keys.add(exact_key)
        shape_key = item.kind, item.points
        shape_matches = by_shape.setdefault(shape_key, [])
        duplicate = False
        for existing in shape_matches:
            if comparisons >= MAX_VECTOR_DEDUP_COMPARISONS:
                comparison_limit_reached = True
                break
            comparisons += 1
            if _bbox_iou(item.bbox, existing.bbox) >= 0.98:
                duplicate = True
                break
        if duplicate:
            continue
        selected.append(item)
        shape_matches.append(item)
    return selected, comparison_limit_reached


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
    *,
    max_comparisons: int,
) -> tuple[str | None, int, bool]:
    best_distance = math.inf
    best_id: str | None = None
    best_is_tied = False
    comparisons = 0
    for primitive, element_id in records:
        if comparisons >= max_comparisons:
            return None, comparisons, True
        comparisons += 1
        distance = _point_bbox_distance(point, primitive.bbox)
        if distance < best_distance and not math.isclose(
            distance,
            best_distance,
            abs_tol=1e-6,
        ):
            best_distance = distance
            best_id = element_id
            best_is_tied = False
        elif math.isclose(distance, best_distance, abs_tol=1e-6):
            best_is_tied = True
    if best_id is None or best_distance > tolerance or best_is_tied:
        return None, comparisons, False
    return best_id, comparisons, False


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
