from __future__ import annotations

import json

import pytest
from PIL import Image

from marker_mermaid.config import MermaidConfig
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.markdown import standalone_document_markdown
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
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
        ocr_texts=["Start End"],
    )
    relative = SidecarStore(tmp_path).write(result)
    bundle = tmp_path / relative
    assert (bundle / "final.mmd").is_file()
    assert (bundle / "final.svg").is_file()
    assert (bundle / "provenance.json").is_file()
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["schema_version"] == "mmx-sidecar-0.3"
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
