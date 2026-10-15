from __future__ import annotations

import json
import os
import resource
import signal
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import marker_mermaid.validation as validation
from marker_mermaid.resource_limits import (
    MAX_RUNTIME_WORKER_CPU_SECONDS,
    MAX_RUNTIME_WORKER_DATA_BYTES,
)
from marker_mermaid.validation import NodeMermaidRuntime, default_runtime_dir

pytestmark = pytest.mark.integration


def _owned_processes(leader):
    snapshot = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat=,comm="],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    processes = {}
    for line in snapshot.stdout.splitlines():
        pid, parent, group, status, command = line.strip().split(maxsplit=4)
        processes[int(pid)] = (int(parent), int(group), status, command)
    owned = {leader}
    while True:
        descendants = {pid for pid, row in processes.items() if row[0] in owned}
        if descendants <= owned:
            break
        owned.update(descendants)
    return {pid: processes[pid] for pid in owned if pid in processes}


def _assert_browser_group_is_owned(leader):
    processes = _owned_processes(leader)
    assert len(processes) >= 2, "real rendering must start a browser descendant"
    assert any(
        any(name in row[3].lower() for name in ("chromium", "chrome", "headless"))
        for pid, row in processes.items()
        if pid != leader
    ), processes
    assert all(row[1] == leader for row in processes.values()), processes
    return processes


def _assert_processes_stopped(pids):
    deadline = time.monotonic() + 3
    running = pids
    while running and time.monotonic() < deadline:
        running = []
        for pid in pids:
            status = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            ).stdout.strip()
            if status and not status.startswith("Z"):
                running.append(pid)
        if running:
            time.sleep(0.02)
    assert not running, f"runtime left running owned processes: {running}"


@pytest.mark.parametrize("worker_exit_code", [0, 7])
def test_native_launcher_preserves_required_resource_policy(worker_exit_code):
    launcher = Path(validation.__file__).with_name("runtime_launcher.py")
    observed_limits = ["RLIMIT_CORE", "RLIMIT_CPU", "RLIMIT_DATA", "RLIMIT_AS"]
    inherited_limits = {
        name: list(resource.getrlimit(getattr(resource, name))) for name in observed_limits
    }
    probe = subprocess.run(
        [
            sys.executable,
            str(launcher),
            str(MAX_RUNTIME_WORKER_DATA_BYTES),
            str(MAX_RUNTIME_WORKER_CPU_SECONDS),
            sys.executable,
            "-c",
            "import json, resource; "
            "print(json.dumps({name: resource.getrlimit(getattr(resource, name)) "
            f"for name in {observed_limits!r}}})); "
            f"raise SystemExit({worker_exit_code})",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        start_new_session=True,
    )
    assert probe.returncode == worker_exit_code, probe.stderr
    limits = json.loads(probe.stdout)

    assert limits["RLIMIT_CORE"] == [0, 0]
    assert 0 < limits["RLIMIT_CPU"][0] <= limits["RLIMIT_CPU"][1] <= MAX_RUNTIME_WORKER_CPU_SECONDS
    if sys.platform == "darwin":
        assert limits["RLIMIT_DATA"] == inherited_limits["RLIMIT_DATA"]
        assert limits["RLIMIT_AS"] == inherited_limits["RLIMIT_AS"]
    else:
        data_soft, data_hard = limits["RLIMIT_DATA"]
        assert 0 < data_soft <= data_hard <= MAX_RUNTIME_WORKER_DATA_BYTES


def test_native_browser_renders_png_and_restarts_under_process_budget():
    runtime = NodeMermaidRuntime(default_runtime_dir())
    processes = []
    for _ in range(2):
        with runtime:
            result = runtime.validate_and_render(
                "flowchart LR\nA[Native Start] --> B[Native Ready]\n", 30
            )
            process = runtime._process  # noqa: SLF001 - native launcher lifecycle contract
            assert result.syntax_valid and result.render_valid, result.error
            assert result.svg is not None
            assert all(label in result.svg for label in ("Native", "Start", "Ready"))
            assert result.png is not None, result.png_omitted_reason
            with Image.open(BytesIO(result.png)) as image:
                image.verify()
                assert image.format == "PNG" and image.width > 0 and image.height > 0
            assert process is not None
            owned = _assert_browser_group_is_owned(process.pid)
            processes.append(process)
        assert process.returncode is not None
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        _assert_processes_stopped(owned)
    assert processes[0] is not processes[1]


def test_native_browser_is_cleaned_up_after_node_is_killed():
    with NodeMermaidRuntime(default_runtime_dir()) as runtime:
        result = runtime.validate_and_render("flowchart LR\nA-->B\n", 30)
        assert result.render_valid, result.error
        process = runtime._process  # noqa: SLF001 - ownership regression
        assert process is not None
        owned = _assert_browser_group_is_owned(process.pid)
        if sys.platform == "darwin":
            node_pids = [
                pid
                for pid, row in owned.items()
                if row[0] == process.pid and Path(row[3]).name == "node"
            ]
            assert len(node_pids) == 1, owned
            node_pid = node_pids[0]
        else:
            node_pid = process.pid
        os.kill(node_pid, signal.SIGKILL)
    _assert_processes_stopped(owned)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin group footprint supervisor")
def test_darwin_group_memory_budget_stops_worker_and_descendants(monkeypatch, tmp_path):
    marker = tmp_path / "owned-processes.json"
    # Each Node process allocates at most 192 MiB, below the 256 MiB group
    # budget even with its normal heap. Their combined growth must stop the
    # whole group; a per-process limit would miss this case.
    child_code = """
process.on('SIGTERM', () => {});
const allocations = [];
process.on('message', () => {
  const allocate = setInterval(() => {
    allocations.push(Buffer.alloc(32 * 1024 * 1024, 1));
    if (allocations.length === 6) clearInterval(allocate);
  }, 50);
});
process.send('ready');
setInterval(() => allocations.length, 1000);
"""
    (tmp_path / "worker.mjs").write_text(
        f"""
import {{spawn}} from 'node:child_process';
import fs from 'node:fs';
const child = spawn(process.execPath, ['-e', {json.dumps(child_code)}], {{
  stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
}});
child.once('message', () => {{
  fs.writeFileSync({json.dumps(str(marker))}, JSON.stringify([process.pid, child.pid]));
  child.send('allocate');
  const allocations = [];
  const allocate = setInterval(() => {{
    allocations.push(Buffer.alloc(32 * 1024 * 1024, 1));
    if (allocations.length === 6) clearInterval(allocate);
  }}, 50);
  setInterval(() => allocations.length, 1000);
}});
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(validation, "MAX_RUNTIME_WORKER_DATA_BYTES", 256 * 1024 * 1024)
    with NodeMermaidRuntime(tmp_path) as runtime:
        result = runtime.validate_and_render("flowchart LR\nA-->B\n", 20)

    assert not result.render_valid
    assert "memory" in (result.error or "").lower(), result.error
    assert "budget" in (result.error or "").lower(), result.error
    assert marker.is_file(), "memory budget must leave enough headroom to start both Node processes"
    pids = json.loads(marker.read_text(encoding="utf-8"))
    _assert_processes_stopped(pids)
