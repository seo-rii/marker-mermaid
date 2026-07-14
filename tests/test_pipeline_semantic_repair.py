import copy

import pytest
from PIL import Image

import marker_mermaid.pipeline as pipeline_module
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
    MAX_EVIDENCE_REFS,
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    SceneGroup,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.semantic_repair import EvidenceBackedFlowchartRepair
from marker_mermaid.serializers import serialize_typed_ir_result
from marker_mermaid.validation import CandidateValidator


class FlowRuntime:
    def __init__(self, *, drift_on_payment=False):
        self.drift_on_payment = drift_on_payment
        self.calls = []

    def validate_and_render(self, code, timeout_seconds):
        self.calls.append(code)
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


class NodeSetChangingRepair:
    name = "node_set_changing_repair"

    def repair(self, context, candidate):
        typed_ir = copy.deepcopy(candidate.typed_ir)
        old_id = typed_ir["nodes"][1]["id"]
        typed_ir["nodes"][1]["id"] = "invented-node"
        for edge in typed_ir.get("edges", []):
            if edge.get("target") == old_id:
                edge["target"] = "invented-node"
        for group in typed_ir.get("groups", []):
            group["member_ids"] = [
                "invented-node" if member_id == old_id else member_id
                for member_id in group.get("member_ids", [])
            ]
        return RepairProposal(
            code=f"{candidate.mermaid_code}\n",
            operation="node_set_changing_repair",
            typed_ir=typed_ir,
        )


class MutatingNoopRepair:
    name = "mutating_noop_repair"

    def repair(self, context, candidate):
        context.evidence.clear()
        context.trusted_label_evidence_ids.clear()
        context.trusted_connector_evidence_ids.clear()
        context.trusted_connector_relations.clear()
        if context.source_mapping is not None:
            context.source_mapping["nested"]["value"] = "Mutated"
        candidate.typed_ir["nodes"][0]["label"] = "Mutated"
        candidate.scene_ir.elements[0].text = "Mutated"
        return None


class NonCopyableSource:
    def __deepcopy__(self, _memo):
        raise AssertionError("opaque Marker source must not be deep-copied for semantic repair")


class VlmFixtureEngine(JsonFixtureEngine):
    name = "vlm_fixture"
    fusion_source = "vlm"


class PromptOmittingFixtureEngine(JsonFixtureEngine):
    name = "prompt_omitting_fixture"
    fusion_source = "vlm"
    omitted_evidence_ids: frozenset[str] = frozenset()

    def observe(self, context):
        observation = super().observe(context)
        observation._set_prompt_supplied_prior_evidence_ids(
            {item.id for item in context.evidence if item.id not in self.omitted_evidence_ids}
        )
        return observation


class LabelOmittingFixtureEngine(PromptOmittingFixtureEngine):
    omitted_evidence_ids = frozenset({"text-a"})


class ConnectorOmittingFixtureEngine(PromptOmittingFixtureEngine):
    omitted_evidence_ids = frozenset({"geometry-line-001", "geometry-arrowhead-001"})


class RecordingRepair:
    name = "recording_evidence_backed_flowchart_repair"

    def __init__(self):
        self.conflicted_connector_pairs = []

    def repair(self, context, candidate):
        self.conflicted_connector_pairs.append(set(context.conflicted_connector_pairs))
        return EvidenceBackedFlowchartRepair().repair(context, candidate)


class PacketRangeSwapRepair:
    name = "packet_range_swap"

    def repair(self, context, candidate):
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["fields"] = [
            {
                "id": "ihl",
                "start": 0,
                "end": 3,
                "label": "IHL",
                "bbox": (0, 0, 40, 20),
                "evidence_ids": ["ocr-ihl"],
            },
            {
                "id": "version",
                "start": 4,
                "end": 7,
                "label": "Version",
                "bbox": (50, 0, 90, 20),
                "evidence_ids": ["ocr-version"],
            },
        ]
        serialized = serialize_typed_ir_result(
            "packet",
            typed_ir,
            experimental=True,
        )
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
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
                    source_id=("geometry-node-002" if scene_reversed else "geometry-node-001"),
                    target_id=("geometry-node-001" if scene_reversed else "geometry-node-002"),
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


