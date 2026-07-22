from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

import marker_mermaid.fusion as fusion_module
import marker_mermaid.models as models_module
from marker_mermaid.fusion import FusionEngine, FusionInput
from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    DiagramSceneIR,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    SceneElement,
    SceneGroup,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
    canonical_typed_ir_snapshot,
)


def _harmonization_authority() -> FusionInput:
    return FusionInput(
        "geometry",
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.8]),
            scene_ir=DiagramSceneIR(
                elements=[
                    SceneElement(
                        id="geometry-node-001",
                        role="unknown",
                        bbox=(10, 10, 40, 40),
                        confidence=0.8,
                        evidence_ids=["contour-a"],
                    ),
                    SceneElement(
                        id="geometry-node-002",
                        role="unknown",
                        bbox=(60, 10, 90, 40),
                        confidence=0.8,
                        evidence_ids=["contour-b"],
                    ),
                ],
                relations=[
                    SceneRelation(
                        id="geometry-edge",
                        source_id="geometry-node-001",
                        target_id="geometry-node-002",
                        relation_type="directed_connector",
                        polyline=[(40, 25), (60, 25)],
                        confidence=0.8,
                        evidence_ids=["line-a-b", "arrow-b"],
                    )
                ],
                reading_direction="LR",
                canvas_size=(100, 100),
            ),
            evidence=[
                VisualEvidence(
                    id="contour-a",
                    kind="contour",
                    bbox=(10, 10, 40, 40),
                    score=0.8,
                    source_block_ids=["source"],
                ),
                VisualEvidence(
                    id="contour-b",
                    kind="contour",
                    bbox=(60, 10, 90, 40),
                    score=0.8,
                    source_block_ids=["source"],
                ),
                VisualEvidence(
                    id="line-a-b",
                    kind="line_segment",
                    bbox=(40, 25, 60, 25),
                    score=0.8,
                    source_block_ids=["source"],
                ),
                VisualEvidence(
                    id="arrow-b",
                    kind="arrowhead",
                    bbox=(58, 23, 60, 27),
                    score=0.8,
                    source_block_ids=["source"],
                ),
            ],
        ),
        "geometry",
        publication_evidence_ids=frozenset({"contour-a", "contour-b", "line-a-b", "arrow-b"}),
        trusted_canvas_size=(100, 100),
        trusted_source_block_ids=frozenset({"source"}),
    )


def _harmonization_semantic(
    *,
    diagram_type="flowchart",
    node_ids=("A", "B"),
    name="vlm",
    confidence=0.9,
    include_evidence=True,
) -> FusionInput:
    first_id, second_id = node_ids
    all_evidence = [
        VisualEvidence(
            id="text-a",
            kind="ocr_token",
            text="Approve?",
            bbox=(15, 15, 35, 25),
            score=0.95,
            source_block_ids=["source"],
        ),
        VisualEvidence(
            id="text-b",
            kind="ocr_token",
            text="Continue",
            bbox=(65, 15, 85, 25),
            score=0.95,
            source_block_ids=["source"],
        ),
        VisualEvidence(
            id="branch-yes",
            kind="ocr_token",
            text="Yes",
            bbox=(45, 20, 55, 24),
            score=0.95,
            source_block_ids=["source"],
        ),
        VisualEvidence(
            id="branch-no",
            kind="ocr_token",
            text="No",
            bbox=(45, 27, 55, 31),
            score=0.95,
            source_block_ids=["source"],
        ),
    ]
    evidence = all_evidence if include_evidence else []
    return FusionInput(
        "vlm",
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=[diagram_type], scores=[0.9]),
            scene_ir=DiagramSceneIR(
                elements=[
                    SceneElement(
                        id=second_id,
                        role="process",
                        text="Continue",
                        bbox=(60, 10, 90, 40),
                        confidence=0.9,
                        evidence_ids=["text-b"],
                    ),
                    SceneElement(
                        id=first_id,
                        role="decision",
                        text="Approve?",
                        bbox=(10, 10, 40, 40),
                        shape="diamond",
                        confidence=0.9,
                        evidence_ids=["text-a"],
                    ),
                ],
                relations=[
                    SceneRelation(
                        id="branch-no-edge",
                        source_id=first_id,
                        target_id=second_id,
                        relation_type="conditional_branch",
                        semantic_relation="conditional",
                        label="No",
                        evidence_ids=["branch-no"],
                    ),
                    SceneRelation(
                        id="branch-yes-edge",
                        source_id=first_id,
                        target_id=second_id,
                        relation_type="conditional_branch",
                        semantic_relation="conditional",
                        label="Yes",
                        evidence_ids=["branch-yes"],
                    ),
                ],
                groups=[
                    SceneGroup(
                        id="decision-lane",
                        role="lane",
                        label="Decision",
                        bbox=(5, 5, 95, 45),
                        member_ids=[second_id, first_id],
                    )
                ],
                reading_direction="LR",
                canvas_size=(100, 100),
            ),
            typed_candidates=[
                TypedIRCandidate(
                    diagram_type=diagram_type,
                    confidence=confidence,
                    ir={
                        "direction": "LR",
                        "nodes": [
                            {
                                "id": second_id,
                                "label": "Continue",
                                "bbox": (60, 10, 90, 40),
                                "evidence_ids": ["text-b"],
                            },
                            {
                                "id": first_id,
                                "label": "Approve?",
                                "shape": "diamond",
                                "bbox": (10, 10, 40, 40),
                                "evidence_ids": ["text-a"],
                            },
                        ],
                        "edges": [
                            {
                                "id": "typed-yes",
                                "source": first_id,
                                "target": second_id,
                                "label": "Yes",
                                "evidence_ids": ["branch-yes"],
                            },
                            {
                                "id": "typed-no",
                                "source": first_id,
                                "target": second_id,
                                "label": "No",
                                "evidence_ids": ["branch-no"],
                            },
                        ],
                        "groups": [
                            {
                                "id": "decision-lane",
                                "label": "Decision",
                                "member_ids": [second_id, first_id],
                            }
                        ],
                    },
                )
            ],
            evidence=evidence,
        ),
        name,
        prior_evidence_ids=frozenset({"text-a", "text-b"}),
        prior_evidence=tuple(
            item.model_copy(deep=True) for item in all_evidence if item.id in {"text-a", "text-b"}
        ),
        publication_evidence_ids=frozenset(item.id for item in all_evidence),
        trusted_canvas_size=(100, 100),
        trusted_source_block_ids=frozenset({"source"}),
    )


def _mapping_dump(item):
    return item.model_dump(exclude={"claim_digest"}) if hasattr(item, "model_dump") else item


def _assert_harmonization_refused(authority, semantic, original_ir):
    fused = FusionEngine().fuse([authority, semantic])

    [candidate] = fused.typed_candidates
    assert candidate.ir == canonical_typed_ir_snapshot(original_ir)
    assert fused.fusion_node_id_mappings_for(candidate) == []
    assert any("harmon" in warning.casefold() for warning in fused.warnings)
    return fused


