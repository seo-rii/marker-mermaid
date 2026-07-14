"""Structured VLM and deterministic fixture adapters."""

from __future__ import annotations

import hashlib
import json
import math
import re
from itertools import islice
from pathlib import Path
from typing import Any

from PIL import Image

from marker_mermaid.config import (
    ALL_TYPES,
    MAX_VLM_PROMPT_CHARS,
    MAX_VLM_PROMPT_ITEMS,
    MAX_VLM_TOTAL_VIEW_PIXELS,
    MAX_VLM_VIEW_DIMENSION,
    MAX_VLM_VIEW_PIXELS,
    MAX_VLM_VIEWS,
    MIN_VLM_PROMPT_CHARS,
)
from marker_mermaid.models import (
    MAX_EVIDENCE_REFS,
    MAX_ID_CHARS,
    MAX_OBSERVATION_EVIDENCE,
    MAX_OBSERVATION_WARNINGS,
    MAX_TEXT_CHARS,
    EngineObservation,
    PromptBudgetNotice,
    VisualEvidence,
)
from marker_mermaid.protocols import SourceContext
from marker_mermaid.resource_limits import MAX_EVIDENCE_INPUT_CHARS
from marker_mermaid.typed_contracts import typed_ir_contract_prompt

SYSTEM_PROMPT = """You reconstruct diagrams from source images and structural overlays.
Return only data matching the supplied schema. Do not invent unreadable labels or numbers.
Every node and relation must cite evidence_ids. Rank diagram types rather than forcing one.
Provide typed candidates when the type is supported and direct Mermaid only when necessary.
For flowchart and generic_network typed candidates, reuse the exact IDs of matching
scene_ir.elements from this same response; do not rename, normalize, or invent node IDs.
Every semantic typed node must include evidence_ids copied from supplied Prior evidence;
reuse at least one of the same Prior evidence IDs on its matching scene_ir element.
Never self-declare or synthesize evidence IDs.
Never emit click actions, URLs, directives, HTML, callbacks, CSS imports, or remote icons.
"""

MAX_VLM_RESPONSE_SCHEMA_CHARS = 65_536
MAX_VLM_VIEW_NAME_CHARS = 128
MAX_VLM_EVIDENCE_INPUT_CHARS = MAX_EVIDENCE_INPUT_CHARS
MAX_VLM_OCR_INPUT_CHARS = 8_000_000
_PIL_IMAGING_CORE_TYPE = type(Image.new("RGB", (1, 1)).im)
_PIL_IMAGE_DICT_DESCRIPTOR = Image.Image.__dict__["__dict__"]
_VIEW_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*\Z")
_STRUCTURAL_KINDS = ("arrowhead", "line_segment", "contour", "vector_text")
_EVIDENCE_KINDS = frozenset(
    {
        "source_crop",
        "ocr_token",
        "vector_text",
        "contour",
        "line_segment",
        "arrowhead",
        "vlm_observation",
        "user_edit",
    }
)
_SELECTION_PROFILE = "structural-quota-v1"
_MIN_EVIDENCE_JSON_CHARS = len(
    json.dumps(
        {
            "id": "x",
            "kind": "contour",
            "bbox": None,
            "text": None,
            "font_weight": None,
            "score": None,
            "source_block_ids": [],
        },
        separators=(",", ":"),
    )
)


class StructuredVLMRequestError(RuntimeError):
    """A provider/response failure with the adapter-owned request audit notice."""

    def __init__(self, message: str, prompt_budget_notice: PromptBudgetNotice):
        super().__init__(message)
        if type(prompt_budget_notice) is not PromptBudgetNotice:
            raise TypeError("prompt_budget_notice must be canonical")
        self.prompt_budget_notice = PromptBudgetNotice.model_validate(
            prompt_budget_notice.model_dump(mode="python")
        )


