import copy

import pytest

from marker_mermaid.models import (
    DiagramSceneIR,
    MermaidCandidate,
    RepairEvent,
    SceneElement,
    SceneRelation,
    VisualEvidence,
)
from marker_mermaid.protocols import SourceContext
from marker_mermaid.semantic_repair import (
    EvidenceBackedFlowchartRepair,
    EvidenceBackedLabelRepair,
)


def _candidate(diagram_type="flowchart"):
    return MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type=diagram_type,
        emitted_diagram_type="flowchart",
        typed_ir={
            "nodes": [
                {"id": "A", "label": "Paymant"},
                {"id": "B", "label": "Done"},
            ],
            "edges": [{"source": "A", "target": "B"}],
        },
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="A",
                    role="node",
                    text="Payment",
                    bbox=(0, 0, 1, 1),
                    evidence_ids=["ocr-a"],
                ),
                SceneElement(id="B", role="node", text="Done", bbox=(2, 0, 3, 1)),
            ],
            relations=[SceneRelation(id="E", source_id="A", target_id="B", relation_type="edge")],
        ),
    )


def _context(text="Payment", *, score=0.95, kind="ocr_token"):
    evidence = VisualEvidence(
        id="ocr-a",
        kind=kind,
        text=text,
        score=score,
        bbox=(0, 0, 1, 1),
        source_block_ids=["source"],
    )
    context = SourceContext(
        source_id="source",
        source_block_ids=["source"],
        source_image_name="source.png",
        image=None,  # type: ignore[arg-type]
        evidence=[evidence],
    )
    context.trusted_label_evidence_ids = {"ocr-a"}
    return context


def _structural_candidate(*, edge=True, relation_label=None):
    candidate = _candidate()
    candidate.typed_ir["edges"] = (
        [
            {
                "id": "typed-e",
                "source": "B",
                "target": "A",
                "style": "dashed",
                "evidence_ids": ["prior"],
            }
        ]
        if edge
        else []
    )
    candidate.scene_ir.relations = [
        SceneRelation(
            id="E",
            source_id="A",
            target_id="B",
            relation_type="sequence",
            semantic_relation="sequence",
            label=relation_label,
            confidence=0.95,
            evidence_ids=["line-e", "arrow-e"],
        )
    ]
    return candidate


def _structural_context(*, line_score=0.95, arrow_score=0.95, arrow_blocks=None):
    context = _context()
    context.trusted_connector_evidence_ids = {"line-e", "arrow-e"}
    context.trusted_connector_relations = {
        ("A", "B", frozenset({"line-e", "arrow-e"}))
    }
    context.evidence.extend(
        [
            VisualEvidence(
                id="line-e",
                kind="line_segment",
                score=line_score,
                bbox=(1, 0, 2, 1),
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="arrow-e",
                kind="arrowhead",
                score=arrow_score,
                bbox=(2, 0, 3, 1),
                source_block_ids=arrow_blocks or ["source"],
            ),
        ]
    )
    return context


def _conditional_label_candidate(
    label=None,
    *,
    conditional=True,
    duplicate_edge=False,
):
    semantic_relation = "conditional" if conditional else "sequence"
    relation_type = "conditional_branch" if conditional else "sequence"
    source_shape = "diamond" if conditional else "rectangle"
    edge = {
        "id": "typed-e",
        "source": "A",
        "target": "B",
        "semantic_relation": semantic_relation,
        "arrow_at_start": False,
        "arrow_at_end": True,
        "evidence_ids": ["prior"],
    }
    if label is not None:
        edge["label"] = label
    edges = [edge]
    if duplicate_edge:
        edges.append(
            {
                **edge,
                "id": "typed-e-duplicate",
                "evidence_ids": ["duplicate-prior"],
            }
        )
    return MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type="flowchart",
        emitted_diagram_type="flowchart",
        typed_ir={
            "direction": "LR",
            "nodes": [
                {"id": "A", "label": "Approved?", "shape": source_shape},
                {"id": "B", "label": "Continue"},
            ],
            "edges": edges,
        },
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="A",
                    role="decision" if conditional else "node",
                    text="Approved?",
                    bbox=(0, 0, 10, 10),
                    shape=source_shape,
                ),
                SceneElement(
                    id="B",
                    role="node",
                    text="Continue",
                    bbox=(40, 0, 50, 10),
                ),
            ],
            relations=[
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type=relation_type,
                    semantic_relation=semantic_relation,
                    label="Yes",
                    polyline=[(10, 5), (40, 5)],
                    confidence=0.95,
                    evidence_ids=["branch-label", "line-e", "arrow-e"],
                )
            ],
            reading_direction="LR",
        ),
    )


