"""Apply POSIX process limits and replace this process with the Node worker."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence


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
    os.execvp(command[0], command)
    return 0  # pragma: no cover - os.execvp replaces the process


if __name__ == "__main__":
    main()
