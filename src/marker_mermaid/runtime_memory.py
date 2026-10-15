"""Read a Darwin supervisor's owned process-group physical memory footprint.

This module uses only the standard library so the launcher can import it when
executed directly, without importing the marker_mermaid package.
"""

from __future__ import annotations

import ctypes
import errno
import os
import sys

if __package__:
    from .resource_limits import MAX_RUNTIME_GROUP_MEMBERS
else:
    from resource_limits import MAX_RUNTIME_GROUP_MEMBERS


class _RusageInfoV0(ctypes.Structure):
    # Public ABI: xnu/bsd/sys/resource.h, struct rusage_info_v0.
    # https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/resource.h
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
    ]


class DarwinProcessGroupMemory:
    """Fail closed while sampling the calling group leader and its members.

    ``snapshot`` returns active member PIDs and the sum of their physical
    footprints in bytes. Exited/zombie records are omitted from both. The
    calling process must remain the group leader for the reader's lifetime.
    Enumeration and memory reads are not atomic; confirmed departed members
    can be omitted, while ambiguous failures abort the sample.
    """

    def __init__(self, group_id: int) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Darwin process-group memory accounting requires macOS")
        if type(group_id) is not int or not 0 < group_id < 2**31:
            raise ValueError("process group ID must be a positive pid_t")
        self.group_id = group_id
        self._require_owned_group()
        self._libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self._list_members = self._libproc.proc_listpgrppids
        self._list_members.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self._list_members.restype = ctypes.c_int
        self._read_usage = self._libproc.proc_pid_rusage
        self._read_usage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self._read_usage.restype = ctypes.c_int

    def _require_owned_group(self) -> None:
        if os.getpid() != self.group_id or os.getpgrp() != self.group_id:
            raise RuntimeError("memory accounting requires the caller's owned process group")

    def _departed(self, pid: int) -> bool:
        try:
            return os.getpgid(pid) != self.group_id
        except ProcessLookupError:
            return True

    def _usage(self, pid: int) -> _RusageInfoV0 | None:
        for attempt in range(2):
            info = _RusageInfoV0()
            ctypes.set_errno(0)
            result = self._read_usage(pid, 0, ctypes.byref(info))
            error = ctypes.get_errno()
            if result == 0 and error == 0:
                return info
            if result == -1 and error == errno.ESRCH:
                if self._departed(pid):
                    return None
                if attempt == 0:
                    continue
            raise OSError(error or errno.EIO, f"proc_pid_rusage failed for PID {pid}")
        raise RuntimeError("unreachable memory accounting retry")  # pragma: no cover

    def snapshot(self) -> tuple[tuple[int, ...], int]:
        """Return active member PIDs and their total physical footprint bytes."""

        self._require_owned_group()
        buffer = (ctypes.c_int * MAX_RUNTIME_GROUP_MEMBERS)()
        ctypes.set_errno(0)
        # Unlike proc_listpids, proc_listpgrppids RETURNS A PID COUNT, but still
        # takes the buffer capacity in BYTES. A full buffer may be truncated.
        # https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c
        count = self._list_members(self.group_id, buffer, ctypes.sizeof(buffer))
        error = ctypes.get_errno()
        if error or count <= 0:
            raise OSError(error or errno.EIO, "proc_listpgrppids failed")
        if count >= len(buffer):
            raise RuntimeError("process-group memory accounting member limit exceeded")
        members = tuple(buffer[:count])
        if any(pid <= 0 for pid in members) or len(set(members)) != len(members):
            raise RuntimeError("invalid process-group member list")
        if self.group_id not in members:
            raise RuntimeError("memory accounting lost its process-group leader")

        active: list[int] = []
        total = 0
        for pid in members:
            info = self._usage(pid)
            if info is None or info.ri_proc_exit_abstime:
                continue
            if info.ri_phys_footprint == 0:
                raise RuntimeError(f"physical footprint unavailable for live PID {pid}")
            active.append(pid)
            total += info.ri_phys_footprint
        if self.group_id not in active:
            raise RuntimeError("memory accounting lost its active process-group leader")
        return tuple(sorted(active)), total
