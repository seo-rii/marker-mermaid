from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

import marker_mermaid.models as models_module
from marker_mermaid.config import MermaidConfig, PublishPolicy, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.markdown import reconstruction_markdown, standalone_document_markdown
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    MermaidCandidate,
    ReconstructionResult,
    SceneElement,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
    _candidate_quality_sha256,
)
from marker_mermaid.pipeline import ReconstructionPipeline, certify_publication_result
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.sidecars import SidecarStore
from marker_mermaid.validation import CandidateValidator


def _observation() -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="A",
                    role="process",
                    text="Start",
                    bbox=(0, 0, 10, 10),
                    evidence_ids=["ocr-start"],
                ),
                SceneElement(
                    id="B",
                    role="process",
                    text="End",
                    bbox=(20, 0, 30, 10),
                    evidence_ids=["vlm-end"],
                ),
            ],
            relations=[
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type="arrow",
                    arrow_at_end=True,
                    evidence_ids=["vlm-end"],
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
                    "nodes": [
                        {"id": "A", "label": "Start"},
                        {"id": "B", "label": "End"},
                    ],
                    "edges": [{"source": "A", "target": "B"}],
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="ocr-start",
                kind="ocr_token",
                text="Start",
                bbox=(0, 0, 10, 10),
            ),
            VisualEvidence(id="vlm-end", kind="vlm_observation", score=0.9),
        ],
    )


def _pipeline_result(runtime, config: MermaidConfig) -> ReconstructionResult:
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(_observation())],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "_page_0_Figure_1",
        "_page_0_Figure_1.jpeg",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )
    return result


def _validated_result(fake_runtime) -> ReconstructionResult:
    config = MermaidConfig(candidate_count=1, type_candidate_count=1)
    result = _pipeline_result(fake_runtime, config)
    assert result.publish
    assert result.selected is not None
    assert result.selected.syntax_valid
    assert result.selected.render_valid
    assert result.selected.svg
    return result


class _PngRuntime:
    def __init__(self, color: str):
        payload = BytesIO()
        Image.new("RGB", (2, 2), color).save(payload, format="PNG")
        self.png = payload.getvalue()

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        return RuntimeResult(
            True,
            True,
            diagram_type="flowchart-v2",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text>x</text></svg>'
            ),
            png=self.png,
        )

    def close(self) -> None:
        pass


class _MissingTypeRuntime:
    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        return RuntimeResult(
            True,
            True,
            diagram_type=None,
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>x</text></svg>'
            ),
        )

    def close(self) -> None:
        pass


class _MisleadingStr(str):
    def __new__(cls, visible: str, encoded: bytes):
        instance = super().__new__(cls, visible)
        instance.encoded = encoded
        return instance

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        return self.encoded


def test_unvalidated_publish_claim_cannot_emit_mermaid() -> None:
    candidate = MermaidCandidate(
        candidate_id="unvalidated",
        generation_method="direct_mermaid",
        diagram_type="flowchart",
        mermaid_code="this is not Mermaid",
        syntax_valid=False,
        render_valid=False,
    )

    try:
        result = ReconstructionResult(
            source_id="source",
            source_image_name="source.png",
            selected=candidate,
            grade="C",
            publish=True,
            review_required=False,
            status="success",
        )
    except ValidationError:
        # Rejecting the inconsistent state at the model boundary is also fail-closed.
        return

    assert reconstruction_markdown(result) == ""
    markdown = standalone_document_markdown(result, image_path="images/source.png")
    assert markdown == "![원본 다이어그램](images/source.png)\n"


def test_mutating_validated_code_invalidates_publication(fake_runtime) -> None:
    result = _validated_result(fake_runtime)
    original_code = result.selected.mermaid_code
    assert original_code
    assert reconstruction_markdown(result).count("```mermaid") == 1

    result.selected.mermaid_code = original_code + "\nBROKEN"

    assert reconstruction_markdown(result) == ""
    markdown = standalone_document_markdown(result, image_path="images/source.jpeg")
    assert "![원본 다이어그램](images/source.jpeg)" in markdown
    assert "```mermaid" not in markdown


def test_mutated_aggregate_evidence_provenance_invalidates_publication(
    monkeypatch,
    fake_runtime,
) -> None:
    result = _validated_result(fake_runtime)
    result.evidence = [
        VisualEvidence(
            id="first",
            kind="contour",
            source_block_ids=["source", "a"],
        ),
        VisualEvidence(
            id="second",
            kind="line_segment",
            source_block_ids=["source", "b"],
        ),
    ]
    monkeypatch.setattr(models_module, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 4)

    assert result.has_authorized_publication()

    monkeypatch.setattr(models_module, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 3)

    def forbidden_result_copy(*_args, **_kwargs):
        raise AssertionError("certification must preflight evidence before copying the result")

    monkeypatch.setattr(ReconstructionResult, "model_copy", forbidden_result_copy)

    assert not certify_publication_result(
        result,
        MermaidConfig(candidate_count=1, type_candidate_count=1),
    )
    assert not result.has_authorized_publication()
    assert reconstruction_markdown(result) == ""
    markdown = standalone_document_markdown(result, image_path="images/source.jpeg")
    assert markdown == "![원본 다이어그램](images/source.jpeg)\n"