def namespaced_conditional_label_observation(*, scene_reversed=False):
    source_id = "A"
    target_id = "B"
    scene_source = target_id if scene_reversed else source_id
    scene_target = source_id if scene_reversed else target_id
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id=source_id,
                    role="decision",
                    text="Approve?",
                    bbox=(0, 0, 10, 10),
                    shape="diamond",
                    confidence=0.9,
                    evidence_ids=["vlm-node-a"],
                ),
                SceneElement(
                    id=target_id,
                    role="process",
                    text="Done",
                    bbox=(40, 0, 50, 10),
                    shape="rectangle",
                    confidence=0.9,
                    evidence_ids=["vlm-node-b"],
                ),
            ],
            relations=[
                SceneRelation(
                    id="vlm-conditional-branch",
                    source_id=scene_source,
                    target_id=scene_target,
                    relation_type="conditional_branch",
                    semantic_relation="conditional",
                    label="Yes",
                    polyline=[(10, 5), (40, 5)],
                    arrow_at_start=False,
                    arrow_at_end=True,
                    confidence=0.9,
                    evidence_ids=["vlm-relation", "branch-label"],
                )
            ],
            groups=[
                SceneGroup(
                    id="vlm-phase",
                    role="subgraph",
                    label="Approval",
                    bbox=(0, 0, 50, 10),
                    member_ids=[source_id, target_id],
                )
            ],
            reading_direction="LR",
            diagram_type_candidates=["flowchart"],
            canvas_size=(100, 50),
        ),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "direction": "LR",
                    "nodes": [
                        {
                            "id": source_id,
                            "label": "Approve?",
                            "role": "decision",
                            "shape": "diamond",
                            "bbox": [0, 0, 10, 10],
                            "evidence_ids": ["vlm-node-a"],
                        },
                        {
                            "id": target_id,
                            "label": "Done",
                            "role": "process",
                            "shape": "rectangle",
                            "bbox": [40, 0, 50, 10],
                            "evidence_ids": ["vlm-node-b"],
                        },
                    ],
                    "edges": [
                        {
                            "id": "typed-branch",
                            "source": source_id,
                            "target": target_id,
                            "relation_type": "conditional_branch",
                            "semantic_relation": "conditional",
                            "arrow_at_start": False,
                            "arrow_at_end": True,
                            "style": "dashed",
                            "evidence_ids": ["vlm-relation"],
                        }
                    ],
                    "groups": [
                        {
                            "id": "typed-phase",
                            "label": "Approval",
                            "role": "subgraph",
                            "bbox": [0, 0, 50, 10],
                            "member_ids": [source_id, target_id],
                            "evidence_ids": ["vlm-node-a", "vlm-node-b"],
                        }
                    ],
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="vlm-node-a",
                kind="vlm_observation",
                bbox=(0, 0, 10, 10),
                score=0.9,
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="vlm-node-b",
                kind="vlm_observation",
                bbox=(40, 0, 50, 10),
                score=0.9,
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="vlm-relation",
                kind="vlm_observation",
                bbox=(10, 5, 40, 5),
                score=0.9,
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="branch-label",
                kind="ocr_token",
                text="Yes",
                bbox=(20, 3, 30, 7),
                score=0.95,
                source_block_ids=["source"],
            ),
        ],
    )


