from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.semantic_repair import EvidenceBackedLabelRepair
from marker_mermaid.validation import CandidateValidator


class FlowRuntime:
    def __init__(self, *, drift_on_payment=False):
        self.drift_on_payment = drift_on_payment

    def validate_and_render(self, code, timeout_seconds):
        diagram_type = (
            "sequence" if self.drift_on_payment and 'A["Payment"]' in code else "flowchart-v2"
        )
        return RuntimeResult(
            True,
            True,
            diagram_type=diagram_type,
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
        )

    def close(self):
        pass


def repair_observation(*, invented_node=False, evidence_kind="ocr_token"):
    nodes = [
        {"id": "A", "label": "Paymant"},
        {"id": "B", "label": "Done", "evidence_ids": ["text-b"]},
    ]
    edges = [{"source": "A", "target": "B"}]
    if invented_node:
        nodes.append({"id": "C", "label": "Invented"})
        edges.append({"source": "B", "target": "C"})
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="A",
                    role="node",
                    text="Payment",
                    bbox=(0, 0, 10, 10),
                    evidence_ids=["text-a"],
                ),
                SceneElement(
                    id="B",
                    role="node",
                    text="Done",
                    bbox=(20, 0, 30, 10),
                    evidence_ids=["text-b"],
                ),
            ],
            relations=[
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type="edge",
                    evidence_ids=["line-e"],
                )
            ],
            reading_direction="LR",
        ),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={"nodes": nodes, "edges": edges},
            )
        ],
        evidence=[
            VisualEvidence(
                id="text-a",
                kind=evidence_kind,
                text="Payment",
                score=0.95 if evidence_kind == "ocr_token" else None,
                bbox=(0, 0, 10, 10),
            ),
            VisualEvidence(
                id="text-b",
                kind="vector_text",
                text="Done",
                bbox=(20, 0, 30, 10),
            ),
            VisualEvidence(id="line-e", kind="line_segment", bbox=(10, 5, 20, 5)),
        ],
    )


def run_repair(observation, runtime=None):
    config = MermaidConfig(candidate_count=1)
    return ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime or FlowRuntime(), config.security_profile),
        repair_engine=EvidenceBackedLabelRepair(),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))


def test_default_semantic_repair_improves_ocr_and_preserves_structural_scores():
    result = run_repair(repair_observation())

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.typed_ir["nodes"][0]["label"] == "Payment"
    assert result.selected.scores["ocr_recall"] == 1
    assert result.selected.scores["edge_agreement"] == 1
    assert result.selected.scores["arrow_agreement"] == 1
    assert result.selected.scores["path_consistency"] == 1
    assert result.selected.scores["visual_entailment_precision"] == 1
    assert result.selected.repair_history[-1].accepted
    [baseline] = result.alternatives
    assert baseline.typed_ir["nodes"][0]["label"] == "Paymant"
    assert baseline.repair_history == []


def test_semantic_repair_cannot_unlock_a_provenance_gated_candidate():
    result = run_repair(repair_observation(invented_node=True))

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["nodes"][0]["label"] == "Paymant"


def test_semantic_repair_rejects_runtime_type_drift_without_corrupting_baseline():
    result = run_repair(
        repair_observation(evidence_kind="vector_text"),
        FlowRuntime(drift_on_payment=True),
    )

    assert result.selected is not None
    assert result.selected.typed_ir["nodes"][0]["label"] == "Paymant"
    assert not result.selected.repair_history[-1].accepted
    assert any("runtime diagram type changed" in warning for warning in result.selected.warnings)
    baseline = next(item for item in result.alternatives if item.candidate_id == "candidate-1")
    assert baseline.repair_history == []
