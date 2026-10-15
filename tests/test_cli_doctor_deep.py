import json
import subprocess
from types import SimpleNamespace

import pytest

from marker_mermaid import cli
from marker_mermaid.protocols import RuntimeResult


def _installed_runtime(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    (runtime_dir / "node_modules" / "mermaid").mkdir(parents=True)
    (runtime_dir / "node_modules" / "playwright").mkdir()
    for source in cli._packaged_runtime_files():
        if source.suffix == ".mjs":
            (runtime_dir / source.name).write_text("", encoding="utf-8")
    browser = tmp_path / "chromium"
    browser.touch()
    monkeypatch.setenv("MARKER_MERMAID_CHROMIUM_EXECUTABLE", str(browser))
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/node")
    return runtime_dir


def test_default_doctor_does_not_launch_the_renderer(monkeypatch, tmp_path, capsys):
    runtime_dir = _installed_runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "NodeMermaidRuntime", lambda _: pytest.fail("must not launch browser"))

    assert cli.main(["doctor", "--runtime-dir", str(runtime_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["render_smoke"] is None


@pytest.mark.parametrize("failure", [None, "render", "exception", "empty-svg"])
def test_deep_doctor_validates_real_runtime_output_and_closes_runtime(
    monkeypatch, tmp_path, capsys, failure
):
    runtime_dir = _installed_runtime(monkeypatch, tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"render_timeout_seconds": 1.25}), encoding="utf-8")
    calls = []
    closed = []

    def render(code, timeout):
        calls.append((code, timeout))
        if failure == "exception":
            raise RuntimeError("Chromium failed to start")
        if failure == "render":
            return RuntimeResult(True, False, error="renderer timed out")
        svg = None if failure == "empty-svg" else (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<text>Doctor Ready</text></svg>'
        )
        return RuntimeResult(True, True, diagram_type="flowchart-v2", svg=svg)

    def runtime(path):
        assert path == runtime_dir
        return SimpleNamespace(validate_and_render=render, close=lambda: closed.append(True))

    monkeypatch.setattr(cli, "NodeMermaidRuntime", runtime)
    status = cli.main(["doctor", "--deep", "--runtime-dir", str(runtime_dir),
                       "--config", str(config)])
    report = json.loads(capsys.readouterr().out)

    assert status == (1 if failure else 0)
    assert report["render_smoke"]["passed"] is (failure is None)
    assert bool(report["render_smoke"]["error"]) is (failure is not None)
    assert calls == [("flowchart LR\n    A[Doctor] --> B[Ready]\n", 1.25)]
    assert closed == [True]


def test_deep_doctor_reports_missing_prerequisites_without_launching(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "NodeMermaidRuntime", lambda _: pytest.fail("missing runtime"))
    assert cli.main(["doctor", "--deep", "--runtime-dir", str(tmp_path / "missing")]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["render_smoke"]["error"] == "runtime prerequisites unavailable"


def test_doctor_bounds_the_executable_probe(monkeypatch, tmp_path, capsys):
    runtime_dir = _installed_runtime(monkeypatch, tmp_path)
    monkeypatch.delenv("MARKER_MERMAID_CHROMIUM_EXECUTABLE")

    def timeout_probe(*args, **kwargs):
        assert kwargs["timeout"] == 10.0
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(cli.subprocess, "run", timeout_probe)
    assert cli.main(["doctor", "--runtime-dir", str(runtime_dir)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "timed out" in report["chromium_probe_error"]


@pytest.mark.integration
def test_deep_doctor_smoke_with_real_mermaid_and_chromium(capsys):
    assert cli.main(["doctor", "--deep"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["render_smoke"] == {"passed": True, "error": None, "warnings": []}
