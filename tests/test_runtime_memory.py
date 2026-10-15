from __future__ import annotations

import ctypes
import errno
import importlib.util
import sys
from pathlib import Path

import pytest

import marker_mermaid.runtime_memory as memory


class _Function:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


class _Libproc:
    def __init__(self):
        self.members = [101, 102, 103]
        self.usage = {101: (100, 0), 102: (200, 0), 103: (300, 0)}
        self.list_result = None
        self.list_errno = 0
        self.read_errors = {}
        self.read_calls = []
        self.buffer_bytes = None
        self.proc_listpgrppids = _Function(self._list)
        self.proc_pid_rusage = _Function(self._read)

    def _list(self, group_id, buffer, buffer_bytes):
        assert group_id == 101
        assert ctypes.get_errno() == 0
        self.buffer_bytes = buffer_bytes
        for index, pid in enumerate(self.members[: len(buffer)]):
            buffer[index] = pid
        ctypes.set_errno(self.list_errno)
        return len(self.members) if self.list_result is None else self.list_result

    def _read(self, pid, flavor, buffer):
        assert flavor == 0
        assert ctypes.get_errno() == 0
        self.read_calls.append(pid)
        errors = self.read_errors.get(pid, [])
        if errors:
            result, error = errors.pop(0)
            ctypes.set_errno(error)
            return result
        footprint, exited = self.usage[pid]
        info = ctypes.cast(buffer, ctypes.POINTER(memory._RusageInfoV0)).contents
        info.ri_phys_footprint = footprint
        info.ri_proc_exit_abstime = exited
        return 0


@pytest.fixture
def reader(monkeypatch):
    libproc = _Libproc()
    monkeypatch.setattr(memory.sys, "platform", "darwin")
    monkeypatch.setattr(memory.os, "getpid", lambda: 101)
    monkeypatch.setattr(memory.os, "getpgrp", lambda: 101)
    monkeypatch.setattr(memory.os, "getpgid", lambda pid: 101)

    def load(path, *, use_errno):
        assert path == "/usr/lib/libproc.dylib"
        assert use_errno is True
        return libproc

    monkeypatch.setattr(memory.ctypes, "CDLL", load)
    return memory.DarwinProcessGroupMemory(101), libproc


def test_rusage_info_v0_matches_public_darwin_abi():
    assert ctypes.sizeof(memory._RusageInfoV0) == 96
    assert memory._RusageInfoV0.ri_phys_footprint.offset == 72
    assert memory._RusageInfoV0.ri_proc_exit_abstime.offset == 88


def test_list_count_uses_pid_count_and_byte_capacity_and_sums_footprints(reader):
    monitor, libproc = reader
    ctypes.set_errno(errno.EIO)
    assert monitor.snapshot() == ((101, 102, 103), 600)
    assert libproc.buffer_bytes == memory.MAX_RUNTIME_GROUP_MEMBERS * ctypes.sizeof(ctypes.c_int)
    assert libproc.read_calls == [101, 102, 103]


@pytest.mark.parametrize("count", [memory.MAX_RUNTIME_GROUP_MEMBERS, 10000])
def test_full_or_overflowed_member_buffer_fails_closed(reader, count):
    monitor, libproc = reader
    libproc.list_result = count
    with pytest.raises(RuntimeError, match="member limit"):
        monitor.snapshot()
    assert not libproc.read_calls


@pytest.mark.parametrize("members", [[101, 101], [101, 0], [101, -7], [102]])
def test_invalid_or_unowned_member_lists_fail_closed(reader, members):
    monitor, libproc = reader
    libproc.members = members
    with pytest.raises(RuntimeError, match="member list|leader"):
        monitor.snapshot()


@pytest.mark.parametrize("count,error", [(0, 0), (-1, errno.EPERM), (3, errno.EIO)])
def test_list_failure_never_becomes_a_zero_memory_sample(reader, count, error):
    monitor, libproc = reader
    libproc.list_result = count
    libproc.list_errno = error
    with pytest.raises(OSError) as failure:
        monitor.snapshot()
    assert failure.value.errno == (error or errno.EIO)


