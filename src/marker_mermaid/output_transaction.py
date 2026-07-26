"""Descriptor-anchored transaction for publishing one complete document tree."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from marker_mermaid.sidecars import _rename_noreplace


class OutputDurabilityError(OSError):
    """The complete output was published, but syncing its parent failed."""


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _file_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _safe_relative_path(relative: str | PurePosixPath) -> PurePosixPath:
    normalized = PurePosixPath(relative)
    if (
        normalized.is_absolute()
        or not normalized.parts
        or any(part in {"", ".", ".."} for part in normalized.parts)
    ):
        raise ValueError(f"unsafe output transaction path: {relative!r}")
    return normalized


def _open_child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        with suppress(FileExistsError):
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    child_fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    after = os.fstat(child_fd)
    if (
        not stat.S_ISDIR(before.st_mode)
        or stat.S_ISLNK(before.st_mode)
        or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
    ):
        os.close(child_fd)
        raise ValueError("output transaction directory identity changed")
    return child_fd


def _open_exclusive_file(directory_fd: int, relative: PurePosixPath) -> int:
    parent_fd = os.dup(directory_fd)
    try:
        for component in relative.parts[:-1]:
            child_fd = _open_child_directory(parent_fd, component, create=True)
            os.close(parent_fd)
            parent_fd = child_fd
        return os.open(relative.name, _file_flags(), 0o600, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _fsync_tree(directory_fd: int) -> None:
    """Sync every regular file and directory without following path replacements."""

    for name in os.listdir(directory_fd):
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        identity = (before.st_dev, before.st_ino)
        if stat.S_ISDIR(before.st_mode):
            child_fd = _open_child_directory(directory_fd, name, create=False)
            try:
                _fsync_tree(child_fd)
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise ValueError(f"output transaction contains an unsafe artifact: {name!r}")
        file_fd = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            after = os.fstat(file_fd)
            if identity != (after.st_dev, after.st_ino) or not stat.S_ISREG(after.st_mode):
                raise ValueError("output transaction file identity changed before sync")
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
    os.fsync(directory_fd)


class OutputTransaction:
    """Build an output directory privately and publish it with a no-replace rename."""

    def __init__(self, destination: str | Path):
        lexical_destination = Path(destination).absolute()
        if (
            not lexical_destination.name
            or lexical_destination.name in {".", ".."}
            or Path(lexical_destination.name).name != lexical_destination.name
        ):
            raise ValueError("output directory must end in one safe path component")
        self.destination = lexical_destination
        self._parent = lexical_destination.parent
        self._final_name = lexical_destination.name
        self._parent_fd: int | None = None
        self._temporary_name: str | None = None
        self._temporary_fd: int | None = None
        self._temporary_identity: tuple[int, int] | None = None
        self._created_parents: list[tuple[Path, tuple[int, int]]] = []
        self._published = False

    @property
    def directory_fd(self) -> int:
        if self._temporary_fd is None:
            raise RuntimeError("output transaction is not open")
        return self._temporary_fd

    @property
    def published(self) -> bool:
        return self._published

    def _create_parent(self) -> None:
        missing: list[Path] = []
        cursor = self._parent
        while True:
            try:
                cursor.lstat()
                break
            except FileNotFoundError:
                missing.append(cursor)
                if cursor == cursor.parent:
                    raise
                cursor = cursor.parent
        self._parent.mkdir(parents=True, exist_ok=True)
        for path in missing:
            current = path.lstat()
            if not stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode):
                raise ValueError("output parent must be a real directory")
            self._created_parents.append((path, (current.st_dev, current.st_ino)))

    def __enter__(self) -> OutputTransaction:
        self._create_parent()
        try:
            self._parent_fd = os.open(self._parent, _directory_flags())
            try:
                os.stat(self._final_name, dir_fd=self._parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(
                    f"output directory already exists: {self.destination}"
                )
            for _ in range(128):
                proposal = f".{self._final_name}.tmp-{secrets.token_hex(8)}"
                try:
                    os.mkdir(proposal, mode=0o700, dir_fd=self._parent_fd)
                except FileExistsError:
                    continue
                self._temporary_name = proposal
                break
            if self._temporary_name is None:
                raise FileExistsError("unable to allocate an output staging directory")
            before = os.stat(
                self._temporary_name,
                dir_fd=self._parent_fd,
                follow_symlinks=False,
            )
            self._temporary_fd = os.open(
                self._temporary_name,
                _directory_flags(),
                dir_fd=self._parent_fd,
            )
            after = os.fstat(self._temporary_fd)
            self._temporary_identity = (before.st_dev, before.st_ino)
            if (
                not stat.S_ISDIR(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or self._temporary_identity != (after.st_dev, after.st_ino)
            ):
                raise ValueError("output staging directory identity changed")
            return self
        except Exception:
            self.close()
            raise

    @contextmanager
    def open_binary(self, relative: str | PurePosixPath) -> Iterator[BinaryIO]:
        file_fd = _open_exclusive_file(
            self.directory_fd,
            _safe_relative_path(relative),
        )
        with os.fdopen(file_fd, "wb") as output:
            yield output

    def write_bytes(self, relative: str | PurePosixPath, payload: bytes) -> None:
        if type(payload) is not bytes:
            raise TypeError("output transaction payload must be bytes")
        with self.open_binary(relative) as output:
            output.write(payload)

    def commit(self) -> Path:
        if (
            self._parent_fd is None
            or self._temporary_fd is None
            or self._temporary_name is None
            or self._temporary_identity is None
        ):
            raise RuntimeError("output transaction is not open")
        _fsync_tree(self._temporary_fd)
        os.fsync(self._parent_fd)
        current = os.stat(
            self._temporary_name,
            dir_fd=self._parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != self._temporary_identity
        ):
            raise ValueError("output staging directory identity changed before publication")
        try:
            os.stat(self._final_name, dir_fd=self._parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(
                f"output directory already exists: {self.destination}"
            )
        _rename_noreplace(
            self._parent_fd,
            self._temporary_name,
            self._parent_fd,
            self._final_name,
        )
        self._published = True
        self._temporary_name = None
        try:
            os.fsync(self._parent_fd)
        except OSError as exc:
            raise OutputDurabilityError(
                "output was published completely, but its parent could not be synced"
            ) from exc
        return self.destination

    def _cleanup_staging(self) -> None:
        if (
            self._temporary_name is None
            or self._parent_fd is None
            or self._temporary_identity is None
        ):
            return
        try:
            current = os.stat(
                self._temporary_name,
                dir_fd=self._parent_fd,
                follow_symlinks=False,
            )
            cleanup_is_safe = (
                stat.S_ISDIR(current.st_mode)
                and not stat.S_ISLNK(current.st_mode)
                and (current.st_dev, current.st_ino) == self._temporary_identity
            )
        except (FileNotFoundError, OSError):
            cleanup_is_safe = False
        if cleanup_is_safe:
            shutil.rmtree(
                self._temporary_name,
                dir_fd=self._parent_fd,
                ignore_errors=True,
            )
        self._temporary_name = None

    def _cleanup_created_parents(self) -> None:
        if self._published:
            return
        for path, identity in self._created_parents:
            try:
                current = path.lstat()
                if (
                    stat.S_ISDIR(current.st_mode)
                    and not stat.S_ISLNK(current.st_mode)
                    and (current.st_dev, current.st_ino) == identity
                ):
                    path.rmdir()
            except (FileNotFoundError, OSError):
                pass

    def close(self) -> None:
        self._cleanup_staging()
        if self._temporary_fd is not None:
            os.close(self._temporary_fd)
            self._temporary_fd = None
        if self._parent_fd is not None:
            os.close(self._parent_fd)
            self._parent_fd = None
        self._cleanup_created_parents()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
