from __future__ import annotations

import json
from io import BytesIO

import pytest
from PIL import Image

import marker_mermaid.models as model_module
import marker_mermaid.sidecars as sidecar_module
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.markdown import (
    reconstruction_markdown,
    reconstruction_markdown_from_snapshot,
)
from marker_mermaid.models import (
    AuthorizedPublicationSnapshot,
    MermaidCandidate,
    ReconstructionResult,
    ValidatedArtifactCertificate,
)
from marker_mermaid.pipeline import certify_publication_result
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.sidecars import SidecarStore
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

_SAFE_CODE = 'flowchart LR\n    A["Start"] --> B["End"]\n'
_MALICIOUS_CODE = 'flowchart LR\n    A --> B; click A "https://evil.example"\n'
_SAFE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text>safe</text></svg>'
_MALICIOUS_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><script>evil()</script></svg>'


def _png(color: str, *, size: tuple[int, int] = (4, 3)) -> bytes:
    payload = BytesIO()
    Image.new("RGB", size, color).save(payload, format="PNG")
    return payload.getvalue()


def _published_result(
    *,
    png: bytes | None = None,
    code: str = _SAFE_CODE,
    aggregate_score: float = 0.8,
    grade: str = "B",
    serialization_stability: str = "stable",
) -> ReconstructionResult:
    candidate = MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type="flowchart",
        mermaid_code=code,
        syntax_valid=True,
        render_valid=True,
        scores={"ocr_recall": 0.8},
        aggregate_score=aggregate_score,
        serialization_stability=serialization_stability,
        svg=_SAFE_SVG,
        png=png,
        runtime_diagram_type="flowchart-v2",
    )

    class Runtime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(
                True,
                True,
                diagram_type="flowchart-v2",
                svg=_SAFE_SVG,
                png=png,
            )

        def close(self):
            pass

    validator = CandidateValidator(Runtime(), SecurityProfile.STRICT)
    outcome = validator.validate(code, 1)
    validator.seal_candidate(candidate, outcome)
    result = ReconstructionResult(
        source_id="_page_0_Figure_1",
        source_image_name="_page_0_Figure_1.jpeg",
        selected=candidate,
        grade=grade,
        publish=True,
        review_required=False,
        status="success",
    )
    assert certify_publication_result(result, MermaidConfig())
    assert candidate.has_validated_publication_artifacts()
    assert result.has_authorized_publication()
    return result


def test_validation_certificate_cannot_be_hand_constructed_or_reused_for_other_code() -> None:
    validator = CandidateValidator(
        type(
            "Runtime",
            (),
            {
                "validate_and_render": lambda self, code, timeout: RuntimeResult(
                    True,
                    True,
                    diagram_type="flowchart-v2",
                    svg=_SAFE_SVG,
                ),
                "close": lambda self: None,
            },
        )(),
        SecurityProfile.STRICT,
    )
    outcome = validator.validate(_SAFE_CODE, 1)
    assert outcome.certificate is not None
    malicious = MermaidCandidate(
        candidate_id="malicious",
        generation_method="direct_mermaid",
        diagram_type="flowchart",
        mermaid_code=_MALICIOUS_CODE,
        syntax_valid=True,
        render_valid=True,
        svg=_SAFE_SVG,
        runtime_diagram_type="flowchart-v2",
    )

    validator.seal_candidate(malicious, outcome)
    assert malicious.validation_receipt is None

    forged = ValidatedArtifactCertificate(
        code_sha256=model_module._artifact_sha256(_MALICIOUS_CODE),
        svg_sha256=model_module._artifact_sha256(_SAFE_SVG),
        security_profile=SecurityProfile.STRICT,
        runtime_diagram_type="flowchart-v2",
    )
    malicious._seal_validation_receipt(forged)
    assert malicious.validation_receipt is None

    wrong_type = MermaidCandidate(
        candidate_id="wrong-type",
        generation_method="direct_mermaid",
        diagram_type="sequence",
        emitted_diagram_type="sequence",
        mermaid_code=_SAFE_CODE,
        syntax_valid=True,
        render_valid=True,
        svg=_SAFE_SVG,
        runtime_diagram_type="flowchart-v2",
    )
    validator.seal_candidate(wrong_type, outcome)
    assert wrong_type.validation_receipt is None


