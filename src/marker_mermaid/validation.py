"""Mermaid security, real browser parse/render, and SVG inspection."""

from __future__ import annotations

import base64
import ctypes
import errno
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
import weakref
import xml.etree.ElementTree as ET
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tinycss2

from marker_mermaid.config import SecurityProfile
from marker_mermaid.models import (
    MermaidCandidate,
    ValidatedArtifactCertificate,
    _issue_validated_artifact_certificate,
)
from marker_mermaid.protocols import MermaidRuntime, RuntimeResult
from marker_mermaid.render_artifacts import (
    MAX_RENDER_BASE64_CHARS,
    MAX_RENDER_BYTES,
    RenderArtifactLimits,
    png_inspection_error,
)
from marker_mermaid.resource_limits import (
    MAX_RUNTIME_WORKER_CPU_SECONDS,
    MAX_RUNTIME_WORKER_DATA_BYTES,
)
from marker_mermaid.security import MermaidSecurityScanner

MAX_RUNTIME_RESPONSE_BYTES = 64_000_000
MAX_RUNTIME_STDERR_BYTES = 64_000
MAX_PNG_OMITTED_REASON_CHARS = 256


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    runtime: RuntimeResult
    warnings: list[str]
    certificate: ValidatedArtifactCertificate | None = None


def normalize_runtime_dir(path: str | Path) -> Path:
    """Bind a runtime directory to the caller's current directory once."""
    return Path(path).expanduser().resolve()


def default_runtime_dir() -> Path:
    override = os.environ.get("MARKER_MERMAID_RUNTIME_DIR")
    if override:
        return normalize_runtime_dir(override)
    packaged = Path(__file__).resolve().parent / "runtime"
    if (packaged / "node_modules" / "mermaid").is_dir():
        return packaged
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return normalize_runtime_dir(cache_root / "marker-mermaid" / "runtime")


