from __future__ import annotations

from copy import deepcopy

import pytest
from PIL import Image

import marker_mermaid.pipeline as pipeline_module
from marker_mermaid.config import MermaidConfig
from marker_mermaid.discovery import FragmentMergeProposal, SourceFragment
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.marker_discovery import merge_proposal_to_source
from marker_mermaid.models import DiagramTypePrediction, EngineObservation, TypedIRCandidate
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.serializers import serialize_typed_ir_result
from marker_mermaid.validation import CandidateValidator


def _flowchart_observation() -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "nodes": [{"id": "A", "label": "Before"}],
                    "edges": [],
                },
            )
        ],
    )


class _NoopRepair:
    name = "noop"

    def repair(self, _context, _candidate):
        return None


def test_noop_repair_does_not_duplicate_the_selected_candidate(fake_runtime) -> None:
    config = MermaidConfig(
        candidate_count=1,
        enable_generic_scene_ir=False,
        enable_direct_mermaid=False,
        max_repair_iterations=1,
    )
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(_flowchart_observation())],
        CandidateValidator(fake_runtime, config.security_profile),
        repair_engine=_NoopRepair(),
    ).reconstruct(
        "source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.candidate_id == "candidate-1"
    assert result.alternatives == []


class _RelabelRepair:
    name = "relabel"

    def __init__(self) -> None:
        self.image_size: tuple[int, int] | None = None
        self.original_view_size: tuple[int, int] | None = None

    def repair(self, context, candidate):
        self.image_size = context.image.size
        self.original_view_size = context.views["original"].size
        assert candidate.typed_ir is not None
        repaired_ir = deepcopy(candidate.typed_ir)
        repaired_ir["nodes"][0]["label"] = "After"
        code = serialize_typed_ir_result(candidate.diagram_type, repaired_ir).code
        return RepairProposal(
            code=code,
            operation="relabel",
            typed_ir=repaired_ir,
        )


class _RecordingPipeline(ReconstructionPipeline):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.evaluation_image_sizes: list[tuple[int, int]] = []

    def _evaluate_candidate(self, **kwargs):
        self.evaluation_image_sizes.append(kwargs["image"].size)
        return super()._evaluate_candidate(**kwargs)


def test_repair_evaluation_keeps_source_resolution_while_engine_uses_bounded_view(
    fake_runtime,
) -> None:
    source_size = (3_000, 100)
    config = MermaidConfig(
        candidate_count=1,
        enable_generic_scene_ir=False,
        enable_direct_mermaid=False,
        max_repair_iterations=1,
    )
    repair = _RelabelRepair()
    pipeline = _RecordingPipeline(
        config,
        [JsonFixtureEngine(_flowchart_observation())],
        CandidateValidator(fake_runtime, config.security_profile),
        repair_engine=repair,
    )

    pipeline.reconstruct(
        "source",
        "source.png",
        Image.new("RGB", source_size, "white"),
    )

    assert pipeline.evaluation_image_sizes == [source_size, source_size]
    assert repair.image_size == repair.original_view_size
    assert repair.image_size is not None
    assert max(repair.image_size) <= config.max_image_dimension
    assert repair.image_size != source_size


@pytest.mark.parametrize(
    ("diagram_type", "runtime_type", "typed_ir"),
    [
        (
            "packet",
            "packet",
            {
                "fields": [
                    {
                        "id": "field-a",
                        "start": 0,
                        "end": 3,
                        "label": "A",
                        "bbox": ["invalid", 0, 10, 10],
                    }
                ]
            },
        ),
        (
            "pie",
            "pie",
            {"slices": [{"label": "A", "value": 1, "bbox": [None, 0, 10, 10]}]},
        ),
        (
            "quadrant",
            "quadrant",
            {
                "x_axis": {"low": "Left", "high": "Right", "bbox": ["invalid", 0, 10, 10]},
                "y_axis": {"low": "Low", "high": "High", "bbox": [0, 0, 10, 10]},
                "points": [
                    {"label": "P", "x": 0.5, "y": 0.5, "bbox": [10, 10, 20, 20]}
                ],
            },
        ),
        (
            "xychart",
            "xychart",
            {
                "x_axis": {"categories": ["A"], "bbox": [None, 0, 10, 10]},
                "y_axis": {"min": 0, "max": 10, "bbox": [0, 0, 10, 10]},
                "series": [{"kind": "bar", "values": [5], "bbox": [10, 10, 20, 20]}],
            },
        ),
    ],
)
def test_engine_supplied_invalid_chart_bbox_fails_closed_during_evaluation(
    fake_runtime,
    diagram_type: str,
    runtime_type: str,
    typed_ir: dict,
) -> None:
    config = MermaidConfig()
    pipeline = ReconstructionPipeline(
        config,
        [],
        CandidateValidator(fake_runtime, config.security_profile),
    )

    evaluation = pipeline._evaluate_candidate(
        code=f"{diagram_type}\n",
        runtime=RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type=runtime_type,
            svg='<svg xmlns="http://www.w3.org/2000/svg"/>',
        ),
        syntax_valid=True,
        render_valid=True,
        semantic_diagram_type=diagram_type,
        gate_diagram_type=diagram_type,
        method="typed_ir",
        typed_ir=typed_ir,
        source_scene=None,
        evidence=[],
        approved_user_edit_evidence_ids=frozenset(),
        references=pipeline_module._reference_text_sets([], []),
        type_fitness=1.0,
        image=Image.new("RGB", (100, 100), "white"),
        quadrant_metadata_role_limited=False,
    )

    assert evaluation.aggregate_score is None
    assert any("association" in warning for warning in evaluation.warnings)