def _rename_semantic_node(semantic, old_id, new_id, *, bbox=None):
    scene = semantic.observation.scene_ir
    assert scene is not None
    [candidate] = semantic.observation.typed_candidates

    for element in scene.elements:
        if element.id == old_id:
            element.id = new_id
            if bbox is not None:
                element.bbox = bbox
    for relation in scene.relations:
        if relation.source_id == old_id:
            relation.source_id = new_id
        if relation.target_id == old_id:
            relation.target_id = new_id
    for group in scene.groups:
        group.member_ids = [new_id if item == old_id else item for item in group.member_ids]

    for node in candidate.ir["nodes"]:
        if node["id"] == old_id:
            node["id"] = new_id
            if bbox is not None:
                node["bbox"] = bbox
    for edge in candidate.ir["edges"]:
        if edge["source"] == old_id:
            edge["source"] = new_id
        if edge["target"] == old_id:
            edge["target"] = new_id
    for group in candidate.ir["groups"]:
        group["member_ids"] = [new_id if item == old_id else item for item in group["member_ids"]]


@pytest.mark.parametrize("diagram_type", ["flowchart", "generic_network"])
def test_harmonizes_typed_graph_ids_to_unique_geometry_clusters(diagram_type) -> None:
    authority = _harmonization_authority()
    authority.observation.prediction = DiagramTypePrediction(
        candidates=[diagram_type], scores=[0.8]
    )
    semantic = _harmonization_semantic(diagram_type=diagram_type)
    authority_before = copy.deepcopy(authority.observation)
    semantic_before = copy.deepcopy(semantic.observation)

    fused = FusionEngine().fuse([authority, semantic])

    assert fused.scene_ir is not None
    assert [element.id for element in fused.scene_ir.elements] == [
        "geometry-node-001",
        "geometry-node-002",
    ]
    fused_elements = {element.id: element for element in fused.scene_ir.elements}
    assert fused_elements["geometry-node-001"].bbox == (10, 10, 40, 40)
    assert fused_elements["geometry-node-001"].text == "Approve?"
    assert set(fused_elements["geometry-node-001"].evidence_ids) == {
        "contour-a",
        "text-a",
    }
    assert fused_elements["geometry-node-002"].bbox == (60, 10, 90, 40)
    assert fused_elements["geometry-node-002"].text == "Continue"
    assert set(fused_elements["geometry-node-002"].evidence_ids) == {
        "contour-b",
        "text-b",
    }
    assert set(fused.scene_ir.groups[0].member_ids) == {
        "geometry-node-001",
        "geometry-node-002",
    }

    [candidate] = fused.typed_candidates
    assert [node["id"] for node in candidate.ir["nodes"]] == [
        "geometry-node-002",
        "geometry-node-001",
    ]
    assert [node["label"] for node in candidate.ir["nodes"]] == [
        "Continue",
        "Approve?",
    ]
    assert [node["bbox"] for node in candidate.ir["nodes"]] == [
        [60, 10, 90, 40],
        [10, 10, 40, 40],
    ]
    assert [edge["id"] for edge in candidate.ir["edges"]] == [
        "typed-yes",
        "typed-no",
    ]
    assert [edge["label"] for edge in candidate.ir["edges"]] == ["Yes", "No"]
    assert [(edge["source"], edge["target"]) for edge in candidate.ir["edges"]] == [
        ("geometry-node-001", "geometry-node-002"),
        ("geometry-node-001", "geometry-node-002"),
    ]
    assert candidate.ir["groups"] == [
        {
            "id": "decision-lane",
            "label": "Decision",
            "member_ids": ["geometry-node-002", "geometry-node-001"],
        }
    ]

    mappings = {
        item["source_id"]: item
        for item in map(_mapping_dump, fused.fusion_node_id_mappings_for(candidate))
    }
    assert mappings == {
        "A": {
            "source_owner": "vlm#001",
            "source_id": "A",
            "fused_id": "geometry-node-001",
            "authority_source": "geometry",
            "authority_owner": "geometry#000",
            "match_method": "unique_iou",
            "iou": 1.0,
            "source_bbox": (0.1, 0.1, 0.4, 0.4),
            "authority_bbox": (0.1, 0.1, 0.4, 0.4),
            "source_text": "Approve?",
            "source_evidence_ids": ("text-a",),
            "authority_evidence_ids": ("contour-a",),
        },
        "B": {
            "source_owner": "vlm#001",
            "source_id": "B",
            "fused_id": "geometry-node-002",
            "authority_source": "geometry",
            "authority_owner": "geometry#000",
            "match_method": "unique_iou",
            "iou": 1.0,
            "source_bbox": (0.6, 0.1, 0.9, 0.4),
            "authority_bbox": (0.6, 0.1, 0.9, 0.4),
            "source_text": "Continue",
            "source_evidence_ids": ("text-b",),
            "authority_evidence_ids": ("contour-b",),
        },
    }
    assert fused.fusion_typed_evidence_authority_for(candidate) == {
        "text-a",
        "text-b",
        "branch-yes",
        "branch-no",
        "contour-a",
        "contour-b",
    }
    assert fused.fusion_scene_evidence_authority == frozenset()
    assert authority.observation == authority_before
    assert semantic.observation == semantic_before


@pytest.mark.parametrize("empty_side", ["source", "authority"])
def test_harmonization_cannot_launder_explicitly_unauthorized_evidence(empty_side) -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    if empty_side == "source":
        semantic = replace(semantic, publication_evidence_ids=frozenset())
    else:
        authority = replace(authority, publication_evidence_ids=frozenset())
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    fused = _assert_harmonization_refused(authority, semantic, original_ir)

    [candidate] = fused.typed_candidates
    candidate_authority = fused.fusion_typed_evidence_authority_for(candidate)
    expected_authority = (
        frozenset() if empty_side == "source" else semantic.publication_evidence_ids
    )
    assert candidate_authority == expected_authority
    assert not set(candidate_authority or ()).intersection({"contour-a", "contour-b"})


def test_harmonization_records_certified_exact_id_as_identity() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    _rename_semantic_node(semantic, "A", "geometry-node-001")

    fused = FusionEngine().fuse([authority, semantic])

    [candidate] = fused.typed_candidates
    mappings = {item.source_id: item for item in fused.fusion_node_id_mappings_for(candidate)}
    assert mappings["geometry-node-001"].fused_id == "geometry-node-001"
    assert mappings["geometry-node-001"].match_method == "identity"
    assert mappings["geometry-node-001"].iou == 1.0
    assert mappings["B"].fused_id == "geometry-node-002"
    assert mappings["B"].match_method == "unique_iou"


def test_harmonization_refuses_equal_iou_authority_matches() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    scene = authority.observation.scene_ir
    assert scene is not None
    scene.elements.append(
        SceneElement(
            id="geometry-node-003",
            role="unknown",
            bbox=(10, 10, 40, 40),
            confidence=0.8,
            evidence_ids=["contour-c"],
        )
    )
    authority.observation.evidence.append(
        VisualEvidence(
            id="contour-c",
            kind="contour",
            bbox=(10, 10, 40, 40),
            score=0.8,
        )
    )
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)
    authority_before = copy.deepcopy(authority.observation)
    semantic_before = copy.deepcopy(semantic.observation)

    _assert_harmonization_refused(authority, semantic, original_ir)

    assert authority.observation == authority_before
    assert semantic.observation == semantic_before


