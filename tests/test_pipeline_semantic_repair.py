import copy

import pytest
from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.geometry import (
    ArrowheadObservation,
    ContourObservation,
    GeometryEngine,
    GeometryObservation,
    LineObservation,
)
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
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.semantic_repair import EvidenceBackedFlowchartRepair
from marker_mermaid.validation import CandidateValidator


class FlowRuntime:
    def __init__(self, *, drift_on_payment=False):
        self.drift_on_payment = drift_on_payment

    def validate_and_render(self, code, timeout_seconds):
        diagram_type = (
            "sequence"
            if self.drift_on_payment and 'geometry_node_001["Payment"]' in code
            else "flowchart-v2"
        )
        return RuntimeResult(
            True,
            True,
            diagram_type=diagram_type,
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
        )

    def close(self):
        pass


class DivergentRepair:
    name = "divergent_repair"

    def repair(self, context, candidate):
        proposal = EvidenceBackedFlowchartRepair().repair(context, candidate)
        if proposal is None:
            return None
        return RepairProposal(
            code=f"{candidate.mermaid_code}\n",
            operation="divergent_repair",
            typed_ir=copy.deepcopy(proposal.typed_ir),
        )


class OversizedRepair:
    name = "oversized_repair"

    def repair(self, context, candidate):
        nested = "overflow"
        for _ in range(70):
            nested = [nested]
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["oversized"] = nested
        return RepairProposal(
            code=f"{candidate.mermaid_code}\n",
            operation="oversized_repair",
            typed_ir=typed_ir,
        )


def repair_observation(
    *,
    invented_node=False,
    evidence_kind="ocr_token",
    correct_label=False,
    edge_mode="correct",
    spoof_connector=False,
    scene_reversed=False,
):
    nodes = [
        {
            "id": "geometry-node-001",
            "label": "Payment" if correct_label else "Paymant",
        },
        {"id": "geometry-node-002", "label": "Done", "evidence_ids": ["text-b"]},
    ]
    edges = {
        "correct": [{"source": "geometry-node-001", "target": "geometry-node-002"}],
        "reversed": [{"source": "geometry-node-002", "target": "geometry-node-001"}],
        "missing": [],
    }[edge_mode]
    if invented_node:
        nodes.append({"id": "C", "label": "Invented"})
        edges.append({"source": "geometry-node-002", "target": "C"})
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="geometry-node-001",
                    role="node",
                    text="Payment",
                    bbox=(0, 0, 10, 10),
                    evidence_ids=["text-a"],
                ),
                SceneElement(
                    id="geometry-node-002",
                    role="node",
                    text="Done",
                    bbox=(40, 0, 50, 10),
                    evidence_ids=["text-b"],
                ),
            ],
            relations=[
                SceneRelation(
                    id="E",
                    source_id=(
                        "geometry-node-002" if scene_reversed else "geometry-node-001"
                    ),
                    target_id=(
                        "geometry-node-001" if scene_reversed else "geometry-node-002"
                    ),
                    relation_type="edge",
                    confidence=0.95,
                    evidence_ids=["geometry-line-001", "geometry-arrowhead-001"],
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
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="text-b",
                kind="vector_text",
                text="Done",
                bbox=(40, 0, 50, 10),
                source_block_ids=["source"],
            ),
        ],
    )
    if spoof_connector:
        observation.evidence.extend(
            [
                VisualEvidence(
                    id="geometry-line-001",
                    kind="line_segment",
                    score=1,
                    bbox=(10, 5, 40, 5),
                    source_block_ids=["source"],
                ),
                VisualEvidence(
                    id="geometry-arrowhead-001",
                    kind="arrowhead",
                    score=1,
                    bbox=(38, 3, 40, 7),
                    source_block_ids=["source"],
                ),
            ]
        )
    return observation