def _conditional_label_context(
    *,
    kind="ocr_token",
    score=0.95,
    text="Yes",
    label_bbox=(20, 4, 30, 6),
    line_score=0.95,
    arrow_score=0.95,
):
    context = SourceContext(
        source_id="source",
        source_block_ids=["source"],
        source_image_name="source.png",
        image=None,  # type: ignore[arg-type]
        evidence=[
            VisualEvidence(
                id="branch-label",
                kind=kind,
                text=text,
                score=score,
                bbox=label_bbox,
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="line-e",
                kind="line_segment",
                score=line_score,
                bbox=(10, 5, 40, 5),
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="arrow-e",
                kind="arrowhead",
                score=arrow_score,
                bbox=(38, 3, 40, 7),
                source_block_ids=["source"],
            ),
        ],
    )
    context.trusted_label_evidence_ids = {"branch-label"}
    context.trusted_connector_evidence_ids = {"line-e", "arrow-e"}
    context.trusted_connector_relations = {("A", "B", frozenset({"line-e", "arrow-e"}))}
    return context


def _edge_structure(edges):
    return [
        (
            edge.get("id"),
            edge.get("source"),
            edge.get("target"),
            edge.get("arrow_at_start"),
            edge.get("arrow_at_end"),
            edge.get("bidirectional"),
        )
        for edge in edges
    ]


def test_label_repair_uses_exact_id_and_high_confidence_evidence_without_topology_changes():
    candidate = _candidate()

    proposal = EvidenceBackedLabelRepair().repair(_context(), candidate)

    assert proposal is not None
    assert proposal.typed_ir["nodes"][0]["label"] == "Payment"
    assert proposal.typed_ir["edges"] == candidate.typed_ir["edges"]
    assert proposal.details["corrections"][0]["evidence_ids"] == ["ocr-a"]
    assert "Payment" in proposal.code
    assert candidate.typed_ir["nodes"][0]["label"] == "Paymant"


def test_label_repair_refuses_weak_mismatched_or_non_flow_evidence():
    engine = EvidenceBackedLabelRepair()

    assert engine.repair(_context(score=0.5), _candidate()) is None
    assert engine.repair(_context(text="Different"), _candidate()) is None
    assert engine.repair(_context(), _candidate("state")) is None


def test_label_repair_requires_trusted_colocated_source_evidence():
    engine = EvidenceBackedLabelRepair()

    untrusted = _context()
    untrusted.trusted_label_evidence_ids.clear()
    assert engine.repair(untrusted, _candidate()) is None

    other_block = _context()
    other_block.evidence[0].source_block_ids = ["other"]
    assert engine.repair(other_block, _candidate()) is None

    outside_node = _context()
    outside_node.evidence[0].bbox = (4, 4, 5, 5)
    assert engine.repair(outside_node, _candidate()) is None


def test_vector_text_without_probability_is_accepted_as_primary_pdf_evidence():
    proposal = EvidenceBackedLabelRepair().repair(
        _context(score=None, kind="vector_text"),
        _candidate(),
    )

    assert proposal is not None


def test_label_repair_does_not_discard_an_accepted_style_recovery():
    candidate = _candidate()
    candidate.repair_history.append(
        RepairEvent(iteration=0, operation="recover_style", accepted=True)
    )

    assert EvidenceBackedLabelRepair().repair(_context(), candidate) is None


def test_flowchart_repair_reverses_an_unlabeled_edge_and_preserves_edge_metadata():
    candidate = _structural_candidate()

    proposal = EvidenceBackedFlowchartRepair().repair(_structural_context(), candidate)

    assert proposal is not None
    edge = proposal.typed_ir["edges"][0]
    assert edge["source"] == "A"
    assert edge["target"] == "B"
    assert edge["id"] == "typed-e"
    assert edge["style"] == "dashed"
    assert edge["evidence_ids"] == ["prior", "line-e", "arrow-e"]
    assert proposal.details["structural_corrections"][0]["operation"] == "reverse_edge"
    assert candidate.typed_ir["edges"][0]["source"] == "B"