def inspect_svg(
    svg: str,
    profile: SecurityProfile,
    *,
    diagram_type: str | None = None,
) -> list[str]:
    findings: list[str] = []
    try:
        parser = ET.XMLPullParser(events=("start", "pi"))
        parser.feed(svg)
        parser.close()
        root = None
        for event, element in parser.read_events():
            if event == "pi":
                findings.append("rendered SVG contains a processing instruction")
            elif root is None:
                root = element
        assert root is not None
    except ET.ParseError as exc:
        return [f"rendered output is not XML: {exc}"]
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        findings.append("rendered output does not have an SVG root")
    if view_box := root.get("viewBox"):
        try:
            dimensions = [float(part) for part in re.split(r"[\s,]+", view_box.strip())]
        except ValueError:
            dimensions = []
        if not (
            len(dimensions) == 4
            and all(math.isfinite(value) for value in dimensions)
            and dimensions[2] > 0
            and dimensions[3] > 0
        ):
            findings.append("rendered SVG has an invalid viewBox")
    else:
        length = re.compile(
            r"(?P<number>\+?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
            r"(?:%|px|pt|pc|mm|cm|in|em|ex|rem)?"
        )
        for attribute in ("width", "height"):
            value = (root.get(attribute) or "").strip()
            match = length.fullmatch(value)
            if match is None or not 0 < float(match["number"]) < math.inf:
                findings.append("rendered SVG has no positive dimensions")
                break
    if diagram_type is None:
        is_gantt = (root.get("aria-roledescription") or "").strip().casefold() == "gantt"
    else:
        is_gantt = type(diagram_type) is str and diagram_type.strip().casefold() == "gantt"
    forbidden = {
        "script",
        "iframe",
        "object",
        "embed",
        "link",
        "img",
        "animate",
        "animatemotion",
        "animatetransform",
        "set",
        "discard",
    }
    geometry_attributes = {
        "cx",
        "cy",
        "d",
        "height",
        "points",
        "r",
        "rx",
        "ry",
        "transform",
        "viewbox",
        "width",
        "x",
        "x1",
        "x2",
        "y",
        "y1",
        "y2",
    }
    if profile == SecurityProfile.STRICT:
        forbidden.add("foreignobject")

    def has_external_css(value: str) -> bool:
        # CSS escapes are decoded by the tokenizer, including function names,
        # quoted URLs and @import. Inspect nested blocks, not serialized text.
        pending = list(tinycss2.parse_component_value_list(value, skip_comments=True))
        while pending:
            token = pending.pop()
            if token.type == "at-keyword" and token.lower_value == "import":
                return True
            if token.type == "url" and not token.value.strip().startswith("#"):
                return True
            if token.type == "error" and token.kind in {"bad-url", "bad-string"}:
                return True
            if token.type == "function":
                if token.lower_name == "url":
                    args = [item for item in token.arguments if item.type != "whitespace"]
                    if not (
                        len(args) == 1
                        and args[0].type == "string"
                        and args[0].value.strip().startswith("#")
                    ):
                        return True
                # These image-producing functions may load bare string URLs.
                if token.lower_name in {"image", "image-set", "-webkit-image-set", "src"}:
                    return True
                pending.extend(token.arguments)
            elif hasattr(token, "content"):
                pending.extend(token.content)
        return False

    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag.casefold() in forbidden:
            findings.append(f"rendered SVG contains forbidden <{tag}>")
        if is_gantt and tag == "rect":
            classes = set((element.get("class") or "").split())
            if "task" in classes:
                try:
                    width = float(element.get("width", ""))
                    height = float(element.get("height", ""))
                except ValueError:
                    width = height = 0
                if not (
                    math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0
                ):
                    findings.append("rendered Gantt contains a non-visible task rectangle")
        for raw_name, value in element.attrib.items():
            name = raw_name.rsplit("}", 1)[-1].lower()
            lowered = value.strip().lower()
            if name.startswith("on"):
                findings.append(f"rendered SVG contains event handler {name}")
            if name == "href" and lowered and not lowered.startswith("#"):
                findings.append("rendered SVG contains an external href")
            if name in {"src", "srcset"} and lowered:
                findings.append(f"rendered SVG contains external {name}")
            if has_external_css(lowered):
                findings.append("rendered SVG contains external CSS")
            if name in geometry_attributes and ("nan" in lowered or "infinity" in lowered):
                findings.append(f"rendered SVG contains non-finite geometry attribute {name}")
        if tag == "style" and has_external_css("".join(element.itertext())):
            findings.append("rendered SVG contains external CSS")
    return sorted(set(findings))


class _DarwinSiginfo(ctypes.Structure):
    # Darwin's public siginfo_t ABI, shared by arm64 and x86_64.
    # https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/signal.h
    _fields_ = [
        ("si_signo", ctypes.c_int),
        ("si_errno", ctypes.c_int),
        ("si_code", ctypes.c_int),
        ("si_pid", ctypes.c_int),
        ("si_uid", ctypes.c_uint),
        ("si_status", ctypes.c_int),
        ("si_addr", ctypes.c_void_p),
        ("si_value", ctypes.c_void_p),
        ("si_band", ctypes.c_long),
        ("_pad", ctypes.c_ulong * 7),
    ]


@lru_cache(maxsize=1)
def _darwin_waitid():
    # Python exposes os.waitid on macOS only from 3.13. Earlier supported
    # versions can use libc, provided siginfo_t is zeroed for WNOHANG.
    waitid = ctypes.CDLL(None, use_errno=True).waitid
    waitid.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_DarwinSiginfo), ctypes.c_int]
    waitid.restype = ctypes.c_int
    return waitid