def test_harmonization_refuses_many_source_nodes_for_one_authority_node() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    scene = semantic.observation.scene_ir
    assert scene is not None
    next(element for element in scene.elements if element.id == "B").bbox = (10, 10, 40, 40)
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_harmonization_refuses_partial_node_certification() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    scene = semantic.observation.scene_ir
    assert scene is not None
    next(element for element in scene.elements if element.id == "B").bbox = (
        200,
        200,
        230,
        230,
    )
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


@pytest.mark.parametrize("reference_kind", ["edge", "group"])
def test_harmonization_refuses_dangling_typed_references(reference_kind) -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    [candidate] = semantic.observation.typed_candidates
    if reference_kind == "edge":
        candidate.ir["edges"][0]["target"] = "missing-node"
    else:
        candidate.ir["groups"][0]["member_ids"].append("missing-node")
    original_ir = copy.deepcopy(candidate.ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metadata", {"depends_on": "A"}),
        ("metadata", ({"parent_id": "A"},)),
        ("lanes", [{"nodes": [{"id": "A"}]}]),
    ],
)
def test_harmonization_refuses_unsupported_nested_node_reference(field, value) -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    [candidate] = semantic.observation.typed_candidates
    candidate.ir[field] = value
    original_ir = copy.deepcopy(candidate.ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


@pytest.mark.parametrize("typed_evidence", [[], ["not-source-evidence"]])
def test_harmonization_refuses_missing_typed_to_scene_evidence(typed_evidence) -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    [candidate] = semantic.observation.typed_candidates
    next(node for node in candidate.ir["nodes"] if node["id"] == "A")["evidence_ids"] = (
        typed_evidence
    )
    original_ir = copy.deepcopy(candidate.ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


@pytest.mark.parametrize("conflicting", [False, True])
def test_harmonization_refuses_duplicated_or_conflicting_evidence_ids(conflicting) -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    duplicate = next(item for item in semantic.observation.evidence if item.id == "text-a")
    duplicate = duplicate.model_copy(deep=True)
    if conflicting:
        duplicate.text = "Conflicting label"
        duplicate.bbox = (0, 0, 5, 5)
    authority.observation.evidence.append(duplicate)
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_harmonization_refuses_source_evidence_not_supplied_as_prior() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    semantic = FusionInput(
        semantic.source,
        semantic.observation,
        semantic.name,
    )
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


@pytest.mark.parametrize("swapped_field", ["bbox", "text"])
def test_harmonization_refuses_prior_evidence_attached_to_the_wrong_scene_node(
    swapped_field,
) -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    first, second = semantic.prior_evidence
    first_value = getattr(first, swapped_field)
    setattr(first, swapped_field, getattr(second, swapped_field))
    setattr(second, swapped_field, first_value)
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    fused = _assert_harmonization_refused(authority, semantic, original_ir)

    assert any("spatially/text aligned" in warning for warning in fused.warnings)


@pytest.mark.parametrize("evidence_side", ["source", "authority"])
def test_harmonization_refuses_mapping_evidence_without_source_blocks(evidence_side) -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    evidence = (
        semantic.prior_evidence
        if evidence_side == "source"
        else tuple(item for item in authority.observation.evidence if item.kind == "contour")
    )
    for item in evidence:
        item.source_block_ids = []
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_harmonization_refuses_authority_evidence_declared_by_another_owner() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    authority.observation.evidence = [
        item for item in authority.observation.evidence if item.id not in {"contour-a", "contour-b"}
    ]
    semantic.observation.evidence.extend(
        [
            VisualEvidence(id="contour-a", kind="vlm_observation", score=0.9),
            VisualEvidence(id="contour-b", kind="vlm_observation", score=0.9),
        ]
    )
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_harmonization_refuses_authority_contour_attached_to_the_wrong_scene_node() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    contours = {item.id: item for item in authority.observation.evidence if item.kind == "contour"}
    contours["contour-a"].bbox, contours["contour-b"].bbox = (
        contours["contour-b"].bbox,
        contours["contour-a"].bbox,
    )
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_harmonization_refuses_far_exact_id_collision() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    _rename_semantic_node(
        semantic,
        "A",
        "geometry-node-001",
        bbox=(200, 200, 230, 230),
    )
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_harmonization_refuses_out_of_canvas_boxes_without_failing_fusion() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    authority_scene = authority.observation.scene_ir
    semantic_scene = semantic.observation.scene_ir
    assert authority_scene is not None
    assert semantic_scene is not None
    next(
        element for element in authority_scene.elements if element.id == "geometry-node-001"
    ).bbox = (110, 10, 140, 40)
    next(element for element in semantic_scene.elements if element.id == "A").bbox = (
        110,
        10,
        140,
        40,
    )
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_harmonization_refuses_owner_declared_canvas_scale_spoof() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    authority_scene = authority.observation.scene_ir
    semantic_scene = semantic.observation.scene_ir
    assert authority_scene is not None
    assert semantic_scene is not None
    authority_boxes = {
        "geometry-node-001": (80, 80, 90, 90),
        "geometry-node-002": (20, 20, 30, 30),
    }
    semantic_boxes = {"A": (8, 8, 9, 9), "B": (2, 2, 3, 3)}
    for element in authority_scene.elements:
        element.bbox = authority_boxes[element.id]
    for evidence in authority.observation.evidence:
        if evidence.id == "contour-a":
            evidence.bbox = authority_boxes["geometry-node-001"]
        elif evidence.id == "contour-b":
            evidence.bbox = authority_boxes["geometry-node-002"]
    semantic_scene.canvas_size = (10, 10)
    for element in semantic_scene.elements:
        element.bbox = semantic_boxes[element.id]
    for evidence in semantic.prior_evidence:
        evidence.bbox = semantic_boxes["A" if evidence.id == "text-a" else "B"]
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    _assert_harmonization_refused(authority, semantic, original_ir)


def test_identity_only_typed_ids_keep_legacy_candidate_without_mapping_warning() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    _rename_semantic_node(semantic, "A", "geometry-node-001")
    _rename_semantic_node(semantic, "B", "geometry-node-002")
    [source_candidate] = semantic.observation.typed_candidates
    for node in source_candidate.ir["nodes"]:
        node.pop("evidence_ids")

    fused = FusionEngine().fuse([authority, semantic])

    [candidate] = fused.typed_candidates
    assert fused.fusion_node_id_mappings_for(candidate) == []
    assert not any("harmonization" in warning for warning in fused.warnings)


def test_harmonization_deduplicates_remapped_payload_independent_of_input_order() -> None:
    authority = _harmonization_authority()
    first = _harmonization_semantic(
        node_ids=("A", "B"),
        name="vlm-a",
        confidence=0.7,
    )
    second = _harmonization_semantic(
        node_ids=("X", "Y"),
        name="vlm-x",
        confidence=0.9,
        include_evidence=False,
    )
    authority_before = copy.deepcopy(authority.observation)
    first_before = copy.deepcopy(first.observation)
    second_before = copy.deepcopy(second.observation)

    forward = FusionEngine().fuse([authority, first, second])
    backward = FusionEngine().fuse([second, first, authority])

    assert forward == backward
    [candidate] = forward.typed_candidates
    assert candidate.confidence == 0.9
    assert [node["id"] for node in candidate.ir["nodes"]] == [
        "geometry-node-002",
        "geometry-node-001",
    ]
    mappings = forward.fusion_node_id_mappings_for(candidate)
    assert {item.source_id: item.fused_id for item in mappings} == {
        "X": "geometry-node-001",
        "Y": "geometry-node-002",
    }
    assert {item.source_owner for item in mappings} == {"vlm-x#002"}
    assert authority.observation == authority_before
    assert first.observation == first_before
    assert second.observation == second_before


def test_harmonization_prefers_audited_candidate_over_code_equivalent_identity_input() -> None:
    authority = _harmonization_authority()
    mapped = _harmonization_semantic(
        name="vlm-mapped",
        confidence=0.7,
    )
    identity = _harmonization_semantic(
        node_ids=("geometry-node-001", "geometry-node-002"),
        name="vlm-identity",
        confidence=0.9,
        include_evidence=False,
    )
    identity.observation.typed_candidates[0].ir["ignored_metadata"] = "higher confidence"

    fused = FusionEngine().fuse([authority, identity, mapped])

    assert len(fused.typed_candidates) == 2
    preferred = fused.typed_candidates[0]
    assert preferred.confidence == 0.7
    assert fused.fusion_node_id_mappings_for(preferred)
    assert fused.fusion_node_id_mappings_for(fused.typed_candidates[1]) == []


def test_harmonization_does_not_rewrite_other_typed_diagram_families() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic(diagram_type="deployment")
    original_ir = copy.deepcopy(semantic.observation.typed_candidates[0].ir)

    fused = FusionEngine().fuse([authority, semantic])

    [candidate] = fused.typed_candidates
    assert candidate.ir == original_ir
    assert fused.fusion_node_id_mappings_for(candidate) == []


def test_harmonization_refuses_nested_container_in_known_scalar_field() -> None:
    authority = _harmonization_authority()
    semantic = _harmonization_semantic()
    [candidate] = semantic.observation.typed_candidates
    candidate.ir["nodes"][0]["label"] = {"parent_id": "A"}

    fused = FusionEngine().fuse([authority, semantic])

    assert fused.typed_candidates == []
    assert any("skipped invalid typed candidate" in warning for warning in fused.warnings)


@pytest.mark.parametrize("invalid_value", [{"unstable"}, float("nan")])
def test_fusion_revalidates_typed_ir_after_construction_mutation(invalid_value) -> None:
    semantic = _harmonization_semantic()
    [candidate] = semantic.observation.typed_candidates
    candidate.ir["mutated"] = invalid_value

    with pytest.raises(ValueError):
        candidate.canonical_key()

    fused = FusionEngine().fuse([semantic])

    assert fused.typed_candidates == []
    assert any("skipped invalid typed candidate" in warning for warning in fused.warnings)


def _observation(
    diagram_type: str,
    score: float,
    *,
    scene: DiagramSceneIR | None = None,
    evidence: list[VisualEvidence] | None = None,
) -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=[diagram_type], scores=[score]),
        scene_ir=scene,
        evidence=evidence or [],
    )


