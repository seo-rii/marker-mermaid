"""Deterministic fusion of vector, geometry, OCR, and VLM observations.

The fusion layer is intentionally Marker-independent.  Callers identify the
origin of each observation explicitly, which makes the precedence rules
auditable instead of guessing them from engine names:

* geometry: vector > geometry > other > VLM > OCR;
* labels: vector text > OCR consensus > other > VLM;
* relation semantics: VLM > other > vector/geometry/OCR;
* provenance: retain every unique evidence id and merge source block ids.

Fusion never manufactures evidence.  Conflicting inputs are resolved by the
rules above and recorded in the returned observation's warnings.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from marker_mermaid.mapping_validation import (
    authority_evidence_matches,
    normalize_scene_bbox,
    source_evidence_matches,
)
from marker_mermaid.models import (
    MAX_OBSERVATION_CANDIDATES,
    MAX_OBSERVATION_TYPED_IR_JSON_BYTES,
    MAX_WARNING_CHARS,
    NODE_ID_MAPPING_MIN_IOU,
    DiagramSceneIR,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    NodeIdMapping,
    SceneElement,
    SceneGroup,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
    _canonical_typed_candidate_fields,
    canonical_typed_ir_snapshot,
)

FusionSource = Literal["vector", "geometry", "ocr", "vlm", "other"]

_GEOMETRY_RANK: dict[FusionSource, int] = {
    "vector": 5,
    "geometry": 4,
    "other": 3,
    "vlm": 2,
    "ocr": 1,
}
_SEMANTIC_RANK: dict[FusionSource, int] = {
    "vlm": 5,
    "other": 4,
    "vector": 3,
    "geometry": 2,
    "ocr": 1,
}
_TYPE_WEIGHT: dict[FusionSource, float] = {
    "vector": 1.0,
    "geometry": 0.9,
    "ocr": 0.5,
    "vlm": 1.0,
    "other": 0.75,
}


def _canonical_typed_candidates(
    value: object,
) -> tuple[list[TypedIRCandidate], list[str]]:
    """Return a bounded detached typed-candidate projection for fusion."""

    warnings: list[str] = []
    if type(value) is not list:
        candidates: list[object] = []
        warnings.append("fusion skipped a non-canonical typed candidate collection")
    else:
        candidates = list.__getitem__(value, slice(0, MAX_OBSERVATION_CANDIDATES + 1))
        if len(candidates) > MAX_OBSERVATION_CANDIDATES:
            warnings.append(
                "fusion truncated typed candidates to the bounded observation item limit"
            )
            candidates = candidates[:MAX_OBSERVATION_CANDIDATES]

    canonical: list[TypedIRCandidate] = []
    aggregate_json_bytes = 0
    for position, candidate in enumerate(candidates):
        try:
            if type(candidate) is not TypedIRCandidate:
                raise TypeError("candidate must be an exact TypedIRCandidate record")
            is_model, diagram_type, ir_snapshot, confidence = (
                _canonical_typed_candidate_fields(candidate)
            )
            if not is_model:  # pragma: no cover - exact type guard above
                raise TypeError("typed candidate must remain a canonical model")
            ir_json = json.dumps(
                ir_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            ir_json_bytes = len(ir_json.encode("utf-8"))
            next_aggregate_json_bytes = aggregate_json_bytes + ir_json_bytes
            if next_aggregate_json_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                warnings.append(
                    "fusion stopped typed candidate capture at the aggregate JSON byte budget"
                )
                break
            aggregate_json_bytes = next_aggregate_json_bytes
            snapshot = TypedIRCandidate.model_validate(
                {
                    "diagram_type": diagram_type,
                    "ir": ir_snapshot,
                    "confidence": confidence,
                }
            )
            canonical.append(snapshot)
        except Exception as exc:
            warning = (
                f"fusion skipped invalid typed candidate at index {position}: {type(exc).__name__}"
            )
            warnings.append(warning[:MAX_WARNING_CHARS])
    return canonical, warnings


@dataclass(frozen=True, slots=True)
class FusionInput:
    """An observation plus its explicit, stable origin.

    ``name`` should be the engine instance name when available.  It is used
    only as a deterministic tie-breaker and in conflict diagnostics.
    ``prior_evidence_ids`` is the collision-free evidence snapshot that
    existed before this observation was produced; an observation cannot
    authorize its own typed-to-Scene identity claims. ``prior_evidence``
    carries the payload snapshot needed to verify that each referenced ID's
    bbox and text belong to the claimed Scene element. Evidence collisions
    known by the caller, including collisions with initial context, belong in
    ``excluded_evidence_ids``.
    """

    source: FusionSource
    observation: EngineObservation
    name: str = ""
    prior_evidence_ids: frozenset[str] = frozenset()
    prior_evidence: tuple[VisualEvidence, ...] = ()
    publication_evidence_ids: frozenset[str] | None = None
    excluded_evidence_ids: frozenset[str] = frozenset()
    trusted_canvas_size: tuple[float, float] | None = None
    trusted_source_block_ids: frozenset[str] = frozenset()


@dataclass(slots=True)
class _ElementRecord:
    owner: str
    source: FusionSource
    element: SceneElement
    scene: DiagramSceneIR
    trusted_canvas_size: tuple[float, float] | None
    trusted_source_block_ids: frozenset[str]


@dataclass(slots=True)
class _RelationRecord:
    owner: str
    source: FusionSource
    relation: SceneRelation
    mapped_source: str | None
    mapped_target: str | None


@dataclass(slots=True)
class _SceneFusionResult:
    scene: DiagramSceneIR
    warnings: list[str]
    element_ids: dict[tuple[str, str], str]
    certified_mappings: dict[tuple[str, str], NodeIdMapping]
    certification_failures: dict[tuple[str, str], str]
    conflicted_connector_pairs: set[frozenset[str]]


class FusionEngine:
    """Merge independently produced observations into one observation."""

    name = "deterministic_fusion"

    def __init__(self, *, element_iou_threshold: float = 0.45):
        if not NODE_ID_MAPPING_MIN_IOU <= element_iou_threshold <= 1:
            raise ValueError(
                "element_iou_threshold must be at least "
                f"{NODE_ID_MAPPING_MIN_IOU} and at most one"
            )
        self.element_iou_threshold = element_iou_threshold

    def fuse(self, inputs: Iterable[FusionInput]) -> EngineObservation:
        """Fuse observations using stable precedence and spatial matching."""

        ordered = self._ordered_inputs(inputs)
        if not ordered:
            raise ValueError("at least one fusion input is required")
        owners = {id(item): _owner(item, position) for position, item in enumerate(ordered)}

        warnings = _unique(
            warning for item in ordered for warning in item.observation.warnings if warning
        )
        evidence, evidence_warnings, collided_evidence_ids = self._fuse_evidence(ordered)
        collided_evidence_ids.update(
            evidence_id for item in ordered for evidence_id in item.excluded_evidence_ids
        )
        warnings.extend(evidence_warnings)

        scene_inputs = [item for item in ordered if item.observation.scene_ir is not None]
        scene_ir: DiagramSceneIR | None = None
        scene_result: _SceneFusionResult | None = None
        if scene_inputs:
            scene_result = self._fuse_scenes(
                scene_inputs,
                evidence,
                owners,
                collided_evidence_ids,
            )
            scene_ir = scene_result.scene
            warnings.extend(scene_result.warnings)

        prediction = self._fuse_predictions(ordered)
        if scene_ir is not None:
            scene_ir.diagram_type_candidates = list(prediction.candidates)
        (
            typed_candidates,
            typed_mappings,
            typed_evidence_authorities,
            typed_warnings,
        ) = self._fuse_typed_candidates(
            ordered,
            owners,
            scene_result,
        )
        warnings.extend(typed_warnings)
        direct_candidates, direct_evidence_authorities = self._fuse_direct_candidates(ordered)

        result = EngineObservation(
            prediction=prediction,
            scene_ir=scene_ir,
            typed_candidates=typed_candidates,
            direct_candidates=direct_candidates,
            evidence=evidence,
            warnings=_unique(warnings),
        )
        result._set_fusion_metadata(
            typed_mappings,
            (scene_result.conflicted_connector_pairs if scene_result is not None else set()),
            typed_evidence_authorities=typed_evidence_authorities,
            scene_evidence_authority=(
                frozenset.intersection(
                    *[
                        item.publication_evidence_ids
                        for item in scene_inputs
                        if item.publication_evidence_ids is not None
                    ]
                )
                if scene_inputs
                and all(item.publication_evidence_ids is not None for item in scene_inputs)
                else None
            ),
            direct_evidence_authorities=direct_evidence_authorities,
        )
        return result

    def _ordered_inputs(self, inputs: Iterable[FusionInput]) -> list[FusionInput]:
        values = list(inputs)
        for value in values:
            if not isinstance(value, FusionInput):
                raise TypeError("fusion inputs must be FusionInput instances")
        canonical_values: list[FusionInput] = []
        observation_projection_digests: dict[int, str] = {}
        for value in values:
            typed_candidates, typed_warnings = _canonical_typed_candidates(
                value.observation.typed_candidates
            )
            observation = value.observation.model_copy(deep=False)
            observation.typed_candidates = typed_candidates
            observation.warnings = list(
                dict.fromkeys([*list(observation.warnings), *typed_warnings])
            )
            canonical_value = replace(value, observation=observation)
            canonical_values.append(canonical_value)
            projection = EngineObservation.__pydantic_serializer__.to_python(
                observation,
                mode="json",
                exclude_none=True,
                warnings=False,
            )
            projection_json = json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            observation_projection_digests[id(canonical_value)] = hashlib.sha256(
                projection_json
            ).hexdigest()
        return sorted(
            canonical_values,
            key=lambda item: (
                -_GEOMETRY_RANK[item.source],
                item.source,
                item.name,
                tuple(sorted(item.prior_evidence_ids)),
                item.publication_evidence_ids is None,
                tuple(sorted(item.publication_evidence_ids or ())),
                tuple(
                    sorted(
                        evidence.model_dump_json(exclude_none=True)
                        for evidence in item.prior_evidence
                    )
                ),
                tuple(sorted(item.excluded_evidence_ids)),
                item.trusted_canvas_size or (),
                tuple(sorted(item.trusted_source_block_ids)),
                observation_projection_digests[id(item)],
            ),
        )

    def _fuse_evidence(
        self, inputs: Sequence[FusionInput]
    ) -> tuple[list[VisualEvidence], list[str], set[str]]:
        records: dict[str, tuple[FusionSource, str, VisualEvidence]] = {}
        warnings: list[str] = []
        collided_ids: set[str] = set()
        for item in inputs:
            for candidate in item.observation.evidence:
                existing = records.get(candidate.id)
                if existing is None:
                    records[candidate.id] = (
                        item.source,
                        item.name,
                        candidate.model_copy(deep=True),
                    )
                    continue
                collided_ids.add(candidate.id)
                old_source, old_name, old = existing
                equivalent = old.model_dump(exclude={"score", "source_block_ids"}) == (
                    candidate.model_dump(exclude={"score", "source_block_ids"})
                )
                winner_source, winner_name, winner = old_source, old_name, old
                if _GEOMETRY_RANK[item.source] > _GEOMETRY_RANK[old_source]:
                    winner_source, winner_name = item.source, item.name
                    winner = candidate.model_copy(deep=True)
                winner.source_block_ids = sorted(
                    set(old.source_block_ids) | set(candidate.source_block_ids)
                )
                scores = [score for score in (old.score, candidate.score) if score is not None]
                winner.score = max(scores) if scores else None
                records[candidate.id] = (winner_source, winner_name, winner)
                if not equivalent:
                    warnings.append(
                        "fusion evidence conflict for "
                        f"{candidate.id!r}; kept {winner_source} input {winner_name!r}"
                    )
        return [records[key][2] for key in sorted(records)], warnings, collided_ids

    def _fuse_predictions(self, inputs: Sequence[FusionInput]) -> DiagramTypePrediction:
        totals: defaultdict[str, float] = defaultdict(float)
        total_weight = sum(_TYPE_WEIGHT[item.source] for item in inputs)
        for item in inputs:
            weight = _TYPE_WEIGHT[item.source]
            for candidate, score in zip(
                item.observation.prediction.candidates,
                item.observation.prediction.scores,
                strict=True,
            ):
                totals[candidate] += score * weight
        ranked = sorted(
            ((candidate, value / total_weight) for candidate, value in totals.items()),
            key=lambda item: (-item[1], item[0]),
        )
        return DiagramTypePrediction(
            candidates=[candidate for candidate, _ in ranked],
            scores=[score for _, score in ranked],
            visual_signals=_unique(
                signal for item in inputs for signal in item.observation.prediction.visual_signals
            ),
            negative_signals=_unique(
                signal for item in inputs for signal in item.observation.prediction.negative_signals
            ),
        )

    def _fuse_scenes(
        self,
        inputs: Sequence[FusionInput],
        evidence: Sequence[VisualEvidence],
        owners: dict[int, str],
        collided_evidence_ids: set[str],
    ) -> _SceneFusionResult:
        warnings: list[str] = []
        clusters: list[list[_ElementRecord]] = []
        element_map: dict[tuple[str, str], int] = {}

        for item in inputs:
            scene = item.observation.scene_ir
            assert scene is not None
            owner = owners[id(item)]
            for element in sorted(scene.elements, key=lambda value: value.id):
                record = _ElementRecord(
                    owner,
                    item.source,
                    element,
                    scene,
                    item.trusted_canvas_size,
                    item.trusted_source_block_ids,
                )
                cluster_index = self._matching_element_cluster(record, clusters)
                if cluster_index is None:
                    cluster_index = len(clusters)
                    clusters.append([record])
                else:
                    clusters[cluster_index].append(record)
                element_map[(owner, element.id)] = cluster_index

        fused_elements: list[SceneElement] = []
        output_ids: list[str] = []
        used_ids: set[str] = set()
        for cluster in clusters:
            fused, cluster_warnings = self._fuse_element_cluster(cluster, evidence)
            fused.id = _unique_id(fused.id, used_ids)
            used_ids.add(fused.id)
            output_ids.append(fused.id)
            fused_elements.append(fused)
            warnings.extend(cluster_warnings)

        relation_clusters: list[list[_RelationRecord]] = []
        for item in inputs:
            scene = item.observation.scene_ir
            assert scene is not None
            owner = owners[id(item)]
            for relation in sorted(scene.relations, key=lambda value: value.id):
                source_index = element_map.get((owner, relation.source_id))
                target_index = element_map.get((owner, relation.target_id))
                mapped_source = output_ids[source_index] if source_index is not None else None
                mapped_target = output_ids[target_index] if target_index is not None else None
                record = _RelationRecord(
                    owner,
                    item.source,
                    relation,
                    mapped_source,
                    mapped_target,
                )
                cluster_index = self._matching_relation_cluster(record, relation_clusters)
                if cluster_index is None:
                    relation_clusters.append([record])
                else:
                    relation_clusters[cluster_index].append(record)

        fused_relations: list[SceneRelation] = []
        used_relation_ids: set[str] = set()
        conflicted_connector_pairs: set[frozenset[str]] = set()
        for cluster in relation_clusters:
            fused, cluster_warnings, direction_conflict = self._fuse_relation_cluster(cluster)
            fused.id = _unique_id(fused.id, used_relation_ids)
            used_relation_ids.add(fused.id)
            fused_relations.append(fused)
            warnings.extend(cluster_warnings)
            if (
                direction_conflict
                and fused.source_id is not None
                and fused.target_id is not None
                and fused.source_id != fused.target_id
            ):
                conflicted_connector_pairs.add(frozenset({fused.source_id, fused.target_id}))

        relation_pair_counts: defaultdict[frozenset[str], int] = defaultdict(int)
        for relation in fused_relations:
            if (
                relation.source_id is None
                or relation.target_id is None
                or relation.source_id == relation.target_id
            ):
                continue
            pair = frozenset({relation.source_id, relation.target_id})
            relation_pair_counts[pair] += 1
        conflicted_connector_pairs.update(
            pair for pair, count in relation_pair_counts.items() if count > 1
        )

        fused_groups = self._fuse_groups(
            inputs,
            owners,
            element_map,
            output_ids,
            warnings,
        )
        primary = inputs[0].observation.scene_ir
        assert primary is not None
        direction = "unknown"
        direction_owner = ""
        for item in inputs:
            scene = item.observation.scene_ir
            assert scene is not None
            if scene.reading_direction == "unknown":
                continue
            if direction == "unknown":
                direction = scene.reading_direction
                direction_owner = owners[id(item)]
            elif scene.reading_direction != direction:
                warnings.append(
                    "fusion reading-direction conflict; "
                    f"kept {direction!r} from {direction_owner!r} over "
                    f"{scene.reading_direction!r} from {owners[id(item)]!r}"
                )

        fused_scene = DiagramSceneIR(
            elements=fused_elements,
            relations=fused_relations,
            groups=fused_groups,
            reading_direction=direction,
            diagram_type_candidates=[],
            coordinate_space=primary.coordinate_space,
            canvas_size=primary.canvas_size,
        )
        element_ids = {key: output_ids[cluster_index] for key, cluster_index in element_map.items()}
        certified_mappings, certification_failures = self._certify_element_mappings(
            clusters,
            output_ids,
            inputs,
            owners,
            collided_evidence_ids,
        )
        return _SceneFusionResult(
            scene=fused_scene,
            warnings=warnings,
            element_ids=element_ids,
            certified_mappings=certified_mappings,
            certification_failures=certification_failures,
            conflicted_connector_pairs=conflicted_connector_pairs,
        )

    def _certify_element_mappings(
        self,
        clusters: Sequence[list[_ElementRecord]],
        output_ids: Sequence[str],
        inputs: Sequence[FusionInput],
        owners: dict[int, str],
        collided_evidence_ids: set[str],
    ) -> tuple[
        dict[tuple[str, str], NodeIdMapping],
        dict[tuple[str, str], str],
    ]:
        """Certify aliases without trusting the best-effort cluster assignment alone."""

        reference_counts: dict[str, defaultdict[str, int]] = {}
        for item in inputs:
            scene = item.observation.scene_ir
            assert scene is not None
            owner = owners[id(item)]
            counts: defaultdict[str, int] = defaultdict(int)
            for element in scene.elements:
                for evidence_id in set(element.evidence_ids):
                    counts[evidence_id] += 1
            for relation in scene.relations:
                for evidence_id in set(relation.evidence_ids):
                    counts[evidence_id] += 1
            reference_counts[owner] = counts

        prior_evidence_by_owner: dict[str, dict[str, VisualEvidence]] = {}
        authority_evidence_by_owner: dict[str, dict[str, VisualEvidence]] = {}
        for item in inputs:
            owner = owners[id(item)]
            prior_counts: defaultdict[str, int] = defaultdict(int)
            for evidence in item.prior_evidence:
                prior_counts[evidence.id] += 1
            prior_evidence_by_owner[owner] = {
                evidence.id: evidence
                for evidence in item.prior_evidence
                if evidence.id in item.prior_evidence_ids
                and (
                    item.publication_evidence_ids is None
                    or evidence.id in item.publication_evidence_ids
                )
                and prior_counts[evidence.id] == 1
                and evidence.id not in collided_evidence_ids
            }
            authority_evidence_by_owner[owner] = {
                evidence.id: evidence
                for evidence in item.observation.evidence
                if evidence.kind == "contour"
                and (
                    item.publication_evidence_ids is None
                    or evidence.id in item.publication_evidence_ids
                )
                and evidence.id not in collided_evidence_ids
            }

        def usable_evidence(
            record: _ElementRecord,
            *,
            evidence_by_id: dict[str, VisualEvidence],
            authority: bool,
        ) -> list[str]:
            if record.trusted_canvas_size is None or record.scene.coordinate_space != "pixels":
                return []
            record_bbox = normalize_scene_bbox(
                record.element.bbox,
                record.scene,
                trusted_canvas_size=record.trusted_canvas_size,
            )
            if record_bbox is None:
                return []
            values: list[str] = []
            for evidence_id in sorted(set(record.element.evidence_ids)):
                evidence = evidence_by_id.get(evidence_id)
                if (
                    evidence is None
                    or evidence_id in collided_evidence_ids
                    or reference_counts[record.owner][evidence_id] != 1
                    or evidence.bbox is None
                    or not evidence.source_block_ids
                ):
                    continue
                if authority:
                    if not authority_evidence_matches(
                        evidence,
                        record.scene,
                        record_bbox,
                        self.element_iou_threshold,
                        trusted_canvas_size=record.trusted_canvas_size,
                    ):
                        continue
                elif not source_evidence_matches(
                    evidence,
                    record.scene,
                    record_bbox,
                    record.element.text,
                    trusted_canvas_size=record.trusted_canvas_size,
                ):
                    continue
                values.append(evidence_id)
            return values

        certified: dict[tuple[str, str], NodeIdMapping] = {}
        failures: dict[tuple[str, str], str] = {}
        all_records = [record for cluster in clusters for record in cluster]
        for source_record in all_records:
            source_key = (source_record.owner, source_record.element.id)
            source_bbox = (
                normalize_scene_bbox(
                    source_record.element.bbox,
                    source_record.scene,
                    trusted_canvas_size=source_record.trusted_canvas_size,
                )
                if source_record.trusted_canvas_size is not None
                and source_record.scene.coordinate_space == "pixels"
                else None
            )
            source_evidence_ids = usable_evidence(
                source_record,
                evidence_by_id=prior_evidence_by_owner[source_record.owner],
                authority=False,
            )
            if source_bbox is None:
                failures[source_key] = "source Scene bbox is not safely normalizable"
                continue
            if not source_evidence_ids:
                referenced_prior_ids = set(source_record.element.evidence_ids).intersection(
                    prior_evidence_by_owner[source_record.owner]
                )
                failures[source_key] = (
                    "source evidence is not spatially/text aligned with the owner Scene node"
                    if referenced_prior_ids
                    else "source evidence is not a unique collision-free prior payload"
                )
                continue

            overlapping_clusters: list[
                tuple[
                    int,
                    float,
                    list[tuple[_ElementRecord, list[str]]],
                    list[str],
                ]
            ] = []
            for cluster_index, cluster in enumerate(clusters):
                authorities: list[tuple[_ElementRecord, list[str]]] = []
                authority_owners: list[str] = []
                best_overlap = 0.0
                has_overlapping_authority = False
                for authority_record in cluster:
                    if (
                        authority_record.owner == source_record.owner
                        or authority_record.source not in {"vector", "geometry"}
                    ):
                        continue
                    authority_bbox = normalize_scene_bbox(
                        authority_record.element.bbox,
                        authority_record.scene,
                        trusted_canvas_size=authority_record.trusted_canvas_size,
                    )
                    if authority_bbox is None:
                        continue
                    overlap = _bbox_iou(source_bbox, authority_bbox)
                    if overlap < self.element_iou_threshold:
                        continue
                    has_overlapping_authority = True
                    best_overlap = max(best_overlap, overlap)
                    authority_owners.append(authority_record.owner)
                    record_authority_evidence_ids = usable_evidence(
                        authority_record,
                        evidence_by_id=authority_evidence_by_owner[authority_record.owner],
                        authority=True,
                    )
                    if record_authority_evidence_ids:
                        authorities.append((authority_record, record_authority_evidence_ids))
                if has_overlapping_authority:
                    overlapping_clusters.append(
                        (
                            cluster_index,
                            best_overlap,
                            authorities,
                            authority_owners,
                        )
                    )

            if len(overlapping_clusters) != 1:
                failures[source_key] = "independent geometry authority is absent or ambiguous"
                continue
            cluster_index, _best_overlap, authorities, authority_owners = overlapping_clusters[0]
            if (
                not authorities
                or len(authority_owners) != len(set(authority_owners))
                or source_record not in clusters[cluster_index]
            ):
                failures[source_key] = (
                    "independent geometry authority evidence is absent or ambiguous"
                )
                continue
            authority_record, authority_evidence_ids = min(
                authorities,
                key=lambda item: (
                    -_GEOMETRY_RANK[item[0].source],
                    item[0].owner,
                    item[0].element.id,
                ),
            )
            authority_bbox = normalize_scene_bbox(
                authority_record.element.bbox,
                authority_record.scene,
                trusted_canvas_size=authority_record.trusted_canvas_size,
            )
            assert authority_bbox is not None
            source_blocks = {
                block_id
                for evidence_id in source_evidence_ids
                for block_id in prior_evidence_by_owner[source_record.owner][
                    evidence_id
                ].source_block_ids
            }
            authority_blocks = {
                block_id
                for evidence_id in authority_evidence_ids
                for block_id in authority_evidence_by_owner[authority_record.owner][
                    evidence_id
                ].source_block_ids
            }
            trusted_blocks = source_record.trusted_source_block_ids.intersection(
                authority_record.trusted_source_block_ids
            )
            if (
                not trusted_blocks
                or not source_blocks.intersection(authority_blocks, trusted_blocks)
            ):
                failures[source_key] = (
                    "source and authority evidence do not share a trusted source block"
                )
                continue
            overlap = _bbox_iou(source_bbox, authority_bbox)
            fused_id = output_ids[cluster_index]
            certified[source_key] = NodeIdMapping(
                source_owner=source_record.owner,
                source_id=source_record.element.id,
                fused_id=fused_id,
                authority_source=authority_record.source,
                authority_owner=authority_record.owner,
                match_method=("identity" if source_record.element.id == fused_id else "unique_iou"),
                iou=overlap,
                source_bbox=source_bbox,
                authority_bbox=authority_bbox,
                source_text=(
                    source_record.element.text
                    if any(
                        prior_evidence_by_owner[source_record.owner][evidence_id].kind
                        in {"ocr_token", "vector_text"}
                        for evidence_id in source_evidence_ids
                    )
                    else None
                ),
                source_evidence_ids=source_evidence_ids,
                authority_evidence_ids=authority_evidence_ids,
            )
            failures.pop(source_key, None)
        return certified, failures

    def _matching_element_cluster(
        self,
        record: _ElementRecord,
        clusters: Sequence[list[_ElementRecord]],
    ) -> int | None:
        exact = [
            index
            for index, cluster in enumerate(clusters)
            if any(item.element.id == record.element.id for item in cluster)
        ]
        if exact:
            return exact[0]

        scored: list[tuple[float, int]] = []
        record_box = _unit_bbox(record.element.bbox, record.scene)
        for index, cluster in enumerate(clusters):
            representative = cluster[0]
            candidate_box = _unit_bbox(representative.element.bbox, representative.scene)
            overlap = _bbox_iou(record_box, candidate_box)
            if overlap >= self.element_iou_threshold:
                scored.append((overlap, index))
        if not scored:
            return None
        return min(scored, key=lambda item: (-item[0], item[1]))[1]

    def _fuse_element_cluster(
        self,
        cluster: Sequence[_ElementRecord],
        evidence: Sequence[VisualEvidence],
    ) -> tuple[SceneElement, list[str]]:
        ordered = sorted(
            cluster,
            key=lambda item: (
                -_GEOMETRY_RANK[item.source],
                item.owner,
                item.element.id,
            ),
        )
        geometry = ordered[0]
        result = geometry.element.model_copy(deep=True)
        result.evidence_ids = sorted(
            {evidence_id for item in ordered for evidence_id in item.element.evidence_ids}
        )
        result.confidence = max(item.element.confidence for item in ordered)
        warnings: list[str] = []
        base_box = _unit_bbox(geometry.element.bbox, geometry.scene)
        for item in ordered[1:]:
            if _bbox_iou(base_box, _unit_bbox(item.element.bbox, item.scene)) < 0.2:
                warnings.append(
                    "fusion element geometry conflict for "
                    f"{result.id!r}; kept {geometry.source} geometry"
                )

        label, label_warning = _select_label(
            ordered,
            evidence,
            base_box,
            geometry.scene,
        )
        result.text = label
        if label_warning:
            warnings.append(f"fusion element {result.id!r} {label_warning}")

        font_weights = {
            item.element.font_weight
            for item in ordered
            if item.element.font_weight is not None
        }
        if len(font_weights) == 1:
            result.font_weight = next(iter(font_weights))
        elif len(font_weights) > 1:
            result.font_weight = None
            warnings.append(
                f"fusion element {result.id!r} font-weight conflict; emphasis omitted"
            )

        # Roles carry semantic meaning, so prefer a non-unknown VLM role even
        # when the shape and position come from vector or CV geometry.
        role_records = sorted(
            (item for item in ordered if item.element.role not in {"", "unknown"}),
            key=lambda item: (-_SEMANTIC_RANK[item.source], item.owner),
        )
        if role_records:
            result.role = role_records[0].element.role
        return result, warnings

    def _matching_relation_cluster(
        self,
        record: _RelationRecord,
        clusters: Sequence[list[_RelationRecord]],
    ) -> int | None:
        endpoints = frozenset((record.mapped_source, record.mapped_target))
        for index, cluster in enumerate(clusters):
            first = cluster[0]
            if record.relation.id == first.relation.id:
                return index
            if record.mapped_source is None and record.mapped_target is None:
                continue
            if endpoints != frozenset((first.mapped_source, first.mapped_target)):
                continue
            existing_labels = {
                _normal_text(item.relation.label) for item in cluster if item.relation.label
            }
            if (
                record.relation.label
                and existing_labels
                and (_normal_text(record.relation.label) not in existing_labels)
            ):
                continue
            return index
        return None

    def _fuse_relation_cluster(
        self, cluster: Sequence[_RelationRecord]
    ) -> tuple[SceneRelation, list[str], bool]:
        geometry_order = sorted(
            cluster,
            key=lambda item: (
                -_GEOMETRY_RANK[item.source],
                item.owner,
                item.relation.id,
            ),
        )
        geometry = geometry_order[0]
        result = geometry.relation.model_copy(deep=True)
        result.source_id = geometry.mapped_source
        result.target_id = geometry.mapped_target
        result.evidence_ids = sorted(
            {evidence_id for item in cluster for evidence_id in item.relation.evidence_ids}
        )
        result.confidence = max(item.relation.confidence for item in cluster)
        warnings: list[str] = []
        direction_conflict = False
        for item in geometry_order[1:]:
            if (
                item.mapped_source,
                item.mapped_target,
                item.relation.arrow_at_start,
                item.relation.arrow_at_end,
            ) != (
                geometry.mapped_source,
                geometry.mapped_target,
                geometry.relation.arrow_at_start,
                geometry.relation.arrow_at_end,
            ):
                direction_conflict = True
                warnings.append(
                    "fusion relation direction conflict for "
                    f"{result.id!r}; kept {geometry.source} direction"
                )

        semantic_order = sorted(
            (
                item
                for item in cluster
                if item.relation.semantic_relation != "unknown"
                or item.relation.relation_type not in {"", "unknown"}
            ),
            key=lambda item: (
                -_SEMANTIC_RANK[item.source],
                item.owner,
                item.relation.id,
            ),
        )
        if semantic_order:
            semantic = semantic_order[0].relation
            result.semantic_relation = semantic.semantic_relation
            result.relation_type = semantic.relation_type
        label_records = [
            (item.source, item.owner, item.relation.label, item.relation.confidence)
            for item in cluster
            if item.relation.label
        ]
        result.label, label_warning = _select_text_records(label_records)
        if label_warning:
            warnings.append(f"fusion relation {result.id!r} {label_warning}")
        return result, warnings, direction_conflict

    def _fuse_groups(
        self,
        inputs: Sequence[FusionInput],
        owners: dict[int, str],
        element_map: dict[tuple[str, str], int],
        output_ids: Sequence[str],
        warnings: list[str],
    ) -> list[SceneGroup]:
        groups: dict[tuple[str, ...], list[tuple[FusionSource, str, SceneGroup, list[str]]]] = {}
        for item in inputs:
            scene = item.observation.scene_ir
            assert scene is not None
            owner = owners[id(item)]
            for group in scene.groups:
                member_ids = sorted(
                    output_ids[element_map[(owner, member_id)]]
                    for member_id in group.member_ids
                    if (owner, member_id) in element_map
                )
                key = tuple(member_ids) if member_ids else (f"id:{group.id}",)
                groups.setdefault(key, []).append((item.source, owner, group, member_ids))

        fused: list[SceneGroup] = []
        used_ids: set[str] = set()
        for key in sorted(groups):
            records = sorted(
                groups[key],
                key=lambda item: (-_GEOMETRY_RANK[item[0]], item[1], item[2].id),
            )
            source, owner, primary, member_ids = records[0]
            result = primary.model_copy(deep=True)
            result.id = _unique_id(result.id, used_ids)
            used_ids.add(result.id)
            result.member_ids = member_ids
            labels = [
                (record_source, record_owner, group.label, 0.5)
                for record_source, record_owner, group, _ in records
                if group.label
            ]
            result.label, label_warning = _select_text_records(labels)
            if label_warning:
                warnings.append(f"fusion group {result.id!r} {label_warning}")
            if any(group.bbox != primary.bbox for _, _, group, _ in records[1:]):
                warnings.append(
                    f"fusion group geometry conflict for {result.id!r}; kept {source} geometry "
                    f"from {owner!r}"
                )
            fused.append(result)
        return fused

    def _fuse_typed_candidates(
        self,
        inputs: Sequence[FusionInput],
        owners: dict[int, str],
        scene_result: _SceneFusionResult | None,
    ) -> tuple[
        list[TypedIRCandidate],
        dict[str, list[NodeIdMapping]],
        dict[str, frozenset[str]],
        list[str],
    ]:
        records: dict[str, tuple[FusionSource, str, TypedIRCandidate]] = {}
        mappings: dict[str, list[NodeIdMapping]] = {}
        authorities: dict[str, frozenset[str]] = {}
        warnings: list[str] = []
        fused_typed_ir_json_bytes = 0
        fused_typed_budget_exhausted = False
        for item in inputs:
            value_candidates, candidate_warnings = _canonical_typed_candidates(
                item.observation.typed_candidates
            )
            warnings.extend(
                f"{warning} from {owners[id(item)]!r}" for warning in candidate_warnings
            )
            for value_candidate in value_candidates:
                candidate_mappings: list[NodeIdMapping] = []
                if (
                    scene_result is not None
                    and item.observation.scene_ir is not None
                    and value_candidate.diagram_type in {"flowchart", "generic_network"}
                ):
                    value_candidate, candidate_mappings, warning = self._harmonize_typed_candidate(
                        value_candidate,
                        owners[id(item)],
                        scene_result,
                    )
                    if warning:
                        warnings.append(warning)
                key = value_candidate.canonical_key()
                old = records.get(key)
                if old is None:
                    ir_snapshot = canonical_typed_ir_snapshot(value_candidate.ir)
                    if ir_snapshot is None:  # pragma: no cover - canonical candidate guard
                        warnings.append(
                            "fusion skipped a typed candidate with a non-canonical IR snapshot"
                        )
                        continue
                    candidate_ir_json_bytes = len(
                        json.dumps(
                            ir_snapshot,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ).encode("utf-8")
                    )
                    if (
                        len(records) >= MAX_OBSERVATION_CANDIDATES
                        or fused_typed_ir_json_bytes + candidate_ir_json_bytes
                        > MAX_OBSERVATION_TYPED_IR_JSON_BYTES
                    ):
                        warnings.append(
                            "fusion stopped typed candidate accumulation at the global "
                            "item or JSON byte budget"
                        )
                        fused_typed_budget_exhausted = True
                        break
                    fused_typed_ir_json_bytes += candidate_ir_json_bytes
                value = (item.source, item.name, value_candidate)
                old_has_mapping = key in mappings
                if (
                    old is None
                    or (candidate_mappings and not old_has_mapping)
                    or (
                        bool(candidate_mappings) == old_has_mapping
                        and _candidate_order(value) < _candidate_order(old)
                    )
                ):
                    records[key] = value
                    if item.publication_evidence_ids is None:
                        authorities.pop(key, None)
                    else:
                        authority_ids = set(item.publication_evidence_ids)
                        for mapping in candidate_mappings:
                            authority_ids.update(mapping.source_evidence_ids)
                            authority_ids.update(mapping.authority_evidence_ids)
                        authorities[key] = frozenset(authority_ids)
                    if candidate_mappings:
                        mappings[key] = candidate_mappings
                    else:
                        mappings.pop(key, None)
            if fused_typed_budget_exhausted:
                break

        ordered_records = sorted(
            records.items(),
            key=lambda item: (
                item[1][2].diagram_type,
                -int(item[0] in mappings),
                -item[1][2].confidence,
                -_SEMANTIC_RANK[item[1][0]],
                item[0],
            ),
        )
        return (
            [value[2] for _, value in ordered_records],
            {
                key: [mapping.model_copy(deep=True) for mapping in mappings[key]]
                for key, _value in ordered_records
                if key in mappings
            },
            {key: authorities[key] for key, _value in ordered_records if key in authorities},
            warnings,
        )

    def _harmonize_typed_candidate(
        self,
        candidate: TypedIRCandidate,
        owner: str,
        scene_result: _SceneFusionResult,
    ) -> tuple[TypedIRCandidate, list[NodeIdMapping], str | None]:
        """Atomically rewrite flat graph references after full independent certification."""

        reason_prefix = f"fusion typed node ID harmonization for {candidate.diagram_type!r}"
        ir = candidate.ir
        nodes = ir.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return candidate.model_copy(deep=True), [], f"{reason_prefix} skipped: invalid nodes"

        source_ids: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: nodes must be objects"),
                )
            source_id = node.get("id")
            if not isinstance(source_id, str) or not source_id:
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: every node needs an explicit string id"),
                )
            source_ids.append(source_id)
        if len(source_ids) != len(set(source_ids)):
            return (
                candidate.model_copy(deep=True),
                [],
                (f"{reason_prefix} skipped: node ids are not unique"),
            )

        target_ids: dict[str, str] = {}
        for source_id in source_ids:
            target_id = scene_result.element_ids.get((owner, source_id))
            if target_id is None:
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: node is absent from the owner Scene IR"),
                )
            target_ids[source_id] = target_id
        if len(set(target_ids.values())) != len(target_ids):
            return (
                candidate.model_copy(deep=True),
                [],
                (f"{reason_prefix} skipped: fused node mapping is not one-to-one"),
            )
        if all(source_id == target_ids[source_id] for source_id in source_ids):
            return candidate.model_copy(deep=True), [], None

        typed_evidence: dict[str, set[str]] = {}
        for node in nodes:
            source_id = node["id"]
            evidence_ids = node.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not all(
                isinstance(value, str) and value for value in evidence_ids
            ):
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: every node needs evidence_ids"),
                )
            typed_evidence[source_id] = set(evidence_ids)

        source_id_set = set(source_ids)
        edges = ir.get("edges", [])
        if not isinstance(edges, list):
            return candidate.model_copy(deep=True), [], f"{reason_prefix} skipped: invalid edges"
        for edge in edges:
            if not isinstance(edge, dict):
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: edges must be objects"),
                )
            source = edge.get("source")
            target = edge.get("target")
            if (
                not isinstance(source, str)
                or not isinstance(target, str)
                or source not in source_id_set
                or target not in source_id_set
            ):
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: edge endpoint is missing or dangling"),
                )

        groups = ir.get("groups", [])
        if not isinstance(groups, list):
            return candidate.model_copy(deep=True), [], f"{reason_prefix} skipped: invalid groups"
        for group in groups:
            if not isinstance(group, dict):
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: groups must be objects"),
                )
            member_ids = group.get("member_ids", [])
            if not isinstance(member_ids, list) or any(
                not isinstance(member_id, str) or member_id not in source_id_set
                for member_id in member_ids
            ):
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (f"{reason_prefix} skipped: group member is missing or dangling"),
                )

        unsupported_reference_keys = {
            "children",
            "member_ids",
            "node_id",
            "node_ids",
            "parent",
            "parent_id",
            "source",
            "source_id",
            "target",
            "target_id",
        }
        root_safe_keys = {
            "acc_description",
            "acc_title",
            "description",
            "direction",
            "edges",
            "groups",
            "nodes",
            "title",
        }
        node_safe_keys = {
            "bbox",
            "border_color",
            "border_style",
            "confidence",
            "evidence_ids",
            "fill_color",
            "font_weight",
            "id",
            "label",
            "polygon",
            "role",
            "shape",
            "text",
        }
        edge_safe_keys = {
            "arrow_at_end",
            "arrow_at_start",
            "bidirectional",
            "confidence",
            "evidence_ids",
            "id",
            "label",
            "line_color",
            "line_style",
            "polyline",
            "relation_type",
            "semantic_relation",
            "source",
            "style",
            "target",
        }
        group_safe_keys = {
            "bbox",
            "border_color",
            "border_style",
            "evidence_ids",
            "fill_color",
            "id",
            "label",
            "member_ids",
            "role",
        }
        container_types = (dict, list, tuple, set, frozenset)
        records_and_container_fields = [
            (ir, {"nodes", "edges", "groups"}),
            *((node, {"bbox", "evidence_ids", "polygon"}) for node in nodes),
            *((edge, {"evidence_ids", "polyline"}) for edge in edges),
            *((group, {"bbox", "evidence_ids", "member_ids"}) for group in groups),
        ]
        if any(
            isinstance(value, container_types) and key not in container_fields
            for record, container_fields in records_and_container_fields
            for key, value in record.items()
        ):
            return (
                candidate.model_copy(deep=True),
                [],
                (f"{reason_prefix} skipped: known scalar field has a nested container"),
            )

        if any(
            (
                node.get("bbox") is not None
                and not (
                    isinstance(node.get("bbox"), list | tuple)
                    and len(node["bbox"]) == 4
                    and all(
                        isinstance(item, int | float)
                        and not isinstance(item, bool)
                        and math.isfinite(item)
                        for item in node["bbox"]
                    )
                )
            )
            or (
                node.get("polygon") is not None
                and not (
                    isinstance(node.get("polygon"), list | tuple)
                    and all(
                        isinstance(point, list | tuple)
                        and len(point) == 2
                        and all(
                            isinstance(item, int | float)
                            and not isinstance(item, bool)
                            and math.isfinite(item)
                            for item in point
                        )
                        for point in node["polygon"]
                    )
                )
            )
            or (
                not isinstance(node.get("evidence_ids"), list)
                or not all(
                    isinstance(item, str) and item for item in node["evidence_ids"]
                )
            )
            for node in nodes
        ) or any(
            (
                edge.get("polyline") is not None
                and not (
                    isinstance(edge.get("polyline"), list | tuple)
                    and all(
                        isinstance(point, list | tuple)
                        and len(point) == 2
                        and all(
                            isinstance(item, int | float)
                            and not isinstance(item, bool)
                            and math.isfinite(item)
                            for item in point
                        )
                        for point in edge["polyline"]
                    )
                )
            )
            or (
                edge.get("evidence_ids") is not None
                and not (
                    isinstance(edge.get("evidence_ids"), list)
                    and all(
                        isinstance(item, str) and item for item in edge["evidence_ids"]
                    )
                )
            )
            for edge in edges
        ) or any(
            (
                group.get("bbox") is not None
                and not (
                    isinstance(group.get("bbox"), list | tuple)
                    and len(group["bbox"]) == 4
                    and all(
                        isinstance(item, int | float)
                        and not isinstance(item, bool)
                        and math.isfinite(item)
                        for item in group["bbox"]
                    )
                )
            )
            or (
                group.get("evidence_ids") is not None
                and not (
                    isinstance(group.get("evidence_ids"), list)
                    and all(
                        isinstance(item, str) and item for item in group["evidence_ids"]
                    )
                )
            )
            for group in groups
        ):
            return (
                candidate.model_copy(deep=True),
                [],
                (f"{reason_prefix} skipped: known container field has an invalid shape"),
            )

        unknown_fields = [(key, value) for key, value in ir.items() if key not in root_safe_keys]
        unknown_fields.extend(
            (key, value)
            for node in nodes
            for key, value in node.items()
            if key not in node_safe_keys
        )
        unknown_fields.extend(
            (key, value)
            for edge in edges
            for key, value in edge.items()
            if key not in edge_safe_keys
        )
        unknown_fields.extend(
            (key, value)
            for group in groups
            for key, value in group.items()
            if key not in group_safe_keys
        )
        unsupported_reference = any(
            key in unsupported_reference_keys
            or isinstance(value, dict | list | tuple | set | frozenset)
            or (isinstance(value, str) and value in source_id_set)
            for key, value in unknown_fields
        )
        if unsupported_reference:
            return (
                candidate.model_copy(deep=True),
                [],
                (f"{reason_prefix} skipped: unsupported nested node reference"),
            )

        certified: list[NodeIdMapping] = []
        for source_id in source_ids:
            mapping = scene_result.certified_mappings.get((owner, source_id))
            if (
                mapping is None
                or mapping.fused_id != target_ids[source_id]
                or not typed_evidence[source_id].intersection(mapping.source_evidence_ids)
            ):
                failure = scene_result.certification_failures.get((owner, source_id))
                detail = f": {failure}" if mapping is None and failure else ""
                return (
                    candidate.model_copy(deep=True),
                    [],
                    (
                        f"{reason_prefix} skipped: full provenance-backed certification failed"
                        f"{detail}"
                    ),
                )
            certified.append(mapping.model_copy(deep=True))

        remapped_ir = candidate.model_copy(deep=True).ir
        for node in remapped_ir["nodes"]:
            node["id"] = target_ids[node["id"]]
        for edge in remapped_ir.get("edges", []):
            edge["source"] = target_ids[edge["source"]]
            edge["target"] = target_ids[edge["target"]]
        for group in remapped_ir.get("groups", []):
            group["member_ids"] = [
                target_ids[member_id] for member_id in group.get("member_ids", [])
            ]
        remapped = TypedIRCandidate.model_validate(
            {
                "diagram_type": candidate.diagram_type,
                "ir": remapped_ir,
                "confidence": candidate.confidence,
            }
        )
        certified.sort(key=lambda item: item.source_id)
        return (
            remapped,
            certified,
            (
                f"{reason_prefix} applied to {len(certified)} nodes using independent "
                "vector/geometry authority"
            ),
        )

    def _fuse_direct_candidates(
        self, inputs: Sequence[FusionInput]
    ) -> tuple[list[DirectMermaidCandidate], dict[str, frozenset[str]]]:
        records: dict[tuple[str, str], tuple[FusionSource, str, DirectMermaidCandidate]] = {}
        authorities: dict[tuple[str, str], frozenset[str]] = {}
        for item in inputs:
            for candidate in item.observation.direct_candidates:
                key = (candidate.diagram_type, candidate.code.strip())
                old = records.get(key)
                value = (item.source, item.name, candidate.model_copy(deep=True))
                if old is None or _candidate_order(value) < _candidate_order(old):
                    records[key] = value
                    if item.publication_evidence_ids is None:
                        authorities.pop(key, None)
                    else:
                        authorities[key] = frozenset(item.publication_evidence_ids)
        ordered_records = sorted(
            records.items(),
            key=lambda item: (
                item[0][0],
                -item[1][2].confidence,
                -_SEMANTIC_RANK[item[1][0]],
                item[0][1],
            ),
        )
        return (
            [value[2] for _, value in ordered_records],
            {
                value[2].canonical_key(): authorities[key]
                for key, value in ordered_records
                if key in authorities
            },
        )


def _owner(item: FusionInput, position: int) -> str:
    return f"{item.name or item.source}#{position:03d}"


def _candidate_order(
    value: tuple[FusionSource, str, TypedIRCandidate | DirectMermaidCandidate],
) -> tuple[float, int, str]:
    confidence = float(value[2].confidence)
    return (-confidence, -_SEMANTIC_RANK[value[0]], value[1])


def _unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _unique_id(candidate: str, used: set[str]) -> str:
    if candidate not in used:
        return candidate
    suffix = 2
    while f"{candidate}__{suffix}" in used:
        suffix += 1
    return f"{candidate}__{suffix}"


def _unit_bbox(
    bbox: tuple[float, float, float, float], scene: DiagramSceneIR
) -> tuple[float, float, float, float]:
    if scene.coordinate_space == "normalized":
        return bbox
    if scene.canvas_size and scene.canvas_size[0] > 0 and scene.canvas_size[1] > 0:
        width, height = scene.canvas_size
        return (bbox[0] / width, bbox[1] / height, bbox[2] / width, bbox[3] / height)
    return bbox


def _strict_unit_bbox(
    bbox: tuple[float, float, float, float],
    scene: DiagramSceneIR,
) -> tuple[float, float, float, float] | None:
    """Normalize only when the Scene declares a comparable coordinate space."""

    if scene.coordinate_space == "normalized":
        normalized = bbox
    elif scene.canvas_size is not None:
        width, height = scene.canvas_size
        normalized = (
            bbox[0] / width,
            bbox[1] / height,
            bbox[2] / width,
            bbox[3] / height,
        )
    else:
        return None
    if not all(0 <= value <= 1 for value in normalized):
        return None
    return normalized


def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _normal_text(value: str) -> str:
    return " ".join(value.split()).casefold()


def _select_label(
    records: Sequence[_ElementRecord],
    evidence: Sequence[VisualEvidence],
    unit_bbox: tuple[float, float, float, float],
    output_scene: DiagramSceneIR,
) -> tuple[str | None, str | None]:
    text_records: list[tuple[FusionSource, str, str | None, float]] = [
        (record.source, record.owner, record.element.text, record.element.confidence)
        for record in records
        if record.element.text
    ]
    for item in evidence:
        if item.kind not in {"vector_text", "ocr_token"} or not item.text or item.bbox is None:
            continue
        # Evidence boxes are expected in the fused scene's coordinate space.
        # Requiring the token centre to lie in the node avoids broad OCR boxes
        # contributing labels to adjacent nodes.
        evidence_bbox = _unit_bbox(item.bbox, output_scene)
        centre = (
            (evidence_bbox[0] + evidence_bbox[2]) / 2,
            (evidence_bbox[1] + evidence_bbox[3]) / 2,
        )
        inside_x = unit_bbox[0] <= centre[0] <= unit_bbox[2]
        inside_y = unit_bbox[1] <= centre[1] <= unit_bbox[3]
        if not (inside_x and inside_y):
            continue
        source: FusionSource = "vector" if item.kind == "vector_text" else "ocr"
        text_records.append((source, item.id, item.text, item.score or 0.5))
    return _select_text_records(text_records)


def _select_text_records(
    records: Sequence[tuple[FusionSource, str, str | None, float]],
) -> tuple[str | None, str | None]:
    populated = [record for record in records if record[2] and record[2].strip()]
    if not populated:
        return None, None

    by_source: dict[str, list[tuple[FusionSource, str, str, float]]] = defaultdict(list)
    for source, owner, text, confidence in populated:
        assert text is not None
        bucket = "vector" if source == "vector" else "ocr" if source == "ocr" else "semantic"
        by_source[bucket].append((source, owner, text.strip(), confidence))

    if by_source["vector"]:
        candidates = by_source["vector"]
        precedence = "vector label"
    elif by_source["ocr"]:
        candidates = by_source["ocr"]
        precedence = "OCR label"
    else:
        candidates = by_source["semantic"]
        precedence = "semantic label"

    grouped: defaultdict[str, list[tuple[FusionSource, str, str, float]]] = defaultdict(list)
    for record in candidates:
        grouped[_normal_text(record[2])].append(record)
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            -len(item[1]),
            -max(record[3] for record in item[1]),
            -max(_SEMANTIC_RANK[record[0]] for record in item[1]),
            item[0],
        ),
    )
    winner_records = ranked[0][1]
    winner = min(
        winner_records,
        key=lambda item: (-item[3], -_SEMANTIC_RANK[item[0]], item[1], item[2]),
    )[2]
    warning = None
    all_values = {_normal_text(record[2]) for record in populated}
    if len(all_values) > 1:
        warning = f"label conflict; selected {precedence} {winner!r}"
    return winner, warning