def test_validator_profile_cannot_be_detached_from_its_scanner() -> None:
    validator = CandidateValidator(
        type(
            "Runtime",
            (),
            {
                "validate_and_render": lambda self, code, timeout: RuntimeResult(
                    True,
                    True,
                    diagram_type="flowchart-v2",
                    svg=_SAFE_SVG,
                ),
                "close": lambda self: None,
            },
        )(),
        SecurityProfile.STYLE_ONLY,
    )

    with pytest.raises(AttributeError):
        validator.profile = SecurityProfile.STRICT

    validator._profile = SecurityProfile.STRICT
    outcome = validator.validate("flowchart LR\nA --> B\nstyle A fill:#fff\n", 1)

    assert not outcome.runtime.render_valid
    assert outcome.certificate is None
    assert outcome.warnings == [
        "security:validator_profile: validator security profile is inconsistent"
    ]


def test_validator_withholds_publication_certificate_without_trailing_newline() -> None:
    code = "flowchart LR\nA --> B"

    class Runtime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(True, True, diagram_type="flowchart-v2", svg=_SAFE_SVG)

        def close(self):
            pass

    outcome = CandidateValidator(Runtime(), SecurityProfile.STRICT).validate(code, 1)

    assert outcome.runtime.render_valid
    assert outcome.certificate is None
    assert any("trailing newline" in warning for warning in outcome.warnings)


def test_validator_rejects_render_success_without_syntax_success() -> None:
    class ContradictoryRuntime:
        def validate_and_render(self, code, timeout_seconds):
            return RuntimeResult(False, True, diagram_type="flowchart-v2", svg=_SAFE_SVG)

        def close(self):
            pass

    outcome = CandidateValidator(
        ContradictoryRuntime(),
        SecurityProfile.STRICT,
    ).validate(_SAFE_CODE, 1)

    assert not outcome.runtime.syntax_valid
    assert not outcome.runtime.render_valid
    assert outcome.certificate is None
    assert any("without syntax validation" in warning for warning in outcome.warnings)


def test_markdown_fence_payload_is_the_exact_validated_source() -> None:
    code = "flowchart LR\nA --> B   \n\n"
    result = _published_result(code=code)

    markdown = reconstruction_markdown(result, show_warning=False)
    payload = markdown.split("```mermaid\n", 1)[1].rsplit("```", 1)[0]

    assert payload == code
    assert result.selected is not None
    assert result.selected.validation_receipt is not None
    assert result.selected.validation_receipt.code_sha256 == model_module._artifact_sha256(payload)


@pytest.mark.integration
def test_markdown_uses_a_longer_fence_for_backtick_lines_inside_mermaid_labels() -> None:
    code = 'flowchart LR\nA["safe\n```\n# FORGED DOCUMENT CONTENT\n```mermaid\nstill label"]\n'
    runtime = NodeMermaidRuntime()
    try:
        validator = CandidateValidator(runtime, SecurityProfile.STRICT)
        outcome = validator.validate(code, 60)
    finally:
        runtime.close()
    assert outcome.runtime.render_valid, outcome.runtime.error
    candidate = MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="direct_mermaid",
        diagram_type="flowchart",
        mermaid_code=code,
        syntax_valid=True,
        render_valid=True,
        scores={"ocr_recall": 0.8},
        aggregate_score=0.8,
        svg=outcome.runtime.svg,
        png=outcome.runtime.png,
        runtime_diagram_type=outcome.runtime.diagram_type,
    )
    validator.seal_candidate(candidate, outcome)
    result = ReconstructionResult(
        source_id="source",
        source_image_name="source.png",
        selected=candidate,
        grade="B",
        publish=True,
        review_required=False,
        status="success",
    )
    assert certify_publication_result(result, MermaidConfig())

    markdown = reconstruction_markdown(result, show_warning=False)

    assert markdown.startswith("````mermaid\n")
    assert markdown.endswith("````")
    assert markdown.count("````") == 2
    assert "# FORGED DOCUMENT CONTENT" in markdown


