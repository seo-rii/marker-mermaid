import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image, PngImagePlugin

from marker_mermaid import cli


@pytest.mark.parametrize(
    ("content", "settings", "error"),
    [
        ("x" * 101, {"max_mermaid_chars": 100}, "max_mermaid_chars"),
        ("x" * 401, {"max_mermaid_chars": 100}, "max_mermaid_chars"),
        ("한" * 101, {"max_mermaid_chars": 100}, "max_mermaid_chars"),
        ("a\nb\nc", {"max_mermaid_lines": 2}, "max_mermaid_lines"),
    ],
)
def test_validate_rejects_input_budgets_before_creating_runtime(
    monkeypatch, tmp_path, content, settings, error
):
    source = tmp_path / "source.mmd"
    source.write_text(content, encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(cli, "_runtime", lambda _: pytest.fail("runtime must not start"))

    with pytest.raises(ValueError, match=error):
        cli.command_validate(SimpleNamespace(config=str(config), file=str(source)))


def test_validate_accepts_exact_character_limit_with_multibyte_unicode(
    monkeypatch, tmp_path, capsys
):
    source = tmp_path / "source.mmd"
    source.write_text("한" * 100, encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"max_mermaid_chars": 100}), encoding="utf-8")
    runtime = SimpleNamespace(closed=False)
    runtime.close = lambda: setattr(runtime, "closed", True)

    def validate(code, _timeout):
        assert code == "한" * 100
        return SimpleNamespace(
            runtime=SimpleNamespace(
                syntax_valid=True, render_valid=True, diagram_type="flowchart", error=None
            ),
            warnings=[],
        )

    monkeypatch.setattr(cli, "_runtime", lambda _: runtime)
    monkeypatch.setattr(cli, "_validator", lambda *_: SimpleNamespace(validate=validate))

    assert cli.command_validate(SimpleNamespace(config=str(config), file=str(source))) == 0
    assert runtime.closed
    assert json.loads(capsys.readouterr().out)["render_valid"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_validate_rejects_fifo_without_waiting_for_a_writer(tmp_path):
    source = tmp_path / "source.mmd"
    os.mkfifo(source)
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; from types import SimpleNamespace; "
         "from marker_mermaid.cli import command_validate; "
         "command_validate(SimpleNamespace(config=None, file=sys.argv[1]))", str(source)],
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert probe.returncode != 0
    assert "Mermaid input must be a regular file" in probe.stderr


@pytest.mark.parametrize(
    ("size", "settings"),
    [((11, 1), {"max_virtual_source_dimension": 10}),
     ((8, 8), {"max_virtual_source_pixels": 63})],
)
def test_reconstruct_checks_header_budget_before_pixel_decode(
    monkeypatch, tmp_path, size, settings
):
    source = tmp_path / "source.png"
    Image.new("RGB", size).save(source)
    config = tmp_path / "config.json"
    config.write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setattr(
        PngImagePlugin.PngImageFile, "load", lambda _: pytest.fail("must not decode pixels")
    )
    monkeypatch.setattr(cli, "_runtime", lambda _: pytest.fail("runtime must not start"))

    with pytest.raises(ValueError, match="dimension/pixel budget"):
        cli.command_reconstruct(
            SimpleNamespace(
                config=str(config), image=str(source), output=tmp_path / "output", name="document"
            )
        )
