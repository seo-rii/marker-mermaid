from __future__ import annotations

import pytest

from marker_mermaid.models import DiagramSceneIR, SceneElement, SceneRelation
from marker_mermaid.quality import (
    align_scene_elements,
    arrow_agreement,
    edge_topology_agreement,
    path_consistency,
    relative_layout_similarity,
)


def _element(
    element_id: str,
    text: str | None,
    bbox: tuple[float, float, float, float],
) -> SceneElement:
    return SceneElement(
        id=element_id,
        role="node",
        text=text,
        bbox=bbox,
        confidence=1,
        evidence_ids=[f"evidence-{element_id}"],
    )


def _relation(
    relation_id: str,
    source_id: str,
    target_id: str,
    *,
    arrow_at_start: bool = False,
    arrow_at_end: bool = True,
) -> SceneRelation:
    return SceneRelation(
        id=relation_id,
        source_id=source_id,
        target_id=target_id,
        relation_type="edge",
        arrow_at_start=arrow_at_start,
        arrow_at_end=arrow_at_end,
        confidence=1,
        evidence_ids=[f"evidence-{relation_id}"],
    )


def _linear_scene(*, reverse: bool = False) -> DiagramSceneIR:
    elements = [
        _element("A", "Start", (0, 0, 10, 10)),
        _element("B", "Review", (40, 0, 50, 10)),
        _element("C", "Done", (80, 0, 90, 10)),
    ]
    if reverse:
        relations = [_relation("BA", "B", "A"), _relation("CB", "C", "B")]
    else:
        relations = [_relation("AB", "A", "B"), _relation("BC", "B", "C")]
    return DiagramSceneIR(elements=elements, relations=relations, canvas_size=(100, 20))


def _dead_branch_scene() -> DiagramSceneIR:
    branch_ids = [f"C{index}" for index in range(6)]
    elements = [
        _element("R", "Root", (0, 0, 1, 1)),
        _element("T", "Terminal", (20, 0, 21, 1)),
        *[
            _element(node_id, node_id, (index * 2 + 2, 2, index * 2 + 3, 3))
            for index, node_id in enumerate(branch_ids)
        ],
    ]
    relations = [
        _relation("RT", "R", "T"),
        _relation("RC0", "R", "C0"),
        *[
            _relation(f"{source}{target}", source, target)
            for source in branch_ids
            for target in branch_ids
            if source != target
        ],
    ]
    return DiagramSceneIR(elements=elements, relations=relations)


def test_alignment_uses_unique_labels_after_ids() -> None:
    source = DiagramSceneIR(
        elements=[_element("A", "Start", (0, 0, 1, 1)), _element("B", "Same", (2, 0, 3, 1))]
    )
    generated = DiagramSceneIR(
        elements=[
            _element("X", "START", (0, 0, 1, 1)),
            _element("Y", "Same", (2, 0, 3, 1)),
            _element("Z", "Same", (4, 0, 5, 1)),
        ]
    )

    alignment = align_scene_elements(source, generated)

    assert alignment.generated_to_source == {"X": "A"}
    assert alignment.unmatched_source_ids == ("B",)
    assert alignment.unmatched_generated_ids == ("Y", "Z")


def test_alignment_uses_collision_free_portable_emitted_id_aliases() -> None:
    source = DiagramSceneIR(
        elements=[
            _element("logical-node", "Original", (0, 0, 1, 1)),
            _element("other", "Other", (2, 0, 3, 1)),
        ]
    )
    generated = DiagramSceneIR(
        elements=[
            _element("logical_node", "Changed", (0, 0, 1, 1)),
            _element("other", "Other", (2, 0, 3, 1)),
        ]
    )

    alignment = align_scene_elements(source, generated)

    assert alignment.generated_to_source["logical_node"] == "logical-node"

    collision_source = DiagramSceneIR(
        elements=[
            _element("A-B", "First", (0, 0, 1, 1)),
            _element("A_B", "Second", (2, 0, 3, 1)),
        ]
    )
    collision_generated = DiagramSceneIR(
        elements=[
            _element("A_B", "Changed", (0, 0, 1, 1)),
            _element("A_B_2", "Changed too", (2, 0, 3, 1)),
        ]
    )

    collision = align_scene_elements(collision_source, collision_generated)

    assert "A_B" not in collision.generated_to_source
    assert "A_B_2" in collision.unmatched_generated_ids


def test_edge_topology_ignores_direction_but_detects_missing_edge() -> None:
    source = _linear_scene()
    generated = DiagramSceneIR(
        elements=source.elements,
        relations=[_relation("BA", "B", "A")],
    )

    result = edge_topology_agreement(source, generated)

    assert result.available
    assert result.value == pytest.approx(2 / 3)


def test_arrow_agreement_detects_direction_errors() -> None:
    result = arrow_agreement(_linear_scene(), _linear_scene(reverse=True))

    assert result.available
    assert result.value == 0


def test_arrow_agreement_is_unavailable_without_explicit_source_arrows() -> None:
    scene = DiagramSceneIR(
        elements=[_element("A", "A", (0, 0, 1, 1)), _element("B", "B", (2, 0, 3, 1))],
        relations=[_relation("AB", "A", "B", arrow_at_end=False)],
    )

    result = arrow_agreement(scene, scene)

    assert not result.available
    assert result.value is None
    assert "no explicit arrowheads" in (result.warning or "")


