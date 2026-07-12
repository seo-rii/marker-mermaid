"""Typed intermediate representations and candidate/result models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marker_mermaid.config import QualityGrade

BBox = tuple[float, float, float, float]
Point = tuple[float, float]


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
    score: float | None = None
    source_block_ids: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def score_is_probability(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("evidence score must be between 0 and 1")
        return value

    @field_validator("bbox")
    @classmethod
    def bbox_is_ordered(cls, value: BBox | None) -> BBox | None:
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
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def confidence_is_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


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
    line_style: str | None = None
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def confidence_is_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class SceneGroup(BaseModel):
    id: str
    role: str
    label: str | None = None
    bbox: BBox
    member_ids: list[str] = Field(default_factory=list)


class DiagramSceneIR(BaseModel):
    elements: list[SceneElement] = Field(default_factory=list)
    relations: list[SceneRelation] = Field(default_factory=list)
    groups: list[SceneGroup] = Field(default_factory=list)
    reading_direction: Literal["TB", "BT", "LR", "RL", "radial", "timeline", "unknown"] = "unknown"
    diagram_type_candidates: list[str] = Field(default_factory=list)
    coordinate_space: Literal["pixels", "normalized"] = "pixels"
    canvas_size: tuple[float, float] | None = None

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
    candidates: list[str]
    scores: list[float]
    visual_signals: list[str] = Field(default_factory=list)
    negative_signals: list[str] = Field(default_factory=list)

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


class TypedIRCandidate(BaseModel):
    diagram_type: str
    ir: dict[str, Any]
    confidence: float = 0.5


class DirectMermaidCandidate(BaseModel):
    diagram_type: str
    code: str
    confidence: float = 0.5


class EngineObservation(BaseModel):
    prediction: DiagramTypePrediction
    scene_ir: DiagramSceneIR | None = None
    typed_candidates: list[TypedIRCandidate] = Field(default_factory=list)
    direct_candidates: list[DirectMermaidCandidate] = Field(default_factory=list)
    evidence: list[VisualEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    scene_ir: DiagramSceneIR | None = None
    typed_ir: dict[str, Any] | None = None
    raw_mermaid: str | None = None
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

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("scores")
    @classmethod
    def scores_are_probabilities(cls, value: dict[str, float]) -> dict[str, float]:
        invalid = {key: score for key, score in value.items() if not 0 <= score <= 1}
        if invalid:
            raise ValueError(f"candidate scores must be between 0 and 1: {invalid}")
        return value


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
