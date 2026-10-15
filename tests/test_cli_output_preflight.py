from types import SimpleNamespace

import pytest
from PIL import Image

from marker_mermaid import cli
from marker_mermaid.output import preflight_output_destination, save_document_output


@pytest.mark.parametrize("kind", ["empty-directory", "file", "symlink", "broken-symlink"])
@pytest.mark.parametrize("command", [cli.command_reconstruct, cli.command_convert])
def test_cli_refuses_existing_output_before_loading_input_or_models(
    monkeypatch, tmp_path, kind, command
):
    output = tmp_path / "output"
    if kind == "empty-directory":
        output.mkdir()
    elif kind == "file":
        output.write_text("keep", encoding="utf-8")
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.mkdir()
        output.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(Image, "open", lambda _: pytest.fail("must not decode input"))
    monkeypatch.setattr(cli, "_runtime", lambda _: pytest.fail("must not start runtime"))
    args = SimpleNamespace(
        config=None, output=output, name="document", image="missing.png", pdf="missing.pdf"
    )

    with pytest.raises(FileExistsError, match="output directory already exists"):
        command(args)


@pytest.mark.parametrize("command", [cli.command_reconstruct, cli.command_convert])
def test_cli_refuses_invalid_document_name_before_loading_input(tmp_path, command):
    args = SimpleNamespace(
        config=None, output=tmp_path / "output", name="../document",
        image="missing.png", pdf="missing.pdf",
    )
    with pytest.raises(ValueError, match="filename must be a single safe path component"):
        command(args)
    assert not args.output.exists()


def test_preflight_does_not_create_or_reserve_destination(tmp_path):
    output = tmp_path / "missing-parent" / "output"
    preflight_output_destination(output_dir=output, filename="document")
    assert not output.parent.exists()


def test_final_save_rejects_collision_created_after_early_preflight(tmp_path):
    output = tmp_path / "output"
    preflight_output_destination(output_dir=output, filename="document")
    output.mkdir()
    (output / "keep").write_bytes(b"keep")

    with pytest.raises(FileExistsError, match="output directory already exists"):
        save_document_output(
            output_dir=output, filename="document", markdown="", images={},
            metadata={}, reconstructions=[],
        )

    assert (output / "keep").read_bytes() == b"keep"
    assert sorted(path.name for path in output.iterdir()) == ["keep"]