def test_flowchart_repair_adds_an_unlabeled_missing_edge_with_a_safe_id():
    candidate = _structural_candidate(edge=False)

    proposal = EvidenceBackedFlowchartRepair().repair(_structural_context(), candidate)

    assert proposal is not None
    assert proposal.typed_ir["edges"] == [
        {
            "id": "repair_edge_1",
            "source": "A",
            "target": "B",
            "relation_type": "sequence",
            "semantic_relation": "sequence",
            "arrow_at_start": False,
            "arrow_at_end": True,
            "evidence_ids": ["line-e", "arrow-e"],
        }
    ]
    assert candidate.typed_ir["edges"] == []


def test_flowchart_repair_requires_strong_colocated_line_and_arrow_evidence():
    engine = EvidenceBackedFlowchartRepair()
    candidate = _structural_candidate()
    candidate.scene_ir.elements[0].text = "Paymant"

    assert engine.repair(_structural_context(arrow_score=0.5), candidate) is None
    assert engine.repair(_structural_context(arrow_blocks=["other"]), candidate) is None

    missing_bbox = _structural_context()
    missing_bbox.evidence[-1].bbox = None
    assert engine.repair(missing_bbox, candidate) is None

    untrusted = _structural_context()
    untrusted.trusted_connector_evidence_ids.clear()
    assert engine.repair(untrusted, candidate) is None


@pytest.mark.parametrize(
    ("before", "kind", "score"),
    [
        (None, "ocr_token", 0.95),
        ("Yse", "vector_text", None),
    ],
)
def test_flowchart_repair_repairs_conditional_label_without_structural_changes(
    before,
    kind,
    score,
):
    candidate = _conditional_label_candidate(before)
    original_ir = copy.deepcopy(candidate.typed_ir)
    original_scene = candidate.scene_ir.model_copy(deep=True)
    original_structure = _edge_structure(candidate.typed_ir["edges"])

    proposal = EvidenceBackedFlowchartRepair().repair(
        _conditional_label_context(kind=kind, score=score),
        candidate,
    )

    assert proposal is not None
    assert proposal.operation == "repair_evidence_backed_flowchart"
    assert len(proposal.typed_ir["edges"]) == len(original_ir["edges"]) == 1
    assert _edge_structure(proposal.typed_ir["edges"]) == original_structure
    [edge] = proposal.typed_ir["edges"]
    assert edge["label"] == "Yes"
    assert edge["evidence_ids"][0] == "prior"
    assert set(edge["evidence_ids"][1:]) == {
        "branch-label",
        "line-e",
        "arrow-e",
    }
    assert "A -->|Yes| B" in proposal.code
    assert not proposal.details.get("structural_corrections")
    assert proposal.details["edge_label_corrections"] == [
        {
            "operation": "relabel_conditional_edge",
            "edge_id": "typed-e",
            "relation_id": "E",
            "source": "A",
            "target": "B",
            "before": before,
            "after": "Yes",
            "label_evidence_ids": ["branch-label"],
            "connector_evidence_ids": ["line-e", "arrow-e"],
        }
    ]
    assert candidate.typed_ir == original_ir
    assert candidate.scene_ir == original_scene


def test_conditional_label_repair_requires_trusted_text_and_geometry_connector():
    engine = EvidenceBackedFlowchartRepair()
    candidate = _conditional_label_candidate()

    untrusted_text = _conditional_label_context()
    untrusted_text.trusted_label_evidence_ids.clear()
    assert engine.repair(untrusted_text, candidate) is None

    weak_text = _conditional_label_context(score=0.5)
    assert engine.repair(weak_text, candidate) is None

    untrusted_connector = _conditional_label_context()
    untrusted_connector.trusted_connector_evidence_ids.clear()
    untrusted_connector.trusted_connector_relations.clear()
    assert engine.repair(untrusted_connector, candidate) is None

    weak_connector = _conditional_label_context(arrow_score=0.5)
    assert engine.repair(weak_connector, candidate) is None