def test_relative_layout_is_scale_invariant_and_detects_reversal() -> None:
    source = _linear_scene()
    scaled = DiagramSceneIR(
        elements=[
            _element("A", "Start", (0, 0, 20, 30)),
            _element("B", "Review", (200, 0, 220, 30)),
            _element("C", "Done", (400, 0, 420, 30)),
        ],
        canvas_size=(500, 100),
    )
    reversed_layout = DiagramSceneIR(
        elements=[
            _element("A", "Start", (80, 0, 90, 10)),
            _element("B", "Review", (40, 0, 50, 10)),
            _element("C", "Done", (0, 0, 10, 10)),
        ],
        canvas_size=(100, 20),
    )

    assert relative_layout_similarity(source, scaled).value == 1
    assert relative_layout_similarity(source, reversed_layout).value == 0


def test_relative_layout_is_unavailable_with_only_one_match() -> None:
    source = _linear_scene()
    generated = DiagramSceneIR(elements=[_element("X", "Start", (0, 0, 1, 1))])

    result = relative_layout_similarity(source, generated)

    assert not result.available
    assert "fewer than two" in (result.warning or "")


def test_relative_layout_is_unavailable_without_generated_positions() -> None:
    generated = DiagramSceneIR(
        elements=[
            _element("A", "Start", (0, 0, 0, 0)),
            _element("B", "Review", (0, 0, 0, 0)),
            _element("C", "Done", (0, 0, 0, 0)),
        ]
    )

    result = relative_layout_similarity(_linear_scene(), generated)

    assert not result.available
    assert "no explicit relative layout" in (result.warning or "")


def test_path_consistency_detects_topology_mismatch() -> None:
    source = _linear_scene()
    generated = DiagramSceneIR(
        elements=source.elements,
        relations=[_relation("AC", "A", "C")],
    )

    result = path_consistency(source, generated)

    assert result.available
    assert result.value == 0


def test_path_consistency_matches_branches_independent_of_relation_order() -> None:
    elements = [
        _element("A", "Start", (0, 0, 10, 10)),
        _element("B", "Yes", (20, 0, 30, 10)),
        _element("C", "No", (20, 20, 30, 30)),
        _element("D", "End", (40, 10, 50, 20)),
    ]
    source = DiagramSceneIR(
        elements=elements,
        relations=[
            _relation("AB", "A", "B"),
            _relation("AC", "A", "C"),
            _relation("BD", "B", "D"),
            _relation("CD", "C", "D"),
        ],
    )
    generated = DiagramSceneIR(elements=elements, relations=list(reversed(source.relations)))

    result = path_consistency(source, generated)

    assert result.available
    assert result.value == 1


def test_path_consistency_is_unavailable_for_unrooted_cycle() -> None:
    elements = [_element("A", "A", (0, 0, 1, 1)), _element("B", "B", (2, 0, 3, 1))]
    cyclic = DiagramSceneIR(
        elements=elements,
        relations=[_relation("AB", "A", "B"), _relation("BA", "B", "A")],
    )

    result = path_consistency(cyclic, cyclic)

    assert not result.available
    assert result.value is None
    assert "no root" in (result.warning or "")


def test_path_consistency_is_unavailable_when_enumeration_budget_is_exceeded() -> None:
    elements = [
        _element("A", "Start", (0, 0, 1, 1)),
        _element("B", "Left", (2, 0, 3, 1)),
        _element("C", "Right", (2, 2, 3, 3)),
    ]
    branched = DiagramSceneIR(
        elements=elements,
        relations=[_relation("AB", "A", "B"), _relation("AC", "A", "C")],
    )

    result = path_consistency(branched, branched, max_paths=1)

    assert not result.available
    assert "budget" in (result.warning or "")


def test_path_consistency_bounds_search_states_before_dead_branch_expansion() -> None:
    result = path_consistency(_dead_branch_scene(), _dead_branch_scene(), max_states=10)

    assert not result.available
    assert "10-state budget" in (result.warning or "")


def test_generated_path_state_budget_is_reported_without_partial_score() -> None:
    source = DiagramSceneIR(
        elements=[
            _element("R", "Root", (0, 0, 1, 1)),
            _element("T", "Terminal", (20, 0, 21, 1)),
        ],
        relations=[_relation("RT", "R", "T")],
    )

    result = path_consistency(source, _dead_branch_scene(), max_states=10)

    assert not result.available
    assert "generated path enumeration exceeded the 10-state budget" in (
        result.warning or ""
    )


def test_path_consistency_rejects_nonpositive_state_budget() -> None:
    with pytest.raises(ValueError, match="max_states must be positive"):
        path_consistency(_linear_scene(), _linear_scene(), max_states=0)


def test_missing_generated_structure_is_a_real_zero_not_unavailable() -> None:
    empty = DiagramSceneIR()

    for result in (
        edge_topology_agreement(_linear_scene(), empty),
        arrow_agreement(_linear_scene(), empty),
        path_consistency(_linear_scene(), empty),
    ):
        assert result.available
        assert result.value == 0
