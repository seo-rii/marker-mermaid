from __future__ import annotations

import json
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import marker_mermaid.validation as validation
from marker_mermaid.render_artifacts import RenderArtifactLimits
from marker_mermaid.resource_limits import (
    MAX_RUNTIME_WORKER_CPU_SECONDS,
    MAX_RUNTIME_WORKER_DATA_BYTES,
)
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


def test_runtime_sends_per_request_render_limits_and_preserves_omission_reason(tmp_path):
    _worker(
        tmp_path,
        """
import readline from "node:readline";
const expected = {
  maxSvgBytes: 1234,
  maxPngBytes: 2345,
  maxDimension: 345,
  maxPixels: 4567,
  maxSvgNodes: 56,
  maxSvgTextChars: 678,
  maxSvgPaths: 78,
  maxSvgPathDataChars: 890,
};
const rl = readline.createInterface({input: process.stdin});
rl.on("line", line => {
  const request = JSON.parse(line);
  if (JSON.stringify(request.limits) !== JSON.stringify(expected)) {
    process.stdout.write(JSON.stringify({
      id: request.id,
      ok: false,
      syntaxValid: false,
      error: `unexpected limits: ${JSON.stringify(request.limits)}`,
    }) + "\\n");
    return;
  }
  process.stdout.write(JSON.stringify({
    id: request.id,
    ok: true,
    syntaxValid: true,
    diagramType: "flowchart-v2",
    svg: "<svg viewBox='0 0 1 1'/>",
    png: null,
    pngOmittedReason: "render resource limit",
  }) + "\\n");
});
""",
    )
    limits = RenderArtifactLimits(
        max_svg_bytes=1234,
        max_png_bytes=2345,
        max_dimension=345,
        max_pixels=4567,
        max_svg_nodes=56,
        max_svg_text_chars=678,
        max_svg_paths=78,
        max_svg_path_data_chars=890,
    )
    runtime = NodeMermaidRuntime(tmp_path, render_limits=limits)
    try:
        result = runtime.validate_and_render("flowchart LR\nA-->B", 2)
    finally:
        runtime.close()

    assert result.syntax_valid
    assert result.render_valid
    assert result.png is None
    assert result.png_omitted_reason == "render resource limit"


