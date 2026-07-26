from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath

import pytest
from PIL import Image

from marker_mermaid.models import ReconstructionResult
from marker_mermaid.output import save_document_output
from marker_mermaid.output_transaction import OutputTransaction


def _output_case():
    result = ReconstructionResult(
        source_id="source",
        source_image_name="source.png",
        status="failed",
    )
    metadata = {"mermaid": [{"source_id": "source"}]}
    images = {"source.png": Image.new("RGB", (2, 2), "white")}
    return result, metadata, images


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


def test_output_rolls_back_when_markdown_write_fails(tmp_path, monkeypatch):
    output = tmp_path / "output"
    result, metadata, images = _output_case()
    original_write = OutputTransaction.write_bytes

    def failing_write(self, relative, payload):
        if PurePosixPath(relative).name == "document.md":
            raise OSError("injected Markdown write failure")
        return original_write(self, relative, payload)

    monkeypatch.setattr(OutputTransaction, "write_bytes", failing_write)

    with pytest.raises(OSError, match="Markdown write failure"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="content",
            images=images,
            metadata=metadata,
            reconstructions=[result],
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".output.tmp-*"))
    assert result.sidecar_dir is None
    assert "sidecar_dir" not in metadata["mermaid"][0]


def test_output_rolls_back_when_metadata_write_fails(tmp_path, monkeypatch):
    output = tmp_path / "output"
    result, metadata, images = _output_case()
    original_write = OutputTransaction.write_bytes

    def failing_write(self, relative, payload):
        if PurePosixPath(relative).name == "document_meta.json":
            raise OSError("injected metadata write failure")
        return original_write(self, relative, payload)

    monkeypatch.setattr(OutputTransaction, "write_bytes", failing_write)

    with pytest.raises(OSError, match="metadata write failure"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="content",
            images=images,
            metadata=metadata,
            reconstructions=[result],
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".output.tmp-*"))
    assert result.sidecar_dir is None
    assert "sidecar_dir" not in metadata["mermaid"][0]


def test_output_rejects_symlink_swap_after_preflight(tmp_path, monkeypatch):
    output = tmp_path / "output"
    victim = tmp_path / "victim"
    victim.mkdir()
    sentinel = victim / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    original_commit = OutputTransaction.commit

    def swapping_commit(self):
        self.destination.symlink_to(victim, target_is_directory=True)
        return original_commit(self)

    monkeypatch.setattr(OutputTransaction, "commit", swapping_commit)

    with pytest.raises(FileExistsError, match="output directory already exists"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="replacement",
            images={},
            metadata={"mermaid": []},
            reconstructions=[],
        )

    assert output.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (victim / "document.md").exists()
    assert not list(tmp_path.glob(".output.tmp-*"))


def test_output_concurrent_writers_never_overwrite(tmp_path, monkeypatch):
    output = tmp_path / "output"
    barrier = threading.Barrier(2)
    from marker_mermaid import output_transaction as transaction_module

    original_rename = transaction_module._rename_noreplace

    def synchronized_rename(*args):
        barrier.wait(timeout=5)
        return original_rename(*args)

    monkeypatch.setattr(transaction_module, "_rename_noreplace", synchronized_rename)

    def write(markdown):
        try:
            save_document_output(
                output_dir=output,
                filename="document",
                markdown=markdown,
                images={},
                metadata={"mermaid": [], "writer": markdown},
                reconstructions=[],
            )
        except FileExistsError:
            return "collision"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, ("first", "second")))

    assert sorted(outcomes) == ["collision", "published"]
    winner = (output / "document.md").read_text(encoding="utf-8")
    assert winner in {"first", "second"}
    metadata = json.loads((output / "document_meta.json").read_text(encoding="utf-8"))
    assert metadata["writer"] == winner
    assert not list(tmp_path.glob(".output.tmp-*"))


def test_output_does_not_mutate_live_results_before_commit(tmp_path, monkeypatch):
    output = tmp_path / "output"
    result, metadata, images = _output_case()

    def rejecting_commit(self):
        assert result.sidecar_dir is None
        assert "sidecar_dir" not in metadata["mermaid"][0]
        raise OSError("injected commit failure")

    monkeypatch.setattr(OutputTransaction, "commit", rejecting_commit)

    with pytest.raises(OSError, match="commit failure"):
        save_document_output(
            output_dir=output,
            filename="document",
            markdown="content",
            images=images,
            metadata=metadata,
            reconstructions=[result],
        )

    assert not output.exists()
    assert result.sidecar_dir is None
    assert "sidecar_dir" not in metadata["mermaid"][0]
