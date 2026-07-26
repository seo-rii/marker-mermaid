"""Atomic, traversal-safe sidecar bundle writer."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
from collections import Counter
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any

from marker_mermaid.mapping_validation import (
    authority_evidence_matches,
    bbox_iou,
    normalize_scene_bbox,
    source_evidence_matches,
)
from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_OBSERVATION_CANDIDATES,
    MAX_OBSERVATION_TYPED_IR_JSON_BYTES,
    NODE_ID_MAPPING_MIN_IOU,
    EvidenceBudgetUsage,
    MermaidCandidate,
    ReconstructionResult,
    TypedIRCandidate,
    canonical_evidence_collection_snapshot,
    canonical_prompt_budget_notice_json,
    canonical_source_mapping_snapshot,
    canonical_typed_ir_snapshot,
)
from marker_mermaid.render_artifacts import MAX_RENDER_BYTES, png_inspection_error

SCHEMA_VERSION = "mmx-sidecar-0.5"
_LINUX_RENAME_NOREPLACE = 1
_DARWIN_RENAME_EXCL = 0x00000004


def _safe_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-").lower()
    if not component or component in {".", ".."}:
        raise ValueError(f"unsafe sidecar component: {value!r}")
    return component


def safe_artifact_component(value: str) -> str:
    """Return the canonical filesystem component used by sidecar artifacts."""

    return _safe_component(value)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def _canonical_candidate_fields(candidate: MermaidCandidate) -> dict[str, Any]:
    """Return a bounded exact-field view without invoking hostile key hooks."""

    if type(candidate) is not MermaidCandidate:
        raise ValueError("sidecar candidate must be canonical")
    fields = object.__getattribute__(candidate, "__dict__")
    if type(fields) is not dict:
        raise ValueError("sidecar candidate fields must be canonical")
    field_count = dict.__len__(fields)
    if field_count > len(MermaidCandidate.model_fields):
        raise ValueError("sidecar candidate exceeds the public field-count limit")
    snapshot = dict.copy(fields)
    if dict.__len__(snapshot) != field_count or dict.__len__(fields) != field_count:
        raise ValueError("sidecar candidate changed while it was captured")
    for field_name in list(dict.keys(snapshot)):
        if type(field_name) is not str or field_name not in MermaidCandidate.model_fields:
            raise ValueError("sidecar candidate contains an unknown public field")
    return snapshot


def _candidate_json(candidate: MermaidCandidate) -> dict[str, Any]:
    _canonical_candidate_fields(candidate)
    return candidate.model_dump(mode="json", exclude={"svg", "png"})


class _AnchoredArtifactPath:
    """A relative artifact path rooted at an already-open directory descriptor."""

    __slots__ = ("directory_fd", "relative")

    def __init__(self, directory_fd: int, relative: str | PurePosixPath):
        normalized = PurePosixPath(relative)
        if (
            normalized.is_absolute()
            or not normalized.parts
            or any(part in {"", ".", ".."} for part in normalized.parts)
        ):
            raise ValueError(f"unsafe anchored artifact path: {relative!r}")
        self.directory_fd = directory_fd
        self.relative = normalized

    @property
    def name(self) -> str:
        return self.relative.name


def _write(path: _AnchoredArtifactPath, data: bytes | str) -> str:
    payload = str(data).encode("utf-8") if isinstance(data, str) else bytes(data)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent_fd = os.dup(path.directory_fd)
    try:
        for component in path.relative.parts[:-1]:
            with suppress(FileExistsError):
                os.mkdir(component, mode=0o700, dir_fd=parent_fd)
            child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        file_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_fd = os.open(path.name, file_flags, 0o600, dir_fd=parent_fd)
        with os.fdopen(file_fd, "wb") as artifact:
            artifact.write(payload)
    finally:
        os.close(parent_fd)
    return hashlib.sha256(payload).hexdigest()


def _rename_noreplace(
    source_fd: int,
    source_name: str,
    destination_fd: int,
    destination_name: str,
) -> None:
    """Atomically publish a directory without replacing an existing entry."""

    if sys.platform.startswith("linux"):
        primitive_name = "renameat2"
        flags = _LINUX_RENAME_NOREPLACE
    elif sys.platform == "darwin":
        primitive_name = "renameatx_np"
        flags = _DARWIN_RENAME_EXCL
    else:
        raise RuntimeError(
            "sidecar publication requires descriptor-anchored no-replace rename support"
        )
    try:
        primitive = getattr(ctypes.CDLL(None, use_errno=True), primitive_name)
    except (AttributeError, OSError) as exc:
        raise RuntimeError(
            "sidecar publication requires descriptor-anchored no-replace rename support"
        ) from exc
    primitive.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    primitive.restype = ctypes.c_int
    ctypes.set_errno(0)
    rename_result = primitive(
        source_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(destination_name),
        flags,
    )
    if rename_result == 0:
        return
    rename_errno = ctypes.get_errno()
    if rename_errno == errno.EEXIST:
        raise FileExistsError(
            rename_errno,
            "sidecar bundle already exists",
            destination_name,
        )
    raise OSError(
        rename_errno,
        "descriptor-anchored no-replace sidecar publication failed",
    )


class SidecarStore:
    def __init__(
        self,
        output_root: str | Path,
        *,
        write_ir: bool = True,
        write_svg: bool = True,
        write_png: bool = True,
        write_alternatives: bool = True,
        write_provenance: bool = True,
    ):
        self.output_root = Path(output_root).resolve()
        self.write_ir = write_ir
        self.write_svg = write_svg
        self.write_png = write_png
        self.write_alternatives = write_alternatives
        self.write_provenance = write_provenance

    def write(
        self,
        result: ReconstructionResult,
        *,
        output_root_fd: int | None = None,
    ) -> str:
        if type(result) is not ReconstructionResult:
            raise TypeError("sidecar writes require a ReconstructionResult")
        try:
            before_evidence_snapshot = canonical_evidence_collection_snapshot(result.evidence)
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError(f"sidecar evidence preflight failed: {exc}") from exc
        before_evidence_usage: EvidenceBudgetUsage = before_evidence_snapshot.usage
        live_result = result
        live_before_selected = result.selected
        try:
            if type(result.alternatives) is not list:
                raise ValueError("sidecar alternatives must be an exact plain list")
            before_alternative_count = list.__len__(result.alternatives)
            if before_alternative_count > MAX_OBSERVATION_CANDIDATES:
                raise ValueError("sidecar alternatives exceed the candidate count limit")
            live_before_alternatives = list.copy(result.alternatives)
            if (
                list.__len__(live_before_alternatives) != before_alternative_count
                or list.__len__(result.alternatives) != before_alternative_count
            ):
                raise ValueError("sidecar alternatives changed while they were captured")

            before_selected = None
            before_typed_ir_bytes = 0
            if live_before_selected is not None:
                before_fields = _canonical_candidate_fields(live_before_selected)
                before_diagram_type = dict.get(before_fields, "diagram_type")
                if type(before_diagram_type) is not str:
                    raise ValueError("sidecar selected diagram type must be a plain string")
                if not before_diagram_type or len(before_diagram_type) > MAX_ID_CHARS:
                    raise ValueError("sidecar selected diagram type exceeds its size limit")
                _ = before_diagram_type.encode("utf-8")
                before_typed_ir = canonical_typed_ir_snapshot(dict.get(before_fields, "typed_ir"))
                if before_typed_ir is not None:
                    before_typed_ir = TypedIRCandidate(
                        diagram_type=before_diagram_type,
                        ir=before_typed_ir,
                        confidence=1.0,
                    ).ir
                    before_typed_ir_bytes += len(
                        json.dumps(
                            before_typed_ir,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    if before_typed_ir_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                        raise ValueError("sidecar typed IR exceeds the aggregate JSON byte limit")
                before_selected = MermaidCandidate.model_copy(
                    live_before_selected,
                    deep=False,
                )
                before_selected.typed_ir = before_typed_ir

            before_alternatives: list[MermaidCandidate] = []
            for live_candidate in live_before_alternatives:
                before_fields = _canonical_candidate_fields(live_candidate)
                before_diagram_type = dict.get(before_fields, "diagram_type")
                if type(before_diagram_type) is not str:
                    raise ValueError("sidecar alternative diagram type must be a plain string")
                if not before_diagram_type or len(before_diagram_type) > MAX_ID_CHARS:
                    raise ValueError("sidecar alternative diagram type exceeds its size limit")
                _ = before_diagram_type.encode("utf-8")
                before_typed_ir = canonical_typed_ir_snapshot(dict.get(before_fields, "typed_ir"))
                if before_typed_ir is not None:
                    before_typed_ir = TypedIRCandidate(
                        diagram_type=before_diagram_type,
                        ir=before_typed_ir,
                        confidence=1.0,
                    ).ir
                    before_typed_ir_bytes += len(
                        json.dumps(
                            before_typed_ir,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    if before_typed_ir_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                        raise ValueError("sidecar typed IR exceeds the aggregate JSON byte limit")
                before_candidate = MermaidCandidate.model_copy(live_candidate, deep=False)
                before_candidate.typed_ir = before_typed_ir
                before_alternatives.append(before_candidate)
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError(
                "sidecar source changed while its snapshot was captured: invalid typed IR"
            ) from exc
        if result.publish and not result.has_authorized_publication():
            raise ValueError(
                "published results must retain their trusted publication authorization"
            )
        try:
            before_source_mapping = canonical_source_mapping_snapshot(result.source_mapping)
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError(
                "sidecar source changed while its snapshot was captured: invalid source mapping"
            ) from exc
        try:
            before_sink_payload = _json_bytes(
                {
                    "selected": (
                        _candidate_json(before_selected)
                        if type(before_selected) is MermaidCandidate
                        else None
                    ),
                    "alternatives": [
                        _candidate_json(candidate) for candidate in before_alternatives
                    ],
                    "evidence": [
                        item.model_dump(mode="json") for item in before_evidence_snapshot.evidence
                    ],
                    "source_mapping": before_source_mapping,
                    "failures": [item.model_dump(mode="json") for item in result.failures],
                    "prompt_budget_notices": canonical_prompt_budget_notice_json(
                        result.prompt_budget_notices
                    ),
                }
            )
            if len(before_sink_payload) > MAX_RENDER_BYTES:
                raise ValueError("sidecar source projection exceeds the snapshot byte limit")
            before_sink_sha256 = hashlib.sha256(before_sink_payload).hexdigest()
            before_quality_sha256 = (
                hashlib.sha256(
                    _json_bytes(
                        {
                            "aggregate_score": before_selected.aggregate_score,
                            "scores": before_selected.scores,
                            "warnings": before_selected.warnings,
                        }
                    )
                ).hexdigest()
                if type(before_selected) is MermaidCandidate
                else None
            )
            before_core = (
                result.source_id,
                result.source_image_name,
                result.source_kind,
                tuple(result.source_block_ids),
                tuple(result.page_ids),
                result.anchor_block_id,
                result.status,
                result.grade,
                result.publish,
                result.review_required,
                (
                    before_selected.candidate_id,
                    before_selected.diagram_type,
                    before_selected.emitted_diagram_type,
                    before_selected.runtime_diagram_type,
                    before_selected.syntax_valid,
                    before_selected.render_valid,
                    hashlib.sha256(before_selected.mermaid_code.encode("utf-8")).hexdigest()
                    if type(before_selected.mermaid_code) is str
                    else None,
                    hashlib.sha256(before_selected.svg.encode("utf-8")).hexdigest()
                    if type(before_selected.svg) is str
                    else None,
                    hashlib.sha256(before_selected.png).hexdigest()
                    if type(before_selected.png) is bytes
                    else None,
                    before_quality_sha256,
                    before_selected.validation_receipt,
                    before_selected._node_id_mapping_seal,
                    before_selected._validation_receipt_seal,
                )
                if type(before_selected) is MermaidCandidate
                else None,
                result.publication_receipt,
                result._publication_authorization_seal,
                before_sink_sha256,
            )
            before_trusted_validation = bool(
                type(before_selected) is MermaidCandidate
                and before_selected.has_validated_publication_artifacts()
            )
            before_trusted_decision = result.has_trusted_publication_decision()
            before_authorized_publication = result.has_authorized_publication()
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError("sidecar source has an invalid publication core") from exc
        # Derive the path, policy decision, artifacts, and manifest exclusively
        # from one snapshot so authorization cannot race later field reads.
        try:
            shallow_result = ReconstructionResult.model_copy(result, deep=False)
            shallow_result.selected = before_selected
            shallow_result.alternatives = before_alternatives
            shallow_result.source_mapping = before_source_mapping
            shallow_result.evidence = list(before_evidence_snapshot.evidence)
            result = ReconstructionResult.model_copy(shallow_result, deep=True)
        except Exception as exc:
            raise ValueError("sidecar snapshot failed publication authorization") from exc
        live_after_selected = live_result.selected
        try:
            if type(live_result.alternatives) is not list:
                raise ValueError("sidecar alternatives must be an exact plain list")
            after_alternative_count = list.__len__(live_result.alternatives)
            if after_alternative_count > MAX_OBSERVATION_CANDIDATES:
                raise ValueError("sidecar alternatives exceed the candidate count limit")
            live_after_alternatives = list.copy(live_result.alternatives)
            if (
                list.__len__(live_after_alternatives) != after_alternative_count
                or list.__len__(live_result.alternatives) != after_alternative_count
            ):
                raise ValueError("sidecar alternatives changed while they were captured")

            after_selected = None
            after_typed_ir_bytes = 0
            if live_after_selected is not None:
                after_fields = _canonical_candidate_fields(live_after_selected)
                after_diagram_type = dict.get(after_fields, "diagram_type")
                if type(after_diagram_type) is not str:
                    raise ValueError("sidecar selected diagram type must be a plain string")
                if not after_diagram_type or len(after_diagram_type) > MAX_ID_CHARS:
                    raise ValueError("sidecar selected diagram type exceeds its size limit")
                _ = after_diagram_type.encode("utf-8")
                after_typed_ir = canonical_typed_ir_snapshot(dict.get(after_fields, "typed_ir"))
                if after_typed_ir is not None:
                    after_typed_ir = TypedIRCandidate(
                        diagram_type=after_diagram_type,
                        ir=after_typed_ir,
                        confidence=1.0,
                    ).ir
                    after_typed_ir_bytes += len(
                        json.dumps(
                            after_typed_ir,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    if after_typed_ir_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                        raise ValueError("sidecar typed IR exceeds the aggregate JSON byte limit")
                after_selected = MermaidCandidate.model_copy(live_after_selected, deep=False)
                after_selected.typed_ir = after_typed_ir

            after_alternatives: list[MermaidCandidate] = []
            for live_candidate in live_after_alternatives:
                after_fields = _canonical_candidate_fields(live_candidate)
                after_diagram_type = dict.get(after_fields, "diagram_type")
                if type(after_diagram_type) is not str:
                    raise ValueError("sidecar alternative diagram type must be a plain string")
                if not after_diagram_type or len(after_diagram_type) > MAX_ID_CHARS:
                    raise ValueError("sidecar alternative diagram type exceeds its size limit")
                _ = after_diagram_type.encode("utf-8")
                after_typed_ir = canonical_typed_ir_snapshot(dict.get(after_fields, "typed_ir"))
                if after_typed_ir is not None:
                    after_typed_ir = TypedIRCandidate(
                        diagram_type=after_diagram_type,
                        ir=after_typed_ir,
                        confidence=1.0,
                    ).ir
                    after_typed_ir_bytes += len(
                        json.dumps(
                            after_typed_ir,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    if after_typed_ir_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                        raise ValueError("sidecar typed IR exceeds the aggregate JSON byte limit")
                after_candidate = MermaidCandidate.model_copy(live_candidate, deep=False)
                after_candidate.typed_ir = after_typed_ir
                after_alternatives.append(after_candidate)

            snapshot_selected = result.selected
            snapshot_typed_ir_bytes = 0
            if snapshot_selected is not None:
                snapshot_fields = _canonical_candidate_fields(snapshot_selected)
                snapshot_diagram_type = dict.get(snapshot_fields, "diagram_type")
                if type(snapshot_diagram_type) is not str:
                    raise ValueError("sidecar snapshot diagram type must be a plain string")
                if not snapshot_diagram_type or len(snapshot_diagram_type) > MAX_ID_CHARS:
                    raise ValueError("sidecar snapshot diagram type exceeds its size limit")
                _ = snapshot_diagram_type.encode("utf-8")
                snapshot_typed_ir = canonical_typed_ir_snapshot(
                    dict.get(snapshot_fields, "typed_ir")
                )
                if snapshot_typed_ir is not None:
                    snapshot_typed_ir = TypedIRCandidate(
                        diagram_type=snapshot_diagram_type,
                        ir=snapshot_typed_ir,
                        confidence=1.0,
                    ).ir
                    snapshot_typed_ir_bytes += len(
                        json.dumps(
                            snapshot_typed_ir,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    if snapshot_typed_ir_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                        raise ValueError("sidecar typed IR exceeds the aggregate JSON byte limit")
                canonical_snapshot_selected = MermaidCandidate.model_copy(
                    snapshot_selected,
                    deep=False,
                )
                canonical_snapshot_selected.typed_ir = snapshot_typed_ir
                snapshot_selected = canonical_snapshot_selected

            if type(result.alternatives) is not list:
                raise ValueError("sidecar snapshot alternatives must be an exact plain list")
            snapshot_alternative_count = list.__len__(result.alternatives)
            if snapshot_alternative_count > MAX_OBSERVATION_CANDIDATES:
                raise ValueError("sidecar snapshot alternatives exceed the candidate count limit")
            live_snapshot_alternatives = list.copy(result.alternatives)
            if (
                list.__len__(live_snapshot_alternatives) != snapshot_alternative_count
                or list.__len__(result.alternatives) != snapshot_alternative_count
            ):
                raise ValueError("sidecar snapshot alternatives changed while they were captured")
            snapshot_alternatives: list[MermaidCandidate] = []
            for snapshot_candidate in live_snapshot_alternatives:
                snapshot_fields = _canonical_candidate_fields(snapshot_candidate)
                snapshot_diagram_type = dict.get(snapshot_fields, "diagram_type")
                if type(snapshot_diagram_type) is not str:
                    raise ValueError("sidecar snapshot diagram type must be a plain string")
                if not snapshot_diagram_type or len(snapshot_diagram_type) > MAX_ID_CHARS:
                    raise ValueError("sidecar snapshot diagram type exceeds its size limit")
                _ = snapshot_diagram_type.encode("utf-8")
                snapshot_typed_ir = canonical_typed_ir_snapshot(
                    dict.get(snapshot_fields, "typed_ir")
                )
                if snapshot_typed_ir is not None:
                    snapshot_typed_ir = TypedIRCandidate(
                        diagram_type=snapshot_diagram_type,
                        ir=snapshot_typed_ir,
                        confidence=1.0,
                    ).ir
                    snapshot_typed_ir_bytes += len(
                        json.dumps(
                            snapshot_typed_ir,
                            allow_nan=False,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    if snapshot_typed_ir_bytes > MAX_OBSERVATION_TYPED_IR_JSON_BYTES:
                        raise ValueError("sidecar typed IR exceeds the aggregate JSON byte limit")
                canonical_snapshot_candidate = MermaidCandidate.model_copy(
                    snapshot_candidate,
                    deep=False,
                )
                canonical_snapshot_candidate.typed_ir = snapshot_typed_ir
                snapshot_alternatives.append(canonical_snapshot_candidate)
            result.selected = snapshot_selected
            result.alternatives = snapshot_alternatives
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError(
                "sidecar source changed while its snapshot was captured: invalid typed IR"
            ) from exc
        try:
            after_source_mapping = canonical_source_mapping_snapshot(live_result.source_mapping)
            snapshot_source_mapping = canonical_source_mapping_snapshot(result.source_mapping)
            result.source_mapping = snapshot_source_mapping
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError(
                "sidecar source changed while its snapshot was captured: invalid source mapping"
            ) from exc
        try:
            after_evidence_snapshot = canonical_evidence_collection_snapshot(live_result.evidence)
            snapshot_evidence_snapshot = canonical_evidence_collection_snapshot(result.evidence)
            result.evidence = list(snapshot_evidence_snapshot.evidence)
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError(
                f"sidecar source changed while its snapshot was captured: invalid evidence: {exc}"
            ) from exc
        try:
            after_sink_payload = _json_bytes(
                {
                    "selected": (
                        _candidate_json(after_selected)
                        if type(after_selected) is MermaidCandidate
                        else None
                    ),
                    "alternatives": [
                        _candidate_json(candidate) for candidate in after_alternatives
                    ],
                    "evidence": [
                        item.model_dump(mode="json") for item in after_evidence_snapshot.evidence
                    ],
                    "source_mapping": after_source_mapping,
                    "failures": [item.model_dump(mode="json") for item in live_result.failures],
                    "prompt_budget_notices": canonical_prompt_budget_notice_json(
                        live_result.prompt_budget_notices
                    ),
                }
            )
            snapshot_sink_payload = _json_bytes(
                {
                    "selected": (
                        _candidate_json(snapshot_selected)
                        if type(snapshot_selected) is MermaidCandidate
                        else None
                    ),
                    "alternatives": [
                        _candidate_json(candidate) for candidate in snapshot_alternatives
                    ],
                    "evidence": [
                        item.model_dump(mode="json") for item in snapshot_evidence_snapshot.evidence
                    ],
                    "source_mapping": snapshot_source_mapping,
                    "failures": [item.model_dump(mode="json") for item in result.failures],
                    "prompt_budget_notices": canonical_prompt_budget_notice_json(
                        result.prompt_budget_notices
                    ),
                }
            )
            if (
                len(after_sink_payload) > MAX_RENDER_BYTES
                or len(snapshot_sink_payload) > MAX_RENDER_BYTES
            ):
                raise ValueError("sidecar snapshot projection exceeds the snapshot byte limit")
            after_sink_sha256 = hashlib.sha256(after_sink_payload).hexdigest()
            snapshot_sink_sha256 = hashlib.sha256(snapshot_sink_payload).hexdigest()
            after_quality_sha256 = (
                hashlib.sha256(
                    _json_bytes(
                        {
                            "aggregate_score": after_selected.aggregate_score,
                            "scores": after_selected.scores,
                            "warnings": after_selected.warnings,
                        }
                    )
                ).hexdigest()
                if type(after_selected) is MermaidCandidate
                else None
            )
            after_core = (
                live_result.source_id,
                live_result.source_image_name,
                live_result.source_kind,
                tuple(live_result.source_block_ids),
                tuple(live_result.page_ids),
                live_result.anchor_block_id,
                live_result.status,
                live_result.grade,
                live_result.publish,
                live_result.review_required,
                (
                    after_selected.candidate_id,
                    after_selected.diagram_type,
                    after_selected.emitted_diagram_type,
                    after_selected.runtime_diagram_type,
                    after_selected.syntax_valid,
                    after_selected.render_valid,
                    hashlib.sha256(after_selected.mermaid_code.encode("utf-8")).hexdigest()
                    if type(after_selected.mermaid_code) is str
                    else None,
                    hashlib.sha256(after_selected.svg.encode("utf-8")).hexdigest()
                    if type(after_selected.svg) is str
                    else None,
                    hashlib.sha256(after_selected.png).hexdigest()
                    if type(after_selected.png) is bytes
                    else None,
                    after_quality_sha256,
                    after_selected.validation_receipt,
                    after_selected._node_id_mapping_seal,
                    after_selected._validation_receipt_seal,
                )
                if type(after_selected) is MermaidCandidate
                else None,
                live_result.publication_receipt,
                live_result._publication_authorization_seal,
                after_sink_sha256,
            )
            snapshot_quality_sha256 = (
                hashlib.sha256(
                    _json_bytes(
                        {
                            "aggregate_score": snapshot_selected.aggregate_score,
                            "scores": snapshot_selected.scores,
                            "warnings": snapshot_selected.warnings,
                        }
                    )
                ).hexdigest()
                if type(snapshot_selected) is MermaidCandidate
                else None
            )
            snapshot_core = (
                result.source_id,
                result.source_image_name,
                result.source_kind,
                tuple(result.source_block_ids),
                tuple(result.page_ids),
                result.anchor_block_id,
                result.status,
                result.grade,
                result.publish,
                result.review_required,
                (
                    snapshot_selected.candidate_id,
                    snapshot_selected.diagram_type,
                    snapshot_selected.emitted_diagram_type,
                    snapshot_selected.runtime_diagram_type,
                    snapshot_selected.syntax_valid,
                    snapshot_selected.render_valid,
                    hashlib.sha256(snapshot_selected.mermaid_code.encode("utf-8")).hexdigest()
                    if type(snapshot_selected.mermaid_code) is str
                    else None,
                    hashlib.sha256(snapshot_selected.svg.encode("utf-8")).hexdigest()
                    if type(snapshot_selected.svg) is str
                    else None,
                    hashlib.sha256(snapshot_selected.png).hexdigest()
                    if type(snapshot_selected.png) is bytes
                    else None,
                    snapshot_quality_sha256,
                    snapshot_selected.validation_receipt,
                    snapshot_selected._node_id_mapping_seal,
                    snapshot_selected._validation_receipt_seal,
                )
                if type(snapshot_selected) is MermaidCandidate
                else None,
                result.publication_receipt,
                result._publication_authorization_seal,
                snapshot_sink_sha256,
            )
        except (AttributeError, TypeError, UnicodeEncodeError, ValueError) as exc:
            raise ValueError("sidecar snapshot has an invalid publication core") from exc
        if (
            after_core != before_core
            or snapshot_core != before_core
            or after_evidence_snapshot.usage != before_evidence_usage
            or snapshot_evidence_snapshot.usage != before_evidence_usage
        ):
            raise ValueError("sidecar source changed while its snapshot was captured")
        if (
            (
                before_trusted_validation
                and (
                    type(snapshot_selected) is not MermaidCandidate
                    or not snapshot_selected.has_validated_publication_artifacts()
                )
            )
            or (before_trusted_decision and not result.has_trusted_publication_decision())
            or (before_authorized_publication and not result.has_authorized_publication())
        ):
            raise ValueError("sidecar snapshot lost trusted publication authorization")
        name = _safe_component(result.source_id)
        relative = PurePosixPath("diagrams") / name
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("sidecar path must remain inside the output root")
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if output_root_fd is None:
            self.output_root.mkdir(parents=True, exist_ok=True)
            try:
                output_fd = os.open(self.output_root, directory_flags)
            except OSError as exc:
                raise ValueError("sidecar output root must be a real directory") from exc
        else:
            try:
                output_fd = os.dup(output_root_fd)
                output_stat = os.fstat(output_fd)
                if not stat.S_ISDIR(output_stat.st_mode):
                    raise ValueError("sidecar output descriptor must reference a directory")
            except Exception:
                with suppress(UnboundLocalError):
                    os.close(output_fd)
                raise
        try:
            with suppress(FileExistsError):
                os.mkdir("diagrams", mode=0o700, dir_fd=output_fd)
            try:
                diagrams_fd = os.open("diagrams", directory_flags, dir_fd=output_fd)
            except OSError as exc:
                raise ValueError(
                    "sidecar diagrams path must be a real direct child of the output root"
                ) from exc
        except Exception:
            os.close(output_fd)
            raise
        diagrams_stat = os.fstat(diagrams_fd)
        diagrams_identity = (diagrams_stat.st_dev, diagrams_stat.st_ino)
        temporary_name: str | None = None
        temporary_fd: int | None = None
        try:
            try:
                os.stat(name, dir_fd=diagrams_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(
                    f"sidecar bundle already exists: {self.output_root / 'diagrams' / name}"
                )
            for _ in range(128):
                proposal = f".{name}-{secrets.token_hex(8)}"
                try:
                    os.mkdir(proposal, mode=0o700, dir_fd=diagrams_fd)
                except FileExistsError:
                    continue
                temporary_name = proposal
                break
            if temporary_name is None:
                raise FileExistsError("unable to allocate a unique sidecar staging directory")
            temporary_stat = os.stat(
                temporary_name,
                dir_fd=diagrams_fd,
                follow_symlinks=False,
            )
            temporary_fd = os.open(temporary_name, directory_flags, dir_fd=diagrams_fd)
            opened_temporary_stat = os.fstat(temporary_fd)
            temporary_identity = (temporary_stat.st_dev, temporary_stat.st_ino)
            if (
                not stat.S_ISDIR(temporary_stat.st_mode)
                or (opened_temporary_stat.st_dev, opened_temporary_stat.st_ino)
                != temporary_identity
            ):
                raise ValueError("sidecar staging directory identity changed before writing")
        except Exception:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_name is not None:
                shutil.rmtree(temporary_name, dir_fd=diagrams_fd, ignore_errors=True)
            os.close(diagrams_fd)
            os.close(output_fd)
            raise
        assert temporary_fd is not None
        assert temporary_name is not None
        hashes: dict[str, str] = {}
        mapping_requires_provenance = False
        trusted_validation_receipt: dict[str, Any] | None = None
        trusted_publication_receipt: dict[str, Any] | None = None
        try:
            current_diagrams_stat = os.stat(
                "diagrams",
                dir_fd=output_fd,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(current_diagrams_stat.st_mode)
                or stat.S_ISLNK(current_diagrams_stat.st_mode)
                or (current_diagrams_stat.st_dev, current_diagrams_stat.st_ino) != diagrams_identity
            ):
                raise ValueError("sidecar diagrams directory identity changed before writing")
            selected = result.selected
            validated_evidence = list(snapshot_evidence_snapshot.evidence)
            has_trusted_decision = result.has_trusted_publication_decision()
            if has_trusted_decision:
                assert result.publication_receipt is not None
                trusted_publication_receipt = result.publication_receipt.model_dump(mode="json")
            if selected is not None:
                if result.publish and not result.has_authorized_publication():
                    raise ValueError(
                        "published results must retain their trusted publication authorization"
                    )
                has_trusted_validation = selected.has_validated_publication_artifacts()
                if result.publish and not has_trusted_validation:
                    raise ValueError(
                        "published candidates must retain their trusted validation receipt"
                    )
                if has_trusted_validation:
                    assert selected.validation_receipt is not None
                    trusted_validation_receipt = selected.validation_receipt.model_dump(mode="json")
                if (
                    result.publish
                    and self.write_png
                    and selected.validation_receipt is not None
                    and (
                        selected.png is not None
                        or selected.validation_receipt.png_sha256 is not None
                    )
                    and not selected.has_validated_rendered_preview()
                ):
                    raise ValueError("published PNG must retain its trusted validation receipt")
                if selected.node_id_mappings and not selected._has_valid_node_id_mapping_seal():
                    raise ValueError(
                        "node id mappings must retain their trusted pipeline certification seal"
                    )
                selected = MermaidCandidate.model_validate(selected.model_dump(mode="python"))
                if (
                    (self.write_svg or result.publish)
                    and selected.svg is not None
                    and len(selected.svg.encode("utf-8")) > MAX_RENDER_BYTES
                ):
                    raise ValueError("selected SVG exceeds the render artifact byte limit")
                if self.write_png and selected.png is not None:
                    png_error = png_inspection_error(selected.png)
                    if png_error is not None:
                        raise ValueError(f"selected PNG failed artifact inspection: {png_error}")
                if selected.mermaid_code:
                    hashes["final.mmd"] = _write(
                        _AnchoredArtifactPath(temporary_fd, "final.mmd"),
                        selected.mermaid_code,
                    )
                if (self.write_svg or result.publish) and selected.svg:
                    hashes["final.svg"] = _write(
                        _AnchoredArtifactPath(temporary_fd, "final.svg"), selected.svg
                    )
                if self.write_png and selected.png:
                    hashes["final.png"] = _write(
                        _AnchoredArtifactPath(temporary_fd, "final.png"), selected.png
                    )
                if self.write_ir and selected.scene_ir is not None:
                    hashes["scene-ir.json"] = _write(
                        _AnchoredArtifactPath(temporary_fd, "scene-ir.json"),
                        _json_bytes(selected.scene_ir.model_dump(mode="json")),
                    )
                if self.write_ir and selected.generated_scene_ir is not None:
                    hashes["generated-scene-ir.json"] = _write(
                        _AnchoredArtifactPath(temporary_fd, "generated-scene-ir.json"),
                        _json_bytes(selected.generated_scene_ir.model_dump(mode="json")),
                    )
                if self.write_ir and selected.typed_ir is not None:
                    hashes["typed-ir.json"] = _write(
                        _AnchoredArtifactPath(temporary_fd, "typed-ir.json"),
                        _json_bytes(selected.typed_ir),
                    )
                if selected.node_id_mappings:
                    mapping_requires_provenance = True
                    evidence_counts = Counter(item.id for item in validated_evidence)
                    evidence_by_id = {item.id: item for item in validated_evidence}
                    mapping_evidence_references = [
                        evidence_id
                        for mapping in selected.node_id_mappings
                        for evidence_id in (
                            *mapping.source_evidence_ids,
                            *mapping.authority_evidence_ids,
                        )
                    ]
                    mapping_reference_counts = Counter(mapping_evidence_references)
                    mapping_evidence_ids = set(mapping_evidence_references)
                    invalid_evidence_ids = sorted(
                        evidence_id
                        for evidence_id in mapping_evidence_ids
                        if evidence_counts[evidence_id] != 1
                        or mapping_reference_counts[evidence_id] != 1
                    )
                    if invalid_evidence_ids:
                        raise ValueError(
                            "node id mapping evidence must occur exactly once in provenance: "
                            f"{invalid_evidence_ids}"
                        )
                    assert selected.scene_ir is not None
                    scene_elements = {element.id: element for element in selected.scene_ir.elements}
                    for mapping in selected.node_id_mappings:
                        source_evidence = [
                            evidence_by_id[evidence_id]
                            for evidence_id in mapping.source_evidence_ids
                        ]
                        authority_evidence = [
                            evidence_by_id[evidence_id]
                            for evidence_id in mapping.authority_evidence_ids
                        ]
                        has_source_text_evidence = any(
                            item.kind in {"ocr_token", "vector_text"} for item in source_evidence
                        )
                        fused_bbox = normalize_scene_bbox(
                            scene_elements[mapping.fused_id].bbox,
                            selected.scene_ir,
                        )
                        fused_evidence_ids = set(scene_elements[mapping.fused_id].evidence_ids)
                        if (
                            (mapping.source_text is not None) != has_source_text_evidence
                            or not all(
                                source_evidence_matches(
                                    item,
                                    selected.scene_ir,
                                    mapping.source_bbox,
                                    mapping.source_text,
                                )
                                for item in source_evidence
                            )
                            or not all(
                                authority_evidence_matches(
                                    item,
                                    selected.scene_ir,
                                    mapping.authority_bbox,
                                    NODE_ID_MAPPING_MIN_IOU,
                                )
                                for item in authority_evidence
                            )
                            or (
                                fused_bbox is None
                                or bbox_iou(fused_bbox, mapping.authority_bbox)
                                < NODE_ID_MAPPING_MIN_IOU
                            )
                            or not set(
                                (
                                    *mapping.source_evidence_ids,
                                    *mapping.authority_evidence_ids,
                                )
                            ).issubset(fused_evidence_ids)
                        ):
                            raise ValueError(
                                "node id mapping evidence must remain spatially/text aligned "
                                f"with source and authority boxes: {mapping.source_id!r}"
                            )
                        source_blocks = {
                            block_id
                            for item in source_evidence
                            for block_id in item.source_block_ids
                        }
                        authority_blocks = {
                            block_id
                            for item in authority_evidence
                            for block_id in item.source_block_ids
                        }
                        shared_blocks = source_blocks.intersection(authority_blocks)
                        result_blocks = set(result.source_block_ids)
                        if (
                            not source_blocks
                            or not authority_blocks
                            or not shared_blocks
                            or not result_blocks
                            or shared_blocks.isdisjoint(result_blocks)
                        ):
                            raise ValueError(
                                "node id mapping source and authority evidence must share "
                                "a source block"
                            )
                    hashes["node-id-map.json"] = _write(
                        _AnchoredArtifactPath(temporary_fd, "node-id-map.json"),
                        _json_bytes(
                            [item.model_dump(mode="json") for item in selected.node_id_mappings]
                        ),
                    )
                hashes["scores.json"] = _write(
                    _AnchoredArtifactPath(temporary_fd, "scores.json"),
                    _json_bytes(
                        {
                            "aggregate_score": selected.aggregate_score,
                            "grade": result.grade,
                            "metrics": selected.scores,
                            "warnings": selected.warnings,
                        }
                    ),
                )
                if trusted_validation_receipt is not None:
                    if hashes.get("final.mmd") != trusted_validation_receipt["code_sha256"]:
                        raise ValueError("written Mermaid source differs from validation receipt")
                    if hashes.get("final.svg") != trusted_validation_receipt["svg_sha256"]:
                        if result.publish:
                            raise ValueError("written SVG differs from validation receipt")
                        trusted_validation_receipt = None
                        trusted_publication_receipt = None
                    elif "final.png" in hashes:
                        if trusted_validation_receipt.get("png_sha256") is None or hashes[
                            "final.png"
                        ] != trusted_validation_receipt.get("png_sha256"):
                            if result.publish:
                                raise ValueError("written PNG differs from validation receipt")
                            trusted_validation_receipt = None
                            trusted_publication_receipt = None
            if (self.write_provenance or mapping_requires_provenance) and validated_evidence:
                hashes["provenance.json"] = _write(
                    _AnchoredArtifactPath(temporary_fd, "provenance.json"),
                    _json_bytes([item.model_dump(mode="json") for item in validated_evidence]),
                )
            if result.source_mapping is not None:
                hashes["source-map.json"] = _write(
                    _AnchoredArtifactPath(temporary_fd, "source-map.json"),
                    _json_bytes(result.source_mapping),
                )
            hashes["review-history.json"] = _write(
                _AnchoredArtifactPath(temporary_fd, "review-history.json"), b"[]\n"
            )
            if self.write_alternatives:
                for candidate in result.alternatives:
                    filename = f"alternatives/{_safe_component(candidate.candidate_id)}.json"
                    hashes[filename] = _write(
                        _AnchoredArtifactPath(temporary_fd, filename),
                        _json_bytes(_candidate_json(candidate)),
                    )
                    if candidate.mermaid_code:
                        mmd_name = f"alternatives/{_safe_component(candidate.candidate_id)}.mmd"
                        hashes[mmd_name] = _write(
                            _AnchoredArtifactPath(temporary_fd, mmd_name),
                            candidate.mermaid_code,
                        )
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "source_id": result.source_id,
                "source_image": f"images/{Path(result.source_image_name).name}",
                "source_kind": result.source_kind,
                "source_block_ids": result.source_block_ids,
                "page_ids": result.page_ids,
                "anchor_block_id": result.anchor_block_id,
                "status": result.status,
                "grade": result.grade,
                "publish": result.publish,
                "review_required": result.review_required,
                "selected_candidate_id": selected.candidate_id if selected else None,
                "requested_diagram_type": selected.diagram_type if selected else None,
                "emitted_diagram_type": selected.emitted_diagram_type if selected else None,
                "runtime_diagram_type": selected.runtime_diagram_type if selected else None,
                "fallback_chain": selected.fallback_chain if selected else [],
                "serialization_stability": (selected.serialization_stability if selected else None),
                "generation_validation_receipt": trusted_validation_receipt,
                "generation_publication_receipt": trusted_publication_receipt,
                "generation_artifact_presence": {
                    name: name in hashes for name in ("final.mmd", "final.svg", "final.png")
                },
                "files": hashes,
                "failures": [item.model_dump(mode="json") for item in result.failures],
                "prompt_budget_notices": canonical_prompt_budget_notice_json(
                    result.prompt_budget_notices
                ),
            }
            _write(_AnchoredArtifactPath(temporary_fd, "manifest.json"), _json_bytes(manifest))
            try:
                current_diagrams_stat = os.stat(
                    "diagrams",
                    dir_fd=output_fd,
                    follow_symlinks=False,
                )
            except (FileNotFoundError, OSError) as exc:
                raise ValueError(
                    "sidecar directory identity changed before bundle publication"
                ) from exc
            if (
                not stat.S_ISDIR(current_diagrams_stat.st_mode)
                or stat.S_ISLNK(current_diagrams_stat.st_mode)
                or (current_diagrams_stat.st_dev, current_diagrams_stat.st_ino) != diagrams_identity
            ):
                raise ValueError("sidecar directory identity changed before bundle publication")
            try:
                current_temporary_stat = os.stat(
                    temporary_name,
                    dir_fd=diagrams_fd,
                    follow_symlinks=False,
                )
            except (FileNotFoundError, OSError) as exc:
                raise ValueError(
                    "sidecar directory identity changed before bundle publication"
                ) from exc
            if (
                not stat.S_ISDIR(current_temporary_stat.st_mode)
                or stat.S_ISLNK(current_temporary_stat.st_mode)
                or (current_temporary_stat.st_dev, current_temporary_stat.st_ino)
                != temporary_identity
            ):
                raise ValueError("sidecar directory identity changed before bundle publication")
            try:
                os.stat(name, dir_fd=diagrams_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(
                    f"sidecar bundle already exists: {self.output_root / 'diagrams' / name}"
                )
            _rename_noreplace(
                diagrams_fd,
                temporary_name,
                diagrams_fd,
                name,
            )
            temporary_name = None
        except Exception:
            if temporary_name is not None:
                try:
                    current_temporary_stat = os.stat(
                        temporary_name,
                        dir_fd=diagrams_fd,
                        follow_symlinks=False,
                    )
                    cleanup_is_safe = (
                        stat.S_ISDIR(current_temporary_stat.st_mode)
                        and not stat.S_ISLNK(current_temporary_stat.st_mode)
                        and (current_temporary_stat.st_dev, current_temporary_stat.st_ino)
                        == temporary_identity
                    )
                except (FileNotFoundError, OSError):
                    cleanup_is_safe = False
                if cleanup_is_safe:
                    shutil.rmtree(
                        temporary_name,
                        dir_fd=diagrams_fd,
                        ignore_errors=True,
                    )
            raise
        finally:
            os.close(temporary_fd)
            os.close(diagrams_fd)
            os.close(output_fd)
        live_result.sidecar_dir = relative.as_posix()
        return relative.as_posix()