def test_publication_certifier_recomputes_policy_and_has_no_unchecked_model_sealer() -> None:
    result = _published_result()
    assert not hasattr(result, "_seal_publication_authorization")

    result.publish = False
    result.review_required = True
    result.status = "review_required"

    assert not certify_publication_result(result, MermaidConfig())
    assert result.publication_receipt is None
    assert reconstruction_markdown(result) == ""


def test_publication_certifier_rejects_mutated_config_without_side_effects() -> None:
    result = _published_result()
    config = MermaidConfig()
    config.publish_min_score = -1.0

    assert not certify_publication_result(result, config)
    assert result.publication_receipt is None
    assert reconstruction_markdown(result) == ""


def test_markdown_emits_only_the_authorized_snapshot_when_source_changes_after_hash(
    monkeypatch,
) -> None:
    result = _published_result()
    original_hash = model_module._artifact_sha256
    mutation_observed = False

    def hash_then_mutate_original(value: str) -> str:
        nonlocal mutation_observed
        digest = original_hash(value)
        # A secure sink has already captured ``value`` in its immutable snapshot;
        # changing the source object now must not affect the emitted bytes.
        if value == _SAFE_CODE and not mutation_observed:
            assert result.selected is not None
            result.selected.mermaid_code = _MALICIOUS_CODE
            mutation_observed = True
        return digest

    monkeypatch.setattr(model_module, "_artifact_sha256", hash_then_mutate_original)

    markdown = reconstruction_markdown(result)

    assert mutation_observed
    assert _SAFE_CODE.rstrip() in markdown
    assert "click A" not in markdown
    assert markdown.count("```mermaid") == 1


def test_publication_snapshot_cannot_be_forged_or_modified() -> None:
    result = _published_result()
    snapshot = result.authorized_publication_snapshot()
    assert snapshot is not None
    assert snapshot.has_trusted_values()

    modified = snapshot.model_copy(update={"mermaid_code": _MALICIOUS_CODE})
    reconstructed = AuthorizedPublicationSnapshot.model_validate(snapshot.model_dump(mode="python"))

    assert not modified.has_trusted_values()
    assert not reconstructed.has_trusted_values()
    assert reconstruction_markdown_from_snapshot(modified) == ""
    assert reconstruction_markdown_from_snapshot(reconstructed) == ""


@pytest.mark.parametrize(
    ("serialization_stability", "expects_warning"),
    [
        ("stable", False),
        ("extended", False),
        ("experimental", True),
    ],
)
def test_grade_a_markdown_discloses_only_experimental_serialization(
    serialization_stability: str,
    expects_warning: bool,
) -> None:
    result = _published_result(
        aggregate_score=0.9,
        grade="A",
        serialization_stability=serialization_stability,
    )

    markdown = reconstruction_markdown(result)
    hidden_warning_markdown = reconstruction_markdown(result, show_warning=False)

    assert (markdown.startswith("> **Experimental reconstruction:**")) is expects_warning
    assert hidden_warning_markdown.startswith("```mermaid\n")
    snapshot = result.authorized_publication_snapshot()
    assert snapshot is not None
    assert snapshot.serialization_stability == serialization_stability
    assert snapshot.publication_receipt.serialization_stability == serialization_stability


def test_publication_snapshot_rejects_serialization_stability_tampering() -> None:
    result = _published_result(
        aggregate_score=0.9,
        grade="A",
        serialization_stability="experimental",
    )
    snapshot = result.authorized_publication_snapshot()
    assert snapshot is not None
    serialized = snapshot.model_dump(mode="python")

    modified = snapshot.model_copy(update={"serialization_stability": "stable"})
    reconstructed = AuthorizedPublicationSnapshot.model_validate(serialized)

    assert serialized["serialization_stability"] == "experimental"
    assert not modified.has_trusted_values()
    assert not reconstructed.has_trusted_values()
    assert reconstruction_markdown_from_snapshot(modified) == ""
    assert reconstruction_markdown_from_snapshot(reconstructed) == ""