def test_conditional_label_repair_refuses_spatial_or_relation_ambiguity():
    engine = EvidenceBackedFlowchartRepair()

    far_label = _conditional_label_context(label_bbox=(20, 20, 30, 24))
    assert engine.repair(far_label, _conditional_label_candidate()) is None

    duplicate_edge = _conditional_label_candidate(duplicate_edge=True)
    assert engine.repair(_conditional_label_context(), duplicate_edge) is None

    ambiguous_connector = _conditional_label_context()
    ambiguous_connector.evidence.extend(
        [
            VisualEvidence(
                id="line-e-2",
                kind="line_segment",
                score=0.95,
                bbox=(10, 5, 40, 5),
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="arrow-e-2",
                kind="arrowhead",
                score=0.95,
                bbox=(38, 3, 40, 7),
                source_block_ids=["source"],
            ),
        ]
    )
    ambiguous_connector.trusted_connector_evidence_ids.update({"line-e-2", "arrow-e-2"})
    ambiguous_connector.trusted_connector_relations.add(
        ("A", "B", frozenset({"line-e-2", "arrow-e-2"}))
    )
    assert engine.repair(ambiguous_connector, _conditional_label_candidate()) is None


def test_conditional_label_repair_refuses_nonconditional_or_semantic_overwrite():
    engine = EvidenceBackedFlowchartRepair()
    context = _conditional_label_context()

    assert (
        engine.repair(
            context,
            _conditional_label_candidate(conditional=False),
        )
        is None
    )
    assert engine.repair(context, _conditional_label_candidate("No")) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("semantic_relation", []),
        ("bidirectional", 0),
        ("arrow_at_start", []),
        ("arrow_at_end", "false"),
    ],
)
def test_conditional_label_repair_rejects_malformed_typed_fields_without_mutation(
    field,
    value,
):
    candidate = _conditional_label_candidate()
    candidate.typed_ir["edges"][0][field] = value
    original_ir = copy.deepcopy(candidate.typed_ir)
    original_scene = candidate.scene_ir.model_copy(deep=True)

    proposal = EvidenceBackedFlowchartRepair().repair(
        _conditional_label_context(),
        candidate,
    )

    assert proposal is None
    assert candidate.typed_ir == original_ir
    assert candidate.scene_ir == original_scene


def test_conditional_label_repair_refuses_explicit_nonconditional_semantics():
    engine = EvidenceBackedFlowchartRepair()
    context = _conditional_label_context()

    source_sequence = _conditional_label_candidate()
    source_sequence.scene_ir.relations[0].semantic_relation = "sequence"
    source_sequence.typed_ir["edges"][0]["semantic_relation"] = "unknown"
    assert engine.repair(context, source_sequence) is None

    typed_sequence = _conditional_label_candidate()
    typed_sequence.typed_ir["edges"][0]["semantic_relation"] = "sequence"
    assert engine.repair(context, typed_sequence) is None

    unconditional = _conditional_label_candidate()
    unconditional.scene_ir.relations[0].semantic_relation = "unknown"
    unconditional.scene_ir.relations[0].relation_type = "unconditional"
    unconditional.typed_ir["edges"][0]["semantic_relation"] = "unknown"
    assert engine.repair(context, unconditional) is None


def test_conditional_label_repair_requires_unique_direct_relation_attribution():
    engine = EvidenceBackedFlowchartRepair()
    context = _conditional_label_context()

    indirect = _conditional_label_candidate()
    indirect.scene_ir.relations[0].evidence_ids.remove("branch-label")
    assert engine.repair(context, indirect) is None

    shared = _conditional_label_candidate()
    shared.typed_ir["nodes"].append({"id": "C", "label": "Review"})
    shared.scene_ir.elements.append(
        SceneElement(
            id="C",
            role="node",
            text="Review",
            bbox=(40, 20, 50, 30),
        )
    )
    shared.scene_ir.relations.append(
        SceneRelation(
            id="E2",
            source_id="A",
            target_id="C",
            relation_type="sequence",
            polyline=[(10, 5), (40, 25)],
            confidence=0.95,
            evidence_ids=["branch-label"],
        )
    )
    assert engine.repair(context, shared) is None


