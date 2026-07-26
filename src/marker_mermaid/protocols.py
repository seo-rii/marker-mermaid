"""Replaceable engine contracts used by the synchronous Marker pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image

from marker_mermaid.models import EngineObservation, MermaidCandidate, VisualEvidence


@dataclass(slots=True)
class SourceContext:
    source_id: str
    source_block_ids: list[str]
    source_image_name: str
    image: Image.Image
    views: dict[str, Image.Image] = field(default_factory=dict)
    evidence: list[VisualEvidence] = field(default_factory=list)
    trusted_label_evidence_ids: set[str] = field(default_factory=set)
    trusted_connector_evidence_ids: set[str] = field(default_factory=set)
    trusted_connector_relations: set[tuple[str, str, frozenset[str]]] = field(
        default_factory=set
    )
    conflicted_connector_pairs: set[frozenset[str]] = field(default_factory=set)
    ocr_texts: list[str] = field(default_factory=list)
    source_block: Any = None
    source_blocks: list[Any] = field(default_factory=list)
    vector_sources: list[Any] = field(default_factory=list)
    source_mapping: dict[str, Any] | None = None


class CandidateEngine(Protocol):
    name: str

    def observe(self, context: SourceContext) -> EngineObservation: ...


@dataclass(frozen=True, slots=True)
class RepairProposal:
    """A semantic repair together with the structured state that produced it."""

    code: str
    operation: str
    typed_ir: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)


class RepairEngine(Protocol):
    name: str

    def repair(
        self, context: SourceContext, candidate: MermaidCandidate
    ) -> RepairProposal | None: ...


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    syntax_valid: bool
    render_valid: bool
    diagram_type: str | None = None
    svg: str | None = None
    png: bytes | None = None
    error: str | None = None
    png_omitted_reason: str | None = None


class MermaidRuntime(Protocol):
    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult: ...

    def close(self) -> None: ...
