from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from marker_mermaid.review import ReviewHandler


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")
@pytest.mark.parametrize(
    "artifact",
    ["images/blocked.png", "diagrams/diagram-a/final.svg", "diagrams/diagram-a/final.png"],
)
def test_static_fifo_is_rejected_without_a_writer(tmp_path, artifact):
    path = tmp_path / artifact
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    def open_fifo():
        handler = object.__new__(ReviewHandler)
        handler.review_root = tmp_path
        assert handler._open_static_artifact(f"/{artifact}") is None

    # Fork after imports so the deadline measures file opening, not interpreter
    # startup on a busy machine. Terminate a regressed blocking child on failure.
    process = multiprocessing.get_context("fork").Process(target=open_fifo)
    process.start()
    try:
        process.join(timeout=3)
        assert process.exitcode == 0, "static artifact opening blocked or failed"
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        process.close()



def test_static_inspection_error_closes_the_open_artifact(monkeypatch, tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "source.png").write_bytes(b"image")
    handler = object.__new__(ReviewHandler)
    handler.review_root = tmp_path
    inspected = []
    original_fstat = os.fstat

    def fail_inspection(descriptor):
        inspected.append(descriptor)
        raise OSError("injected metadata failure")

    monkeypatch.setattr(os, "fstat", fail_inspection)
    assert handler._open_static_artifact("/images/source.png") is None
    assert len(inspected) == 1
    with pytest.raises(OSError):
        original_fstat(inspected[0])


def test_static_regular_file_remains_readable(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    (images / "source.png").write_bytes(b"image")
    handler = object.__new__(ReviewHandler)
    handler.review_root = Path(tmp_path)
    opened = handler._open_static_artifact("/images/source.png")
    assert opened is not None
    descriptor, filename, size = opened
    try:
        assert (filename, size) == ("source.png", 5)
        assert os.read(descriptor, size) == b"image"
    finally:
        os.close(descriptor)
