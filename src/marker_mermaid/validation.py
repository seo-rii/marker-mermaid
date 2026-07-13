"""Mermaid security, real browser parse/render, and SVG inspection."""

from __future__ import annotations

import atexit
import base64
import json
import os
import re
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
from marker_mermaid.models import (
    MermaidCandidate,
    ValidatedArtifactCertificate,
    _issue_validated_artifact_certificate,
)
from marker_mermaid.protocols import MermaidRuntime, RuntimeResult
from marker_mermaid.render_artifacts import MAX_RENDER_BYTES, png_inspection_error
from marker_mermaid.security import MermaidSecurityScanner

MAX_RUNTIME_RESPONSE_BYTES = 64_000_000


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    runtime: RuntimeResult
    warnings: list[str]
    certificate: ValidatedArtifactCertificate | None = None


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

    def has_external_css(value: str) -> bool:
        lowered = value.casefold()
        if "@import" in lowered:
            return True
        for match in re.finditer(r"url\s*\(\s*(['\"]?)(.*?)\1\s*\)", lowered):
            if not match.group(2).strip().startswith("#"):
                return True
        return False

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
            if has_external_css(lowered):
                findings.append("rendered SVG contains external CSS")
        if tag == "style" and has_external_css("".join(element.itertext())):
            findings.append("rendered SVG contains external CSS")
    return sorted(set(findings))