@pytest.mark.parametrize("result,error", [(-1, errno.EPERM), (-1, 0), (1, 0), (0, errno.EIO)])
def test_usage_failure_never_becomes_a_zero_memory_sample(reader, result, error):
    monitor, libproc = reader
    libproc.read_errors[102] = [(result, error)]
    with pytest.raises(OSError) as failure:
        monitor.snapshot()
    assert failure.value.errno == (error or errno.EIO)


@pytest.mark.parametrize("departed", ["gone", "other_group"])
def test_esrch_is_omitted_only_after_confirmed_departure(reader, monkeypatch, departed):
    monitor, libproc = reader
    libproc.read_errors[102] = [(-1, errno.ESRCH)]

    def getpgid(pid):
        assert pid == 102
        if departed == "gone":
            raise ProcessLookupError(errno.ESRCH, "gone")
        return 999

    monkeypatch.setattr(memory.os, "getpgid", getpgid)
    assert monitor.snapshot() == ((101, 103), 400)


def test_esrch_for_a_still_present_member_retries_once(reader):
    monitor, libproc = reader
    libproc.read_errors[102] = [(-1, errno.ESRCH)]
    assert monitor.snapshot() == ((101, 102, 103), 600)
    assert libproc.read_calls.count(102) == 2


def test_repeated_esrch_for_a_still_present_member_fails_closed(reader):
    monitor, libproc = reader
    libproc.read_errors[102] = [(-1, errno.ESRCH)] * 2
    with pytest.raises(OSError) as failure:
        monitor.snapshot()
    assert failure.value.errno == errno.ESRCH
    assert libproc.read_calls.count(102) == 2


def test_getpgid_permission_failure_fails_closed(reader, monkeypatch):
    monitor, libproc = reader
    libproc.read_errors[102] = [(-1, errno.ESRCH)]

    def denied(pid):
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(memory.os, "getpgid", denied)
    with pytest.raises(PermissionError):
        monitor.snapshot()


@pytest.mark.parametrize("footprint", [0, 2**60])
def test_zombies_do_not_count_as_active_members_or_memory(reader, footprint):
    monitor, libproc = reader
    libproc.usage[102] = (footprint, 1)
    assert monitor.snapshot() == ((101, 103), 400)


def test_live_zero_footprint_is_unavailable(reader):
    monitor, libproc = reader
    libproc.usage[102] = (0, 0)
    with pytest.raises(RuntimeError, match="footprint unavailable"):
        monitor.snapshot()


def test_sum_is_not_truncated_to_c_integer_width(reader):
    monitor, libproc = reader
    libproc.usage = {pid: (2**63, 0) for pid in libproc.members}
    assert monitor.snapshot() == ((101, 102, 103), 3 * 2**63)


def test_constructor_rejects_another_process_group(reader):
    with pytest.raises(RuntimeError, match="owned process group"):
        memory.DarwinProcessGroupMemory(102)


@pytest.mark.parametrize("group_id", [True, 0, -1, 2**31])
def test_constructor_rejects_invalid_group_ids(reader, group_id):
    with pytest.raises(ValueError, match="pid_t"):
        memory.DarwinProcessGroupMemory(group_id)


def test_snapshot_rechecks_group_ownership(reader, monkeypatch):
    monitor, _ = reader
    monkeypatch.setattr(memory.os, "getpgrp", lambda: 999)
    with pytest.raises(RuntimeError, match="owned process group"):
        monitor.snapshot()


def test_reader_imports_directly_without_loading_package(monkeypatch):
    source = Path(memory.__file__)
    monkeypatch.syspath_prepend(str(source.parent))
    spec = importlib.util.spec_from_file_location("direct_runtime_memory", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DarwinProcessGroupMemory.__name__ == "DarwinProcessGroupMemory"


def test_constructor_rejects_other_platforms(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="macOS"):
        memory.DarwinProcessGroupMemory(101)
