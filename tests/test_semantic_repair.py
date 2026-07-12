from marker_mermaid.models import (
    DiagramSceneIR,
    MermaidCandidate,
    RepairEvent,
    SceneElement,
    SceneRelation,
    VisualEvidence,
)
from marker_mermaid.protocols import SourceContext
from marker_mermaid.semantic_repair import EvidenceBackedLabelRepair


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
    )
    return SourceContext(
        source_id="source",
        source_block_ids=["source"],
        source_image_name="source.png",
        image=None,  # type: ignore[arg-type]
        evidence=[evidence],
    )


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