class NodeMermaidRuntime(MermaidRuntime):
    """JSONL bridge to a reusable, network-isolated Playwright Chromium worker."""

    def __init__(self, runtime_dir: str | Path | None = None):
        self.runtime_dir = Path(runtime_dir) if runtime_dir else default_runtime_dir()
        self._process: subprocess.Popen[bytes] | None = None
        self._process_group_id: int | None = None
        self._stdout_buffer = bytearray()
        self._lock = threading.RLock()
        self._next_id = 0
        atexit.register(self.close)

    def _start(self) -> subprocess.Popen[bytes]:
        worker = self.runtime_dir / "worker.mjs"
        if not worker.is_file():
            raise RuntimeError(f"Mermaid worker not found: {worker}")
        process = subprocess.Popen(
            ["node", str(worker)],
            cwd=self.runtime_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            start_new_session=True,
        )
        self._process = process
        self._process_group_id = process.pid
        self._stdout_buffer.clear()
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        return process

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                process = self._start()
            self._next_id += 1
            request_id = str(self._next_id)
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write((json.dumps({"id": request_id, "code": code}) + "\n").encode())
            process.stdin.flush()
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout_seconds
            try:
                while time.monotonic() < deadline:
                    while b"\n" in self._stdout_buffer:
                        raw_line, _, remainder = self._stdout_buffer.partition(b"\n")
                        self._stdout_buffer = bytearray(remainder)
                        try:
                            payload = json.loads(raw_line)
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            self.close()
                            return RuntimeResult(
                                False,
                                False,
                                error=f"Mermaid runtime returned invalid JSON: {exc}",
                            )
                        if payload.get("id") != request_id:
                            continue
                        if not payload.get("ok"):
                            return RuntimeResult(
                                syntax_valid=bool(payload.get("syntaxValid")),
                                render_valid=False,
                                error=payload.get("error", "Mermaid runtime failed"),
                            )
                        try:
                            png = (
                                base64.b64decode(payload["png"], validate=True)
                                if payload.get("png")
                                else None
                            )
                        except (ValueError, TypeError) as exc:
                            self.close()
                            return RuntimeResult(
                                False,
                                False,
                                error=f"Mermaid runtime returned invalid PNG data: {exc}",
                            )
                        return RuntimeResult(
                            syntax_valid=True,
                            render_valid=True,
                            diagram_type=payload.get("diagramType"),
                            svg=payload.get("svg"),
                            png=png,
                        )
                    events = selector.select(max(0, deadline - time.monotonic()))
                    if not events:
                        break
                    try:
                        chunk = os.read(process.stdout.fileno(), 65_536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    self._stdout_buffer.extend(chunk)
                    if len(self._stdout_buffer) > MAX_RUNTIME_RESPONSE_BYTES:
                        self.close()
                        return RuntimeResult(
                            False,
                            False,
                            error="Mermaid runtime response exceeds the size limit",
                        )
            finally:
                selector.close()
            self.close()
            return RuntimeResult(False, False, error=f"Mermaid runtime exceeded {timeout_seconds}s")

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            process_group_id, self._process_group_id = self._process_group_id, None
            self._stdout_buffer.clear()
            if process_group_id is None:
                return
            try:
                os.killpg(process_group_id, signal.SIGTERM)
                if process is not None and process.poll() is None:
                    process.wait(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                with suppress(ProcessLookupError):
                    os.killpg(process_group_id, signal.SIGKILL)
                if process is not None and process.poll() is None:
                    process.wait(timeout=3)


class CandidateValidator:
    __slots__ = ("runtime", "_profile", "max_chars", "max_lines", "_scanner")

    def __init__(
        self,
        runtime: MermaidRuntime,
        profile: SecurityProfile,
        max_chars: int = 50_000,
        max_lines: int = 5_000,
    ):
        self.runtime = runtime
        self._profile = profile
        self.max_chars = max_chars
        self.max_lines = max_lines
        self._scanner = MermaidSecurityScanner(profile)

    @property
    def profile(self) -> SecurityProfile:
        return self._profile

    def validate(self, code: str, timeout_seconds: float) -> ValidationOutcome:
        profile = self._profile
        scanner = self._scanner
        if not (
            type(profile) is SecurityProfile
            and type(scanner) is MermaidSecurityScanner
            and scanner.profile is profile
        ):
            return ValidationOutcome(
                RuntimeResult(False, False, error="validator security profile is inconsistent"),
                ["security:validator_profile: validator security profile is inconsistent"],
            )
        if type(code) is not str:
            return ValidationOutcome(
                RuntimeResult(False, False, error="Mermaid source must be a plain string"),
                ["security:source_type: Mermaid source must be a plain string"],
            )
        if len(code) > self.max_chars or code.count("\n") + 1 > self.max_lines:
            return ValidationOutcome(
                RuntimeResult(False, False, error="Mermaid source exceeds resource limits"),
                ["resource_limit: source is too large"],
            )
        report = scanner.scan(code)
        if not report.safe:
            return ValidationOutcome(
                RuntimeResult(False, False, error="security scan failed"),
                [f"security:{item.rule}:line {item.line}" for item in report.findings],
            )
        runtime_result = self.runtime.validate_and_render(code, timeout_seconds)
        warnings: list[str] = []
        if runtime_result.render_valid:
            if type(runtime_result.svg) is not str or not runtime_result.svg.strip():
                warnings.append("rendered SVG artifact is missing or empty")
                runtime_result = RuntimeResult(
                    syntax_valid=runtime_result.syntax_valid,
                    render_valid=False,
                    diagram_type=runtime_result.diagram_type,
                    error=(
                        "Mermaid runtime reported render success without a non-empty SVG artifact"
                    ),
                )
                return ValidationOutcome(runtime_result, warnings)
            if len(runtime_result.svg.encode("utf-8")) > MAX_RENDER_BYTES:
                warnings.append("rendered SVG artifact exceeds the byte limit")
                runtime_result = RuntimeResult(
                    syntax_valid=runtime_result.syntax_valid,
                    render_valid=False,
                    diagram_type=runtime_result.diagram_type,
                    error="rendered SVG exceeds the artifact size limit",
                )
                return ValidationOutcome(runtime_result, warnings)
            svg_warnings = inspect_svg(runtime_result.svg, profile)
            warnings.extend(svg_warnings)
            if svg_warnings:
                runtime_result = RuntimeResult(
                    syntax_valid=runtime_result.syntax_valid,
                    render_valid=False,
                    diagram_type=runtime_result.diagram_type,
                    error="rendered SVG failed security inspection",
                )
                return ValidationOutcome(runtime_result, warnings)
            if runtime_result.png is not None:
                png_error = png_inspection_error(runtime_result.png)
                if png_error is not None:
                    warnings.append(f"rendered PNG artifact was omitted: {png_error}")
                    runtime_result = RuntimeResult(
                        syntax_valid=runtime_result.syntax_valid,
                        render_valid=runtime_result.render_valid,
                        diagram_type=runtime_result.diagram_type,
                        svg=runtime_result.svg,
                    )
        certificate = None
        if runtime_result.render_valid and not runtime_result.syntax_valid:
            warnings.append("runtime reported render success without syntax validation")
            runtime_result = RuntimeResult(
                syntax_valid=False,
                render_valid=False,
                diagram_type=runtime_result.diagram_type,
                error="runtime render result lacks successful syntax validation",
            )
        if runtime_result.syntax_valid and runtime_result.render_valid:
            if not code.endswith("\n"):
                warnings.append(
                    "validated Mermaid source has no trailing newline; "
                    "automatic publication certification was withheld"
                )
            elif type(runtime_result.svg) is str and (
                runtime_result.diagram_type is None or type(runtime_result.diagram_type) is str
            ):
                certificate = _issue_validated_artifact_certificate(
                    code=code,
                    svg=runtime_result.svg,
                    png=runtime_result.png,
                    profile=profile,
                    runtime_diagram_type=runtime_result.diagram_type,
                )
        return ValidationOutcome(runtime_result, warnings, certificate)

    def seal_candidate(
        self,
        candidate: MermaidCandidate,
        outcome: ValidationOutcome | None,
    ) -> None:
        """Install a receipt only from this validator's exact accepted outcome."""

        certificate = outcome.certificate if type(outcome) is ValidationOutcome else None
        if (
            type(certificate) is not ValidatedArtifactCertificate
            or type(self._profile) is not SecurityProfile
            or type(self._scanner) is not MermaidSecurityScanner
            or self._scanner.profile is not self._profile
            or certificate.security_profile != self._profile
        ):
            certificate = None
        candidate._seal_validation_receipt(certificate)
