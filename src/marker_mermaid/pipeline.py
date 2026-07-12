"""Budgeted, failure-isolated reconstruction orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageChops, ImageFilter, ImageOps

from marker_mermaid.ast_repair import DeterministicMermaidRepair
from marker_mermaid.candidate_scene import typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, Mode
from marker_mermaid.fusion import FusionEngine, FusionInput
from marker_mermaid.models import (
    CandidateFailure,
    EngineObservation,
    MermaidCandidate,
    ReconstructionResult,
    RepairEvent,
    VisualEvidence,
)
from marker_mermaid.protocols import CandidateEngine, RepairEngine, RuntimeResult, SourceContext
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
)
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serialization import SerializationContractError, SerializationResult
from marker_mermaid.serializers import (
    SerializationError,
    scene_to_flowchart,
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


def _provenance_score(candidate: MermaidCandidate, evidence: list[VisualEvidence]) -> float | None:
    if candidate.scene_ir is None or not candidate.scene_ir.elements:
        return None
    known = {item.id for item in evidence}
    supported = sum(
        1 for element in candidate.scene_ir.elements if known.intersection(element.evidence_ids)
    )
    return supported / len(candidate.scene_ir.elements)


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
    ) -> ReconstructionResult:
        failures: list[CandidateFailure] = []
        all_evidence = list(evidence or [])
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
            source_mapping=source_mapping,
        )

        successful_observations: list[tuple[str, str, EngineObservation]] = []
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
            evidence_changed = False
            for item in observation.evidence:
                if item.id not in {existing.id for existing in all_evidence}:
                    all_evidence.append(item)
                    evidence_changed = True
            if evidence_changed:
                try:
                    context.views, new_warnings = build_visual_priors(
                        image,
                        all_evidence,
                        self.config,
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
                known_evidence = {item.id for item in all_evidence}
                for item in fused.evidence:
                    if item.id not in known_evidence:
                        all_evidence.append(item)
                        known_evidence.add(item.id)
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
                for typed in observation.typed_candidates:
                    if typed.diagram_type not in top_types:
                        continue
                    try:
                        serialized = serialize_typed_ir_result(
                            typed.diagram_type,
                            typed.ir,
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
                            typed_ir=typed.ir,
                            warnings=list(serialized.warnings),
                        )
                    )
            if (
                self.config.enable_generic_scene_ir
                and observation.scene_ir is not None
                and observation.scene_ir.elements
            ):
                code = scene_to_flowchart(
                    observation.scene_ir, experimental=self.config.mode != Mode.STRICT
                )
                fallback_from = top_types[0] if top_types else "unknown"
                requested_type = fallback_from if fallback_from != "unknown" else "flowchart"
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
                for direct in observation.direct_candidates:
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
        numeric_types = {
            "gantt",
            "pie",
            "xychart",
            "quadrant",
            "sankey",
            "radar",
            "treemap",
            "packet",
        }
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
            scores: dict[str, float] = {
                "syntax": float(candidate.syntax_valid),
                "render": float(candidate.render_valid),
            }
            recall = ocr_recall(reference_texts, candidate_code)
            if recall is not None:
                scores["ocr_recall"] = recall
            numeric = numeric_consistency(reference_texts, candidate_code)
            if numeric is not None and draft.diagram_type in numeric_types:
                scores["numeric_consistency"] = numeric
            prediction_scores = dict(
                zip(
                    draft.observation.prediction.candidates,
                    draft.observation.prediction.scores,
                    strict=True,
                )
            )
            if draft.diagram_type in prediction_scores:
                scores["type_fitness"] = (
                    0.0 if contract_mismatch else prediction_scores[draft.diagram_type]
                )
            provenance = _provenance_score(candidate, all_evidence)
            if provenance is not None:
                scores["visual_entailment_precision"] = provenance
                if provenance < 0.8:
                    candidate.warnings.append("more than 20% of scene nodes lack provenance")
            generated_scene = None
            if draft.typed_ir is not None:
                generated_scene = typed_ir_to_scene(draft.diagram_type, draft.typed_ir)
            elif draft.method == "scene_ir_fallback" and draft.observation.scene_ir is not None:
                generated_scene = draft.observation.scene_ir.model_copy(deep=True)
            source_scene = draft.observation.scene_ir
            structural_edge_available = False
            if source_scene is not None and generated_scene is not None:
                structural_metrics = []
                if self.config.enable_render_compare:
                    structural_metrics.append(
                        edge_topology_agreement(source_scene, generated_scene)
                    )
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
                edge = _edge_iou(context.image, runtime.png)
                if edge is not None:
                    scores["edge_agreement"] = edge
            candidate.scores = scores
            candidate.aggregate_score = aggregate_scores(scores, self.config)
            if draft.diagram_type in numeric_types and numeric is None:
                candidate.aggregate_score = None
                candidate.warnings.append(
                    "numeric diagram lacks OCR/vector numeric evidence and cannot auto-publish"
                )
            if (
                candidate.scene_ir is not None
                and not any(element.text for element in candidate.scene_ir.elements)
                and draft.method == "scene_ir_fallback"
            ):
                candidate.aggregate_score = None
                candidate.warnings.append(
                    "unlabeled scene-only candidates require OCR/VLM fusion before publishing"
                )
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

    def _repair(self, context: SourceContext, selected: MermaidCandidate) -> MermaidCandidate:
        current = selected
        for iteration in range(1, int(self.config.max_repair_iterations or 0) + 1):
            try:
                code = self.repair_engine.repair(context, current)  # type: ignore[union-attr]
            except Exception as exc:
                current.warnings.append(f"repair engine failed: {exc}")
                break
            if not code or code == current.mermaid_code:
                break
            try:
                outcome = self.validator.validate(code, self.config.render_timeout_seconds)
            except Exception as exc:
                current.warnings.append(f"repair validation failed: {exc}")
                break
            if not outcome.runtime.render_valid:
                current.repair_history.append(
                    RepairEvent(iteration=iteration, operation="repair", accepted=False)
                )
                continue
            recall = ocr_recall(context.ocr_texts, code)
            scores = dict(current.scores)
            scores["syntax"] = 1.0
            scores["render"] = 1.0
            if recall is not None:
                scores["ocr_recall"] = recall
            numeric = numeric_consistency(context.ocr_texts, code)
            if numeric is not None and current.diagram_type in {
                "gantt",
                "pie",
                "xychart",
                "quadrant",
                "sankey",
                "radar",
                "treemap",
                "packet",
            }:
                scores["numeric_consistency"] = numeric
            if self.config.enable_render_compare:
                edge = _edge_iou(context.image, outcome.runtime.png)
                if edge is not None:
                    scores["edge_agreement"] = edge
            aggregate = aggregate_scores(scores, self.config)
            improved = aggregate is not None and (
                current.aggregate_score is None or aggregate > current.aggregate_score
            )
            event = RepairEvent(
                iteration=iteration,
                operation="repair",
                before_score=current.aggregate_score,
                after_score=aggregate,
                accepted=improved,
            )
            current.repair_history.append(event)
            if improved:
                current = current.model_copy(deep=True)
                current.candidate_id = f"{selected.candidate_id}-repair-{iteration}"
                current.mermaid_code = code
                current.svg = outcome.runtime.svg
                current.png = outcome.runtime.png
                current.scores = scores
                current.aggregate_score = aggregate
                current.warnings.extend(outcome.warnings)
        return current