@pytest.mark.parametrize(("source_id", "target_id"), [("A", "B"), ("B", "A")])
def test_conditional_label_repair_refuses_parallel_or_reversed_source_relations(
    source_id,
    target_id,
):
    candidate = _conditional_label_candidate()
    candidate.scene_ir.relations.append(
        SceneRelation(
            id="E2",
            source_id=source_id,
            target_id=target_id,
            relation_type="sequence",
            polyline=[(10, 5), (40, 5)],
            confidence=0.95,
        )
    )

    assert (
        EvidenceBackedFlowchartRepair().repair(
            _conditional_label_context(),
            candidate,
        )
        is None
    )


def test_conditional_label_repair_refuses_a_conflicted_connector_pair():
    context = _conditional_label_context()
    context.conflicted_connector_pairs.add(frozenset({"A", "B"}))

    assert (
        EvidenceBackedFlowchartRepair().repair(
            context,
            _conditional_label_candidate(),
        )
        is None
    )


def test_conditional_label_repair_refuses_multiple_nearby_trusted_connectors():
    candidate = _conditional_label_candidate()
    candidate.typed_ir["nodes"].extend(
        [
            {"id": "C", "label": "Audit"},
            {"id": "D", "label": "Archive"},
        ]
    )
    candidate.scene_ir.elements.extend(
        [
            SceneElement(
                id="C",
                role="node",
                text="Audit",
                bbox=(20, -20, 30, -10),
            ),
            SceneElement(
                id="D",
                role="node",
                text="Archive",
                bbox=(20, 20, 30, 30),
            ),
        ]
    )
    candidate.scene_ir.relations.append(
        SceneRelation(
            id="E2",
            source_id="C",
            target_id="D",
            relation_type="sequence",
            semantic_relation="sequence",
            label="Auxiliary",
            polyline=[(25, -10), (25, 20)],
            confidence=0.95,
            evidence_ids=["line-other", "arrow-other"],
        )
    )
    context = _conditional_label_context()
    context.evidence.extend(
        [
            VisualEvidence(
                id="line-other",
                kind="line_segment",
                score=0.95,
                bbox=(25, -10, 25, 20),
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="arrow-other",
                kind="arrowhead",
                score=0.95,
                bbox=(23, 18, 27, 20),
                source_block_ids=["source"],
            ),
        ]
    )
    context.trusted_connector_evidence_ids.update({"line-other", "arrow-other"})
    context.trusted_connector_relations.add(
        ("C", "D", frozenset({"line-other", "arrow-other"}))
    )

    assert EvidenceBackedFlowchartRepair().repair(context, candidate) is None


@pytest.mark.parametrize("polyline", [[], [(10, 5), (10, 5)]])
def test_conditional_label_repair_refuses_degenerate_source_polylines(polyline):
    candidate = _conditional_label_candidate()
    candidate.scene_ir.relations[0].polyline = polyline

    assert (
        EvidenceBackedFlowchartRepair().repair(
            _conditional_label_context(),
            candidate,
        )
        is None
    )


def test_conditional_label_repair_requires_spatially_isolated_same_block_evidence():
    engine = EvidenceBackedFlowchartRepair()
    candidate = _conditional_label_candidate()

    zero_area = _conditional_label_context(label_bbox=(20, 5, 20, 5))
    assert engine.repair(zero_area, candidate) is None

    node_overlap = _conditional_label_context(label_bbox=(2, 2, 8, 8))
    assert engine.repair(node_overlap, candidate) is None

    other_block = _conditional_label_context()
    other_block.evidence[0].source_block_ids = ["other"]
    assert engine.repair(other_block, candidate) is None

    line_mismatch = _conditional_label_context()
    line_mismatch.evidence[1].bbox = (100, 100, 120, 100)
    assert engine.repair(line_mismatch, candidate) is None


def test_conditional_label_repair_uses_nfkc_casefolded_source_evidence_match():
    candidate = _conditional_label_candidate()
    candidate.scene_ir.relations[0].label = "Ｙｅｓ"

    proposal = EvidenceBackedFlowchartRepair().repair(
        _conditional_label_context(text=" yes "),
        candidate,
    )

    assert proposal is not None
    assert proposal.typed_ir["edges"][0]["label"] == "Ｙｅｓ"
    assert proposal.details["edge_label_corrections"][0]["label_evidence_ids"] == [
        "branch-label"
    ]