@pytest.mark.parametrize(
    "response_fields",
    [
        {"png": None, "pngOmittedReason": ""},
        {"png": None, "pngOmittedReason": 1},
        {"png": "aGVsbG8=", "pngOmittedReason": "limit"},
    ],
)
def test_runtime_rejects_invalid_png_omission_protocol(tmp_path, response_fields):
    _worker(
        tmp_path,
        f"""
import readline from "node:readline";
const fields = {json.dumps(response_fields)};
const rl = readline.createInterface({{input: process.stdin}});
rl.on("line", line => {{
  const request = JSON.parse(line);
  process.stdout.write(JSON.stringify({{
    id: request.id,
    ok: true,
    syntaxValid: true,
    svg: "<svg viewBox='0 0 1 1'/>",
    ...fields,
  }}) + "\\n");
}});
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)

    result = runtime.validate_and_render("flowchart LR\nA-->B", 2)

    assert not result.render_valid
    assert "PNG" in (result.error or "")
    assert runtime._process is None  # noqa: SLF001 - invalid protocol cleanup contract


def test_runtime_rejects_png_base64_before_decoding_when_over_budget(
    monkeypatch, tmp_path
):
    _worker(
        tmp_path,
        """
import readline from "node:readline";
const rl = readline.createInterface({input: process.stdin});
rl.on("line", line => {
  const request = JSON.parse(line);
  process.stdout.write(JSON.stringify({
    id: request.id,
    ok: true,
    syntaxValid: true,
    svg: "<svg viewBox='0 0 1 1'/>",
    png: "aGVsbG8=",
  }) + "\\n");
});
""",
    )
    monkeypatch.setattr(validation, "MAX_RENDER_BASE64_CHARS", 4)
    runtime = NodeMermaidRuntime(tmp_path)

    result = runtime.validate_and_render("flowchart LR\nA-->B", 2)

    assert not result.render_valid
    assert result.error == "Mermaid runtime returned oversized PNG data"
    assert runtime._process is None  # noqa: SLF001 - invalid protocol cleanup contract


def _render_limits_module() -> Path:
    return (
        Path(validation.__file__).resolve().parent
        / "runtime"
        / "render_limits.mjs"
    )


def test_preflight_omission_never_calls_screenshot() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is required for runtime protocol tests")
    module_uri = _render_limits_module().as_uri()
    render_limits = json.dumps(RenderArtifactLimits().worker_payload())
    script = f"""
import {{ captureBoundedPng, validateRenderLimits }} from {module_uri!r};
const limits = validateRenderLimits({render_limits});
let screenshotCalled = false;
const locator = {{
  async screenshot() {{
    screenshotCalled = true;
    throw new Error("screenshot must not be called");
  }},
}};
const result = await captureBoundedPng(
  locator,
  "rendered SVG DOM exceeds the node limit",
  limits,
);
process.stdout.write(JSON.stringify({{ screenshotCalled, result }}));
"""

    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    payload = json.loads(completed.stdout)

    assert payload == {
        "screenshotCalled": False,
        "result": {
            "png": None,
            "pngOmittedReason": "rendered SVG DOM exceeds the node limit",
        },
    }


def test_worker_preflight_covers_dom_text_paths_bounds_and_viewbox() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is required for runtime protocol tests")
    module_uri = _render_limits_module().as_uri()
    render_limits = json.dumps(RenderArtifactLimits().worker_payload())
    script = f"""
import {{
  geometryOmissionReason,
  staticSvgOmissionReason,
  validateRenderLimits,
}} from {module_uri!r};
const limits = validateRenderLimits({render_limits});
const staticBase = {{
  nodeCount: 1,
  textLength: 1,
  pathCount: 1,
  pathDataLength: 1,
  securityFinding: null,
}};
const geometryBase = {{
  rect: {{ x: 0, y: 0, width: 10, height: 10 }},
  viewBox: {{ x: 0, y: 0, width: 10, height: 10 }},
  contentBounds: {{ x: 0, y: 0, width: 10, height: 10 }},
  intrinsicSize: {{ x: 0, y: 0, width: 10, height: 10 }},
}};
const reasons = {{
  nodes: staticSvgOmissionReason(
    {{ ...staticBase, nodeCount: limits.maxSvgNodes + 1 }},
    limits,
  ),
  text: staticSvgOmissionReason(
    {{ ...staticBase, textLength: limits.maxSvgTextChars + 1 }},
    limits,
  ),
  paths: staticSvgOmissionReason(
    {{ ...staticBase, pathCount: limits.maxSvgPaths + 1 }},
    limits,
  ),
  pathData: staticSvgOmissionReason(
    {{ ...staticBase, pathDataLength: limits.maxSvgPathDataChars + 1 }},
    limits,
  ),
  unsafe: staticSvgOmissionReason(
    {{ ...staticBase, securityFinding: "external href" }},
    limits,
  ),
  bounds: geometryOmissionReason(
    {{ ...geometryBase, rect: {{ x: 0, y: 0, width: 0, height: 10 }} }},
    limits,
  ),
  viewBox: geometryOmissionReason(
    {{
      ...geometryBase,
      viewBox: {{
        x: 0,
        y: 0,
        width: limits.maxDimension + 1,
        height: 10,
      }},
    }},
    limits,
  ),
  pixels: geometryOmissionReason(
    {{
      ...geometryBase,
      rect: {{ x: 0, y: 0, width: 8000, height: 8000 }},
      viewBox: null,
      contentBounds: null,
      intrinsicSize: null,
    }},
    limits,
  ),
}};
process.stdout.write(JSON.stringify(reasons));
"""

    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert json.loads(completed.stdout) == {
        "nodes": "rendered SVG DOM exceeds the node limit",
        "text": "rendered SVG text exceeds the character limit",
        "paths": "rendered SVG exceeds the path limit",
        "pathData": "rendered SVG path data exceeds the character limit",
        "unsafe": "rendered SVG contains content that cannot be previewed safely",
        "bounds": "rendered SVG has invalid layout bounds",
        "viewBox": "rendered SVG dimensions exceed the preview limit",
        "pixels": "rendered SVG pixel area exceeds the preview limit",
    }


def test_worker_checks_png_bytes_before_base64_encoding() -> None:
    if shutil.which("node") is None:
        pytest.skip("Node is required for runtime protocol tests")
    module_uri = _render_limits_module().as_uri()
    render_limits = RenderArtifactLimits(max_png_bytes=4).worker_payload()
    script = f"""
import {{ captureBoundedPng, validateRenderLimits }} from {module_uri!r};
const limits = validateRenderLimits({json.dumps(render_limits)});
let screenshotCalled = false;
const locator = {{
  async screenshot() {{
    screenshotCalled = true;
    return Buffer.alloc(5);
  }},
}};
const result = await captureBoundedPng(locator, null, limits);
process.stdout.write(JSON.stringify({{ screenshotCalled, result }}));
"""

    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert json.loads(completed.stdout) == {
        "screenshotCalled": True,
        "result": {
            "png": None,
            "pngOmittedReason": "rendered PNG exceeds the byte limit",
        },
    }


def test_runtime_launcher_applies_limits_without_preexec_fn(monkeypatch) -> None:
    import marker_mermaid.runtime_launcher as launcher

    calls: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(resource, "getrlimit", lambda _kind: (-1, -1))
    monkeypatch.setattr(
        resource,
        "setrlimit",
        lambda kind, limit: calls.append((kind, limit)),
    )

    launcher.apply_worker_process_limits(123_456, 78)

    assert calls == [
        (resource.RLIMIT_CORE, (0, 0)),
        (resource.RLIMIT_DATA, (123_456, 123_456)),
        (resource.RLIMIT_CPU, (78, 78)),
    ]


@pytest.mark.integration
@pytest.mark.skipif(
    not hasattr(resource, "prlimit") or sys.platform != "linux",
    reason="Linux prlimit is required to inspect the running worker",
)
def test_real_worker_inherits_os_memory_and_cpu_limits() -> None:
    runtime = NodeMermaidRuntime()
    try:
        result = runtime.validate_and_render("flowchart LR\nA-->B\n", 20)
        process = runtime._process  # noqa: SLF001 - OS-limit integration contract
        assert result.render_valid, result.error
        assert process is not None
        assert resource.prlimit(process.pid, resource.RLIMIT_DATA) == (
            MAX_RUNTIME_WORKER_DATA_BYTES,
            MAX_RUNTIME_WORKER_DATA_BYTES,
        )
        assert resource.prlimit(process.pid, resource.RLIMIT_CPU) == (
            MAX_RUNTIME_WORKER_CPU_SECONDS,
            MAX_RUNTIME_WORKER_CPU_SECONDS,
        )
    finally:
        runtime.close()


@pytest.mark.integration
def test_real_worker_omits_png_when_svg_exceeds_pre_screenshot_node_limit() -> None:
    runtime = NodeMermaidRuntime(
        render_limits=RenderArtifactLimits(max_svg_nodes=1)
    )
    try:
        result = runtime.validate_and_render("flowchart LR\nA-->B\n", 20)
    finally:
        runtime.close()

    assert result.syntax_valid
    assert result.render_valid
    assert result.svg is not None
    assert result.png is None
    assert result.png_omitted_reason == "rendered SVG DOM exceeds the node limit"


@pytest.mark.integration
def test_real_worker_rejects_svg_bytes_before_dom_and_screenshot() -> None:
    runtime = NodeMermaidRuntime(
        render_limits=RenderArtifactLimits(max_svg_bytes=1)
    )
    try:
        result = runtime.validate_and_render("flowchart LR\nA-->B\n", 20)
    finally:
        runtime.close()

    assert result.syntax_valid
    assert not result.render_valid
    assert result.svg is None
    assert result.png is None
    assert result.error == "rendered SVG exceeds the byte limit"
