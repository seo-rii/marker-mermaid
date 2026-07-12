from __future__ import annotations

from PIL import Image

from marker_mermaid.config import MermaidConfig, Mode
from marker_mermaid.models import (
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.validation import CandidateValidator


class _DirectEngine:
    name = "direct"

    def __init__(self, code: str):
        self.code = code

    def observe(self, context):
        return EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
            direct_candidates=[DirectMermaidCandidate(diagram_type="flowchart", code=self.code)],
        )


def test_pipeline_applies_complete_idempotent_source_repair_before_validation(fake_runtime):
    source = '\ufeff```mermaid\nflowchart LR\n    bad-id["Start]\n```'
    config = MermaidConfig(mode=Mode.MAXIMAL, candidate_count=1)

    result = ReconstructionPipeline(
        config,
        [_DirectEngine(source)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (20, 20), "white"))

    assert result.selected is not None
    assert result.selected.mermaid_code.startswith("flowchart LR\n    accTitle:")
    assert 'bad_id["Start"]' in result.selected.mermaid_code
    assert result.selected.raw_mermaid == source
    assert len(fake_runtime.calls) == 2
    assert fake_runtime.calls[-1] == result.selected.mermaid_code
    assert [event.operation for event in result.selected.repair_history] == [
        "remove_bom",
        "unwrap_markdown_fence",
        "close_unambiguous_label_quote",
        "normalize_identifier",
        "add_terminal_newline",
        "augment_accessibility",
    ]
    assert all(event.accepted for event in result.selected.repair_history)


def test_pipeline_does_not_use_partial_budget_exhausted_repair(fake_runtime):
    source = '\ufeff```mermaid\nflowchart LR\n    A["Start"]\n```'
    config = MermaidConfig(mode=Mode.MAXIMAL, candidate_count=1)
    pipeline = ReconstructionPipeline(
        config,
        [_DirectEngine(source)],
        CandidateValidator(fake_runtime, config.security_profile),
    )
    pipeline.source_repair.event_budget = 1

    result = pipeline.reconstruct("source", "source.png", Image.new("RGB", (20, 20), "white"))

    assert result.selected is not None
    assert result.selected.mermaid_code == source
    assert all(not event.accepted for event in result.selected.repair_history)
    assert any("discarded" in warning for warning in result.selected.warnings)
