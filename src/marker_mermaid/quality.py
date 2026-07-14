"""Conservative structural quality metrics for reconstructed diagram scenes.

The helpers in this module compare a source :class:`DiagramSceneIR` with a
scene reconstructed from a rendered Mermaid candidate.  They deliberately
return an unavailable ``MetricResult`` when correspondence or required source
structure cannot be established.  In particular, missing arrows are not
silently interpreted as ``source -> target`` and cyclic graphs without a root
are not assigned an arbitrary path score.

Element correspondence uses identical IDs, collision-free portable emitted-ID aliases,
then unique normalized text labels. Geometry is compared through relative ordering, so
translation and uniform or non-uniform scaling do not affect the layout score.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations

from marker_mermaid.flowchart_structure import (
    ambiguous_portable_ids,
    unique_portable_id_aliases,
)
from marker_mermaid.models import (
    DiagramSceneIR,
    MetricResult,
    SceneElement,
    SceneRelation,
    VisualEvidence,
)

_NODE_PROVENANCE_KINDS = frozenset(
    {
        "contour",
        "ocr_token",
        "user_edit",
        "vector_text",
        "vlm_observation",
    }
)


@dataclass(frozen=True, slots=True)
class SceneAlignment:
    """A deterministic generated-element to source-element correspondence."""

    generated_to_source: dict[str, str]
    unmatched_source_ids: tuple[str, ...]
    unmatched_generated_ids: tuple[str, ...]


def injective_node_provenance_counts(
    node_evidence_ids: Iterable[Iterable[str]],
    evidence: Iterable[VisualEvidence],
) -> tuple[int, int]:
    """Count nodes with one unambiguous, node-eligible evidence claim.

    Text, shape, VLM, and user-edit evidence is node-specific. If two generated
    nodes claim the same record, that record supports neither node; another
    collision-free record can still support either node. Source crops and
    connector evidence remain available to group/relation metrics but do not
    establish generated-node provenance.
    """

    evidence_by_id: dict[str, VisualEvidence] = {}
    duplicate_ids: set[str] = set()
    for item in evidence:
        if item.id in evidence_by_id:
            duplicate_ids.add(item.id)
        else:
            evidence_by_id[item.id] = item
    for evidence_id in duplicate_ids:
        evidence_by_id.pop(evidence_id, None)

    eligible_ids = {
        evidence_id
        for evidence_id, item in evidence_by_id.items()
        if item.kind in _NODE_PROVENANCE_KINDS
    }
    claims_by_node: list[frozenset[str]] = []
    claim_counts: Counter[str] = Counter()
    for values in node_evidence_ids:
        claims = frozenset(evidence_id for evidence_id in values if evidence_id in eligible_ids)
        claims_by_node.append(claims)
        claim_counts.update(claims)

    supported = sum(
        any(claim_counts[evidence_id] == 1 for evidence_id in claims) for claims in claims_by_node
    )
    return supported, len(claims_by_node)


def align_scene_elements(source: DiagramSceneIR, generated: DiagramSceneIR) -> SceneAlignment:
    """Align by stable/emitted ID and then by unique, non-empty label.

    Ambiguous duplicate labels are intentionally left unmatched.  Geometry is
    not used for matching because doing so would make the layout metric partly
    validate its own assumption.
    """

    source_ids = {element.id for element in source.elements}
    generated_ids = {element.id for element in generated.elements}
    ambiguous_source_ids, ambiguous_emitted_ids = ambiguous_portable_ids(
        [element.id for element in source.elements]
    )
    mapping = {
        element_id: element_id
        for element_id in source_ids & generated_ids
        if element_id not in ambiguous_source_ids
        and element_id not in ambiguous_emitted_ids
    }

    unmatched_source = source_ids - set(mapping.values())
    unmatched_generated = generated_ids - set(mapping)

    portable_source_ids = unique_portable_id_aliases(
        [element.id for element in source.elements if element.id in unmatched_source]
    )
    for generated_id in tuple(unmatched_generated):
        source_id = portable_source_ids.get(generated_id)
        if source_id is not None:
            mapping[generated_id] = source_id

    unmatched_source = source_ids - set(mapping.values())
    unmatched_generated = generated_ids - set(mapping)

    source_labels = _unique_label_index(
        element for element in source.elements if element.id in unmatched_source
    )
    generated_labels = _unique_label_index(
        element for element in generated.elements if element.id in unmatched_generated
    )
    for label in source_labels.keys() & generated_labels.keys():
        source_id = source_labels[label]
        generated_id = generated_labels[label]
        if source_id is not None and generated_id is not None:
            mapping[generated_id] = source_id

    return SceneAlignment(
        generated_to_source=mapping,
        unmatched_source_ids=tuple(sorted(source_ids - set(mapping.values()))),
        unmatched_generated_ids=tuple(sorted(generated_ids - set(mapping))),
    )


def edge_topology_agreement(
    source: DiagramSceneIR,
    generated: DiagramSceneIR,
) -> MetricResult:
    """Return multiset F1 for resolved, direction-independent scene edges.

    Direction is evaluated separately by :func:`arrow_agreement`.  Parallel
    edges therefore count independently here, while labels and line styles do
    not.  Relations with an unresolved source or target are excluded.
    """

    source_edges = _edge_counter(source.relations)
    if not source_edges:
        return _unavailable("edge_agreement", "source scene has no resolved relations")

    alignment = align_scene_elements(source, generated)
    if not generated.elements:
        return _available("edge_agreement", 0.0, source)
    if not alignment.generated_to_source:
        return _unavailable("edge_agreement", "no scene elements could be aligned")

    generated_edges = _edge_counter(
        generated.relations,
        generated_to_source=alignment.generated_to_source,
        preserve_unmatched=True,
    )
    return _available(
        "edge_agreement",
        _counter_f1(source_edges, generated_edges),
        source,
        generated,
    )


def arrow_agreement(source: DiagramSceneIR, generated: DiagramSceneIR) -> MetricResult:
    """Return multiset F1 for arrowheads attached to aligned edge endpoints.

    A conventional ``source -> target`` relation contributes the target node
    as its arrow endpoint.  Reversing that relation therefore yields zero
    overlap.  Double-headed edges contribute two endpoints, and relations with
    no explicit arrow flag are ignored rather than assigned a direction.
    """

    source_arrows = _arrow_counter(source.relations)
    if not source_arrows:
        return _unavailable("arrow_agreement", "source scene has no explicit arrowheads")

    alignment = align_scene_elements(source, generated)
    if not generated.elements:
        return _available("arrow_agreement", 0.0, source)
    if not alignment.generated_to_source:
        return _unavailable("arrow_agreement", "no scene elements could be aligned")

    generated_arrows = _arrow_counter(
        generated.relations,
        generated_to_source=alignment.generated_to_source,
        preserve_unmatched=True,
    )
    return _available(
        "arrow_agreement",
        _counter_f1(source_arrows, generated_arrows),
        source,
        generated,
    )


def relative_layout_similarity(
    source: DiagramSceneIR,
    generated: DiagramSceneIR,
    *,
    separation_ratio: float = 0.03,
) -> MetricResult:
    """Compare pairwise left/right and above/below ordering of aligned nodes.

    A source-axis comparison is used only when the source centers are separated
    by more than ``separation_ratio`` of the source canvas extent on that axis.
    A generated tie does not satisfy a separated source relation.  At least two
    aligned nodes and one significant source-axis relation are required.
    """

    if not 0 <= separation_ratio < 1:
        raise ValueError("separation_ratio must be between 0 (inclusive) and 1")

    alignment = align_scene_elements(source, generated)
    if len(alignment.generated_to_source) < 2:
        return _unavailable("layout_similarity", "fewer than two elements could be aligned")

    source_by_id = {element.id: element for element in source.elements}
    generated_by_source = {
        source_id: next(element for element in generated.elements if element.id == generated_id)
        for generated_id, source_id in alignment.generated_to_source.items()
    }
    if len({_center(element) for element in generated_by_source.values()}) < 2:
        return _unavailable(
            "layout_similarity",
            "generated scene has no explicit relative layout",
        )
    common_ids = sorted(generated_by_source)
    source_width, source_height = _scene_extent(source)
    generated_width, generated_height = _scene_extent(generated)
    source_thresholds = (source_width * separation_ratio, source_height * separation_ratio)
    generated_thresholds = (
        generated_width * separation_ratio,
        generated_height * separation_ratio,
    )

    agreements: list[float] = []
    for left_id, right_id in combinations(common_ids, 2):
        source_left = _center(source_by_id[left_id])
        source_right = _center(source_by_id[right_id])
        generated_left = _center(generated_by_source[left_id])
        generated_right = _center(generated_by_source[right_id])
        for axis in (0, 1):
            source_delta = source_right[axis] - source_left[axis]
            if abs(source_delta) <= source_thresholds[axis]:
                continue
            generated_delta = generated_right[axis] - generated_left[axis]
            if abs(generated_delta) <= generated_thresholds[axis]:
                agreements.append(0.0)
            else:
                agreements.append(float((source_delta > 0) == (generated_delta > 0)))

    if not agreements:
        return _unavailable(
            "layout_similarity",
            "aligned source elements have no significant relative separation",
        )
    return _available(
        "layout_similarity",
        sum(agreements) / len(agreements),
        source,
        generated,
    )


def path_consistency(
    source: DiagramSceneIR,
    generated: DiagramSceneIR,
    *,
    max_paths: int = 10_000,
    max_states: int = 100_000,
    max_depth: int | None = None,
) -> MetricResult:
    """Return multiset F1 for explicit directed root-to-terminal paths.

    Paths are simple (a node cannot repeat) and must contain at least one edge.
    Explicit arrowheads determine direction.  If enumeration reaches the
    supplied path, state, or depth budget, the metric is unavailable rather
    than based on a partial path set.
    """

    if max_paths < 1:
        raise ValueError("max_paths must be positive")
    if max_states < 1:
        raise ValueError("max_states must be positive")
    if max_depth is not None and max_depth < 2:
        raise ValueError("max_depth must be at least 2")

    source_paths, source_warning = _root_to_terminal_paths(
        source,
        max_paths=max_paths,
        max_states=max_states,
        max_depth=max_depth,
    )
    if source_warning is not None:
        return _unavailable("path_consistency", source_warning)
    if not source_paths:
        return _unavailable(
            "path_consistency",
            "source scene has no explicit directed root-to-terminal paths",
        )

    alignment = align_scene_elements(source, generated)
    if not generated.elements:
        return _available("path_consistency", 0.0, source)
    if not alignment.generated_to_source:
        return _unavailable("path_consistency", "no scene elements could be aligned")

    generated_paths, generated_warning = _root_to_terminal_paths(
        generated,
        max_paths=max_paths,
        max_states=max_states,
        max_depth=max_depth,
    )
    if generated_warning is not None:
        return _unavailable("path_consistency", f"generated {generated_warning}")

    mapped_generated_paths = Counter(
        tuple(
            alignment.generated_to_source.get(node_id, f"generated:{node_id}") for node_id in path
        )
        for path in generated_paths
    )
    return _available(
        "path_consistency",
        _counter_f1(Counter(source_paths), mapped_generated_paths),
        source,
        generated,
    )


def _normalize_label(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.findall(r"[\w가-힣ぁ-んァ-ン一-龥]+", normalized))


def _unique_label_index(elements: Iterable[SceneElement]) -> dict[str, str | None]:
    index: dict[str, str | None] = {}
    for element in elements:
        label = _normalize_label(element.text)
        if not label:
            continue
        index[label] = element.id if label not in index else None
    return index


def _mapped_id(
    element_id: str,
    generated_to_source: dict[str, str] | None,
    preserve_unmatched: bool,
) -> str | None:
    if generated_to_source is None:
        return element_id
    if element_id in generated_to_source:
        return generated_to_source[element_id]
    return f"generated:{element_id}" if preserve_unmatched else None


def _edge_counter(
    relations: list[SceneRelation],
    *,
    generated_to_source: dict[str, str] | None = None,
    preserve_unmatched: bool = False,
) -> Counter[tuple[str, str]]:
    edges: Counter[tuple[str, str]] = Counter()
    for relation in relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        source_id = _mapped_id(relation.source_id, generated_to_source, preserve_unmatched)
        target_id = _mapped_id(relation.target_id, generated_to_source, preserve_unmatched)
        if source_id is None or target_id is None:
            continue
        edges[tuple(sorted((source_id, target_id)))] += 1
    return edges


def _arrow_counter(
    relations: list[SceneRelation],
    *,
    generated_to_source: dict[str, str] | None = None,
    preserve_unmatched: bool = False,
) -> Counter[tuple[tuple[str, str], str]]:
    arrows: Counter[tuple[tuple[str, str], str]] = Counter()
    for relation in relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        source_id = _mapped_id(relation.source_id, generated_to_source, preserve_unmatched)
        target_id = _mapped_id(relation.target_id, generated_to_source, preserve_unmatched)
        if source_id is None or target_id is None:
            continue
        edge = tuple(sorted((source_id, target_id)))
        if relation.arrow_at_start:
            arrows[(edge, source_id)] += 1
        if relation.arrow_at_end:
            arrows[(edge, target_id)] += 1
    return arrows


def _counter_f1(reference: Counter[object], candidate: Counter[object]) -> float:
    reference_count = reference.total()
    candidate_count = candidate.total()
    if reference_count == 0:
        raise ValueError("reference counter must not be empty")
    if candidate_count == 0:
        return 0.0
    overlap = sum((reference & candidate).values())
    precision = overlap / candidate_count
    recall = overlap / reference_count
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _center(element: SceneElement) -> tuple[float, float]:
    x1, y1, x2, y2 = element.bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _scene_extent(scene: DiagramSceneIR) -> tuple[float, float]:
    if scene.canvas_size is not None:
        return max(scene.canvas_size[0], 1e-9), max(scene.canvas_size[1], 1e-9)
    x_values = [
        coordinate
        for element in scene.elements
        for coordinate in (element.bbox[0], element.bbox[2])
    ]
    y_values = [
        coordinate
        for element in scene.elements
        for coordinate in (element.bbox[1], element.bbox[3])
    ]
    return max(max(x_values) - min(x_values), 1e-9), max(max(y_values) - min(y_values), 1e-9)


def _directed_adjacency(scene: DiagramSceneIR) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for relation in scene.relations:
        if relation.source_id is None or relation.target_id is None:
            continue
        if relation.arrow_at_end:
            adjacency[relation.source_id].add(relation.target_id)
        if relation.arrow_at_start:
            adjacency[relation.target_id].add(relation.source_id)
    return adjacency


def _root_to_terminal_paths(
    scene: DiagramSceneIR,
    *,
    max_paths: int,
    max_states: int,
    max_depth: int | None,
) -> tuple[list[tuple[str, ...]], str | None]:
    adjacency = _directed_adjacency(scene)
    nodes = set(adjacency) | {target for targets in adjacency.values() for target in targets}
    if not nodes:
        return [], None
    indegree = Counter(target for targets in adjacency.values() for target in targets)
    roots = sorted(node for node in nodes if indegree[node] == 0)
    terminals = {node for node in nodes if not adjacency.get(node)}
    if not roots:
        return [], "directed graph has no root (it may be cyclic)"
    if not terminals:
        return [], "directed graph has no terminal (it may be cyclic)"

    depth_limit = max_depth or len(nodes)
    paths: list[tuple[str, ...]] = []
    stack = [(root, (root,)) for root in reversed(roots)]
    if len(stack) > max_states:
        return [], f"path enumeration exceeded the {max_states}-state budget"
    expanded = 0
    while stack:
        expanded += 1
        if expanded > max_states:
            return [], f"path enumeration exceeded the {max_states}-state budget"
        node, path = stack.pop()
        if node in terminals and len(path) >= 2:
            paths.append(path)
            if len(paths) > max_paths:
                return [], f"path enumeration exceeded the {max_paths}-path budget"
            continue
        if len(path) >= depth_limit:
            if any(target not in path for target in adjacency.get(node, ())):
                return [], f"path enumeration exceeded the {depth_limit}-node depth budget"
            continue
        for target in sorted(adjacency.get(node, ()), reverse=True):
            if target not in path:
                if expanded + len(stack) >= max_states:
                    return [], f"path enumeration exceeded the {max_states}-state budget"
                stack.append((target, (*path, target)))
    return paths, None


def _evidence_ids(*scenes: DiagramSceneIR) -> list[str]:
    return sorted(
        {
            evidence_id
            for scene in scenes
            for item in (*scene.elements, *scene.relations)
            for evidence_id in item.evidence_ids
        }
    )


def _available(name: str, value: float, *scenes: DiagramSceneIR) -> MetricResult:
    return MetricResult(
        name=name,
        value=value,
        available=True,
        evidence_ids=_evidence_ids(*scenes),
    )


def _unavailable(name: str, warning: str) -> MetricResult:
    return MetricResult(name=name, available=False, warning=warning)
