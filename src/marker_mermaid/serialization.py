"""Result-aware dispatch for deterministic Mermaid serializers.

The original serializer API returns only Mermaid source text.  That is
convenient for callers, but it loses an important distinction when a requested
diagram type is represented with another Mermaid grammar (for example, BPMN
rendered as a portable flowchart).  This module adds that distinction without
requiring existing serializers to change their return type.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias

SerializationStability: TypeAlias = Literal["stable", "extended", "experimental"]


class SerializationContractError(ValueError):
    """Raised when a serializer or result violates the serialization contract."""


class UnknownSerializerError(SerializationContractError):
    """Raised when no serializer is registered for a requested diagram type."""


class SerializerAlreadyRegisteredError(SerializationContractError):
    """Raised when registration would silently replace an existing serializer."""


class StringSerializer(Protocol):
    """The legacy typed-IR serializer shape used by :mod:`serializers`."""

    def __call__(self, ir: Mapping[str, Any], *, experimental: bool = False) -> str: ...


class ResultSerializer(Protocol):
    """A serializer that reports its complete output contract itself."""

    def __call__(
        self, ir: Mapping[str, Any], *, experimental: bool = False
    ) -> SerializationResult: ...


Serializer: TypeAlias = StringSerializer | ResultSerializer


def _diagram_type(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SerializationContractError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise SerializationContractError(f"{field_name} must not contain surrounding whitespace")
    return value


def _messages(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    messages = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in messages):
        raise SerializationContractError(f"{field_name} entries must be non-empty strings")
    if len(messages) != len(set(messages)):
        raise SerializationContractError(f"{field_name} entries must be unique")
    return messages


@dataclass(frozen=True, slots=True)
class SerializationResult:
    """Mermaid text together with the grammar that was actually emitted.

    ``fallback_chain`` always contains the complete route, including the
    requested type as its first item and the emitted Mermaid type as its last.
    A native serialization therefore has a one-item chain.  A fallback must
    include at least one warning so downstream publishing and review surfaces
    cannot accidentally hide the representation change.
    """

    requested_type: str
    emitted_type: str
    code: str
    fallback_chain: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    stability: SerializationStability = "stable"

    def __post_init__(self) -> None:
        requested_type = _diagram_type(self.requested_type, "requested_type")
        emitted_type = _diagram_type(self.emitted_type, "emitted_type")
        if not isinstance(self.code, str) or not self.code.strip():
            raise SerializationContractError("code must contain non-whitespace Mermaid source")
        if "\x00" in self.code:
            raise SerializationContractError("code must not contain NUL bytes")

        chain = tuple(self.fallback_chain) or (requested_type,)
        for index, diagram_type in enumerate(chain):
            _diagram_type(diagram_type, f"fallback_chain[{index}]")
        if chain[0] != requested_type:
            raise SerializationContractError("fallback_chain must start with requested_type")
        if chain[-1] != emitted_type:
            raise SerializationContractError("fallback_chain must end with emitted_type")
        if len(chain) != len(set(chain)):
            raise SerializationContractError("fallback_chain must not contain cycles")
        if requested_type == emitted_type and chain != (requested_type,):
            raise SerializationContractError(
                "native serialization must use a one-item fallback_chain"
            )

        warnings = _messages(self.warnings, "warnings")
        if requested_type != emitted_type and not warnings:
            raise SerializationContractError("fallback serialization must include a warning")
        if self.stability not in {"stable", "extended", "experimental"}:
            raise SerializationContractError("stability must be stable, extended, or experimental")

        object.__setattr__(self, "fallback_chain", chain)
        object.__setattr__(self, "warnings", warnings)

    @property
    def used_fallback(self) -> bool:
        """Whether the output grammar differs from the requested diagram type."""

        return self.requested_type != self.emitted_type

    @classmethod
    def native(
        cls,
        diagram_type: str,
        code: str,
        *,
        warnings: Sequence[str] = (),
        stability: SerializationStability = "stable",
    ) -> SerializationResult:
        """Build a result emitted in the requested diagram grammar."""

        return cls(
            requested_type=diagram_type,
            emitted_type=diagram_type,
            code=code,
            fallback_chain=(diagram_type,),
            warnings=tuple(warnings),
            stability=stability,
        )

    @classmethod
    def fallback(
        cls,
        requested_type: str,
        emitted_type: str,
        code: str,
        *,
        via: Sequence[str] = (),
        warnings: Sequence[str] = (),
        stability: SerializationStability = "experimental",
    ) -> SerializationResult:
        """Build a fallback result and supply a standard warning by default."""

        fallback_warnings = tuple(warnings) or (
            f"Requested {requested_type} was emitted as {emitted_type}.",
        )
        return cls(
            requested_type=requested_type,
            emitted_type=emitted_type,
            code=code,
            fallback_chain=(requested_type, *via, emitted_type),
            warnings=fallback_warnings,
            stability=stability,
        )


@dataclass(frozen=True, slots=True)
class _Registration:
    serializer: Serializer
    returns_result: bool
    emitted_type: str
    fallback_via: tuple[str, ...]
    warnings: tuple[str, ...]
    stability: SerializationStability


class SerializationRegistry:
    """Dispatch legacy and result-aware typed-IR serializers uniformly."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def __contains__(self, requested_type: object) -> bool:
        return requested_type in self._registrations

    def __len__(self) -> int:
        return len(self._registrations)

    @property
    def registered_types(self) -> tuple[str, ...]:
        """Registered request types in deterministic insertion order."""

        return tuple(self._registrations)

    def register_string(
        self,
        requested_type: str,
        serializer: StringSerializer,
        *,
        emitted_type: str | None = None,
        fallback_via: Sequence[str] = (),
        warnings: Sequence[str] = (),
        stability: SerializationStability = "stable",
        replace: bool = False,
    ) -> None:
        """Register an existing serializer that returns Mermaid source text."""

        requested_type = _diagram_type(requested_type, "requested_type")
        emitted_type = _diagram_type(emitted_type or requested_type, "emitted_type")
        via = tuple(fallback_via)
        for index, diagram_type in enumerate(via):
            _diagram_type(diagram_type, f"fallback_via[{index}]")
        if requested_type == emitted_type and via:
            raise SerializationContractError("native registration cannot declare fallback_via")
        fallback_warnings = tuple(warnings)
        if requested_type != emitted_type and not fallback_warnings:
            fallback_warnings = (f"Requested {requested_type} was emitted as {emitted_type}.",)
        registration = _Registration(
            serializer=serializer,
            returns_result=False,
            emitted_type=emitted_type,
            fallback_via=via,
            warnings=_messages(fallback_warnings, "warnings"),
            stability=stability,
        )
        self._set_registration(requested_type, registration, replace=replace)

    def register_result(
        self,
        requested_type: str,
        serializer: ResultSerializer,
        *,
        replace: bool = False,
    ) -> None:
        """Register a serializer that returns :class:`SerializationResult`."""

        requested_type = _diagram_type(requested_type, "requested_type")
        self._set_registration(
            requested_type,
            _Registration(
                serializer=serializer,
                returns_result=True,
                emitted_type=requested_type,
                fallback_via=(),
                warnings=(),
                stability="stable",
            ),
            replace=replace,
        )

    def _set_registration(
        self, requested_type: str, registration: _Registration, *, replace: bool
    ) -> None:
        if not callable(registration.serializer):
            raise SerializationContractError("serializer must be callable")
        if requested_type in self._registrations and not replace:
            raise SerializerAlreadyRegisteredError(
                f"serializer already registered for {requested_type}"
            )
        self._registrations[requested_type] = registration

    def dispatch(
        self,
        requested_type: str,
        ir: Mapping[str, Any],
        *,
        experimental: bool = False,
    ) -> SerializationResult:
        """Serialize typed IR and always return the result-aware contract."""

        requested_type = _diagram_type(requested_type, "requested_type")
        registration = self._registrations.get(requested_type)
        if registration is None:
            raise UnknownSerializerError(f"no typed serializer for {requested_type}")

        output = registration.serializer(ir, experimental=experimental)
        if registration.returns_result:
            if not isinstance(output, SerializationResult):
                raise SerializationContractError(
                    f"result serializer for {requested_type} returned {type(output).__name__}"
                )
            if output.requested_type != requested_type:
                raise SerializationContractError(
                    "result serializer requested_type does not match the dispatched type"
                )
            return output
        if not isinstance(output, str):
            raise SerializationContractError(
                f"string serializer for {requested_type} returned {type(output).__name__}"
            )
        if registration.emitted_type == requested_type:
            return SerializationResult.native(
                requested_type,
                output,
                warnings=registration.warnings,
                stability=registration.stability,
            )
        return SerializationResult.fallback(
            requested_type,
            registration.emitted_type,
            output,
            via=registration.fallback_via,
            warnings=registration.warnings,
            stability=registration.stability,
        )


def registry_from_string_serializers(
    serializers: Mapping[str, StringSerializer],
    *,
    emitted_types: Mapping[str, str] | None = None,
    fallback_paths: Mapping[str, Sequence[str]] | None = None,
    warnings: Mapping[str, Sequence[str]] | None = None,
    stabilities: Mapping[str, SerializationStability] | None = None,
) -> SerializationRegistry:
    """Wrap a legacy serializer mapping with optional fallback metadata."""

    emitted_types = emitted_types or {}
    fallback_paths = fallback_paths or {}
    warnings = warnings or {}
    stabilities = stabilities or {}
    unknown_metadata = (
        set(emitted_types) | set(fallback_paths) | set(warnings) | set(stabilities)
    ) - set(serializers)
    if unknown_metadata:
        raise SerializationContractError(
            f"serializer metadata references unregistered types: {sorted(unknown_metadata)}"
        )

    registry = SerializationRegistry()
    for requested_type, serializer in serializers.items():
        registry.register_string(
            requested_type,
            serializer,
            emitted_type=emitted_types.get(requested_type),
            fallback_via=fallback_paths.get(requested_type, ()),
            warnings=warnings.get(requested_type, ()),
            stability=stabilities.get(requested_type, "stable"),
        )
    return registry
