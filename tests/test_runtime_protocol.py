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
