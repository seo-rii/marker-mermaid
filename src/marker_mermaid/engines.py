"""Structured VLM and deterministic fixture adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from marker_mermaid.config import ALL_TYPES
from marker_mermaid.models import EngineObservation
from marker_mermaid.protocols import SourceContext
from marker_mermaid.typed_contracts import typed_ir_contract_prompt

SYSTEM_PROMPT = """You reconstruct diagrams from source images and structural overlays.
Return only data matching the supplied schema. Do not invent unreadable labels or numbers.
Every node and relation must cite evidence_ids. Rank diagram types rather than forcing one.
Provide typed candidates when the type is supported and direct Mermaid only when necessary.
Never emit click actions, URLs, directives, HTML, callbacks, CSS imports, or remote icons.
"""


class MarkerStructuredVLMEngine:
    """Adapter around Marker 1.10.2's BaseService response-schema API."""

    name = "marker_structured_vlm"
    fusion_source = "vlm"

    def __init__(self, llm_service: Any, *, enabled_types: set[str] | None = None):
        self.llm_service = llm_service
        self.enabled_types = set(enabled_types) if enabled_types is not None else set(ALL_TYPES)

    def observe(self, context: SourceContext) -> EngineObservation:
        if self.llm_service is None:
            raise RuntimeError("Marker LLM service is not configured")
        prior_evidence = [item.model_dump(mode="json") for item in context.evidence[:256]]
        prompt = (
            SYSTEM_PROMPT
            + "\n"
            + typed_ir_contract_prompt(self.enabled_types)
            + "\nView manifest (images use this exact order): "
            + json.dumps(
                [
                    {"name": name, "width": view.width, "height": view.height}
                    for name, view in context.views.items()
                ],
                ensure_ascii=False,
            )
            + "\nOCR tokens: "
            + json.dumps(context.ocr_texts, ensure_ascii=False)
            + "\nPrior evidence: "
            + json.dumps(prior_evidence, ensure_ascii=False)
        )
        response = self.llm_service(
            prompt=prompt,
            image=list(context.views.values()),
            block=context.source_block,
            response_schema=EngineObservation,
        )
        return (
            response
            if isinstance(response, EngineObservation)
            else EngineObservation.model_validate(response)
        )


class JsonFixtureEngine:
    """Deterministic offline engine for examples, CI, and reproducible debugging."""

    name = "json_fixture"
    fusion_source = "other"

    def __init__(self, observation: EngineObservation):
        self.observation = observation

    @classmethod
    def from_path(cls, path: str | Path) -> JsonFixtureEngine:
        return cls(EngineObservation.model_validate_json(Path(path).read_text(encoding="utf-8")))

    def observe(self, context: SourceContext) -> EngineObservation:
        return self.observation.model_copy(deep=True)