def _scene(
    element: SceneElement,
    *,
    relation: SceneRelation | None = None,
    direction: str = "unknown",
) -> DiagramSceneIR:
    elements = [element]
    if relation is not None:
        elements.append(
            SceneElement(id="target", role="process", bbox=(70, 10, 100, 40), confidence=0.8)
        )
    return DiagramSceneIR(
        elements=elements,
        relations=[relation] if relation else [],
        reading_direction=direction,
        canvas_size=(100, 100),
    )


def test_fuses_geometry_with_vlm_text_role_and_semantics() -> None:
    geometry_scene = DiagramSceneIR(
        elements=[
            SceneElement(
                id="cv-node",
                role="unknown",
                bbox=(10, 10, 42, 42),
                shape="rectangle",
                confidence=0.7,
                evidence_ids=["contour-1"],
            ),
            SceneElement(id="target", role="unknown", bbox=(70, 10, 100, 40), confidence=0.7),
        ],
        relations=[
            SceneRelation(
                id="cv-edge",
                source_id="cv-node",
                target_id="target",
                relation_type="connector",
                polyline=[(42, 25), (70, 25)],
                confidence=0.7,
                evidence_ids=["line-1"],
            )
        ],
        canvas_size=(100, 100),
    )
    vlm_scene = DiagramSceneIR(
        elements=[
            SceneElement(
                id="request",
                role="process",
                text="Payment request",
                bbox=(11, 11, 41, 41),
                confidence=0.9,
                evidence_ids=["vlm-node"],
            ),
            SceneElement(id="target", role="decision", bbox=(69, 9, 99, 39), confidence=0.9),
        ],
        relations=[
            SceneRelation(
                id="vlm-edge",
                source_id="request",
                target_id="target",
                relation_type="decision_branch",
                semantic_relation="conditional",
                label="yes",
                polyline=[(41, 24), (69, 24)],
                confidence=0.9,
                evidence_ids=["vlm-edge-evidence"],
            )
        ],
        canvas_size=(100, 100),
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm_scene), "vlm"),
            FusionInput("geometry", _observation("flowchart", 0.7, scene=geometry_scene), "opencv"),
        ]
    )

    assert fused.scene_ir is not None
    assert len(fused.scene_ir.elements) == 2
    node = fused.scene_ir.elements[0]
    assert node.id == "cv-node"
    assert node.bbox == (10, 10, 42, 42)
    assert node.shape == "rectangle"
    assert node.text == "Payment request"
    assert node.role == "process"
    assert node.evidence_ids == ["contour-1", "vlm-node"]
    edge = fused.scene_ir.relations[0]
    assert edge.polyline == [(42, 25), (70, 25)]
    assert edge.semantic_relation == "conditional"
    assert edge.relation_type == "decision_branch"
    assert edge.label == "yes"


def test_vector_geometry_and_label_override_other_sources() -> None:
    vector = _scene(
        SceneElement(
            id="node",
            role="unknown",
            text="Vector label",
            bbox=(10, 10, 40, 40),
            confidence=0.8,
        )
    )
    geometry = _scene(
        SceneElement(id="shape", role="unknown", bbox=(12, 12, 42, 42), confidence=0.9)
    )
    vlm = _scene(
        SceneElement(
            id="meaning",
            role="process",
            text="Invented label",
            bbox=(11, 11, 41, 41),
            confidence=0.99,
        )
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("geometry", _observation("flowchart", 0.8, scene=geometry), "cv"),
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm), "vlm"),
            FusionInput("vector", _observation("flowchart", 0.7, scene=vector), "pdf"),
        ]
    )

    assert fused.scene_ir is not None
    node = fused.scene_ir.elements[0]
    assert node.id == "node"
    assert node.bbox == (10, 10, 40, 40)
    assert node.text == "Vector label"
    assert node.role == "process"
    assert any("label conflict" in warning for warning in fused.warnings)


def test_conflicting_font_weight_evidence_is_omitted_during_fusion() -> None:
    vector = _scene(
        SceneElement(
            id="node",
            role="unknown",
            text="Label",
            bbox=(10, 10, 40, 40),
            font_weight="bold",
        )
    )
    vlm = _scene(
        SceneElement(
            id="semantic-node",
            role="process",
            text="Label",
            bbox=(10, 10, 40, 40),
            font_weight="normal",
        )
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vector", _observation("flowchart", 0.8, scene=vector), "pdf"),
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm), "vlm"),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].font_weight is None
    assert any("font-weight conflict" in warning for warning in fused.warnings)


