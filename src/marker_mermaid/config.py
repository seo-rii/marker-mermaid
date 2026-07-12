"""Configuration and policy defaults from MMX-001 v0.3."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Mode(StrEnum):
    STRICT = "strict"
    EXTENDED = "extended"
    MAXIMAL = "maximal"


class PublishPolicy(StrEnum):
    STRICT_VALIDATED = "strict_validated"
    BEST_EFFORT_VALIDATED = "best_effort_validated"
    REVIEW_REQUIRED = "review_required"
    SIDECAR_ONLY = "sidecar_only"


class CompatibilityProfile(StrEnum):
    PORTABLE_BASIC = "portable-basic"
    PORTABLE_RICH = "portable-rich"
    STYLE_RICH = "style-rich"
    TRUSTED_LOCAL = "trusted-local"


class SecurityProfile(StrEnum):
    STRICT = "strict"
    STYLE_ONLY = "style-only"
    TRUSTED_LOCAL = "trusted-local"
    SANDBOX_EXPERIMENTAL = "sandbox-experimental"


CORE_TYPES = frozenset({"flowchart", "sequence", "state", "class", "er"})
PHASE_ONE_TYPES = frozenset(
    {"flowchart", "architecture", "swimlane", "bpmn", "sequence", "mindmap", "timeline", "gantt"}
)
ALL_TYPES = frozenset(
    {
        "flowchart",
        "swimlane",
        "bpmn",
        "sequence",
        "state",
        "class",
        "er",
        "architecture",
        "c4",
        "requirement",
        "mindmap",
        "timeline",
        "gantt",
        "journey",
        "kanban",
        "gitgraph",
        "pie",
        "xychart",
        "quadrant",
        "sankey",
        "radar",
        "treemap",
        "venn",
        "packet",
        "ishikawa",
        "wardley",
        "cynefin",
        "treeview",
        "block",
        "eventmodeling",
        "zenuml",
        "railroad",
        "generic_network",
        "organization",
        "data_lineage",
        "deployment",
        "component",
        "usecase",
    }
)


class ScoreWeights(BaseModel):
    """Normalized aggregate score weights.

    Missing metrics are excluded and the remaining weights are normalized, so an
    unavailable visual model does not turn a syntactically sound candidate into zero.
    """

    syntax: float = 0.12
    render: float = 0.13
    ocr_recall: float = 0.14
    visual_entailment_precision: float = 0.13
    edge_agreement: float = 0.10
    arrow_agreement: float = 0.08
    layout_similarity: float = 0.08
    type_fitness: float = 0.08
    path_consistency: float = 0.08
    numeric_consistency: float = 0.06

    @model_validator(mode="after")
    def weights_are_valid(self) -> ScoreWeights:
        values = list(self.model_dump().values())
        if any(value < 0 for value in values) or sum(values) <= 0:
            raise ValueError("score weights must be non-negative and not all zero")
        return self


class MermaidConfig(BaseModel):
    """Runtime configuration.

    The Marker adapter accepts both these concise field names and Marker-style
    ``MermaidDiagramProcessor_*`` keys through :meth:`from_marker_config`.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Mode = Mode.EXTENDED
    publish_policy: PublishPolicy = PublishPolicy.BEST_EFFORT_VALIDATED
    enabled_types: set[str] = Field(default_factory=lambda: set(ALL_TYPES))

    enable_page_detector: bool = True
    split_composite_figures: bool = True
    merge_adjacent_fragments: bool = True
    detect_multi_page_diagrams: bool = True

    use_vector_primitives: bool = True
    use_canny_edge_map: bool = True
    use_hough_line_map: bool = True
    use_arrow_overlay: bool = True
    use_ocr_overlay: bool = True
    use_color_group_map: bool = True
    use_tiled_images: bool = True

    enable_typed_ir: bool = True
    enable_generic_scene_ir: bool = True
    enable_direct_mermaid: bool = True
    enable_style_recovery: bool = True
    enable_fusion: bool = True

    candidate_count: int | None = None
    type_candidate_count: int | None = None
    max_repair_iterations: int | None = None

    enable_reference_free_scoring: bool = True
    enable_render_compare: bool = True
    enable_path_scoring: bool = True
    publish_min_score: float = 0.50
    review_below_score: float = 0.70

    write_ir: bool = True
    write_svg: bool = True
    write_png: bool = True
    write_alternatives: bool = True
    write_provenance: bool = True

    include_original_image: Literal[True] = True
    extract_images: Literal[True] = True

    compatibility_profile: CompatibilityProfile = CompatibilityProfile.PORTABLE_RICH
    security_profile: SecurityProfile = SecurityProfile.STRICT
    score_weights: ScoreWeights = Field(default_factory=ScoreWeights)

    runtime_dir: Path | None = None
    sidecar_root: Path | None = None
    render_timeout_seconds: float = 20.0
    max_mermaid_chars: int = 50_000
    max_mermaid_lines: int = 5_000
    max_image_dimension: int = 2048
    max_virtual_source_dimension: int = 32_768
    max_virtual_source_pixels: int = 100_000_000
    tile_size: int = 1280
    tile_overlap: int = 128
    max_views: int = 8

    @field_validator("enabled_types")
    @classmethod
    def enabled_types_are_known(cls, value: set[str]) -> set[str]:
        unknown = value - ALL_TYPES
        if unknown:
            raise ValueError(f"unknown diagram types: {', '.join(sorted(unknown))}")
        if not value:
            raise ValueError("enabled_types cannot be empty")
        return value

    @field_validator("publish_min_score", "review_below_score")
    @classmethod
    def score_is_probability(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("score thresholds must be between 0 and 1")
        return value

    @field_validator("candidate_count")
    @classmethod
    def candidate_budget_is_bounded(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 12:
            raise ValueError("candidate_count must be between 1 and 12")
        return value

    @field_validator("type_candidate_count")
    @classmethod
    def type_budget_is_bounded(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 3:
            raise ValueError("type_candidate_count must be between 1 and 3")
        return value

    @field_validator("max_repair_iterations")
    @classmethod
    def repair_budget_is_bounded(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= 10:
            raise ValueError("max_repair_iterations must be between 0 and 10")
        return value

    @field_validator(
        "max_mermaid_chars",
        "max_mermaid_lines",
        "max_image_dimension",
        "max_virtual_source_dimension",
        "max_virtual_source_pixels",
        "max_views",
    )
    @classmethod
    def resource_limit_is_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("resource limits must be positive")
        return value

    @model_validator(mode="after")
    def apply_mode_defaults_and_policy_invariants(self) -> MermaidConfig:
        defaults = {
            Mode.STRICT: (1, 1, 1),
            Mode.EXTENDED: (3, 2, 3),
            Mode.MAXIMAL: (6, 3, 10),
        }
        candidates, types, repairs = defaults[self.mode]
        if self.candidate_count is None:
            self.candidate_count = candidates
        if self.type_candidate_count is None:
            self.type_candidate_count = types
        if self.max_repair_iterations is None:
            self.max_repair_iterations = repairs
        if self.mode == Mode.STRICT:
            self.enable_direct_mermaid = False
            self.enable_style_recovery = False
            self.enabled_types &= CORE_TYPES
            if not self.enabled_types:
                raise ValueError("strict mode requires at least one core diagram type")
        if self.security_profile == SecurityProfile.TRUSTED_LOCAL and self.publish_policy not in {
            PublishPolicy.REVIEW_REQUIRED,
            PublishPolicy.SIDECAR_ONLY,
        }:
            raise ValueError("trusted-local output cannot be published to automatic Markdown")
        if self.review_below_score < self.publish_min_score:
            raise ValueError("review_below_score cannot be below publish_min_score")
        return self

    @classmethod
    def from_marker_config(cls, config: dict[str, Any] | BaseModel | None) -> MermaidConfig:
        if config is None:
            return cls()
        raw = config.model_dump() if isinstance(config, BaseModel) else dict(config)
        prefix = "MermaidDiagramProcessor_"
        known = set(cls.model_fields)
        selected = {key: value for key, value in raw.items() if key in known}
        selected.update(
            {
                key.removeprefix(prefix): value
                for key, value in raw.items()
                if key.startswith(prefix)
            }
        )
        return cls.model_validate(selected)


QualityGrade = Literal["A", "B", "C", "D", "U"]


def quality_grade(score: float | None) -> QualityGrade:
    if score is None:
        return "U"
    if score >= 0.85:
        return "A"
    if score >= 0.70:
        return "B"
    if score >= 0.50:
        return "C"
    return "D"
