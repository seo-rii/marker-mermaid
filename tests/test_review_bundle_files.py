from __future__ import annotations

import multiprocessing
import os
import stat

import pytest
from test_review_store import make_bundle

from marker_mermaid.review_store import ReviewStore, ReviewStoreError, ReviewValidationError


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
@pytest.mark.parametrize(
    "artifact",
    [
        "manifest.json", "final.mmd", "review-history.json", "final.svg", "final.png",
        "scene-ir.json", "provenance.json", "layout-hints.json", "review-state.json",
        ".review-transaction.json",
    ],
)
def test_bundle_load_rejects_fifo_without_a_writer(tmp_path, artifact):
    bundle = make_bundle(tmp_path)
    path = bundle / artifact
    path.unlink(missing_ok=True)
    os.mkfifo(path)

    def load():
        try:
            ReviewStore(tmp_path).load_bundle("diagram-a")
        except ReviewStoreError:
            return
        raise AssertionError("special artifact was accepted")

    process = multiprocessing.get_context("fork").Process(target=load)
    process.start()
    try:
        process.join(timeout=3)
        assert process.exitcode == 0, "bundle artifact read blocked or failed"
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        process.close()


def test_bundle_read_rejects_growth_after_metadata_check(monkeypatch, tmp_path):
    bundle = make_bundle(tmp_path)
    path = bundle / "data.bin"
    path.write_bytes(b"ok")
    inode = path.stat().st_ino
    original_fstat = os.fstat

    def grow_after_stat(descriptor):
        metadata = original_fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode) and metadata.st_ino == inode:
            path.write_bytes(b"oversized")
        return metadata

    monkeypatch.setattr(os, "fstat", grow_after_stat)
    with pytest.raises(ReviewValidationError, match="exceeds the artifact size limit"):
        ReviewStore(tmp_path)._read_artifact_bytes(bundle, "data.bin", 2, kind="artifact")


def test_bundle_read_accepts_exact_byte_limit(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "data.bin").write_bytes(b"ok")
    assert ReviewStore(tmp_path)._read_artifact_bytes(
        bundle, "data.bin", 2, kind="artifact"
    ) == b"ok"


def test_bundle_read_rejects_symlink_swapped_at_final_open(monkeypatch, tmp_path):
    bundle = make_bundle(tmp_path)
    path = bundle / "data.bin"
    path.write_bytes(b"ok")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"private")
    original_open = os.open

    def swap_before_open(name, flags, *args, **kwargs):
        if name == "data.bin" and kwargs.get("dir_fd") is not None:
            path.unlink()
            path.symlink_to(outside)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(ReviewValidationError, match="could not be read safely"):
        ReviewStore(tmp_path)._read_artifact_bytes(bundle, "data.bin", 20, kind="artifact")


def test_bundle_json_still_requires_utf8(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "data.json").write_bytes('{"x":1}'.encode("utf-16"))
    with pytest.raises(ReviewValidationError, match="UTF-8 JSON"):
        ReviewStore(tmp_path)._read_json(bundle, "data.json", expected=dict)


def test_bundle_read_closes_leaf_when_fdopen_fails(monkeypatch, tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "data.bin").write_bytes(b"ok")
    opened = []

    def fail_fdopen(descriptor, *_args, **_kwargs):
        opened.append(descriptor)
        raise OSError("injected fdopen failure")

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    with pytest.raises(ReviewValidationError, match="could not be read safely"):
        ReviewStore(tmp_path)._read_artifact_bytes(bundle, "data.bin", 2, kind="artifact")
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])