def test_ocr_consensus_beats_vlm_label() -> None:
    vlm = _scene(
        SceneElement(
            id="node",
            role="process",
            text="Approval?",
            bbox=(10, 10, 60, 40),
            confidence=0.95,
        )
    )
    ocr_one = _scene(
        SceneElement(
            id="ocr-a",
            role="unknown",
            text="Payment approval",
            bbox=(10, 10, 60, 40),
            confidence=0.7,
        )
    )
    ocr_two = _scene(
        SceneElement(
            id="ocr-b",
            role="unknown",
            text=" payment   approval ",
            bbox=(10, 10, 60, 40),
            confidence=0.6,
        )
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("ocr", _observation("flowchart", 0.5, scene=ocr_two), "ocr-b"),
            FusionInput("vlm", _observation("flowchart", 0.9, scene=vlm), "vlm"),
            FusionInput("ocr", _observation("flowchart", 0.5, scene=ocr_one), "ocr-a"),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].text == "Payment approval"


def test_ocr_evidence_can_enrich_a_scene_without_an_ocr_scene() -> None:
    scene = _scene(
        SceneElement(
            id="node",
            role="process",
            text="VLM guess",
            bbox=(10, 10, 60, 40),
            confidence=0.9,
        )
    )
    ocr = _observation(
        "unknown",
        1.0,
        evidence=[
            VisualEvidence(
                id="ocr-token",
                kind="ocr_token",
                bbox=(20, 15, 50, 30),
                text="Verified label",
                score=0.8,
            )
        ],
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vlm", _observation("flowchart", 0.9, scene=scene), "vlm"),
            FusionInput("ocr", ocr, "surya"),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].text == "Verified label"


def test_zero_confidence_vector_text_is_not_promoted_as_missing_confidence() -> None:
    semantic_scene = _scene(
        SceneElement(
            id="node",
            role="process",
            text="Semantic guess",
            bbox=(10, 10, 60, 40),
            confidence=0.9,
        )
    )
    vector_evidence = _observation(
        "unknown",
        1.0,
        evidence=[
            VisualEvidence(
                id="zero-confidence",
                kind="vector_text",
                bbox=(20, 15, 30, 25),
                text="Rejected label",
                score=0.0,
            ),
            VisualEvidence(
                id="measured-confidence",
                kind="vector_text",
                bbox=(35, 15, 50, 25),
                text="Measured label",
                score=0.4,
            ),
        ],
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vlm", _observation("flowchart", 0.9, scene=semantic_scene), "vlm"),
            FusionInput("vector", vector_evidence, "pdf"),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].text == "Measured label"


