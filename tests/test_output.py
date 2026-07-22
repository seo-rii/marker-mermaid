from __future__ import annotations

import pytest
from PIL import Image

from marker_mermaid.models import ReconstructionResult
from marker_mermaid.output import save_document_output


def test_output_preflight_rejects_missing_source_image_before_writing(tmp_path):
    output = tmp_path / "output"
    result = ReconstructionResult(
        source_id="source",
        source_image_name="missing.png",
        status="failed",
    )

    with pytest.raises(ValueError, match="missing source image"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="",
            images={},
            metadata={"mermaid": []},
            reconstructions=[result],
        )

    assert not output.exists()


def test_output_preflight_rejects_duplicate_source_and_sidecar_names(tmp_path):
    image = Image.new("RGB", (10, 10), "white")
    first = ReconstructionResult(
        source_id="Source",
        source_image_name="first.png",
        status="failed",
    )
    duplicate = first.model_copy(update={"source_image_name": "second.png"})
    with pytest.raises(ValueError, match="duplicate reconstruction source id"):
        save_document_output(
            output_dir=tmp_path / "duplicate",
            filename="document",
            markdown="",
            images={"first.png": image, "second.png": image.copy()},
            metadata={"mermaid": []},
            reconstructions=[first, duplicate],
        )

    collision = first.model_copy(update={"source_id": "source", "source_image_name": "second.png"})
    with pytest.raises(ValueError, match="colliding sidecar directory"):
        save_document_output(
            output_dir=tmp_path / "collision",
            filename="document",
            markdown="",
            images={"first.png": image, "second.png": image.copy()},
            metadata={"mermaid": []},
            reconstructions=[first, collision],
        )


def test_output_metadata_must_be_strictly_json_serializable(tmp_path):
    output = tmp_path / "output"
    with pytest.raises(TypeError):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="",
            images={},
            metadata={"mermaid": [], "leaked_image": Image.new("RGB", (1, 1))},
            reconstructions=[],
        )

    assert not output.exists()


@pytest.mark.parametrize("rows", [None, 1, "source", [None], ["source"]])
def test_output_preflight_rejects_invalid_mermaid_metadata_rows(tmp_path, rows):
    output = tmp_path / "output"

    with pytest.raises(TypeError, match="metadata Mermaid rows"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="",
            images={},
            metadata={"mermaid": rows},
            reconstructions=[],
        )

    assert not output.exists()


@pytest.mark.parametrize("name", ["source", "source.unknown"])
def test_output_preflight_rejects_images_without_a_writable_format(tmp_path, name):
    output = tmp_path / "output"

    with pytest.raises(ValueError, match="supported writable extension"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="",
            images={name: Image.new("RGB", (1, 1), "white")},
            metadata={"mermaid": []},
            reconstructions=[],
        )

    assert not output.exists()


@pytest.mark.parametrize("existing_name", ["document.md", "document_meta.json"])
def test_output_preflight_refuses_to_overwrite_document_artifacts(tmp_path, existing_name):
    output = tmp_path / "output"
    output.mkdir()
    existing = output / existing_name
    existing.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="output artifact already exists"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="replacement",
            images={},
            metadata={"mermaid": []},
            reconstructions=[],
        )

    assert existing.read_text(encoding="utf-8") == "keep"


def test_output_preflight_refuses_to_overwrite_images(tmp_path):
    output = tmp_path / "output"
    image_dir = output / "images"
    image_dir.mkdir(parents=True)
    existing = image_dir / "source.png"
    existing.write_bytes(b"keep")

    with pytest.raises(FileExistsError, match="output image already exists"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="",
            images={"source.png": Image.new("RGB", (1, 1), "white")},
            metadata={"mermaid": []},
            reconstructions=[],
        )

    assert existing.read_bytes() == b"keep"
