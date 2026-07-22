"""Typed intermediate representations and candidate/result models."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from marker_mermaid.config import PublishPolicy, QualityGrade, SecurityProfile
from marker_mermaid.mapping_validation import bbox_iou
from marker_mermaid.render_artifacts import MAX_RENDER_BYTES, png_inspection_error
from marker_mermaid.resource_limits import (
    MAX_EVIDENCE_INPUT_CHARS,
    MAX_EVIDENCE_REFS,
    MAX_EVIDENCE_SOURCE_BLOCK_CHARS,
    MAX_EVIDENCE_SOURCE_BLOCK_REFS,
)
from marker_mermaid.typed_contracts import validate_typed_ir_contract

BBox = tuple[float, float, float, float]
Point = tuple[float, float]
MAX_OBSERVATION_CANDIDATES = 64
MAX_OBSERVATION_EVIDENCE = 20_000
MAX_OBSERVATION_WARNINGS = 256
MAX_IR_DEPTH = 64
MAX_IR_ITEMS = 100_000
MAX_IR_TEXT_CHARS = 50_000
MAX_IR_UTF8_TEXT_BYTES = 1_000_000
MAX_IR_JSON_BYTES = 4_000_000
MAX_IR_ABS_NUMBER = 9_007_199_254_740_991
MAX_OBSERVATION_TYPED_IR_JSON_BYTES = 8_000_000
MAX_TYPED_IR_CANDIDATE_FIELDS = 3
MAX_ID_CHARS = 256
MAX_TEXT_CHARS = 50_000
MAX_WARNING_CHARS = 4_096
NODE_ID_MAPPING_MIN_IOU = 0.45
_NODE_ID_MAPPING_SEAL_KEY = secrets.token_bytes(32)
_VALIDATION_RECEIPT_SEAL_KEY = secrets.token_bytes(32)
_VALIDATED_ARTIFACT_CERTIFICATE_SEAL_KEY = secrets.token_bytes(32)
_PUBLICATION_AUTHORIZATION_SEAL_KEY = secrets.token_bytes(32)
_PUBLICATION_SNAPSHOT_SEAL_KEY = secrets.token_bytes(32)
MAX_SCENE_ELEMENTS = 5_000
MAX_SCENE_RELATIONS = 10_000
MAX_SCENE_GROUPS = 1_000
MAX_POLYGON_POINTS = 4_096
MAX_POLYLINE_POINTS = 10_000
MAX_SOURCE_MAPPING_DEPTH = 32
MAX_SOURCE_MAPPING_ITEMS = 25_000
MAX_SOURCE_MAPPING_STRING_CHARS = 50_000
MAX_SOURCE_MAPPING_JSON_BYTES = 4_000_000
MAX_SOURCE_MAPPING_ABS_NUMBER = 9_007_199_254_740_991
_VISUAL_EVIDENCE_KINDS = frozenset(
    {
        "source_crop",
        "ocr_token",
        "vector_text",
        "contour",
        "line_segment",
        "arrowhead",
        "vlm_observation",
        "user_edit",
    }
)
_MAX_VISUAL_EVIDENCE_KIND_CHARS = max(len(value) for value in _VISUAL_EVIDENCE_KINDS)
_VISUAL_EVIDENCE_FONT_WEIGHTS = frozenset({"normal", "bold"})
_MAX_VISUAL_EVIDENCE_FONT_WEIGHT_CHARS = max(len(value) for value in _VISUAL_EVIDENCE_FONT_WEIGHTS)
_VISUAL_EVIDENCE_FIELDS = frozenset(
    {
        "id",
        "kind",
        "bbox",
        "text",
        "font_weight",
        "score",
        "source_block_ids",
    }
)


def _sink_safe_diagnostic_text(value: str) -> str:
    """Return plain UTF-8 diagnostic text without losing malformed code points silently."""

    if type(value) is not str:
        raise ValueError("diagnostic text must be a plain string")
    return value.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _require_utf8_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field} must contain valid Unicode scalar values") from exc
    return value


def _canonical_runtime_diagram_type(value: str | None) -> str | None:
    if value is None or type(value) is not str:
        return None
    normalized = value.casefold()
    aliases = {
        "flowchart-v2": "flowchart",
        "flowchart": "flowchart",
        "statediagram": "state",
        "statediagram-v2": "state",
        "class": "class",
        "classdiagram": "class",
        "classdiagram-v2": "class",
        "er": "er",
        "architecture": "architecture",
        "requirement": "requirement",
        "block": "block",
        "sequence": "sequence",
        "mindmap": "mindmap",
        "timeline": "timeline",
        "gantt": "gantt",
        "c4": "c4",
        "pie": "pie",
        "xychart": "xychart",
        "quadrantchart": "quadrant",
        "sankey": "sankey",
        "radar": "radar",
        "treemap": "treemap",
        "venn": "venn",
    }
    return aliases.get(normalized, normalized)


def _bounded_text(value: str | None, field: str, limit: int = MAX_TEXT_CHARS) -> str | None:
    if value is not None and len(value) > limit:
        raise ValueError(f"{field} exceeds the text size limit")
    _require_utf8_text(value, field)
    return value


def _bounded_references(
    values: list[str], field: str, *, limit: int = MAX_EVIDENCE_REFS
) -> list[str]:
    if len(values) > limit:
        raise ValueError(f"{field} exceeds the reference count limit")
    if any(not value or len(value) > MAX_ID_CHARS for value in values):
        raise ValueError(f"{field} contains an invalid bounded identifier")
    for value in values:
        _require_utf8_text(value, field)
    return values


def _finite_bbox(value: BBox | None, field: str) -> BBox | None:
    if value is not None and not all(math.isfinite(item) for item in value):
        raise ValueError(f"{field} coordinates must be finite")
    return value


def _finite_points(values: list[Point] | None, field: str, limit: int) -> list[Point] | None:
    if values is None:
        return None
    if len(values) > limit:
        raise ValueError(f"{field} exceeds the point count limit")
    if any(not all(math.isfinite(item) for item in point) for point in values):
        raise ValueError(f"{field} coordinates must be finite")
    return values


def _bounded_evidence_authority_ids(
    evidence_ids: set[str] | frozenset[str],
) -> frozenset[str]:
    if type(evidence_ids) not in {set, frozenset} or len(evidence_ids) > MAX_OBSERVATION_EVIDENCE:
        raise ValueError("evidence authority IDs must be a bounded set")
    for evidence_id in evidence_ids:
        if type(evidence_id) is not str:
            raise ValueError("evidence authority IDs must be plain strings")
        _require_utf8_text(evidence_id, "evidence authority ID")
        if not evidence_id or len(evidence_id) > MAX_ID_CHARS:
            raise ValueError("evidence authority ID must be non-empty and bounded")
    return frozenset(evidence_ids)


class VisualEvidence(BaseModel):
    id: str
    kind: Literal[
        "source_crop",
        "ocr_token",
        "vector_text",
        "contour",
        "line_segment",
        "arrowhead",
        "vlm_observation",
        "user_edit",
    ]
    bbox: BBox | None = None
    text: str | None = None
    font_weight: Literal["normal", "bold"] | None = None
    score: float | None = None
    source_block_ids: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def score_is_probability(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("evidence score must be between 0 and 1")
        return value

    @field_validator("id")
    @classmethod
    def id_is_bounded(cls, value: str) -> str:
        if not value or len(value) > MAX_ID_CHARS:
            raise ValueError("evidence id must be non-empty and bounded")
        _require_utf8_text(value, "evidence id")
        return value

    @field_validator("text")
    @classmethod
    def text_is_bounded(cls, value: str | None) -> str | None:
        return _bounded_text(value, "evidence text")

    @field_validator("source_block_ids")
    @classmethod
    def source_ids_are_bounded(cls, value: list[str]) -> list[str]:
        return _bounded_references(value, "source_block_ids")

    @field_validator("bbox")
    @classmethod
    def bbox_is_ordered(cls, value: BBox | None) -> BBox | None:
        _finite_bbox(value, "evidence bbox")
        if value is not None and (value[2] < value[0] or value[3] < value[1]):
            raise ValueError("bbox coordinates must be ordered as x1, y1, x2, y2")
        return value


@dataclass(frozen=True, slots=True)
class EvidenceBudgetUsage:
    """Cumulative logical work represented by one canonical evidence collection."""

    items: int = 0
    source_block_references: int = 0
    source_block_characters: int = 0
    characters: int = 0

    def __post_init__(self) -> None:
        values = (
            self.items,
            self.source_block_references,
            self.source_block_characters,
            self.characters,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("evidence budget usage must contain non-negative exact integers")
        if self.source_block_characters < self.source_block_references:
            raise ValueError("evidence source-block characters cannot be fewer than references")
        if self.characters < self.source_block_characters:
            raise ValueError("evidence characters cannot be fewer than source-block characters")


@dataclass(frozen=True, slots=True)
class EvidenceCollectionSnapshot:
    """Detached evidence records and the cumulative budget used to retain them."""

    evidence: tuple[VisualEvidence, ...]
    usage: EvidenceBudgetUsage


def canonical_evidence_collection_snapshot(
    evidence: object,
    *,
    base: EvidenceBudgetUsage | None = None,
    item_limit: int | None = None,
    source_block_reference_limit: int | None = None,
    source_block_character_limit: int | None = None,
    character_limit: int | None = None,
) -> EvidenceCollectionSnapshot:
    """Capture exact evidence records without materializing unbounded provenance fan-out."""

    resolved_item_limit = MAX_OBSERVATION_EVIDENCE if item_limit is None else item_limit
    resolved_reference_limit = (
        MAX_EVIDENCE_SOURCE_BLOCK_REFS
        if source_block_reference_limit is None
        else source_block_reference_limit
    )
    resolved_source_character_limit = (
        MAX_EVIDENCE_SOURCE_BLOCK_CHARS
        if source_block_character_limit is None
        else source_block_character_limit
    )
    resolved_character_limit = (
        MAX_EVIDENCE_INPUT_CHARS if character_limit is None else character_limit
    )
    limits = (
        resolved_item_limit,
        resolved_reference_limit,
        resolved_source_character_limit,
        resolved_character_limit,
    )
    if any(type(limit) is not int or limit < 0 for limit in limits):
        raise ValueError("evidence limits must be non-negative exact integers")
    if base is None:
        base = EvidenceBudgetUsage()
    elif type(base) is not EvidenceBudgetUsage:
        raise TypeError("evidence base usage must be an exact EvidenceBudgetUsage")
    if type(evidence) is not list:
        raise TypeError("evidence must be an exact plain list")

    item_count = list.__len__(evidence)
    total_items = base.items + item_count
    if total_items > resolved_item_limit:
        raise ValueError("evidence exceeds the observation item limit")
    if base.source_block_references > resolved_reference_limit:
        raise ValueError("evidence source-block references exceed the aggregate limit")
    if base.source_block_characters > resolved_source_character_limit:
        raise ValueError("evidence source-block characters exceed the aggregate limit")
    if base.characters > resolved_character_limit:
        raise ValueError("evidence characters exceed the aggregate limit")

    items = list.__getitem__(evidence, slice(0, item_count))
    if list.__len__(evidence) != item_count or list.__len__(items) != item_count:
        raise ValueError("evidence changed while it was captured")

    payloads: list[dict[str, object]] = []
    source_block_references = base.source_block_references
    source_block_characters = base.source_block_characters
    characters = base.characters
    missing = object()
    for item in items:
        if type(item) is not VisualEvidence:
            raise TypeError("evidence must contain exact canonical VisualEvidence records")
        fields = object.__getattribute__(item, "__dict__")
        if type(fields) is not dict:
            raise TypeError("evidence record fields must be canonical")
        field_count = dict.__len__(fields)
        if field_count != len(_VISUAL_EVIDENCE_FIELDS):
            raise ValueError("evidence record must contain exactly its public fields")
        field_snapshot = dict.copy(fields)
        if dict.__len__(field_snapshot) != field_count or dict.__len__(fields) != field_count:
            raise ValueError("evidence record changed while it was captured")
        for field_name in list(dict.keys(field_snapshot)):
            if type(field_name) is not str or field_name not in _VISUAL_EVIDENCE_FIELDS:
                raise ValueError("evidence record contains an unknown public field")

        item_id = dict.get(field_snapshot, "id", missing)
        kind = dict.get(field_snapshot, "kind", missing)
        bbox = dict.get(field_snapshot, "bbox", missing)
        text = dict.get(field_snapshot, "text", missing)
        font_weight = dict.get(field_snapshot, "font_weight", missing)
        score = dict.get(field_snapshot, "score", missing)
        live_block_ids = dict.get(field_snapshot, "source_block_ids", missing)
        if type(item_id) is not str or not item_id or len(item_id) > MAX_ID_CHARS:
            raise TypeError("evidence contains an invalid bounded id")
        _require_utf8_text(item_id, "evidence id")
        if (
            type(kind) is not str
            or len(kind) > _MAX_VISUAL_EVIDENCE_KIND_CHARS
            or kind not in _VISUAL_EVIDENCE_KINDS
        ):
            raise TypeError("evidence contains a non-canonical kind")
        _require_utf8_text(kind, "evidence kind")
        if text is not None:
            if type(text) is not str or len(text) > MAX_TEXT_CHARS:
                raise TypeError("evidence contains invalid bounded text")
            _require_utf8_text(text, "evidence text")
        if font_weight is not None:
            if (
                type(font_weight) is not str
                or len(font_weight) > _MAX_VISUAL_EVIDENCE_FONT_WEIGHT_CHARS
                or font_weight not in _VISUAL_EVIDENCE_FONT_WEIGHTS
            ):
                raise TypeError("evidence contains a non-canonical font weight")
            _require_utf8_text(font_weight, "evidence font weight")
        if bbox is not None and (
            type(bbox) is not tuple
            or len(bbox) != 4
            or any(type(value) not in {int, float} for value in bbox)
            or not all(math.isfinite(value) for value in bbox)
        ):
            raise TypeError("evidence contains a non-canonical bbox")
        if score is not None and (
            type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1
        ):
            raise TypeError("evidence contains a non-canonical score")
        if type(live_block_ids) is not list:
            raise TypeError("evidence source_block_ids must be an exact plain list")
        block_count = list.__len__(live_block_ids)
        if block_count > MAX_EVIDENCE_REFS:
            raise ValueError("evidence source_block_ids exceeds its reference count limit")
        source_block_references += block_count
        if source_block_references > resolved_reference_limit:
            raise ValueError("evidence source-block references exceed the aggregate limit")
        block_ids = list.__getitem__(live_block_ids, slice(0, block_count))
        if list.__len__(live_block_ids) != block_count or list.__len__(block_ids) != block_count:
            raise ValueError("evidence source_block_ids changed while they were captured")
        block_characters = 0
        for block_id in block_ids:
            if type(block_id) is not str or not block_id or len(block_id) > MAX_ID_CHARS:
                raise TypeError("evidence source_block_ids contains an invalid identifier")
            _require_utf8_text(block_id, "evidence source block id")
            block_characters += len(block_id)
            if source_block_characters + block_characters > resolved_source_character_limit:
                raise ValueError("evidence source-block characters exceed the aggregate limit")
        source_block_characters += block_characters
        characters += len(item_id) + len(kind) + block_characters
        if text is not None:
            characters += len(text)
        if font_weight is not None:
            characters += len(font_weight)
        if characters > resolved_character_limit:
            raise ValueError("evidence characters exceed the aggregate limit")

        if list.__len__(live_block_ids) != block_count or any(
            list.__getitem__(live_block_ids, index) is not block_ids[index]
            for index in range(block_count)
        ):
            raise ValueError("evidence source_block_ids changed while they were captured")
        if dict.__len__(fields) != field_count:
            raise ValueError("evidence record changed while it was captured")
        after_fields = dict.copy(fields)
        if dict.__len__(after_fields) != field_count or dict.__len__(fields) != field_count:
            raise ValueError("evidence record changed while it was captured")
        for field_name in list(dict.keys(after_fields)):
            if type(field_name) is not str or field_name not in _VISUAL_EVIDENCE_FIELDS:
                raise ValueError("evidence record contains an unknown public field")
        for field_name, original in field_snapshot.items():
            if dict.get(after_fields, field_name, missing) is not original:
                raise ValueError("evidence record changed while it was captured")

        payloads.append(
            {
                "id": item_id,
                "kind": kind,
                "bbox": bbox,
                "text": text,
                "font_weight": font_weight,
                "score": score,
                "source_block_ids": block_ids,
            }
        )

    if list.__len__(evidence) != item_count or any(
        list.__getitem__(evidence, index) is not items[index] for index in range(item_count)
    ):
        raise ValueError("evidence changed while it was captured")
    canonical_evidence = tuple(VisualEvidence.model_validate(payload) for payload in payloads)
    return EvidenceCollectionSnapshot(
        evidence=canonical_evidence,
        usage=EvidenceBudgetUsage(
            items=total_items,
            source_block_references=source_block_references,
            source_block_characters=source_block_characters,
            characters=characters,
        ),
    )


def canonical_evidence_input_snapshot(
    evidence: object,
    *,
    item_limit: int | None = None,
    character_limit: int | None = None,
    ignore_unknown_fields: bool = False,
) -> EvidenceCollectionSnapshot:
    """Normalize bounded JSON evidence through the aggregate snapshot contract.

    Caller-specific item/full-character limits leave the shared source-block limits intact.
    Unknown fields remain strict unless a versioned permissive ingress opts into ignoring them.
    """

    if type(evidence) is not list:
        raise TypeError("evidence input must be an exact plain list")
    resolved_item_limit = MAX_OBSERVATION_EVIDENCE if item_limit is None else item_limit
    resolved_character_limit = (
        MAX_EVIDENCE_INPUT_CHARS if character_limit is None else character_limit
    )
    if type(resolved_item_limit) is not int or resolved_item_limit < 0:
        raise ValueError("evidence input item limit must be a non-negative exact integer")
    if type(resolved_character_limit) is not int or resolved_character_limit < 0:
        raise ValueError("evidence input character limit must be a non-negative exact integer")
    if type(ignore_unknown_fields) is not bool:
        raise ValueError("ignore_unknown_fields must be an exact boolean")
    item_count = list.__len__(evidence)
    if item_count > resolved_item_limit:
        raise ValueError("evidence exceeds the observation item limit")
    items = list.__getitem__(evidence, slice(0, item_count))
    if list.__len__(evidence) != item_count or list.__len__(items) != item_count:
        raise ValueError("evidence input changed while it was captured")

    canonical: list[VisualEvidence] = []
    usage: EvidenceBudgetUsage | None = None
    for item in items:
        if type(item) is VisualEvidence:
            record = item
        elif type(item) is dict:
            field_count = dict.__len__(item)
            if not ignore_unknown_fields and field_count > len(_VISUAL_EVIDENCE_FIELDS):
                raise ValueError("evidence input record exceeds the public field-count limit")
            if ignore_unknown_fields:
                payload: dict[str, object] = {}
                observed_fields = 0
                field_iterator = dict.__iter__(item)
                try:
                    for _ in range(field_count + 1):
                        try:
                            field_name = next(field_iterator)
                        except StopIteration:
                            break
                        observed_fields += 1
                        if type(field_name) is str and field_name in _VISUAL_EVIDENCE_FIELDS:
                            payload[field_name] = dict.__getitem__(item, field_name)
                except (KeyError, RuntimeError) as exc:
                    raise ValueError("evidence input record changed while it was captured") from exc
                if observed_fields != field_count or dict.__len__(item) != field_count:
                    raise ValueError("evidence input record changed while it was captured")
                after_known_fields: list[str] = []
                after_observed_fields = 0
                after_field_iterator = dict.__iter__(item)
                try:
                    for _ in range(field_count + 1):
                        try:
                            field_name = next(after_field_iterator)
                        except StopIteration:
                            break
                        after_observed_fields += 1
                        if type(field_name) is str and field_name in _VISUAL_EVIDENCE_FIELDS:
                            after_known_fields.append(field_name)
                            if (
                                field_name not in payload
                                or dict.__getitem__(item, field_name) is not payload[field_name]
                            ):
                                raise ValueError(
                                    "evidence input record changed while it was captured"
                                )
                except (KeyError, RuntimeError) as exc:
                    raise ValueError("evidence input record changed while it was captured") from exc
                if (
                    after_observed_fields != field_count
                    or dict.__len__(item) != field_count
                    or len(after_known_fields) != len(payload)
                    or any(field_name not in payload for field_name in after_known_fields)
                ):
                    raise ValueError("evidence input record changed while it was captured")
            else:
                field_names: list[object] = []
                field_iterator = dict.__iter__(item)
                try:
                    for _ in range(len(_VISUAL_EVIDENCE_FIELDS) + 1):
                        try:
                            field_names.append(next(field_iterator))
                        except StopIteration:
                            break
                except RuntimeError as exc:
                    raise ValueError("evidence input record changed while it was captured") from exc
                if len(field_names) != field_count or dict.__len__(item) != field_count:
                    raise ValueError("evidence input record changed while it was captured")
                for field_name in field_names:
                    if type(field_name) is not str or field_name not in _VISUAL_EVIDENCE_FIELDS:
                        raise ValueError("evidence input record contains an unknown public field")
                try:
                    payload = {
                        field_name: dict.__getitem__(item, field_name) for field_name in field_names
                    }
                except KeyError as exc:
                    raise ValueError("evidence input record changed while it was captured") from exc
                after_field_names: list[object] = []
                after_field_iterator = dict.__iter__(item)
                try:
                    for _ in range(len(_VISUAL_EVIDENCE_FIELDS) + 1):
                        try:
                            after_field_names.append(next(after_field_iterator))
                        except StopIteration:
                            break
                except RuntimeError as exc:
                    raise ValueError("evidence input record changed while it was captured") from exc
                try:
                    record_unchanged = (
                        dict.__len__(item) == field_count
                        and after_field_names == field_names
                        and all(
                            dict.__getitem__(item, field_name) is payload[field_name]
                            for field_name in field_names
                        )
                    )
                except KeyError as exc:
                    raise ValueError("evidence input record changed while it was captured") from exc
                if not record_unchanged:
                    raise ValueError("evidence input record changed while it was captured")
            live_source_block_ids = dict.get(payload, "source_block_ids", [])
            if type(live_source_block_ids) is not list:
                raise TypeError("evidence source_block_ids must be an exact plain list")
            source_block_count = list.__len__(live_source_block_ids)
            if source_block_count > MAX_EVIDENCE_REFS:
                raise ValueError("evidence source_block_ids exceeds its reference count limit")
            source_block_ids = list.__getitem__(
                live_source_block_ids,
                slice(0, source_block_count),
            )
            if (
                list.__len__(live_source_block_ids) != source_block_count
                or list.__len__(source_block_ids) != source_block_count
            ):
                raise ValueError("evidence source_block_ids changed while they were captured")
            for field_name, limit in (
                ("id", MAX_ID_CHARS),
                ("text", MAX_TEXT_CHARS),
                ("kind", _MAX_VISUAL_EVIDENCE_KIND_CHARS),
                ("font_weight", _MAX_VISUAL_EVIDENCE_FONT_WEIGHT_CHARS),
            ):
                value = dict.get(payload, field_name)
                if type(value) is str and len(value) > limit:
                    raise ValueError(f"evidence {field_name} exceeds its character limit")
            for block_id in source_block_ids:
                if type(block_id) is str and len(block_id) > MAX_ID_CHARS:
                    raise ValueError("evidence source_block_ids contains an oversized identifier")
            if list.__len__(live_source_block_ids) != source_block_count or any(
                list.__getitem__(live_source_block_ids, index) is not source_block_ids[index]
                for index in range(source_block_count)
            ):
                raise ValueError("evidence source_block_ids changed while they were captured")
            payload["source_block_ids"] = source_block_ids
            live_bbox = dict.get(payload, "bbox")
            if live_bbox is not None:
                if type(live_bbox) is list:
                    bbox_count = list.__len__(live_bbox)
                    if bbox_count != 4:
                        raise ValueError("evidence bbox must contain exactly four coordinates")
                    bbox = list.__getitem__(live_bbox, slice(0, bbox_count))
                    if list.__len__(live_bbox) != bbox_count or list.__len__(bbox) != bbox_count:
                        raise ValueError("evidence bbox changed while it was captured")
                    if any(
                        list.__getitem__(live_bbox, index) is not bbox[index]
                        for index in range(bbox_count)
                    ):
                        raise ValueError("evidence bbox changed while it was captured")
                    payload["bbox"] = tuple(bbox)
                elif type(live_bbox) is not tuple or tuple.__len__(live_bbox) != 4:
                    raise TypeError("evidence bbox must be a canonical four-coordinate sequence")
            item_id = dict.get(payload, "id")
            kind = dict.get(payload, "kind")
            text = dict.get(payload, "text")
            font_weight = dict.get(payload, "font_weight")
            score = dict.get(payload, "score")
            bbox = dict.get(payload, "bbox")
            if type(item_id) is not str or not item_id or len(item_id) > MAX_ID_CHARS:
                raise TypeError("evidence contains an invalid bounded id")
            _require_utf8_text(item_id, "evidence id")
            if (
                type(kind) is not str
                or len(kind) > _MAX_VISUAL_EVIDENCE_KIND_CHARS
                or kind not in _VISUAL_EVIDENCE_KINDS
            ):
                raise TypeError("evidence contains a non-canonical kind")
            _require_utf8_text(kind, "evidence kind")
            if text is not None:
                if type(text) is not str or len(text) > MAX_TEXT_CHARS:
                    raise TypeError("evidence contains invalid bounded text")
                _require_utf8_text(text, "evidence text")
            if font_weight is not None:
                if (
                    type(font_weight) is not str
                    or len(font_weight) > _MAX_VISUAL_EVIDENCE_FONT_WEIGHT_CHARS
                    or font_weight not in _VISUAL_EVIDENCE_FONT_WEIGHTS
                ):
                    raise TypeError("evidence contains a non-canonical font weight")
                _require_utf8_text(font_weight, "evidence font weight")
            if bbox is not None and (
                type(bbox) is not tuple
                or len(bbox) != 4
                or any(type(value) not in {int, float} for value in bbox)
                or not all(math.isfinite(value) for value in bbox)
                or bbox[2] < bbox[0]
                or bbox[3] < bbox[1]
            ):
                raise TypeError("evidence contains a non-canonical bbox")
            if score is not None and (
                type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1
            ):
                raise TypeError("evidence contains a non-canonical score")

            previous_usage = usage or EvidenceBudgetUsage()
            if (
                previous_usage.source_block_references + source_block_count
                > MAX_EVIDENCE_SOURCE_BLOCK_REFS
            ):
                raise ValueError("evidence source-block references exceed the aggregate limit")
            source_block_characters = 0
            for block_id in source_block_ids:
                if type(block_id) is not str or not block_id or len(block_id) > MAX_ID_CHARS:
                    raise TypeError("evidence source_block_ids contains an invalid identifier")
                _require_utf8_text(block_id, "evidence source block id")
                source_block_characters += len(block_id)
                if (
                    previous_usage.source_block_characters + source_block_characters
                    > MAX_EVIDENCE_SOURCE_BLOCK_CHARS
                ):
                    raise ValueError("evidence source-block characters exceed the aggregate limit")
            characters = len(item_id) + len(kind) + source_block_characters
            if text is not None:
                characters += len(text)
            if font_weight is not None:
                characters += len(font_weight)
            if previous_usage.characters + characters > resolved_character_limit:
                raise ValueError("evidence characters exceed the aggregate limit")
            record = VisualEvidence.model_validate(payload)
        else:
            raise TypeError("evidence input must contain plain objects or VisualEvidence records")
        snapshot = canonical_evidence_collection_snapshot(
            [record],
            base=usage,
            item_limit=resolved_item_limit,
            character_limit=resolved_character_limit,
        )
        usage = snapshot.usage
        canonical.extend(snapshot.evidence)

    if list.__len__(evidence) != item_count or any(
        list.__getitem__(evidence, index) is not items[index] for index in range(item_count)
    ):
        raise ValueError("evidence input changed while it was captured")
    return EvidenceCollectionSnapshot(
        evidence=tuple(canonical),
        usage=usage or EvidenceBudgetUsage(),
    )


class SceneElement(BaseModel):
    id: str
    role: str
    text: str | None = None
    bbox: BBox
    polygon: list[Point] | None = None
    shape: str | None = None
    fill_color: str | None = None
    border_color: str | None = None
    border_style: str | None = None
    font_weight: Literal["normal", "bold"] | None = None
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def confidence_is_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("id", "role")
    @classmethod
    def identifiers_are_bounded(cls, value: str) -> str:
        _require_utf8_text(value, "scene element id/role")
        if not value or len(value) > MAX_ID_CHARS:
            raise ValueError("scene element id/role must be non-empty and bounded")
        return value

    @field_validator("text", "shape", "fill_color", "border_color", "border_style")
    @classmethod
    def text_fields_are_bounded(cls, value: str | None) -> str | None:
        return _bounded_text(value, "scene element text")

    @field_validator("bbox")
    @classmethod
    def bbox_is_finite(cls, value: BBox) -> BBox:
        return _finite_bbox(value, "scene element bbox")  # type: ignore[return-value]

    @field_validator("polygon")
    @classmethod
    def polygon_is_bounded(cls, value: list[Point] | None) -> list[Point] | None:
        return _finite_points(value, "scene element polygon", MAX_POLYGON_POINTS)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_is_bounded(cls, value: list[str]) -> list[str]:
        return _bounded_references(value, "scene element evidence_ids")


class SceneRelation(BaseModel):
    id: str
    source_id: str | None = None
    target_id: str | None = None
    relation_type: str
    semantic_relation: Literal[
        "sequence",
        "conditional",
        "causal",
        "dependency",
        "association",
        "containment",
        "message",
        "data_flow",
        "unknown",
    ] = "unknown"
    label: str | None = None
    polyline: list[Point] = Field(default_factory=list)
    arrow_at_start: bool = False
    arrow_at_end: bool = True
    line_color: str | None = None
    line_style: str | None = None
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def confidence_is_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value

    @field_validator("id", "relation_type")
    @classmethod
    def identifiers_are_bounded(cls, value: str) -> str:
        _require_utf8_text(value, "scene relation id/type")
        if not value or len(value) > MAX_ID_CHARS:
            raise ValueError("scene relation id/type must be non-empty and bounded")
        return value

    @field_validator("source_id", "target_id")
    @classmethod
    def endpoint_ids_are_bounded(cls, value: str | None) -> str | None:
        _require_utf8_text(value, "scene relation endpoint")
        if value is not None and (not value or len(value) > MAX_ID_CHARS):
            raise ValueError("scene relation endpoint must be a non-empty bounded identifier")
        return value

    @field_validator("label", "line_color", "line_style")
    @classmethod
    def text_fields_are_bounded(cls, value: str | None) -> str | None:
        return _bounded_text(value, "scene relation text")

    @field_validator("polyline")
    @classmethod
    def polyline_is_bounded(cls, value: list[Point]) -> list[Point]:
        return _finite_points(value, "scene relation polyline", MAX_POLYLINE_POINTS) or []

    @field_validator("evidence_ids")
    @classmethod
    def evidence_is_bounded(cls, value: list[str]) -> list[str]:
        return _bounded_references(value, "scene relation evidence_ids")


class SceneGroup(BaseModel):
    id: str
    role: str
    label: str | None = None
    bbox: BBox
    member_ids: list[str] = Field(default_factory=list)

    @field_validator("id", "role")
    @classmethod
    def identifiers_are_bounded(cls, value: str) -> str:
        _require_utf8_text(value, "scene group id/role")
        if not value or len(value) > MAX_ID_CHARS:
            raise ValueError("scene group id/role must be non-empty and bounded")
        return value

    @field_validator("label")
    @classmethod
    def label_is_bounded(cls, value: str | None) -> str | None:
        return _bounded_text(value, "scene group label")

    @field_validator("bbox")
    @classmethod
    def bbox_is_finite(cls, value: BBox) -> BBox:
        return _finite_bbox(value, "scene group bbox")  # type: ignore[return-value]

    @field_validator("member_ids")
    @classmethod
    def members_are_bounded(cls, value: list[str]) -> list[str]:
        if len(value) > MAX_SCENE_ELEMENTS:
            raise ValueError("scene group has too many members")
        return _bounded_references(
            value,
            "scene group member_ids",
            limit=MAX_SCENE_ELEMENTS,
        )


class DiagramSceneIR(BaseModel):
    elements: list[SceneElement] = Field(default_factory=list, max_length=MAX_SCENE_ELEMENTS)
    relations: list[SceneRelation] = Field(default_factory=list, max_length=MAX_SCENE_RELATIONS)
    groups: list[SceneGroup] = Field(default_factory=list, max_length=MAX_SCENE_GROUPS)
    reading_direction: Literal["TB", "BT", "LR", "RL", "radial", "timeline", "unknown"] = "unknown"
    diagram_type_candidates: list[str] = Field(default_factory=list)
    coordinate_space: Literal["pixels", "normalized"] = "pixels"
    canvas_size: tuple[float, float] | None = None

    @field_validator("diagram_type_candidates")
    @classmethod
    def type_candidates_are_bounded(cls, value: list[str]) -> list[str]:
        return _bounded_references(value, "diagram_type_candidates")

    @field_validator("canvas_size")
    @classmethod
    def canvas_is_finite(cls, value: tuple[float, float] | None):
        if value is not None and (
            not all(math.isfinite(item) for item in value) or any(item <= 0 for item in value)
        ):
            raise ValueError("scene canvas size must be positive and finite")
        return value

    @model_validator(mode="after")
    def references_existing_elements(self) -> DiagramSceneIR:
        ids = {element.id for element in self.elements}
        if len(ids) != len(self.elements):
            raise ValueError("scene element ids must be unique")
        relation_ids = {relation.id for relation in self.relations}
        if len(relation_ids) != len(self.relations):
            raise ValueError("scene relation ids must be unique")
        for group in self.groups:
            missing = set(group.member_ids) - ids
            if missing:
                raise ValueError(f"group {group.id} references missing elements: {sorted(missing)}")
        for relation in self.relations:
            missing = {relation.source_id, relation.target_id} - ids - {None}
            if missing:
                raise ValueError(
                    f"relation {relation.id} references missing elements: {sorted(missing)}"
                )
        group_ids = [group.id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("scene group ids must be unique")
        boxes = [element.bbox for element in self.elements] + [group.bbox for group in self.groups]
        for x1, y1, x2, y2 in boxes:
            if x2 < x1 or y2 < y1:
                raise ValueError("bbox coordinates must be ordered as x1, y1, x2, y2")
            if self.coordinate_space == "normalized" and not all(
                0 <= value <= 1 for value in (x1, y1, x2, y2)
            ):
                raise ValueError("normalized bbox coordinates must be between 0 and 1")
        return self


class DiagramTypePrediction(BaseModel):
    candidates: list[str] = Field(max_length=64)
    scores: list[float] = Field(max_length=64)
    visual_signals: list[str] = Field(default_factory=list, max_length=256)
    negative_signals: list[str] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def candidates_and_scores_align(self) -> DiagramTypePrediction:
        if not self.candidates or len(self.candidates) != len(self.scores):
            raise ValueError("type candidates and scores must be non-empty and aligned")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("type candidates must be unique")
        if any(not 0 <= score <= 1 for score in self.scores):
            raise ValueError("type scores must be between 0 and 1")
        if any(left < right for left, right in zip(self.scores, self.scores[1:], strict=False)):
            raise ValueError("type candidates must be sorted by descending score")
        return self

    @field_validator("candidates")
    @classmethod
    def candidate_names_are_bounded(cls, value: list[str]) -> list[str]:
        return _bounded_references(value, "diagram type candidates")

    @field_validator("visual_signals", "negative_signals")
    @classmethod
    def signals_are_bounded(cls, value: list[str]) -> list[str]:
        for item in value:
            _require_utf8_text(item, "diagram type signal")
        if any(len(item) > MAX_WARNING_CHARS for item in value):
            raise ValueError("diagram type signal exceeds the text size limit")
        return value


class NodeIdMapping(BaseModel):
    """Auditable owner-Scene to fused-Scene identity mapping."""

    model_config = ConfigDict(frozen=True)

    source_owner: str = Field(min_length=1, max_length=512)
    source_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    fused_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    authority_source: Literal["vector", "geometry"]
    authority_owner: str = Field(min_length=1, max_length=512)
    match_method: Literal["identity", "unique_iou"]
    iou: float = Field(ge=NODE_ID_MAPPING_MIN_IOU, le=1)
    source_bbox: BBox
    authority_bbox: BBox
    source_text: str | None = None
    source_evidence_ids: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    authority_evidence_ids: tuple[str, ...] = Field(max_length=MAX_EVIDENCE_REFS)
    claim_digest: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @field_validator("source_owner", "source_id", "fused_id", "authority_owner")
    @classmethod
    def identifiers_are_utf8(cls, value: str) -> str:
        return _require_utf8_text(value, "node id mapping identifier")  # type: ignore[return-value]

    @field_validator("source_text")
    @classmethod
    def source_text_is_bounded(cls, value: str | None) -> str | None:
        return _bounded_text(value, "node id mapping source_text")

    @field_validator("source_bbox", "authority_bbox")
    @classmethod
    def normalized_bbox_is_valid(cls, value: BBox) -> BBox:
        _finite_bbox(value, "node id mapping bbox")
        if value[2] < value[0] or value[3] < value[1] or not all(0 <= item <= 1 for item in value):
            raise ValueError("node id mapping bbox must be ordered and normalized")
        return value

    @field_validator("source_evidence_ids", "authority_evidence_ids")
    @classmethod
    def evidence_is_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("node id mappings require source and authority evidence")
        if len(value) != len(set(value)):
            raise ValueError("node id mapping evidence must be unique")
        _bounded_references(list(value), "node id mapping evidence_ids")
        return value

    @model_validator(mode="after")
    def method_matches_identity(self) -> NodeIdMapping:
        if (self.source_id == self.fused_id) != (self.match_method == "identity"):
            raise ValueError("node id mapping method must match whether the id changed")
        measured_iou = bbox_iou(self.source_bbox, self.authority_bbox)
        if measured_iou < NODE_ID_MAPPING_MIN_IOU or not math.isclose(
            self.iou,
            measured_iou,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError("node id mapping IoU must match its normalized source/authority boxes")
        claim_payload = {
            "source_owner": self.source_owner,
            "source_id": self.source_id,
            "fused_id": self.fused_id,
            "authority_source": self.authority_source,
            "authority_owner": self.authority_owner,
            "match_method": self.match_method,
            "iou": self.iou,
            "source_bbox": self.source_bbox,
            "authority_bbox": self.authority_bbox,
            "source_text": self.source_text,
            "source_evidence_ids": self.source_evidence_ids,
            "authority_evidence_ids": self.authority_evidence_ids,
        }
        expected_digest = hashlib.sha256(
            json.dumps(
                claim_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
        if self.claim_digest is None:
            object.__setattr__(self, "claim_digest", expected_digest)
        elif self.claim_digest != expected_digest:
            raise ValueError("node id mapping claim digest does not match its fields")
        return self


def _node_id_mapping_seal(mappings: list[NodeIdMapping]) -> str:
    payload = json.dumps(
        [item.model_dump(mode="json") for item in mappings],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hmac.new(_NODE_ID_MAPPING_SEAL_KEY, payload, hashlib.sha256).hexdigest()


class TypedIRCandidate(BaseModel):
    diagram_type: str = Field(max_length=MAX_ID_CHARS)
    ir: dict[str, Any]
    confidence: float = Field(default=0.5, ge=0, le=1)

    model_config = ConfigDict(hide_input_in_errors=True)

    @field_validator("diagram_type")
    @classmethod
    def diagram_type_is_utf8(cls, value: str) -> str:
        return _require_utf8_text(value, "typed IR diagram type")  # type: ignore[return-value]

    @field_validator("ir", mode="before")
    @classmethod
    def ir_is_bounded(cls, value: Any) -> dict[str, Any]:
        snapshot = canonical_typed_ir_snapshot(value)
        if snapshot is None:
            raise ValueError("typed IR must be an exact plain dictionary")
        return snapshot

    @model_validator(mode="after")
    def matches_diagram_contract(self) -> TypedIRCandidate:
        validate_typed_ir_contract(self.diagram_type, self.ir)
        return self

    def canonical_key(self) -> str:
        # Repair workflows intentionally mutate typed IR in memory. Revalidate
        # the current payload here so post-construction mutation cannot bypass
        # the JSON/finite-number contract or destabilize fusion keys.
        _is_model, diagram_type, snapshot, confidence = _canonical_typed_candidate_fields(self)
        validated = TypedIRCandidate.model_validate(
            {
                "diagram_type": diagram_type,
                "ir": snapshot,
                "confidence": confidence,
            }
        )
        digest = hashlib.sha256(_canonical_json_bytes(validated.ir)).hexdigest()
        return f"{validated.diagram_type}\0sha256:{digest}"


def _canonical_typed_candidate_fields(
    value: object,
) -> tuple[bool, str, dict[str, Any], int | float]:
    """Capture the three public typed-candidate fields without unbounded copies."""

    is_model = type(value) is TypedIRCandidate
    if is_model:
        live_fields = object.__getattribute__(value, "__dict__")
    elif type(value) is dict:
        live_fields = value
    else:
        raise ValueError("engine typed candidates must be canonical records")
    if type(live_fields) is not dict:
        raise ValueError("typed IR candidate fields must be canonical")
    field_count = dict.__len__(live_fields)
    if field_count > MAX_TYPED_IR_CANDIDATE_FIELDS:
        raise ValueError("typed IR candidate exceeds the public field-count limit")
    fields = dict.copy(live_fields)
    if dict.__len__(fields) != field_count or dict.__len__(live_fields) != field_count:
        raise ValueError("typed IR candidate changed while it was captured")
    for field_name in list(dict.keys(fields)):
        if type(field_name) is not str or field_name not in {
            "diagram_type",
            "ir",
            "confidence",
        }:
            raise ValueError("typed IR candidate contains an unknown public field")

    missing = object()
    diagram_type = dict.get(fields, "diagram_type", missing)
    ir = dict.get(fields, "ir", missing)
    raw_confidence = dict.get(fields, "confidence", missing)
    if type(diagram_type) is not str:
        raise ValueError("typed IR diagram type must be a plain string")
    if not diagram_type or len(diagram_type) > MAX_ID_CHARS:
        raise ValueError("typed IR diagram type exceeds the text size limit")
    _require_utf8_text(diagram_type, "typed IR diagram type")
    snapshot = canonical_typed_ir_snapshot(ir)
    if snapshot is None:
        raise ValueError("typed IR must be an exact plain dictionary")
    if raw_confidence is missing:
        confidence: int | float = 0.5
    else:
        confidence = raw_confidence
    if type(confidence) not in {int, float}:
        raise ValueError("typed IR confidence must be an exact number")
    if (type(confidence) is float and not math.isfinite(confidence)) or not (0 <= confidence <= 1):
        raise ValueError("typed IR confidence must be a finite probability")

    if dict.__len__(live_fields) != field_count:
        raise ValueError("typed IR candidate changed while it was captured")
    after_fields = dict.copy(live_fields)
    if dict.__len__(after_fields) != field_count or dict.__len__(live_fields) != field_count:
        raise ValueError("typed IR candidate changed while it was captured")
    for field_name in list(dict.keys(after_fields)):
        if type(field_name) is not str or field_name not in {
            "diagram_type",
            "ir",
            "confidence",
        }:
            raise ValueError("typed IR candidate contains an unknown public field")
    if (
        dict.get(after_fields, "diagram_type", missing) is not diagram_type
        or dict.get(after_fields, "ir", missing) is not ir
        or dict.get(after_fields, "confidence", missing) is not raw_confidence
    ):
        raise ValueError("typed IR candidate changed while it was captured")
    return is_model, diagram_type, snapshot, confidence


class DirectMermaidCandidate(BaseModel):
    diagram_type: str = Field(max_length=MAX_ID_CHARS)
    code: str = Field(max_length=50_000)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("diagram_type", "code")
    @classmethod
    def text_is_utf8(cls, value: str) -> str:
        return _require_utf8_text(value, "direct Mermaid candidate")  # type: ignore[return-value]

    def canonical_key(self) -> str:
        """Return the fusion identity without treating confidence as source content."""

        validated = type(self).model_validate(self.model_dump(mode="python"))
        return json.dumps(
            [validated.diagram_type, validated.code.strip()],
            ensure_ascii=False,
            separators=(",", ":"),
        )


class PromptBudgetNotice(BaseModel):
    """Adapter-owned audit record for one bounded Structured VLM request."""

    engine: str = Field(max_length=MAX_ID_CHARS)
    selection_profile: Literal["structural-quota-v1"]
    prompt_chars: int = Field(ge=0, le=1_000_000)
    max_prompt_chars: int = Field(ge=1, le=1_000_000)
    schema_reserve_chars: int = Field(ge=0, le=65_536)
    max_evidence_items: int = Field(ge=1, le=4_096)
    max_ocr_items: int = Field(ge=0, le=4_096)
    evidence_total: int = Field(ge=0, le=MAX_OBSERVATION_EVIDENCE)
    evidence_considered: int = Field(ge=0, le=MAX_OBSERVATION_EVIDENCE)
    evidence_included: int = Field(ge=0, le=MAX_OBSERVATION_EVIDENCE)
    ocr_total: int = Field(ge=0)
    ocr_considered: int = Field(ge=0, le=4_096)
    ocr_included: int = Field(ge=0, le=4_096)
    omission_reasons: list[
        Literal[
            "evidence_item_limit",
            "evidence_char_limit",
            "ocr_item_limit",
            "ocr_char_limit",
        ]
    ] = Field(default_factory=list, max_length=4)
    selected_evidence_sha256: str

    @field_validator("engine")
    @classmethod
    def engine_is_plain_utf8(cls, value: str) -> str:
        _require_utf8_text(value, "prompt budget engine")
        if not value:
            raise ValueError("prompt budget engine must be non-empty")
        return value

    @field_validator("selected_evidence_sha256")
    @classmethod
    def selected_digest_is_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("selected evidence digest must be lowercase SHA-256 hex")
        return value

    @model_validator(mode="after")
    def counts_and_request_fit(self) -> PromptBudgetNotice:
        if not self.evidence_included <= self.evidence_considered <= self.evidence_total:
            raise ValueError("prompt evidence counts must be ordered")
        if not self.ocr_included <= self.ocr_considered <= self.ocr_total:
            raise ValueError("prompt OCR counts must be ordered")
        if self.evidence_included > min(self.evidence_total, self.max_evidence_items):
            raise ValueError("included prompt evidence exceeds its item budget")
        if self.ocr_considered != min(self.ocr_total, self.max_ocr_items):
            raise ValueError("considered prompt OCR must match its bounded input prefix")
        if self.prompt_chars + self.schema_reserve_chars > self.max_prompt_chars:
            raise ValueError("prompt plus schema reserve exceeds its request budget")
        if len(self.omission_reasons) != len(set(self.omission_reasons)):
            raise ValueError("prompt omission reasons must be unique")
        reasons = set(self.omission_reasons)
        if (self.evidence_total > self.max_evidence_items) != ("evidence_item_limit" in reasons):
            raise ValueError("evidence item-limit reason does not match its item budget")
        if (self.ocr_total > self.max_ocr_items) != ("ocr_item_limit" in reasons):
            raise ValueError("OCR item-limit reason does not match its item budget")
        if (self.evidence_considered > self.evidence_included) != (
            "evidence_char_limit" in reasons
        ):
            raise ValueError("evidence character-limit reason does not match selection counts")
        if (self.ocr_considered > self.ocr_included) != ("ocr_char_limit" in reasons):
            raise ValueError("OCR character-limit reason does not match selection counts")
        return self


def canonical_prompt_budget_notice_json(
    notices: list[PromptBudgetNotice],
) -> list[dict[str, Any]]:
    """Return canonical JSON records for bounded prompt-audit sinks."""

    if type(notices) is not list or len(notices) > MAX_OBSERVATION_WARNINGS:
        raise TypeError("prompt budget notices must be a bounded plain list")
    values: list[dict[str, Any]] = []
    for item in notices:
        if type(item) is not PromptBudgetNotice:
            raise TypeError("prompt budget notices must be canonical records")
        values.append(
            PromptBudgetNotice.model_validate(item.model_dump(mode="python")).model_dump(
                mode="json"
            )
        )
    return values


class EngineObservation(BaseModel):
    prediction: DiagramTypePrediction
    scene_ir: DiagramSceneIR | None = None
    typed_candidates: list[TypedIRCandidate] = Field(
        default_factory=list, max_length=MAX_OBSERVATION_CANDIDATES
    )
    direct_candidates: list[DirectMermaidCandidate] = Field(
        default_factory=list, max_length=MAX_OBSERVATION_CANDIDATES
    )
    evidence: list[VisualEvidence] = Field(
        default_factory=list, max_length=MAX_OBSERVATION_EVIDENCE
    )
    warnings: list[str] = Field(default_factory=list, max_length=MAX_OBSERVATION_WARNINGS)
    _fusion_node_id_mappings: dict[str, list[NodeIdMapping]] = PrivateAttr(default_factory=dict)
    _fusion_conflicted_connector_pairs: set[frozenset[str]] = PrivateAttr(default_factory=set)
    _prompt_supplied_prior_evidence_ids: frozenset[str] | None = PrivateAttr(default=None)
    _prompt_budget_notice: PromptBudgetNotice | None = PrivateAttr(default=None)
    _fusion_typed_evidence_authorities: dict[str, frozenset[str]] = PrivateAttr(
        default_factory=dict
    )
    _fusion_direct_evidence_authorities: dict[str, frozenset[str]] = PrivateAttr(
        default_factory=dict
    )
    _fusion_scene_evidence_authority: frozenset[str] | None = PrivateAttr(default=None)

    model_config = ConfigDict(hide_input_in_errors=True)

    @field_validator("typed_candidates", mode="wrap")
    @classmethod
    def typed_candidate_ir_is_prevalidated(
        cls,
        value: Any,
        _handler: Any,
    ) -> list[TypedIRCandidate]:
        if type(value) is not list:
            raise ValueError("engine typed candidates must be an exact plain list")
        candidate_count = list.__len__(value)
        if candidate_count > MAX_OBSERVATION_CANDIDATES:
            raise ValueError("too_long: engine typed candidates exceed the candidate count limit")
        candidates = list.__getitem__(value, slice(0, candidate_count))
        if list.__len__(value) != candidate_count:
            raise ValueError("engine typed candidates changed while they were captured")

        aggregate_bytes = 0
        snapshots: list[TypedIRCandidate] = []
        for index in range(candidate_count):
            candidate = list.__getitem__(candidates, index)
            is_model, diagram_type, ir_snapshot, confidence = _canonical_typed_candidate_fields(
                candidate
            )
            aggregate_bytes += len(_canonical_json_bytes(ir_snapshot))
            if aggregate_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                raise ValueError("engine typed candidates exceed the aggregate JSON byte budget")
            if is_model:
                snapshots.append(
                    TypedIRCandidate.model_construct(
                        diagram_type=diagram_type,
                        ir=ir_snapshot,
                        confidence=confidence,
                    )
                )
            else:
                snapshots.append(
                    TypedIRCandidate.model_validate(
                        {
                            "diagram_type": diagram_type,
                            "ir": ir_snapshot,
                            "confidence": confidence,
                        }
                    )
                )
        return snapshots

    @field_validator("warnings")
    @classmethod
    def warning_text_is_bounded(cls, value: list[str]) -> list[str]:
        normalized = [_sink_safe_diagnostic_text(item) for item in value]
        if any(len(item) > MAX_WARNING_CHARS for item in normalized):
            raise ValueError("engine warning exceeds the text size limit")
        return normalized

    def _set_fusion_metadata(
        self,
        node_id_mappings: dict[str, list[NodeIdMapping]],
        conflicted_connector_pairs: set[frozenset[str]],
        typed_evidence_authorities: dict[str, frozenset[str]] | None = None,
        scene_evidence_authority: frozenset[str] | None = None,
        direct_evidence_authorities: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self._fusion_node_id_mappings = {
            key: [item.model_copy(deep=True) for item in values]
            for key, values in node_id_mappings.items()
        }
        self._fusion_conflicted_connector_pairs = set(conflicted_connector_pairs)
        self._fusion_typed_evidence_authorities = {
            key: _bounded_evidence_authority_ids(values)
            for key, values in (typed_evidence_authorities or {}).items()
        }
        self._fusion_direct_evidence_authorities = {
            key: _bounded_evidence_authority_ids(values)
            for key, values in (direct_evidence_authorities or {}).items()
        }
        self._fusion_scene_evidence_authority = (
            _bounded_evidence_authority_ids(scene_evidence_authority)
            if scene_evidence_authority is not None
            else None
        )

    def _set_prompt_supplied_prior_evidence_ids(self, evidence_ids: set[str]) -> None:
        """Attach adapter-owned prompt authority without exposing it in provider schemas."""

        self._prompt_supplied_prior_evidence_ids = _bounded_evidence_authority_ids(evidence_ids)

    @property
    def prompt_supplied_prior_evidence_ids(self) -> frozenset[str] | None:
        return self._prompt_supplied_prior_evidence_ids

    def _set_prompt_budget_notice(self, notice: PromptBudgetNotice) -> None:
        if type(notice) is not PromptBudgetNotice:
            raise ValueError("prompt budget notice must be canonical")
        self._prompt_budget_notice = PromptBudgetNotice.model_validate(
            notice.model_dump(mode="python")
        )

    @property
    def prompt_budget_notice(self) -> PromptBudgetNotice | None:
        return (
            self._prompt_budget_notice.model_copy(deep=True)
            if self._prompt_budget_notice is not None
            else None
        )

    def fusion_typed_evidence_authority_for(
        self,
        candidate: TypedIRCandidate,
    ) -> frozenset[str] | None:
        return self._fusion_typed_evidence_authorities.get(candidate.canonical_key())

    def fusion_direct_evidence_authority_for(
        self,
        candidate: DirectMermaidCandidate,
    ) -> frozenset[str] | None:
        return self._fusion_direct_evidence_authorities.get(candidate.canonical_key())

    @property
    def fusion_scene_evidence_authority(self) -> frozenset[str] | None:
        return self._fusion_scene_evidence_authority

    def fusion_node_id_mappings_for(
        self,
        candidate: TypedIRCandidate,
    ) -> list[NodeIdMapping]:
        return [
            item.model_copy(deep=True)
            for item in self._fusion_node_id_mappings.get(candidate.canonical_key(), [])
        ]

    @property
    def fusion_conflicted_connector_pairs(self) -> set[frozenset[str]]:
        return set(self._fusion_conflicted_connector_pairs)


class RepairEvent(BaseModel):
    iteration: int
    operation: str
    before_score: float | None = None
    after_score: float | None = None
    accepted: bool
    details: dict[str, Any] = Field(default_factory=dict)


class MetricResult(BaseModel):
    name: str
    value: float | None = None
    available: bool = False
    warning: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def availability_matches_value(self) -> MetricResult:
        if self.available != (self.value is not None):
            raise ValueError("available must be true exactly when metric value is present")
        if self.value is not None and not 0 <= self.value <= 1:
            raise ValueError("metric value must be between 0 and 1")
        return self


class CandidateValidationReceipt(BaseModel):
    """Public digests for the exact artifacts accepted by the trusted validator.

    The receipt remains useful in sidecar metadata, but publication additionally
    requires a process-private seal. Deserializing or hand-constructing matching
    digest fields therefore cannot turn an untrusted candidate into Markdown.
    """

    schema_version: Literal["1"] = "1"
    code_sha256: str
    svg_sha256: str
    png_sha256: str | None = None
    security_profile: SecurityProfile
    emitted_diagram_type: str
    runtime_diagram_type: str | None = None

    model_config = ConfigDict(frozen=True)

    @field_validator("code_sha256", "svg_sha256", "png_sha256")
    @classmethod
    def digest_is_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("validation receipt digests must be lowercase SHA-256 hex")
        return value

    @field_validator("emitted_diagram_type", "runtime_diagram_type")
    @classmethod
    def diagram_type_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and (not value or len(value) > MAX_ID_CHARS):
            raise ValueError("validation receipt diagram types must be non-empty and bounded")
        return value


class ValidatedArtifactCertificate(BaseModel):
    """Process-local proof that CandidateValidator accepted exact artifacts."""

    code_sha256: str
    svg_sha256: str
    png_sha256: str | None = None
    security_profile: SecurityProfile
    runtime_diagram_type: str | None = None
    _certificate_seal: str | None = PrivateAttr(default=None)

    model_config = ConfigDict(frozen=True)

    @field_validator("code_sha256", "svg_sha256", "png_sha256")
    @classmethod
    def digest_is_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("artifact certificate digests must be lowercase SHA-256 hex")
        return value

    @field_validator("runtime_diagram_type")
    @classmethod
    def runtime_type_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and (not value or len(value) > MAX_ID_CHARS):
            raise ValueError("artifact certificate runtime type must be non-empty and bounded")
        return value

    def has_trusted_values(self) -> bool:
        try:
            seal = self._certificate_seal
            return bool(
                type(self) is ValidatedArtifactCertificate
                and type(seal) is str
                and hmac.compare_digest(seal, _validated_artifact_certificate_seal(self))
            )
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
            return False


class PublicationAuthorizationReceipt(BaseModel):
    """Auditable fields from the publication decision bound by a private seal."""

    schema_version: Literal["1"] = "1"
    source_id: str
    selected_candidate_id: str
    candidate_validation_sha256: str
    candidate_quality_sha256: str
    publish_policy: PublishPolicy
    security_profile: SecurityProfile
    publish: bool
    review_required: bool
    status: Literal["success", "failed", "skipped", "review_required"]
    grade: QualityGrade
    serialization_stability: Literal["stable", "extended", "experimental"] = "stable"

    model_config = ConfigDict(frozen=True)

    @field_validator("source_id", "selected_candidate_id")
    @classmethod
    def identifier_is_bounded(cls, value: str) -> str:
        if not value or len(value) > MAX_ID_CHARS:
            raise ValueError("publication receipt identifiers must be non-empty and bounded")
        return value

    @field_validator("candidate_validation_sha256", "candidate_quality_sha256")
    @classmethod
    def candidate_digest_is_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("publication receipt digest must be lowercase SHA-256 hex")
        return value


class AuthorizedPublicationSnapshot(BaseModel):
    """Immutable, already-authorized values consumed by publication sinks.

    Returning the exact values that were checked avoids a check-then-reread race
    when a live reconstruction result is shared with another thread.
    """

    source_id: str
    selected_candidate_id: str
    mermaid_code: str
    grade: QualityGrade
    serialization_stability: Literal["stable", "extended", "experimental"]
    aggregate_score: float | None = None
    png: bytes | None = None
    preview_omitted: bool = False
    validation_receipt: CandidateValidationReceipt
    publication_receipt: PublicationAuthorizationReceipt
    _authorization_seal: str | None = PrivateAttr(default=None)

    model_config = ConfigDict(frozen=True)

    def has_trusted_values(self) -> bool:
        """Return whether every sink-visible value retains its private seal."""

        try:
            seal = self._authorization_seal
            if not (
                type(self) is AuthorizedPublicationSnapshot
                and type(self.source_id) is str
                and type(self.selected_candidate_id) is str
                and type(self.mermaid_code) is str
                and type(self.grade) is str
                and type(self.serialization_stability) is str
                and self.serialization_stability in {"stable", "extended", "experimental"}
                and (self.aggregate_score is None or type(self.aggregate_score) is float)
                and (self.png is None or type(self.png) is bytes)
                and type(self.preview_omitted) is bool
                and type(self.validation_receipt) is CandidateValidationReceipt
                and type(self.publication_receipt) is PublicationAuthorizationReceipt
                and type(seal) is str
            ):
                return False
            validation_receipt = self.validation_receipt
            publication_receipt = self.publication_receipt
            png_digest = _binary_artifact_sha256(self.png) if self.png is not None else None
            if (
                validation_receipt.code_sha256 != _artifact_sha256(self.mermaid_code)
                or publication_receipt.source_id != self.source_id
                or publication_receipt.selected_candidate_id != self.selected_candidate_id
                or publication_receipt.grade != self.grade
                or publication_receipt.serialization_stability
                != self.serialization_stability
                or publication_receipt.candidate_validation_sha256
                != _canonical_model_sha256(validation_receipt)
                or publication_receipt.security_profile != validation_receipt.security_profile
                or (self.png is not None and validation_receipt.png_sha256 != png_digest)
                or (
                    self.png is None
                    and validation_receipt.png_sha256 is not None
                    and not self.preview_omitted
                )
            ):
                return False
            return hmac.compare_digest(seal, _publication_snapshot_seal(self))
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
            return False


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_json_mapping_snapshot(
    value: dict[str, Any] | None,
    *,
    boundary_name: str,
    max_depth: int,
    max_items: int,
    max_string_chars: int,
    max_utf8_text_bytes: int | None,
    max_json_bytes: int,
    max_abs_number: int,
    count_object_keys_as_items: bool,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError(f"{boundary_name} must be an exact plain dictionary")

    holder: list[Any] = [None]
    # Visit records contain source, destination container, destination slot, and depth.
    pending: list[tuple[str, Any, Any, Any, int]] = [("visit", value, holder, 0, 0)]
    active_container_ids: set[int] = set()
    item_count = 1
    utf8_text_bytes = 0
    encoded_size = 0

    while pending:
        operation, source, destination, slot, depth = pending.pop()
        if operation == "exit":
            active_container_ids.remove(source)
            continue
        if depth > max_depth:
            raise ValueError(f"{boundary_name} exceeds the nesting depth budget")

        source_type = type(source)
        if source_type is dict:
            source_id = id(source)
            if source_id in active_container_ids:
                raise ValueError(f"{boundary_name} must not contain reference cycles")
            source_length = dict.__len__(source)
            additional_items = source_length * (2 if count_object_keys_as_items else 1)
            if item_count + additional_items > max_items:
                raise ValueError(f"{boundary_name} exceeds the item budget")
            shallow_source = dict.copy(source)
            if dict.__len__(shallow_source) != source_length:
                raise ValueError(f"{boundary_name} changed while it was captured")
            keys = list(dict.keys(shallow_source))
            for key in keys:
                if type(key) is not str:
                    raise ValueError(f"{boundary_name} object keys must be plain strings")
                if len(key) > max_string_chars:
                    raise ValueError(f"{boundary_name} string exceeds the field size budget")
                _require_utf8_text(key, f"{boundary_name} string")
                utf8_text_bytes += len(key.encode("utf-8"))
                if max_utf8_text_bytes is not None and utf8_text_bytes > max_utf8_text_bytes:
                    raise ValueError(f"{boundary_name} exceeds the UTF-8 text byte budget")
            keys.sort()
            item_count += additional_items
            encoded_size += 2 + source_length + max(0, source_length - 1)
            if encoded_size > max_json_bytes:
                raise ValueError(f"{boundary_name} exceeds the escaped JSON byte budget")
            for key in keys:
                encoded_size += len(_canonical_json_bytes(key))
                if encoded_size > max_json_bytes:
                    raise ValueError(f"{boundary_name} exceeds the escaped JSON byte budget")

            copied: dict[str, Any] = {}
            destination[slot] = copied
            active_container_ids.add(source_id)
            pending.append(("exit", source_id, None, None, depth))
            for key in reversed(keys):
                pending.append(
                    (
                        "visit",
                        dict.__getitem__(shallow_source, key),
                        copied,
                        key,
                        depth + 1,
                    )
                )
            continue

        if source_type in {list, tuple}:
            source_id = id(source)
            if source_id in active_container_ids:
                raise ValueError(f"{boundary_name} must not contain reference cycles")
            source_length = list.__len__(source) if source_type is list else tuple.__len__(source)
            if item_count + source_length > max_items:
                raise ValueError(f"{boundary_name} exceeds the item budget")
            shallow_source = list.copy(source) if source_type is list else source
            if len(shallow_source) != source_length:
                raise ValueError(f"{boundary_name} changed while it was captured")
            item_count += source_length
            encoded_size += 2 + max(0, source_length - 1)
            if encoded_size > max_json_bytes:
                raise ValueError(f"{boundary_name} exceeds the escaped JSON byte budget")

            copied = [None] * source_length
            destination[slot] = copied
            active_container_ids.add(source_id)
            pending.append(("exit", source_id, None, None, depth))
            item_getter = list.__getitem__ if source_type is list else tuple.__getitem__
            for index in range(source_length - 1, -1, -1):
                pending.append(
                    (
                        "visit",
                        item_getter(shallow_source, index),
                        copied,
                        index,
                        depth + 1,
                    )
                )
            continue

        if source_type is str:
            if len(source) > max_string_chars:
                raise ValueError(f"{boundary_name} string exceeds the field size budget")
            _require_utf8_text(source, f"{boundary_name} string")
            utf8_text_bytes += len(source.encode("utf-8"))
            if max_utf8_text_bytes is not None and utf8_text_bytes > max_utf8_text_bytes:
                raise ValueError(f"{boundary_name} exceeds the UTF-8 text byte budget")
        elif source_type is int:
            if not -max_abs_number <= source <= max_abs_number:
                raise ValueError(f"{boundary_name} integer exceeds the numeric budget")
        elif source_type is float:
            if not math.isfinite(source) or abs(source) > max_abs_number:
                raise ValueError(f"{boundary_name} number must be finite and bounded")
        elif source_type not in {bool, type(None)}:
            raise ValueError(f"{boundary_name} values must be exact JSON-compatible built-ins")
        encoded_size += len(_canonical_json_bytes(source))
        if encoded_size > max_json_bytes:
            raise ValueError(f"{boundary_name} exceeds the escaped JSON byte budget")
        destination[slot] = source

    snapshot = holder[0]
    if type(snapshot) is not dict:
        raise ValueError(f"{boundary_name} snapshot did not retain a dictionary root")
    if len(_canonical_json_bytes(snapshot)) != encoded_size:
        raise ValueError(f"{boundary_name} escaped JSON accounting was inconsistent")
    return snapshot


def canonical_source_mapping_snapshot(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a bounded, hook-free JSON snapshot of source mapping metadata."""

    return _canonical_json_mapping_snapshot(
        value,
        boundary_name="source mapping",
        max_depth=MAX_SOURCE_MAPPING_DEPTH,
        max_items=MAX_SOURCE_MAPPING_ITEMS,
        max_string_chars=MAX_SOURCE_MAPPING_STRING_CHARS,
        max_utf8_text_bytes=None,
        max_json_bytes=MAX_SOURCE_MAPPING_JSON_BYTES,
        max_abs_number=MAX_SOURCE_MAPPING_ABS_NUMBER,
        count_object_keys_as_items=False,
    )