def test_conditional_label_repair_treats_normalized_equal_typed_label_as_a_noop():
    candidate = _conditional_label_candidate(" YES ")
    original_ir = copy.deepcopy(candidate.typed_ir)
    original_scene = candidate.scene_ir.model_copy(deep=True)

    proposal = EvidenceBackedFlowchartRepair().repair(
        _conditional_label_context(),
        candidate,
    )

    assert proposal is None
    assert candidate.typed_ir == original_ir
    assert candidate.scene_ir == original_scene


def test_flowchart_repair_refuses_ambiguous_or_semantic_topology_changes():
    engine = EvidenceBackedFlowchartRepair()
    context = _structural_context()

    labeled = _structural_candidate(edge=False, relation_label="yes")
    labeled.scene_ir.elements[0].text = "Paymant"
    assert engine.repair(context, labeled) is None

    bidirectional = _structural_candidate()
    bidirectional.scene_ir.elements[0].text = "Paymant"
    bidirectional.typed_ir["edges"][0]["bidirectional"] = True
    assert engine.repair(context, bidirectional) is None

    labeled_edge = _structural_candidate()
    labeled_edge.scene_ir.elements[0].text = "Paymant"
    labeled_edge.typed_ir["edges"][0]["label"] = "yes"
    assert engine.repair(context, labeled_edge) is None

    ambiguous = _structural_candidate()
    ambiguous.scene_ir.elements[0].text = "Paymant"
    ambiguous.scene_ir.relations.append(
        SceneRelation(
            id="E2",
            source_id="B",
            target_id="A",
            relation_type="sequence",
            confidence=0.95,
            evidence_ids=["line-e", "arrow-e"],
        )
    )
    assert engine.repair(context, ambiguous) is None

    weak_parallel = _structural_candidate()
    weak_parallel.scene_ir.elements[0].text = "Paymant"
    weak_parallel.scene_ir.relations.append(
        SceneRelation(
            id="E2",
            source_id="B",
            target_id="A",
            relation_type="association",
            arrow_at_end=False,
            confidence=0.2,
        )
    )
    assert engine.repair(context, weak_parallel) is None

    hidden_parallel_context = _structural_context()
    hidden_parallel_context.trusted_connector_relations.add(
        ("B", "A", frozenset({"other-line", "other-arrow"}))
    )
    hidden_parallel = _structural_candidate()
    hidden_parallel.scene_ir.elements[0].text = "Paymant"
    assert engine.repair(hidden_parallel_context, hidden_parallel) is None

    conditional = _structural_candidate(edge=False)
    conditional.scene_ir.elements[0].text = "Paymant"
    conditional.scene_ir.relations[0].semantic_relation = "conditional"
    assert engine.repair(context, conditional) is None

    typed_conditional = _structural_candidate()
    typed_conditional.scene_ir.elements[0].text = "Paymant"
    typed_conditional.typed_ir["edges"][0]["semantic_relation"] = "conditional"
    assert engine.repair(context, typed_conditional) is None

    decision_source = _structural_candidate(edge=False)
    decision_source.scene_ir.elements[0].text = "Paymant"
    decision_source.scene_ir.elements[0].shape = "diamond"
    assert engine.repair(context, decision_source) is None

    typed_decision = _structural_candidate(edge=False)
    typed_decision.scene_ir.elements[0].text = "Paymant"
    typed_decision.typed_ir["nodes"][0]["role"] = "gateway"
    assert engine.repair(context, typed_decision) is None


def test_flowchart_repair_combines_label_and_direction_corrections():
    proposal = EvidenceBackedFlowchartRepair().repair(
        _structural_context(),
        _structural_candidate(),
    )

    assert proposal is not None
    assert proposal.operation == "repair_evidence_backed_flowchart"
    assert proposal.typed_ir["nodes"][0]["label"] == "Payment"
    assert proposal.typed_ir["edges"][0]["source"] == "A"
    assert proposal.details["label_corrections"][0]["node_id"] == "A"