class _MarkerOllamaEngineObservation(EngineObservation):
    """Schema-only adapter for Marker 1.10.2 Ollama's dropped ``$defs`` wrapper."""

    @classmethod
    def model_json_schema(cls, *args, **kwargs) -> dict[str, Any]:
        schema = EngineObservation.model_json_schema(*args, **kwargs)
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict):
            raise ValueError("response schema has no local definitions to inline")

        # Recursive local references require a recursive visitor. Keeping this
        # callback nested prevents it from becoming a misleading general helper.
        def expand(value: Any, stack: frozenset[str]) -> Any:
            if isinstance(value, list):
                return [expand(item, stack) for item in value]
            if not isinstance(value, dict):
                return value
            reference = value.get("$ref")
            if reference is not None:
                if type(reference) is not str or not reference.startswith("#/$defs/"):
                    raise ValueError("response schema contains a non-local reference")
                if len(value) != 1:
                    raise ValueError("response schema local references cannot have sibling fields")
                definition_name = reference.removeprefix("#/$defs/")
                if (
                    not definition_name
                    or "/" in definition_name
                    or definition_name not in definitions
                ):
                    raise ValueError("response schema contains an invalid local reference")
                if definition_name in stack:
                    raise ValueError("response schema contains a recursive local reference")
                return expand(definitions[definition_name], stack | {definition_name})
            return {key: expand(item, stack) for key, item in value.items() if key != "$defs"}

        inlined = expand(schema, frozenset())
        if not isinstance(inlined, dict):  # pragma: no cover - root schema invariant
            raise ValueError("response schema root must remain an object")
        encoded = json.dumps(
            inlined,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded) > MAX_VLM_RESPONSE_SCHEMA_CHARS:
            raise ValueError("inlined response schema exceeds its character limit")
        return inlined


