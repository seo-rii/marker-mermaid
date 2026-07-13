"""Typed intermediate representations and candidate/result models."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from marker_mermaid.config import QualityGrade
from marker_mermaid.mapping_validation import bbox_iou
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
MAX_SCENE_ELEMENTS = 5_000
MAX_SCENE_RELATIONS = 10_000
MAX_SCENE_GROUPS = 1_000
MAX_POLYGON_POINTS = 4_096
MAX_POLYLINE_POINTS = 10_000


def _bounded_text(value: str | None, field: str, limit: int = MAX_TEXT_CHARS) -> str | None:
    if value is not None and len(value) > limit:
        raise ValueError(f"{field} exceeds the text size limit")
    return value


def _bounded_references(
    values: list[str], field: str, *, limit: int = MAX_EVIDENCE_REFS
) -> list[str]:
    if len(values) > limit:
        raise ValueError(f"{field} exceeds the reference count limit")
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
        if not value or len(value) > MAX_ID_CHARS:
            raise ValueError("scene relation id/type must be non-empty and bounded")
        return value

    @field_validator("source_id", "target_id")
    @classmethod
    def endpoint_ids_are_bounded(cls, value: str | None) -> str | None:
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
            if isinstance(item, str) and len(item) > MAX_IR_TEXT_CHARS:
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
        if any(len(item) > MAX_WARNING_CHARS for item in value):
            raise ValueError("engine warning exceeds the text size limit")
        return value

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
    syntax_valid: bool = False
    render_valid: bool = False
    scores: dict[str, float] = Field(default_factory=dict)
    aggregate_score: float | None = None
    warnings: list[str] = Field(default_factory=list)
    repair_history: list[RepairEvent] = Field(default_factory=list)
    _node_id_mapping_seal: str | None = PrivateAttr(default=None)

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

    model_config = ConfigDict(arbitrary_types_allowed=True)

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
