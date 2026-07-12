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

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    SceneElement,
    SceneGroup,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
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


@dataclass(frozen=True, slots=True)
class FusionInput:
    """An observation plus its explicit, stable origin.

    ``name`` should be the engine instance name when available.  It is used
    only as a deterministic tie-breaker and in conflict diagnostics.
    """

    source: FusionSource
    observation: EngineObservation
    name: str = ""


@dataclass(slots=True)
class _ElementRecord:
    owner: str
    source: FusionSource
    element: SceneElement
    scene: DiagramSceneIR


@dataclass(slots=True)
class _RelationRecord:
    owner: str
    source: FusionSource
    relation: SceneRelation
    mapped_source: str | None
    mapped_target: str | None


class FusionEngine:
    """Merge independently produced observations into one observation."""

    name = "deterministic_fusion"

    def __init__(self, *, element_iou_threshold: float = 0.45):
        if not 0 <= element_iou_threshold <= 1:
            raise ValueError("element_iou_threshold must be between 0 and 1")
        self.element_iou_threshold = element_iou_threshold

    def fuse(self, inputs: Iterable[FusionInput]) -> EngineObservation:
        """Fuse observations using stable precedence and spatial matching."""

        ordered = self._ordered_inputs(inputs)
        if not ordered:
            raise ValueError("at least one fusion input is required")

        warnings = _unique(
            warning for item in ordered for warning in item.observation.warnings if warning
        )
        evidence, evidence_warnings = self._fuse_evidence(ordered)
        warnings.extend(evidence_warnings)

        scene_inputs = [item for item in ordered if item.observation.scene_ir is not None]
        scene_ir: DiagramSceneIR | None = None
        if scene_inputs:
            scene_ir, scene_warnings = self._fuse_scenes(scene_inputs, evidence)
            warnings.extend(scene_warnings)

        prediction = self._fuse_predictions(ordered)
        if scene_ir is not None:
            scene_ir.diagram_type_candidates = list(prediction.candidates)

        return EngineObservation(
            prediction=prediction,
            scene_ir=scene_ir,
            typed_candidates=self._fuse_typed_candidates(ordered),
            direct_candidates=self._fuse_direct_candidates(ordered),
            evidence=evidence,
            warnings=_unique(warnings),
        )

    def _ordered_inputs(self, inputs: Iterable[FusionInput]) -> list[FusionInput]:
        values = list(inputs)
        for value in values:
            if not isinstance(value, FusionInput):
                raise TypeError("fusion inputs must be FusionInput instances")
        return sorted(
            values,
            key=lambda item: (
                -_GEOMETRY_RANK[item.source],
                item.source,
                item.name,
                item.observation.model_dump_json(exclude_none=True),
            ),
        )

    def _fuse_evidence(
        self, inputs: Sequence[FusionInput]
    ) -> tuple[list[VisualEvidence], list[str]]:
        records: dict[str, tuple[FusionSource, str, VisualEvidence]] = {}
        warnings: list[str] = []
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
        return [records[key][2] for key in sorted(records)], warnings

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
    ) -> tuple[DiagramSceneIR, list[str]]:
        warnings: list[str] = []
        clusters: list[list[_ElementRecord]] = []
        element_map: dict[tuple[str, str], int] = {}

        for position, item in enumerate(inputs):
            scene = item.observation.scene_ir
            assert scene is not None
            owner = _owner(item, position)
            for element in sorted(scene.elements, key=lambda value: value.id):
                record = _ElementRecord(owner, item.source, element, scene)
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
        for position, item in enumerate(inputs):
            scene = item.observation.scene_ir
            assert scene is not None
            owner = _owner(item, position)
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
        for cluster in relation_clusters:
            fused, cluster_warnings = self._fuse_relation_cluster(cluster)
            fused.id = _unique_id(fused.id, used_relation_ids)
            used_relation_ids.add(fused.id)
            fused_relations.append(fused)
            warnings.extend(cluster_warnings)

        fused_groups = self._fuse_groups(inputs, element_map, output_ids, warnings)
        primary = inputs[0].observation.scene_ir
        assert primary is not None
        direction = "unknown"
        direction_owner = ""
        for position, item in enumerate(inputs):
            scene = item.observation.scene_ir
            assert scene is not None
            if scene.reading_direction == "unknown":
                continue
            if direction == "unknown":
                direction = scene.reading_direction
                direction_owner = _owner(item, position)
            elif scene.reading_direction != direction:
                warnings.append(
                    "fusion reading-direction conflict; "
                    f"kept {direction!r} from {direction_owner!r} over "
                    f"{scene.reading_direction!r} from {_owner(item, position)!r}"
                )

        return (
            DiagramSceneIR(
                elements=fused_elements,
                relations=fused_relations,
                groups=fused_groups,
                reading_direction=direction,
                diagram_type_candidates=[],
                coordinate_space=primary.coordinate_space,
                canvas_size=primary.canvas_size,
            ),
            warnings,
        )

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
    ) -> tuple[SceneRelation, list[str]]:
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
        for item in geometry_order[1:]:
            if (item.mapped_source, item.mapped_target) == (
                geometry.mapped_target,
                geometry.mapped_source,
            ):
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
        return result, warnings

    def _fuse_groups(
        self,
        inputs: Sequence[FusionInput],
        element_map: dict[tuple[str, str], int],
        output_ids: Sequence[str],
        warnings: list[str],
    ) -> list[SceneGroup]:
        groups: dict[tuple[str, ...], list[tuple[FusionSource, str, SceneGroup, list[str]]]] = {}
        for position, item in enumerate(inputs):
            scene = item.observation.scene_ir
            assert scene is not None
            owner = _owner(item, position)
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

    def _fuse_typed_candidates(self, inputs: Sequence[FusionInput]) -> list[TypedIRCandidate]:
        records: dict[tuple[str, str], tuple[FusionSource, str, TypedIRCandidate]] = {}
        for item in inputs:
            for candidate in item.observation.typed_candidates:
                payload = json.dumps(
                    candidate.ir,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                key = (candidate.diagram_type, payload)
                old = records.get(key)
                value = (item.source, item.name, candidate.model_copy(deep=True))
                if old is None or _candidate_order(value) < _candidate_order(old):
                    records[key] = value
        return [
            value[2]
            for _, value in sorted(
                records.items(),
                key=lambda item: (
                    item[0][0],
                    -item[1][2].confidence,
                    -_SEMANTIC_RANK[item[1][0]],
                    item[0][1],
                ),
            )
        ]

    def _fuse_direct_candidates(
        self, inputs: Sequence[FusionInput]
    ) -> list[DirectMermaidCandidate]:
        records: dict[tuple[str, str], tuple[FusionSource, str, DirectMermaidCandidate]] = {}
        for item in inputs:
            for candidate in item.observation.direct_candidates:
                key = (candidate.diagram_type, candidate.code.strip())
                old = records.get(key)
                value = (item.source, item.name, candidate.model_copy(deep=True))
                if old is None or _candidate_order(value) < _candidate_order(old):
                    records[key] = value
        return [
            value[2]
            for _, value in sorted(
                records.items(),
                key=lambda item: (
                    item[0][0],
                    -item[1][2].confidence,
                    -_SEMANTIC_RANK[item[1][0]],
                    item[0][1],
                ),
            )
        ]


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