def conditional_label_observation(
    *,
    typed_label=None,
    label_evidence_kind="ocr_token",
    edge_mode="exact",
):
    observation = repair_observation(correct_label=True)
    source_id = "geometry-node-001"
    target_id = "geometry-node-002"
    edge = {
        "id": "typed-branch",
        "source": source_id,
        "target": target_id,
        "relation_type": "conditional_branch",
        "semantic_relation": "conditional",
        "arrow_at_start": False,
        "arrow_at_end": True,
        "style": "dashed",
        "evidence_ids": ["typed-prior"],
    }
    if typed_label is not None:
        edge["label"] = typed_label
    if edge_mode == "reversed":
        edge["source"], edge["target"] = edge["target"], edge["source"]
        edges = [edge]
    elif edge_mode == "parallel":
        parallel = copy.deepcopy(edge)
        parallel["id"] = "typed-branch-parallel"
        edges = [edge, parallel]
    else:
        edges = [edge]

    typed_ir = observation.typed_candidates[0].ir
    typed_ir["nodes"][0]["role"] = "decision"
    typed_ir["nodes"][0]["shape"] = "diamond"
    typed_ir["edges"] = edges
    observation.scene_ir.elements[0].role = "decision"
    observation.scene_ir.elements[0].shape = "diamond"
    observation.scene_ir.relations = [
        SceneRelation(
            id="source-conditional-branch",
            source_id=source_id,
            target_id=target_id,
            relation_type="conditional_branch",
            semantic_relation="conditional",
            label="Yes",
            polyline=[(10, 5), (40, 5)],
            arrow_at_start=False,
            arrow_at_end=True,
            confidence=0.95,
            evidence_ids=[
                "geometry-line-001",
                "geometry-arrowhead-001",
                "branch-label",
            ],
        )
    ]
    observation.evidence.append(
        VisualEvidence(
            id="branch-label",
            kind=label_evidence_kind,
            text="Yes",
            score=0.95 if label_evidence_kind == "ocr_token" else None,
            bbox=(20, 3, 30, 7),
            source_block_ids=["source"],
        )
    )
    return observation


def run_repair(
    observation,
    runtime=None,
    *,
    connector_score=0.95,
    repair_engine=None,
    trust_labels=True,
):
    config = MermaidConfig(candidate_count=1, enable_generic_scene_ir=False)
    geometry = GeometryObservation(
        canvas_size=(100, 50),
        contours=(
            ContourObservation(bbox=(0, 0, 10, 10), confidence=0.95),
            ContourObservation(bbox=(40, 0, 50, 10), confidence=0.95),
        ),
        lines=(
            LineObservation(start=(10, 5), end=(40, 5), confidence=connector_score),
        ),
        arrowheads=(
            ArrowheadObservation(
                bbox=(38, 3, 40, 7),
                tip=(40, 5),
                confidence=connector_score,
            ),
        ),
    )
    fixture_observation = observation.model_copy(deep=True)
    trusted_label_evidence = []
    if trust_labels:
        trusted_label_evidence = [
            item
            for item in fixture_observation.evidence
            if item.kind in {"ocr_token", "vector_text"}
        ]
        fixture_observation.evidence = [
            item
            for item in fixture_observation.evidence
            if item.kind not in {"ocr_token", "vector_text"}
        ]
    return ReconstructionPipeline(
        config,
        [
            GeometryEngine(detector=lambda _image: geometry),
            JsonFixtureEngine(fixture_observation),
        ],
        CandidateValidator(runtime or FlowRuntime(), config.security_profile),
        repair_engine=repair_engine or EvidenceBackedFlowchartRepair(),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        evidence=trusted_label_evidence,
    )


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


def test_semantic_repair_refuses_self_declared_vlm_label_evidence():
    result = run_repair(repair_observation(), trust_labels=False)

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["nodes"][0]["label"] == "Paymant"
    assert result.selected.repair_history == []


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