class MarkerStructuredVLMEngine:
    """Adapter around Marker 1.10.2's BaseService response-schema API."""

    name = "marker_structured_vlm"
    fusion_source = "vlm"

    def __init__(
        self,
        llm_service: Any,
        *,
        enabled_types: set[str] | None = None,
        max_prompt_chars: int = 100_000,
        max_evidence_items: int = 256,
        max_ocr_items: int = 512,
        max_views: int = 8,
    ):
        self.llm_service = llm_service
        self.enabled_types = set(enabled_types) if enabled_types is not None else set(ALL_TYPES)
        unknown_types = self.enabled_types - ALL_TYPES
        if unknown_types or not self.enabled_types:
            detail = ", ".join(sorted(unknown_types)) if unknown_types else "empty allowlist"
            raise ValueError(f"invalid enabled diagram types: {detail}")
        if (
            type(max_prompt_chars) is not int
            or not MIN_VLM_PROMPT_CHARS <= max_prompt_chars <= MAX_VLM_PROMPT_CHARS
        ):
            raise ValueError(
                f"max_prompt_chars must be between {MIN_VLM_PROMPT_CHARS} "
                f"and {MAX_VLM_PROMPT_CHARS}"
            )
        if (
            type(max_evidence_items) is not int
            or not 1 <= max_evidence_items <= MAX_VLM_PROMPT_ITEMS
        ):
            raise ValueError(f"max_evidence_items must be between 1 and {MAX_VLM_PROMPT_ITEMS}")
        if type(max_ocr_items) is not int or not 0 <= max_ocr_items <= MAX_VLM_PROMPT_ITEMS:
            raise ValueError(f"max_ocr_items must be between 0 and {MAX_VLM_PROMPT_ITEMS}")
        if type(max_views) is not int or not 1 <= max_views <= MAX_VLM_VIEWS:
            raise ValueError(f"max_views must be between 1 and {MAX_VLM_VIEWS}")
        schema_example = EngineObservation.model_json_schema()
        schema_system_prompt = (
            "Follow the instructions given by the user prompt.  You must provide your response "
            "in JSON format matching this schema:\n\n"
            f"{json.dumps(schema_example, indent=2)}\n\n"
            "Respond only with the JSON schema, nothing else.  Do not include ```json, ```,  "
            "or any other formatting."
        )
        schema_reserve_chars = len(schema_system_prompt)
        self.response_schema: type[EngineObservation] = EngineObservation
        service_type = type(llm_service)
        if (
            service_type.__module__ == "marker.services.ollama"
            and service_type.__name__ == "OllamaService"
        ):
            self.response_schema = _MarkerOllamaEngineObservation
            ollama_schema = self.response_schema.model_json_schema()
            ollama_format_schema = {
                "type": "object",
                "properties": ollama_schema["properties"],
                "required": ollama_schema["required"],
            }
            schema_reserve_chars = max(
                schema_reserve_chars,
                len(
                    json.dumps(
                        ollama_format_schema,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                ),
            )
        if schema_reserve_chars > MAX_VLM_RESPONSE_SCHEMA_CHARS:
            raise ValueError("EngineObservation response schema exceeds its character limit")
        self.max_prompt_chars = max_prompt_chars
        self.max_evidence_items = max_evidence_items
        self.max_ocr_items = max_ocr_items
        self.max_views = max_views
        self.response_schema_chars_reserved = schema_reserve_chars

    def observe(self, context: SourceContext) -> EngineObservation:
        if self.llm_service is None:
            raise RuntimeError("Marker LLM service is not configured")
        if type(context.evidence) is not list:
            raise RuntimeError("Structured VLM prior evidence must be an exact plain list")
        evidence_context = context.evidence[: MAX_OBSERVATION_EVIDENCE + 1]
        if len(evidence_context) > MAX_OBSERVATION_EVIDENCE:
            raise RuntimeError("Structured VLM prior evidence exceeds the observation item limit")
        if type(evidence_context) is not list:  # pragma: no cover - exact-list invariant
            raise RuntimeError("Structured VLM prior evidence snapshot must be a plain list")
        evidence_payloads: list[dict[str, Any]] = []
        evidence_input_chars = 0
        for item in evidence_context:
            if type(item) is not VisualEvidence:
                raise RuntimeError(
                    "Structured VLM prior evidence must contain canonical VisualEvidence records"
                )
            try:
                item_id = item.id
                kind = item.kind
                text = item.text
                font_weight = item.font_weight
                bbox = item.bbox
                score = item.score
                live_source_block_ids = item.source_block_ids
                if type(item_id) is not str or not item_id or len(item_id) > MAX_ID_CHARS:
                    raise TypeError
                item_id.encode("utf-8")
                if type(kind) is not str or len(kind) > 32 or kind not in _EVIDENCE_KINDS:
                    raise TypeError
                if text is not None and (type(text) is not str or len(text) > MAX_TEXT_CHARS):
                    raise TypeError
                if text is not None:
                    text.encode("utf-8")
                if font_weight is not None and (
                    type(font_weight) is not str or font_weight not in {"normal", "bold"}
                ):
                    raise TypeError
                if bbox is not None and (
                    type(bbox) is not tuple
                    or len(bbox) != 4
                    or any(type(value) not in {int, float} for value in bbox)
                    or not all(math.isfinite(value) for value in bbox)
                ):
                    raise TypeError
                if score is not None and (
                    type(score) not in {int, float}
                    or not math.isfinite(score)
                    or not 0 <= score <= 1
                ):
                    raise TypeError
                if type(live_source_block_ids) is not list:
                    raise TypeError
                source_block_ids = live_source_block_ids[: MAX_EVIDENCE_REFS + 1]
                if len(source_block_ids) > MAX_EVIDENCE_REFS:
                    raise TypeError
                for source_block_id in source_block_ids:
                    if (
                        type(source_block_id) is not str
                        or not source_block_id
                        or len(source_block_id) > MAX_ID_CHARS
                    ):
                        raise TypeError
                    source_block_id.encode("utf-8")
                    evidence_input_chars += len(source_block_id)
                    if evidence_input_chars > MAX_VLM_EVIDENCE_INPUT_CHARS:
                        break
                evidence_input_chars += len(item_id) + len(kind)
                if text is not None:
                    evidence_input_chars += len(text)
                if font_weight is not None:
                    evidence_input_chars += len(font_weight)
                evidence_payloads.append(
                    {
                        "id": item_id,
                        "kind": kind,
                        "bbox": bbox,
                        "text": text,
                        "font_weight": font_weight,
                        "score": score,
                        "source_block_ids": source_block_ids,
                    }
                )
            except (AttributeError, TypeError, UnicodeEncodeError) as exc:
                raise RuntimeError(
                    "Structured VLM prior evidence failed canonical structure preflight"
                ) from exc
            if evidence_input_chars > MAX_VLM_EVIDENCE_INPUT_CHARS:
                raise RuntimeError(
                    "Structured VLM prior evidence exceeds the aggregate input character budget"
                )

        canonical_evidence: list[VisualEvidence] = []
        for payload in evidence_payloads:
            try:
                canonical_evidence.append(VisualEvidence.model_validate(payload))
            except Exception as exc:
                raise RuntimeError(
                    "Structured VLM prior evidence failed canonical validation"
                ) from exc

        if type(context.views) is not dict:
            raise RuntimeError("Structured VLM views must be an ordered plain dictionary")
        view_items = list(islice(context.views.items(), self.max_views + 1))
        if not view_items or len(view_items) > self.max_views:
            raise RuntimeError(
                f"Structured VLM views must contain between 1 and {self.max_views} images"
            )
        first_view_name = view_items[0][0]
        if type(first_view_name) is not str or first_view_name != "original":
            raise RuntimeError("Structured VLM views must start with the original image")
        total_view_pixels = 0
        validated_view_cores: list[tuple[str, _PIL_IMAGING_CORE_TYPE, int, int]] = []
        for name, view in view_items:
            if (
                type(name) is not str
                or not name
                or len(name) > MAX_VLM_VIEW_NAME_CHARS
                or _VIEW_NAME_PATTERN.fullmatch(name) is None
            ):
                raise RuntimeError("Structured VLM view names must be bounded portable IDs")
            if not isinstance(view, Image.Image):
                raise RuntimeError("Structured VLM views must contain PIL images")
            try:
                image_state = _PIL_IMAGE_DICT_DESCRIPTOR.__get__(view, Image.Image)
            except Exception as exc:
                raise RuntimeError(
                    "Structured VLM view has no readable Pillow image state"
                ) from exc
            if type(image_state) is not dict:
                raise RuntimeError("Structured VLM view has no canonical Pillow image state")
            declared_mode = image_state.get("_mode")
            declared_size = image_state.get("_size")
            source_core = image_state.get("im")
            if (
                type(declared_mode) is not str
                or declared_mode != "RGB"
                or type(declared_size) is not tuple
                or len(declared_size) != 2
                or type(declared_size[0]) is not int
                or type(declared_size[1]) is not int
            ):
                raise RuntimeError("Structured VLM view image exceeds its RGB size boundary")
            width, height = declared_size
            if (
                width <= 0
                or height <= 0
                or width > MAX_VLM_VIEW_DIMENSION
                or height > MAX_VLM_VIEW_DIMENSION
                or width * height > MAX_VLM_VIEW_PIXELS
            ):
                raise RuntimeError("Structured VLM view image exceeds its RGB size boundary")
            next_total_view_pixels = total_view_pixels + width * height
            if next_total_view_pixels > MAX_VLM_TOTAL_VIEW_PIXELS:
                raise RuntimeError("Structured VLM views exceed the aggregate pixel boundary")
            if (
                type(source_core) is not _PIL_IMAGING_CORE_TYPE
                or source_core.mode != "RGB"
                or source_core.size != (width, height)
            ):
                raise RuntimeError("Structured VLM view has no canonical RGB pixel core")
            total_view_pixels = next_total_view_pixels
            validated_view_cores.append((name, source_core, width, height))

        # Provider adapters may retain or inspect their image arguments after this
        # method returns.  Never hand them the caller-owned objects that were checked
        # above: a Pillow subclass can expose stateful size/mode properties, and a
        # concurrent owner can otherwise mutate a valid image after the size gate.
        validated_view_items: list[tuple[str, Image.Image]] = []
        snapshot_total_pixels = 0
        for name, source_core, expected_width, expected_height in validated_view_cores:
            try:
                snapshot_core = _PIL_IMAGING_CORE_TYPE.copy(source_core)
            except Exception as exc:
                raise RuntimeError("Structured VLM view image could not be snapshotted") from exc
            if (
                type(snapshot_core) is not _PIL_IMAGING_CORE_TYPE
                or snapshot_core is source_core
                or snapshot_core.mode != "RGB"
                or snapshot_core.size != (expected_width, expected_height)
            ):
                raise RuntimeError("Structured VLM view snapshot changed its RGB size boundary")
            snapshot = Image.Image._new(Image.Image(), snapshot_core)
            if type(snapshot) is not Image.Image:  # pragma: no cover - Pillow _new invariant
                raise RuntimeError("Structured VLM view snapshot must be a plain Pillow image")
            try:
                Image.Image.load(snapshot)
            except Exception as exc:
                raise RuntimeError("Structured VLM view snapshot is closed or invalid") from exc
            width, height = snapshot.size
            if (
                snapshot.mode != "RGB"
                or type(width) is not int
                or type(height) is not int
                or (width, height) != (expected_width, expected_height)
                or width <= 0
                or height <= 0
                or width > MAX_VLM_VIEW_DIMENSION
                or height > MAX_VLM_VIEW_DIMENSION
                or width * height > MAX_VLM_VIEW_PIXELS
            ):
                raise RuntimeError("Structured VLM view snapshot changed its RGB size boundary")
            snapshot_total_pixels += width * height
            if snapshot_total_pixels > MAX_VLM_TOTAL_VIEW_PIXELS:
                raise RuntimeError(
                    "Structured VLM view snapshots exceed the aggregate pixel boundary"
                )
            validated_view_items.append((name, snapshot))

        view_manifest = json.dumps(
            [
                {"name": name, "width": view.width, "height": view.height}
                for name, view in validated_view_items
            ],
            ensure_ascii=False,
            allow_nan=False,
        )
        fixed_prefix = (
            SYSTEM_PROMPT
            + "\n"
            + typed_ir_contract_prompt(self.enabled_types)
            + "\nView manifest (images use this exact order): "
            + view_manifest
        )
        evidence_total = len(canonical_evidence)
        if type(context.ocr_texts) is not list:
            raise RuntimeError("Structured VLM OCR context must be an exact plain list")
        ocr_total = len(context.ocr_texts)
        selected_ocr_context = context.ocr_texts[: self.max_ocr_items]
        if type(selected_ocr_context) is not list:  # pragma: no cover - exact-list invariant
            raise RuntimeError("Structured VLM selected OCR snapshot must be a plain list")
        ocr_input_chars = 0
        for text in selected_ocr_context:
            if type(text) is not str:
                raise RuntimeError("Structured VLM selected OCR context must contain plain strings")
            ocr_input_chars += len(text)
            if ocr_input_chars > MAX_VLM_OCR_INPUT_CHARS:
                raise RuntimeError(
                    "Structured VLM selected OCR context exceeds the aggregate input "
                    "character budget"
                )
        summary_reserve = json.dumps(
            {
                "selection_profile": _SELECTION_PROFILE,
                "schema_chars_reserved": self.response_schema_chars_reserved,
                "evidence_considered": evidence_total,
                "evidence_included": evidence_total,
                "evidence_total": evidence_total,
                "ocr_considered": ocr_total,
                "ocr_included": ocr_total,
                "ocr_total": ocr_total,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        section_overhead = (
            "\nPrompt selection: " + summary_reserve + "\nPrior evidence: []" + "\nOCR tokens: []"
        )
        fixed_size = len(fixed_prefix) + len(section_overhead) + self.response_schema_chars_reserved
        if fixed_size > self.max_prompt_chars:
            raise RuntimeError(
                "Structured VLM request fixed content exceeds max_prompt_chars; "
                "provider was not called"
            )

        live_trusted_connector_ids = context.trusted_connector_evidence_ids
        if type(live_trusted_connector_ids) not in {set, frozenset}:
            raise RuntimeError("Structured VLM trusted connector evidence IDs must be an exact set")
        if len(live_trusted_connector_ids) > MAX_OBSERVATION_EVIDENCE:
            raise RuntimeError(
                "Structured VLM trusted connector evidence IDs exceeds the evidence item limit"
            )
        connector_snapshot = tuple(islice(live_trusted_connector_ids, MAX_OBSERVATION_EVIDENCE + 1))
        if len(connector_snapshot) > MAX_OBSERVATION_EVIDENCE or len(connector_snapshot) != len(
            live_trusted_connector_ids
        ):
            raise RuntimeError(
                "Structured VLM trusted connector evidence IDs changed while it was snapshotted"
            )
        for evidence_id in connector_snapshot:
            if type(evidence_id) is not str or not evidence_id or len(evidence_id) > MAX_ID_CHARS:
                raise RuntimeError(
                    "Structured VLM trusted connector evidence IDs contains an invalid identifier"
                )
            try:
                evidence_id.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise RuntimeError(
                    "Structured VLM trusted connector evidence IDs contains an invalid identifier"
                ) from exc
        trusted_connector_ids = frozenset(connector_snapshot)

        live_trusted_label_ids = context.trusted_label_evidence_ids
        if type(live_trusted_label_ids) not in {set, frozenset}:
            raise RuntimeError("Structured VLM trusted label evidence IDs must be an exact set")
        if len(live_trusted_label_ids) > MAX_OBSERVATION_EVIDENCE:
            raise RuntimeError(
                "Structured VLM trusted label evidence IDs exceeds the evidence item limit"
            )
        label_snapshot = tuple(islice(live_trusted_label_ids, MAX_OBSERVATION_EVIDENCE + 1))
        if len(label_snapshot) > MAX_OBSERVATION_EVIDENCE or len(label_snapshot) != len(
            live_trusted_label_ids
        ):
            raise RuntimeError(
                "Structured VLM trusted label evidence IDs changed while it was snapshotted"
            )
        for evidence_id in label_snapshot:
            if type(evidence_id) is not str or not evidence_id or len(evidence_id) > MAX_ID_CHARS:
                raise RuntimeError(
                    "Structured VLM trusted label evidence IDs contains an invalid identifier"
                )
            try:
                evidence_id.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise RuntimeError(
                    "Structured VLM trusted label evidence IDs contains an invalid identifier"
                ) from exc
        trusted_label_ids = frozenset(label_snapshot)
        kind_priority = {
            "user_edit": 0,
            "arrowhead": 3,
            "line_segment": 4,
            "contour": 5,
            "vector_text": 6,
            "source_crop": 7,
            "ocr_token": 8,
            "vlm_observation": 9,
        }
        ranked_evidence_indices = sorted(
            range(len(canonical_evidence)),
            key=lambda index: (
                0
                if canonical_evidence[index].kind == "user_edit"
                else 1
                if canonical_evidence[index].id in trusted_connector_ids
                else 2
                if canonical_evidence[index].id in trusted_label_ids
                else kind_priority[canonical_evidence[index].kind],
                index,
            ),
        )
        critical_evidence_indices = [
            index
            for index in ranked_evidence_indices
            if canonical_evidence[index].kind == "user_edit"
            or canonical_evidence[index].id in trusted_connector_ids
        ]
        structural_buckets = {
            kind: [index for index, item in enumerate(canonical_evidence) if item.kind == kind]
            for kind in _STRUCTURAL_KINDS
        }
        structural_offsets = {kind: 0 for kind in _STRUCTURAL_KINDS}
        structural_evidence_indices: list[int] = []
        while True:
            progressed = False
            for kind in _STRUCTURAL_KINDS:
                offset = structural_offsets[kind]
                bucket = structural_buckets[kind]
                if offset >= len(bucket):
                    continue
                index = bucket[offset]
                structural_offsets[kind] += 1
                structural_evidence_indices.append(index)
                progressed = True
            if not progressed:
                break
        selected_evidence: list[str] = []
        selected_evidence_ids: set[str] = set()
        selected_ocr: list[str] = []
        evidence_chars = 0
        ocr_chars = 0
        evidence_considered = 0
        evidence_char_omission = False
        considered_evidence_indices: set[int] = set()
        evidence_budget_exhausted = False
        critical_target = min(
            len(critical_evidence_indices),
            max(1, self.max_evidence_items // 8),
        )
        critical_included = 0
        critical_offset = 0
        structural_target: int | None = None
        structural_included = 0
        structural_offset = 0
        ranked_offset = 0
        selection_phase = "critical"
        while not evidence_budget_exhausted:
            if selection_phase == "critical":
                if critical_included >= critical_target or critical_offset >= len(
                    critical_evidence_indices
                ):
                    remaining_slots = max(0, self.max_evidence_items - len(selected_evidence))
                    structural_target = max(1, (remaining_slots + 3) // 4) if remaining_slots else 0
                    selection_phase = "structural"
                    continue
                index = critical_evidence_indices[critical_offset]
                critical_offset += 1
            elif selection_phase == "structural":
                if (
                    structural_target is None  # pragma: no cover - phase invariant
                    or structural_included >= structural_target
                    or structural_offset >= len(structural_evidence_indices)
                ):
                    selection_phase = "ranked"
                    continue
                index = structural_evidence_indices[structural_offset]
                structural_offset += 1
            else:
                if len(selected_evidence) >= self.max_evidence_items or ranked_offset >= len(
                    ranked_evidence_indices
                ):
                    break
                index = ranked_evidence_indices[ranked_offset]
                ranked_offset += 1

            if index in considered_evidence_indices:
                continue
            considered_evidence_indices.add(index)
            item = canonical_evidence[index]
            evidence_considered += 1
            comma_chars = 1 if selected_evidence else 0
            remaining_chars = self.max_prompt_chars - fixed_size - evidence_chars - ocr_chars
            if remaining_chars < _MIN_EVIDENCE_JSON_CHARS + comma_chars:
                evidence_char_omission = True
                evidence_budget_exhausted = True
                break

            lower_bound = len(
                '{"id":,"kind":,"bbox":,"text":,"font_weight":,"score":,"source_block_ids":}'
            )
            lower_bound += 4 if item.bbox is None else 9
            lower_bound += 4 if item.score is None else 1
            lower_bound += 2 + max(0, len(item.source_block_ids) - 1)
            required_strings: tuple[str, ...] = (item.id, item.kind)
            optional_strings = (item.text, item.font_weight)
            for string_value in required_strings:
                escaped_chars = 2
                for string_index, character in enumerate(string_value):
                    codepoint = ord(character)
                    if 0xD800 <= codepoint <= 0xDFFF:
                        raise UnicodeEncodeError(
                            "utf-8",
                            string_value,
                            string_index,
                            string_index + 1,
                            "surrogates not allowed",
                        )
                    if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
                        escaped_chars += 2
                    elif codepoint < 0x20:
                        escaped_chars += 6
                    else:
                        escaped_chars += 1
                lower_bound += escaped_chars
            for string_value in optional_strings:
                if string_value is None:
                    lower_bound += 4
                    continue
                escaped_chars = 2
                for string_index, character in enumerate(string_value):
                    codepoint = ord(character)
                    if 0xD800 <= codepoint <= 0xDFFF:
                        raise UnicodeEncodeError(
                            "utf-8",
                            string_value,
                            string_index,
                            string_index + 1,
                            "surrogates not allowed",
                        )
                    if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
                        escaped_chars += 2
                    elif codepoint < 0x20:
                        escaped_chars += 6
                    else:
                        escaped_chars += 1
                lower_bound += escaped_chars
            for string_value in item.source_block_ids:
                escaped_chars = 2
                for string_index, character in enumerate(string_value):
                    codepoint = ord(character)
                    if 0xD800 <= codepoint <= 0xDFFF:
                        raise UnicodeEncodeError(
                            "utf-8",
                            string_value,
                            string_index,
                            string_index + 1,
                            "surrogates not allowed",
                        )
                    if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
                        escaped_chars += 2
                    elif codepoint < 0x20:
                        escaped_chars += 6
                    else:
                        escaped_chars += 1
                lower_bound += escaped_chars
            if lower_bound + comma_chars > remaining_chars:
                evidence_char_omission = True
                continue
            encoded = json.dumps(
                item.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            added_chars = len(encoded) + comma_chars
            if fixed_size + evidence_chars + added_chars + ocr_chars <= self.max_prompt_chars:
                selected_evidence.append(encoded)
                selected_evidence_ids.add(item.id)
                evidence_chars += added_chars
                if selection_phase == "critical":
                    critical_included += 1
                elif selection_phase == "structural":
                    structural_included += 1
            else:  # pragma: no cover - finite-number lengths can exceed the lower bound
                evidence_char_omission = True
        ocr_char_omission = False
        for text in selected_ocr_context:
            comma_chars = 1 if selected_ocr else 0
            remaining_chars = self.max_prompt_chars - fixed_size - evidence_chars - ocr_chars
            if len(text) + 2 + comma_chars > remaining_chars:
                ocr_char_omission = True
                continue
            encoded_chars = 2
            try:
                for string_index, character in enumerate(text):
                    codepoint = ord(character)
                    if 0xD800 <= codepoint <= 0xDFFF:
                        raise UnicodeEncodeError(
                            "utf-8",
                            text,
                            string_index,
                            string_index + 1,
                            "surrogates not allowed",
                        )
                    if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
                        encoded_chars += 2
                    elif codepoint < 0x20:
                        encoded_chars += 6
                    else:
                        encoded_chars += 1
            except UnicodeEncodeError as exc:
                raise RuntimeError("Structured VLM OCR context contains invalid Unicode") from exc
            if encoded_chars + comma_chars > remaining_chars:
                ocr_char_omission = True
                continue
            encoded = json.dumps(text, ensure_ascii=False, separators=(",", ":"))
            added_chars = len(encoded) + comma_chars
            if fixed_size + evidence_chars + ocr_chars + added_chars <= self.max_prompt_chars:
                selected_ocr.append(encoded)
                ocr_chars += added_chars
            else:  # pragma: no cover - exact string length preflight invariant
                ocr_char_omission = True
        selection_summary = json.dumps(
            {
                "selection_profile": _SELECTION_PROFILE,
                "schema_chars_reserved": self.response_schema_chars_reserved,
                "evidence_considered": evidence_considered,
                "evidence_included": len(selected_evidence),
                "evidence_total": evidence_total,
                "ocr_considered": len(selected_ocr_context),
                "ocr_included": len(selected_ocr),
                "ocr_total": ocr_total,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        prompt = (
            fixed_prefix
            + "\nPrompt selection: "
            + selection_summary
            + "\nPrior evidence: ["
            + ",".join(selected_evidence)
            + "]\nOCR tokens: ["
            + ",".join(selected_ocr)
            + "]"
        )
        if (
            len(prompt) + self.response_schema_chars_reserved > self.max_prompt_chars
        ):  # pragma: no cover - reserve invariant
            raise RuntimeError("Structured VLM prompt assembly exceeded its character budget")
        try:
            prompt.encode("utf-8")
        except UnicodeEncodeError as exc:  # pragma: no cover - canonical fields are validated
            raise RuntimeError("Structured VLM prompt contains invalid Unicode") from exc
        omission_reasons: list[str] = []
        if evidence_total > self.max_evidence_items:
            omission_reasons.append("evidence_item_limit")
        if evidence_char_omission:
            omission_reasons.append("evidence_char_limit")
        if ocr_total > self.max_ocr_items:
            omission_reasons.append("ocr_item_limit")
        if ocr_char_omission:
            omission_reasons.append("ocr_char_limit")
        selected_evidence_sha256 = hashlib.sha256(
            json.dumps(
                sorted(selected_evidence_ids),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        prompt_budget_notice = PromptBudgetNotice(
            engine=self.name,
            selection_profile=_SELECTION_PROFILE,
            prompt_chars=len(prompt),
            max_prompt_chars=self.max_prompt_chars,
            schema_reserve_chars=self.response_schema_chars_reserved,
            max_evidence_items=self.max_evidence_items,
            max_ocr_items=self.max_ocr_items,
            evidence_total=evidence_total,
            evidence_considered=evidence_considered,
            evidence_included=len(selected_evidence),
            ocr_total=ocr_total,
            ocr_considered=len(selected_ocr_context),
            ocr_included=len(selected_ocr),
            omission_reasons=omission_reasons,
            selected_evidence_sha256=selected_evidence_sha256,
        )
        try:
            response = self.llm_service(
                prompt=prompt,
                image=[view for _name, view in validated_view_items],
                block=context.source_block,
                response_schema=self.response_schema,
            )
        except Exception as exc:
            raise StructuredVLMRequestError(
                "Structured VLM provider request failed",
                prompt_budget_notice,
            ) from exc
        try:
            observation = EngineObservation.model_validate(
                response.model_dump(mode="python")
                if isinstance(response, EngineObservation)
                else response
            )
        except Exception as exc:
            raise StructuredVLMRequestError(
                "Structured VLM response failed canonical validation",
                prompt_budget_notice,
            ) from exc
        budget_warnings: list[str] = []
        if len(selected_evidence) < evidence_total:
            budget_warnings.append(
                "Structured VLM prompt omitted "
                f"{evidence_total - len(selected_evidence)} of {evidence_total} "
                "prior evidence items"
            )
        if len(selected_ocr) < ocr_total:
            budget_warnings.append(
                "Structured VLM prompt omitted "
                f"{ocr_total - len(selected_ocr)} of {ocr_total} OCR text items"
            )
        if budget_warnings:
            payload = observation.model_dump(mode="python")
            payload["warnings"] = (budget_warnings + list(observation.warnings))[
                :MAX_OBSERVATION_WARNINGS
            ]
            observation = EngineObservation.model_validate(payload)
        observation._set_prompt_supplied_prior_evidence_ids(selected_evidence_ids)
        observation._set_prompt_budget_notice(prompt_budget_notice)
        return observation


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