def test_publication_snapshot_rejects_candidate_stability_mutation_after_certification() -> None:
    result = _published_result(
        aggregate_score=0.9,
        grade="A",
        serialization_stability="experimental",
    )
    assert result.selected is not None

    result.selected.serialization_stability = "stable"

    assert not result.has_authorized_publication()
    assert result.authorized_publication_snapshot() is None
    assert reconstruction_markdown(result) == ""


@pytest.mark.parametrize(
    "update",
    [
        {"source_id": "other-source"},
        {"selected_candidate_id": "other-candidate"},
        {"grade": "A"},
        {"serialization_stability": "experimental"},
        {"png": _png("red"), "preview_omitted": False},
    ],
)
def test_publication_snapshot_rechecks_receipt_relationships(update) -> None:
    snapshot = _published_result(png=_png("white")).authorized_publication_snapshot()
    assert snapshot is not None

    modified = snapshot.model_copy(update=update)

    assert not modified.has_trusted_values()
    assert reconstruction_markdown_from_snapshot(modified) == ""


@pytest.mark.integration
def test_marker_preview_uses_the_same_validated_png_snapshot(monkeypatch) -> None:
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer

    from marker_mermaid.marker_integration import MermaidMarkdownRenderer

    safe_png = _png("white")
    replacement_png = _png("red")
    result = _published_result(png=safe_png)
    original_hash = model_module._artifact_sha256
    mutation_observed = False

    def hash_then_replace_original(value: str) -> str:
        nonlocal mutation_observed
        digest = original_hash(value)
        # The publication snapshot captures code, SVG, and PNG together before
        # hashing. Mutate the source PNG while that captured snapshot is checked.
        if value == _SAFE_CODE and not mutation_observed:
            assert result.selected is not None
            result.selected.png = replacement_png
            mutation_observed = True
        return digest

    monkeypatch.setattr(
        model_module,
        "_artifact_sha256",
        hash_then_replace_original,
    )

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
            markdown="![](_page_0_Figure_1.jpeg)",
            images={},
            metadata={},
        ),
    )
    renderer = MermaidMarkdownRenderer()
    renderer.include_rendered_preview = True

    rendered = renderer(Document())

    preview_name = "page_0_figure_1--mermaid-preview.png"
    assert mutation_observed
    assert preview_name in rendered.images
    assert rendered.images[preview_name].getpixel((0, 0)) == (255, 255, 255)
    assert f"images/{preview_name}" in rendered.markdown
    assert rendered.markdown.count("```mermaid") == 1


def test_sidecar_uses_one_decision_and_artifact_snapshot_or_fails_atomically(
    monkeypatch,
    tmp_path,
) -> None:
    safe_png = _png("white")
    result = _published_result(png=safe_png)
    original_write = sidecar_module._write
    mutation_observed = False

    def write_then_mutate_original(path, data):
        nonlocal mutation_observed
        digest = original_write(path, data)
        if path.name == "final.mmd" and not mutation_observed:
            assert result.selected is not None
            result.publish = False
            result.review_required = True
            result.status = "review_required"
            result.selected.mermaid_code = _MALICIOUS_CODE
            result.selected.svg = _MALICIOUS_SVG
            result.selected.png = _png("red")
            mutation_observed = True
        return digest

    monkeypatch.setattr(sidecar_module, "_write", write_then_mutate_original)
    target = tmp_path / "diagrams" / "page_0_figure_1"

    try:
        relative = SidecarStore(tmp_path).write(result)
    except ValueError:
        assert mutation_observed
        assert not target.exists()
        return

    assert mutation_observed
    bundle = tmp_path / relative
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    publication_receipt = manifest["generation_publication_receipt"]

    assert manifest["publish"] is True
    assert manifest["review_required"] is False
    assert manifest["status"] == "success"
    assert publication_receipt["publish"] is manifest["publish"]
    assert publication_receipt["review_required"] is manifest["review_required"]
    assert publication_receipt["status"] == manifest["status"]
    assert (bundle / "final.mmd").read_text(encoding="utf-8") == _SAFE_CODE
    assert (bundle / "final.svg").read_text(encoding="utf-8") == _SAFE_SVG
    with Image.open(bundle / "final.png") as preview:
        assert preview.convert("RGB").getpixel((0, 0)) == (255, 255, 255)