def run_repair(
    observation,
    runtime=None,
    *,
    connector_score=0.95,
    fixture_engine_type=JsonFixtureEngine,
    repair_engine=None,
    trust_labels=True,
    enable_fusion=True,
    source_block=None,
    vector_sources=None,
    source_mapping=None,
):
    config = MermaidConfig(
        candidate_count=1,
        enable_fusion=enable_fusion,
        enable_generic_scene_ir=False,
    )
    geometry = GeometryObservation(
        canvas_size=(100, 50),
        contours=(
            ContourObservation(bbox=(0, 0, 10, 10), confidence=0.95),
            ContourObservation(bbox=(40, 0, 50, 10), confidence=0.95),
        ),
        lines=(LineObservation(start=(10, 5), end=(40, 5), confidence=connector_score),),
        arrowheads=(
            ArrowheadObservation(
                bbox=(38, 3, 40, 7),
                tip=(40, 5),
                confidence=connector_score,
            ),
        ),
    )
    fixture_observation = observation.model_copy(deep=True)
    prior_evidence = []
    if trust_labels:
        prior_node_evidence_ids = (
            {
                evidence_id
                for element in fixture_observation.scene_ir.elements
                for evidence_id in element.evidence_ids
            }
            if fixture_engine_type is VlmFixtureEngine and fixture_observation.scene_ir is not None
            else set()
        )
        prior_evidence = [
            item
            for item in fixture_observation.evidence
            if item.kind in {"ocr_token", "vector_text"} or item.id in prior_node_evidence_ids
        ]
        fixture_observation.evidence = [
            item for item in fixture_observation.evidence if item not in prior_evidence
        ]
    return ReconstructionPipeline(
        config,
        [
            GeometryEngine(detector=lambda _image: geometry),
            fixture_engine_type(fixture_observation),
        ],
        CandidateValidator(runtime or FlowRuntime(), config.security_profile),
        repair_engine=repair_engine or EvidenceBackedFlowchartRepair(),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        evidence=prior_evidence,
        source_block=source_block,
        vector_sources=vector_sources,
        source_mapping=source_mapping,
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
    assert result.selected.has_validated_publication_artifacts()
    [baseline] = result.alternatives
    assert baseline.typed_ir["nodes"][0]["label"] == "Paymant"
    assert baseline.repair_history == []


def test_semantic_repair_cannot_unlock_a_provenance_gated_candidate():
    result = run_repair(repair_observation(invented_node=True))

    assert result.selected is not None
    assert result.selected.aggregate_score is None
    assert not result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["nodes"][0]["label"] == "Paymant"


def test_semantic_repair_cannot_bypass_packet_field_range_association() -> None:
    class PacketRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="packet",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["packet"], scores=[0.9]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="packet",
                ir={
                    "fields": [
                        {
                            "id": "version",
                            "start": 0,
                            "end": 3,
                            "label": "Version",
                            "bbox": (0, 0, 40, 20),
                            "evidence_ids": ["ocr-version"],
                        },
                        {
                            "id": "ihl",
                            "start": 4,
                            "end": 7,
                            "label": "IHL",
                            "bbox": (50, 0, 90, 20),
                            "evidence_ids": ["ocr-ihl"],
                        },
                    ]
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="ocr-version",
                kind="ocr_token",
                text="Version 0 3",
                bbox=(5, 5, 35, 15),
            ),
            VisualEvidence(
                id="ocr-ihl",
                kind="vector_text",
                text="IHL 4 7",
                bbox=(55, 5, 85, 15),
            ),
        ],
    )
    config = MermaidConfig(
        candidate_count=1,
        enable_fusion=False,
        enable_generic_scene_ir=False,
        max_repair_iterations=1,
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(PacketRuntime(), config.security_profile),
        repair_engine=PacketRangeSwapRepair(),
    ).reconstruct(
        "packet-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        ocr_texts=["Version 0 3 IHL 4 7"],
    )

    assert result.selected is not None
    assert result.selected.scores["numeric_consistency"] == 1
    assert result.selected.aggregate_score is not None
    assert [
        (field["label"], field["start"], field["end"])
        for field in result.selected.typed_ir["fields"]
    ] == [("Version", 0, 3), ("IHL", 4, 7)]
    event = result.selected.repair_history[-1]
    assert event.operation == "packet_range_swap"
    assert not event.accepted
    assert event.before_score is not None
    assert event.after_score is None


def test_semantic_repair_refuses_self_declared_vlm_label_evidence():
    result = run_repair(repair_observation(), trust_labels=False)

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["nodes"][0]["label"] == "Paymant"
    assert result.selected.repair_history == []


