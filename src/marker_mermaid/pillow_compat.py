"""Lazy, isolated access to the Pillow image-core snapshot API."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from PIL import Image


class UnsupportedPillowVersion(RuntimeError):
    """The installed Pillow version has no supported private snapshot layout."""


class PillowImageStateAccessError(RuntimeError):
    """A Pillow image's base instance state could not be read."""


class PillowImageStateShapeError(RuntimeError):
    """A Pillow image's base instance state is not a plain dictionary."""


class PillowImageCoreError(RuntimeError):
    """A Pillow image does not contain the expected canonical pixel core."""


class PillowImageCopyError(RuntimeError):
    """A Pillow pixel core could not be copied."""


class PillowImageSnapshotBoundaryError(RuntimeError):
    """A copied Pillow pixel core changed its declared mode or dimensions."""


class PillowImageSnapshotTypeError(RuntimeError):
    """A copied Pillow pixel core did not produce a plain base image."""


class PillowImageSnapshotLoadError(RuntimeError):
    """A copied Pillow image could not be loaded through the base implementation."""


@dataclass(frozen=True, slots=True)
class _PillowLayout:
    image_class: type
    image_dict_descriptor: Any = field(repr=False)
    mode_state_key: str
    size_state_key: str
    core_type: type = field(repr=False)
    core_state_key: str
    copy_core: Any = field(repr=False)
    new_image: Any = field(repr=False)
    load_image: Any = field(repr=False)


@dataclass(frozen=True, slots=True)
class PillowImageState:
    """Base-instance state captured without invoking image-subclass hooks."""

    mode: object
    size: object
    _core: object = field(repr=False)
    _layout: _PillowLayout = field(repr=False)
    _adapter_token: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedPillowImage:
    """A validated pixel core ready to be copied into a plain Pillow image."""

    mode: str
    size: tuple[int, int]
    _core: object = field(repr=False)
    _layout: _PillowLayout = field(repr=False)
    _adapter_token: object = field(repr=False)


