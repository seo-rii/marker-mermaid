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
