"""Typed intermediate representations and candidate/result models."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from marker_mermaid.config import PublishPolicy, QualityGrade, SecurityProfile
from marker_mermaid.mapping_validation import bbox_iou
from marker_mermaid.render_artifacts import MAX_RENDER_BYTES, png_inspection_error
from marker_mermaid.typed_contracts import validate_typed_ir_contract

BBox = tuple[float, float, float, float]
Point = tuple[float, float]
MAX_OBSERVATION_CANDIDATES = 64
MAX_OBSERVATION_EVIDENCE = 20_000
MAX_OBSERVATION_WARNINGS = 256
MAX_IR_DEPTH = 64
MAX_IR_ITEMS = 100_000
MAX_IR_TEXT_CHARS = 50_000
MAX_ID_CHARS = 256
MAX_TEXT_CHARS = 50_000
MAX_WARNING_CHARS = 4_096
MAX_EVIDENCE_REFS = 256
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
        "class": "class",
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
    _require_utf8_text(value, field)
    if value is not None and len(value) > limit:
        raise ValueError(f"{field} exceeds the text size limit")
    return value


def _bounded_references(
    values: list[str], field: str, *, limit: int = MAX_EVIDENCE_REFS
) -> list[str]:
    if len(values) > limit:
        raise ValueError(f"{field} exceeds the reference count limit")
    for value in values:
        _require_utf8_text(value, field)
    if any(not value or len(value) > MAX_ID_CHARS for value in values):
        raise ValueError(f"{field} contains an invalid bounded identifier")
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
        _require_utf8_text(value, "evidence id")
        if not value or len(value) > MAX_ID_CHARS:
            raise ValueError("evidence id must be non-empty and bounded")
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

    @field_validator("diagram_type")
    @classmethod
    def diagram_type_is_utf8(cls, value: str) -> str:
        return _require_utf8_text(value, "typed IR diagram type")  # type: ignore[return-value]

    @field_validator("ir")
    @classmethod
    def ir_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        pending: list[tuple[Any, int]] = [(value, 0)]
        item_count = 0
        while pending:
            item, depth = pending.pop()
            item_count += 1
            if item_count > MAX_IR_ITEMS:
                raise ValueError("typed IR exceeds the item budget")
            if depth > MAX_IR_DEPTH:
                raise ValueError("typed IR exceeds the nesting depth budget")
            if isinstance(item, str):
                _require_utf8_text(item, "typed IR text")
                if len(item) > MAX_IR_TEXT_CHARS:
                    raise ValueError("typed IR text exceeds the field size budget")
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError("typed IR object keys must be strings")
                pending.extend((key, depth + 1) for key in item)
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list | tuple):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("typed IR numbers must be finite")
            elif item is not None and not isinstance(item, str | int | float | bool):
                raise ValueError("typed IR values must be JSON-compatible")
        return value

    @model_validator(mode="after")
    def matches_diagram_contract(self) -> TypedIRCandidate:
        validate_typed_ir_contract(self.diagram_type, self.ir)
        return self

    def canonical_key(self) -> str:
        # Repair workflows intentionally mutate typed IR in memory. Revalidate
        # the current payload here so post-construction mutation cannot bypass
        # the JSON/finite-number contract or destabilize fusion keys.
        validated = type(self).model_validate(self.model_dump(mode="python"))
        payload = json.dumps(
            validated.model_dump(mode="json")["ir"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return f"{self.diagram_type}\0{payload}"


class DirectMermaidCandidate(BaseModel):
    diagram_type: str = Field(max_length=MAX_ID_CHARS)
    code: str = Field(max_length=50_000)
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("diagram_type", "code")
    @classmethod
    def text_is_utf8(cls, value: str) -> str:
        return _require_utf8_text(value, "direct Mermaid candidate")  # type: ignore[return-value]


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
    ) -> None:
        self._fusion_node_id_mappings = {
            key: [item.model_copy(deep=True) for item in values]
            for key, values in node_id_mappings.items()
        }
        self._fusion_conflicted_connector_pairs = set(conflicted_connector_pairs)

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
    diagram_type: str
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

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def _seal_node_id_mappings(self) -> None:
        """Mark current mappings as produced by the trusted reconstruction pipeline."""

        self._node_id_mapping_seal = (
            _node_id_mapping_seal(self.node_id_mappings) if self.node_id_mappings else None
        )

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