def _runtime_worker_exit_code(process: subprocess.Popen[bytes]) -> int | None:
    """Observe exit without freeing the PID that owns the process group."""
    if process.returncode is not None:
        raise ChildProcessError("runtime worker was already reaped")
    try:
        if hasattr(os, "waitid"):
            result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if result is None:
                return None
            return result.si_status if result.si_code == os.CLD_EXITED else -result.si_status
        if sys.platform != "darwin":
            raise RuntimeError("runtime cleanup requires non-reaping waitid with WNOWAIT")
        info = _DarwinSiginfo()
        # Darwin sys/wait.h: P_PID=1, WEXITED=4, WNOHANG=1, WNOWAIT=32.
        while _darwin_waitid()(1, process.pid, ctypes.byref(info), 0x25) != 0:
            error = ctypes.get_errno()
            if error == errno.EINTR:
                continue
            raise OSError(error, os.strerror(error))
        if info.si_pid == 0:
            return None
        return info.si_status if info.si_code == 1 else -info.si_status  # CLD_EXITED=1
    except ChildProcessError:
        # Match Popen's ECHILD convention. Prevent its later destructor from
        # polling a PID that an external waitpid may already have made reusable.
        process.returncode = 0
        raise


def _signal_runtime_process(
    process: subprocess.Popen[bytes], process_group_id: int | None, signum: int
) -> None:
    """Signal only while the original leader still reserves this process-group ID."""
    _runtime_worker_exit_code(process)
    try:
        if process_group_id is not None:
            os.killpg(process_group_id, signum)
        else:  # pragma: no cover - partial-start fallback
            os.kill(process.pid, signum)
    except PermissionError:
        # Darwin killpg skips zombies and reports EPERM for a zombie-only group.
        # Its unreaped leader must still pin the PGID until the final signal.
        if sys.platform != "darwin" or _runtime_worker_exit_code(process) is None:
            raise


def _close_runtime_process(
    process: subprocess.Popen[bytes], process_group_id: int | None
) -> None:
    """Keep the leader waitable until every signal to its owned group is sent."""
    owned = True

    try:
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        graceful_deadline = time.monotonic() + 1
        while _runtime_worker_exit_code(process) is None:
            remaining = graceful_deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.02, remaining))

        _signal_runtime_process(process, process_group_id, signal.SIGTERM)
        terminate_deadline = time.monotonic() + 3
        while time.monotonic() < terminate_deadline:
            if _runtime_worker_exit_code(process) is not None:
                # The exited leader still pins the PGID. Give surviving children
                # a short TERM grace, then remove them before releasing that PID.
                terminate_deadline = min(terminate_deadline, time.monotonic() + 0.1)
            time.sleep(min(0.02, max(0, terminate_deadline - time.monotonic())))
        _signal_runtime_process(process, process_group_id, signal.SIGKILL)
    except ChildProcessError:
        # A caller that reaped the private Popen surrendered group ownership.
        # Never signal the numeric PGID, which may now belong to another session.
        owned = False
    except ProcessLookupError:
        pass
    finally:
        if owned:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=3)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with suppress(OSError):
                    stream.close()