def test_pixel_evidence_is_normalized_with_trusted_canvas_for_normalized_scene() -> None:
    normalized_scene = DiagramSceneIR(
        elements=[
            SceneElement(
                id="node",
                role="process",
                text="Semantic guess",
                bbox=(0.1, 0.1, 0.6, 0.4),
                confidence=0.9,
            )
        ],
        coordinate_space="normalized",
    )
    ocr = _observation(
        "unknown",
        1.0,
        evidence=[
            VisualEvidence(
                id="ocr-token",
                kind="ocr_token",
                bbox=(20, 15, 50, 30),
                text="Verified label",
                score=0.8,
            )
        ],
    )

    fused = FusionEngine().fuse(
        [
            FusionInput(
                "vlm",
                _observation("flowchart", 0.9, scene=normalized_scene),
                "vlm",
                trusted_canvas_size=(100, 100),
            ),
            FusionInput("ocr", ocr, "surya", trusted_canvas_size=(100, 100)),
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.elements[0].text == "Verified label"


def test_vector_direction_and_vlm_relation_semantics_are_combined() -> None:
    vector_scene = DiagramSceneIR(
        elements=[
            SceneElement(id="a", role="unknown", bbox=(0, 0, 20, 20)),
            SceneElement(id="b", role="unknown", bbox=(80, 0, 100, 20)),
        ],
        relations=[
            SceneRelation(
                id="path",
                source_id="a",
                target_id="b",
                relation_type="connector",
                polyline=[(20, 10), (80, 10)],
                line_color="#445566",
            )
        ],
        canvas_size=(100, 100),
    )
    vlm_scene = DiagramSceneIR(
        elements=[
            SceneElement(id="a", role="service", bbox=(0, 0, 20, 20)),
            SceneElement(id="b", role="database", bbox=(80, 0, 100, 20)),
        ],
        relations=[
            SceneRelation(
                id="meaning",
                source_id="b",
                target_id="a",
                relation_type="query",
                semantic_relation="data_flow",
            )
        ],
        canvas_size=(100, 100),
    )

    fused = FusionEngine().fuse(
        [
            FusionInput("vlm", _observation("architecture", 0.9, scene=vlm_scene), "vlm"),
            FusionInput("vector", _observation("architecture", 0.8, scene=vector_scene), "pdf"),
        ]
    )

    assert fused.scene_ir is not None
    edge = fused.scene_ir.relations[0]
    assert (edge.source_id, edge.target_id) == ("a", "b")
    assert edge.polyline == [(20, 10), (80, 10)]
    assert edge.line_color == "#445566"
    assert edge.semantic_relation == "data_flow"
    assert edge.relation_type == "query"
    assert any("direction conflict" in warning for warning in fused.warnings)


def test_parallel_labeled_relations_are_not_collapsed() -> None:
    scene = DiagramSceneIR(
        elements=[
            SceneElement(id="a", role="decision", bbox=(0, 0, 20, 20)),
            SceneElement(id="b", role="process", bbox=(80, 0, 100, 20)),
        ],
        relations=[
            SceneRelation(
                id="yes-edge",
                source_id="a",
                target_id="b",
                relation_type="branch",
                label="yes",
            ),
            SceneRelation(
                id="no-edge",
                source_id="a",
                target_id="b",
                relation_type="branch",
                label="no",
            ),
        ],
        canvas_size=(100, 100),
    )

    fused = FusionEngine().fuse(
        [FusionInput("vlm", _observation("flowchart", 0.9, scene=scene), "vlm")]
    )

    assert fused.scene_ir is not None
    assert {relation.label for relation in fused.scene_ir.relations} == {"yes", "no"}


def test_deduplicates_evidence_and_reports_payload_conflict() -> None:
    left = _observation(
        "flowchart",
        0.7,
        evidence=[
            VisualEvidence(
                id="shared",
                kind="ocr_token",
                text="left",
                score=0.5,
                source_block_ids=["block-b"],
            )
        ],
    )
    right = _observation(
        "flowchart",
        0.8,
        evidence=[
            VisualEvidence(
                id="shared",
                kind="vector_text",
                text="right",
                score=0.9,
                source_block_ids=["block-a"],
            )
        ],
    )

    fused = FusionEngine().fuse(
        [FusionInput("ocr", left, "ocr"), FusionInput("vector", right, "pdf")]
    )

    assert len(fused.evidence) == 1
    assert fused.evidence[0].kind == "vector_text"
    assert fused.evidence[0].text == "right"
    assert fused.evidence[0].score == 0.9
    assert fused.evidence[0].source_block_ids == ["block-a", "block-b"]
    assert any("evidence conflict" in warning for warning in fused.warnings)


def test_prediction_and_candidate_fusion_is_order_independent() -> None:
    vector = _observation("architecture", 0.8)
    vector.prediction = DiagramTypePrediction(
        candidates=["architecture", "flowchart"],
        scores=[0.8, 0.2],
        visual_signals=["groups"],
    )
    vector.typed_candidates = [
        TypedIRCandidate(diagram_type="architecture", ir={"services": []}, confidence=0.7)
    ]
    vlm = _observation("flowchart", 0.9)
    vlm.prediction.visual_signals = ["arrows", "groups"]
    vlm.direct_candidates = [
        DirectMermaidCandidate(
            diagram_type="flowchart", code="flowchart LR\n A --> B", confidence=0.8
        ),
        DirectMermaidCandidate(
            diagram_type="flowchart", code="flowchart LR\n A --> B", confidence=0.6
        ),
    ]
    inputs = [FusionInput("vector", vector, "pdf"), FusionInput("vlm", vlm, "vlm")]

    forward = FusionEngine().fuse(inputs)
    backward = FusionEngine().fuse(reversed(inputs))

    assert forward == backward
    # The weighted mean is 0.55 for flowchart and 0.40 for architecture.
    # This documents averaging rather than accidental max-score fusion.
    assert forward.prediction.candidates == ["flowchart", "architecture"]
    assert forward.prediction.scores == [0.55, 0.4]
    assert forward.prediction.visual_signals == ["arrows", "groups"]
    assert len(forward.typed_candidates) == 1
    assert len(forward.direct_candidates) == 1


def test_fused_direct_candidate_keeps_only_winning_owner_publication_authority() -> None:
    code = "flowchart LR\n A --> B"
    restricted = _observation("flowchart", 0.9)
    restricted.direct_candidates = [
        DirectMermaidCandidate(diagram_type="flowchart", code=code, confidence=0.9)
    ]
    unrelated = _observation("flowchart", 0.8)
    unrelated.direct_candidates = [
        DirectMermaidCandidate(diagram_type="flowchart", code=code, confidence=0.8)
    ]

    fused = FusionEngine().fuse(
        [
            FusionInput(
                "vlm",
                restricted,
                "restricted",
                publication_evidence_ids=frozenset(),
            ),
            FusionInput(
                "geometry",
                unrelated,
                "unrelated",
                publication_evidence_ids=frozenset({"geometry-own"}),
            ),
        ]
    )

    [candidate] = fused.direct_candidates
    assert candidate.confidence == 0.9
    assert fused.fusion_direct_evidence_authority_for(candidate) == frozenset()


def test_rejects_empty_or_untyped_inputs() -> None:
    engine = FusionEngine()

    try:
        engine.fuse([])
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty fusion should fail")

    try:
        engine.fuse([object()])  # type: ignore[list-item]
    except TypeError as exc:
        assert "FusionInput" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("untyped fusion input should fail")


@pytest.mark.parametrize("record_kind", ["element", "relation"])
@pytest.mark.parametrize(("right_count", "overflow"), [(128, False), (129, True)])
def test_fusion_bounds_cross_input_scene_evidence_enrichment(
    record_kind: str,
    right_count: int,
    overflow: bool,
) -> None:
    left_ids = [f"left-{index:03d}" for index in range(128)]
    right_ids = [f"right-{index:03d}" for index in range(right_count)]
    scenes: list[DiagramSceneIR] = []
    for evidence_ids in (left_ids, right_ids):
        elements = [
            SceneElement(
                id="A",
                role="node",
                bbox=(0, 0, 10, 10),
                evidence_ids=(evidence_ids if record_kind == "element" else []),
            )
        ]
        relations: list[SceneRelation] = []
        if record_kind == "element":
            elements.append(
                SceneElement(
                    id="B",
                    role="node",
                    bbox=(20, 0, 30, 10),
                    evidence_ids=[evidence_ids[0]],
                )
            )
        else:
            elements.append(SceneElement(id="B", role="node", bbox=(20, 0, 30, 10)))
            relations.append(
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type="edge",
                    evidence_ids=evidence_ids,
                )
            )
        scenes.append(DiagramSceneIR(elements=elements, relations=relations))

    inputs = [
        FusionInput(
            "vector",
            EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
                scene_ir=scenes[0],
            ),
            "left",
        ),
        FusionInput(
            "vlm",
            EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
                scene_ir=scenes[1],
            ),
            "right",
        ),
    ]

    fused = FusionEngine().fuse(inputs)
    reversed_fused = FusionEngine().fuse(reversed(inputs))

    EngineObservation.model_validate(fused.model_dump(mode="python"))
    assert fused.model_dump(mode="json") == reversed_fused.model_dump(mode="json")
    assert fused.scene_ir is not None
    record = fused.scene_ir.elements[0] if record_kind == "element" else fused.scene_ir.relations[0]
    if overflow:
        assert record.evidence_ids == left_ids
        assert any(
            f"fusion {record_kind} evidence union exceeded" in warning for warning in fused.warnings
        )
    else:
        assert record.evidence_ids == sorted([*left_ids, *right_ids])
        assert not any(
            f"fusion {record_kind} evidence union exceeded" in warning for warning in fused.warnings
        )
    if record_kind == "element":
        assert fused.scene_ir.elements[1].evidence_ids == sorted([left_ids[0], right_ids[0]])


@pytest.mark.parametrize(("right_count", "overflow"), [(128, False), (129, True)])
def test_fusion_bounds_visual_evidence_source_block_enrichment(
    right_count: int,
    overflow: bool,
) -> None:
    left_blocks = [f"left-block-{index:03d}" for index in range(128)]
    right_blocks = [f"right-block-{index:03d}" for index in range(right_count)]
    inputs = [
        FusionInput(
            "vector",
            EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
                evidence=[
                    VisualEvidence(
                        id="shared-evidence",
                        kind="contour",
                        score=0.5,
                        source_block_ids=left_blocks,
                    )
                ],
            ),
            "left",
        ),
        FusionInput(
            "vlm",
            EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
                evidence=[
                    VisualEvidence(
                        id="shared-evidence",
                        kind="contour",
                        score=0.9,
                        source_block_ids=right_blocks,
                    )
                ],
            ),
            "right",
        ),
    ]

    fused = FusionEngine().fuse(inputs)
    reversed_fused = FusionEngine().fuse(reversed(inputs))

    assert fused.model_dump(mode="json") == reversed_fused.model_dump(mode="json")
    assert len(fused.evidence) == 1
    evidence = VisualEvidence.model_validate(fused.evidence[0].model_dump(mode="python"))
    assert evidence.score == 0.9
    if overflow:
        assert evidence.source_block_ids == left_blocks
        assert any(
            "fusion evidence source-block union exceeded" in warning for warning in fused.warnings
        )
    else:
        assert evidence.source_block_ids == sorted([*left_blocks, *right_blocks])
        assert not any(
            "fusion evidence source-block union exceeded" in warning for warning in fused.warnings
        )


