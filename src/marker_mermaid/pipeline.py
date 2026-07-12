"""Budgeted, failure-isolated reconstruction orchestration."""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageChops, ImageFilter, ImageOps

from marker_mermaid.accessibility import (
    accessibility_limitation_warning,
    augment_accessibility_directives,
    enrich_accessibility_ir,
    supports_accessibility_directives,
)
from marker_mermaid.ast_repair import DeterministicMermaidRepair
from marker_mermaid.candidate_scene import typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, Mode
from marker_mermaid.fusion import FusionEngine, FusionInput
from marker_mermaid.models import (
    CandidateFailure,
    DiagramSceneIR,
    EngineObservation,
    MermaidCandidate,
    ReconstructionResult,
    RepairEvent,
    VisualEvidence,
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
    path_consistency,
    relative_layout_similarity,
)
from marker_mermaid.scoring import (
    aggregate_scores,
    decide_publication,
    numeric_consistency,
    ocr_recall,
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
from marker_mermaid.style_recovery import recover_flowchart_styles
from marker_mermaid.validation import CandidateValidator
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
    raw_mermaid: str | None = None
    warnings: list[str] | None = None


@dataclass(slots=True)
class _CandidateEvaluation:
    scores: dict[str, float]
    aggregate_score: float | None
    warnings: list[str]
    generated_scene_ir: DiagramSceneIR | None


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

_EVALUATION_WARNING_TEXT = frozenset(
    {
        "generated-node attribution is unavailable; review is required",
        "generated-node provenance gate requires at least 80% attribution",
        "more than 20% of generated nodes lack provenance",
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
    or from one unique normalized label match.  Ambiguous label matches never count.
    """

    if generated_scene is None or not generated_scene.elements:
        return None
    known = {item.id for item in evidence}
    source_by_id = {
        element.id: element for element in (source_scene.elements if source_scene else [])
    }
    source_by_label: dict[str, list] = {}
    for element in source_by_id.values():
        label = _normalized_label(element.text)
        if label:
            source_by_label.setdefault(label, []).append(element)

    supported = 0
    for element in generated_scene.elements:
        if known.intersection(element.evidence_ids):
            supported += 1
            continue
        source_element = source_by_id.get(element.id)
        if source_element is None:
            matches = source_by_label.get(_normalized_label(element.text), [])
            source_element = matches[0] if len(matches) == 1 else None
        if source_element is not None and known.intersection(source_element.evidence_ids):
            supported += 1
    return supported / len(generated_scene.elements)


def _canonical_runtime_type(value: str | None) -> str | None:
    if value is None:
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
        all_evidence = list(evidence or [])
        known_evidence_ids = {item.id for item in all_evidence}
        try:
            views, view_warnings = build_visual_priors(image, all_evidence, self.config)
        except Exception as exc:
            views = {"original": image.convert("RGB")}
            view_warnings = [f"visual prior generation failed: {exc}"]
        context = SourceContext(
            source_id=source_id,
            source_block_ids=source_block_ids or [source_id],
            source_image_name=source_image_name,
            image=image,
            views=views,
            evidence=all_evidence,
            ocr_texts=list(ocr_texts or []),
            source_block=source_block,
            source_blocks=list(
                source_blocks or ([source_block] if source_block is not None else [])
            ),
            vector_sources=list(vector_sources or []),
            source_mapping=source_mapping,
        )

        successful_observations: list[tuple[str, str, EngineObservation]] = []
        view_type_hints: list[str] = []
        for engine in self.engines:
            try:
                observation = engine.observe(context)
            except Exception as exc:  # Candidate failures never fail the document.
                failures.append(
                    CandidateFailure(
                        stage="generation",
                        engine=engine.name,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                )
                continue
            fusion_source = getattr(engine, "fusion_source", "other")
            if fusion_source not in {"vector", "geometry", "ocr", "vlm", "other"}:
                fusion_source = "other"
            has_payload = bool(
                observation.scene_ir is not None
                or observation.typed_candidates
                or observation.direct_candidates
                or observation.evidence
            )
            if has_payload:
                successful_observations.append((engine.name, fusion_source, observation))
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
            for item in observation.evidence:
                if item.id not in known_evidence_ids:
                    all_evidence.append(item)
                    known_evidence_ids.add(item.id)
                    evidence_changed = True
            if evidence_changed or hints_changed:
                try:
                    context.views, new_warnings = build_visual_priors(
                        image,
                        all_evidence,
                        self.config,
                        diagram_types=view_type_hints,
                    )
                    view_warnings = list(dict.fromkeys([*view_warnings, *new_warnings]))
                except Exception as exc:
                    view_warnings = list(
                        dict.fromkeys([*view_warnings, f"visual prior enrichment failed: {exc}"])
                    )

        generation_observations = [
            (name, observation) for name, _source, observation in successful_observations
        ]
        if self.config.enable_fusion and len(successful_observations) >= 2:
            try:
                fused = FusionEngine().fuse(
                    [
                        FusionInput(source=source, observation=observation, name=name)
                        for name, source, observation in successful_observations
                    ]
                )
                generation_observations = [
                    (FusionEngine.name, fused),
                    *generation_observations,
                ]
                for item in fused.evidence:
                    if item.id not in known_evidence_ids:
                        all_evidence.append(item)
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
        for engine_name, observation in generation_observations:
            top_types = [
                item
                for item in observation.prediction.candidates[: self.config.type_candidate_count]
                if item in self.config.enabled_types
            ]
            generated: list[_Draft] = []
            if self.config.enable_typed_ir:
                for typed in observation.typed_candidates[:candidate_budget]:
                    if typed.diagram_type not in top_types:
                        continue
                    try:
                        enriched_ir = enrich_accessibility_ir(
                            typed.ir,
                            typed.diagram_type,
                            experimental=self.config.mode != Mode.STRICT,
                        )
                        serialized = serialize_typed_ir_result(
                            typed.diagram_type,
                            enriched_ir,
                            experimental=self.config.mode != Mode.STRICT,
                        )
                    except (SerializationError, SerializationContractError) as exc:
                        failures.append(
                            CandidateFailure(
                                stage="serialization",
                                engine=engine_name,
                                error_type=type(exc).__name__,
                                message=str(exc),
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
                            warnings=list(serialized.warnings),
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
                digest = hashlib.sha256(draft.code.encode()).hexdigest()
                if digest not in code_hashes:
                    code_hashes.add(digest)
                    drafts.append(draft)
                if group:
                    remaining_groups.append(group)
            draft_groups = remaining_groups

        candidates: list[MermaidCandidate] = []
        reference_texts = list(
            dict.fromkeys(
                [
                    *context.ocr_texts,
                    *(
                        item.text
                        for item in all_evidence
                        if item.text and item.kind in {"ocr_token", "vector_text"}
                    ),
                ]
            )
        )
        for index, draft in enumerate(drafts, start=1):
            if self.config.enable_style_recovery:
                style_recovery = recover_flowchart_styles(
                    draft.code,
                    draft.observation.scene_ir,
                    compatibility_profile=self.config.compatibility_profile,
                    security_profile=self.config.security_profile,
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
            source_repair = self.source_repair.repair(styled_code)
            repair_accepted = bool(
                source_repair.changed
                and source_repair.security_preserved
                and source_repair.idempotent
                and not source_repair.budget_exhausted
            )
            candidate_code = source_repair.source if repair_accepted else styled_code
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
                mermaid_code=candidate_code,
                warnings=[
                    *view_warnings,
                    *draft.observation.warnings,
                    *(draft.warnings or []),
                    *(style_recovery.warnings if style_recovery is not None else ()),
                    *source_repair_warnings,
                ],
                repair_history=[*style_repair_history, *source_repair_history],
            )
            try:
                outcome = self.validator.validate(
                    candidate_code, self.config.render_timeout_seconds
                )
                runtime = outcome.runtime
                validation_warnings = outcome.warnings
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
                and candidate.emitted_diagram_type == candidate.diagram_type
            ):
                try:
                    fallback = serialize_runtime_fallback_result(
                        draft.diagram_type,
                        draft.typed_ir,
                        experimental=self.config.mode != Mode.STRICT,
                    )
                    if fallback is not None:
                        fallback_outcome = self.validator.validate(
                            fallback.code,
                            self.config.render_timeout_seconds,
                        )
                        if fallback_outcome.runtime.render_valid:
                            candidate_code = fallback.code
                            candidate.mermaid_code = fallback.code
                            candidate.emitted_diagram_type = fallback.emitted_type
                            candidate.fallback_chain = list(fallback.fallback_chain)
                            candidate.serialization_stability = fallback.stability
                            candidate.warnings.extend(fallback.warnings)
                            candidate.repair_history.append(
                                RepairEvent(
                                    iteration=0,
                                    operation="runtime_portable_fallback",
                                    accepted=True,
                                    details={
                                        "rejected_type": draft.diagram_type,
                                        "emitted_type": fallback.emitted_type,
                                        "stage": "validation",
                                    },
                                )
                            )
                            runtime = fallback_outcome.runtime
                            validation_warnings = fallback_outcome.warnings
                        else:
                            candidate.warnings.append(
                                "declared portable fallback also failed parse/render validation"
                            )
                except (SerializationError, SerializationContractError) as exc:
                    candidate.warnings.append(f"runtime fallback unavailable: {exc}")
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
            evaluation = self._evaluate_candidate(
                code=candidate_code,
                runtime=runtime,
                syntax_valid=candidate.syntax_valid,
                render_valid=candidate.render_valid,
                diagram_type=draft.diagram_type,
                method=draft.method,
                typed_ir=draft.typed_ir,
                source_scene=draft.observation.scene_ir,
                evidence=all_evidence,
                reference_texts=reference_texts,
                type_fitness=type_fitness,
                image=context.image,
            )
            candidate.scores = evaluation.scores
            candidate.aggregate_score = evaluation.aggregate_score
            candidate.generated_scene_ir = evaluation.generated_scene_ir
            candidate.warnings.extend(evaluation.warnings)
            candidates.append(candidate)

        selected = self._select(candidates)
        if selected is not None and self.repair_engine is not None:
            selected = self._repair(context, selected)
            if selected not in candidates:
                candidates.append(selected)
        decision = decide_publication(selected, self.config)
        status = "success" if decision.publish else "review_required"
        if selected is None:
            status = "failed"
        return ReconstructionResult(
            source_id=source_id,
            source_image_name=source_image_name,
            source_kind=source_kind,
            source_block_ids=context.source_block_ids,
            page_ids=list(page_ids or []),
            anchor_block_id=anchor_block_id,
            source_mapping=source_mapping,
            selected=selected,
            alternatives=[item for item in candidates if item is not selected],
            evidence=all_evidence,
            failures=failures,
            grade=decision.grade,
            publish=decision.publish,
            review_required=decision.review_required,
            status=status,
        )

    @staticmethod
    def _select(candidates: list[MermaidCandidate]) -> MermaidCandidate | None:
        eligible = [item for item in candidates if item.syntax_valid and item.render_valid]
        if not eligible:
            return None
        priority = {"typed_ir": 3, "scene_ir_fallback": 2, "direct_mermaid": 1}
        return max(
            eligible,
            key=lambda item: (
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
        diagram_type: str,
        method: str,
        typed_ir: dict | None,
        source_scene: DiagramSceneIR | None,
        evidence: list[VisualEvidence],
        reference_texts: list[str],
        type_fitness: float | None,
        image: Image.Image,
    ) -> _CandidateEvaluation:
        """Score initial and repaired candidates through one availability/gating path."""

        scores: dict[str, float] = {
            "syntax": float(syntax_valid),
            "render": float(render_valid),
        }
        warnings: list[str] = []
        recall = ocr_recall(reference_texts, code)
        if recall is not None:
            scores["ocr_recall"] = recall
        numeric = numeric_consistency(reference_texts, code)
        if numeric is not None and diagram_type in _NUMERIC_TYPES:
            scores["numeric_consistency"] = numeric
        if type_fitness is not None:
            scores["type_fitness"] = type_fitness

        generated_scene = None
        if typed_ir is not None:
            generated_scene = typed_ir_to_scene(diagram_type, typed_ir)
        elif method == "scene_ir_fallback" and source_scene is not None:
            generated_scene = source_scene.model_copy(deep=True)
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
        if self.config.mode != Mode.STRICT and diagram_type in _PROVENANCE_GATED_TYPES:
            if provenance is None:
                aggregate = None
                warnings.append("generated-node attribution is unavailable; review is required")
            elif provenance < 0.8:
                aggregate = None
                warnings.append("generated-node provenance gate requires at least 80% attribution")
        if diagram_type in _NUMERIC_TYPES and numeric is None:
            aggregate = None
            warnings.append(
                "numeric diagram lacks OCR/vector numeric evidence and cannot auto-publish"
            )
        elif (
            diagram_type in _NUMERIC_TYPES
            and numeric is not None
            and numeric < self.config.publish_min_score
        ):
            aggregate = None
            warnings.append("numeric consistency is below the automatic publication threshold")
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

    def _repair(self, context: SourceContext, selected: MermaidCandidate) -> MermaidCandidate:
        current = selected
        reference_texts = list(
            dict.fromkeys(
                [
                    *context.ocr_texts,
                    *(
                        item.text
                        for item in context.evidence
                        if item.text and item.kind in {"ocr_token", "vector_text"}
                    ),
                ]
            )
        )
        for iteration in range(1, int(self.config.max_repair_iterations or 0) + 1):
            try:
                proposal = self.repair_engine.repair(context, current)  # type: ignore[union-attr]
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
            if (
                not proposal.code
                or proposal.code == current.mermaid_code
                or proposal.typed_ir is None
            ):
                break
            attempted = current.model_copy(deep=True)
            attempted.candidate_id = f"{selected.candidate_id}-repair-{iteration}"
            try:
                outcome = self.validator.validate(
                    proposal.code,
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
            evaluation = self._evaluate_candidate(
                code=proposal.code,
                runtime=outcome.runtime,
                syntax_valid=outcome.runtime.syntax_valid,
                render_valid=outcome.runtime.render_valid,
                diagram_type=current.diagram_type,
                method=current.generation_method,
                typed_ir=proposal.typed_ir,
                source_scene=current.scene_ir,
                evidence=context.evidence,
                reference_texts=reference_texts,
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
            attempted.mermaid_code = proposal.code
            attempted.typed_ir = proposal.typed_ir
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
            current = attempted
        return current
