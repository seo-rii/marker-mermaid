from __future__ import annotations

from types import SimpleNamespace

from marker_mermaid import cli
from marker_mermaid.evaluation import EvaluationManifestError


def test_evaluate_cli_writes_report_and_returns_gate_failure(monkeypatch, tmp_path, capsys):
    loaded = SimpleNamespace(manifest=object())
    report = SimpleNamespace(overall_status="fail")
    monkeypatch.setattr(cli, "load_evaluation_manifest", lambda path: loaded)
    monkeypatch.setattr(cli, "evaluate_manifest", lambda value: report)
    monkeypatch.setattr(
        cli,
        "write_evaluation_report",
        lambda value, output, evaluation: tmp_path / "evaluation-report.json",
    )

    status = cli.main(["evaluate", "manifest.json", "--output", str(tmp_path)])

    assert status == 1
    assert "evaluation-report.json" in capsys.readouterr().out


def test_evaluate_cli_returns_two_for_invalid_manifest(monkeypatch, tmp_path, capsys):
    def reject(path):
        raise EvaluationManifestError("digest mismatch")

    monkeypatch.setattr(cli, "load_evaluation_manifest", reject)

    status = cli.main(["evaluate", "manifest.json", "--output", str(tmp_path)])

    assert status == 2
    assert "digest mismatch" in capsys.readouterr().err


def test_evaluate_cli_returns_zero_for_pass(monkeypatch, tmp_path):
    loaded = SimpleNamespace(manifest=object())
    report = SimpleNamespace(overall_status="pass")
    monkeypatch.setattr(cli, "load_evaluation_manifest", lambda path: loaded)
    monkeypatch.setattr(cli, "evaluate_manifest", lambda value: report)
    monkeypatch.setattr(
        cli,
        "write_evaluation_report",
        lambda value, output, evaluation: tmp_path / "evaluation-report.json",
    )

    assert cli.main(["evaluate", "manifest.json", "--output", str(tmp_path)]) == 0


def test_evaluate_cli_returns_three_for_internal_runtime_error(monkeypatch, tmp_path, capsys):
    loaded = SimpleNamespace(manifest=object())

    def fail(value):
        raise RuntimeError("metric crashed")

    monkeypatch.setattr(cli, "load_evaluation_manifest", lambda path: loaded)
    monkeypatch.setattr(cli, "evaluate_manifest", fail)

    status = cli.main(["evaluate", "manifest.json", "--output", str(tmp_path)])

    assert status == 3
    assert "metric crashed" in capsys.readouterr().err
