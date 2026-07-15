"""Budgeted, failure-isolated reconstruction orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter
from dataclasses import dataclass
from itertools import chain
from typing import Literal

from PIL import Image, ImageChops, ImageFilter, ImageOps

from marker_mermaid.accessibility import (
    EXPERIMENTAL_NOTICE,
    accessibility_limitation_warning,
    augment_accessibility_directives,
    enrich_accessibility_ir,
    resolve_accessibility,
    supports_accessibility_directives,
)
from marker_mermaid.ast_repair import DeterministicMermaidRepair
from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import (
    MAX_VLM_TOTAL_VIEW_PIXELS,
    MAX_VLM_VIEW_DIMENSION,
    MAX_VLM_VIEW_PIXELS,
    MermaidConfig,
    Mode,
    PublishPolicy,
    ScoreWeights,
    SecurityProfile,
)
from marker_mermaid.engines import MAX_VLM_EVIDENCE_INPUT_CHARS, StructuredVLMRequestError
from marker_mermaid.flowchart_structure import (
    ambiguous_portable_ids,
    unique_portable_id_aliases,
)
from marker_mermaid.fusion import FusionEngine, FusionInput
from marker_mermaid.geometry import GeometryEngine
from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    MAX_ID_CHARS,
    MAX_OBSERVATION_CANDIDATES,
    MAX_OBSERVATION_EVIDENCE,
    MAX_OBSERVATION_TYPED_IR_JSON_BYTES,
    MAX_OBSERVATION_WARNINGS,
    MAX_TEXT_CHARS,
    MAX_WARNING_CHARS,
    CandidateFailure,
    DiagramSceneIR,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    EvidenceBudgetUsage,
    MermaidCandidate,
    NodeIdMapping,
    PromptBudgetNotice,
    ReconstructionResult,
    RepairEvent,
    SceneElement,
    TypedIRCandidate,
    VisualEvidence,
    _canonical_typed_candidate_fields,
    _publication_authorization_seal,
    _sink_safe_diagnostic_text,
    canonical_evidence_collection_snapshot,
    canonical_source_mapping_snapshot,
    canonical_typed_ir_snapshot,
)
from marker_mermaid.models import (
    _canonical_runtime_diagram_type as _canonical_runtime_type,
)
from marker_mermaid.protocols import (
    CandidateEngine,
    RepairEngine,
    RepairProposal,
    RuntimeResult,
    SourceContext,
)
from marker_mermaid.quality import (
    arrow_agreement,
    edge_topology_agreement,
    injective_node_provenance_counts,
    path_consistency,
    relative_layout_similarity,
)
from marker_mermaid.scoring import (
    aggregate_scores,
    bounded_ocr_token_multiset,
    decide_publication,
    numeric_consistency,
    numeric_token_multiset,
    ocr_recall,
    ocr_token_multiset,
    semantic_score,
)
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serialization import SerializationContractError, SerializationResult
from marker_mermaid.serializers import (
    SerializationError,
    scene_to_flowchart,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
)
from marker_mermaid.serializers_charts_core import plan_pie_records
from marker_mermaid.serializers_special import plan_packet_fields
from marker_mermaid.style_recovery import (
    TrustedEdgeStyleEvidence,
    recover_flowchart_styles,
)
from marker_mermaid.validation import CandidateValidator
from marker_mermaid.vector import VectorPrimitiveEngine
from marker_mermaid.views import build_visual_priors


@dataclass(slots=True)
class _Draft:
    method: str
    engine_name: str
    diagram_type: str
    code: str
    observation: EngineObservation
    emitted_diagram_type: str | None = None
    fallback_chain: list[str] | None = None
    serialization_stability: str = "stable"
    typed_ir: dict | None = None
    node_id_mappings: list[NodeIdMapping] | None = None
    raw_mermaid: str | None = None
    warnings: list[str] | None = None
    evidence_authority_ids: frozenset[str] | None = None


@dataclass(slots=True)
class _CandidateEvaluation:
    scores: dict[str, float]
    aggregate_score: float | None
    warnings: list[str]
    generated_scene_ir: DiagramSceneIR | None


@dataclass(frozen=True, slots=True)
class _ReferenceTexts:
    numeric_tokens: Counter[str]
    ocr_tokens: Counter[str] | None
    warning: str | None = None


_MAX_OCR_REFERENCE_TEXTS = 50_000
_MAX_OCR_REFERENCE_CHARS = 1_000_000
_MAX_OCR_REFERENCE_TOKENS = 100_000
_MAX_PACKET_ASSOCIATION_REFERENCES = MAX_OBSERVATION_EVIDENCE
_MAX_PACKET_FIELD_OVERLAP_COMPARISONS = 100_000
_MAX_PIE_ASSOCIATION_REFERENCES = MAX_OBSERVATION_EVIDENCE
_MAX_PIE_SLICE_OVERLAP_COMPARISONS = 100_000
_PIL_IMAGING_CORE_TYPE = type(Image.new("RGB", (1, 1)).im)
_PIL_IMAGE_DICT_DESCRIPTOR = Image.Image.__dict__["__dict__"]


def _reference_text_sets(ocr_texts: list[str], evidence: list[VisualEvidence]) -> _ReferenceTexts:
    """Return occurrence-preserving numeric and OCR source token multisets."""

    budget_warning = (
        "OCR/vector reference text exceeds the semantic scoring budget; review is required"
    )
    if len(ocr_texts) > _MAX_OCR_REFERENCE_TEXTS:
        return _ReferenceTexts(Counter(), None, budget_warning)
    reference_chars = sum(len(text) for text in ocr_texts)
    if reference_chars > _MAX_OCR_REFERENCE_CHARS:
        return _ReferenceTexts(Counter(), None, budget_warning)
    evidence_texts: list[str] = []
    seen_observations: set[tuple[str, object]] = set()
    for item in evidence:
        if not item.text or item.kind not in {"ocr_token", "vector_text"}:
            continue
        normalized = unicodedata.normalize("NFKC", item.text).casefold().strip()
        location: object = tuple(item.bbox) if item.bbox is not None else None
        key = (normalized, location)
        if not normalized or key in seen_observations:
            continue
        seen_observations.add(key)
        reference_chars += len(item.text)
        if (
            len(ocr_texts) + len(evidence_texts) + 1 > _MAX_OCR_REFERENCE_TEXTS
            or reference_chars > _MAX_OCR_REFERENCE_CHARS
        ):
            return _ReferenceTexts(Counter(), None, budget_warning)
        evidence_texts.append(item.text)
    context_numbers = numeric_token_multiset(ocr_texts)
    evidence_numbers = numeric_token_multiset(evidence_texts)
    numeric_tokens = context_numbers | evidence_numbers
    token_budget = {
        "max_texts": _MAX_OCR_REFERENCE_TEXTS,
        "max_chars": _MAX_OCR_REFERENCE_CHARS,
        "max_tokens": _MAX_OCR_REFERENCE_TOKENS,
    }
    context_tokens = bounded_ocr_token_multiset(ocr_texts, **token_budget)
    evidence_tokens = bounded_ocr_token_multiset(evidence_texts, **token_budget)
    if context_tokens is None or evidence_tokens is None:
        return _ReferenceTexts(
            Counter(),
            None,
            "OCR/vector reference tokens exceed the semantic scoring budget; review is required",
        )
    merged_tokens: Counter[str] = context_tokens | evidence_tokens
    if merged_tokens.total() > _MAX_OCR_REFERENCE_TOKENS:
        return _ReferenceTexts(
            Counter(),
            None,
            "OCR/vector reference tokens exceed the semantic scoring budget; review is required",
        )
    return _ReferenceTexts(numeric_tokens, merged_tokens)


def _evaluation_gate_diagram_type(
    *,
    method: str,
    semantic_type: str,
    emitted_type: str | None,
    runtime_type: str | None,
) -> str:
    """Use validated direct grammar for gates without changing typed semantic adapters."""

    if method == "direct_mermaid":
        return runtime_type or emitted_type or semantic_type
    return semantic_type


def _canonical_rgb_image_snapshot(
    image: Image.Image,
    *,
    max_dimension: int = MAX_VLM_VIEW_DIMENSION,
    max_pixels: int = MAX_VLM_VIEW_PIXELS,
) -> Image.Image:
    """Copy bounded pixels as RGB without invoking caller-owned Pillow hooks."""

    if not isinstance(image, Image.Image):
        raise ValueError("repair images must be Pillow images")
    try:
        image_state = _PIL_IMAGE_DICT_DESCRIPTOR.__get__(image, Image.Image)
    except Exception as exc:
        raise ValueError("repair image has no readable Pillow state") from exc
    if type(image_state) is not dict:
        raise ValueError("repair image has no canonical Pillow state")
    declared_mode = image_state.get("_mode")
    declared_size = image_state.get("_size")
    source_core = image_state.get("im")
    if (
        type(declared_mode) is not str
        or not declared_mode
        or type(declared_size) is not tuple
        or len(declared_size) != 2
        or type(declared_size[0]) is not int
        or type(declared_size[1]) is not int
    ):
        raise ValueError("repair image must have canonical RGB dimensions")
    width, height = declared_size
    if (
        width <= 0
        or height <= 0
        or width > max_dimension
        or height > max_dimension
        or width * height > max_pixels
        or type(source_core) is not _PIL_IMAGING_CORE_TYPE
        or source_core.mode != declared_mode
        or source_core.size != declared_size
    ):
        raise ValueError("repair image exceeds the RGB pixel boundary")
    try:
        snapshot_core = _PIL_IMAGING_CORE_TYPE.copy(source_core)
    except Exception as exc:
        raise ValueError("repair image pixels could not be snapshotted") from exc
    if (
        type(snapshot_core) is not _PIL_IMAGING_CORE_TYPE
        or snapshot_core is source_core
        or snapshot_core.mode != declared_mode
        or snapshot_core.size != declared_size
    ):
        raise ValueError("repair image snapshot changed its RGB boundary")
    snapshot = Image.Image._new(Image.Image(), snapshot_core)
    if type(snapshot) is not Image.Image:  # pragma: no cover - Pillow invariant
        raise ValueError("repair image snapshot must be a plain Pillow image")
    Image.Image.load(snapshot)
    if declared_mode != "RGB":
        try:
            converted = Image.Image.convert(snapshot, "RGB")
        except Exception as exc:
            raise ValueError("repair image could not be converted to RGB") from exc
        return _canonical_rgb_image_snapshot(
            converted,
            max_dimension=max_dimension,
            max_pixels=max_pixels,
        )
    return snapshot


def _edge_iou(source: Image.Image, rendered: bytes | None) -> float | None:
    if rendered is None:
        return None
    from io import BytesIO

    try:
        generated = Image.open(BytesIO(rendered)).convert("L")
    except OSError:
        return None
    source_edges = ImageOps.grayscale(source).filter(ImageFilter.FIND_EDGES)
    generated_edges = generated.filter(ImageFilter.FIND_EDGES)
    generated_edges = generated_edges.resize(source_edges.size)
    source_mask = source_edges.point(lambda value: 255 if value > 80 else 0).convert("1")
    generated_mask = generated_edges.point(lambda value: 255 if value > 80 else 0).convert("1")
    intersection = ImageChops.logical_and(source_mask, generated_mask).histogram()[1]
    union = ImageChops.logical_or(source_mask, generated_mask).histogram()[1]
    return intersection / union if union else None


_PROVENANCE_GATED_TYPES = frozenset(
    {
        "architecture",
        "block",
        "bpmn",
        "c4",
        "class",
        "component",
        "cynefin",
        "data_lineage",
        "deployment",
        "er",
        "eventmodeling",
        "flowchart",
        "generic_network",
        "gitgraph",
        "ishikawa",
        "journey",
        "kanban",
        "mindmap",
        "organization",
        "packet",
        "pie",
        "radar",
        "railroad",
        "requirement",
        "sankey",
        "sequence",
        "state",
        "swimlane",
        "timeline",
        "treeview",
        "treemap",
        "usecase",
        "venn",
        "wardley",
        "zenuml",
    }
)

_NUMERIC_TYPES = frozenset(
    {
        "gantt",
        "journey",
        "packet",
        "pie",
        "quadrant",
        "radar",
        "sankey",
        "treemap",
        "venn",
        "xychart",
    }
)

_CYNEFIN_TEMPLATE_REVIEW_WARNING = (
    "Cynefin native runtime adds fixed template content without source provenance; "
    "review is required"
)
_PACKET_NUMERIC_ASSOCIATION_UNAVAILABLE_WARNING = (
    "Packet field/range association lacks candidate-authorized spatial OCR/vector evidence; "
    "review is required"
)
_PACKET_NUMERIC_ASSOCIATION_MISMATCH_WARNING = (
    "Packet field/range association conflicts with source numeric evidence; review is required"
)
_PIE_NUMERIC_ASSOCIATION_UNAVAILABLE_WARNING = (
    "Pie slice/value association lacks candidate-authorized spatial OCR/vector evidence; "
    "review is required"
)
_PIE_NUMERIC_ASSOCIATION_MISMATCH_WARNING = (
    "Pie slice/value association conflicts with source numeric evidence; review is required"
)
_PIE_TITLE_ATTRIBUTION_UNAVAILABLE_WARNING = (
    "Pie title/accTitle lacks independent candidate-authorized spatial OCR/vector or user-edit "
    "evidence; review is required"
)
_PIE_DESCRIPTION_ATTRIBUTION_UNAVAILABLE_WARNING = (
    "Pie explicit accessibility description lacks independent candidate-authorized spatial "
    "OCR/vector or user-edit evidence; "
    "review is required"
)

_EVALUATION_WARNING_TEXT = frozenset(
    {
        "generated-node attribution is unavailable; review is required",
        "generated-node provenance gate requires at least 80% attribution",
        "more than 20% of generated nodes lack provenance",
        _CYNEFIN_TEMPLATE_REVIEW_WARNING,
        _PACKET_NUMERIC_ASSOCIATION_UNAVAILABLE_WARNING,
        _PACKET_NUMERIC_ASSOCIATION_MISMATCH_WARNING,
        _PIE_NUMERIC_ASSOCIATION_UNAVAILABLE_WARNING,
        _PIE_NUMERIC_ASSOCIATION_MISMATCH_WARNING,
        _PIE_TITLE_ATTRIBUTION_UNAVAILABLE_WARNING,
        _PIE_DESCRIPTION_ATTRIBUTION_UNAVAILABLE_WARNING,
        "numeric consistency is below the automatic publication threshold",
        "numeric diagram lacks OCR/vector numeric evidence and cannot auto-publish",
        "unlabeled scene-only candidates require OCR/VLM fusion before publishing",
    }
)


def _without_evaluation_warnings(warnings: list[str]) -> list[str]:
    return [warning for warning in warnings if warning not in _EVALUATION_WARNING_TEXT]


def _normalized_label(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _generated_node_provenance_score(
    generated_scene: DiagramSceneIR | None,
    source_scene: DiagramSceneIR | None,
    evidence: list[VisualEvidence],
) -> float | None:
    """Measure attribution on emitted nodes, not merely on source observations.

    Typed serializers do not always copy evidence IDs into their emitted IR.  In that
    case a generated node may inherit attribution from a source node with the same ID,
    one collision-free portable emitted-ID alias, or one unique normalized label match.
    Ambiguous ID aliases and label matches never count.
    """

    if generated_scene is None or not generated_scene.elements:
        return None
    generated_elements = generated_scene.elements
    if (
        generated_scene.diagram_type_candidates == ["radar"]
        and generated_scene.reading_direction == "radial"
    ):
        # Native Radar renders data points as derived curve geometry rather than
        # independently attributable nodes.  Axis and series evidence supports
        # the emitted chart components while numeric consistency separately
        # verifies point values.  The exact Flowchart fallback still evaluates
        # every emitted cell because those data points become actual nodes.
        generated_elements = [
            element for element in generated_elements if element.role != "data_point"
        ]
    if not generated_elements:
        return None
    known = {item.id for item in evidence}
    source_by_id = {
        element.id: element for element in (source_scene.elements if source_scene else [])
    }
    ambiguous_source_ids, ambiguous_emitted_ids = ambiguous_portable_ids(list(source_by_id))
    safe_source_by_id = {
        source_id: element
        for source_id, element in source_by_id.items()
        if source_id not in ambiguous_source_ids and source_id not in ambiguous_emitted_ids
    }
    source_by_portable_id = {
        emitted_id: source_by_id[source_id]
        for emitted_id, source_id in unique_portable_id_aliases(list(source_by_id)).items()
    }
    source_by_label: dict[str, list] = {}
    for element in source_by_id.values():
        label = _normalized_label(element.text)
        if label:
            source_by_label.setdefault(label, []).append(element)

    effective_evidence_ids: list[set[str]] = []
    for element in generated_elements:
        direct_evidence_ids = known.intersection(element.evidence_ids)
        if direct_evidence_ids:
            effective_evidence_ids.append(direct_evidence_ids)
            continue
        source_element = safe_source_by_id.get(element.id) or source_by_portable_id.get(element.id)
        if source_element is None:
            matches = source_by_label.get(_normalized_label(element.text), [])
            source_element = matches[0] if len(matches) == 1 else None
        effective_evidence_ids.append(
            known.intersection(source_element.evidence_ids) if source_element is not None else set()
        )
    supported, total = injective_node_provenance_counts(effective_evidence_ids, evidence)
    return supported / total


def _canonical_publication_source(code: str) -> str:
    """Keep validated Mermaid fence payloads byte-identical at publication."""

    return code if code.endswith("\n") else code + "\n"


def certify_publication_result(
    result: ReconstructionResult,
    config: MermaidConfig,
) -> bool:
    """Seal only a result that exactly matches a freshly computed policy decision."""

    result.publication_receipt = None
    result._publication_authorization_seal = None
    if type(result) is not ReconstructionResult or type(config) is not MermaidConfig:
        return False
    try:
        evidence_snapshot = canonical_evidence_collection_snapshot(result.evidence)
    except (AttributeError, TypeError, UnicodeEncodeError, ValueError):
        return False
    selected = result.selected
    weights = config.score_weights
    if not (
        type(selected) is MermaidCandidate
        and type(config.publish_policy) is PublishPolicy
        and type(config.security_profile) is SecurityProfile
        and type(config.publish_min_score) is float
        and type(config.review_below_score) is float
        and type(weights) is ScoreWeights
    ):
        return False
    try:
        weight_values = weights.model_dump(mode="python")
        if any(type(value) is not float for value in weight_values.values()):
            return False
        trusted_config = MermaidConfig(
            publish_policy=config.publish_policy,
            security_profile=config.security_profile,
            publish_min_score=config.publish_min_score,
            review_below_score=config.review_below_score,
            score_weights=ScoreWeights.model_validate(weight_values),
        )
    except (TypeError, ValueError):
        return False
    included_fields = {
        "source_id",
        "selected",
        "grade",
        "publish",
        "review_required",
        "status",
    }
    try:
        before_projection = result.model_dump(
            mode="python",
            include=included_fields,
        )
        before_validation_seal = selected._validation_receipt_seal
        shallow_snapshot = ReconstructionResult.model_copy(result, deep=False)
        shallow_snapshot.evidence = list(evidence_snapshot.evidence)
        snapshot = ReconstructionResult.model_copy(shallow_snapshot, deep=True)
        after_projection = result.model_dump(
            mode="python",
            include=included_fields,
        )
        snapshot_projection = snapshot.model_dump(
            mode="python",
            include=included_fields,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    snapshot_selected = snapshot.selected
    if not (
        before_projection == after_projection == snapshot_projection
        and type(before_validation_seal) is str
        and type(snapshot_selected) is MermaidCandidate
        and snapshot_selected._validation_receipt_seal == before_validation_seal
        and snapshot_selected.has_validated_publication_artifacts()
    ):
        return False
    decision = decide_publication(snapshot_selected, trusted_config)
    if decision.publish or not decision.review_required:
        expected_status = "success"
    else:
        expected_status = "review_required"
    if (
        snapshot.grade != decision.grade
        or snapshot.publish is not decision.publish
        or snapshot.review_required is not decision.review_required
        or snapshot.status != expected_status
    ):
        return False
    receipt = snapshot._build_publication_receipt(
        trusted_config.publish_policy,
        trusted_config.security_profile,
    )
    if receipt is None:
        return False
    try:
        final_projection = result.model_dump(
            mode="python",
            include=included_fields,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    if (
        final_projection != before_projection
        or result.selected is not selected
        or selected._validation_receipt_seal != before_validation_seal
    ):
        return False
    result.publication_receipt = receipt
    result._publication_authorization_seal = _publication_authorization_seal(receipt)
    if result.has_trusted_publication_decision():
        return True
    result.publication_receipt = None
    result._publication_authorization_seal = None
    return False


def _scene_accessibility_ir(
    scene: DiagramSceneIR | None,
    evidence: list[VisualEvidence],
) -> dict:
    if scene is not None and scene.elements:
        return {
            "nodes": [
                {"id": element.id, "label": element.text or element.id}
                for element in scene.elements
            ],
            "edges": [
                {"source": relation.source_id, "target": relation.target_id}
                for relation in scene.relations
                if relation.source_id is not None and relation.target_id is not None
            ],
        }
    labels = list(
        dict.fromkeys(
            item.text.strip()
            for item in evidence
            if item.text and item.kind in {"ocr_token", "vector_text"} and item.text.strip()
        )
    )
    return {
        "nodes": [
            {"id": f"evidence_{index}", "label": label} for index, label in enumerate(labels[:5], 1)
        ]
    }


class ReconstructionPipeline:
    """Generate, validate, score, select, and optionally repair Mermaid candidates."""

    def __init__(
        self,
        config: MermaidConfig,
        engines: list[CandidateEngine],
        validator: CandidateValidator,
        repair_engine: RepairEngine | None = None,
    ):
        self.config = config
        self.engines = engines
        self.validator = validator
        self.repair_engine = repair_engine
        self.source_repair = DeterministicMermaidRepair(
            event_budget=8,
            max_source_chars=config.max_mermaid_chars,
            max_lines=config.max_mermaid_lines,
            security_scanner=MermaidSecurityScanner(config.security_profile),
        )

    def reconstruct(
        self,
        source_id: str,
        source_image_name: str,
        image: Image.Image,
        *,
        source_block_ids: list[str] | None = None,
        source_kind: Literal["original", "panel", "merged", "full_page", "page_proposal"] = (
            "original"
        ),
        page_ids: list[int] | None = None,
        anchor_block_id: str | None = None,
        source_mapping: dict | None = None,
        evidence: list[VisualEvidence] | None = None,
        ocr_texts: list[str] | None = None,
        source_block: object | None = None,
        source_blocks: list[object] | None = None,
        vector_sources: list[object] | None = None,
    ) -> ReconstructionResult:
        failures: list[CandidateFailure] = []
        prompt_budget_notices: list[PromptBudgetNotice] = []
        boundary_warnings: list[str] = []

        try:
            resolved_source_mapping = canonical_source_mapping_snapshot(source_mapping)
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            resolved_source_mapping = None
            failures.append(
                CandidateFailure(
                    stage="source_context",
                    engine="source_context",
                    error_type=type(exc).__name__,
                    message=f"invalid source_mapping was isolated: {exc}",
                )
            )
            boundary_warnings.append(
                "invalid or oversized source mapping was isolated before reconstruction"
            )

        try:
            source_image = _canonical_rgb_image_snapshot(
                image,
                max_dimension=self.config.max_virtual_source_dimension,
                max_pixels=self.config.max_virtual_source_pixels,
            )
        except Exception as exc:
            source_image = Image.new("RGB", (1, 1), "white")
            failures.append(
                CandidateFailure(
                    stage="source_context",
                    engine="source_context",
                    error_type=type(exc).__name__,
                    message=f"invalid source image was isolated: {exc}",
                )
            )
            boundary_warnings.append(
                "invalid or oversized source image was isolated before reconstruction"
            )

        resolved_source_block_ids: list[str]
        if source_block_ids is None:
            resolved_source_block_ids = [source_id]
        elif type(source_block_ids) is not list:
            resolved_source_block_ids = [source_id]
            failures.append(
                CandidateFailure(
                    stage="source_context",
                    engine="source_context",
                    error_type="CollectionTypeError",
                    message=(
                        "source_block_ids must be an exact plain list; the collection was isolated"
                    ),
                )
            )
            boundary_warnings.append(
                "non-canonical source_block_ids were isolated before reconstruction"
            )
        else:
            source_block_id_snapshot = source_block_ids[: MAX_EVIDENCE_REFS + 1]
            try:
                if len(source_block_id_snapshot) > MAX_EVIDENCE_REFS:
                    raise ValueError(f"source_block_ids exceeds the {MAX_EVIDENCE_REFS}-item limit")
                for block_id in source_block_id_snapshot:
                    if type(block_id) is not str or not block_id or len(block_id) > MAX_ID_CHARS:
                        raise ValueError("source_block_ids contains an invalid bounded identifier")
                    block_id.encode("utf-8")
            except (UnicodeEncodeError, ValueError) as exc:
                resolved_source_block_ids = [source_id]
                failures.append(
                    CandidateFailure(
                        stage="source_context",
                        engine="source_context",
                        error_type=type(exc).__name__,
                        message=f"invalid source_block_ids collection was isolated: {exc}",
                    )
                )
                boundary_warnings.append(
                    "invalid source_block_ids were isolated before reconstruction"
                )
            else:
                resolved_source_block_ids = source_block_id_snapshot or [source_id]

        resolved_page_ids: list[int] = []
        if page_ids is not None:
            if type(page_ids) is not list:
                failures.append(
                    CandidateFailure(
                        stage="source_context",
                        engine="source_context",
                        error_type="CollectionTypeError",
                        message="page_ids must be an exact plain list; the collection was isolated",
                    )
                )
                boundary_warnings.append(
                    "non-canonical page_ids were isolated before reconstruction"
                )
            else:
                page_id_snapshot = page_ids[: MAX_EVIDENCE_REFS + 1]
                if len(page_id_snapshot) > MAX_EVIDENCE_REFS or any(
                    type(page_id) is not int or page_id < 0 for page_id in page_id_snapshot
                ):
                    failures.append(
                        CandidateFailure(
                            stage="source_context",
                            engine="source_context",
                            error_type="CollectionValueError",
                            message=(
                                "page_ids must contain at most "
                                f"{MAX_EVIDENCE_REFS} non-negative plain integers; "
                                "the collection was isolated"
                            ),
                        )
                    )
                    boundary_warnings.append("invalid page_ids were isolated before reconstruction")
                else:
                    resolved_page_ids = page_id_snapshot

        resolved_ocr_texts: list[str] = []
        if ocr_texts is not None:
            if type(ocr_texts) is not list:
                failures.append(
                    CandidateFailure(
                        stage="source_context",
                        engine="source_context",
                        error_type="CollectionTypeError",
                        message=(
                            "ocr_texts must be an exact plain list; the collection was isolated"
                        ),
                    )
                )
                boundary_warnings.append(
                    "non-canonical OCR text input was isolated before reconstruction"
                )
            else:
                ocr_snapshot = ocr_texts[: _MAX_OCR_REFERENCE_TEXTS + 1]
                ocr_chars = 0
                try:
                    if len(ocr_snapshot) > _MAX_OCR_REFERENCE_TEXTS:
                        raise ValueError(
                            f"ocr_texts exceeds the {_MAX_OCR_REFERENCE_TEXTS}-item limit"
                        )
                    for text in ocr_snapshot:
                        if type(text) is not str or len(text) > MAX_TEXT_CHARS:
                            raise ValueError("ocr_texts contains a non-canonical bounded string")
                        text.encode("utf-8")
                        ocr_chars += len(text)
                        if ocr_chars > _MAX_OCR_REFERENCE_CHARS:
                            raise ValueError("ocr_texts exceeds the aggregate character limit")
                except (UnicodeEncodeError, ValueError) as exc:
                    failures.append(
                        CandidateFailure(
                            stage="source_context",
                            engine="source_context",
                            error_type=type(exc).__name__,
                            message=f"invalid OCR text collection was isolated: {exc}",
                        )
                    )
                    boundary_warnings.append(
                        "invalid or oversized OCR text input was isolated before reconstruction"
                    )
                else:
                    resolved_ocr_texts = ocr_snapshot

        fallback_source_blocks = [source_block] if source_block is not None else []
        if source_blocks is None:
            resolved_source_blocks = fallback_source_blocks
        elif type(source_blocks) is not list:
            resolved_source_blocks = fallback_source_blocks
            failures.append(
                CandidateFailure(
                    stage="source_context",
                    engine="source_context",
                    error_type="CollectionTypeError",
                    message=(
                        "source_blocks must be an exact plain list; the collection was isolated"
                    ),
                )
            )
            boundary_warnings.append(
                "non-canonical source block input was isolated before reconstruction"
            )
        else:
            source_block_snapshot = source_blocks[: MAX_EVIDENCE_REFS + 1]
            if len(source_block_snapshot) > MAX_EVIDENCE_REFS:
                resolved_source_blocks = fallback_source_blocks
                failures.append(
                    CandidateFailure(
                        stage="source_context",
                        engine="source_context",
                        error_type="CollectionLimitError",
                        message=(
                            f"source_blocks exceeds the {MAX_EVIDENCE_REFS}-item limit; "
                            "the collection was isolated"
                        ),
                    )
                )
                boundary_warnings.append(
                    "oversized source block input was isolated before reconstruction"
                )
            else:
                resolved_source_blocks = source_block_snapshot or fallback_source_blocks

        resolved_vector_sources: list[object] = []
        if vector_sources is not None:
            if type(vector_sources) is not list:
                failures.append(
                    CandidateFailure(
                        stage="source_context",
                        engine="source_context",
                        error_type="CollectionTypeError",
                        message=(
                            "vector_sources must be an exact plain list; "
                            "the collection was isolated"
                        ),
                    )
                )
                boundary_warnings.append(
                    "non-canonical vector source input was isolated before reconstruction"
                )
            else:
                vector_source_snapshot = vector_sources[: MAX_EVIDENCE_REFS + 1]
                if len(vector_source_snapshot) > MAX_EVIDENCE_REFS:
                    failures.append(
                        CandidateFailure(
                            stage="source_context",
                            engine="source_context",
                            error_type="CollectionLimitError",
                            message=(
                                f"vector_sources exceeds the {MAX_EVIDENCE_REFS}-item limit; "
                                "the collection was isolated"
                            ),
                        )
                    )
                    boundary_warnings.append(
                        "oversized vector source input was isolated before reconstruction"
                    )
                else:
                    resolved_vector_sources = vector_source_snapshot

        all_evidence: list[VisualEvidence] = []
        global_evidence_usage = EvidenceBudgetUsage()
        evidence_collection_error: Exception | None = None
        try:
            initial_evidence_snapshot = canonical_evidence_collection_snapshot(
                [] if evidence is None else evidence,
                item_limit=MAX_OBSERVATION_EVIDENCE,
                character_limit=MAX_VLM_EVIDENCE_INPUT_CHARS,
            )
            all_evidence = list(initial_evidence_snapshot.evidence)
            global_evidence_usage = initial_evidence_snapshot.usage
        except Exception as exc:
            evidence_collection_error = exc
        if evidence_collection_error is not None:
            failures.append(
                CandidateFailure(
                    stage="source_context",
                    engine="source_context",
                    error_type=type(evidence_collection_error).__name__,
                    message=(
                        "invalid initial evidence was isolated as one collection: "
                        f"{evidence_collection_error}"
                    ),
                )
            )
            boundary_warnings.append(
                "invalid or oversized initial evidence was isolated before reconstruction"
            )

        source_block_id_set = set(resolved_source_block_ids)
        initial_evidence_ids: set[str] = set()
        duplicate_initial_evidence_ids: set[str] = set()
        for item in all_evidence:
            if item.id in initial_evidence_ids:
                duplicate_initial_evidence_ids.add(item.id)
            initial_evidence_ids.add(item.id)
        trusted_label_evidence_ids = {
            item.id
            for item in all_evidence
            if item.id not in duplicate_initial_evidence_ids
            and item.kind in {"ocr_token", "vector_text"}
            and item.bbox is not None
            and bool(source_block_id_set.intersection(item.source_block_ids))
        }
        known_evidence_ids = {item.id for item in all_evidence}
        collided_evidence_ids = set(duplicate_initial_evidence_ids)
        publication_evidence_ids = known_evidence_ids - collided_evidence_ids
        approved_user_edit_evidence_ids = frozenset(
            item.id
            for item in all_evidence
            if item.id not in duplicate_initial_evidence_ids and item.kind == "user_edit"
        )
        publication_evidence_registry = {
            item.id: VisualEvidence.model_validate(
                {
                    "id": item.id,
                    "kind": item.kind,
                    "bbox": item.bbox,
                    "text": item.text,
                    "font_weight": item.font_weight,
                    "score": item.score,
                    "source_block_ids": item.source_block_ids[:],
                }
            )
            for item in all_evidence
            if item.id in publication_evidence_ids
        }
        trusted_bold_evidence: dict[str, VisualEvidence] = {}
        trusted_vector_style_evidence: dict[str, SceneElement] = {}
        trusted_edge_style_evidence: dict[str, TrustedEdgeStyleEvidence] = {}
        trusted_label_registry = set(trusted_label_evidence_ids)
        trusted_connector_registry: set[str] = set()
        trusted_connector_relation_registry: set[tuple[str, str, frozenset[str]]] = set()
        conflicted_connector_registry: set[frozenset[str]] = set()
        try:
            views, view_warnings = build_visual_priors(source_image, all_evidence, self.config)
        except Exception as exc:
            views = {"original": Image.new("RGB", (1, 1), "white")}
            view_warnings = [f"visual prior generation failed: {exc}"]
        view_warnings = list(dict.fromkeys([*boundary_warnings, *view_warnings]))
        try:
            if type(views) is not dict or not views or len(views) > self.config.max_views:
                raise ValueError("visual priors must be a bounded plain dictionary")
            first_view_name = next(iter(views))
            if type(first_view_name) is not str or first_view_name != "original":
                raise ValueError("visual priors must start with the original image")
            repair_views: dict[str, Image.Image] = {}
            repair_view_pixels = 0
            for name, view in views.items():
                if type(name) is not str or not name:
                    raise ValueError("visual prior names must be non-empty plain strings")
                snapshot = _canonical_rgb_image_snapshot(view)
                repair_view_pixels += snapshot.width * snapshot.height
                if repair_view_pixels > MAX_VLM_TOTAL_VIEW_PIXELS:
                    raise ValueError("visual priors exceed the aggregate pixel boundary")
                repair_views[name] = snapshot
            repair_image = _canonical_rgb_image_snapshot(repair_views["original"])
        except Exception as exc:
            repair_image = Image.new("RGB", (1, 1), "white")
            repair_views = {"original": Image.new("RGB", (1, 1), "white")}
            view_warnings = list(
                dict.fromkeys(
                    [
                        *view_warnings,
                        f"repair visual snapshot generation failed: {exc}",
                    ]
                )
            )
        trusted_engine_views = repair_views
        context = SourceContext(
            source_id=source_id,
            source_block_ids=resolved_source_block_ids[:],
            source_image_name=source_image_name,
            image=_canonical_rgb_image_snapshot(
                source_image,
                max_dimension=self.config.max_virtual_source_dimension,
                max_pixels=self.config.max_virtual_source_pixels,
            ),
            views=views,
            evidence=[
                VisualEvidence.model_validate(
                    {
                        "id": item.id,
                        "kind": item.kind,
                        "bbox": item.bbox,
                        "text": item.text,
                        "font_weight": item.font_weight,
                        "score": item.score,
                        "source_block_ids": item.source_block_ids[:],
                    }
                )
                for item in all_evidence
            ],
            trusted_label_evidence_ids=set(trusted_label_registry),
            trusted_connector_evidence_ids=set(trusted_connector_registry),
            trusted_connector_relations=set(trusted_connector_relation_registry),
            conflicted_connector_pairs=set(conflicted_connector_registry),
            ocr_texts=resolved_ocr_texts[:],
            source_block=source_block,
            source_blocks=resolved_source_blocks[:],
            vector_sources=resolved_vector_sources[:],
            source_mapping=canonical_source_mapping_snapshot(resolved_source_mapping),
        )

        successful_observations: list[tuple[str, str, EngineObservation]] = []
        prior_evidence_by_observation: dict[int, dict[str, VisualEvidence]] = {}
        evaluation_evidence_by_observation: dict[int, frozenset[str]] = {}
        observed_relation_directions: dict[frozenset[str], set[tuple[str, str, bool, bool]]] = {}
        view_type_hints: list[str] = []
        global_evidence_limit_reported = False
        for engine in self.engines:
            engine_context = SourceContext(
                source_id=source_id,
                source_block_ids=resolved_source_block_ids[:],
                source_image_name=source_image_name,
                image=_canonical_rgb_image_snapshot(
                    source_image,
                    max_dimension=self.config.max_virtual_source_dimension,
                    max_pixels=self.config.max_virtual_source_pixels,
                ),
                views={
                    name: _canonical_rgb_image_snapshot(view)
                    for name, view in trusted_engine_views.items()
                },
                evidence=[
                    VisualEvidence.model_validate(
                        {
                            "id": item.id,
                            "kind": item.kind,
                            "bbox": item.bbox,
                            "text": item.text,
                            "font_weight": item.font_weight,
                            "score": item.score,
                            "source_block_ids": item.source_block_ids[:],
                        }
                    )
                    for item in all_evidence
                ],
                trusted_label_evidence_ids=set(trusted_label_registry),
                trusted_connector_evidence_ids=set(trusted_connector_registry),
                trusted_connector_relations=set(trusted_connector_relation_registry),
                conflicted_connector_pairs=set(conflicted_connector_registry),
                ocr_texts=resolved_ocr_texts[:],
                source_block=source_block,
                source_blocks=resolved_source_blocks[:],
                vector_sources=resolved_vector_sources[:],
                source_mapping=canonical_source_mapping_snapshot(resolved_source_mapping),
            )
            try:
                raw_observation = engine.observe(engine_context)
            except Exception as exc:  # Candidate failures never fail the document.
                if type(exc) is StructuredVLMRequestError:
                    try:
                        prompt_budget_notice = PromptBudgetNotice.model_validate(
                            exc.prompt_budget_notice.model_dump(mode="python")
                        )
                    except Exception:
                        prompt_budget_notice = None
                    if prompt_budget_notice is not None:
                        if len(prompt_budget_notices) >= MAX_OBSERVATION_WARNINGS:
                            failures.append(
                                CandidateFailure(
                                    stage="generation",
                                    engine=engine.name,
                                    error_type="PromptBudgetNoticeLimit",
                                    message="additional prompt budget notices were omitted",
                                )
                            )
                        else:
                            prompt_budget_notices.append(prompt_budget_notice)
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
                continue
            if not isinstance(raw_observation, EngineObservation):
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type="TypeError",
                        message="engine returned a non-EngineObservation payload",
                    )
                )
                continue
            try:
                prompt_supplied_prior_ids = raw_observation.prompt_supplied_prior_evidence_ids
                prompt_budget_notice = raw_observation.prompt_budget_notice
            except Exception as exc:
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type=type(exc).__name__,
                        message=f"invalid private engine metadata was isolated: {exc}",
                    )
                )
                continue
            if prompt_budget_notice is not None:
                if len(prompt_budget_notices) >= MAX_OBSERVATION_WARNINGS:
                    failures.append(
                        CandidateFailure(
                            stage="generation",
                            engine=engine.name,
                            error_type="PromptBudgetNoticeLimit",
                            message="additional prompt budget notices were omitted",
                        )
                    )
                else:
                    prompt_budget_notices.append(prompt_budget_notice)
            try:
                prediction = DiagramTypePrediction.model_validate(
                    raw_observation.prediction.model_dump(mode="python")
                )
            except Exception as exc:
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type=type(exc).__name__,
                        message=f"invalid diagram type prediction: {exc}",
                    )
                )
                continue
            scene_ir = None
            if raw_observation.scene_ir is not None:
                try:
                    scene_ir = DiagramSceneIR.model_validate(
                        raw_observation.scene_ir.model_dump(mode="python")
                    )
                except Exception as exc:
                    failures.append(
                        CandidateFailure(
                            stage="generation",
                            engine=engine.name,
                            error_type=type(exc).__name__,
                            message=f"invalid Scene IR was isolated: {exc}",
                        )
                    )
            typed_candidates: list[TypedIRCandidate] = []
            typed_candidate_json_bytes = 0
            if type(raw_observation.typed_candidates) is not list:
                raw_typed_candidates: list[object] = []
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type="TypeError",
                        message="typed candidates must be an exact plain list",
                    )
                )
            else:
                raw_typed_candidates = list.__getitem__(
                    raw_observation.typed_candidates,
                    slice(0, MAX_OBSERVATION_CANDIDATES + 1),
                )
                if len(raw_typed_candidates) > MAX_OBSERVATION_CANDIDATES:
                    failures.append(
                        CandidateFailure(
                            stage="generation",
                            engine=engine.name,
                            error_type="TypedCandidateLimitError",
                            message=(
                                "typed candidate collection exceeded its item limit; "
                                "the bounded prefix was retained"
                            ),
                        )
                    )
                    raw_typed_candidates = raw_typed_candidates[:MAX_OBSERVATION_CANDIDATES]
            for candidate in raw_typed_candidates:
                typed_candidate_budget_exhausted = False
                try:
                    if type(candidate) is not TypedIRCandidate:
                        raise TypeError(
                            "typed candidate must be an exact canonical TypedIRCandidate record"
                        )
                    is_model, diagram_type, ir_snapshot, confidence = (
                        _canonical_typed_candidate_fields(candidate)
                    )
                    if not is_model:  # pragma: no cover - exact type guard above
                        raise TypeError("typed candidate must remain a canonical model")
                    ir_json_bytes = len(
                        json.dumps(
                            ir_snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("utf-8")
                    )
                    next_typed_candidate_json_bytes = typed_candidate_json_bytes + ir_json_bytes
                    if next_typed_candidate_json_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                        typed_candidate_budget_exhausted = True
                        raise ValueError(
                            "typed candidate exceeds the aggregate observation IR budget"
                        )
                    typed_candidate_json_bytes = next_typed_candidate_json_bytes
                    value_candidate = TypedIRCandidate.model_validate(
                        {
                            "diagram_type": diagram_type,
                            "ir": ir_snapshot,
                            "confidence": confidence,
                        }
                    )
                    typed_candidates.append(value_candidate)
                except Exception as exc:
                    failure_detail = (
                        "typed candidate exceeds the aggregate observation IR budget"
                        if typed_candidate_budget_exhausted
                        else type(exc).__name__
                    )
                    failures.append(
                        CandidateFailure(
                            stage="generation",
                            engine=engine.name,
                            error_type=type(exc).__name__,
                            message=f"invalid typed candidate was isolated: {failure_detail}",
                        )
                    )
                    if typed_candidate_budget_exhausted:
                        break
            direct_candidates = []
            for candidate in raw_observation.direct_candidates:
                try:
                    direct_candidates.append(
                        DirectMermaidCandidate.model_validate(candidate.model_dump(mode="python"))
                    )
                except Exception as exc:
                    failures.append(
                        CandidateFailure(
                            stage="generation",
                            engine=engine.name,
                            error_type=type(exc).__name__,
                            message=f"invalid direct candidate was isolated: {exc}",
                        )
                    )
            observation_evidence: list[VisualEvidence] = []
            observation_evidence_error: Exception | None = None
            try:
                observation_evidence_snapshot = canonical_evidence_collection_snapshot(
                    raw_observation.evidence,
                    item_limit=MAX_OBSERVATION_EVIDENCE,
                    character_limit=MAX_VLM_EVIDENCE_INPUT_CHARS,
                )
                observation_evidence = list(observation_evidence_snapshot.evidence)
            except Exception as exc:
                observation_evidence_error = exc
            if observation_evidence_error is not None:
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type=type(observation_evidence_error).__name__,
                        message=(
                            "invalid evidence was isolated as one engine collection: "
                            f"{observation_evidence_error}"
                        ),
                    )
                )
            try:
                observation = EngineObservation(
                    prediction=prediction,
                    scene_ir=scene_ir,
                    typed_candidates=typed_candidates,
                    direct_candidates=direct_candidates,
                    evidence=observation_evidence,
                    warnings=list(raw_observation.warnings),
                )
            except Exception as exc:
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type=type(exc).__name__,
                        message=f"invalid engine warnings were dropped: {exc}",
                    )
                )
                observation = EngineObservation(
                    prediction=prediction,
                    scene_ir=scene_ir,
                    typed_candidates=typed_candidates,
                    direct_candidates=direct_candidates,
                    evidence=observation_evidence,
                )
            if prompt_supplied_prior_ids is not None:
                observation._set_prompt_supplied_prior_evidence_ids(set(prompt_supplied_prior_ids))
            if prompt_budget_notice is not None:
                observation._set_prompt_budget_notice(prompt_budget_notice)
            fusion_source = getattr(engine, "fusion_source", "other")
            if fusion_source not in {"vector", "geometry", "ocr", "vlm", "other"}:
                fusion_source = "other"
            trusted_vector_engine = type(engine) is VectorPrimitiveEngine
            trusted_geometry_engine = type(engine) is GeometryEngine
            new_trusted_vector_contours: set[str] = set()
            new_trusted_vector_lines: set[str] = set()
            relation_counts: dict[frozenset[str], int] = {}
            if observation.scene_ir is not None:
                for relation in observation.scene_ir.relations:
                    if (
                        relation.source_id is None
                        or relation.target_id is None
                        or relation.source_id == relation.target_id
                    ):
                        continue
                    pair = frozenset({relation.source_id, relation.target_id})
                    relation_counts[pair] = relation_counts.get(pair, 0) + 1
                    directions = observed_relation_directions.setdefault(pair, set())
                    directions.add(
                        (
                            relation.source_id,
                            relation.target_id,
                            relation.arrow_at_start,
                            relation.arrow_at_end,
                        )
                    )
                    if len(directions) > 1:
                        conflicted_connector_registry.add(pair)
                conflicted_connector_registry.update(
                    pair for pair, count in relation_counts.items() if count > 1
                )
            has_payload = bool(
                observation.scene_ir is not None
                or observation.typed_candidates
                or observation.direct_candidates
                or observation.evidence
            )
            if not has_payload:
                for warning in observation.warnings:
                    failures.append(
                        CandidateFailure(
                            stage="generation",
                            engine=engine.name,
                            error_type="EmptyObservationWarning",
                            message=warning,
                        )
                    )
            current_prior = {
                evidence_id: item
                for evidence_id, item in publication_evidence_registry.items()
                if prompt_supplied_prior_ids is None or evidence_id in prompt_supplied_prior_ids
            }
            hints_changed = False
            for diagram_type in observation.prediction.candidates[
                : self.config.type_candidate_count
            ]:
                if (
                    diagram_type in self.config.enabled_types
                    and diagram_type not in view_type_hints
                ):
                    view_type_hints.append(diagram_type)
                    hints_changed = True
            evidence_changed = False
            new_publication_evidence_ids: set[str] = set()
            prospective_evidence_ids = set(known_evidence_ids)
            new_evidence: list[VisualEvidence] = []
            for item in observation.evidence:
                if item.id not in prospective_evidence_ids:
                    new_evidence.append(item)
                    prospective_evidence_ids.add(item.id)
            admitted_evidence_by_id: dict[str, VisualEvidence] = {}
            try:
                admitted_evidence = canonical_evidence_collection_snapshot(
                    new_evidence,
                    base=global_evidence_usage,
                    item_limit=MAX_OBSERVATION_EVIDENCE,
                    character_limit=MAX_VLM_EVIDENCE_INPUT_CHARS,
                )
            except (TypeError, UnicodeEncodeError, ValueError):
                observation.evidence = []
                if not global_evidence_limit_reported:
                    global_evidence_limit_reported = True
                    message = (
                        "global evidence item limit, character limit, or provenance limit reached; "
                        "additional engine evidence was isolated without publication authority"
                    )
                    failures.append(
                        CandidateFailure(
                            stage="generation",
                            engine=engine.name,
                            error_type="EvidenceLimitError",
                            message=message,
                        )
                    )
                    view_warnings = list(dict.fromkeys([*view_warnings, message]))
            else:
                global_evidence_usage = admitted_evidence.usage
                admitted_evidence_by_id = {item.id: item for item in admitted_evidence.evidence}
            for item in observation.evidence:
                if item.id not in known_evidence_ids:
                    canonical_item = admitted_evidence_by_id.get(item.id)
                    if canonical_item is None:
                        continue
                    item = canonical_item
                    all_evidence.append(item)
                    known_evidence_ids.add(item.id)
                    evidence_changed = True
                    if prompt_supplied_prior_ids is None:
                        publication_evidence_ids.add(item.id)
                        publication_evidence_registry[item.id] = VisualEvidence.model_validate(
                            {
                                "id": item.id,
                                "kind": item.kind,
                                "bbox": item.bbox,
                                "text": item.text,
                                "font_weight": item.font_weight,
                                "score": item.score,
                                "source_block_ids": item.source_block_ids[:],
                            }
                        )
                        new_publication_evidence_ids.add(item.id)
                    if trusted_geometry_engine and item.kind in {"line_segment", "arrowhead"}:
                        trusted_connector_registry.add(item.id)
                    if (
                        trusted_vector_engine
                        and item.kind == "vector_text"
                        and item.bbox is not None
                        and source_block_id_set.intersection(item.source_block_ids)
                    ):
                        trusted_label_registry.add(item.id)
                    if (
                        trusted_vector_engine
                        and item.kind == "contour"
                        and item.bbox is not None
                        and source_block_id_set.intersection(item.source_block_ids)
                    ):
                        new_trusted_vector_contours.add(item.id)
                    if (
                        trusted_vector_engine
                        and item.kind == "line_segment"
                        and item.bbox is not None
                        and source_block_id_set.intersection(item.source_block_ids)
                    ):
                        new_trusted_vector_lines.add(item.id)
                    if (
                        trusted_vector_engine
                        and item.kind == "vector_text"
                        and item.font_weight == "bold"
                        and item.bbox is not None
                        and source_block_id_set.intersection(item.source_block_ids)
                    ):
                        trusted_bold_evidence[item.id] = item
                else:
                    # A provenance ID collision cannot authorize style even
                    # when the duplicate payload happens to be identical.
                    collided_evidence_ids.add(item.id)
                    publication_evidence_ids.discard(item.id)
                    publication_evidence_registry.pop(item.id, None)
                    new_publication_evidence_ids.discard(item.id)
                    trusted_bold_evidence.pop(item.id, None)
                    trusted_vector_style_evidence.pop(item.id, None)
                    trusted_edge_style_evidence.pop(item.id, None)
                    new_trusted_vector_contours.discard(item.id)
                    new_trusted_vector_lines.discard(item.id)
                    trusted_label_registry.discard(item.id)
                    trusted_connector_registry.discard(item.id)
                    trusted_connector_relation_registry = {
                        relation
                        for relation in trusted_connector_relation_registry
                        if item.id not in relation[2]
                    }
            has_payload = bool(
                observation.scene_ir is not None
                or observation.typed_candidates
                or observation.direct_candidates
                or observation.evidence
            )
            if has_payload:
                successful_observations.append((engine.name, fusion_source, observation))
                existing_prior = prior_evidence_by_observation.get(id(observation))
                if existing_prior is None:
                    prior_evidence_by_observation[id(observation)] = current_prior
                else:
                    prior_evidence_by_observation[id(observation)] = {
                        evidence_id: existing
                        for evidence_id, existing in existing_prior.items()
                        if evidence_id in current_prior
                        and existing.model_dump(mode="json")
                        == current_prior[evidence_id].model_dump(mode="json")
                    }
            if has_payload:
                evaluation_authority = set(current_prior)
                if prompt_supplied_prior_ids is None:
                    evaluation_authority.update(new_publication_evidence_ids)
                evaluation_evidence_by_observation[id(observation)] = frozenset(
                    evaluation_authority
                )
            if trusted_vector_engine and observation.scene_ir is not None:
                contour_reference_counts: dict[str, int] = {}
                for element in observation.scene_ir.elements:
                    for evidence_id in set(element.evidence_ids).intersection(
                        new_trusted_vector_contours
                    ):
                        contour_reference_counts[evidence_id] = (
                            contour_reference_counts.get(evidence_id, 0) + 1
                        )
                for element in observation.scene_ir.elements:
                    if not (
                        element.fill_color
                        or element.border_color
                        or element.border_style in {"dashed", "thick"}
                    ):
                        continue
                    for evidence_id in element.evidence_ids:
                        if (
                            evidence_id in new_trusted_vector_contours
                            and contour_reference_counts.get(evidence_id) == 1
                        ):
                            trusted_vector_style_evidence[evidence_id] = element.model_copy(
                                deep=True
                            )
                vector_elements_by_id = {
                    element.id: element for element in observation.scene_ir.elements
                }
                line_reference_counts: dict[str, int] = {}
                for relation in observation.scene_ir.relations:
                    for evidence_id in set(relation.evidence_ids).intersection(
                        new_trusted_vector_lines
                    ):
                        line_reference_counts[evidence_id] = (
                            line_reference_counts.get(evidence_id, 0) + 1
                        )
                for relation in observation.scene_ir.relations:
                    if not (
                        relation.source_id in vector_elements_by_id
                        and relation.target_id in vector_elements_by_id
                        and (relation.line_color or relation.line_style in {"dashed", "thick"})
                    ):
                        continue
                    for evidence_id in relation.evidence_ids:
                        if (
                            evidence_id in new_trusted_vector_lines
                            and line_reference_counts.get(evidence_id) == 1
                        ):
                            trusted_edge_style_evidence[evidence_id] = TrustedEdgeStyleEvidence(
                                relation=relation.model_copy(deep=True),
                                source_bbox=vector_elements_by_id[relation.source_id].bbox,
                                target_bbox=vector_elements_by_id[relation.target_id].bbox,
                            )
            if trusted_geometry_engine and observation.scene_ir is not None:
                for relation in observation.scene_ir.relations:
                    if (
                        relation.source_id is not None
                        and relation.target_id is not None
                        and relation.evidence_ids
                        and set(relation.evidence_ids).issubset(trusted_connector_registry)
                    ):
                        trusted_connector_relation_registry.add(
                            (
                                relation.source_id,
                                relation.target_id,
                                frozenset(relation.evidence_ids),
                            )
                        )
            if evidence_changed or hints_changed:
                try:
                    rebuilt_views, new_warnings = build_visual_priors(
                        source_image,
                        all_evidence,
                        self.config,
                        diagram_types=view_type_hints,
                    )
                    if (
                        type(rebuilt_views) is not dict
                        or not rebuilt_views
                        or len(rebuilt_views) > self.config.max_views
                    ):
                        raise ValueError("rebuilt visual priors are not a bounded plain dictionary")
                    rebuilt_snapshot: dict[str, Image.Image] = {}
                    rebuilt_pixels = 0
                    for name, view in rebuilt_views.items():
                        if type(name) is not str or not name:
                            raise ValueError(
                                "rebuilt visual prior names must be non-empty plain strings"
                            )
                        snapshot = _canonical_rgb_image_snapshot(view)
                        rebuilt_pixels += snapshot.width * snapshot.height
                        if rebuilt_pixels > MAX_VLM_TOTAL_VIEW_PIXELS:
                            raise ValueError(
                                "rebuilt visual priors exceed the aggregate pixel boundary"
                            )
                        rebuilt_snapshot[name] = snapshot
                    trusted_engine_views = rebuilt_snapshot
                    view_warnings = list(dict.fromkeys([*view_warnings, *new_warnings]))
                except Exception as exc:
                    view_warnings = list(
                        dict.fromkeys([*view_warnings, f"visual prior enrichment failed: {exc}"])
                    )

        # Restore repair inputs from snapshots that were never exposed to a
        # candidate engine. This prevents custom engines from planting copy or
        # iteration hooks in the semantic-repair context.
        context.image = _canonical_rgb_image_snapshot(
            source_image,
            max_dimension=self.config.max_virtual_source_dimension,
            max_pixels=self.config.max_virtual_source_pixels,
        )
        context.views = {
            name: _canonical_rgb_image_snapshot(view) for name, view in repair_views.items()
        }
        context.evidence = [
            VisualEvidence.model_validate(
                {
                    "id": item.id,
                    "kind": item.kind,
                    "bbox": item.bbox,
                    "text": item.text,
                    "font_weight": item.font_weight,
                    "score": item.score,
                    "source_block_ids": item.source_block_ids[:],
                }
            )
            for item in all_evidence
        ]
        context.trusted_label_evidence_ids = set(trusted_label_registry)
        context.trusted_connector_evidence_ids = set(trusted_connector_registry)
        context.trusted_connector_relations = set(trusted_connector_relation_registry)
        context.conflicted_connector_pairs = set(conflicted_connector_registry)
        context.source_block_ids = resolved_source_block_ids[:]
        context.ocr_texts = resolved_ocr_texts[:]
        context.source_mapping = canonical_source_mapping_snapshot(resolved_source_mapping)

        generation_observations = [
            (name, observation, False) for name, _source, observation in successful_observations
        ]
        if self.config.enable_fusion and len(successful_observations) >= 2:
            try:
                fusion_inputs: list[FusionInput] = []
                for name, source, observation in successful_observations:
                    prior_snapshot = prior_evidence_by_observation.get(id(observation), {})
                    allowed_prior_ids = set(prior_snapshot) - collided_evidence_ids
                    publication_authority_ids = (
                        set(evaluation_evidence_by_observation.get(id(observation), frozenset()))
                        - collided_evidence_ids
                    )
                    fusion_inputs.append(
                        FusionInput(
                            source=source,
                            observation=observation,
                            name=name,
                            prior_evidence_ids=frozenset(allowed_prior_ids),
                            prior_evidence=tuple(
                                prior_snapshot[evidence_id]
                                for evidence_id in sorted(allowed_prior_ids)
                            ),
                            publication_evidence_ids=frozenset(publication_authority_ids),
                            excluded_evidence_ids=frozenset(collided_evidence_ids),
                            trusted_canvas_size=(
                                float(source_image.width),
                                float(source_image.height),
                            ),
                            trusted_source_block_ids=frozenset(source_block_id_set),
                        )
                    )
                fused = FusionEngine().fuse(fusion_inputs)
                if fused.scene_ir is not None:
                    fused.scene_ir = DiagramSceneIR.model_validate(
                        fused.scene_ir.model_dump(mode="python")
                    )
                fused_evidence_snapshot = canonical_evidence_collection_snapshot(
                    fused.evidence,
                    item_limit=MAX_OBSERVATION_EVIDENCE,
                    character_limit=MAX_VLM_EVIDENCE_INPUT_CHARS,
                )
                fused.evidence = list(fused_evidence_snapshot.evidence)
                prospective_fused_ids = set(known_evidence_ids)
                new_fused_evidence: list[VisualEvidence] = []
                for item in fused.evidence:
                    if item.id not in prospective_fused_ids:
                        new_fused_evidence.append(item)
                        prospective_fused_ids.add(item.id)
                admitted_fused_evidence: tuple[VisualEvidence, ...] = ()
                try:
                    fused_admission = canonical_evidence_collection_snapshot(
                        new_fused_evidence,
                        base=global_evidence_usage,
                        item_limit=MAX_OBSERVATION_EVIDENCE,
                        character_limit=MAX_VLM_EVIDENCE_INPUT_CHARS,
                    )
                except (TypeError, UnicodeEncodeError, ValueError):
                    fused.evidence = []
                    if not global_evidence_limit_reported:
                        global_evidence_limit_reported = True
                        message = (
                            "global evidence item limit, character limit, or provenance limit "
                            "reached; "
                            "additional fused evidence was isolated without publication authority"
                        )
                        failures.append(
                            CandidateFailure(
                                stage="fusion",
                                engine=FusionEngine.name,
                                error_type="EvidenceLimitError",
                                message=message,
                            )
                        )
                        view_warnings = list(dict.fromkeys([*view_warnings, message]))
                else:
                    global_evidence_usage = fused_admission.usage
                    admitted_fused_evidence = fused_admission.evidence
                generation_observations = [
                    (FusionEngine.name, fused, True),
                    *generation_observations,
                ]
                fused_authorities = [
                    set(item.publication_evidence_ids)
                    for item in fusion_inputs
                    if item.publication_evidence_ids is not None
                ]
                evaluation_evidence_by_observation[id(fused)] = frozenset(
                    set().union(*fused_authorities) if fused_authorities else set()
                )
                context.conflicted_connector_pairs.update(fused.fusion_conflicted_connector_pairs)
                for item in admitted_fused_evidence:
                    all_evidence.append(item)
                    if type(context.evidence) is not list:
                        context.evidence = []
                    context.evidence.append(item.model_copy(deep=True))
                    known_evidence_ids.add(item.id)
            except Exception as exc:
                failures.append(
                    CandidateFailure(
                        stage="fusion",
                        engine=FusionEngine.name,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )

        draft_groups: list[list[_Draft]] = []
        candidate_budget = int(self.config.candidate_count or 1)
        for engine_name, observation, is_fused in generation_observations:
            observation_authority = evaluation_evidence_by_observation.get(id(observation))
            top_types = [
                item
                for item in observation.prediction.candidates[: self.config.type_candidate_count]
                if item in self.config.enabled_types
            ]
            generated: list[_Draft] = []
            if self.config.enable_typed_ir:
                eligible_typed_candidates = [
                    typed
                    for diagram_type in top_types
                    for typed in observation.typed_candidates
                    if typed.diagram_type == diagram_type
                ]
                for typed in eligible_typed_candidates[:candidate_budget]:
                    try:
                        enriched_ir = enrich_accessibility_ir(
                            typed.ir,
                            typed.diagram_type,
                            experimental=self.config.mode != Mode.STRICT,
                        )
                        enriched_snapshot = canonical_typed_ir_snapshot(enriched_ir)
                        if enriched_snapshot is None:
                            raise TypeError("accessibility-enriched typed IR must be an object")
                        enriched_candidate = TypedIRCandidate.model_validate(
                            {
                                "diagram_type": typed.diagram_type,
                                "ir": enriched_snapshot,
                                "confidence": typed.confidence,
                            }
                        )
                        enriched_ir = canonical_typed_ir_snapshot(enriched_candidate.ir)
                        if enriched_ir is None:  # pragma: no cover - model contract guard
                            raise TypeError("validated accessibility typed IR must be an object")
                        serialized = serialize_typed_ir_result(
                            typed.diagram_type,
                            enriched_ir,
                            experimental=self.config.mode != Mode.STRICT,
                        )
                    except (
                        SerializationError,
                        SerializationContractError,
                        TypeError,
                        ValueError,
                    ) as exc:
                        serialization_message = (
                            str(exc)
                            if isinstance(exc, SerializationError | SerializationContractError)
                            else type(exc).__name__
                        )
                        failures.append(
                            CandidateFailure(
                                stage="serialization",
                                engine=engine_name,
                                error_type=type(exc).__name__,
                                message=serialization_message,
                            )
                        )
                        continue
                    generated.append(
                        _Draft(
                            "typed_ir",
                            engine_name,
                            typed.diagram_type,
                            serialized.code,
                            observation,
                            emitted_diagram_type=serialized.emitted_type,
                            fallback_chain=list(serialized.fallback_chain),
                            serialization_stability=serialized.stability,
                            typed_ir=enriched_ir,
                            node_id_mappings=(
                                observation.fusion_node_id_mappings_for(typed) if is_fused else []
                            ),
                            warnings=list(serialized.warnings),
                            evidence_authority_ids=(
                                observation.fusion_typed_evidence_authority_for(typed)
                                if is_fused
                                else observation_authority
                            ),
                        )
                    )
            if (
                self.config.enable_generic_scene_ir
                and observation.scene_ir is not None
                and observation.scene_ir.elements
            ):
                fallback_from = top_types[0] if top_types else "unknown"
                requested_type = fallback_from if fallback_from != "unknown" else "flowchart"
                code = scene_to_flowchart(
                    observation.scene_ir,
                    experimental=self.config.mode != Mode.STRICT,
                    accessibility_type=requested_type,
                )
                serialized = (
                    SerializationResult.native("flowchart", code)
                    if requested_type == "flowchart"
                    else SerializationResult.fallback(
                        requested_type,
                        "flowchart",
                        code,
                        warnings=(f"Portable flowchart fallback from {requested_type} Scene IR.",),
                        stability="experimental",
                    )
                )
                generated.append(
                    _Draft(
                        "scene_ir_fallback",
                        engine_name,
                        requested_type,
                        serialized.code,
                        observation,
                        emitted_diagram_type=serialized.emitted_type,
                        fallback_chain=list(serialized.fallback_chain),
                        serialization_stability=serialized.stability,
                        warnings=list(serialized.warnings),
                        evidence_authority_ids=(
                            observation.fusion_scene_evidence_authority
                            if is_fused
                            else observation_authority
                        ),
                    )
                )
            if self.config.enable_direct_mermaid:
                for direct in observation.direct_candidates[:candidate_budget]:
                    if direct.diagram_type in top_types:
                        generated.append(
                            _Draft(
                                "direct_mermaid",
                                engine_name,
                                direct.diagram_type,
                                direct.code,
                                observation,
                                emitted_diagram_type=direct.diagram_type,
                                fallback_chain=[direct.diagram_type],
                                serialization_stability="experimental",
                                raw_mermaid=direct.code,
                                evidence_authority_ids=(
                                    observation.fusion_direct_evidence_authority_for(direct)
                                    if is_fused
                                    else observation_authority
                                ),
                            )
                        )
            if generated:
                draft_groups.append(generated)

        drafts: list[_Draft] = []
        code_hashes: set[str] = set()
        while draft_groups and len(drafts) < candidate_budget:
            remaining_groups: list[list[_Draft]] = []
            for group in draft_groups:
                if len(drafts) >= candidate_budget:
                    break
                draft = group.pop(0)
                try:
                    digest = hashlib.sha256(draft.code.encode("utf-8")).hexdigest()
                except (AttributeError, UnicodeEncodeError) as exc:
                    failures.append(
                        CandidateFailure(
                            stage="candidate_deduplication",
                            engine=draft.engine_name,
                            error_type=type(exc).__name__,
                            message=f"candidate source was isolated before validation: {exc}",
                        )
                    )
                    if group:
                        remaining_groups.append(group)
                    continue
                if digest not in code_hashes:
                    code_hashes.add(digest)
                    drafts.append(draft)
                if group:
                    remaining_groups.append(group)
            draft_groups = remaining_groups

        candidates: list[MermaidCandidate] = []
        references = _reference_text_sets(context.ocr_texts, all_evidence)
        for index, draft in enumerate(drafts, start=1):
            style_preflight_warnings: list[str] = []
            if (
                self.config.enable_style_recovery
                and len(draft.code) <= self.config.max_mermaid_chars
            ):
                style_candidate_scene = None
                if draft.typed_ir is not None:
                    try:
                        style_candidate_scene = typed_ir_to_scene(
                            draft.diagram_type,
                            draft.typed_ir,
                            emitted_diagram_type=draft.emitted_diagram_type,
                        )
                    except Exception as exc:
                        style_preflight_warnings.append(
                            f"style recovery scene conversion was isolated: {exc}"
                        )
                elif draft.method == "scene_ir_fallback" and draft.observation.scene_ir is not None:
                    style_candidate_scene = draft.observation.scene_ir.model_copy(deep=True)
                style_recovery = recover_flowchart_styles(
                    draft.code,
                    draft.observation.scene_ir,
                    style_candidate_scene,
                    compatibility_profile=self.config.compatibility_profile,
                    security_profile=self.config.security_profile,
                    known_evidence_ids={item.id for item in all_evidence},
                    trusted_mapping_evidence_ids={
                        *context.trusted_label_evidence_ids,
                        *set(trusted_vector_style_evidence),
                    },
                    known_bold_evidence=trusted_bold_evidence,
                    known_node_style_evidence=trusted_vector_style_evidence,
                    known_edge_style_evidence=trusted_edge_style_evidence,
                    known_group_style_evidence=trusted_vector_style_evidence,
                )
                styled_code = style_recovery.code
                style_repair_history = (
                    [
                        RepairEvent(
                            iteration=0,
                            operation="recover_style",
                            accepted=True,
                            details={
                                "element_ids": list(style_recovery.applied_element_ids),
                                "link_indexes": list(style_recovery.applied_link_indexes),
                                "group_ids": list(style_recovery.applied_group_ids),
                                "attributions": [
                                    {
                                        "source_element_id": item.source_element_id,
                                        "emitted_element_id": item.emitted_element_id,
                                        "evidence_ids": list(item.evidence_ids),
                                        "match_method": item.match_method,
                                    }
                                    for item in style_recovery.attributions
                                ],
                                "edge_attributions": [
                                    {
                                        "source_relation_id": item.source_relation_id,
                                        "link_index": item.link_index,
                                        "evidence_ids": list(item.evidence_ids),
                                        "match_method": item.match_method,
                                    }
                                    for item in style_recovery.edge_attributions
                                ],
                                "group_attributions": [
                                    {
                                        "source_group_id": item.source_group_id,
                                        "emitted_group_id": item.emitted_group_id,
                                        "evidence_ids": list(item.evidence_ids),
                                        "match_method": item.match_method,
                                    }
                                    for item in style_recovery.group_attributions
                                ],
                                "stage": "pre_validation",
                            },
                        )
                    ]
                    if style_recovery.changed
                    else []
                )
            else:
                styled_code = draft.code
                style_recovery = None
                style_repair_history = []
                if self.config.enable_style_recovery:
                    style_preflight_warnings.append(
                        "style recovery skipped because Mermaid source exceeds the resource limit"
                    )
            source_repair = self.source_repair.repair(styled_code)
            repair_accepted = bool(
                source_repair.changed
                and source_repair.security_preserved
                and source_repair.idempotent
                and not source_repair.budget_exhausted
            )
            candidate_code = _canonical_publication_source(
                source_repair.source if repair_accepted else styled_code
            )
            source_repair_history = [
                RepairEvent(
                    iteration=0,
                    operation=event.operation,
                    accepted=repair_accepted,
                    details={
                        "line": event.line,
                        "before": event.before,
                        "after": event.after,
                        "reason": event.reason,
                        "stage": "pre_validation",
                    },
                )
                for event in source_repair.events
            ]
            source_repair_warnings = [
                f"source repair {issue.code}: {issue.message}" for issue in source_repair.issues
            ]
            if source_repair.changed and not repair_accepted:
                source_repair_warnings.append(
                    "deterministic source repairs were discarded because the bounded pass "
                    "was not complete and idempotent"
                )
            candidate = MermaidCandidate(
                candidate_id=f"candidate-{index}",
                generation_method=draft.method,
                generation_engine=draft.engine_name,
                diagram_type=draft.diagram_type,
                emitted_diagram_type=draft.emitted_diagram_type or draft.diagram_type,
                fallback_chain=draft.fallback_chain or [draft.diagram_type],
                serialization_stability=draft.serialization_stability,
                scene_ir=draft.observation.scene_ir,
                typed_ir=draft.typed_ir,
                raw_mermaid=draft.raw_mermaid,
                node_id_mappings=list(draft.node_id_mappings or []),
                mermaid_code=candidate_code,
                warnings=[
                    *view_warnings,
                    *draft.observation.warnings,
                    *(draft.warnings or []),
                    *style_preflight_warnings,
                    *(style_recovery.warnings if style_recovery is not None else ()),
                    *source_repair_warnings,
                ],
                repair_history=[*style_repair_history, *source_repair_history],
            )
            if draft.evidence_authority_ids is not None:
                candidate._set_publication_evidence_authority_ids(
                    draft.evidence_authority_ids - collided_evidence_ids
                )
            if candidate.node_id_mappings:
                candidate._seal_node_id_mappings()
            certification_outcome = None
            try:
                outcome = self.validator.validate(
                    candidate_code, self.config.render_timeout_seconds
                )
                runtime = outcome.runtime
                validation_warnings = outcome.warnings
                certification_outcome = outcome
            except Exception as exc:
                runtime = RuntimeResult(False, False, error=f"validator failed: {exc}")
                validation_warnings = []
                failures.append(
                    CandidateFailure(
                        stage="validation",
                        engine=draft.engine_name,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
            if (
                draft.method == "typed_ir"
                and draft.typed_ir is not None
                and not runtime.render_valid
            ):
                rejected_emitted_type = candidate.emitted_diagram_type
                try:
                    fallback = serialize_runtime_fallback_result(
                        draft.diagram_type,
                        draft.typed_ir,
                        experimental=self.config.mode != Mode.STRICT,
                    )
                    fallback_code = (
                        _canonical_publication_source(fallback.code)
                        if fallback is not None
                        else None
                    )
                    if fallback is not None and fallback_code != candidate_code:
                        fallback_details = {
                            "requested_type": draft.diagram_type,
                            "rejected_emitted_type": rejected_emitted_type,
                            "emitted_type": fallback.emitted_type,
                            "fallback_chain": list(fallback.fallback_chain),
                            "stage": "validation",
                        }
                        try:
                            fallback_outcome = self.validator.validate(
                                fallback_code,
                                self.config.render_timeout_seconds,
                            )
                        except Exception as exc:
                            failures.append(
                                CandidateFailure(
                                    stage="runtime_fallback_validation",
                                    engine=draft.engine_name,
                                    error_type=type(exc).__name__,
                                    message=str(exc),
                                )
                            )
                            candidate.warnings.append(
                                f"declared portable fallback validator failed: {exc}"
                            )
                            candidate.repair_history.append(
                                RepairEvent(
                                    iteration=0,
                                    operation="runtime_portable_fallback",
                                    accepted=False,
                                    details={
                                        **fallback_details,
                                        "error_type": type(exc).__name__,
                                        "error": str(exc),
                                    },
                                )
                            )
                        else:
                            fallback_runtime = fallback_outcome.runtime
                            fallback_runtime_type = _canonical_runtime_type(
                                fallback_runtime.diagram_type
                            )
                            fallback_valid = bool(
                                fallback_runtime.syntax_valid
                                and fallback_runtime.render_valid
                                and fallback_runtime_type == fallback.emitted_type
                            )
                            if fallback_valid:
                                candidate_code = fallback_code
                                candidate.mermaid_code = candidate_code
                                candidate.emitted_diagram_type = fallback.emitted_type
                                candidate.fallback_chain = list(fallback.fallback_chain)
                                candidate.serialization_stability = fallback.stability
                                serialization_warning_start = len(view_warnings) + len(
                                    draft.observation.warnings
                                )
                                serialization_warning_end = serialization_warning_start + len(
                                    draft.warnings or ()
                                )
                                candidate.warnings[
                                    serialization_warning_start:serialization_warning_end
                                ] = list(fallback.warnings)
                                candidate.repair_history.append(
                                    RepairEvent(
                                        iteration=0,
                                        operation="runtime_portable_fallback",
                                        accepted=True,
                                        details=fallback_details,
                                    )
                                )
                                runtime = fallback_runtime
                                validation_warnings = fallback_outcome.warnings
                                certification_outcome = fallback_outcome
                            else:
                                if not (
                                    fallback_runtime.syntax_valid and fallback_runtime.render_valid
                                ):
                                    reason = (
                                        "declared portable fallback also failed "
                                        "parse/render validation"
                                    )
                                elif fallback_runtime_type is None:
                                    reason = (
                                        "declared portable fallback did not report a supported "
                                        "terminal runtime type"
                                    )
                                else:
                                    reason = (
                                        "declared portable fallback reported terminal type "
                                        f"{fallback_runtime_type} instead of "
                                        f"{fallback.emitted_type}"
                                    )
                                candidate.warnings.append(reason)
                                candidate.repair_history.append(
                                    RepairEvent(
                                        iteration=0,
                                        operation="runtime_portable_fallback",
                                        accepted=False,
                                        details={
                                            **fallback_details,
                                            "syntax_valid": fallback_runtime.syntax_valid,
                                            "render_valid": fallback_runtime.render_valid,
                                            "runtime_diagram_type": (fallback_runtime.diagram_type),
                                            "error": fallback_runtime.error,
                                        },
                                    )
                                )
                except (SerializationError, SerializationContractError) as exc:
                    candidate.warnings.append(f"runtime fallback unavailable: {exc}")
                except Exception as exc:
                    failures.append(
                        CandidateFailure(
                            stage="runtime_fallback_serialization",
                            engine=draft.engine_name,
                            error_type=type(exc).__name__,
                            message=str(exc),
                        )
                    )
                    candidate.warnings.append(f"runtime fallback serialization failed: {exc}")
            if draft.method == "direct_mermaid" and runtime.render_valid:
                detected_type = _canonical_runtime_type(runtime.diagram_type)
                if detected_type is not None:
                    if not supports_accessibility_directives(detected_type):
                        candidate.warnings.append(accessibility_limitation_warning(detected_type))
                    augmented_code = augment_accessibility_directives(
                        candidate_code,
                        detected_type,
                        _scene_accessibility_ir(
                            draft.observation.scene_ir,
                            draft.observation.evidence,
                        ),
                        semantic_type=draft.diagram_type,
                        experimental=True,
                    )
                    if augmented_code is not None:
                        try:
                            augmented_code = _canonical_publication_source(augmented_code)
                            augmented_outcome = self.validator.validate(
                                augmented_code,
                                self.config.render_timeout_seconds,
                            )
                        except Exception as exc:
                            candidate.warnings.append(
                                f"direct accessibility augmentation failed validation: {exc}"
                            )
                        else:
                            augmented_type = _canonical_runtime_type(
                                augmented_outcome.runtime.diagram_type
                            )
                            if (
                                augmented_outcome.runtime.render_valid
                                and augmented_type == detected_type
                            ):
                                candidate_code = augmented_code
                                candidate.mermaid_code = augmented_code
                                runtime = augmented_outcome.runtime
                                certification_outcome = augmented_outcome
                                validation_warnings = list(
                                    dict.fromkeys(
                                        [
                                            *validation_warnings,
                                            *augmented_outcome.warnings,
                                        ]
                                    )
                                )
                                candidate.repair_history.append(
                                    RepairEvent(
                                        iteration=0,
                                        operation="augment_accessibility",
                                        accepted=True,
                                        details={
                                            "emitted_type": detected_type,
                                            "stage": "post_validation",
                                        },
                                    )
                                )
                            else:
                                candidate.warnings.append(
                                    "direct accessibility augmentation was discarded because "
                                    "revalidation failed or changed diagram type"
                                )
            candidate.syntax_valid = runtime.syntax_valid
            candidate.render_valid = runtime.render_valid
            candidate.svg = runtime.svg
            candidate.png = runtime.png
            candidate.runtime_diagram_type = runtime.diagram_type
            runtime_type = _canonical_runtime_type(runtime.diagram_type)
            contract_mismatch = bool(
                runtime.syntax_valid
                and runtime_type is not None
                and runtime_type != candidate.emitted_diagram_type
            )
            if contract_mismatch:
                candidate.warnings.append(
                    "serializer emitted type mismatch: "
                    f"declared {candidate.emitted_diagram_type}, runtime detected {runtime_type}"
                )
                if draft.method == "direct_mermaid":
                    candidate.emitted_diagram_type = runtime_type
                    candidate.fallback_chain = list(
                        dict.fromkeys([candidate.diagram_type, runtime_type])
                    )
                else:
                    candidate.render_valid = False
            candidate.warnings.extend(validation_warnings)
            if runtime.error:
                candidate.warnings.append(runtime.error)
            prediction_scores = dict(
                zip(
                    draft.observation.prediction.candidates,
                    draft.observation.prediction.scores,
                    strict=True,
                )
            )
            type_fitness = prediction_scores.get(draft.diagram_type)
            if contract_mismatch and type_fitness is not None:
                type_fitness = 0.0
            gate_diagram_type = _evaluation_gate_diagram_type(
                method=draft.method,
                semantic_type=draft.diagram_type,
                emitted_type=candidate.emitted_diagram_type,
                runtime_type=runtime_type,
            )
            evaluation = self._evaluate_candidate(
                code=candidate_code,
                runtime=runtime,
                syntax_valid=candidate.syntax_valid,
                render_valid=candidate.render_valid,
                semantic_diagram_type=draft.diagram_type,
                gate_diagram_type=gate_diagram_type,
                method=draft.method,
                typed_ir=draft.typed_ir,
                source_scene=draft.observation.scene_ir,
                evidence=(
                    [
                        item
                        for item in all_evidence
                        if item.id in candidate.publication_evidence_authority_ids
                        and item.id not in collided_evidence_ids
                    ]
                    if candidate.publication_evidence_authority_ids is not None
                    else all_evidence
                ),
                approved_user_edit_evidence_ids=approved_user_edit_evidence_ids,
                references=references,
                type_fitness=type_fitness,
                image=context.image,
            )
            candidate.scores = evaluation.scores
            candidate.aggregate_score = evaluation.aggregate_score
            candidate.generated_scene_ir = evaluation.generated_scene_ir
            candidate.warnings.extend(evaluation.warnings)
            self.validator.seal_candidate(candidate, certification_outcome)
            candidates.append(candidate)

        selected = self._select(candidates)
        if selected is not None and self.repair_engine is not None:
            context.image = _canonical_rgb_image_snapshot(repair_image)
            context.views = {
                name: _canonical_rgb_image_snapshot(view) for name, view in repair_views.items()
            }
            selected = self._repair(
                context,
                selected,
                approved_user_edit_evidence_ids,
            )
            if selected not in candidates:
                candidates.append(selected)
        if selected is not None:
            bounded_warnings: list[str] = []
            warnings_truncated = False
            for warning in selected.warnings:
                normalized = _sink_safe_diagnostic_text(warning)
                if len(normalized) > MAX_WARNING_CHARS:
                    normalized = normalized[: MAX_WARNING_CHARS - 1] + "…"
                    warnings_truncated = True
                if normalized in bounded_warnings:
                    continue
                if len(bounded_warnings) >= MAX_OBSERVATION_WARNINGS - 1:
                    warnings_truncated = True
                    break
                bounded_warnings.append(normalized)
            if warnings_truncated:
                bounded_warnings.append(
                    "candidate warnings were truncated to the publication metadata budget"
                )
            selected.warnings = bounded_warnings
        final_evidence_safe = True
        try:
            final_evidence_snapshot = canonical_evidence_collection_snapshot(
                all_evidence,
                item_limit=MAX_OBSERVATION_EVIDENCE,
                character_limit=MAX_VLM_EVIDENCE_INPUT_CHARS,
            )
            all_evidence = list(final_evidence_snapshot.evidence)
        except (TypeError, UnicodeEncodeError, ValueError) as exc:
            final_evidence_safe = False
            all_evidence = []
            failures.append(
                CandidateFailure(
                    stage="result",
                    engine="pipeline",
                    error_type=type(exc).__name__,
                    message=f"final evidence was isolated atomically: {exc}",
                )
            )
        decision = decide_publication(selected, self.config)
        if selected is None:
            status = "failed"
        elif not final_evidence_safe:
            status = "review_required"
        elif decision.publish or not decision.review_required:
            status = "success"
        else:
            status = "review_required"
        result = ReconstructionResult(
            source_id=source_id,
            source_image_name=source_image_name,
            source_kind=source_kind,
            source_block_ids=resolved_source_block_ids,
            page_ids=resolved_page_ids,
            anchor_block_id=anchor_block_id,
            source_mapping=resolved_source_mapping,
            selected=selected,
            alternatives=[item for item in candidates if item is not selected],
            evidence=all_evidence,
            failures=failures,
            prompt_budget_notices=prompt_budget_notices,
            grade=decision.grade,
            publish=decision.publish and final_evidence_safe,
            review_required=decision.review_required or not final_evidence_safe,
            status=status,
        )
        certified = certify_publication_result(result, self.config)
        if result.publish and not certified:
            result.publish = False
            result.review_required = True
            result.status = "review_required"
            warning = "automatic publication authorization failed; review is required"
            if selected is not None and warning not in selected.warnings:
                if len(selected.warnings) >= MAX_OBSERVATION_WARNINGS:
                    selected.warnings[-1] = warning
                else:
                    selected.warnings.append(warning)
        return result

    def _select(self, candidates: list[MermaidCandidate]) -> MermaidCandidate | None:
        eligible = [item for item in candidates if item.syntax_valid and item.render_valid]
        if not eligible:
            return None
        priority = {"typed_ir": 3, "scene_ir_fallback": 2, "direct_mermaid": 1}
        automatic_publication = self.config.publish_policy in {
            PublishPolicy.STRICT_VALIDATED,
            PublishPolicy.BEST_EFFORT_VALIDATED,
        }
        return max(
            eligible,
            key=lambda item: (
                int(decide_publication(item, self.config).publish) if automatic_publication else 0,
                int(item.has_validated_publication_artifacts()) if automatic_publication else 0,
                item.aggregate_score if item.aggregate_score is not None else -1,
                item.scores.get("ocr_recall", -1),
                priority.get(item.generation_method, 0),
                item.candidate_id,
            ),
        )

    def _evaluate_candidate(
        self,
        *,
        code: str,
        runtime: RuntimeResult,
        syntax_valid: bool,
        render_valid: bool,
        semantic_diagram_type: str,
        gate_diagram_type: str,
        method: str,
        typed_ir: dict | None,
        source_scene: DiagramSceneIR | None,
        evidence: list[VisualEvidence],
        approved_user_edit_evidence_ids: frozenset[str],
        references: _ReferenceTexts,
        type_fitness: float | None,
        image: Image.Image,
    ) -> _CandidateEvaluation:
        """Score initial and repaired candidates through one availability/gating path."""

        scores: dict[str, float] = {
            "syntax": float(syntax_valid),
            "render": float(render_valid),
        }
        warnings: list[str] = []
        if type_fitness is not None:
            scores["type_fitness"] = type_fitness
        if not syntax_valid or not render_valid:
            warnings.append("semantic scoring skipped because parse/render validation failed")
            return _CandidateEvaluation(scores, None, warnings, None)
        generated_scene = None
        generated_scene_failed = False
        if typed_ir is not None:
            try:
                generated_scene = typed_ir_to_scene(
                    semantic_diagram_type,
                    typed_ir,
                    emitted_diagram_type=runtime.diagram_type,
                )
            except Exception as exc:
                generated_scene_failed = True
                warnings.append(f"generated semantic scene conversion was isolated: {exc}")
        elif method == "scene_ir_fallback" and source_scene is not None:
            generated_scene = source_scene.model_copy(deep=True)
        generated_texts = None
        generated_texts_over_budget = False
        generated_text_projection_failed = False
        if generated_scene is not None:
            if typed_ir is not None:
                semantic_labels = typed_ir_semantic_texts(
                    semantic_diagram_type,
                    typed_ir,
                    generated_scene,
                    emitted_diagram_type=runtime.diagram_type,
                )
            else:
                semantic_labels = chain(
                    (element.text for element in generated_scene.elements if element.text),
                    (relation.label for relation in generated_scene.relations if relation.label),
                    (group.label for group in generated_scene.groups if group.label),
                )
            try:
                generated_texts = bounded_ocr_token_multiset(
                    semantic_labels,
                    max_texts=_MAX_OCR_REFERENCE_TEXTS,
                    max_chars=_MAX_OCR_REFERENCE_CHARS,
                    max_tokens=_MAX_OCR_REFERENCE_TOKENS,
                )
            except Exception as exc:
                generated_text_projection_failed = True
                warnings.append(f"generated semantic text projection was isolated: {exc}")
            if generated_texts is None and not generated_text_projection_failed:
                generated_texts_over_budget = True
                warnings.append(
                    "generated semantic labels exceed the scoring budget; review is required"
                )
        recall = (
            ocr_recall(references.ocr_tokens, code, generated_texts=generated_texts)
            if references.ocr_tokens is not None
            and not generated_texts_over_budget
            and not generated_scene_failed
            and not generated_text_projection_failed
            else None
        )
        if recall is not None:
            scores["ocr_recall"] = recall
        packet_binding_state: Literal["exact", "mismatch", "unavailable"] | None = None
        pie_binding_state: Literal["exact", "mismatch", "unavailable"] | None = None
        pie_title_attribution_state: Literal["exact", "unavailable"] | None = None
        pie_description_attribution_state: Literal["exact", "unavailable"] | None = None
        if gate_diagram_type == "packet":
            # Packet ranges need a field-local proof.  A document-wide number multiset can
            # stay identical when two labels exchange their ranges, so it is not publication
            # authority for this semantic type.
            packet_binding_state = "unavailable"
            packet_fields = ()
            if typed_ir is not None:
                try:
                    packet_fields = plan_packet_fields(typed_ir).fields
                except SerializationError:
                    packet_fields = ()
            if packet_fields:
                image_width, image_height = image.size
                field_boxes: dict[str, tuple[float, float, float, float]] = {}
                spatial_evidence_safe = True
                for field in packet_fields:
                    raw_bbox = field.source_record.get("bbox")
                    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
                        spatial_evidence_safe = False
                        break
                    bbox = tuple(float(value) for value in raw_bbox)
                    x1, y1, x2, y2 = bbox
                    if (
                        x2 <= x1
                        or y2 <= y1
                        or x1 < 0
                        or y1 < 0
                        or x2 > image_width
                        or y2 > image_height
                    ):
                        spatial_evidence_safe = False
                        break
                    field_boxes[field.source_id] = bbox

                if spatial_evidence_safe:
                    overlap_comparisons = 0
                    ordered_fields = sorted(
                        field_boxes.items(), key=lambda item: (item[1][0], item[1][2], item[0])
                    )
                    for index, (_field_id, bbox) in enumerate(ordered_fields):
                        for other_index in range(index + 1, len(ordered_fields)):
                            _other_id, other_bbox = ordered_fields[other_index]
                            if other_bbox[0] >= bbox[2]:
                                break
                            overlap_comparisons += 1
                            if overlap_comparisons > _MAX_PACKET_FIELD_OVERLAP_COMPARISONS or (
                                other_bbox[1] < bbox[3] and other_bbox[3] > bbox[1]
                            ):
                                spatial_evidence_safe = False
                                break
                        if not spatial_evidence_safe:
                            break

                evidence_by_id: dict[str, VisualEvidence] = {}
                authorized_texts_by_bbox: dict[tuple[float, float, float, float], set[str]] = {}
                if spatial_evidence_safe:
                    for item in evidence:
                        if item.id in evidence_by_id:
                            spatial_evidence_safe = False
                            break
                        evidence_by_id[item.id] = item
                    authorized_text_count = sum(
                        1
                        for item in evidence
                        if item.kind in {"ocr_token", "vector_text"}
                        and item.text
                        and item.bbox is not None
                    )
                    authorized_text_chars = sum(
                        len(item.text)
                        for item in evidence
                        if item.kind in {"ocr_token", "vector_text"}
                        and item.text
                        and item.bbox is not None
                    ) + sum(len(field.label) for field in packet_fields)
                    if (
                        authorized_text_count + len(packet_fields) > _MAX_OCR_REFERENCE_TEXTS
                        or authorized_text_chars > _MAX_OCR_REFERENCE_CHARS
                    ):
                        spatial_evidence_safe = False
                    else:
                        for item in evidence:
                            if (
                                item.kind not in {"ocr_token", "vector_text"}
                                or not item.text
                                or item.bbox is None
                            ):
                                continue
                            normalized_text = (
                                unicodedata.normalize("NFKC", item.text).casefold().strip()
                            )
                            if not normalized_text:
                                continue
                            authorized_texts_by_bbox.setdefault(
                                tuple(float(value) for value in item.bbox), set()
                            ).add(normalized_text)

                field_reference_ids: dict[str, tuple[str, ...]] = {}
                if spatial_evidence_safe:
                    reference_count = 0
                    for field in packet_fields:
                        raw_evidence_ids = field.source_record.get("evidence_ids") or []
                        if not isinstance(raw_evidence_ids, list) or any(
                            not isinstance(evidence_id, str) for evidence_id in raw_evidence_ids
                        ):
                            spatial_evidence_safe = False
                            break
                        reference_count += len(raw_evidence_ids)
                        if reference_count > _MAX_PACKET_ASSOCIATION_REFERENCES:
                            spatial_evidence_safe = False
                            break
                        field_reference_ids[field.source_id] = tuple(
                            dict.fromkeys(raw_evidence_ids)
                        )

                observation_texts: dict[tuple[str, tuple[float, float, float, float]], str] = {}
                field_observations: dict[
                    str, set[tuple[str, tuple[float, float, float, float]]]
                ] = {field.source_id: set() for field in packet_fields}
                observation_owners: dict[tuple[str, tuple[float, float, float, float]], str] = {}
                evidence_id_owners: dict[str, str] = {}
                if spatial_evidence_safe:
                    for field in packet_fields:
                        field_bbox = field_boxes[field.source_id]
                        for evidence_id in field_reference_ids[field.source_id]:
                            item = evidence_by_id.get(evidence_id)
                            if item is None:
                                spatial_evidence_safe = False
                                break
                            if item.kind not in {"ocr_token", "vector_text"}:
                                continue
                            if not item.text or item.bbox is None:
                                spatial_evidence_safe = False
                                break
                            evidence_bbox = tuple(float(value) for value in item.bbox)
                            ex1, ey1, ex2, ey2 = evidence_bbox
                            if (
                                ex2 <= ex1
                                or ey2 <= ey1
                                or ex1 < 0
                                or ey1 < 0
                                or ex2 > image_width
                                or ey2 > image_height
                                or ex1 < field_bbox[0]
                                or ey1 < field_bbox[1]
                                or ex2 > field_bbox[2]
                                or ey2 > field_bbox[3]
                            ):
                                spatial_evidence_safe = False
                                break
                            normalized_text = (
                                unicodedata.normalize("NFKC", item.text).casefold().strip()
                            )
                            if not normalized_text:
                                spatial_evidence_safe = False
                                break
                            if len(authorized_texts_by_bbox.get(evidence_bbox, set())) != 1:
                                spatial_evidence_safe = False
                                break
                            previous_id_owner = evidence_id_owners.get(evidence_id)
                            if (
                                previous_id_owner is not None
                                and previous_id_owner != field.source_id
                            ):
                                spatial_evidence_safe = False
                                break
                            observation_key = (normalized_text, evidence_bbox)
                            previous_owner = observation_owners.get(observation_key)
                            if previous_owner is not None and previous_owner != field.source_id:
                                spatial_evidence_safe = False
                                break
                            evidence_id_owners[evidence_id] = field.source_id
                            observation_owners[observation_key] = field.source_id
                            observation_texts.setdefault(observation_key, item.text)
                            field_observations[field.source_id].add(observation_key)
                        if not spatial_evidence_safe:
                            break

                if spatial_evidence_safe and all(field_observations.values()):
                    association_text_count = len(packet_fields) + len(observation_texts)
                    association_char_count = sum(len(field.label) for field in packet_fields) + sum(
                        len(text) for text in observation_texts.values()
                    )
                    if (
                        association_text_count <= _MAX_OCR_REFERENCE_TEXTS
                        and association_char_count <= _MAX_OCR_REFERENCE_CHARS
                    ):
                        token_count = 0
                        label_tokens: dict[str, Counter[str]] = {}
                        observation_tokens: dict[
                            tuple[str, tuple[float, float, float, float]], Counter[str]
                        ] = {}
                        for field in packet_fields:
                            tokens = ocr_token_multiset((field.label,))
                            token_count += tokens.total()
                            label_tokens[field.source_id] = tokens
                        for observation_key, text in observation_texts.items():
                            tokens = ocr_token_multiset((text,))
                            token_count += tokens.total()
                            observation_tokens[observation_key] = tokens
                        if token_count <= _MAX_OCR_REFERENCE_TOKENS and all(label_tokens.values()):
                            association_available = True
                            association_matches = True
                            for field in packet_fields:
                                observed_tokens: Counter[str] = Counter()
                                observed_numbers: Counter[str] = Counter()
                                for observation_key in field_observations[field.source_id]:
                                    observed_tokens.update(observation_tokens[observation_key])
                                    observed_numbers.update(
                                        numeric_token_multiset(
                                            (observation_texts[observation_key],)
                                        )
                                    )
                                if (
                                    label_tokens[field.source_id] - observed_tokens
                                    or not observed_numbers
                                ):
                                    association_available = False
                                    break
                                expected_numbers = numeric_token_multiset((field.label,))
                                expected_numbers.update((str(field.start),))
                                if field.end != field.start:
                                    expected_numbers.update((str(field.end),))
                                if observed_numbers != expected_numbers:
                                    association_matches = False
                            if association_available:
                                packet_binding_state = (
                                    "exact" if association_matches else "mismatch"
                                )
            numeric = (
                1.0
                if packet_binding_state == "exact"
                else 0.0
                if packet_binding_state == "mismatch"
                else None
            )
        elif gate_diagram_type == "pie":
            # A document-wide number multiset cannot prove which value belongs to which
            # slice. Require each typed slice to own non-overlapping source geometry and
            # candidate-authorized OCR/vector observations that jointly bind its label and
            # exact value. Direct Pie remains review-only because it has no typed slots.
            pie_binding_state = "unavailable"
            pie_plan = None
            pie_slices = ()
            if typed_ir is not None:
                try:
                    pie_plan = plan_pie_records(typed_ir)
                    pie_slices = pie_plan.slices
                except SerializationError:
                    pie_slices = ()
            if pie_slices:
                image_width, image_height = image.size
                slice_boxes: dict[str, tuple[float, float, float, float]] = {}
                spatial_evidence_safe = True
                for slice_plan in pie_slices:
                    raw_bbox = slice_plan.source_record.get("bbox")
                    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
                        spatial_evidence_safe = False
                        break
                    try:
                        bbox = tuple(float(value) for value in raw_bbox)
                    except (TypeError, ValueError, OverflowError):
                        spatial_evidence_safe = False
                        break
                    x1, y1, x2, y2 = bbox
                    if (
                        not all(math.isfinite(value) for value in bbox)
                        or x2 <= x1
                        or y2 <= y1
                        or x1 < 0
                        or y1 < 0
                        or x2 > image_width
                        or y2 > image_height
                    ):
                        spatial_evidence_safe = False
                        break
                    slice_boxes[slice_plan.scene_id] = bbox

                if spatial_evidence_safe:
                    overlap_comparisons = 0
                    ordered_slices = sorted(
                        slice_boxes.items(), key=lambda item: (item[1][0], item[1][2], item[0])
                    )
                    for index, (_slice_id, bbox) in enumerate(ordered_slices):
                        for other_index in range(index + 1, len(ordered_slices)):
                            _other_id, other_bbox = ordered_slices[other_index]
                            if other_bbox[0] >= bbox[2]:
                                break
                            overlap_comparisons += 1
                            if overlap_comparisons > _MAX_PIE_SLICE_OVERLAP_COMPARISONS or (
                                other_bbox[1] < bbox[3] and other_bbox[3] > bbox[1]
                            ):
                                spatial_evidence_safe = False
                                break
                        if not spatial_evidence_safe:
                            break

                evidence_by_id: dict[str, VisualEvidence] = {}
                authorized_texts_by_bbox: dict[tuple[float, float, float, float], set[str]] = {}
                if spatial_evidence_safe:
                    for item in evidence:
                        if item.id in evidence_by_id:
                            spatial_evidence_safe = False
                            break
                        evidence_by_id[item.id] = item
                    authorized_text_count = sum(
                        1
                        for item in evidence
                        if item.kind in {"ocr_token", "vector_text"}
                        and item.text
                        and item.bbox is not None
                    )
                    authorized_text_chars = sum(
                        len(item.text)
                        for item in evidence
                        if item.kind in {"ocr_token", "vector_text"}
                        and item.text
                        and item.bbox is not None
                    ) + sum(len(slice_plan.label) for slice_plan in pie_slices)
                    if (
                        authorized_text_count + len(pie_slices) > _MAX_OCR_REFERENCE_TEXTS
                        or authorized_text_chars > _MAX_OCR_REFERENCE_CHARS
                    ):
                        spatial_evidence_safe = False
                    else:
                        for item in evidence:
                            if (
                                item.kind not in {"ocr_token", "vector_text"}
                                or not item.text
                                or item.bbox is None
                            ):
                                continue
                            try:
                                evidence_bbox = tuple(float(value) for value in item.bbox)
                            except (TypeError, ValueError, OverflowError):
                                spatial_evidence_safe = False
                                break
                            normalized_text = (
                                unicodedata.normalize("NFKC", item.text).casefold().strip()
                            )
                            if not normalized_text:
                                continue
                            authorized_texts_by_bbox.setdefault(evidence_bbox, set()).add(
                                normalized_text
                            )

                slice_reference_ids: dict[str, tuple[str, ...]] = {}
                if spatial_evidence_safe:
                    reference_count = 0
                    for slice_plan in pie_slices:
                        raw_evidence_ids = slice_plan.source_record.get("evidence_ids") or []
                        if not isinstance(raw_evidence_ids, list) or any(
                            not isinstance(evidence_id, str) for evidence_id in raw_evidence_ids
                        ):
                            spatial_evidence_safe = False
                            break
                        reference_count += len(raw_evidence_ids)
                        if reference_count > _MAX_PIE_ASSOCIATION_REFERENCES:
                            spatial_evidence_safe = False
                            break
                        slice_reference_ids[slice_plan.scene_id] = tuple(
                            dict.fromkeys(raw_evidence_ids)
                        )

                observation_texts: dict[
                    tuple[str, tuple[float, float, float, float]], str
                ] = {}
                slice_observations: dict[
                    str, set[tuple[str, tuple[float, float, float, float]]]
                ] = {slice_plan.scene_id: set() for slice_plan in pie_slices}
                observation_owners: dict[
                    tuple[str, tuple[float, float, float, float]], str
                ] = {}
                evidence_id_owners: dict[str, str] = {}
                if spatial_evidence_safe:
                    for slice_plan in pie_slices:
                        slice_bbox = slice_boxes[slice_plan.scene_id]
                        for evidence_id in slice_reference_ids[slice_plan.scene_id]:
                            item = evidence_by_id.get(evidence_id)
                            if item is None:
                                spatial_evidence_safe = False
                                break
                            if item.kind not in {"ocr_token", "vector_text"}:
                                continue
                            if not item.text or item.bbox is None:
                                spatial_evidence_safe = False
                                break
                            evidence_bbox = tuple(float(value) for value in item.bbox)
                            ex1, ey1, ex2, ey2 = evidence_bbox
                            if (
                                not all(math.isfinite(value) for value in evidence_bbox)
                                or ex2 <= ex1
                                or ey2 <= ey1
                                or ex1 < 0
                                or ey1 < 0
                                or ex2 > image_width
                                or ey2 > image_height
                                or ex1 < slice_bbox[0]
                                or ey1 < slice_bbox[1]
                                or ex2 > slice_bbox[2]
                                or ey2 > slice_bbox[3]
                            ):
                                spatial_evidence_safe = False
                                break
                            normalized_text = (
                                unicodedata.normalize("NFKC", item.text).casefold().strip()
                            )
                            if (
                                not normalized_text
                                or len(authorized_texts_by_bbox.get(evidence_bbox, set())) != 1
                            ):
                                spatial_evidence_safe = False
                                break
                            previous_id_owner = evidence_id_owners.get(evidence_id)
                            if (
                                previous_id_owner is not None
                                and previous_id_owner != slice_plan.scene_id
                            ):
                                spatial_evidence_safe = False
                                break
                            observation_key = (normalized_text, evidence_bbox)
                            previous_owner = observation_owners.get(observation_key)
                            if previous_owner is not None and previous_owner != slice_plan.scene_id:
                                spatial_evidence_safe = False
                                break
                            evidence_id_owners[evidence_id] = slice_plan.scene_id
                            observation_owners[observation_key] = slice_plan.scene_id
                            observation_texts.setdefault(observation_key, item.text)
                            slice_observations[slice_plan.scene_id].add(observation_key)
                        if not spatial_evidence_safe:
                            break

                if spatial_evidence_safe and all(slice_observations.values()):
                    association_text_count = len(pie_slices) + len(observation_texts)
                    association_char_count = sum(
                        len(slice_plan.label) for slice_plan in pie_slices
                    ) + sum(len(text) for text in observation_texts.values())
                    if (
                        association_text_count <= _MAX_OCR_REFERENCE_TEXTS
                        and association_char_count <= _MAX_OCR_REFERENCE_CHARS
                    ):
                        token_count = 0
                        label_tokens: dict[str, Counter[str]] = {}
                        observation_tokens: dict[
                            tuple[str, tuple[float, float, float, float]], Counter[str]
                        ] = {}
                        for slice_plan in pie_slices:
                            tokens = ocr_token_multiset((slice_plan.label,))
                            token_count += tokens.total()
                            label_tokens[slice_plan.scene_id] = tokens
                        for observation_key, text_value in observation_texts.items():
                            tokens = ocr_token_multiset((text_value,))
                            token_count += tokens.total()
                            observation_tokens[observation_key] = tokens
                        if token_count <= _MAX_OCR_REFERENCE_TOKENS and all(
                            label_tokens.values()
                        ):
                            association_available = True
                            association_matches = True
                            for slice_plan in pie_slices:
                                ordered_observation_keys = sorted(
                                    slice_observations[slice_plan.scene_id],
                                    key=lambda item: (
                                        item[1][1],
                                        item[1][0],
                                        item[1][3],
                                        item[1][2],
                                        item[0],
                                    ),
                                )
                                canonical_observation = _normalized_label(
                                    " ".join(
                                        observation_texts[observation_key]
                                        for observation_key in ordered_observation_keys
                                    )
                                )
                                canonical_label = _normalized_label(slice_plan.label)
                                observed_tokens: Counter[str] = Counter()
                                observed_numbers: Counter[str] = Counter()
                                for observation_key in ordered_observation_keys:
                                    observed_tokens.update(observation_tokens[observation_key])
                                    observed_numbers.update(
                                        numeric_token_multiset(
                                            (observation_texts[observation_key],)
                                        )
                                    )
                                allowed_observations: set[str] = set()
                                for observed_number in observed_numbers:
                                    canonical_number = _normalized_label(observed_number)
                                    allowed_observations.update(
                                        {
                                            f"{canonical_label} {canonical_number}",
                                            f"{canonical_label}: {canonical_number}",
                                            f"{canonical_label}:{canonical_number}",
                                            f"{canonical_label} = {canonical_number}",
                                            f"{canonical_label}={canonical_number}",
                                            f"{canonical_label} [{canonical_number}]",
                                            f"{canonical_label}[{canonical_number}]",
                                            f"{canonical_label} ({canonical_number})",
                                            f"{canonical_label}({canonical_number})",
                                            f"{canonical_number} {canonical_label}",
                                        }
                                    )
                                label_is_bound = canonical_observation in allowed_observations
                                if (
                                    label_tokens[slice_plan.scene_id] - observed_tokens
                                    or not label_is_bound
                                    or not observed_numbers
                                ):
                                    association_available = False
                                    break
                                expected_numbers = numeric_token_multiset(
                                    (slice_plan.label, slice_plan.value_text)
                                )
                                if observed_numbers != expected_numbers:
                                    association_matches = False
                            if association_available:
                                pie_binding_state = (
                                    "exact" if association_matches else "mismatch"
                                )
            global_pie_numeric = numeric_consistency(references.numeric_tokens, code)
            if pie_binding_state == "exact" and global_pie_numeric != 1.0:
                pie_binding_state = (
                    "mismatch" if global_pie_numeric is not None else "unavailable"
                )
            if pie_plan is not None and typed_ir is not None:
                accessibility_base_ir = {
                    key: value
                    for key, value in typed_ir.items()
                    if key not in {"acc_title", "acc_description"}
                }
                derived_accessibility = resolve_accessibility(
                    accessibility_base_ir,
                    "pie",
                    experimental=self.config.mode != Mode.STRICT,
                )
                required_title_texts: set[str] = set()
                if pie_plan.semantic_title is not None:
                    required_title_texts.add(_normalized_label(pie_plan.semantic_title))
                accessibility_title = typed_ir.get("acc_title")
                if (
                    isinstance(accessibility_title, str)
                    and _normalized_label(accessibility_title)
                    != _normalized_label(derived_accessibility.title)
                ):
                    required_title_texts.add(_normalized_label(accessibility_title))
                required_description_texts: set[str] = set()
                explicit_description = typed_ir.get("description")
                if isinstance(explicit_description, str) and explicit_description:
                    required_description_texts.add(_normalized_label(explicit_description))
                accessibility_description = typed_ir.get("acc_description")
                accessibility_description_text = (
                    accessibility_description.removesuffix(
                        f" {EXPERIMENTAL_NOTICE}"
                    ).removesuffix(EXPERIMENTAL_NOTICE)
                    if isinstance(accessibility_description, str)
                    else None
                )
                derived_description_text = derived_accessibility.description.removesuffix(
                    f" {EXPERIMENTAL_NOTICE}"
                ).removesuffix(EXPERIMENTAL_NOTICE)
                if (
                    accessibility_description_text
                    and _normalized_label(accessibility_description_text)
                    != _normalized_label(derived_description_text)
                ):
                    required_description_texts.add(
                        _normalized_label(accessibility_description_text)
                    )
                if required_title_texts:
                    pie_title_attribution_state = "unavailable"
                if required_description_texts:
                    pie_description_attribution_state = "unavailable"
                slice_evidence_ids = {
                    evidence_id
                    for slice_plan in pie_plan.slices
                    for evidence_id in slice_plan.evidence_ids
                }
                slice_owned_observations: set[
                    tuple[str, tuple[float, float, float, float]]
                ] = set()
                for item in evidence:
                    if (
                        item.id not in slice_evidence_ids
                        or item.kind not in {"ocr_token", "vector_text"}
                        or not item.text
                        or item.bbox is None
                    ):
                        continue
                    normalized_text = _normalized_label(item.text)
                    if normalized_text:
                        slice_owned_observations.add(
                            (normalized_text, tuple(float(value) for value in item.bbox))
                        )
                title_exclusion_boxes: list[tuple[float, float, float, float]] = []
                for slice_plan in pie_plan.slices:
                    raw_bbox = slice_plan.source_record.get("bbox")
                    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
                        continue
                    slice_bbox = tuple(float(value) for value in raw_bbox)
                    if all(math.isfinite(value) for value in slice_bbox):
                        title_exclusion_boxes.append(slice_bbox)
                accessibility_texts_by_bbox: dict[
                    tuple[float, float, float, float], set[str]
                ] = {}
                for item in evidence:
                    if (
                        item.kind not in {"ocr_token", "vector_text"}
                        or not item.text
                        or item.bbox is None
                    ):
                        continue
                    evidence_bbox = tuple(float(value) for value in item.bbox)
                    normalized_text = _normalized_label(item.text)
                    if normalized_text:
                        accessibility_texts_by_bbox.setdefault(evidence_bbox, set()).add(
                            normalized_text
                        )
                proven_title_texts: set[str] = set()
                proven_description_texts: set[str] = set()
                image_width, image_height = image.size
                for item in evidence:
                    if (
                        item.id in slice_evidence_ids
                        or not item.text
                    ):
                        continue
                    normalized_text = _normalized_label(item.text)
                    if item.kind == "user_edit":
                        if item.id not in approved_user_edit_evidence_ids:
                            continue
                        if normalized_text in required_title_texts:
                            proven_title_texts.add(normalized_text)
                        if normalized_text in required_description_texts:
                            proven_description_texts.add(normalized_text)
                        continue
                    if item.kind not in {"ocr_token", "vector_text"} or item.bbox is None:
                        continue
                    evidence_bbox = tuple(float(value) for value in item.bbox)
                    x1, y1, x2, y2 = evidence_bbox
                    if (
                        not all(math.isfinite(value) for value in evidence_bbox)
                        or x2 <= x1
                        or y2 <= y1
                        or x1 < 0
                        or y1 < 0
                        or x2 > image_width
                        or y2 > image_height
                        or len(accessibility_texts_by_bbox.get(evidence_bbox, set())) != 1
                        or (normalized_text, evidence_bbox) in slice_owned_observations
                        or any(
                            evidence_bbox[0] < slice_bbox[2]
                            and evidence_bbox[2] > slice_bbox[0]
                            and evidence_bbox[1] < slice_bbox[3]
                            and evidence_bbox[3] > slice_bbox[1]
                            for slice_bbox in title_exclusion_boxes
                        )
                    ):
                        continue
                    if normalized_text in required_title_texts:
                        proven_title_texts.add(normalized_text)
                    if normalized_text in required_description_texts:
                        proven_description_texts.add(normalized_text)
                if required_title_texts and proven_title_texts == required_title_texts:
                    pie_title_attribution_state = "exact"
                if (
                    required_description_texts
                    and proven_description_texts == required_description_texts
                ):
                    pie_description_attribution_state = "exact"
            numeric = (
                1.0
                if pie_binding_state == "exact"
                else 0.0
                if pie_binding_state == "mismatch"
                else None
            )
        else:
            numeric = numeric_consistency(references.numeric_tokens, code)
        if numeric is not None and gate_diagram_type in _NUMERIC_TYPES:
            scores["numeric_consistency"] = numeric
        provenance = _generated_node_provenance_score(
            generated_scene,
            source_scene,
            evidence,
        )
        if provenance is not None:
            scores["visual_entailment_precision"] = provenance
            if provenance < 0.8:
                warnings.append("more than 20% of generated nodes lack provenance")

        structural_edge_available = False
        if source_scene is not None and generated_scene is not None:
            structural_metrics = []
            if self.config.enable_render_compare:
                structural_metrics.append(edge_topology_agreement(source_scene, generated_scene))
            if self.config.enable_reference_free_scoring:
                structural_metrics.extend(
                    [
                        arrow_agreement(source_scene, generated_scene),
                        relative_layout_similarity(source_scene, generated_scene),
                    ]
                )
            if self.config.enable_path_scoring:
                structural_metrics.append(path_consistency(source_scene, generated_scene))
            for metric in structural_metrics:
                if metric.available and metric.value is not None:
                    scores[metric.name] = metric.value
                    if metric.name == "edge_agreement":
                        structural_edge_available = True
        if self.config.enable_render_compare and not structural_edge_available:
            edge = _edge_iou(image, runtime.png)
            if edge is not None:
                scores["edge_agreement"] = edge

        aggregate = aggregate_scores(scores, self.config)
        if (
            generated_texts_over_budget
            or generated_scene_failed
            or generated_text_projection_failed
        ):
            aggregate = None
        if references.warning is not None:
            aggregate = None
            warnings.append(references.warning)
        if self.config.mode != Mode.STRICT and gate_diagram_type in _PROVENANCE_GATED_TYPES:
            if provenance is None:
                aggregate = None
                warnings.append("generated-node attribution is unavailable; review is required")
            elif provenance < 0.8:
                aggregate = None
                warnings.append("generated-node provenance gate requires at least 80% attribution")
        if (
            gate_diagram_type == "cynefin"
            and _canonical_runtime_type(runtime.diagram_type) == "cynefin"
        ):
            aggregate = None
            warnings.append(_CYNEFIN_TEMPLATE_REVIEW_WARNING)
        if gate_diagram_type == "packet" and packet_binding_state == "unavailable":
            aggregate = None
            warnings.append(_PACKET_NUMERIC_ASSOCIATION_UNAVAILABLE_WARNING)
        elif gate_diagram_type == "packet" and packet_binding_state == "mismatch":
            aggregate = None
            warnings.append(_PACKET_NUMERIC_ASSOCIATION_MISMATCH_WARNING)
        elif gate_diagram_type == "pie" and pie_binding_state == "unavailable":
            aggregate = None
            warnings.append(_PIE_NUMERIC_ASSOCIATION_UNAVAILABLE_WARNING)
        elif gate_diagram_type == "pie" and pie_binding_state == "mismatch":
            aggregate = None
            warnings.append(_PIE_NUMERIC_ASSOCIATION_MISMATCH_WARNING)
        elif gate_diagram_type in _NUMERIC_TYPES and numeric is None:
            aggregate = None
            warnings.append(
                "numeric diagram lacks OCR/vector numeric evidence and cannot auto-publish"
            )
        elif (
            gate_diagram_type in _NUMERIC_TYPES
            and numeric is not None
            and numeric < self.config.publish_min_score
        ):
            aggregate = None
            warnings.append("numeric consistency is below the automatic publication threshold")
        if gate_diagram_type == "pie" and pie_title_attribution_state == "unavailable":
            aggregate = None
            warnings.append(_PIE_TITLE_ATTRIBUTION_UNAVAILABLE_WARNING)
        if gate_diagram_type == "pie" and pie_description_attribution_state == "unavailable":
            aggregate = None
            warnings.append(_PIE_DESCRIPTION_ATTRIBUTION_UNAVAILABLE_WARNING)
        if (
            source_scene is not None
            and not any(element.text for element in source_scene.elements)
            and method == "scene_ir_fallback"
        ):
            aggregate = None
            warnings.append(
                "unlabeled scene-only candidates require OCR/VLM fusion before publishing"
            )
        return _CandidateEvaluation(scores, aggregate, warnings, generated_scene)

    def _repair(
        self,
        context: SourceContext,
        selected: MermaidCandidate,
        approved_user_edit_evidence_ids: frozenset[str],
    ) -> MermaidCandidate:
        current = selected
        references = _reference_text_sets(context.ocr_texts, context.evidence)
        for iteration in range(1, int(self.config.max_repair_iterations or 0) + 1):
            try:
                canonical_current_ir = canonical_typed_ir_snapshot(current.typed_ir)
                if current.typed_ir is not None:
                    if canonical_current_ir is None:
                        raise TypeError("repair candidate typed IR must be a JSON object")
                    current_ir_candidate = TypedIRCandidate.model_validate(
                        {
                            "diagram_type": current.diagram_type,
                            "ir": canonical_current_ir,
                            "confidence": 0.5,
                        }
                    )
                    canonical_current_ir = canonical_typed_ir_snapshot(current_ir_candidate.ir)
                current_snapshot = current.model_copy(deep=False)
                current_snapshot.typed_ir = canonical_current_ir
                current = current_snapshot.model_copy(deep=True)
            except Exception as exc:
                failed = current.model_copy(deep=False)
                failed.typed_ir = None
                failed.warnings = [
                    *list(current.warnings),
                    f"repair candidate typed IR validation failed: {type(exc).__name__}",
                ]
                return failed
            try:
                evidence_authority = current.publication_evidence_authority_ids
                scoped_evidence = (
                    context.evidence
                    if evidence_authority is None
                    else [item for item in context.evidence if item.id in evidence_authority]
                )
                trusted_label_ids = set(context.trusted_label_evidence_ids)
                trusted_connector_ids = set(context.trusted_connector_evidence_ids)
                if evidence_authority is not None:
                    trusted_label_ids.intersection_update(evidence_authority)
                    trusted_connector_ids.intersection_update(evidence_authority)
                source_mapping = canonical_source_mapping_snapshot(context.source_mapping)
                repair_image = _canonical_rgb_image_snapshot(context.image)
                if (
                    type(context.views) is not dict
                    or not context.views
                    or len(context.views) > self.config.max_views
                ):
                    raise ValueError("repair views must be a bounded plain dictionary")
                repair_views: dict[str, Image.Image] = {}
                repair_view_pixels = 0
                for name, view in context.views.items():
                    if type(name) is not str or not name:
                        raise ValueError("repair view names must be non-empty plain strings")
                    view_snapshot = _canonical_rgb_image_snapshot(view)
                    repair_view_pixels += view_snapshot.width * view_snapshot.height
                    if repair_view_pixels > MAX_VLM_TOTAL_VIEW_PIXELS:
                        raise ValueError("repair views exceed the aggregate pixel boundary")
                    repair_views[name] = view_snapshot
                # Opaque Marker blocks and vector-provider objects are intentionally not
                # forwarded: the semantic repair contract uses the isolated source snapshot.
                repair_context = SourceContext(
                    source_id=context.source_id,
                    source_block_ids=list(context.source_block_ids),
                    source_image_name=context.source_image_name,
                    image=repair_image,
                    views=repair_views,
                    evidence=[
                        VisualEvidence.model_validate(
                            {
                                "id": item.id,
                                "kind": item.kind,
                                "bbox": item.bbox,
                                "text": item.text,
                                "font_weight": item.font_weight,
                                "score": item.score,
                                "source_block_ids": item.source_block_ids[:],
                            }
                        )
                        for item in scoped_evidence
                    ],
                    trusted_label_evidence_ids=trusted_label_ids,
                    trusted_connector_evidence_ids=trusted_connector_ids,
                    trusted_connector_relations={
                        (source_id, target_id, frozenset(evidence_ids))
                        for source_id, target_id, evidence_ids in (
                            context.trusted_connector_relations
                        )
                        if evidence_authority is None or evidence_ids.issubset(evidence_authority)
                    },
                    conflicted_connector_pairs={
                        frozenset(pair) for pair in context.conflicted_connector_pairs
                    },
                    ocr_texts=list(context.ocr_texts),
                    source_mapping=source_mapping,
                )
                repair_candidate = current.model_copy(deep=True)
                proposal = self.repair_engine.repair(  # type: ignore[union-attr]
                    repair_context,
                    repair_candidate,
                )
            except Exception as exc:
                failed = current.model_copy(deep=True)
                failed.warnings.append(f"repair engine failed: {exc}")
                return failed
            if proposal is None:
                break
            if not isinstance(proposal, RepairProposal):
                failed = current.model_copy(deep=True)
                failed.warnings.append("repair engine returned an invalid structured proposal")
                return failed
            if type(proposal.code) is not str or not proposal.code or proposal.typed_ir is None:
                break
            proposal_code = _canonical_publication_source(proposal.code)
            if proposal_code == current.mermaid_code:
                break
            attempted = current.model_copy(deep=True)
            attempted.candidate_id = f"{selected.candidate_id}-repair-{iteration}"
            try:
                proposal_ir_snapshot = canonical_typed_ir_snapshot(proposal.typed_ir)
                if proposal_ir_snapshot is None:
                    raise TypeError("semantic repair typed IR must be a JSON object")
                proposal_candidate = TypedIRCandidate.model_validate(
                    {
                        "diagram_type": current.diagram_type,
                        "ir": proposal_ir_snapshot,
                        "confidence": 0.5,
                    }
                )
                validated_ir = canonical_typed_ir_snapshot(proposal_candidate.ir)
                if validated_ir is None:  # pragma: no cover - model contract guard
                    raise TypeError("validated semantic repair typed IR must be an object")
                if current.node_id_mappings:
                    proposed_nodes = validated_ir.get("nodes")
                    proposed_node_ids = (
                        [node.get("id") for node in proposed_nodes]
                        if isinstance(proposed_nodes, list)
                        and all(isinstance(node, dict) for node in proposed_nodes)
                        else []
                    )
                    if (
                        not all(
                            isinstance(node_id, str) and node_id for node_id in proposed_node_ids
                        )
                        or len(proposed_node_ids) != len(set(proposed_node_ids))
                        or set(proposed_node_ids)
                        != {item.fused_id for item in current.node_id_mappings}
                    ):
                        raise ValueError(
                            "semantic repair cannot change a provenance-mapped node set"
                        )
                canonical = serialize_typed_ir_result(
                    current.diagram_type,
                    validated_ir,
                    experimental=self.config.mode != Mode.STRICT,
                )
            except Exception as exc:
                repair_ir_detail = (
                    "semantic repair cannot change a provenance-mapped node set"
                    if type(exc) is ValueError
                    and exc.args == ("semantic repair cannot change a provenance-mapped node set",)
                    else type(exc).__name__
                )
                attempted.warnings.append(
                    f"semantic repair IR could not be serialized: {repair_ir_detail}"
                )
                attempted.repair_history.append(
                    RepairEvent(
                        iteration=iteration,
                        operation=proposal.operation,
                        before_score=current.aggregate_score,
                        accepted=False,
                        details=proposal.details,
                    )
                )
                return attempted
            if (
                _canonical_publication_source(canonical.code) != proposal_code
                or canonical.emitted_type != current.emitted_diagram_type
            ):
                attempted.warnings.append(
                    "semantic repair was discarded because code and typed IR diverged"
                )
                attempted.repair_history.append(
                    RepairEvent(
                        iteration=iteration,
                        operation=proposal.operation,
                        before_score=current.aggregate_score,
                        accepted=False,
                        details=proposal.details,
                    )
                )
                return attempted
            try:
                outcome = self.validator.validate(
                    proposal_code,
                    self.config.render_timeout_seconds,
                )
            except Exception as exc:
                attempted.warnings.append(f"repair validation failed: {exc}")
                attempted.repair_history.append(
                    RepairEvent(
                        iteration=iteration,
                        operation=proposal.operation,
                        before_score=current.aggregate_score,
                        accepted=False,
                        details=proposal.details,
                    )
                )
                return attempted
            runtime_type = _canonical_runtime_type(outcome.runtime.diagram_type)
            expected_type = _canonical_runtime_type(current.emitted_diagram_type)
            if not outcome.runtime.render_valid:
                attempted.repair_history.append(
                    RepairEvent(
                        iteration=iteration,
                        operation=proposal.operation,
                        before_score=current.aggregate_score,
                        accepted=False,
                        details=proposal.details,
                    )
                )
                return attempted
            if runtime_type is None or runtime_type != expected_type:
                attempted.warnings.append(
                    "semantic repair was discarded because runtime diagram type changed"
                )
                attempted.repair_history.append(
                    RepairEvent(
                        iteration=iteration,
                        operation=proposal.operation,
                        before_score=current.aggregate_score,
                        accepted=False,
                        details=proposal.details,
                    )
                )
                return attempted
            gate_diagram_type = _evaluation_gate_diagram_type(
                method=current.generation_method,
                semantic_type=current.diagram_type,
                emitted_type=current.emitted_diagram_type,
                runtime_type=runtime_type,
            )
            evaluation = self._evaluate_candidate(
                code=proposal_code,
                runtime=outcome.runtime,
                syntax_valid=outcome.runtime.syntax_valid,
                render_valid=outcome.runtime.render_valid,
                semantic_diagram_type=current.diagram_type,
                gate_diagram_type=gate_diagram_type,
                method=current.generation_method,
                typed_ir=validated_ir,
                source_scene=current.scene_ir,
                evidence=(
                    [
                        item
                        for item in context.evidence
                        if item.id in current.publication_evidence_authority_ids
                    ]
                    if current.publication_evidence_authority_ids is not None
                    else context.evidence
                ),
                approved_user_edit_evidence_ids=approved_user_edit_evidence_ids,
                references=references,
                type_fitness=current.scores.get("type_fitness"),
                image=context.image,
            )
            before_semantic = semantic_score(current.scores, self.config)
            after_semantic = semantic_score(evaluation.scores, self.config)
            improved = (
                evaluation.aggregate_score is not None
                and current.aggregate_score is not None
                and evaluation.aggregate_score > current.aggregate_score + 1e-12
                and before_semantic is not None
                and after_semantic is not None
                and after_semantic + 1e-12 >= before_semantic
            )
            event = RepairEvent(
                iteration=iteration,
                operation=proposal.operation,
                before_score=current.aggregate_score,
                after_score=evaluation.aggregate_score,
                accepted=improved,
                details=proposal.details,
            )
            attempted.repair_history.append(event)
            if not improved:
                return attempted
            attempted.mermaid_code = proposal_code
            attempted.typed_ir = validated_ir
            attempted.syntax_valid = outcome.runtime.syntax_valid
            attempted.render_valid = outcome.runtime.render_valid
            attempted.runtime_diagram_type = outcome.runtime.diagram_type
            attempted.svg = outcome.runtime.svg
            attempted.png = outcome.runtime.png
            attempted.scores = evaluation.scores
            attempted.aggregate_score = evaluation.aggregate_score
            attempted.generated_scene_ir = evaluation.generated_scene_ir
            attempted.warnings = list(
                dict.fromkeys(
                    [
                        *_without_evaluation_warnings(attempted.warnings),
                        *outcome.warnings,
                        *evaluation.warnings,
                    ]
                )
            )
            self.validator.seal_candidate(attempted, outcome)
            current = attempted
        return current