class NodeMermaidRuntime(MermaidRuntime):
    """JSONL bridge to a reusable, network-isolated Playwright Chromium worker."""

    def __init__(
        self,
        runtime_dir: str | Path | None = None,
        *,
        render_limits: RenderArtifactLimits | None = None,
    ):
        if os.name != "posix":
            raise RuntimeError(
                "the Mermaid browser runtime supports POSIX platforms only "
                "(Linux and macOS)"
            )
        if not hasattr(os, "waitid"):
            if sys.platform != "darwin":
                raise RuntimeError("runtime cleanup requires non-reaping waitid with WNOWAIT")
            _darwin_waitid()
        self.runtime_dir = (
            normalize_runtime_dir(runtime_dir)
            if runtime_dir is not None
            else default_runtime_dir()
        )
        if render_limits is not None and type(render_limits) is not RenderArtifactLimits:
            raise TypeError("render_limits must be an exact RenderArtifactLimits")
        self.render_limits = render_limits or RenderArtifactLimits()
        self._process: subprocess.Popen[bytes] | None = None
        self._process_group_id: int | None = None
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._stderr_truncated = False
        self._lock = threading.RLock()
        self._next_id = 0
        self._finalizer: weakref.finalize | None = None

    def __enter__(self) -> NodeMermaidRuntime:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _start(self) -> subprocess.Popen[bytes]:
        worker = self.runtime_dir / "worker.mjs"
        if not worker.is_file():
            raise RuntimeError(f"Mermaid worker not found: {worker}")
        launcher = Path(__file__).with_name("runtime_launcher.py")
        if not launcher.is_file():  # pragma: no cover - installed-package invariant
            raise RuntimeError(f"Mermaid runtime launcher not found: {launcher}")
        process = subprocess.Popen(
            [
                sys.executable,
                str(launcher),
                str(MAX_RUNTIME_WORKER_DATA_BYTES),
                str(MAX_RUNTIME_WORKER_CPU_SECONDS),
                "node",
                str(worker),
            ],
            cwd=self.runtime_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
        self._process = process
        self._process_group_id = process.pid
        # Neither the callback nor its arguments may retain the runtime instance.
        # Each start gets a fresh finalizer so explicit close still permits reuse.
        self._finalizer = weakref.finalize(self, _close_runtime_process, process, process.pid)
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()
        self._stderr_truncated = False
        assert (
            process.stdin is not None
            and process.stdout is not None
            and process.stderr is not None
        )
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        os.set_blocking(process.stderr.fileno(), False)
        return process

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        with self._lock:
            if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
                return RuntimeResult(
                    False,
                    False,
                    error="Mermaid runtime timeout must be finite and positive",
                )
            process = self._process
            exited = process is None
            if process is not None:
                try:
                    exited = _runtime_worker_exit_code(process) is not None
                except ChildProcessError:
                    exited = True
            if exited:
                if process is not None:
                    self.close()
                try:
                    process = self._start()
                except (OSError, RuntimeError) as exc:
                    self.close()
                    return RuntimeResult(
                        False,
                        False,
                        error=(
                            "Mermaid runtime could not start; run `marker-mermaid doctor`: "
                            f"{exc}"
                        ),
                    )
            self._next_id += 1
            request_id = str(self._next_id)
            assert (
                process.stdin is not None
                and process.stdout is not None
                and process.stderr is not None
            )
            request = (
                json.dumps(
                    {
                        "id": request_id,
                        "code": code,
                        "limits": self.render_limits.worker_payload(),
                    }
                )
                + "\n"
            ).encode()
            request_offset = 0
            selector = selectors.DefaultSelector()
            deadline = time.monotonic() + timeout_seconds
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
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
                        if type(payload) is not dict:
                            self.close()
                            return RuntimeResult(
                                False,
                                False,
                                error="Mermaid runtime returned a non-object response",
                            )
                        if payload.get("id") != request_id:
                            detail = payload.get("error")
                            self.close()
                            return RuntimeResult(
                                False,
                                False,
                                error=(
                                    "Mermaid runtime returned a mismatched response id"
                                    + (f": {detail}" if isinstance(detail, str) and detail else "")
                                ),
                            )
                        if not payload.get("ok"):
                            return RuntimeResult(
                                syntax_valid=bool(payload.get("syntaxValid")),
                                render_valid=False,
                                error=payload.get("error", "Mermaid runtime failed"),
                            )
                        png_omitted_reason = payload.get("pngOmittedReason")
                        if png_omitted_reason is not None and not (
                            type(png_omitted_reason) is str
                            and bool(png_omitted_reason.strip())
                            and len(png_omitted_reason) <= MAX_PNG_OMITTED_REASON_CHARS
                        ):
                            self.close()
                            return RuntimeResult(
                                False,
                                False,
                                error=(
                                    "Mermaid runtime returned an invalid PNG omission reason"
                                ),
                            )
                        encoded_png = payload.get("png")
                        if encoded_png is not None and png_omitted_reason is not None:
                            self.close()
                            return RuntimeResult(
                                False,
                                False,
                                error=(
                                    "Mermaid runtime returned both PNG data and an omission reason"
                                ),
                            )
                        if encoded_png is not None and (
                            type(encoded_png) is not str
                            or len(encoded_png) > MAX_RENDER_BASE64_CHARS
                        ):
                            self.close()
                            return RuntimeResult(
                                False,
                                False,
                                error="Mermaid runtime returned oversized PNG data",
                            )
                        try:
                            png = (
                                base64.b64decode(encoded_png, validate=True)
                                if encoded_png is not None
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
                            png_omitted_reason=png_omitted_reason,
                        )
                    events = selector.select(max(0, deadline - time.monotonic()))
                    if not events:
                        break
                    for key, _ in events:
                        if key.data == "stdin":
                            try:
                                written = os.write(
                                    process.stdin.fileno(), request[request_offset:]
                                )
                            except (BrokenPipeError, OSError) as exc:
                                try:
                                    stderr_chunk = os.read(process.stderr.fileno(), 65_536)
                                except (BlockingIOError, OSError):
                                    stderr_chunk = b""
                                remaining = MAX_RUNTIME_STDERR_BYTES - len(self._stderr_buffer)
                                if stderr_chunk and remaining > 0:
                                    self._stderr_buffer.extend(stderr_chunk[:remaining])
                                if len(stderr_chunk) > max(0, remaining):
                                    self._stderr_truncated = True
                                detail = self._stderr_buffer.decode("utf-8", errors="replace")
                                detail = " ".join(detail.split())
                                self.close()
                                return RuntimeResult(
                                    False,
                                    False,
                                    error=(
                                        "Mermaid runtime could not accept the request; "
                                        "run `marker-mermaid doctor`"
                                        + (f": {detail}" if detail else f": {exc}")
                                    ),
                                )
                            request_offset += written
                            if request_offset >= len(request):
                                selector.unregister(process.stdin)
                        elif key.data == "stderr":
                            try:
                                chunk = os.read(process.stderr.fileno(), 65_536)
                            except (BlockingIOError, OSError):
                                continue
                            if not chunk:
                                selector.unregister(process.stderr)
                                continue
                            remaining = MAX_RUNTIME_STDERR_BYTES - len(self._stderr_buffer)
                            if chunk and remaining > 0:
                                self._stderr_buffer.extend(chunk[:remaining])
                            if len(chunk) > max(0, remaining):
                                self._stderr_truncated = True
                        else:
                            try:
                                chunk = os.read(process.stdout.fileno(), 65_536)
                            except BlockingIOError:
                                continue
                            if not chunk:
                                try:
                                    stderr_chunk = os.read(process.stderr.fileno(), 65_536)
                                except (BlockingIOError, OSError):
                                    stderr_chunk = b""
                                remaining = MAX_RUNTIME_STDERR_BYTES - len(self._stderr_buffer)
                                if stderr_chunk and remaining > 0:
                                    self._stderr_buffer.extend(stderr_chunk[:remaining])
                                if len(stderr_chunk) > max(0, remaining):
                                    self._stderr_truncated = True
                                detail = self._stderr_buffer.decode("utf-8", errors="replace")
                                detail = " ".join(detail.split())
                                exit_code = None
                                with suppress(ChildProcessError):
                                    exit_code = _runtime_worker_exit_code(process)
                                self.close()
                                return RuntimeResult(
                                    False,
                                    False,
                                    error=(
                                        "Mermaid runtime exited unexpectedly"
                                        + (
                                            f" with exit code {exit_code}"
                                            if exit_code is not None
                                            else ""
                                        )
                                        + "; run `marker-mermaid doctor`"
                                        + (f": {detail}" if detail else "")
                                    ),
                                )
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
            self._stderr_buffer.clear()
            self._stderr_truncated = False
            if self._finalizer is not None:
                self._finalizer.detach()
                self._finalizer = None
            if process is None:
                return
            _close_runtime_process(process, process_group_id)


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
            if runtime_result.png_omitted_reason is not None:
                warnings.append(
                    "rendered PNG artifact was omitted before screenshot: "
                    f"{runtime_result.png_omitted_reason}"
                )
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
            svg_warnings = inspect_svg(
                runtime_result.svg,
                profile,
                diagram_type=runtime_result.diagram_type,
            )
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
