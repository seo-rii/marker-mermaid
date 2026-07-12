from __future__ import annotations

import json

import pytest
from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.geometry import ContourObservation, GeometryEngine, GeometryObservation
from marker_mermaid.markdown import standalone_document_markdown
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    DirectMermaidCandidate,
    EngineObservation,
    ReconstructionResult,
    SceneElement,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.sidecars import SidecarStore
from marker_mermaid.validation import CandidateValidator


def observation():
    return EngineObservation(
        prediction=DiagramTypePrediction(
            candidates=["flowchart", "architecture"], scores=[0.9, 0.1]
        ),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="A",
                    role="process",
                    text="Start",
                    bbox=(0, 0, 10, 10),
                    evidence_ids=["ocr-1"],
                ),
                SceneElement(
                    id="B", role="process", text="End", bbox=(20, 0, 30, 10), evidence_ids=["vlm-1"]
                ),
            ],
            relations=[
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type="arrow",
                    evidence_ids=["vlm-1"],
                )
            ],
            reading_direction="LR",
            diagram_type_candidates=["flowchart"],
        ),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "title": "Process",
                    "nodes": [{"id": "A", "label": "Start"}, {"id": "B", "label": "End"}],
                    "edges": [{"source": "A", "target": "B"}],
                },
            )
        ],
        evidence=[
            VisualEvidence(id="ocr-1", kind="ocr_token", text="Start", bbox=(0, 0, 10, 10)),
            VisualEvidence(id="vlm-1", kind="vlm_observation", score=0.9),
        ],
    )


def test_pipeline_selects_valid_candidate_and_respects_budget(fake_runtime):
    config = MermaidConfig(candidate_count=1)
    pipeline = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    )
    result = pipeline.reconstruct(
        "page-1-figure-1",
        "source.png",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )
    assert result.selected is not None
    assert result.publish
    assert len(fake_runtime.calls) == 1
    assert result.selected.generation_method == "typed_ir"
    assert result.selected.scores["edge_agreement"] == 1
    assert result.selected.scores["arrow_agreement"] == 1
    assert result.selected.scores["path_consistency"] == 1
    assert "layout_similarity" not in result.selected.scores


def test_candidate_budget_is_shared_fairly_across_engines(fake_runtime):
    class DirectEngine:
        name = "direct"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.8]),
                direct_candidates=[
                    DirectMermaidCandidate(
                        diagram_type="flowchart",
                        code='flowchart LR\n    X["One"] --> Y["Two"]\n',
                    )
                ],
            )

    config = MermaidConfig(candidate_count=2)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation()), DirectEngine()],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    methods = {
        result.selected.generation_method,
        *(item.generation_method for item in result.alternatives),
    }
    assert methods == {"typed_ir", "direct_mermaid"}
    assert result.selected.generation_engine == "deterministic_fusion"
    assert len(fake_runtime.calls) == 2


def test_geometry_evidence_is_available_to_later_engines(fake_runtime):
    class EvidenceEngine:
        name = "evidence"

        def observe(self, context):
            return EngineObservation(
                prediction=DiagramTypePrediction(candidates=["unknown"], scores=[1.0]),
                evidence=[
                    VisualEvidence(id="contour-1", kind="contour", bbox=(0, 0, 5, 5)),
                    VisualEvidence(
                        id="vector-text-1",
                        kind="vector_text",
                        bbox=(10, 10, 30, 20),
                        text="API",
                    ),
                ],
            )

    class CapturingEngine:
        name = "capturing"

        def observe(self, context):
            assert [item.id for item in context.evidence] == ["contour-1", "vector-text-1"]
            assert "vector_overlay" in context.views
            return observation()

    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [EvidenceEngine(), CapturingEngine()],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.selected is not None


def test_unlabeled_geometry_only_candidate_requires_review(fake_runtime):
    geometry = GeometryObservation(
        canvas_size=(100, 50),
        contours=(ContourObservation(bbox=(10, 10, 40, 40), confidence=0.9),),
    )
    config = MermaidConfig(candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [GeometryEngine(detector=lambda image: geometry)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.selected is not None
    assert result.selected.generation_engine == "geometry"
    assert result.selected.aggregate_score is None
    assert not result.publish
    assert result.status == "review_required"


def test_engine_failure_is_isolated(fake_runtime):
    class Broken:
        name = "broken"

        def observe(self, context):
            raise RuntimeError("offline")

    config = MermaidConfig()
    pipeline = ReconstructionPipeline(
        config,
        [Broken(), JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    )
    result = pipeline.reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.selected is not None
    assert result.failures[0].engine == "broken"


def test_validator_failure_is_isolated():
    class ExplodingRuntime:
        def validate_and_render(self, code, timeout_seconds):
            raise RuntimeError("browser unavailable")

        def close(self):
            pass

    config = MermaidConfig()
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(ExplodingRuntime(), config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (100, 50), "white"))
    assert result.status == "failed"
    assert result.failures[0].stage == "validation"


def test_sidecar_tree_and_markdown(tmp_path, fake_runtime):
    config = MermaidConfig(candidate_count=2)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "page-1-figure-1",
        "source.png",
        Image.new("RGB", (100, 60), "white"),
        source_block_ids=["/page/1/Figure/1"],
        source_kind="panel",
        page_ids=[1],
        anchor_block_id="/page/1/Figure/1",
        source_mapping={
            "source": {"source_id": "page-1-figure-1"},
            "assembly": {"canvas_size": [100, 60]},
        },
        ocr_texts=["Start End"],
    )
    relative = SidecarStore(tmp_path).write(result)
    bundle = tmp_path / relative
    assert (bundle / "final.mmd").is_file()
    assert (bundle / "final.svg").is_file()
    assert (bundle / "provenance.json").is_file()
    assert (bundle / "source-map.json").is_file()
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["schema_version"] == "mmx-sidecar-0.3"
    assert manifest["source_kind"] == "panel"
    assert manifest["source_block_ids"] == ["/page/1/Figure/1"]
    assert manifest["page_ids"] == [1]
    assert manifest["anchor_block_id"] == "/page/1/Figure/1"
    assert json.loads((bundle / "source-map.json").read_text())["assembly"]["canvas_size"] == [
        100,
        60,
    ]
    markdown = standalone_document_markdown(result, image_path="images/source.png")
    assert markdown.index("images/source.png") < markdown.index("```mermaid")


def test_sidecar_rejects_empty_traversal_component(tmp_path):
    result = ReconstructionResult(
        source_id="../",
        source_image_name="source.png",
        status="failed",
    )
    with pytest.raises(ValueError, match="unsafe sidecar"):
        SidecarStore(tmp_path).write(result)


def test_sidecar_write_flags_are_honored(tmp_path, fake_runtime):
    config = MermaidConfig(candidate_count=2)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("minimal", "source.png", Image.new("RGB", (100, 60), "white"))
    relative = SidecarStore(
        tmp_path,
        write_ir=False,
        write_svg=False,
        write_png=False,
        write_alternatives=False,
        write_provenance=False,
    ).write(result)
    bundle = tmp_path / relative
    assert (bundle / "final.mmd").is_file()
    assert not (bundle / "final.svg").exists()
    assert not (bundle / "scene-ir.json").exists()
    assert not (bundle / "provenance.json").exists()
    assert not (bundle / "alternatives").exists()
