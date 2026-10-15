from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

import marker_mermaid.runtime_launcher as launcher
import marker_mermaid.validation as validation
from marker_mermaid.validation import NodeMermaidRuntime


def test_darwin_launcher_keeps_inherited_virtual_memory_policy(monkeypatch):
    import resource

    calls = []
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(resource, "getrlimit", lambda _kind: (resource.RLIM_INFINITY,) * 2)
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: calls.append((kind, value)))
    launcher.apply_worker_process_limits(1024, 7)
    assert calls == [(resource.RLIMIT_CORE, (0, 0)), (resource.RLIMIT_CPU, (7, 7))]


def test_supervisor_refuses_a_foreign_process_group(monkeypatch):
    monkeypatch.setattr(launcher.os, "getpgrp", lambda: os.getpid() + 1)
    with pytest.raises(RuntimeError, match="own session"):
        launcher.supervise_darwin_worker(["unused"], 1000)


def test_fatal_budget_diagnostic_cannot_block_on_full_stderr(tmp_path):
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break
        # Inherited stderr is ordinarily blocking; restore that condition after
        # filling it. Nobody drains the pipe until the guardian has exited.
        os.set_blocking(write_fd, True)
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os; from marker_mermaid.runtime_launcher "
                "import _terminate_owned_group; "
                "_terminate_owned_group(os.getpid(), 'memory budget')",
            ],
            stderr=write_fd,
            start_new_session=True,
        )
        try:
            assert process.wait(timeout=3) == -signal.SIGKILL
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_dead_guardian_cannot_accept_a_surviving_child_response(monkeypatch, tmp_path):
    marker = tmp_path / "child.pid"
    # The launcher fixture exits after its child holds stdout open. That child
    # emits a valid response after the guardian has already exited.
    child_code = (
        "import json,os,sys,time; "
        f"open({str(marker)!r},'w').write(str(os.getpid())); "
        "line=sys.stdin.readline(); time.sleep(.3); "
        "print(json.dumps({'id':json.loads(line)['id'],'ok':True,'svg':'<svg/>'}),flush=True); "
        "time.sleep(30)"
    )
    parent_code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_code!r}])"
    process = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    runtime = NodeMermaidRuntime(tmp_path)
    runtime._process = process  # noqa: SLF001 - inject an actual owned guardian
    runtime._process_group_id = process.pid  # noqa: SLF001
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    original_observe = validation._runtime_worker_exit_code  # noqa: SLF001
    observations = 0

    def observe(owned):
        nonlocal observations
        observations += 1
        # Enter the request path with the injected guardian; subsequent native
        # observations must detect its death without freeing its reserved PID.
        return None if observations == 1 else original_observe(owned)

    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    monkeypatch.setattr(validation.sys, "platform", "darwin")
    monkeypatch.setattr(validation, "_runtime_worker_exit_code", observe)
    try:
        result = runtime.validate_and_render("flowchart LR\nA-->B", 2)
        assert not result.render_valid
        assert "guardian exited" in (result.error or "")
        assert process.returncode is not None
        child_pid = int(marker.read_text())
        deadline = time.monotonic() + 2
        while True:
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(child_pid)],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            if not state or state.startswith("Z") or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert not state or state.startswith("Z")
    finally:
        runtime.close()