def canonical_typed_ir_snapshot(
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a bounded detached typed-IR snapshot using exact JSON built-ins."""

    return _canonical_json_mapping_snapshot(
        value,
        boundary_name="typed IR",
        max_depth=MAX_IR_DEPTH,
        max_items=MAX_IR_ITEMS,
        max_string_chars=MAX_IR_TEXT_CHARS,
        max_utf8_text_bytes=MAX_IR_UTF8_TEXT_BYTES,
        max_json_bytes=MAX_IR_JSON_BYTES,
        max_abs_number=MAX_IR_ABS_NUMBER,
        count_object_keys_as_items=True,
    )


def _canonical_model_bytes(value: BaseModel) -> bytes:
    return _canonical_json_bytes(value.model_dump(mode="json"))


def _canonical_model_sha256(value: BaseModel) -> str:
    return hashlib.sha256(_canonical_model_bytes(value)).hexdigest()


def _candidate_quality_sha256(
    aggregate_score: float | None,
    grade: QualityGrade,
    scores: dict[str, float],
    warnings: list[str],
) -> str:
    if aggregate_score is not None and not (
        type(aggregate_score) is float
        and math.isfinite(aggregate_score)
        and 0 <= aggregate_score <= 1
    ):
        raise ValueError("aggregate score must be a finite probability")
    if type(scores) is not dict or len(scores) > MAX_OBSERVATION_WARNINGS:
        raise ValueError("quality metrics must be a bounded plain mapping")
    if any(
        type(key) is not str
        or not key
        or len(key) > MAX_ID_CHARS
        or re.fullmatch(r"[a-z][a-z0-9_]*", key) is None
        or type(score) is not float
        or not math.isfinite(score)
        or not 0 <= score <= 1
        for key, score in scores.items()
    ):
        raise ValueError("quality metrics must contain bounded probability values")
    if type(warnings) is not list or len(warnings) > MAX_OBSERVATION_WARNINGS:
        raise ValueError("quality warnings must be a bounded plain list")
    if any(type(warning) is not str or len(warning) > MAX_WARNING_CHARS for warning in warnings):
        raise ValueError("quality warnings must contain bounded plain strings")

    def canonical_probability(value: float) -> str:
        text = format(Decimal(str(value)), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text in {"-0", ""} else text

    payload = {
        "aggregate_score": (
            canonical_probability(aggregate_score) if aggregate_score is not None else None
        ),
        "grade": grade,
        "metrics": {key: canonical_probability(score) for key, score in scores.items()},
        "warnings": list(warnings),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _artifact_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _binary_artifact_sha256(value: bytes) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def _validation_receipt_seal(receipt: CandidateValidationReceipt) -> str:
    return hmac.new(
        _VALIDATION_RECEIPT_SEAL_KEY,
        _canonical_model_bytes(receipt),
        hashlib.sha256,
    ).hexdigest()


def _validated_artifact_certificate_seal(
    certificate: ValidatedArtifactCertificate,
) -> str:
    return hmac.new(
        _VALIDATED_ARTIFACT_CERTIFICATE_SEAL_KEY,
        _canonical_model_bytes(certificate),
        hashlib.sha256,
    ).hexdigest()


def _issue_validated_artifact_certificate(
    *,
    code: str,
    svg: str,
    png: bytes | None,
    profile: SecurityProfile,
    runtime_diagram_type: str | None,
) -> ValidatedArtifactCertificate:
    """Issue validator evidence for exact already-inspected runtime artifacts."""

    if (
        type(code) is not str
        or type(svg) is not str
        or (png is not None and type(png) is not bytes)
    ):
        raise ValueError("validated artifacts must use exact plain value types")
    certificate = ValidatedArtifactCertificate(
        code_sha256=_artifact_sha256(code),
        svg_sha256=_artifact_sha256(svg),
        png_sha256=_binary_artifact_sha256(png) if png is not None else None,
        security_profile=profile,
        runtime_diagram_type=runtime_diagram_type,
    )
    certificate._certificate_seal = _validated_artifact_certificate_seal(certificate)
    return certificate


def _publication_authorization_seal(receipt: PublicationAuthorizationReceipt) -> str:
    return hmac.new(
        _PUBLICATION_AUTHORIZATION_SEAL_KEY,
        _canonical_model_bytes(receipt),
        hashlib.sha256,
    ).hexdigest()


def _publication_snapshot_seal(snapshot: AuthorizedPublicationSnapshot) -> str:
    payload = {
        "source_id": snapshot.source_id,
        "selected_candidate_id": snapshot.selected_candidate_id,
        "mermaid_code_sha256": _artifact_sha256(snapshot.mermaid_code),
        "grade": snapshot.grade,
        "serialization_stability": snapshot.serialization_stability,
        "aggregate_score": snapshot.aggregate_score,
        "png_sha256": (_binary_artifact_sha256(snapshot.png) if snapshot.png is not None else None),
        "preview_omitted": snapshot.preview_omitted,
        "validation_receipt_sha256": _canonical_model_sha256(snapshot.validation_receipt),
        "publication_receipt_sha256": _canonical_model_sha256(snapshot.publication_receipt),
    }
    return hmac.new(
        _PUBLICATION_SNAPSHOT_SEAL_KEY,
        _canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


class MermaidCandidate(BaseModel):
    candidate_id: str
    generation_method: str
    generation_engine: str | None = None
    diagram_type: str = Field(max_length=MAX_ID_CHARS)
    emitted_diagram_type: str | None = None
    runtime_diagram_type: str | None = None
    fallback_chain: list[str] = Field(default_factory=list)
    serialization_stability: Literal["stable", "extended", "experimental"] = "stable"
    # ``scene_ir`` records what extraction engines observed in the source image.
    # Keep the reconstructed candidate scene separate so evaluation never compares
    # a source observation with itself.
    scene_ir: DiagramSceneIR | None = None
    generated_scene_ir: DiagramSceneIR | None = None
    typed_ir: dict[str, Any] | None = None
    raw_mermaid: str | None = None
    node_id_mappings: list[NodeIdMapping] = Field(
        default_factory=list,
        max_length=MAX_SCENE_ELEMENTS,
    )
    mermaid_code: str | None = None
    ast: dict[str, Any] | None = None
    svg: str | None = None
    png: bytes | None = None
    validation_receipt: CandidateValidationReceipt | None = None
    syntax_valid: bool = False
    render_valid: bool = False
    scores: dict[str, float] = Field(default_factory=dict)
    aggregate_score: float | None = None
    warnings: list[str] = Field(default_factory=list)
    repair_history: list[RepairEvent] = Field(default_factory=list)
    _node_id_mapping_seal: str | None = PrivateAttr(default=None)
    _validation_receipt_seal: str | None = PrivateAttr(default=None)
    _publication_evidence_authority_ids: frozenset[str] | None = PrivateAttr(default=None)

    model_config = ConfigDict(arbitrary_types_allowed=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def diagram_type_is_bounded_plain_utf8(cls, value: Any) -> Any:
        if type(value) is not dict:
            raise ValueError("candidate input must be an exact plain dictionary")
        field_count = dict.__len__(value)
        if field_count > len(cls.model_fields):
            raise ValueError("candidate input exceeds the public field-count limit")
        fields = dict.copy(value)
        if dict.__len__(fields) != field_count or dict.__len__(value) != field_count:
            raise ValueError("candidate input changed while it was captured")
        for field_name in list(dict.keys(fields)):
            if type(field_name) is not str:
                raise ValueError("candidate input field names must be plain strings")
        diagram_type = dict.get(fields, "diagram_type")
        if type(diagram_type) is not str:
            raise ValueError("candidate diagram type must be a plain string")
        if not diagram_type or len(diagram_type) > MAX_ID_CHARS:
            raise ValueError("candidate diagram type exceeds the text size limit")
        _require_utf8_text(diagram_type, "candidate diagram type")
        return fields

    def _seal_node_id_mappings(self) -> None:
        """Mark current mappings as produced by the trusted reconstruction pipeline."""

        self._node_id_mapping_seal = (
            _node_id_mapping_seal(self.node_id_mappings) if self.node_id_mappings else None
        )

    def _set_publication_evidence_authority_ids(
        self,
        evidence_ids: set[str] | frozenset[str],
    ) -> None:
        self._publication_evidence_authority_ids = _bounded_evidence_authority_ids(evidence_ids)

    @property
    def publication_evidence_authority_ids(self) -> frozenset[str] | None:
        return self._publication_evidence_authority_ids

    def _has_valid_node_id_mapping_seal(self) -> bool:
        if not self.node_id_mappings or self._node_id_mapping_seal is None:
            return False
        return hmac.compare_digest(
            self._node_id_mapping_seal,
            _node_id_mapping_seal(self.node_id_mappings),
        )

    def _seal_validation_receipt(
        self,
        certificate: ValidatedArtifactCertificate | None,
    ) -> None:
        """Bind the current code and inspected SVG after trusted validation."""

        self.validation_receipt = None
        self._validation_receipt_seal = None
        if not (
            self.syntax_valid
            and self.render_valid
            and type(certificate) is ValidatedArtifactCertificate
            and certificate.has_trusted_values()
            and type(self.mermaid_code) is str
            and bool(self.mermaid_code.strip())
            and type(self.svg) is str
            and bool(self.svg.strip())
            and len(self.svg.encode("utf-8")) <= MAX_RENDER_BYTES
            and type(self.emitted_diagram_type) is str
            and bool(self.emitted_diagram_type)
            and len(self.emitted_diagram_type) <= MAX_ID_CHARS
            and _canonical_runtime_diagram_type(certificate.runtime_diagram_type)
            == self.emitted_diagram_type
            and (
                self.runtime_diagram_type is None
                or (
                    type(self.runtime_diagram_type) is str
                    and bool(self.runtime_diagram_type)
                    and len(self.runtime_diagram_type) <= MAX_ID_CHARS
                )
            )
        ):
            return
        validated_png = (
            self.png if type(self.png) is bytes and png_inspection_error(self.png) is None else None
        )
        if (
            certificate.code_sha256 != _artifact_sha256(self.mermaid_code)
            or certificate.svg_sha256 != _artifact_sha256(self.svg)
            or certificate.png_sha256
            != (_binary_artifact_sha256(validated_png) if validated_png is not None else None)
            or certificate.runtime_diagram_type != self.runtime_diagram_type
        ):
            return
        try:
            receipt = CandidateValidationReceipt(
                code_sha256=certificate.code_sha256,
                svg_sha256=certificate.svg_sha256,
                png_sha256=certificate.png_sha256,
                security_profile=certificate.security_profile,
                emitted_diagram_type=self.emitted_diagram_type,
                runtime_diagram_type=self.runtime_diagram_type,
            )
        except (UnicodeEncodeError, ValueError):
            return
        self.validation_receipt = receipt
        self._validation_receipt_seal = _validation_receipt_seal(receipt)

    def has_validated_publication_artifacts(self) -> bool:
        """Return whether current publishable bytes still match trusted validation."""

        try:
            receipt = self.validation_receipt
            seal = self._validation_receipt_seal
            if not (
                self.syntax_valid
                and self.render_valid
                and type(self.mermaid_code) is str
                and bool(self.mermaid_code.strip())
                and type(self.svg) is str
                and bool(self.svg.strip())
                and len(self.svg.encode("utf-8")) <= MAX_RENDER_BYTES
                and type(receipt) is CandidateValidationReceipt
                and type(seal) is str
            ):
                return False
            if (
                receipt.code_sha256 != _artifact_sha256(self.mermaid_code)
                or receipt.svg_sha256 != _artifact_sha256(self.svg)
                or receipt.emitted_diagram_type != self.emitted_diagram_type
                or receipt.runtime_diagram_type != self.runtime_diagram_type
                or _canonical_runtime_diagram_type(receipt.runtime_diagram_type)
                != receipt.emitted_diagram_type
            ):
                return False
            return hmac.compare_digest(seal, _validation_receipt_seal(receipt))
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
            return False

    def has_validated_rendered_preview(self) -> bool:
        """Return whether the optional PNG still matches the trusted render."""

        if not self.has_validated_publication_artifacts():
            return False
        receipt = self.validation_receipt
        try:
            return bool(
                type(receipt) is CandidateValidationReceipt
                and receipt.png_sha256 is not None
                and type(self.png) is bytes
                and png_inspection_error(self.png) is None
                and hmac.compare_digest(
                    receipt.png_sha256,
                    _binary_artifact_sha256(self.png),
                )
            )
        except (TypeError, ValueError):
            return False

    @field_validator("typed_ir", mode="before")
    @classmethod
    def typed_ir_is_canonical(cls, value: Any) -> dict[str, Any] | None:
        return canonical_typed_ir_snapshot(value)

    @field_validator("scores")
    @classmethod
    def scores_are_probabilities(cls, value: dict[str, float]) -> dict[str, float]:
        invalid = {key: score for key, score in value.items() if not 0 <= score <= 1}
        if invalid:
            raise ValueError(f"candidate scores must be between 0 and 1: {invalid}")
        return value

    @field_validator("node_id_mappings")
    @classmethod
    def node_id_mapping_is_injective(
        cls,
        value: list[NodeIdMapping],
    ) -> list[NodeIdMapping]:
        source_ids = [item.source_id for item in value]
        fused_ids = [item.fused_id for item in value]
        if len(source_ids) != len(set(source_ids)) or len(fused_ids) != len(set(fused_ids)):
            raise ValueError("candidate node id mappings must be one-to-one")
        return value

    @model_validator(mode="after")
    def serialization_metadata_is_consistent(self) -> MermaidCandidate:
        if self.typed_ir is not None:
            validate_typed_ir_contract(self.diagram_type, self.typed_ir)
        if self.emitted_diagram_type is None:
            self.emitted_diagram_type = self.diagram_type
        if not self.fallback_chain:
            self.fallback_chain = [self.diagram_type]
            if self.emitted_diagram_type != self.diagram_type:
                self.fallback_chain.append(self.emitted_diagram_type)
        if self.fallback_chain[0] != self.diagram_type:
            raise ValueError("candidate fallback chain must start with diagram_type")
        if self.fallback_chain[-1] != self.emitted_diagram_type:
            raise ValueError("candidate fallback chain must end with emitted_diagram_type")
        if len(self.fallback_chain) != len(set(self.fallback_chain)):
            raise ValueError("candidate fallback chain cannot contain cycles")
        if self.node_id_mappings:
            if self.diagram_type not in {"flowchart", "generic_network"}:
                raise ValueError(
                    "node id mappings are limited to flowchart and generic_network candidates"
                )
            if self.scene_ir is None:
                raise ValueError("node id mappings require a fused Scene IR")
            nodes = self.typed_ir.get("nodes") if isinstance(self.typed_ir, dict) else None
            if not isinstance(nodes, list) or not all(isinstance(node, dict) for node in nodes):
                raise ValueError("node id mappings require typed IR nodes")
            node_ids = [node.get("id") for node in nodes]
            if (
                not all(isinstance(node_id, str) and node_id for node_id in node_ids)
                or len(node_ids) != len(set(node_ids))
                or set(node_ids) != {item.fused_id for item in self.node_id_mappings}
            ):
                raise ValueError("node id mappings must cover the typed IR node set exactly")
            if len({item.source_owner for item in self.node_id_mappings}) != 1:
                raise ValueError("candidate node id mappings must have one source owner")
            if not any(item.match_method == "unique_iou" for item in self.node_id_mappings):
                raise ValueError("candidate node id mappings must record an actual id change")
            scene_ids = {element.id for element in self.scene_ir.elements}
            if not set(node_ids).issubset(scene_ids):
                raise ValueError("node id mappings must reference fused Scene elements")
        return self


class CandidateFailure(BaseModel):
    stage: str
    engine: str
    error_type: str
    message: str

    @field_validator("stage", "engine", "error_type", mode="before")
    @classmethod
    def identifier_is_sink_safe(cls, value: str) -> str:
        return _sink_safe_diagnostic_text(value)[:MAX_ID_CHARS]

    @field_validator("message", mode="before")
    @classmethod
    def message_is_sink_safe(cls, value: str) -> str:
        return _sink_safe_diagnostic_text(value)[:MAX_WARNING_CHARS]


class ReconstructionResult(BaseModel):
    source_id: str
    source_image_name: str
    source_kind: Literal["original", "panel", "merged", "full_page", "page_proposal"] = "original"
    source_block_ids: list[str] = Field(default_factory=list)
    page_ids: list[int] = Field(default_factory=list)
    anchor_block_id: str | None = None
    source_mapping: dict[str, Any] | None = None
    selected: MermaidCandidate | None = None
    alternatives: list[MermaidCandidate] = Field(default_factory=list)
    evidence: list[VisualEvidence] = Field(default_factory=list)
    failures: list[CandidateFailure] = Field(default_factory=list)
    prompt_budget_notices: list[PromptBudgetNotice] = Field(
        default_factory=list, max_length=MAX_OBSERVATION_WARNINGS
    )
    grade: QualityGrade = "U"
    publish: bool = False
    review_required: bool = True
    status: Literal["success", "failed", "skipped", "review_required"] = "review_required"
    sidecar_dir: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    publication_receipt: PublicationAuthorizationReceipt | None = None
    _publication_authorization_seal: str | None = PrivateAttr(default=None)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _build_publication_receipt(
        self,
        policy: PublishPolicy,
        profile: SecurityProfile,
    ) -> PublicationAuthorizationReceipt | None:
        try:
            canonical_evidence_collection_snapshot(self.evidence)
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
            return None
        selected = self.selected
        if not (
            type(self.source_id) is str
            and bool(self.source_id)
            and len(self.source_id) <= MAX_ID_CHARS
            and type(selected) is MermaidCandidate
            and type(selected.candidate_id) is str
            and bool(selected.candidate_id)
            and len(selected.candidate_id) <= MAX_ID_CHARS
            and selected.has_validated_publication_artifacts()
            and type(selected.validation_receipt) is CandidateValidationReceipt
            and selected.validation_receipt.security_profile == profile
            and type(self.publish) is bool
            and type(self.review_required) is bool
            and type(self.status) is str
            and type(self.grade) is str
        ):
            return None
        try:
            return PublicationAuthorizationReceipt(
                source_id=self.source_id,
                selected_candidate_id=selected.candidate_id,
                candidate_validation_sha256=_canonical_model_sha256(selected.validation_receipt),
                candidate_quality_sha256=_candidate_quality_sha256(
                    selected.aggregate_score,
                    self.grade,
                    selected.scores,
                    selected.warnings,
                ),
                publish_policy=policy,
                security_profile=profile,
                publish=self.publish,
                review_required=self.review_required,
                status=self.status,
                grade=self.grade,
                serialization_stability=selected.serialization_stability,
            )
        except (TypeError, UnicodeEncodeError, ValueError):
            return None

    def has_trusted_publication_decision(self) -> bool:
        """Return whether the current result still matches its pipeline decision."""

        try:
            receipt = self.publication_receipt
            seal = self._publication_authorization_seal
            if type(receipt) is not PublicationAuthorizationReceipt or type(seal) is not str:
                return False
            expected = self._build_publication_receipt(
                receipt.publish_policy,
                receipt.security_profile,
            )
            if expected is None or expected != receipt:
                return False
            return hmac.compare_digest(seal, _publication_authorization_seal(receipt))
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
            return False

    def has_authorized_publication(self) -> bool:
        """Return whether automatic Markdown publication remains authorized."""

        receipt = self.publication_receipt
        return bool(
            self.has_trusted_publication_decision()
            and type(receipt) is PublicationAuthorizationReceipt
            and receipt.publish
            and not receipt.review_required
            and receipt.status == "success"
            and receipt.publish_policy
            in {PublishPolicy.STRICT_VALIDATED, PublishPolicy.BEST_EFFORT_VALIDATED}
            and receipt.security_profile != SecurityProfile.TRUSTED_LOCAL
        )

    def authorized_publication_snapshot(self) -> AuthorizedPublicationSnapshot | None:
        """Capture and authorize the exact values a Markdown sink may emit.

        Every mutable field is read into a local exactly once.  The receipt
        digests and process-private HMAC seals are then checked only against
        those locals, so a concurrent mutation either produces a coherent
        previously authorized snapshot or fails closed.
        """

        try:
            canonical_evidence_collection_snapshot(self.evidence)
            selected = self.selected
            source_id = self.source_id
            grade = self.grade
            publish = self.publish
            review_required = self.review_required
            status = self.status
            publication_receipt = self.publication_receipt
            publication_seal = self._publication_authorization_seal
            if type(selected) is not MermaidCandidate:
                return None

            candidate_id = selected.candidate_id
            syntax_valid = selected.syntax_valid
            render_valid = selected.render_valid
            mermaid_code = selected.mermaid_code
            svg = selected.svg
            png = selected.png
            emitted_diagram_type = selected.emitted_diagram_type
            runtime_diagram_type = selected.runtime_diagram_type
            aggregate_score = selected.aggregate_score
            serialization_stability = selected.serialization_stability
            scores = selected.scores
            warnings = selected.warnings
            validation_receipt = selected.validation_receipt
            validation_seal = selected._validation_receipt_seal

            if not (
                type(source_id) is str
                and bool(source_id)
                and len(source_id) <= MAX_ID_CHARS
                and type(candidate_id) is str
                and bool(candidate_id)
                and len(candidate_id) <= MAX_ID_CHARS
                and type(grade) is str
                and type(serialization_stability) is str
                and serialization_stability in {"stable", "extended", "experimental"}
                and type(publish) is bool
                and type(review_required) is bool
                and type(status) is str
                and syntax_valid is True
                and render_valid is True
                and type(mermaid_code) is str
                and bool(mermaid_code.strip())
                and type(svg) is str
                and bool(svg.strip())
                and len(svg.encode("utf-8")) <= MAX_RENDER_BYTES
                and type(validation_receipt) is CandidateValidationReceipt
                and type(validation_seal) is str
                and type(publication_receipt) is PublicationAuthorizationReceipt
                and type(publication_seal) is str
            ):
                return None
            if (
                validation_receipt.code_sha256 != _artifact_sha256(mermaid_code)
                or validation_receipt.svg_sha256 != _artifact_sha256(svg)
                or validation_receipt.emitted_diagram_type != emitted_diagram_type
                or validation_receipt.runtime_diagram_type != runtime_diagram_type
                or not hmac.compare_digest(
                    validation_seal,
                    _validation_receipt_seal(validation_receipt),
                )
            ):
                return None
            if (
                publication_receipt.source_id != source_id
                or publication_receipt.selected_candidate_id != candidate_id
                or publication_receipt.candidate_validation_sha256
                != _canonical_model_sha256(validation_receipt)
                or publication_receipt.candidate_quality_sha256
                != _candidate_quality_sha256(
                    aggregate_score,
                    grade,
                    scores,
                    warnings,
                )
                or publication_receipt.security_profile != validation_receipt.security_profile
                or publication_receipt.publish != publish
                or publication_receipt.review_required != review_required
                or publication_receipt.status != status
                or publication_receipt.grade != grade
                or publication_receipt.serialization_stability != serialization_stability
                or not hmac.compare_digest(
                    publication_seal,
                    _publication_authorization_seal(publication_receipt),
                )
                or not publication_receipt.publish
                or publication_receipt.review_required
                or publication_receipt.status != "success"
                or publication_receipt.publish_policy
                not in {
                    PublishPolicy.STRICT_VALIDATED,
                    PublishPolicy.BEST_EFFORT_VALIDATED,
                }
                or publication_receipt.security_profile == SecurityProfile.TRUSTED_LOCAL
            ):
                return None

            validated_png = None
            if (
                validation_receipt.png_sha256 is not None
                and type(png) is bytes
                and png_inspection_error(png) is None
                and hmac.compare_digest(
                    validation_receipt.png_sha256,
                    _binary_artifact_sha256(png),
                )
            ):
                validated_png = bytes(png)
            preview_omitted = validated_png is None and (
                png is not None or validation_receipt.png_sha256 is not None
            )
            safe_score = (
                aggregate_score
                if type(aggregate_score) is float and math.isfinite(aggregate_score)
                else None
            )
            snapshot = AuthorizedPublicationSnapshot(
                source_id=str(source_id),
                selected_candidate_id=str(candidate_id),
                mermaid_code=str(mermaid_code),
                grade=grade,
                serialization_stability=serialization_stability,
                aggregate_score=safe_score,
                png=validated_png,
                preview_omitted=preview_omitted,
                validation_receipt=validation_receipt,
                publication_receipt=publication_receipt,
            )
            snapshot._authorization_seal = _publication_snapshot_seal(snapshot)
            return snapshot
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
            return None

    @model_validator(mode="after")
    def state_is_consistent(self) -> ReconstructionResult:
        if self.publish and self.selected is None:
            raise ValueError("published results require a selected candidate")
        if self.publish and self.status != "success":
            raise ValueError("published results must have success status")
        if self.status == "failed" and self.publish:
            raise ValueError("failed results cannot be published")
        if self.status == "review_required" and not self.review_required:
            raise ValueError("review_required status must request review")
        return self


class ReviewHistoryEntry(BaseModel):
    operation: str
    target: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    source: Literal["user", "system", "vlm"]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str | None = None