def test_semantic_repair_accepts_an_evidence_backed_direction_correction():
    result = run_repair(
        repair_observation(correct_label=True, edge_mode="reversed")
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-001"
    assert result.selected.scores["arrow_agreement"] == 1
    assert result.selected.scores["path_consistency"] == 1
    assert result.selected.repair_history[-1].accepted
    [baseline] = result.alternatives
    assert baseline.typed_ir["edges"][0]["source"] == "geometry-node-002"
    assert baseline.scores["arrow_agreement"] < 1
    assert baseline.scores["path_consistency"] < 1


def test_semantic_repair_accepts_the_builtin_geometry_confidence_floor():
    result = run_repair(
        repair_observation(correct_label=True, edge_mode="reversed"),
        connector_score=0.6,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-001"


def test_semantic_repair_accepts_an_evidence_backed_missing_edge():
    result = run_repair(repair_observation(correct_label=True, edge_mode="missing"))

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-001"
    assert result.selected.typed_ir["edges"][0]["target"] == "geometry-node-002"
    assert result.selected.scores["edge_agreement"] == 1
    assert result.selected.scores["path_consistency"] == 1
    assert result.selected.repair_history[-1].accepted


def test_semantic_repair_refuses_weak_connector_evidence():
    result = run_repair(
        repair_observation(correct_label=True, edge_mode="reversed"),
        connector_score=0.5,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-002"
    assert result.selected.repair_history == []


def test_semantic_repair_revokes_geometry_trust_on_evidence_id_collision():
    result = run_repair(
        repair_observation(
            correct_label=True,
            edge_mode="reversed",
            spoof_connector=True,
        )
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-002"
    assert result.selected.repair_history == []


def test_semantic_repair_refuses_cross_engine_direction_conflicts():
    result = run_repair(
        repair_observation(
            correct_label=True,
            edge_mode="reversed",
            scene_reversed=True,
        )
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-002"
    assert result.selected.repair_history == []


@pytest.mark.parametrize(
    ("typed_label", "label_evidence_kind"),
    [(None, "ocr_token"), ("Yse", "vector_text")],
)
def test_semantic_repair_relabels_an_existing_conditional_edge_without_topology_drift(
    typed_label,
    label_evidence_kind,
):
    result = run_repair(
        conditional_label_observation(
            typed_label=typed_label,
            label_evidence_kind=label_evidence_kind,
        )
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    [baseline] = result.alternatives
    baseline_edge = baseline.typed_ir["edges"][0]
    repaired_edge = result.selected.typed_ir["edges"][0]
    assert baseline_edge.get("label") == typed_label
    assert repaired_edge["label"] == "Yes"
    baseline_connector = "-.->" if typed_label is None else f"-.->|{typed_label}|"
    assert (
        f"geometry_node_001 {baseline_connector} geometry_node_002"
        in baseline.mermaid_code
    )
    assert (
        "geometry_node_001 -.->|Yes| geometry_node_002"
        in result.selected.mermaid_code
    )
    for field in (
        "id",
        "source",
        "target",
        "relation_type",
        "semantic_relation",
        "arrow_at_start",
        "arrow_at_end",
        "style",
    ):
        assert repaired_edge[field] == baseline_edge[field]
    assert len(result.selected.typed_ir["edges"]) == len(baseline.typed_ir["edges"]) == 1
    assert {
        "branch-label",
        "geometry-line-001",
        "geometry-arrowhead-001",
    }.issubset(repaired_edge["evidence_ids"])

    assert result.selected.scores["ocr_recall"] == 1
    assert result.selected.scores["ocr_recall"] > baseline.scores["ocr_recall"]
    for metric in ("edge_agreement", "arrow_agreement", "path_consistency"):
        assert result.selected.scores[metric] == baseline.scores[metric] == 1

    event = result.selected.repair_history[-1]
    assert event.accepted
    [correction] = event.details["edge_label_corrections"]
    assert correction["operation"] == "relabel_conditional_edge"
    assert correction["edge_id"] == "typed-branch"
    assert correction["relation_id"] == baseline.scene_ir.relations[0].id
    assert correction["source"] == "geometry-node-001"
    assert correction["target"] == "geometry-node-002"
    assert correction["before"] in ({None, ""} if typed_label is None else {typed_label})
    assert correction["after"] == "Yes"
    assert correction["label_evidence_ids"] == ["branch-label"]
    assert set(correction["connector_evidence_ids"]) == {
        "geometry-line-001",
        "geometry-arrowhead-001",
    }
    assert baseline.repair_history == []


def test_semantic_repair_refuses_self_declared_conditional_edge_label_evidence():
    result = run_repair(
        conditional_label_observation(),
        trust_labels=False,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["edges"][0].get("label") is None
    assert result.selected.repair_history == []


def test_semantic_repair_refuses_a_conditional_edge_label_with_a_weak_connector():
    result = run_repair(
        conditional_label_observation(typed_label="Yse"),
        connector_score=0.5,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["edges"][0]["label"] == "Yse"
    assert result.selected.repair_history == []


@pytest.mark.parametrize("edge_mode", ["reversed", "parallel"])
def test_semantic_repair_requires_one_exact_same_direction_conditional_edge(edge_mode):
    result = run_repair(
        conditional_label_observation(edge_mode=edge_mode),
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert all(edge.get("label") is None for edge in result.selected.typed_ir["edges"])
    assert result.selected.repair_history == []


def test_semantic_repair_rejects_code_and_typed_ir_divergence():
    result = run_repair(
        repair_observation(correct_label=True, edge_mode="reversed"),
        repair_engine=DivergentRepair(),
    )

    assert result.selected is not None
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-002"
    assert not result.selected.repair_history[-1].accepted
    assert any("code and typed IR diverged" in warning for warning in result.selected.warnings)


def test_semantic_repair_revalidates_typed_ir_resource_budgets():
    result = run_repair(
        repair_observation(correct_label=True),
        repair_engine=OversizedRepair(),
    )

    assert result.selected is not None
    assert "oversized" not in result.selected.typed_ir
    assert not result.selected.repair_history[-1].accepted
    assert any("could not be serialized" in warning for warning in result.selected.warnings)
