from __future__ import annotations

import json
import struct
import zlib
from io import BytesIO

import pytest
from PIL import Image

from marker_mermaid.config import MermaidConfig, PublishPolicy, SecurityProfile
from marker_mermaid.models import MermaidCandidate, ReconstructionResult
from marker_mermaid.pipeline import certify_publication_result
from marker_mermaid.protocols import RuntimeResult
from marker_mermaid.render_artifacts import (
    MAX_RENDER_BYTES,
    MAX_RENDERED_SVG_NODES,
    RenderArtifactLimits,
)
from marker_mermaid.review_store import ReviewValidationResult
from marker_mermaid.sidecars import SidecarStore
from marker_mermaid.validation import CandidateValidator

_CODE = "flowchart LR\nA --> B\n"
_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0"/></svg>'


def _png(color: str = "white") -> bytes:
    payload = BytesIO()
    Image.new("RGB", (4, 3), color).save(payload, format="PNG")
    return payload.getvalue()


def _bad_png(kind: str) -> bytes:
    if kind == "invalid":
        return b"not-a-png"
    if kind == "oversize":
        return b"\x89PNG\r\n\x1a\n" + b"x" * MAX_RENDER_BYTES
    if kind == "dimensions":

        def chunk(chunk_kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + chunk_kind
                + data
                + struct.pack(">I", zlib.crc32(chunk_kind + data) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 100_000, 100_000, 8, 2, 0, 0, 0))
            + chunk(b"IEND", b"")
        )
    raise AssertionError(f"unknown PNG fixture kind: {kind}")


class _ArtifactRuntime:
    def __init__(
        self,
        *,
        svg: str = _SVG,
        png: bytes | None = None,
        png_omitted_reason: str | None = None,
    ):
        self.svg = svg
        self.png = png
        self.png_omitted_reason = png_omitted_reason

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="flowchart-v2",
            svg=self.svg,
            png=self.png,
            png_omitted_reason=self.png_omitted_reason,
        )

    def close(self) -> None:
        pass


def _published_result(*, png: bytes | None = None) -> ReconstructionResult:
    candidate = MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type="flowchart",
        runtime_diagram_type="flowchart-v2",
        mermaid_code=_CODE,
        svg=_SVG,
        png=png,
        syntax_valid=True,
        render_valid=True,
        scores={"ocr_recall": 0.8},
        aggregate_score=0.8,
    )
    validator = CandidateValidator(
        _ArtifactRuntime(png=png),
        SecurityProfile.STRICT,
    )
    outcome = validator.validate(_CODE, 1)
    candidate.svg = outcome.runtime.svg
    candidate.png = outcome.runtime.png
    candidate.runtime_diagram_type = outcome.runtime.diagram_type
    validator.seal_candidate(candidate, outcome)
    assert candidate.has_validated_publication_artifacts()
    result = ReconstructionResult(
        source_id="_page_0_Figure_1",
        source_image_name="_page_0_Figure_1.jpeg",
        selected=candidate,
        grade="B",
        publish=True,
        review_required=False,
        status="success",
    )
    assert certify_publication_result(
        result,
        MermaidConfig(publish_policy=PublishPolicy.BEST_EFFORT_VALIDATED),
    )
    assert result.has_authorized_publication()
    return result


def test_validator_rejects_svg_above_render_artifact_byte_limit() -> None:
    oversized_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>'
        + "x" * MAX_RENDER_BYTES
        + "</text></svg>"
    )

    outcome = CandidateValidator(
        _ArtifactRuntime(svg=oversized_svg), SecurityProfile.STRICT
    ).validate(_CODE, 1)

    assert outcome.runtime.syntax_valid
    assert not outcome.runtime.render_valid
    assert outcome.runtime.svg is None
    assert outcome.runtime.png is None
    assert outcome.runtime.error == "rendered SVG exceeds the artifact size limit"
    assert outcome.warnings == ["rendered SVG artifact exceeds the byte limit"]


def test_validator_counts_utf8_svg_bytes_instead_of_characters() -> None:
    text = "한" * (MAX_RENDER_BYTES // 3)
    oversized_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>'
        + text
        + "</text></svg>"
    )
    assert len(oversized_svg) < MAX_RENDER_BYTES
    assert len(oversized_svg.encode("utf-8")) > MAX_RENDER_BYTES

    outcome = CandidateValidator(
        _ArtifactRuntime(svg=oversized_svg), SecurityProfile.STRICT
    ).validate(_CODE, 1)

    assert not outcome.runtime.render_valid
    assert outcome.runtime.error == "rendered SVG exceeds the artifact size limit"