def test_default_semantic_repair_cannot_reuse_prompt_omitted_label_evidence():
    result = run_repair(
        repair_observation(),
        fixture_engine_type=LabelOmittingFixtureEngine,
        enable_fusion=False,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["nodes"][0]["label"] == "Paymant"
    assert result.selected.repair_history == []
    assert "text-a" not in result.selected.publication_evidence_authority_ids


def test_semantic_repair_receives_isolated_context_and_candidate_snapshots():
    opaque_source = NonCopyableSource()
    source_mapping = {"nested": {"value": "Original"}}
    result = run_repair(
        repair_observation(correct_label=True),
        repair_engine=MutatingNoopRepair(),
        source_block=opaque_source,
        vector_sources=[opaque_source],
        source_mapping=source_mapping,
    )

    assert result.selected is not None
    assert result.selected.typed_ir["nodes"][0]["label"] == "Payment"
    assert result.selected.scene_ir.elements[0].text == "Payment"
    assert {item.id for item in result.evidence}.issuperset(
        {"text-a", "text-b", "geometry-line-001", "geometry-arrowhead-001"}
    )
    assert result.source_mapping == {"nested": {"value": "Original"}}
    assert not any("repair engine failed" in warning for warning in result.selected.warnings)


def test_semantic_repair_revalidates_current_typed_ir_before_copy_or_exposure(
    monkeypatch,
):
    class ExplosiveDict(dict):
        def __deepcopy__(self, _memo):
            raise AssertionError("unvalidated typed IR must not be deep-copied")

    class NeverCalledRepair:
        name = "never_called"

        def __init__(self):
            self.called = False

        def repair(self, _context, _candidate):
            self.called = True
            raise AssertionError("invalid current typed IR must not reach semantic repair")

    repair = NeverCalledRepair()
    original_select = ReconstructionPipeline._select

    def inject_noncanonical_ir(self, candidates):
        selected = original_select(self, candidates)
        assert selected is not None
        selected.typed_ir = ExplosiveDict(selected.typed_ir)
        return selected

    monkeypatch.setattr(ReconstructionPipeline, "_select", inject_noncanonical_ir)
    monkeypatch.setattr(
        pipeline_module,
        "certify_publication_result",
        lambda _result, _config: False,
    )

    result = run_repair(
        repair_observation(correct_label=True),
        repair_engine=repair,
        enable_fusion=False,
    )

    assert not repair.called
    assert result.selected is not None
    assert result.selected.typed_ir is None
    assert any(
        "repair candidate typed IR validation failed" in warning
        for warning in result.selected.warnings
    )


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
    result = run_repair(repair_observation(correct_label=True, edge_mode="reversed"))

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


def test_default_semantic_repair_cannot_reuse_prompt_omitted_connector_evidence():
    result = run_repair(
        repair_observation(correct_label=True, edge_mode="reversed"),
        fixture_engine_type=ConnectorOmittingFixtureEngine,
        enable_fusion=False,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.typed_ir["edges"][0]["source"] == "geometry-node-002"
    assert result.selected.repair_history == []
    assert result.selected.publication_evidence_authority_ids.isdisjoint(
        {"geometry-line-001", "geometry-arrowhead-001"}
    )


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
    assert f"geometry_node_001 {baseline_connector} geometry_node_002" in baseline.mermaid_code
    assert "geometry_node_001 -.->|Yes| geometry_node_002" in result.selected.mermaid_code
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


def test_fused_node_id_remap_reaches_conditional_label_repair_without_structural_drift():
    observation = namespaced_conditional_label_observation()
    original_observation = observation.model_dump(mode="json")
    runtime = FlowRuntime()

    result = run_repair(
        observation,
        runtime,
        fixture_engine_type=VlmFixtureEngine,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1-repair-1"
    assert result.selected.generation_engine == "deterministic_fusion"
    [baseline] = result.alternatives
    assert baseline.candidate_id == "candidate-1"
    assert baseline.generation_engine == "deterministic_fusion"
    assert len(runtime.calls) == 2

    geometry_ids = ["geometry-node-001", "geometry-node-002"]
    assert [node["id"] for node in result.selected.typed_ir["nodes"]] == geometry_ids
    assert result.selected.typed_ir["edges"][0]["source"] == geometry_ids[0]
    assert result.selected.typed_ir["edges"][0]["target"] == geometry_ids[1]
    assert result.selected.typed_ir["groups"][0]["member_ids"] == geometry_ids
    assert [element.id for element in result.selected.scene_ir.elements] == geometry_ids
    assert result.selected.scene_ir.relations[0].source_id == geometry_ids[0]
    assert result.selected.scene_ir.relations[0].target_id == geometry_ids[1]
    assert result.selected.scene_ir.groups[0].member_ids == geometry_ids

    assert [mapping.source_id for mapping in result.selected.node_id_mappings] == ["A", "B"]
    assert [mapping.fused_id for mapping in result.selected.node_id_mappings] == geometry_ids
    assert all(
        mapping.match_method == "unique_iou"
        and mapping.iou == 1
        and mapping.authority_source == "geometry"
        for mapping in result.selected.node_id_mappings
    )
    assert result.selected.node_id_mappings == baseline.node_id_mappings
    assert result.selected._has_valid_node_id_mapping_seal()
    assert baseline._has_valid_node_id_mapping_seal()

    baseline_edge = baseline.typed_ir["edges"][0]
    repaired_edge = result.selected.typed_ir["edges"][0]
    assert baseline_edge.get("label") is None
    assert repaired_edge["label"] == "Yes"
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
    assert result.selected.typed_ir["nodes"] == baseline.typed_ir["nodes"]
    assert result.selected.typed_ir["groups"] == baseline.typed_ir["groups"]
    assert result.selected.scene_ir.model_dump(mode="json") == baseline.scene_ir.model_dump(
        mode="json"
    )
    assert result.selected.aggregate_score > baseline.aggregate_score
    assert result.selected.scores["ocr_recall"] > baseline.scores["ocr_recall"]
    for metric in ("edge_agreement", "arrow_agreement", "path_consistency"):
        assert result.selected.scores[metric] == baseline.scores[metric] == 1

    assert observation.model_dump(mode="json") == original_observation
    assert [element.id for element in observation.scene_ir.elements] == ["A", "B"]
    assert [node["id"] for node in observation.typed_candidates[0].ir["nodes"]] == [
        "A",
        "B",
    ]


def test_fused_node_id_remap_refuses_self_declared_source_evidence() -> None:
    observation = namespaced_conditional_label_observation()
    observation.evidence = [
        item for item in observation.evidence if item.id not in {"vlm-node-a", "vlm-node-b"}
    ]

    result = run_repair(
        observation,
        fixture_engine_type=VlmFixtureEngine,
    )

    assert result.selected is not None
    assert result.selected.node_id_mappings == []
    assert {node["id"] for node in result.selected.typed_ir["nodes"]} == {"A", "B"}
    assert {item.id for item in result.evidence}.isdisjoint({"vlm-node-a", "vlm-node-b"})
    assert any("certification failed" in warning for warning in result.selected.warnings)


def test_fused_mapping_budget_filters_non_predicted_typed_candidates_before_slicing() -> None:
    observation = namespaced_conditional_label_observation()
    observation.typed_candidates.insert(
        0,
        TypedIRCandidate(
            diagram_type="architecture",
            confidence=1,
            ir={"services": [{"id": "ignored", "label": "Ignored"}]},
        ),
    )

    result = run_repair(
        observation,
        fixture_engine_type=VlmFixtureEngine,
    )

    assert result.selected is not None
    assert result.selected.diagram_type == "flowchart"
    assert result.selected.generation_engine == "deterministic_fusion"
    assert result.selected.node_id_mappings


@pytest.mark.parametrize("mutation", ["nested_label", "evidence_overflow", "set", "nan"])
def test_pipeline_revalidates_mutated_typed_candidates_without_losing_valid_siblings(
    mutation,
) -> None:
    observation = namespaced_conditional_label_observation()
    valid = observation.typed_candidates[0].model_copy(deep=True)
    invalid = observation.typed_candidates[0]
    if mutation == "nested_label":
        invalid.ir["nodes"][0]["label"] = {"parent_id": "A"}
    elif mutation == "evidence_overflow":
        invalid.ir["nodes"][0]["evidence_ids"] = [
            f"evidence-{index}" for index in range(MAX_EVIDENCE_REFS + 1)
        ]
    elif mutation == "set":
        invalid.ir["mutated"] = {"unordered"}
    else:
        invalid.ir["mutated"] = float("nan")
    observation.typed_candidates = [invalid, valid]
    config = MermaidConfig(
        candidate_count=2,
        enable_fusion=False,
        enable_generic_scene_ir=False,
        enable_direct_mermaid=False,
    )

    result = ReconstructionPipeline(
        config,
        [VlmFixtureEngine(observation)],
        CandidateValidator(FlowRuntime(), config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.typed_ir["nodes"][0]["label"] == "Approve?"
    assert any("invalid typed candidate was isolated" in item.message for item in result.failures)


def test_pipeline_isolates_mutated_initial_evidence() -> None:
    observation = namespaced_conditional_label_observation()
    initial = VisualEvidence(
        id="invalid-initial",
        kind="ocr_token",
        text="Invalid",
        bbox=(0, 0, 5, 5),
        source_block_ids=["source"],
    )
    initial.kind = "bogus"
    config = MermaidConfig(
        candidate_count=1,
        enable_fusion=False,
        enable_generic_scene_ir=False,
    )

    result = ReconstructionPipeline(
        config,
        [VlmFixtureEngine(observation)],
        CandidateValidator(FlowRuntime(), config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
        evidence=[initial],
    )

    assert result.selected is not None
    assert "invalid-initial" not in {item.id for item in result.evidence}
    assert any("invalid initial evidence was isolated" in item.message for item in result.failures)


@pytest.mark.parametrize(
    ("collection", "message"),
    [
        ("typed_candidates", "invalid typed candidate was isolated"),
        ("direct_candidates", "invalid direct candidate was isolated"),
        ("evidence", "invalid evidence was isolated"),
    ],
)
def test_pipeline_isolates_non_model_engine_components(collection, message) -> None:
    observation = namespaced_conditional_label_observation()
    getattr(observation, collection).append({"malformed": True})
    config = MermaidConfig(
        candidate_count=1,
        enable_fusion=False,
        enable_generic_scene_ir=False,
    )

    result = ReconstructionPipeline(
        config,
        [VlmFixtureEngine(observation)],
        CandidateValidator(FlowRuntime(), config.security_profile),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert any(message in item.message for item in result.failures)


def test_fused_reverse_vlm_relation_marks_remapped_conflict_and_refuses_repair():
    observation = namespaced_conditional_label_observation(scene_reversed=True)
    original_observation = observation.model_dump(mode="json")
    repair = RecordingRepair()

    result = run_repair(
        observation,
        fixture_engine_type=VlmFixtureEngine,
        repair_engine=repair,
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.selected.generation_engine == "deterministic_fusion"
    assert result.selected.typed_ir["edges"][0].get("label") is None
    assert result.selected.repair_history == []
    assert repair.conflicted_connector_pairs == [
        {frozenset({"geometry-node-001", "geometry-node-002"})}
    ]
    assert observation.model_dump(mode="json") == original_observation
    assert observation.scene_ir.relations[0].source_id == "B"
    assert observation.scene_ir.relations[0].target_id == "A"


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


def test_semantic_repair_cannot_change_a_provenance_mapped_node_set():
    result = run_repair(
        namespaced_conditional_label_observation(),
        fixture_engine_type=VlmFixtureEngine,
        repair_engine=NodeSetChangingRepair(),
    )

    assert result.selected is not None
    assert [node["id"] for node in result.selected.typed_ir["nodes"]] == [
        "geometry-node-001",
        "geometry-node-002",
    ]
    assert [mapping.fused_id for mapping in result.selected.node_id_mappings] == [
        "geometry-node-001",
        "geometry-node-002",
    ]
    assert not result.selected.repair_history[-1].accepted
    assert any(
        "cannot change a provenance-mapped node set" in warning
        for warning in result.selected.warnings
    )