def test_fusion_discards_the_entire_multi_input_source_block_union_on_overflow() -> None:
    left_blocks = [f"left-block-{index:03d}" for index in range(128)]
    middle_blocks = [f"middle-block-{index:03d}" for index in range(128)]
    inputs = [
        FusionInput(
            source,
            EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
                evidence=[
                    VisualEvidence(
                        id="shared-evidence",
                        kind="contour",
                        score=score,
                        source_block_ids=blocks,
                    )
                ],
            ),
            name,
        )
        for source, name, score, blocks in (
            ("vector", "left", 0.5, left_blocks),
            ("geometry", "middle", 0.7, middle_blocks),
            ("vlm", "right", 0.9, ["right-block-000"]),
        )
    ]

    fused = FusionEngine().fuse(inputs)
    reversed_fused = FusionEngine().fuse(reversed(inputs))

    assert fused.model_dump(mode="json") == reversed_fused.model_dump(mode="json")
    assert len(fused.evidence) == 1
    assert fused.evidence[0].source_block_ids == left_blocks
    assert fused.evidence[0].score == 0.9
    assert any(
        "fusion evidence source-block union exceeded" in warning for warning in fused.warnings
    )


def test_relation_evidence_overflow_keeps_direction_conflict_tracking() -> None:
    scenes = [
        DiagramSceneIR(
            elements=[
                SceneElement(id="A", role="node", bbox=(0, 0, 10, 10)),
                SceneElement(id="B", role="node", bbox=(20, 0, 30, 10)),
            ],
            relations=[
                SceneRelation(
                    id="E",
                    source_id=source_id,
                    target_id=target_id,
                    relation_type="edge",
                    evidence_ids=[f"{prefix}-{index:03d}" for index in range(count)],
                )
            ],
        )
        for source_id, target_id, prefix, count in (
            ("A", "B", "left", 128),
            ("B", "A", "right", 129),
        )
    ]

    fused = FusionEngine().fuse(
        [
            FusionInput(
                source,
                EngineObservation(
                    prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
                    scene_ir=scene,
                ),
                source,
            )
            for source, scene in zip(("vector", "vlm"), scenes, strict=True)
        ]
    )

    assert fused.scene_ir is not None
    assert fused.scene_ir.relations[0].source_id == "A"
    assert fused.scene_ir.relations[0].target_id == "B"
    assert fused.scene_ir.relations[0].evidence_ids == [f"left-{index:03d}" for index in range(128)]
    assert fused.fusion_conflicted_connector_pairs == {frozenset({"A", "B"})}
    assert any("direction conflict" in warning for warning in fused.warnings)
    assert any("relation evidence union exceeded" in warning for warning in fused.warnings)


@pytest.mark.parametrize("mutation", ["nested_label", "evidence_overflow"])
def test_fusion_isolates_invalid_typed_ir_without_live_model_dump(
    monkeypatch,
    mutation: str,
) -> None:
    valid = TypedIRCandidate(
        diagram_type="flowchart",
        ir={"nodes": [{"id": "A", "label": "Start"}], "edges": []},
    )
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
        typed_candidates=[valid.model_copy(deep=True), valid],
    )
    if mutation == "nested_label":
        observation.typed_candidates[0].ir["nodes"][0]["label"] = {"invalid": "nested label"}
    else:
        observation.typed_candidates[0].ir["nodes"][0]["evidence_ids"] = [
            f"evidence-{index}" for index in range(MAX_EVIDENCE_REFS + 1)
        ]

    def forbidden_model_dump(*_args, **_kwargs):
        raise AssertionError("live typed candidate model_dump must not be used")

    monkeypatch.setattr(TypedIRCandidate, "model_dump", forbidden_model_dump)
    fused = FusionEngine().fuse([FusionInput("vlm", observation, "vlm")])

    assert len(fused.typed_candidates) == 1
    assert fused.typed_candidates[0].ir["nodes"][0]["label"] == "Start"
    assert any("invalid typed candidate" in warning for warning in fused.warnings)


def test_fusion_rejects_hostile_typed_candidate_keys_without_hooks() -> None:
    calls: list[str] = []

    class HostileKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append(str(other))
            raise AssertionError("typed candidate key equality hook must not run")

    candidate = TypedIRCandidate(
        diagram_type="flowchart",
        ir={"nodes": [{"id": "A", "label": "Start"}], "edges": []},
    )
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
        typed_candidates=[candidate],
    )
    value = observation.typed_candidates[0]
    value.__dict__.pop("diagram_type")
    value.__dict__[HostileKey("diagram_type")] = "flowchart"

    fused = FusionEngine().fuse([FusionInput("vlm", observation, "vlm")])

    assert fused.typed_candidates == []
    assert calls == []
    assert any("invalid typed candidate" in warning for warning in fused.warnings)


def test_fusion_uses_bounded_typed_projection_instead_of_observation_dump(
    monkeypatch,
) -> None:
    def same_owner_input(label):
        return FusionInput(
            "vlm",
            EngineObservation(
                prediction=DiagramTypePrediction(
                    candidates=["flowchart"],
                    scores=[1.0],
                ),
                typed_candidates=[
                    TypedIRCandidate(
                        diagram_type="flowchart",
                        ir={"nodes": [{"id": "A", "label": "Node"}], "edges": []},
                    )
                ],
                evidence=[
                    VisualEvidence(
                        id="shared",
                        kind="ocr_token",
                        text=label,
                    )
                ],
            ),
            "same-owner",
        )

    left = same_owner_input("Left")
    right = same_owner_input("Right")

    def forbidden_observation_dump(*_args, **_kwargs):
        raise AssertionError("fusion ordering must not dump a live observation")

    monkeypatch.setattr(EngineObservation, "model_dump_json", forbidden_observation_dump)
    forward = FusionEngine().fuse([left, right])
    backward = FusionEngine().fuse([right, left])

    assert forward == backward
    assert len(forward.typed_candidates) == 1
    assert forward.evidence[0].text in {"Left", "Right"}


