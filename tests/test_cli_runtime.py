from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from marker_mermaid import cli
from marker_mermaid.config import MermaidConfig


def test_cli_import_does_not_load_reconstruction_or_pillow_adapter():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; import marker_mermaid.cli; "
                "names = ('marker_mermaid.engines', 'marker_mermaid.pipeline', "
                "'marker_mermaid.pillow_compat'); "
                "print(json.dumps([name for name in names if name in sys.modules]))"
            ),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(probe.stdout) == []


def test_runtime_dir_resolver_uses_cli_then_config_then_environment_then_default(
    monkeypatch,
    tmp_path,
):
    cli_path = tmp_path / "cli"
    config_path = tmp_path / "config"
    environment_path = tmp_path / "environment"
    fallback_path = tmp_path / "default"
    config = MermaidConfig(runtime_dir=config_path)
    monkeypatch.setenv("MARKER_MERMAID_RUNTIME_DIR", str(environment_path))

    assert cli.resolve_runtime_dir(cli_value=cli_path, config=config) == cli_path
    assert cli.resolve_runtime_dir(cli_value=None, config=config) == config_path
    assert cli.resolve_runtime_dir(cli_value=None, config=MermaidConfig()) == environment_path

    monkeypatch.delenv("MARKER_MERMAID_RUNTIME_DIR")
    monkeypatch.setattr(cli, "default_runtime_dir", lambda: fallback_path)

    assert cli.resolve_runtime_dir(cli_value=None, config=MermaidConfig()) == fallback_path


@pytest.mark.parametrize("command", ["doctor", "install-runtime"])
def test_runtime_commands_accept_config_and_runtime_dir_options(command):
    args = cli.build_parser().parse_args(
        [command, "--config", "config.json", "--runtime-dir", "runtime"]
    )

    assert args.config == "config.json"
    assert args.runtime_dir == "runtime"


def test_doctor_uses_runtime_dir_from_config(monkeypatch, tmp_path, capsys):
    runtime_dir = tmp_path / "configured-runtime"
    (runtime_dir / "node_modules" / "mermaid").mkdir(parents=True)
    (runtime_dir / "node_modules" / "playwright").mkdir()
    for source in cli._packaged_runtime_files():
        if source.suffix == ".mjs":
            (runtime_dir / source.name).write_text("", encoding="utf-8")
    browser = tmp_path / "chromium"
    browser.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"runtime_dir": str(runtime_dir)}), encoding="utf-8")
    probes = []

    def run_probe(*args, **kwargs):
        probes.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=f"{browser}\n")

    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli.subprocess, "run", run_probe)

    assert cli.main(["doctor", "--config", str(config_path)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["runtime_dir"] == str(runtime_dir)
    assert report["runtime_worker"]
    assert all(report["runtime_modules"].values())
    assert report["runtime_node_modules"]
    assert report["runtime_playwright"]
    assert report["chromium_installed"]
    assert probes[0][1]["cwd"] == runtime_dir


def test_doctor_fails_when_a_packaged_runtime_module_is_missing(
    monkeypatch,
    tmp_path,
    capsys,
):
    runtime_dir = tmp_path / "incomplete-runtime"
    (runtime_dir / "node_modules" / "mermaid").mkdir(parents=True)
    (runtime_dir / "node_modules" / "playwright").mkdir()
    (runtime_dir / "worker.mjs").write_text("", encoding="utf-8")
    browser = tmp_path / "chromium"
    browser.write_text("", encoding="utf-8")
    support_modules = [
        source.name
        for source in cli._packaged_runtime_files()
        if source.suffix == ".mjs" and source.name != "worker.mjs"
    ]
    assert support_modules
    monkeypatch.setenv("MARKER_MERMAID_CHROMIUM_EXECUTABLE", str(browser))
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert cli.main(["doctor", "--runtime-dir", str(runtime_dir)]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report["runtime_worker"]
    assert not report["runtime_modules"][support_modules[0]]


def test_install_runtime_cli_option_overrides_config_and_environment(monkeypatch, tmp_path):
    cli_path = tmp_path / "cli-runtime"
    config_path_value = tmp_path / "config-runtime"
    environment_path = tmp_path / "environment-runtime"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"runtime_dir": str(config_path_value)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MARKER_MERMAID_RUNTIME_DIR", str(environment_path))
    calls = []

    def run_install(command, *, cwd, check):
        calls.append((command, cwd, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", run_install)

    assert (
        cli.main(
            [
                "install-runtime",
                "--config",
                str(config_path),
                "--runtime-dir",
                str(cli_path),
            ]
        )
        == 0
    )

    assert [call[1] for call in calls] == [cli_path, cli_path]
    assert all(call[2] for call in calls)
    assert (cli_path / "package.json").is_file()
    assert (cli_path / "package-lock.json").is_file()
    assert (cli_path / "worker.mjs").is_file()
    packaged_runtime = Path(cli.__file__).resolve().parent / "runtime"
    assert {path.name for path in packaged_runtime.glob("*.mjs")} <= {
        path.name for path in cli_path.glob("*.mjs")
    }
    assert not config_path_value.exists()
    assert not environment_path.exists()
