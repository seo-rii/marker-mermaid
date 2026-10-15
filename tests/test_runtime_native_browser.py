from __future__ import annotations

import json
import resource
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


def test_native_launcher_preserves_required_resource_policy():
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
            f"for name in {observed_limits!r}}}))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
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
            processes.append(process)
        assert process.returncode is not None
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
    assert processes[0] is not processes[1]


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin group footprint supervisor")
def test_darwin_group_memory_budget_stops_worker_and_descendants(monkeypatch, tmp_path):
    marker = tmp_path / "owned-processes.json"
    child_code = (
        "process.on('SIGTERM', () => {}); process.send('ready'); setInterval(() => {}, 1000);"
    )
    (tmp_path / "worker.mjs").write_text(
        f"""
import {{spawn}} from 'node:child_process';
import fs from 'node:fs';
const child = spawn(process.execPath, ['-e', {json.dumps(child_code)}], {{
  stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
}});
child.once('message', () => {{
  fs.writeFileSync({json.dumps(str(marker))}, JSON.stringify([process.pid, child.pid]));
  const allocations = [];
  const allocate = setInterval(() => {{
    allocations.push(Buffer.alloc(32 * 1024 * 1024, 1));
    if (allocations.length === 16) clearInterval(allocate);
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
    assert not running, f"memory-budget failure left running owned processes: {running}"