def test_fusion_charges_invalid_typed_ir_against_the_aggregate_budget(
    monkeypatch,
) -> None:
    valid = TypedIRCandidate(
        diagram_type="flowchart",
        ir={"nodes": [{"id": "A", "label": "Start"}], "edges": []},
    )
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
        typed_candidates=[valid.model_copy(deep=True), valid],
    )
    observation.typed_candidates[0].ir["nodes"][0]["label"] = {"invalid": "nested label"}
    invalid_size = len(
        json.dumps(
            observation.typed_candidates[0].ir,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    valid_size = len(
        json.dumps(
            observation.typed_candidates[1].ir,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    monkeypatch.setattr(
        fusion_module,
        "MAX_OBSERVATION_TYPED_IR_JSON_BYTES",
        invalid_size + valid_size - 1,
    )

    fused = FusionEngine().fuse([FusionInput("vlm", observation, "vlm")])

    assert fused.typed_candidates == []
    assert any("aggregate JSON byte budget" in warning for warning in fused.warnings)


def test_fusion_applies_candidate_count_budget_across_all_inputs(monkeypatch) -> None:
    inputs = []
    for name in ("a", "b", "c"):
        inputs.append(
            FusionInput(
                "vlm",
                EngineObservation(
                    prediction=DiagramTypePrediction(
                        candidates=["flowchart"],
                        scores=[1.0],
                    ),
                    typed_candidates=[
                        TypedIRCandidate(
                            diagram_type="flowchart",
                            ir={
                                "nodes": [{"id": name, "label": name.upper()}],
                                "edges": [],
                            },
                        )
                    ],
                ),
                name,
            )
        )
    monkeypatch.setattr(fusion_module, "MAX_OBSERVATION_CANDIDATES", 2)

    forward = FusionEngine().fuse(inputs)
    backward = FusionEngine().fuse(reversed(inputs))

    assert forward == backward
    assert len(forward.typed_candidates) == 2
    assert any("global item or JSON byte budget" in warning for warning in forward.warnings)


def test_fusion_applies_typed_ir_json_budget_across_all_inputs(monkeypatch) -> None:
    inputs = []
    ir_sizes = []
    for name in ("a", "b", "c"):
        ir = {
            "nodes": [{"id": name, "label": name.upper()}],
            "edges": [],
        }
        ir_sizes.append(
            len(
                json.dumps(
                    ir,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        )
        inputs.append(
            FusionInput(
                "vlm",
                EngineObservation(
                    prediction=DiagramTypePrediction(
                        candidates=["flowchart"],
                        scores=[1.0],
                    ),
                    typed_candidates=[TypedIRCandidate(diagram_type="flowchart", ir=ir)],
                ),
                name,
            )
        )
    monkeypatch.setattr(
        fusion_module,
        "MAX_OBSERVATION_TYPED_IR_JSON_BYTES",
        ir_sizes[0] + ir_sizes[1],
    )

    forward = FusionEngine().fuse(inputs)
    backward = FusionEngine().fuse(reversed(inputs))

    assert forward == backward
    assert len(forward.typed_candidates) == 2
    assert any("global item or JSON byte budget" in warning for warning in forward.warnings)


def test_fusion_rejects_mutated_diagram_type_before_typed_ir_scan(
    monkeypatch,
) -> None:
    candidate = TypedIRCandidate(
        diagram_type="flowchart",
        ir={"nodes": [{"id": "A", "label": "Start"}], "edges": []},
    )
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
        typed_candidates=[candidate],
    )
    observation.typed_candidates[0].__dict__["diagram_type"] = "bad\ud800type"
    calls = 0
    original_snapshot = fusion_module.canonical_typed_ir_snapshot

    def recording_snapshot(value):
        nonlocal calls
        calls += 1
        return original_snapshot(value)

    monkeypatch.setattr(fusion_module, "canonical_typed_ir_snapshot", recording_snapshot)

    fused = FusionEngine().fuse([FusionInput("vlm", observation, "vlm")])

    assert calls == 0
    assert fused.typed_candidates == []
    assert any("invalid typed candidate" in warning for warning in fused.warnings)


@pytest.mark.parametrize("invalid_value", [{"unordered"}, float("nan")])
def test_typed_ir_rejects_non_json_or_non_finite_values(invalid_value) -> None:
    with pytest.raises(ValueError, match="JSON-compatible|finite"):
        TypedIRCandidate(
            diagram_type="flowchart",
            ir={"nodes": [], "invalid": invalid_value},
        )


@pytest.mark.parametrize("threshold", [0, 0.44])
def test_fusion_rejects_element_iou_threshold_below_mapping_contract(threshold) -> None:
    with pytest.raises(ValueError, match="at least 0.45"):
        FusionEngine(element_iou_threshold=threshold)


def _provenance_budget_input(
    name: str,
    *,
    observation_blocks: list[str],
    prior_blocks: list[str],
) -> FusionInput:
    return FusionInput(
        "geometry",
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
            evidence=[
                VisualEvidence(
                    id=f"observation-{name}",
                    kind="contour",
                    bbox=(0, 0, 1, 1),
                    source_block_ids=observation_blocks,
                )
            ],
        ),
        name,
        prior_evidence=(
            VisualEvidence(
                id=f"prior-{name}",
                kind="ocr_token",
                text=name,
                bbox=(0, 0, 1, 1),
                source_block_ids=prior_blocks,
            ),
        ),
    )


def test_fusion_accepts_exact_cumulative_provenance_reference_budget(monkeypatch) -> None:
    monkeypatch.setattr(models_module, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 4)
    inputs = [
        _provenance_budget_input(
            "a",
            observation_blocks=["observation-a"],
            prior_blocks=["prior-a"],
        ),
        _provenance_budget_input(
            "b",
            observation_blocks=["observation-b"],
            prior_blocks=["prior-b"],
        ),
    ]

    fused = FusionEngine().fuse(inputs)

    assert [item.id for item in fused.evidence] == ["observation-a", "observation-b"]
    assert sum(len(item.source_block_ids) for item in fused.evidence) == 2


def test_fusion_rejects_cumulative_provenance_reference_budget_plus_one_before_copy(
    monkeypatch,
) -> None:
    monkeypatch.setattr(models_module, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 4)
    inputs = [
        _provenance_budget_input(
            "a",
            observation_blocks=["observation-a"],
            prior_blocks=["prior-a"],
        ),
        _provenance_budget_input(
            "b",
            observation_blocks=["observation-b"],
            prior_blocks=["prior-b", "prior-b-extra"],
        ),
    ]

    def forbidden_evidence_copy(*_args, **_kwargs):
        raise AssertionError("fusion must reject aggregate overflow before evidence deep copy")

    monkeypatch.setattr(VisualEvidence, "model_copy", forbidden_evidence_copy)

    with pytest.raises(ValueError, match="source-block references exceed the aggregate limit"):
        FusionEngine().fuse(inputs)


def test_fusion_rejects_oversized_prior_evidence_before_tuple_materialization(
    monkeypatch,
) -> None:
    monkeypatch.setattr(models_module, "MAX_OBSERVATION_EVIDENCE", 1)
    evidence = VisualEvidence(
        id="prior",
        kind="ocr_token",
        text="prior",
        bbox=(0, 0, 1, 1),
        source_block_ids=["source"],
    )
    fusion_input = FusionInput(
        "geometry",
        EngineObservation(prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])),
        "geometry",
        prior_evidence=(evidence, evidence),
    )
    builtin_list = list

    def guarded_list(value=()):
        if type(value) is tuple and len(value) > models_module.MAX_OBSERVATION_EVIDENCE:
            raise AssertionError("oversized prior evidence must not be materialized")
        return builtin_list(value)

    monkeypatch.setattr(fusion_module, "list", guarded_list, raising=False)

    with pytest.raises(ValueError, match="observation item limit"):
        FusionEngine().fuse([fusion_input])


def test_fusion_defensively_rejects_oversized_fused_evidence(monkeypatch) -> None:
    monkeypatch.setattr(models_module, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 1)
    fusion_input = FusionInput(
        "geometry",
        EngineObservation(prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])),
        "geometry",
    )
    oversized = VisualEvidence(
        id="oversized",
        kind="contour",
        bbox=(0, 0, 1, 1),
        source_block_ids=["block-a", "block-b"],
    )

    monkeypatch.setattr(
        FusionEngine,
        "_fuse_evidence",
        lambda _self, _inputs: ([oversized], [], set()),
    )

    with pytest.raises(ValueError, match="source-block references exceed the aggregate limit"):
        FusionEngine().fuse([fusion_input])
