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
