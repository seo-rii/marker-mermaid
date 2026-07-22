from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import marker_mermaid.cli as cli
from marker_mermaid.models import ReconstructionResult


def test_reconstruct_closes_the_source_image_before_pipeline_output(monkeypatch, tmp_path):
    copied_image = Image.new("RGB", (2, 2), "white")

    class OpenedImage:
        mode = "RGB"
        exited = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.exited = True

        def load(self):
            return None

        def copy(self):
            return copied_image

    opened = OpenedImage()
    runtime = SimpleNamespace(closed=False)
    runtime.close = lambda: setattr(runtime, "closed", True)
    result = ReconstructionResult(
        source_id="source",
        source_image_name="source.png",
        status="failed",
    )
    saved = {}

    class FixtureEngine:
        @staticmethod
        def from_path(_path):
            return object()

    class Pipeline:
        def __init__(self, *_args, **_kwargs):
            pass

        def reconstruct(self, source_id, image_name, image):
            assert (source_id, image_name, image) == ("source", "source.png", copied_image)
            assert opened.exited
            return result

    def save_document_output(**kwargs):
        saved.update(kwargs)
        return tmp_path / "document.md"

    monkeypatch.setattr(cli.Image, "open", lambda _path: opened)
    monkeypatch.setattr(cli, "JsonFixtureEngine", FixtureEngine)
    monkeypatch.setattr(cli, "_runtime", lambda _config: runtime)
    monkeypatch.setattr(cli, "ReconstructionPipeline", Pipeline)
    monkeypatch.setattr(cli, "standalone_document_markdown", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(cli, "save_document_output", save_document_output)
    args = SimpleNamespace(
        config=None,
        image="source.png",
        fixture="fixture.json",
        source_id=None,
        output=tmp_path,
        name="document",
    )

    exit_code = cli.command_reconstruct(args)

    assert exit_code == 1
    assert runtime.closed
    assert saved["images"] == {"source.png": copied_image}
    assert saved["output_dir"] == tmp_path
    assert saved["filename"] == "document"
    assert Path(saved["output_dir"]) == tmp_path
