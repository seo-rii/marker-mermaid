"""Apply POSIX process limits and replace this process with the Node worker."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from typing import NoReturn


def _bounded_hard_limit(
    current: tuple[int, int],
    requested: int,
    *,
    infinity: int,
) -> tuple[int, int]:
    """Return a non-raising hard/soft limit bounded by the inherited policy."""

    current_soft, current_hard = current
    hard = requested if current_hard == infinity else min(current_hard, requested)
    soft = hard if current_soft == infinity else min(current_soft, hard)
    return soft, hard


def apply_worker_process_limits(max_data_bytes: int, max_cpu_seconds: int) -> None:
    """Set hard limits before exec so Node and every Chromium child inherit them."""

    if os.name != "posix":
        raise RuntimeError("runtime worker limits require a POSIX platform")
    if type(max_data_bytes) is not int or max_data_bytes <= 0:
        raise ValueError("max_data_bytes must be a positive integer")
    if type(max_cpu_seconds) is not int or max_cpu_seconds <= 0:
        raise ValueError("max_cpu_seconds must be a positive integer")

    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform != "darwin":
        resource.setrlimit(
            resource.RLIMIT_DATA,
            _bounded_hard_limit(
                resource.getrlimit(resource.RLIMIT_DATA),
                max_data_bytes,
                infinity=resource.RLIM_INFINITY,
            ),
        )
    resource.setrlimit(
        resource.RLIMIT_CPU,
        _bounded_hard_limit(
            resource.getrlimit(resource.RLIMIT_CPU),
            max_cpu_seconds,
            infinity=resource.RLIM_INFINITY,
        ),
    )


def _terminate_owned_group(group_id: int, message: str = "") -> NoReturn:
    """Terminate this live session, even if its inherited stderr pipe is full."""
    if group_id != os.getpid() or group_id != os.getpgrp():
        raise RuntimeError("runtime supervisor no longer owns its process group")
    try:
        if message:
            try:
                os.set_blocking(2, False)
                os.write(2, (message[:1000] + "\n").encode("utf-8", errors="replace"))
            except OSError:
                pass
    finally:
        # The live caller itself pins this PGID; never signal individual PIDs
        # from a potentially stale libproc snapshot.
        os.killpg(group_id, signal.SIGKILL)
    os._exit(125)  # pragma: no cover - SIGKILL terminates the caller too


def supervise_darwin_worker(command: Sequence[str], max_memory_bytes: int) -> int:
    """Sample the physical footprint of the complete owned Node/browser group."""
    if __package__:
        from .resource_limits import RUNTIME_MEMORY_POLL_SECONDS
        from .runtime_memory import DarwinProcessGroupMemory
    else:
        # This launcher is also executed directly by absolute path in wheels.
        from resource_limits import RUNTIME_MEMORY_POLL_SECONDS
        from runtime_memory import DarwinProcessGroupMemory

    group_id = os.getpid()
    if os.getpgrp() != group_id or os.getsid(0) != group_id:
        raise RuntimeError("Darwin runtime supervisor requires its own session and process group")

    def terminate(_signum: int, _frame: object) -> NoReturn:
        _terminate_owned_group(group_id)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        reader = DarwinProcessGroupMemory(group_id)
        _, footprint = reader.snapshot()
        if footprint > max_memory_bytes:
            _terminate_owned_group(group_id, "Mermaid runtime memory budget exceeded before launch")
        # Inherit the protocol descriptors and this session's resource policy.
        # The supervisor remains the PID reserved by the Python parent's waitid.
        child = subprocess.Popen(list(command))
        while True:
            members, footprint = reader.snapshot()
            if footprint > max_memory_bytes:
                _terminate_owned_group(group_id, "Mermaid runtime group memory budget exceeded")
            exit_code = child.poll()
            if exit_code is not None:
                # Re-sample after reaping Node; a previous snapshot can still
                # include it. Any surviving browser children require cleanup.
                members, _ = reader.snapshot()
                if members == (group_id,):
                    return exit_code if exit_code >= 0 else 128 - exit_code
                _terminate_owned_group(group_id)
            time.sleep(RUNTIME_MEMORY_POLL_SECONDS)
    except BaseException as exc:
        _terminate_owned_group(group_id, f"Mermaid runtime memory budget inspection failed: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    """Apply limits supplied by the trusted Python parent, then exec the worker."""

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        raise SystemExit(
            "usage: runtime_launcher.py MAX_DATA_BYTES MAX_CPU_SECONDS COMMAND [ARG ...]"
        )
    try:
        max_data_bytes = int(args[0])
        max_cpu_seconds = int(args[1])
    except ValueError as exc:
        raise SystemExit("runtime worker limits must be integers") from exc
    command = args[2:]
    apply_worker_process_limits(max_data_bytes, max_cpu_seconds)
    if sys.platform == "darwin":
        return supervise_darwin_worker(command, max_data_bytes)
    os.execvp(command[0], command)
    return 0  # pragma: no cover - os.execvp replaces the process


if __name__ == "__main__":
    raise SystemExit(main())