class PillowSnapshotAdapter:
    """Discover and cache Pillow's private snapshot layout on first use."""

    def __init__(self, image_module: Any = Image):
        self._image_module = image_module
        self._layout: _PillowLayout | None = None
        self._unsupported_cause: Exception | None = None
        self._initialization_lock = Lock()
        self._token = object()

    @property
    def initialized(self) -> bool:
        """Whether a supported layout has been discovered."""

        return self._layout is not None

    def _unsupported_error(self) -> UnsupportedPillowVersion:
        return UnsupportedPillowVersion(
            "installed Pillow version uses an unsupported private image layout"
        )

    def _discover_layout(self) -> _PillowLayout:
        try:
            image_class = self._image_module.Image
            image_namespace = image_class.__dict__
            image_dict_descriptor = image_namespace["__dict__"]
            new_image = image_namespace["_new"]
            load_image = image_namespace["load"]

            reference_image = self._image_module.new("RGB", (1, 1))
            reference_state = image_dict_descriptor.__get__(reference_image, image_class)
            if type(reference_state) is not dict:
                raise TypeError("Pillow base image state is not a plain dictionary")
            mode_state_key = next(
                key
                for key in ("_mode", "mode")
                if type(reference_state.get(key)) is str
                and reference_state.get(key) == "RGB"
            )
            size_state_key = next(
                key
                for key in ("_size", "size")
                if type(reference_state.get(key)) is tuple
                and reference_state.get(key) == (1, 1)
            )
            core_state_key = next(
                key for key in ("_im", "im") if reference_state.get(key) is not None
            )
            reference_core = reference_state[core_state_key]
            core_type = type(reference_core)
            copy_core = core_type.copy
            if reference_core.mode != "RGB" or reference_core.size != (1, 1):
                raise AttributeError("Pillow imaging core mode/size API changed")

            copied_core = copy_core(reference_core)
            if (
                type(copied_core) is not core_type
                or copied_core is reference_core
                or copied_core.mode != "RGB"
                or copied_core.size != (1, 1)
            ):
                raise AttributeError("Pillow imaging core copy API changed")
            copied_image = new_image(image_class(), copied_core)
            if type(copied_image) is not image_class:
                raise TypeError("Pillow _new no longer returns a plain base image")
            load_image(copied_image)
            if copied_image.mode != "RGB" or copied_image.size != (1, 1):
                raise AttributeError("Pillow copied image mode/size API changed")
        except Exception as exc:
            raise self._unsupported_error() from exc

        return _PillowLayout(
            image_class=image_class,
            image_dict_descriptor=image_dict_descriptor,
            mode_state_key=mode_state_key,
            size_state_key=size_state_key,
            core_type=core_type,
            core_state_key=core_state_key,
            copy_core=copy_core,
            new_image=new_image,
            load_image=load_image,
        )

    def _resolved_layout(self) -> _PillowLayout:
        if self._layout is not None:
            return self._layout
        if self._unsupported_cause is not None:
            raise self._unsupported_error() from self._unsupported_cause
        with self._initialization_lock:
            if self._layout is not None:
                return self._layout
            if self._unsupported_cause is not None:
                raise self._unsupported_error() from self._unsupported_cause
            try:
                self._layout = self._discover_layout()
            except UnsupportedPillowVersion as exc:
                cause = exc.__cause__
                self._unsupported_cause = (
                    cause.with_traceback(None)
                    if isinstance(cause, Exception)
                    else RuntimeError(str(exc))
                )
                raise
            return self._layout

    def read_image_state(self, image: object) -> PillowImageState:
        """Read trusted base-image storage without running subclass state hooks."""

        layout = self._resolved_layout()
        try:
            image_state = layout.image_dict_descriptor.__get__(image, layout.image_class)
        except Exception as exc:
            raise PillowImageStateAccessError from exc
        if type(image_state) is not dict:
            raise PillowImageStateShapeError
        return PillowImageState(
            mode=image_state.get(layout.mode_state_key),
            size=image_state.get(layout.size_state_key),
            _core=image_state.get(layout.core_state_key),
            _layout=layout,
            _adapter_token=self._token,
        )

    def prepare_image(
        self,
        state: PillowImageState,
        *,
        expected_mode: str,
        expected_size: tuple[int, int],
    ) -> PreparedPillowImage:
        """Validate a captured state against its exact Pillow imaging-core type."""

        if (
            type(state) is not PillowImageState
            or state._adapter_token is not self._token
            or state._layout is not self._resolved_layout()
        ):
            raise PillowImageCoreError
        core = state._core
        layout = state._layout
        try:
            valid_core = (
                type(core) is layout.core_type
                and core.mode == expected_mode
                and core.size == expected_size
            )
        except Exception as exc:
            raise PillowImageCoreError from exc
        if not valid_core:
            raise PillowImageCoreError
        return PreparedPillowImage(
            mode=expected_mode,
            size=expected_size,
            _core=core,
            _layout=layout,
            _adapter_token=self._token,
        )

    def snapshot_image(self, prepared: PreparedPillowImage) -> Image.Image:
        """Copy a validated core with only trusted base Pillow implementations."""

        if (
            type(prepared) is not PreparedPillowImage
            or prepared._adapter_token is not self._token
            or prepared._layout is not self._resolved_layout()
        ):
            raise PillowImageCoreError
        layout = prepared._layout
        source_core = prepared._core
        try:
            snapshot_core = layout.copy_core(source_core)
        except Exception as exc:
            raise PillowImageCopyError from exc
        try:
            valid_snapshot_core = (
                type(snapshot_core) is layout.core_type
                and snapshot_core is not source_core
                and snapshot_core.mode == prepared.mode
                and snapshot_core.size == prepared.size
            )
        except Exception as exc:
            raise PillowImageSnapshotBoundaryError from exc
        if not valid_snapshot_core:
            raise PillowImageSnapshotBoundaryError
        try:
            snapshot = layout.new_image(layout.image_class(), snapshot_core)
        except Exception as exc:
            raise PillowImageSnapshotTypeError from exc
        if type(snapshot) is not layout.image_class:
            raise PillowImageSnapshotTypeError
        try:
            layout.load_image(snapshot)
        except Exception as exc:
            raise PillowImageSnapshotLoadError from exc
        if snapshot.mode != prepared.mode or snapshot.size != prepared.size:
            raise PillowImageSnapshotBoundaryError
        return snapshot


pillow_snapshot_adapter = PillowSnapshotAdapter()


def read_pillow_image_state(image: object) -> PillowImageState:
    """Read base Pillow image state through the process-wide lazy adapter."""

    return pillow_snapshot_adapter.read_image_state(image)


def prepare_pillow_image(
    state: PillowImageState,
    *,
    expected_mode: str,
    expected_size: tuple[int, int],
) -> PreparedPillowImage:
    """Validate image state through the process-wide lazy adapter."""

    return pillow_snapshot_adapter.prepare_image(
        state,
        expected_mode=expected_mode,
        expected_size=expected_size,
    )


def snapshot_pillow_image(prepared: PreparedPillowImage) -> Image.Image:
    """Copy an image through the process-wide lazy adapter."""

    return pillow_snapshot_adapter.snapshot_image(prepared)
