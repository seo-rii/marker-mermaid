from __future__ import annotations

from functools import partial

import pytest
from PIL import Image

import marker_mermaid.pipeline as pipeline_module
import marker_mermaid.quality as quality_module
from marker_mermaid.config import MermaidConfig, PublishPolicy
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.quality import LAYOUT_PAIR_BUDGET_WARNING, relative_layout_similarity
from marker_mermaid.validation import CandidateValidator


def _scene() -> DiagramSceneIR:
    return DiagramSceneIR(
        elements=[
            SceneElement(
                id=node_id,
                role="component",
                text=label,
                bbox=(position, position, position + 10, position + 10),
                evidence_ids=[f"ocr-{node_id}"],
            )
            for node_id, label, position in [
                ("a", "Alpha", 5), ("b", "Beta", 45), ("c", "Gamma", 85)
            ]
        ],
        canvas_size=(100, 100),
    )


def test_layout_exact_pair_budget_preserves_complete_score():
    scene = _scene()

    result = relative_layout_similarity(scene, scene, max_pairs=3)

    assert result.available
    assert result.value == 1
    assert result.warning is None


def test_layout_one_pair_over_budget_returns_no_partial_score(monkeypatch):
    scene = _scene()

    def unexpected_iteration(*args, **kwargs):
        pytest.fail("pair enumeration must not start when the budget is insufficient")

    monkeypatch.setattr(quality_module, "combinations", unexpected_iteration)

    result = relative_layout_similarity(scene, scene, max_pairs=2)

    assert not result.available
    assert result.value is None
    assert result.warning == LAYOUT_PAIR_BUDGET_WARNING


@pytest.mark.parametrize("max_pairs", [0, -1, 1.5, True])
def test_layout_pair_budget_requires_a_positive_integer(max_pairs):
    with pytest.raises(ValueError, match="max_pairs must be a positive integer"):
        relative_layout_similarity(_scene(), _scene(), max_pairs=max_pairs)


def test_layout_pair_budget_keeps_collapsed_positions_from_hiding_overflow():
    source = _scene()
    collapsed = source.model_copy(
        update={
            "elements": [
                element.model_copy(update={"bbox": (0, 0, 0, 0)}) for element in source.elements
            ]
        }
    )

    result = relative_layout_similarity(source, collapsed, max_pairs=2)

    assert not result.available
    assert result.warning == LAYOUT_PAIR_BUDGET_WARNING


def test_pipeline_requires_review_on_layout_budget_and_clears_warning_on_reevaluation(monkeypatch):
    class Runtime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="wardley",
                svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
            )

        def close(self):
            pass

    scene = _scene()
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["wardley"], scores=[1]),
        scene_ir=scene,
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="wardley",
                ir={
                    "components": [
                        {
                            "id": element.id,
                            "label": element.text,
                            "x": (element.bbox[0] + 5) / 100,
                            "y": 1 - (element.bbox[1] + 5) / 100,
                            "evidence_ids": element.evidence_ids,
                        }
                        for element in scene.elements
                    ]
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id=f"ocr-{element.id}",
                kind="ocr_token",
                text=element.text,
                bbox=element.bbox,
            )
            for element in scene.elements
        ],
    )
    config = MermaidConfig(
        publish_policy=PublishPolicy.BEST_EFFORT_VALIDATED,
        candidate_count=1,
        enable_fusion=False,
        enable_generic_scene_ir=False,
        enable_direct_mermaid=False,
        max_repair_iterations=0,
    )
    pipeline = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(Runtime(), config.security_profile),
    )
    monkeypatch.setattr(
        pipeline_module,
        "relative_layout_similarity",
        partial(relative_layout_similarity, max_pairs=3),
    )
    image = Image.new("RGB", (100, 100), "white")

    complete = pipeline.reconstruct("complete", "source.png", image, ocr_texts=["Alpha Beta Gamma"])
    assert complete.selected is not None
    assert complete.selected.scores["layout_similarity"] == 1
    assert complete.selected.aggregate_score is not None
    assert complete.publish

    monkeypatch.setattr(
        pipeline_module,
        "relative_layout_similarity",
        partial(relative_layout_similarity, max_pairs=2),
    )
    exhausted = pipeline.reconstruct(
        "exhausted", "source.png", image, ocr_texts=["Alpha Beta Gamma"]
    )
    assert exhausted.selected is not None
    assert "layout_similarity" not in exhausted.selected.scores
    assert exhausted.selected.aggregate_score is None
    assert LAYOUT_PAIR_BUDGET_WARNING in exhausted.selected.warnings
    assert not exhausted.publish
    assert exhausted.review_required
    assert exhausted.grade == "U"

    assert pipeline_module._without_evaluation_warnings(  # noqa: SLF001 - reevaluation contract
        [LAYOUT_PAIR_BUDGET_WARNING, "keep unrelated warning"]
    ) == ["keep unrelated warning"]
