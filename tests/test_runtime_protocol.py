from __future__ import annotations

import ctypes
import errno
import gc
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
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


@pytest.mark.parametrize("runtime_path", ["runtime", "공백 있는 runtime", "~/runtime"])
def test_runtime_directory_is_resolved_before_worker_changes_directory(
    monkeypatch, tmp_path, runtime_path
):
    runtime_dir = tmp_path / runtime_path.removeprefix("~/")
    runtime_dir.mkdir()
    _worker(
        runtime_dir,
        """
import readline from "node:readline";
const rl = readline.createInterface({input: process.stdin});
rl.on("line", line => {
  const request = JSON.parse(line);
  process.stdout.write(JSON.stringify({id: request.id, ok: true,
    svg: "<svg viewBox='0 0 1 1'/>"}) + "\\n");
});
""",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime = NodeMermaidRuntime(runtime_path)
    monkeypatch.chdir(tmp_path.parent)

    try:
        # This checks path resolution, so allow the first Node launch on a cold
        # CI runner its own startup budget. Deadline tests use short limits below.
        result = runtime.validate_and_render("flowchart LR\nA-->B", 10)
    finally:
        runtime.close()

    assert runtime.runtime_dir == runtime_dir.resolve()
    assert result.render_valid, result.error


@pytest.mark.parametrize("source", ["override", "cache"])
def test_default_runtime_directory_normalizes_environment_paths(
    monkeypatch, tmp_path, source
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MARKER_MERMAID_RUNTIME_DIR", raising=False)
    if source == "override":
        monkeypatch.setenv("MARKER_MERMAID_RUNTIME_DIR", "relative runtime")
        expected = tmp_path / "relative runtime"
    else:
        monkeypatch.setenv("XDG_CACHE_HOME", "relative cache")
        # The fallback is used only when no packaged dependency installation exists.
        original_is_dir = Path.is_dir
        monkeypatch.setattr(
            Path,
            "is_dir",
            lambda path: False if path.name == "mermaid" else original_is_dir(path),
        )
        expected = tmp_path / "relative cache" / "marker-mermaid" / "runtime"

    assert validation.default_runtime_dir() == expected.resolve()


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


def _pid_is_running(pid: int) -> bool:
    # An orphan may remain as an inert zombie until the system reaper collects it.
    # The regression contract is that no owned descendant remains running.
    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(status) and not status.startswith("Z")


def _observe_worker_exit(process, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = validation._runtime_worker_exit_code(process)  # noqa: SLF001
        if result is not None:
            return result
        time.sleep(0.01)
    raise AssertionError("worker did not exit")


@pytest.mark.parametrize("platform,exited", [("darwin", True), ("linux", True), ("darwin", False)])
def test_zombie_only_group_permission_error_is_specific_to_darwin(
    monkeypatch, platform, exited
):
    source = "pass" if exited else "import time; time.sleep(30)"
    process = subprocess.Popen([sys.executable, "-c", source], start_new_session=True)
    if exited:
        assert _observe_worker_exit(process) == 0
    monkeypatch.setattr(validation.sys, "platform", platform)

    def deny_signal(*_args):
        raise PermissionError(errno.EPERM, "mock Darwin zombie-only group")

    monkeypatch.setattr(validation.os, "killpg", deny_signal)
    try:
        if platform == "darwin" and exited:
            validation._signal_runtime_process(process, process.pid, signal.SIGTERM)  # noqa: SLF001
        else:
            with pytest.raises(PermissionError):
                validation._signal_runtime_process(process, process.pid, signal.SIGTERM)  # noqa: SLF001
        assert process.returncode is None
    finally:
        if not exited:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize("reap_with_popen", [True, False])
def test_runtime_close_never_signals_an_externally_reaped_worker(
    monkeypatch, tmp_path, reap_with_popen
):
    _worker(tmp_path, "process.exit(0);")
    runtime = NodeMermaidRuntime(tmp_path)
    process = runtime._start()  # noqa: SLF001 - simulate lost private child ownership
    if reap_with_popen:
        assert process.wait(timeout=5) == 0
    else:
        assert os.waitpid(process.pid, 0) == (process.pid, 0)
    signals = []
    monkeypatch.setattr(validation.os, "killpg", lambda *args: signals.append(args))

    runtime.close()

    assert signals == []
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


@pytest.mark.parametrize("worker_exit", ["stdin_eof", "before_request", "response_eof"])
def test_worker_identity_remains_waitable_until_the_last_group_signal(
    monkeypatch, tmp_path, worker_exit
):
    sources = {
        "stdin_eof": "process.stdin.resume();",
        "before_request": "process.exit(0);",
        "response_eof": "process.stdin.once('data', () => process.exit(7));",
    }
    _worker(tmp_path, sources[worker_exit])
    runtime = NodeMermaidRuntime(tmp_path)
    process = runtime._start()  # noqa: SLF001 - assert the owned PID cannot be reused
    original_killpg = os.killpg
    original_start = runtime._start  # noqa: SLF001 - observe replacement worker too
    processes = {process.pid: process}
    signals = []

    def start_and_remember():
        replacement = original_start()
        processes[replacement.pid] = replacement
        return replacement

    def signal_still_owned_group(process_group_id, signum):
        owned_process = processes[process_group_id]
        # ECHILD here means the PID is already reusable: no signal is safe.
        validation._runtime_worker_exit_code(owned_process)  # noqa: SLF001
        signals.append(signum)
        original_killpg(process_group_id, signum)

    monkeypatch.setattr(runtime, "_start", start_and_remember)
    monkeypatch.setattr(validation.os, "killpg", signal_still_owned_group)
    try:
        if worker_exit == "before_request":
            assert _observe_worker_exit(process) == 0
            # Restart the already-exited worker through the real request path.
            _worker(tmp_path, "process.stdin.once('data', () => process.exit(7));")
        if worker_exit != "stdin_eof":
            result = runtime.validate_and_render("flowchart LR\nA-->B", 5)
            assert not result.render_valid
        else:
            runtime.close()
        assert signal.SIGTERM in signals
        assert signal.SIGKILL in signals
        assert process.poll() is not None
    finally:
        monkeypatch.setattr(validation.os, "killpg", original_killpg)
        runtime.close()


def test_runtime_requires_non_reaping_child_observation(monkeypatch, tmp_path):
    monkeypatch.delattr(validation.os, "waitid", raising=False)
    monkeypatch.setattr(validation.sys, "platform", "unsupported")

    with pytest.raises(RuntimeError, match="WNOWAIT"):
        NodeMermaidRuntime(tmp_path)


@pytest.mark.parametrize("exit_status", [None, 0, -15, "reaped"])
def test_darwin_waitid_fallback_preserves_exit_and_ownership_contract(
    monkeypatch, tmp_path, exit_status
):
    # Exercise the macOS <=3.12 branch on every CI platform; the macOS job also
    # calls the real libc adapter through the complete process regression suite.
    calls = []

    def native_waitid(id_type, pid, pointer, flags):
        assert (id_type, flags) == (1, 0x25)
        info = ctypes.cast(pointer, ctypes.POINTER(validation._DarwinSiginfo)).contents
        assert info.si_pid == 0
        calls.append(pid)
        if exit_status == "reaped":
            ctypes.set_errno(errno.ECHILD)
            return -1
        if exit_status is not None:
            info.si_pid = pid
            info.si_code = 1 if exit_status >= 0 else 2
            info.si_status = abs(exit_status)
        return 0

    monkeypatch.delattr(validation.os, "waitid", raising=False)
    monkeypatch.setattr(validation.sys, "platform", "darwin")
    monkeypatch.setattr(validation, "_darwin_waitid", lambda: native_waitid)
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        if exit_status == "reaped":
            with pytest.raises(ChildProcessError):
                validation._runtime_worker_exit_code(process)  # noqa: SLF001
        else:
            assert validation._runtime_worker_exit_code(process) == exit_status  # noqa: SLF001
            assert process.returncode is None
        assert calls == [process.pid]
    finally:
        # The stub reports ECHILD without actually reaping this fixture child.
        process.returncode = None
        process.wait(timeout=5)


@pytest.mark.parametrize("parent_exit", ["before_close", "stdin_eof", "sigterm"])
@pytest.mark.parametrize("descendant_depth", [1, 2])
def test_close_kills_term_ignoring_descendants_after_worker_exit(
    tmp_path, parent_exit, descendant_depth
):
    marker = tmp_path / "child.pid"
    child_source = (
        "process.on('SIGTERM', () => {});"
        f"require('node:fs').writeFileSync({json.dumps(str(marker))}, String(process.pid));"
        "setInterval(() => {}, 1000);"
    )
    if descendant_depth == 2:
        child_source = (
            "require('node:child_process').spawn(process.execPath,"
            f"['-e', {json.dumps(child_source)}], {{stdio: 'ignore'}}).unref();"
        )
    parent_action = {
        "before_close": "process.exit(0);",
        "stdin_eof": "process.stdin.resume();",
        "sigterm": "process.stdin.resume(); setInterval(() => {}, 1000);",
    }[parent_exit]
    _worker(
        tmp_path,
        f"""
import {{spawn}} from "node:child_process";
import fs from "node:fs";
const child = spawn(process.execPath, ["-e", {json.dumps(child_source)}], {{stdio: "ignore"}});
child.unref();
const ready = setInterval(() => {{
  if (fs.existsSync({json.dumps(str(marker))})) {{
    clearInterval(ready);
    {parent_action}
  }}
}}, 10);
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)
    child_pid = None
    try:
        process = runtime._start()  # noqa: SLF001 - real worker lifetime regression
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        child_pid = int(marker.read_text(encoding="utf-8"))
        assert _pid_is_running(child_pid)
        if parent_exit == "before_close":
            assert _observe_worker_exit(process) == 0

        runtime.close()

        deadline = time.monotonic() + 2
        while _pid_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _pid_is_running(child_pid)
        assert runtime._process is None  # noqa: SLF001 - cleanup contract
        assert runtime._process_group_id is None  # noqa: SLF001 - cleanup contract
    finally:
        runtime.close()
        if child_pid is not None and _pid_is_running(child_pid):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.parametrize("started", [True, False])
def test_runtime_is_collectible_without_explicit_close(tmp_path, started):
    _worker(tmp_path, "process.stdin.resume();")
    runtime = NodeMermaidRuntime(tmp_path)
    process = runtime._start() if started else None  # noqa: SLF001 - GC cleanup contract
    reference = weakref.ref(runtime)

    del runtime
    gc.collect()

    assert reference() is None
    if process is not None:
        assert process.poll() is not None
        assert process.stdin.closed
        assert process.stdout.closed
        assert process.stderr.closed


def test_runtime_close_is_idempotent_and_context_can_be_reused(tmp_path):
    _worker(
        tmp_path,
        """
import readline from "node:readline";
const rl = readline.createInterface({input: process.stdin});
rl.on("line", line => {
  const request = JSON.parse(line);
  process.stdout.write(JSON.stringify({id: request.id, ok: true,
    svg: "<svg viewBox='0 0 1 1'/>"}) + "\\n");
});
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)
    processes = []

    for _ in range(3):
        with runtime:
            assert runtime.validate_and_render("flowchart LR\nA-->B", 2).render_valid
            processes.append(runtime._process)  # noqa: SLF001 - restart contract
        runtime.close()
        assert runtime._finalizer is None  # noqa: SLF001 - finalizer lifecycle contract

    assert len({process.pid for process in processes}) == 3
    assert all(process.poll() is not None for process in processes)


def _responding_worker(tmp_path, *, delay_milliseconds: int) -> None:
    _worker(
        tmp_path,
        f"""
import readline from "node:readline";
import fs from "node:fs";
const rl = readline.createInterface({{input: process.stdin}});
rl.on("line", line => {{
  const request = JSON.parse(line);
  fs.writeFileSync({json.dumps(str(tmp_path / "received"))}, request.id);
  setTimeout(() => process.stdout.write(JSON.stringify({{
    id: request.id, ok: true, svg: "<svg viewBox='0 0 1 1'/>"
  }}) + "\\n"), {delay_milliseconds});
}});
""",
    )


def _wait_for_request(tmp_path) -> None:
    deadline = time.monotonic() + 5
    while not (tmp_path / "received").exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert (tmp_path / "received").exists()


def test_lock_timeout_does_not_interrupt_another_callers_worker(tmp_path):
    _responding_worker(tmp_path, delay_milliseconds=300)
    with NodeMermaidRuntime(tmp_path) as runtime, ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runtime.validate_and_render, "first", 2)
        _wait_for_request(tmp_path)
        process = runtime._process  # noqa: SLF001 - shared-worker ownership contract

        started = time.monotonic()
        waiting = runtime.validate_and_render("waiting", 0.05)
        elapsed = time.monotonic() - started

        assert elapsed < 0.2
        assert "waiting for an available worker" in (waiting.error or "")
        assert runtime._process is process  # noqa: SLF001 - active call remains intact
        assert process.poll() is None
        assert first.result(timeout=2).render_valid
        assert runtime.validate_and_render("after waiting", 2).render_valid


def test_lock_wait_consumes_the_render_deadline(tmp_path):
    _responding_worker(tmp_path, delay_milliseconds=250)
    with NodeMermaidRuntime(tmp_path) as runtime, ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runtime.validate_and_render, "first", 2)
        _wait_for_request(tmp_path)

        started = time.monotonic()
        second = runtime.validate_and_render("second", 0.35)
        elapsed = time.monotonic() - started

        assert first.result(timeout=2).render_valid
        assert not second.render_valid
        assert "exceeded 0.35s" in (second.error or "")
        assert elapsed < 0.55
        assert runtime._process is None  # noqa: SLF001 - expired work is discarded


def test_request_queue_rejects_overflow_and_recovers(monkeypatch, tmp_path):
    monkeypatch.setattr(validation, "MAX_RUNTIME_PENDING_REQUESTS", 2)
    _responding_worker(tmp_path, delay_milliseconds=0)
    with NodeMermaidRuntime(tmp_path) as runtime, ThreadPoolExecutor(max_workers=2) as pool:
        with runtime._lock:  # noqa: SLF001 - hold worker to fill pending slots
            pending = [pool.submit(runtime.validate_and_render, "queued", 2) for _ in range(2)]
            deadline = time.monotonic() + 1
            while runtime._request_slots._value and time.monotonic() < deadline:  # noqa: SLF001
                time.sleep(0.005)
            assert runtime._request_slots._value == 0  # noqa: SLF001 - queue is full

            started = time.monotonic()
            overflow = runtime.validate_and_render("overflow", 2)

            assert time.monotonic() - started < 0.1
            assert overflow.error == "Mermaid runtime request queue is full"

        assert all(future.result(timeout=2).render_valid for future in pending)
        assert runtime.validate_and_render("capacity released", 2).render_valid


def test_timeout_skips_shutdown_grace_for_term_ignoring_worker(monkeypatch, tmp_path):
    _worker(
        tmp_path,
        """
process.on("SIGTERM", () => {});
process.stdin.resume();
setInterval(() => {}, 1000);
""",
    )
    runtime = NodeMermaidRuntime(tmp_path)
    process = runtime._start()  # noqa: SLF001 - preserve identity through timed-out cleanup
    original_killpg = os.killpg
    signals = []

    def signal_still_owned_group(process_group_id, signum):
        assert process_group_id == process.pid
        validation._runtime_worker_exit_code(process)  # noqa: SLF001 - must remain waitable
        signals.append(signum)
        original_killpg(process_group_id, signum)

    monkeypatch.setattr(validation.os, "killpg", signal_still_owned_group)
    started = time.monotonic()

    result = runtime.validate_and_render("never answers", 0.15)

    assert time.monotonic() - started < 0.4
    assert "exceeded 0.15s" in (result.error or "")
    assert runtime._process is None  # noqa: SLF001 - forced cleanup contract
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.poll() is not None


def test_worker_startup_consumes_the_request_deadline(monkeypatch, tmp_path):
    _responding_worker(tmp_path, delay_milliseconds=100)
    runtime = NodeMermaidRuntime(tmp_path)
    original_start = runtime._start  # noqa: SLF001 - inject launcher latency

    def slow_start():
        time.sleep(0.1)
        return original_start()

    monkeypatch.setattr(runtime, "_start", slow_start)
    started = time.monotonic()

    result = runtime.validate_and_render("cold start", 0.15)

    assert time.monotonic() - started < 0.35
    assert "exceeded 0.15s" in (result.error or "")
    assert runtime._process is None  # noqa: SLF001 - cold-start timeout cleanup


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
    monkeypatch.setattr(
        resource, "getrlimit", lambda _kind: (resource.RLIM_INFINITY, resource.RLIM_INFINITY)
    )
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
