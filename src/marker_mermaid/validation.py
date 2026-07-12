"""Mermaid security, real browser parse/render, and SVG inspection."""

from __future__ import annotations

import atexit
import base64
import json
import os
import selectors
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from marker_mermaid.config import SecurityProfile
from marker_mermaid.protocols import MermaidRuntime, RuntimeResult
from marker_mermaid.security import MermaidSecurityScanner


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    runtime: RuntimeResult
    warnings: list[str]


def default_runtime_dir() -> Path:
    override = os.environ.get("MARKER_MERMAID_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    packaged = Path(__file__).resolve().parent / "runtime"
    if (packaged / "node_modules" / "mermaid").is_dir():
        return packaged
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "marker-mermaid" / "runtime"


def inspect_svg(svg: str, profile: SecurityProfile) -> list[str]:
    findings: list[str] = []
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return [f"rendered output is not XML: {exc}"]
    if root.tag.rsplit("}", 1)[-1] != "svg":
        findings.append("rendered output does not have an SVG root")
    if not any(root.get(attribute) for attribute in ("viewBox", "width", "height")):
        findings.append("rendered SVG has no dimensions")
    forbidden = {"script", "iframe", "object", "embed", "link"}
    if profile == SecurityProfile.STRICT:
        forbidden.add("foreignObject")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in forbidden:
            findings.append(f"rendered SVG contains forbidden <{tag}>")
        for raw_name, value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            lowered = value.strip().lower()
            if name.startswith("on"):
                findings.append(f"rendered SVG contains event handler {name}")
            if name == "href" and lowered and not lowered.startswith("#"):
                findings.append("rendered SVG contains an external href")
            if "@import" in lowered or ("url(" in lowered and "url(#" not in lowered):
                findings.append("rendered SVG contains external CSS")
    return sorted(set(findings))


class NodeMermaidRuntime(MermaidRuntime):
    """JSONL bridge to a reusable, network-isolated Playwright Chromium worker."""

    def __init__(self, runtime_dir: str | Path | None = None):
        self.runtime_dir = Path(runtime_dir) if runtime_dir else default_runtime_dir()
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._next_id = 0
        atexit.register(self.close)

    def _start(self) -> subprocess.Popen[str]:
        worker = self.runtime_dir / "worker.mjs"
        if not worker.is_file():
            raise RuntimeError(f"Mermaid worker not found: {worker}")
        process = subprocess.Popen(
            ["node", str(worker)],
            cwd=self.runtime_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self._process = process
        return process

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                process = self._start()
            self._next_id += 1
            request_id = str(self._next_id)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps({"id": request_id, "code": code}) + "\n")
            process.stdin.flush()
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    break
                line = process.stdout.readline()
                if not line:
                    break
                payload = json.loads(line)
                if payload.get("id") != request_id:
                    continue
                if not payload.get("ok"):
                    selector.close()
                    return RuntimeResult(
                        syntax_valid=bool(payload.get("syntaxValid")),
                        render_valid=False,
                        error=payload.get("error", "Mermaid runtime failed"),
                    )
                png = base64.b64decode(payload["png"]) if payload.get("png") else None
                selector.close()
                return RuntimeResult(
                    syntax_valid=True,
                    render_valid=True,
                    diagram_type=payload.get("diagramType"),
                    svg=payload.get("svg"),
                    png=png,
                )
            selector.close()
            self.close()
            return RuntimeResult(False, False, error=f"Mermaid runtime exceeded {timeout_seconds}s")

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            if process is None or process.poll() is not None:
                return
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)


class CandidateValidator:
    def __init__(
        self,
        runtime: MermaidRuntime,
        profile: SecurityProfile,
        max_chars: int = 50_000,
        max_lines: int = 5_000,
    ):
        self.runtime = runtime
        self.profile = profile
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.scanner = MermaidSecurityScanner(profile)

    def validate(self, code: str, timeout_seconds: float) -> ValidationOutcome:
        if len(code) > self.max_chars or code.count("\n") + 1 > self.max_lines:
            return ValidationOutcome(
                RuntimeResult(False, False, error="Mermaid source exceeds resource limits"),
                ["resource_limit: source is too large"],
            )
        report = self.scanner.scan(code)
        if not report.safe:
            return ValidationOutcome(
                RuntimeResult(False, False, error="security scan failed"),
                [f"security:{item.rule}:line {item.line}" for item in report.findings],
            )
        runtime_result = self.runtime.validate_and_render(code, timeout_seconds)
        warnings: list[str] = []
        if runtime_result.render_valid and runtime_result.svg:
            warnings.extend(inspect_svg(runtime_result.svg, self.profile))
            if warnings:
                runtime_result = RuntimeResult(
                    syntax_valid=runtime_result.syntax_valid,
                    render_valid=False,
                    diagram_type=runtime_result.diagram_type,
                    error="rendered SVG failed security inspection",
                )
        return ValidationOutcome(runtime_result, warnings)
