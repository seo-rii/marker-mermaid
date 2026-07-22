from __future__ import annotations

import shutil
import time

import pytest

import marker_mermaid.validation as validation
from marker_mermaid.validation import NodeMermaidRuntime


def _worker(tmp_path, source: str) -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is required for runtime protocol tests")
    (tmp_path / "worker.mjs").write_text(source, encoding="utf-8")


def test_partial_runtime_response_respects_deadline_without_blocking_readline(tmp_path):
    _worker(
        tmp_path,
        """
import readline from "node:readline";
const rl = readline.createInterface({input: process.stdin});
rl.on("line", line => {
  const request = JSON.parse(line);
  process.stdout.write(`{\"id\":\"${request.id}\"`);
  setInterval(() => {}, 1000);
});
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)
    started = time.monotonic()
    try:
        result = runtime.validate_and_render("flowchart LR\nA-->B", 0.2)
    finally:
        runtime.close()

    assert time.monotonic() - started < 2
    assert not result.render_valid
    assert "exceeded 0.2s" in (result.error or "")


def test_oversized_runtime_response_is_bounded_and_worker_is_closed(monkeypatch, tmp_path):
    _worker(
        tmp_path,
        """
import readline from "node:readline";
const rl = readline.createInterface({input: process.stdin});
rl.on("line", line => {
  const request = JSON.parse(line);
  process.stdout.write(JSON.stringify({id: request.id, ok: true, svg: "x".repeat(5000)}) + "\\n");
});
""",
    )
    monkeypatch.setattr(validation, "MAX_RUNTIME_RESPONSE_BYTES", 1024)
    runtime = NodeMermaidRuntime(tmp_path)

    result = runtime.validate_and_render("flowchart LR\nA-->B", 2)

    assert not result.render_valid
    assert result.error == "Mermaid runtime response exceeds the size limit"
    assert runtime._process is None  # noqa: SLF001 - cleanup contract


def test_runtime_request_write_is_bounded_by_the_deadline(tmp_path):
    _worker(
        tmp_path,
        """
setInterval(() => {}, 1000);
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)
    started = time.monotonic()
    try:
        result = runtime.validate_and_render("한" * 50_000, 0.2)
    finally:
        runtime.close()

    assert time.monotonic() - started < 2
    assert not result.render_valid
    assert "exceeded 0.2s" in (result.error or "")


def test_runtime_reports_worker_exit_and_bounded_stderr(tmp_path):
    _worker(
        tmp_path,
        """
process.stderr.write("browser launch failed\\n");
process.exit(7);
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)

    result = runtime.validate_and_render("flowchart LR\nA-->B", 2)

    assert not result.render_valid
    assert "exited unexpectedly" in (result.error or "")
    assert "browser launch failed" in (result.error or "")
    assert "marker-mermaid doctor" in (result.error or "")


def test_runtime_rejects_mismatched_response_id_immediately(tmp_path):
    _worker(
        tmp_path,
        """
import readline from "node:readline";
const rl = readline.createInterface({input: process.stdin});
rl.on("line", () => {
  process.stdout.write(JSON.stringify({id: null, ok: false, error: "bad request"}) + "\\n");
});
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)
    started = time.monotonic()
    try:
        result = runtime.validate_and_render("flowchart LR\nA-->B", 2)
    finally:
        runtime.close()

    assert time.monotonic() - started < 1
    assert result.error == "Mermaid runtime returned a mismatched response id: bad request"


def test_runtime_close_allows_worker_to_handle_stdin_eof(tmp_path):
    marker = tmp_path / "closed.txt"
    _worker(
        tmp_path,
        f"""
import fs from "node:fs";
process.stdin.resume();
process.stdin.on("end", () => {{
  fs.writeFileSync({str(marker)!r}, "closed");
}});
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)
    runtime._start()  # noqa: SLF001 - lifecycle contract

    runtime.close()

    assert marker.read_text(encoding="utf-8") == "closed"


def test_runtime_fails_fast_on_unsupported_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(validation.os, "name", "nt")

    with pytest.raises(RuntimeError, match="POSIX platforms only"):
        NodeMermaidRuntime(tmp_path)