def test_removing_validated_svg_invalidates_publication(fake_runtime) -> None:
    result = _validated_result(fake_runtime)

    result.selected.svg = None

    assert reconstruction_markdown(result) == ""


def test_mutating_validated_svg_invalidates_publication(fake_runtime) -> None:
    result = _validated_result(fake_runtime)

    result.selected.svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text>tampered</text></svg>'
    )

    assert reconstruction_markdown(result) == ""


def test_mutating_quality_metadata_invalidates_publication(fake_runtime, tmp_path) -> None:
    result = _validated_result(fake_runtime)
    assert result.selected is not None
    result.selected.aggregate_score = 0.99

    assert not result.has_authorized_publication()
    assert reconstruction_markdown(result, show_score=True) == ""
    with pytest.raises(ValueError, match="publication authorization"):
        SidecarStore(tmp_path).write(result)


@pytest.mark.parametrize("field", ["mermaid_code", "svg"])
def test_string_subclass_cannot_forge_validated_artifact_bytes(
    fake_runtime, tmp_path, field
) -> None:
    result = _validated_result(fake_runtime)
    assert result.selected is not None
    original = getattr(result.selected, field)
    assert isinstance(original, str)
    visible = (
        'flowchart LR\nA-->B; click A "#bad"'
        if field == "mermaid_code"
        else '<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>'
    )
    setattr(result.selected, field, _MisleadingStr(visible, original.encode("utf-8")))

    assert not result.selected.has_validated_publication_artifacts()
    assert reconstruction_markdown(result) == ""
    with pytest.raises(ValueError, match="publication authorization"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams" / "page_0_figure_1").exists()


def test_valid_pipeline_result_is_emitted_exactly_once(fake_runtime) -> None:
    result = _validated_result(fake_runtime)

    markdown = standalone_document_markdown(result, image_path="images/source.jpeg")

    assert markdown.startswith("![원본 다이어그램](images/source.jpeg)\n\n")
    assert markdown.count("```mermaid") == 1
    assert markdown.count(result.selected.mermaid_code.rstrip()) == 1


def test_missing_runtime_type_downgrades_automatic_publication(tmp_path) -> None:
    config = MermaidConfig(candidate_count=1, type_candidate_count=1)

    result = _pipeline_result(_MissingTypeRuntime(), config)

    assert result.selected is not None
    assert result.selected.syntax_valid
    assert result.selected.render_valid
    assert result.selected.validation_receipt is None
    assert not result.publish
    assert result.review_required
    assert result.status == "review_required"
    assert result.publication_receipt is None
    assert "automatic publication authorization failed" in result.selected.warnings[-1]
    assert reconstruction_markdown(result) == ""
    relative = SidecarStore(tmp_path).write(result)
    assert (tmp_path / relative / "final.mmd").is_file()


def test_selection_prefers_a_certified_publishable_candidate(fake_runtime) -> None:
    sealed_result = _validated_result(fake_runtime)
    assert sealed_result.selected is not None
    sealed = sealed_result.selected
    unsealed = MermaidCandidate.model_validate(sealed.model_dump(mode="python"))
    unsealed.candidate_id = "higher-score-without-private-seal"
    unsealed.aggregate_score = 1.0
    assert not unsealed.has_validated_publication_artifacts()
    config = MermaidConfig(candidate_count=2, type_candidate_count=1)
    pipeline = ReconstructionPipeline(
        config,
        [],
        CandidateValidator(fake_runtime, config.security_profile),
    )

    selected = pipeline._select([unsealed, sealed])

    assert selected is sealed


def test_malformed_engine_warning_is_sink_safe(fake_runtime, tmp_path) -> None:
    observation = _observation()
    observation.warnings = ["bad\ud800warning"]
    config = MermaidConfig(candidate_count=1, type_candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "_page_0_Figure_1",
        "_page_0_Figure_1.jpeg",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )

    assert result.publish
    assert result.selected is not None
    assert "bad\\ud800warning" in result.selected.warnings
    relative = SidecarStore(tmp_path).write(result)
    assert (tmp_path / relative / "manifest.json").is_file()


def test_malformed_typed_ir_isolated_without_losing_good_candidate(fake_runtime, tmp_path) -> None:
    observation = _observation()
    malformed = TypedIRCandidate.model_construct(
        diagram_type="flowchart",
        ir={
            "nodes": [{"id": "bad", "label": "Bad\ud800"}],
            "edges": [],
        },
        confidence=1.0,
    )
    observation.typed_candidates.insert(0, malformed)
    config = MermaidConfig(candidate_count=1, type_candidate_count=1)
    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct(
        "_page_0_Figure_1",
        "_page_0_Figure_1.jpeg",
        Image.new("RGB", (100, 60), "white"),
        ocr_texts=["Start End"],
    )

    assert result.publish
    assert result.selected is not None
    assert result.selected.typed_ir is not None
    assert result.selected.typed_ir["nodes"] == _observation().typed_candidates[0].ir["nodes"]
    assert any(
        "invalid typed candidate was isolated" in failure.message for failure in result.failures
    )
    relative = SidecarStore(tmp_path).write(result)
    assert (tmp_path / relative / "manifest.json").is_file()


def test_serialized_receipt_cannot_restore_process_private_publication_trust(fake_runtime) -> None:
    result = _validated_result(fake_runtime)

    reloaded = ReconstructionResult.model_validate(result.model_dump(mode="python"))

    assert reloaded.selected is not None
    assert reloaded.selected.validation_receipt is not None
    assert reconstruction_markdown(reloaded) == ""


def test_sidecar_manifest_records_exact_validated_artifact_digests(fake_runtime, tmp_path) -> None:
    result = _validated_result(fake_runtime)

    relative = SidecarStore(tmp_path).write(result)
    bundle = tmp_path / relative
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    receipt = manifest["generation_validation_receipt"]

    assert manifest["schema_version"] == "mmx-sidecar-0.5"
    assert receipt["security_profile"] == "strict"
    assert receipt["png_sha256"] is None
    assert manifest["generation_publication_receipt"]["publish_policy"] == ("best_effort_validated")
    assert manifest["generation_publication_receipt"]["publish"] is True
    assert receipt["code_sha256"] == hashlib.sha256((bundle / "final.mmd").read_bytes()).hexdigest()
    assert receipt["svg_sha256"] == hashlib.sha256((bundle / "final.svg").read_bytes()).hexdigest()
    quality_payload = json.loads((bundle / "scores.json").read_text(encoding="utf-8"))
    aggregate_score = quality_payload["aggregate_score"]
    quality_payload["aggregate_score"] = (
        (format(Decimal(str(aggregate_score)), "f").rstrip("0").rstrip(".") or "0")
        if aggregate_score is not None
        else None
    )
    quality_payload["metrics"] = {
        key: (format(Decimal(str(value)), "f").rstrip("0").rstrip(".") or "0")
        for key, value in quality_payload["metrics"].items()
    }
    canonical_quality = json.dumps(
        quality_payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert (
        manifest["generation_publication_receipt"]["candidate_quality_sha256"]
        == hashlib.sha256(canonical_quality).hexdigest()
    )


def test_quality_receipt_has_a_cross_language_decimal_test_vector() -> None:
    assert (
        _candidate_quality_sha256(
            -0.0,
            "C",
            {"tiny": 1e-7, "zero": -0.0},
            [],
        )
        == "ee36d80539010204f914e727bf574ddd015272566ff6981b57a377d86d2d09a5"
    )


def test_quality_receipt_rejects_non_ascii_metric_keys() -> None:
    with pytest.raises(ValueError, match="bounded probability values"):
        _candidate_quality_sha256(0.5, "C", {"😀": 0.5}, [])


def test_sidecar_rejects_tampered_published_candidate_atomically(fake_runtime, tmp_path) -> None:
    result = _validated_result(fake_runtime)
    assert result.selected is not None
    result.selected.mermaid_code += "\nBROKEN"

    with pytest.raises(ValueError, match="publication authorization|validation receipt"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams" / "page_0_figure_1").exists()


@pytest.mark.parametrize(
    ("policy", "profile"),
    [
        (PublishPolicy.REVIEW_REQUIRED, SecurityProfile.STRICT),
        (PublishPolicy.SIDECAR_ONLY, SecurityProfile.STRICT),
        (PublishPolicy.REVIEW_REQUIRED, SecurityProfile.TRUSTED_LOCAL),
    ],
)
def test_mutating_nonautomatic_policy_result_cannot_authorize_publication(
    fake_runtime, tmp_path, policy, profile
) -> None:
    config = MermaidConfig(
        candidate_count=1,
        type_candidate_count=1,
        publish_policy=policy,
        security_profile=profile,
    )
    result = _pipeline_result(fake_runtime, config)
    assert not result.publish
    assert result.selected is not None
    assert result.selected.has_validated_publication_artifacts()

    result.publish = True
    result.review_required = False
    result.status = "success"

    assert not result.has_authorized_publication()
    assert reconstruction_markdown(result) == ""
    with pytest.raises(ValueError, match="publication authorization"):
        SidecarStore(tmp_path).write(result)


def test_swapped_valid_png_is_omitted_and_rejected_by_published_sidecar(tmp_path) -> None:
    config = MermaidConfig(candidate_count=1, type_candidate_count=1)
    result = _pipeline_result(_PngRuntime("white"), config)
    assert result.publish
    assert result.selected is not None
    assert result.selected.has_validated_rendered_preview()
    replacement = BytesIO()
    Image.new("RGB", (2, 2), "red").save(replacement, format="PNG")

    result.selected.png = replacement.getvalue()

    assert result.has_authorized_publication()
    assert not result.selected.has_validated_rendered_preview()
    assert reconstruction_markdown(result).count("```mermaid") == 1
    with pytest.raises(ValueError, match="published PNG"):
        SidecarStore(tmp_path).write(result)


def test_published_sidecar_forces_sealed_svg_when_write_svg_is_disabled(
    fake_runtime, tmp_path
) -> None:
    result = _validated_result(fake_runtime)

    relative = SidecarStore(tmp_path, write_svg=False).write(result)
    bundle = tmp_path / relative
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    assert (bundle / "final.svg").is_file()
    assert manifest["files"]["final.svg"] == manifest["generation_validation_receipt"]["svg_sha256"]


def test_sidecar_png_opt_out_keeps_receipts_referentially_consistent(tmp_path) -> None:
    config = MermaidConfig(candidate_count=1, type_candidate_count=1)
    result = _pipeline_result(_PngRuntime("white"), config)

    relative = SidecarStore(tmp_path, write_png=False).write(result)
    bundle = tmp_path / relative
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    validation_receipt = manifest["generation_validation_receipt"]
    publication_receipt = manifest["generation_publication_receipt"]
    canonical_validation = json.dumps(
        validation_receipt,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert not (bundle / "final.png").exists()
    assert validation_receipt["png_sha256"] is not None
    assert manifest["generation_artifact_presence"]["final.png"] is False
    assert (
        publication_receipt["candidate_validation_sha256"]
        == hashlib.sha256(canonical_validation).hexdigest()
    )


def test_nonpublished_svg_opt_out_does_not_leave_orphan_publication_receipt(
    fake_runtime, tmp_path
) -> None:
    config = MermaidConfig(
        candidate_count=1,
        type_candidate_count=1,
        publish_policy=PublishPolicy.REVIEW_REQUIRED,
    )
    result = _pipeline_result(fake_runtime, config)

    relative = SidecarStore(tmp_path, write_svg=False).write(result)
    manifest = json.loads((tmp_path / relative / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["generation_validation_receipt"] is None
    assert manifest["generation_publication_receipt"] is None
    assert manifest["generation_artifact_presence"]["final.svg"] is False


def test_deserialized_nonautomatic_result_remains_reviewable_sidecar(
    fake_runtime, tmp_path
) -> None:
    config = MermaidConfig(
        candidate_count=1,
        type_candidate_count=1,
        publish_policy=PublishPolicy.REVIEW_REQUIRED,
    )
    original = _pipeline_result(fake_runtime, config)
    reloaded = ReconstructionResult.model_validate(original.model_dump(mode="python"))

    relative = SidecarStore(tmp_path).write(reloaded)
    manifest = json.loads((tmp_path / relative / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["publish"] is False
    assert manifest["review_required"] is True
    assert manifest["generation_validation_receipt"] is None
    assert manifest["generation_publication_receipt"] is None


@pytest.mark.integration
def test_marker_renderer_preserves_original_and_omits_tampered_candidate(
    fake_runtime, monkeypatch
) -> None:
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer

    from marker_mermaid.marker_integration import MermaidMarkdownRenderer

    result = _validated_result(fake_runtime)
    result.selected.mermaid_code += "\nBROKEN"

    class Identifier:
        def to_path(self):
            return "_page_0_Figure_1"

    class Block:
        id = Identifier()
        block_type = "Figure"

        def get_internal_metadata(self, key):
            return {
                "mermaid": {"status": "success", "errors": []},
                "mermaid_results": [result],
            }.get(key)

    class Page:
        def contained_blocks(self, document, block_types):
            return [Block()]

    class Document:
        pages = [Page()]

    monkeypatch.setattr(
        MarkdownRenderer,
        "__call__",
        lambda self, document: MarkdownOutput(
            markdown="![source](_page_0_Figure_1.jpeg)",
            images={"_page_0_Figure_1.jpeg": Image.new("RGB", (100, 60), "white")},
            metadata={},
        ),
    )

    rendered = MermaidMarkdownRenderer()(Document())

    assert "![source](images/_page_0_Figure_1.jpeg)" in rendered.markdown
    assert "```mermaid" not in rendered.markdown
    assert "_page_0_Figure_1.jpeg" in rendered.images
