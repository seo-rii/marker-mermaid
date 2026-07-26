"""Marker Mermaid reconstruction extension."""

from typing import TYPE_CHECKING

from marker_mermaid.config import MermaidConfig, Mode, PublishPolicy
from marker_mermaid.models import (
    CandidateValidationReceipt,
    DiagramSceneIR,
    MermaidCandidate,
    PublicationAuthorizationReceipt,
    ReconstructionResult,
)

if TYPE_CHECKING:
    from marker_mermaid.pipeline import ReconstructionPipeline as ReconstructionPipeline

__all__ = [
    "CandidateValidationReceipt",
    "DiagramSceneIR",
    "MermaidCandidate",
    "MermaidConfig",
    "Mode",
    "PublishPolicy",
    "PublicationAuthorizationReceipt",
    "ReconstructionPipeline",
    "ReconstructionResult",
]

__version__ = "0.1.0"


def __getattr__(name: str) -> object:
    if name == "ReconstructionPipeline":
        from marker_mermaid.pipeline import ReconstructionPipeline

        globals()[name] = ReconstructionPipeline
        return ReconstructionPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
