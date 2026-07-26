from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image

from marker_mermaid.pillow_compat import (
    PillowSnapshotAdapter,
    UnsupportedPillowVersion,
)


def test_pillow_layout_discovery_is_lazy_and_cached() -> None:
    source = Image.new("RGB", (2, 1), "purple")
    new_calls = 0

    def tracked_new(mode, size):
        nonlocal new_calls
        new_calls += 1
        return Image.new(mode, size)

    adapter = PillowSnapshotAdapter(
        SimpleNamespace(
            Image=Image.Image,
            new=tracked_new,
        )
    )

    assert not adapter.initialized
    first_state = adapter.read_image_state(source)
    assert adapter.initialized
    assert new_calls == 1

    second_state = adapter.read_image_state(source)
    assert new_calls == 1
    prepared = adapter.prepare_image(
        second_state,
        expected_mode="RGB",
        expected_size=(2, 1),
    )
    snapshot = adapter.snapshot_image(prepared)

    assert first_state.mode == "RGB"
    assert first_state.size == (2, 1)
    assert type(snapshot) is Image.Image
    assert snapshot is not source
    assert snapshot.getpixel((0, 0)) == (128, 0, 128)


def test_unsupported_pillow_layout_failure_is_cached() -> None:
    new_calls = 0

    class ImageWithoutCore:
        def __init__(self):
            self._mode = "RGB"
            self._size = (1, 1)

        def _new(self, _core):
            return self

        def load(self):
            return None

    def missing_core_new(_mode, _size):
        nonlocal new_calls
        new_calls += 1
        return ImageWithoutCore()

    adapter = PillowSnapshotAdapter(
        SimpleNamespace(
            Image=ImageWithoutCore,
            new=missing_core_new,
        )
    )

    for _ in range(2):
        with pytest.raises(UnsupportedPillowVersion) as captured:
            adapter.read_image_state(ImageWithoutCore())
        assert isinstance(captured.value.__cause__, StopIteration)

    assert not adapter.initialized
    assert new_calls == 1


@pytest.mark.parametrize(
    "image_module",
    [
        SimpleNamespace(
            Image=type("ImageWithoutPrivateMethods", (), {}),
            new=lambda _mode, _size: object(),
        ),
        SimpleNamespace(
            Image=Image.Image,
            new=lambda _mode, _size: (_ for _ in ()).throw(
                AttributeError("layout changed")
            ),
        ),
    ],
)
def test_private_layout_errors_become_unsupported_pillow_version(image_module) -> None:
    adapter = PillowSnapshotAdapter(image_module)

    with pytest.raises(UnsupportedPillowVersion) as captured:
        adapter.read_image_state(object())

    assert isinstance(captured.value.__cause__, (KeyError, AttributeError))