@pytest.mark.parametrize("kind", ["invalid", "oversize", "dimensions"])
def test_validator_omits_unusable_png_without_invalidating_mermaid_render(kind: str) -> None:
    outcome = CandidateValidator(
        _ArtifactRuntime(png=_bad_png(kind)), SecurityProfile.STRICT
    ).validate(_CODE, 1)

    assert outcome.runtime.syntax_valid
    assert outcome.runtime.render_valid
    assert outcome.runtime.svg == _SVG
    assert outcome.runtime.png is None
    assert len(outcome.warnings) == 1
    assert outcome.warnings[0].startswith("rendered PNG artifact was omitted:")
    if kind == "oversize":
        assert "byte limit" in outcome.warnings[0]


@pytest.mark.parametrize("kind", ["invalid", "oversize", "dimensions"])
def test_review_validation_result_rejects_unusable_png(kind: str) -> None:
    with pytest.raises(ValueError):
        ReviewValidationResult(valid=True, svg=_SVG, png=_bad_png(kind))


def test_validator_retains_small_valid_png() -> None:
    png = _png()

    outcome = CandidateValidator(_ArtifactRuntime(png=png), SecurityProfile.STRICT).validate(
        _CODE, 1
    )

    assert outcome.runtime.render_valid
    assert outcome.runtime.svg == _SVG
    assert outcome.runtime.png == png
    assert outcome.warnings == []


def test_validator_preserves_worker_png_omission_reason() -> None:
    outcome = CandidateValidator(
        _ArtifactRuntime(
            png_omitted_reason="rendered SVG DOM exceeds the node limit",
        ),
        SecurityProfile.STRICT,
    ).validate(_CODE, 1)

    assert outcome.runtime.render_valid
    assert outcome.runtime.svg == _SVG
    assert outcome.runtime.png is None
    assert outcome.runtime.png_omitted_reason == (
        "rendered SVG DOM exceeds the node limit"
    )
    assert outcome.warnings == [
        "rendered PNG artifact was omitted before screenshot: "
        "rendered SVG DOM exceeds the node limit"
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_svg_bytes": 0}, "max_svg_bytes"),
        ({"max_png_bytes": MAX_RENDER_BYTES + 1}, "max_png_bytes"),
        ({"max_svg_nodes": MAX_RENDERED_SVG_NODES + 1}, "max_svg_nodes"),
        ({"max_pixels": True}, "max_pixels"),
    ],
)
def test_browser_render_limits_cannot_exceed_publication_policy(
    overrides: dict[str, int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RenderArtifactLimits(**overrides)


def test_published_sidecar_png_opt_out_ignores_invalid_optional_preview(tmp_path) -> None:
    result = _published_result(png=_png())
    assert result.selected is not None
    result.selected.png = _bad_png("invalid")

    relative = SidecarStore(tmp_path, write_png=False).write(result)
    bundle = tmp_path / relative
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    assert not (bundle / "final.png").exists()
    assert manifest["generation_artifact_presence"]["final.png"] is False
    assert manifest["generation_validation_receipt"]["png_sha256"] is not None
    assert manifest["publish"] is True


@pytest.mark.parametrize("replacement", [_png("red"), _bad_png("invalid")])
def test_published_sidecar_rejects_unvalidated_png_when_write_is_enabled(
    tmp_path, replacement: bytes
) -> None:
    result = _published_result(png=_png())
    assert result.selected is not None
    result.selected.png = replacement

    with pytest.raises(ValueError, match="published PNG|artifact inspection"):
        SidecarStore(tmp_path, write_png=True).write(result)

    assert not (tmp_path / "diagrams" / "page_0_figure_1").exists()


@pytest.mark.integration
def test_marker_preview_omits_invalid_png_without_crashing(monkeypatch) -> None:
    from marker.renderers.markdown import MarkdownOutput, MarkdownRenderer

    from marker_mermaid.marker_integration import MermaidMarkdownRenderer

    result = _published_result(png=_png())
    assert result.selected is not None
    result.selected.png = _bad_png("invalid")

    class Identifier:
        def to_path(self) -> str:
            return "_page_0_Figure_1"

    class Block:
        id = Identifier()
        block_type = "Figure"

        def get_internal_metadata(self, key: str):
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
            images={"_page_0_Figure_1.jpeg": Image.new("RGB", (10, 10), "white")},
            metadata={},
        ),
    )
    renderer = MermaidMarkdownRenderer()
    renderer.include_rendered_preview = True

    rendered = renderer(Document())

    preview_name = "page_0_figure_1--mermaid-preview.png"
    assert preview_name not in rendered.images
    assert f"images/{preview_name}" not in rendered.markdown
    assert "![source](images/_page_0_Figure_1.jpeg)" in rendered.markdown
    assert rendered.markdown.count("```mermaid") == 1