def test_packet_nan_bbox_cannot_satisfy_spatial_association(fake_runtime) -> None:
    config = MermaidConfig()
    pipeline = ReconstructionPipeline(
        config,
        [],
        CandidateValidator(fake_runtime, config.security_profile),
    )

    evaluation = pipeline._evaluate_candidate(
        code='packet-beta\n0-3: "A"\n',
        runtime=RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="packet",
            svg='<svg xmlns="http://www.w3.org/2000/svg"/>',
        ),
        syntax_valid=True,
        render_valid=True,
        semantic_diagram_type="packet",
        gate_diagram_type="packet",
        method="typed_ir",
        typed_ir={
            "fields": [
                {
                    "id": "field-a",
                    "start": 0,
                    "end": 3,
                    "label": "A",
                    "bbox": ["nan", 0, 10, 10],
                }
            ]
        },
        source_scene=None,
        evidence=[],
        approved_user_edit_evidence_ids=frozenset(),
        references=pipeline_module._reference_text_sets([], []),
        type_fitness=1.0,
        image=Image.new("RGB", (100, 100), "white"),
        quadrant_metadata_role_limited=False,
    )

    assert evaluation.aggregate_score is None
    assert "numeric_consistency" not in evaluation.scores
    assert any("association" in warning for warning in evaluation.warnings)


def test_same_page_merge_rejects_fragments_with_different_pixel_scales() -> None:
    proposal = FragmentMergeProposal(
        block_ids=("left", "right"),
        bbox=(0, 0, 210, 100),
        pages=(0, 0),
        score=0.9,
        signals=["shared_caption"],
    )
    fragments = {
        "left": SourceFragment(
            fragment_id="left-fragment",
            page_id=0,
            source_block_ids=["left"],
            page_bbox=(0, 0, 100, 100),
            crop_bbox=(0, 0, 100, 100),
            image_size=(100, 100),
        ),
        "right": SourceFragment(
            fragment_id="right-fragment",
            page_id=0,
            source_block_ids=["right"],
            page_bbox=(110, 0, 210, 100),
            crop_bbox=(0, 0, 200, 100),
            image_size=(200, 100),
        ),
    }

    with pytest.raises(ValueError, match="incompatible pixel scales"):
        merge_proposal_to_source(proposal, fragments=fragments)
