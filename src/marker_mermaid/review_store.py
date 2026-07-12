"""Traversal-safe persistence for the local review workspace.

The review server is intentionally kept separate from this module.  ``ReviewStore``
only operates on sidecar bundles below ``<output>/diagrams`` and exposes an
optimistic-concurrency API that an HTTP, CLI, or desktop frontend can share.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from marker_mermaid.models import (
    MAX_OBSERVATION_EVIDENCE,
    DiagramSceneIR,
    ReviewHistoryEntry,
    VisualEvidence,
)
from marker_mermaid.review_layout import ReviewLayoutHints

REVIEW_SCHEMA_VERSION = "mmx-review-0.4.1"
PROVENANCE_REVIEW_SCHEMA_VERSION = "mmx-review-0.4"
LEGACY_REVIEW_SCHEMA_VERSION = "mmx-review-0.3"
MAX_MERMAID_BYTES = 1_000_000
MAX_JSON_BYTES = 4_000_000
MAX_RENDER_BYTES = 16_000_000
MAX_HISTORY_ENTRIES = 10_000
MAX_REASON_LENGTH = 4_096
MAX_LIST_BUNDLES = 1_000
MAX_LIST_CANDIDATES = 5_000
_BUNDLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


def _validated_digest(value: str | None) -> str | None:
    if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("artifact digest must be a lowercase SHA-256 digest")
    return value


class ReviewValidationResult(BaseModel):
    """Validated render artifacts returned by the interactive server."""

    valid: bool
    svg: str | None = None
    png: bytes | None = None
    diagram_type: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    @field_validator("svg")
    @classmethod
    def svg_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > MAX_RENDER_BYTES:
            raise ValueError("rendered SVG exceeds the artifact size limit")
        return value

    @field_validator("png")
    @classmethod
    def png_is_bounded(cls, value: bytes | None) -> bytes | None:
        if value is not None and len(value) > MAX_RENDER_BYTES:
            raise ValueError("rendered PNG exceeds the artifact size limit")
        return value


ValidationCallback = Callable[[str], bool | None | ReviewValidationResult]
ReviewDecision = Literal["pending", "approved", "rejected"]


class ReviewStoreError(RuntimeError):
    """Base error raised by the review persistence layer."""


class UnsafeReviewPathError(ReviewStoreError):
    """A requested path could escape the configured output root."""


class ReviewConflictError(ReviewStoreError):
    """The caller's optimistic version or digest is stale."""


class ReviewValidationError(ReviewStoreError):
    """Review input or an on-disk JSON document failed validation."""


class ReviewState(BaseModel):
    schema_version: Literal[
        REVIEW_SCHEMA_VERSION,
        PROVENANCE_REVIEW_SCHEMA_VERSION,
        LEGACY_REVIEW_SCHEMA_VERSION,
    ] = REVIEW_SCHEMA_VERSION
    version: int = Field(ge=0)
    timeline: list[str]
    cursor: int = Field(ge=0)
    current_revision: str
    code_digest: str
    ir_digest: str | None = None
    svg_digest: str | None = None
    png_digest: str | None = None
    provenance_digest: str | None = None
    layout_digest: str | None = None
    legacy_provenance_digest: str | None = None
    decision: ReviewDecision = "pending"
    decision_reason: str | None = None
    selected_candidate_id: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("timeline")
    @classmethod
    def timeline_is_bounded(cls, value: list[str]) -> list[str]:
        if not value or len(value) > MAX_HISTORY_ENTRIES:
            raise ValueError("review timeline must be non-empty and bounded")
        if any(not re.fullmatch(r"r[0-9]{6,}", item) for item in value):
            raise ValueError("review timeline contains an invalid revision id")
        if len(value) != len(set(value)):
            raise ValueError("review timeline cannot contain duplicate revisions")
        return value

    @field_validator(
        "code_digest",
        "ir_digest",
        "svg_digest",
        "png_digest",
        "provenance_digest",
        "layout_digest",
        "legacy_provenance_digest",
    )
    @classmethod
    def digest_is_sha256(cls, value: str | None) -> str | None:
        return _validated_digest(value)

    @field_validator("decision_reason")
    @classmethod
    def reason_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and len(value) > MAX_REASON_LENGTH:
            raise ValueError("decision reason is too long")
        return value

    @model_validator(mode="after")
    def cursor_matches_revision(self) -> ReviewState:
        if self.cursor >= len(self.timeline):
            raise ValueError("review cursor is outside the timeline")
        if self.timeline[self.cursor] != self.current_revision:
            raise ValueError("current_revision does not match timeline cursor")
        return self


class ReviewBundleSummary(BaseModel):
    bundle_id: str
    source_id: str
    status: str
    grade: str
    version: int
    decision: ReviewDecision
    code_digest: str


class ReviewBundle(BaseModel):
    bundle_id: str
    manifest: dict[str, Any]
    mermaid_code: str
    scene_ir: dict[str, Any] | None = None
    svg: str | None = None
    png: bytes | None = None
    provenance: list[VisualEvidence] | None = None
    layout_hints: ReviewLayoutHints | None = None
    history: list[ReviewHistoryEntry]
    state: ReviewState


class _RevisionSnapshot(BaseModel):
    schema_version: Literal[
        REVIEW_SCHEMA_VERSION,
        PROVENANCE_REVIEW_SCHEMA_VERSION,
        LEGACY_REVIEW_SCHEMA_VERSION,
    ] = REVIEW_SCHEMA_VERSION
    revision: str
    code_digest: str
    ir_digest: str | None = None
    svg_digest: str | None = None
    png_digest: str | None = None
    provenance_digest: str | None = None
    layout_digest: str | None = None
    decision: ReviewDecision
    decision_reason: str | None = None
    selected_candidate_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator(
        "code_digest",
        "ir_digest",
        "svg_digest",
        "png_digest",
        "provenance_digest",
        "layout_digest",
    )
    @classmethod
    def digest_is_sha256(cls, value: str | None) -> str | None:
        return _validated_digest(value)


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _bytes_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _ir_bytes(value: dict[str, Any]) -> bytes:
    return _json_bytes(value)


def _provenance_bytes(value: list[VisualEvidence]) -> bytes:
    return _json_bytes([item.model_dump(mode="json") for item in value])


def _layout_bytes(value: ReviewLayoutHints) -> bytes:
    return _json_bytes(value.model_dump(mode="json"))


def _json_bytes(value: Any) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise ReviewValidationError("JSON payload exceeds the review size limit")
    return encoded


class ReviewStore:
    """Read and mutate review sidecars with bounded, append-only audit history."""

    def __init__(self, output_root: str | Path, *, validator: ValidationCallback | None = None):
        self.output_root = Path(output_root).resolve()
        self.diagrams_root = self.output_root / "diagrams"
        self.validator = validator

    def list_bundles(self, *, limit: int = MAX_LIST_BUNDLES) -> list[ReviewBundleSummary]:
        """Return bounded summaries without loading render, IR, or history artifacts."""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_LIST_BUNDLES
        ):
            raise ReviewValidationError(
                f"review list limit must be between 1 and {MAX_LIST_BUNDLES}"
            )
        if not self.diagrams_root.exists():
            return []
        diagrams_root = self._safe_diagrams_root()
        summaries: list[ReviewBundleSummary] = []
        with os.scandir(diagrams_root) as entries:
            bundle_ids = heapq.nsmallest(
                MAX_LIST_CANDIDATES,
                (
                    entry.name
                    for entry in entries
                    if entry.is_dir(follow_symlinks=False) and _BUNDLE_ID.fullmatch(entry.name)
                ),
            )
        for bundle_id in bundle_ids:
            try:
                summary = self._load_summary(bundle_id)
            except ReviewStoreError:
                continue
            summaries.append(summary)
            if len(summaries) >= limit:
                break
        return summaries

    def _load_summary(self, bundle_id: str) -> ReviewBundleSummary:
        bundle = self._bundle_path(bundle_id)
        manifest = self._read_json(bundle, "manifest.json", expected=dict)
        self._validate_manifest(manifest)
        final_path = self._artifact_path(bundle, "final.mmd", must_exist=False)
        bootstrap_candidate_id: str | None = None
        if final_path.exists():
            code = self._read_code(bundle, "final.mmd")
        else:
            code, _, bootstrap_candidate_id = self._bootstrap_alternative(bundle)

        state_path = self._artifact_path(bundle, "review-state.json", must_exist=False)
        if state_path.exists():
            payload = self._read_json(bundle, "review-state.json", expected=dict)
            try:
                state = ReviewState.model_validate(payload)
            except ValidationError as exc:
                raise ReviewValidationError("review-state.json is invalid") from exc
            if state.code_digest != _digest(code):
                raise ReviewConflictError("final.mmd changed outside the review store")
        else:
            state = ReviewState(
                version=0,
                timeline=["r000000"],
                cursor=0,
                current_revision="r000000",
                code_digest=_digest(code),
                selected_candidate_id=(
                    manifest.get("selected_candidate_id") or bootstrap_candidate_id
                ),
            )
        return ReviewBundleSummary(
            bundle_id=bundle_id,
            source_id=str(manifest.get("source_id", bundle_id)),
            status=str(manifest.get("status", "unknown")),
            grade=str(manifest.get("grade", "U")),
            version=state.version,
            decision=state.decision,
            code_digest=state.code_digest,
        )

    def load_bundle(self, bundle_id: str) -> ReviewBundle:
        bundle = self._bundle_path(bundle_id)
        manifest = self._read_json(bundle, "manifest.json", expected=dict)
        self._validate_manifest(manifest)
        final_path = self._artifact_path(bundle, "final.mmd", must_exist=False)
        bootstrap_candidate_id: str | None = None
        if final_path.exists():
            code = self._read_code(bundle, "final.mmd")
            scene_ir = self._read_optional_json(bundle, "scene-ir.json", expected=dict)
        else:
            code, scene_ir, bootstrap_candidate_id = self._bootstrap_alternative(bundle)
        provenance = self._load_provenance(bundle, manifest)
        layout_hints = self._load_layout_hints(bundle, manifest)
        svg_payload = self._read_optional_bytes(bundle, "final.svg", MAX_RENDER_BYTES)
        png = self._read_optional_bytes(bundle, "final.png", MAX_RENDER_BYTES)
        try:
            svg = svg_payload.decode("utf-8") if svg_payload is not None else None
        except UnicodeDecodeError as exc:
            raise ReviewValidationError("final.svg is not UTF-8") from exc
        history_payload = self._read_json(bundle, "review-history.json", expected=list)
        if len(history_payload) > MAX_HISTORY_ENTRIES:
            raise ReviewValidationError("review history exceeds the entry limit")
        try:
            history = [ReviewHistoryEntry.model_validate(item) for item in history_payload]
        except ValidationError as exc:
            raise ReviewValidationError("review-history.json has an invalid entry") from exc

        state_path = bundle / "review-state.json"
        if state_path.exists():
            payload = self._read_json(bundle, "review-state.json", expected=dict)
            try:
                state = ReviewState.model_validate(payload)
            except ValidationError as exc:
                raise ReviewValidationError("review-state.json is invalid") from exc
            if state.code_digest != _digest(code):
                raise ReviewConflictError("final.mmd changed outside the review store")
            actual_ir_digest = _bytes_digest(_ir_bytes(scene_ir)) if scene_ir is not None else None
            if state.ir_digest != actual_ir_digest:
                raise ReviewConflictError("scene-ir.json changed outside the review store")
            actual_svg_digest = _digest(svg) if svg is not None else None
            actual_png_digest = _bytes_digest(png) if png is not None else None
            if state.svg_digest != actual_svg_digest or state.png_digest != actual_png_digest:
                raise ReviewConflictError("render artifact changed outside the review store")
            actual_provenance_digest = (
                _bytes_digest(_provenance_bytes(provenance)) if provenance is not None else None
            )
            if (
                state.schema_version
                in {REVIEW_SCHEMA_VERSION, PROVENANCE_REVIEW_SCHEMA_VERSION}
                and state.provenance_digest != actual_provenance_digest
            ):
                raise ReviewConflictError("provenance.json changed outside the review store")
            actual_layout_digest = (
                _bytes_digest(_layout_bytes(layout_hints))
                if layout_hints is not None
                else None
            )
            if state.layout_digest != actual_layout_digest:
                raise ReviewConflictError("layout-hints.json changed outside the review store")
        else:
            state = self._initial_state(
                code,
                scene_ir,
                svg,
                png,
                provenance,
                layout_hints,
                selected_candidate_id=(
                    manifest.get("selected_candidate_id") or bootstrap_candidate_id
                ),
            )
        return ReviewBundle(
            bundle_id=bundle_id,
            manifest=manifest,
            mermaid_code=code,
            scene_ir=scene_ir,
            svg=svg,
            png=png,
            provenance=provenance,
            layout_hints=layout_hints,
            history=history,
            state=state,
        )

    def _bootstrap_alternative(self, bundle: Path) -> tuple[str, dict[str, Any] | None, str]:
        alternatives = self._artifact_path(bundle, "alternatives", must_exist=False)
        if not alternatives.is_dir() or alternatives.is_symlink():
            raise ReviewValidationError("bundle has no final Mermaid or reviewable alternative")
        for path in sorted(alternatives.glob("*.json")):
            if path.is_symlink() or path.parent != alternatives:
                continue
            payload = self._read_json(
                bundle,
                f"alternatives/{path.name}",
                expected=dict,
            )
            code = payload.get("mermaid_code")
            candidate_id = payload.get("candidate_id")
            if not isinstance(code, str) or not isinstance(candidate_id, str):
                continue
            try:
                self._validate_code(code)
            except ReviewValidationError:
                continue
            scene_ir = payload.get("scene_ir")
            if scene_ir is not None:
                try:
                    self._validate_scene_ir(scene_ir)
                except ReviewValidationError:
                    scene_ir = None
            return code, scene_ir, candidate_id
        raise ReviewValidationError("bundle has no final Mermaid or reviewable alternative")

    def apply_mermaid_edit(
        self,
        bundle_id: str,
        code: str,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str | None = None,
        validator: ValidationCallback | None = None,
    ) -> ReviewBundle:
        current = self.load_bundle(bundle_id)
        return self.apply_edit(
            bundle_id,
            code,
            scene_ir=current.scene_ir,
            expected_version=expected_version,
            expected_digest=expected_digest,
            reason=reason,
            validator=validator,
        )

    def load_expected_bundle(
        self,
        bundle_id: str,
        *,
        expected_version: int,
        expected_digest: str,
    ) -> ReviewBundle:
        """Load a bundle only when the caller's optimistic revision is current.

        Mutating methods repeat this check under the bundle lock.  This early check
        lets callers reject stale structured commands before interpreting them against
        a newer IR revision.
        """

        bundle = self.load_bundle(bundle_id)
        self._check_expected(bundle, expected_version, expected_digest)
        return bundle

    def apply_edit(
        self,
        bundle_id: str,
        code: str,
        *,
        scene_ir: dict[str, Any] | None,
        expected_version: int,
        expected_digest: str,
        reason: str | None = None,
        validator: ValidationCallback | None = None,
        operation: str = "edit_mermaid",
        selected_candidate_id: str | None = None,
        audit_entry: ReviewHistoryEntry | None = None,
        provenance: list[dict[str, Any] | VisualEvidence] | None = None,
        replace_provenance: bool = False,
        reset_layout: bool = False,
    ) -> ReviewBundle:
        """Atomically persist code, IR, render/provenance artifacts, state, and history.

        Existing callers preserve provenance.  A trusted structured operation must set
        ``replace_provenance=True`` to replace or remove it; HTTP editor payloads do not
        expose that switch.
        """

        self._validate_code(code)
        self._validate_scene_ir(scene_ir)
        self._validate_reason(reason)
        if not isinstance(replace_provenance, bool):
            raise ReviewValidationError("replace_provenance must be a boolean")
        if not isinstance(reset_layout, bool):
            raise ReviewValidationError("reset_layout must be a boolean")
        if provenance is not None and not replace_provenance:
            raise ReviewValidationError(
                "provenance input requires the explicit replace_provenance boundary"
            )
        replacement_provenance = (
            self._validate_provenance(provenance) if replace_provenance else None
        )
        current = self.load_bundle(bundle_id)
        self._check_expected(current, expected_version, expected_digest)
        callback = validator if validator is not None else self.validator
        validation_result: ReviewValidationResult | None = None
        if callback is not None:
            try:
                valid = callback(code)
            except Exception as exc:
                raise ReviewValidationError(f"Mermaid validation failed: {exc}") from exc
            if isinstance(valid, ReviewValidationResult):
                validation_result = valid
                if not valid.valid:
                    detail = valid.error or "; ".join(valid.warnings) or "validation rejected"
                    raise ReviewValidationError(f"Mermaid validation rejected the edit: {detail}")
            elif valid is False:
                raise ReviewValidationError("Mermaid validation rejected the edit")
        with self._locked_bundle(bundle_id):
            bundle = self.load_bundle(bundle_id)
            self._check_expected(bundle, expected_version, expected_digest)
            selected_provenance = (
                replacement_provenance if replace_provenance else bundle.provenance
            )
            selected_layout = (
                None
                if reset_layout
                else self._reconcile_layout_hints(bundle.layout_hints, scene_ir)
            )
            available_evidence_ids = {item.id for item in selected_provenance or []}
            referenced_evidence_ids = {
                evidence_id
                for collection in (
                    scene_ir.get("elements", []) if scene_ir is not None else [],
                    scene_ir.get("relations", []) if scene_ir is not None else [],
                )
                for item in collection
                for evidence_id in item.get("evidence_ids", [])
            }
            missing_evidence_ids = referenced_evidence_ids - available_evidence_ids
            if missing_evidence_ids:
                raise ReviewValidationError(
                    "Scene IR references evidence absent from provenance: "
                    f"{sorted(missing_evidence_ids)[:10]}"
                )
            before = self._state_value(
                bundle.state,
                bundle.mermaid_code,
                bundle.scene_ir,
                bundle.provenance,
                bundle.layout_hints,
            )
            return self._commit_new_revision(
                bundle,
                code=code,
                scene_ir=scene_ir,
                provenance=selected_provenance,
                layout_hints=selected_layout,
                svg=validation_result.svg if validation_result else bundle.svg,
                png=validation_result.png if validation_result else bundle.png,
                decision="pending",
                decision_reason=None,
                selected_candidate_id=(
                    selected_candidate_id
                    if selected_candidate_id is not None
                    else bundle.state.selected_candidate_id
                ),
                operation=operation,
                reason=reason,
                before=before,
                audit_entry=audit_entry,
            )

    edit_mermaid = apply_mermaid_edit

    def apply_layout_hint(
        self,
        bundle_id: str,
        *,
        node_id: str,
        x: float,
        y: float,
        expected_version: int,
        expected_digest: str,
        reason: str | None = None,
    ) -> ReviewBundle:
        """Commit one advisory normalized node position without changing source geometry."""

        self._validate_reason(reason)
        try:
            requested = ReviewLayoutHints().with_node(node_id, x, y).nodes[0]
        except (ValidationError, TypeError) as exc:
            raise ReviewValidationError("invalid normalized layout hint") from exc
        current = self.load_bundle(bundle_id)
        self._check_expected(current, expected_version, expected_digest)
        self._require_scene_node(current.scene_ir, requested.node_id)
        with self._locked_bundle(bundle_id):
            bundle = self.load_bundle(bundle_id)
            self._check_expected(bundle, expected_version, expected_digest)
            self._require_scene_node(bundle.scene_ir, requested.node_id)
            current_layout = bundle.layout_hints or ReviewLayoutHints()
            try:
                updated_layout = current_layout.with_node(
                    requested.node_id,
                    requested.x,
                    requested.y,
                )
            except ValidationError as exc:
                raise ReviewValidationError("layout hint exceeds the node budget") from exc
            if updated_layout == bundle.layout_hints:
                raise ReviewValidationError("layout hint did not change")
            previous_hint = next(
                (
                    item
                    for item in current_layout.nodes
                    if item.node_id == requested.node_id
                ),
                None,
            )
            audit_entry = ReviewHistoryEntry(
                operation="move_node",
                target=requested.node_id,
                before={
                    "layout_position": (
                        [previous_hint.x, previous_hint.y]
                        if previous_hint is not None
                        else None
                    )
                },
                after={"layout_position": [requested.x, requested.y]},
                source="user",
                reason=reason,
            )
            before = self._state_value(
                bundle.state,
                bundle.mermaid_code,
                bundle.scene_ir,
                bundle.provenance,
                bundle.layout_hints,
            )
            return self._commit_new_revision(
                bundle,
                code=bundle.mermaid_code,
                scene_ir=bundle.scene_ir,
                provenance=bundle.provenance,
                layout_hints=updated_layout,
                svg=bundle.svg,
                png=bundle.png,
                decision="pending",
                decision_reason=None,
                selected_candidate_id=bundle.state.selected_candidate_id,
                operation="move_node",
                reason=reason,
                before=before,
                audit_entry=audit_entry,
            )

    def approve(
        self,
        bundle_id: str,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str | None = None,
    ) -> ReviewBundle:
        return self._set_decision(
            bundle_id,
            "approved",
            expected_version=expected_version,
            expected_digest=expected_digest,
            reason=reason,
        )

    def reject(
        self,
        bundle_id: str,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str,
    ) -> ReviewBundle:
        if not reason.strip():
            raise ReviewValidationError("a rejection reason is required")
        return self._set_decision(
            bundle_id,
            "rejected",
            expected_version=expected_version,
            expected_digest=expected_digest,
            reason=reason,
        )

    def undo(
        self,
        bundle_id: str,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str | None = None,
    ) -> ReviewBundle:
        return self._move_cursor(
            bundle_id,
            delta=-1,
            expected_version=expected_version,
            expected_digest=expected_digest,
            reason=reason,
        )

    def redo(
        self,
        bundle_id: str,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str | None = None,
    ) -> ReviewBundle:
        return self._move_cursor(
            bundle_id,
            delta=1,
            expected_version=expected_version,
            expected_digest=expected_digest,
            reason=reason,
        )

    def checkout_revision(
        self,
        bundle_id: str,
        target_revision: str,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str | None = None,
    ) -> ReviewBundle:
        return self._move_cursor(
            bundle_id,
            target_revision=target_revision,
            expected_version=expected_version,
            expected_digest=expected_digest,
            reason=reason,
        )

    def _set_decision(
        self,
        bundle_id: str,
        decision: ReviewDecision,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str | None,
    ) -> ReviewBundle:
        self._validate_reason(reason)
        with self._locked_bundle(bundle_id):
            bundle = self.load_bundle(bundle_id)
            self._check_expected(bundle, expected_version, expected_digest)
            validation_result: ReviewValidationResult | None = None
            if decision == "approved":
                if self.validator is None:
                    raise ReviewValidationError("approval requires a configured Mermaid validator")
                try:
                    valid = self.validator(bundle.mermaid_code)
                except Exception as exc:
                    raise ReviewValidationError(f"approval validation failed: {exc}") from exc
                if isinstance(valid, ReviewValidationResult):
                    validation_result = valid
                    if not valid.valid:
                        detail = valid.error or "; ".join(valid.warnings) or "validation rejected"
                        raise ReviewValidationError(
                            f"Mermaid validation rejected approval: {detail}"
                        )
                elif valid is not True:
                    raise ReviewValidationError("Mermaid validation rejected approval")
            before = self._state_value(
                bundle.state,
                bundle.mermaid_code,
                bundle.scene_ir,
                bundle.provenance,
                bundle.layout_hints,
            )
            return self._commit_new_revision(
                bundle,
                code=bundle.mermaid_code,
                scene_ir=bundle.scene_ir,
                provenance=bundle.provenance,
                layout_hints=bundle.layout_hints,
                svg=validation_result.svg if validation_result else bundle.svg,
                png=validation_result.png if validation_result else bundle.png,
                decision=decision,
                decision_reason=reason,
                selected_candidate_id=bundle.state.selected_candidate_id,
                operation="approve" if decision == "approved" else "reject",
                reason=reason,
                before=before,
            )

    def _move_cursor(
        self,
        bundle_id: str,
        *,
        delta: int | None = None,
        target_revision: str | None = None,
        expected_version: int,
        expected_digest: str,
        reason: str | None,
    ) -> ReviewBundle:
        self._validate_reason(reason)
        with self._locked_bundle(bundle_id):
            bundle = self.load_bundle(bundle_id)
            self._check_expected(bundle, expected_version, expected_digest)
            if delta is None:
                if not isinstance(target_revision, str) or not re.fullmatch(
                    r"r[0-9]{6,}", target_revision
                ):
                    raise ReviewValidationError("target revision has an invalid ID")
                try:
                    next_cursor = bundle.state.timeline.index(target_revision)
                except ValueError as exc:
                    raise ReviewValidationError(
                        "target revision is not in the active timeline"
                    ) from exc
                if next_cursor == bundle.state.cursor:
                    raise ReviewConflictError("target revision is already current")
                operation = "checkout_revision"
            else:
                if delta not in {-1, 1}:
                    raise ReviewValidationError("history cursor delta is invalid")
                next_cursor = bundle.state.cursor + delta
                operation = "undo" if delta < 0 else "redo"
                if not 0 <= next_cursor < len(bundle.state.timeline):
                    raise ReviewConflictError(f"nothing to {operation}")
            revision = bundle.state.timeline[next_cursor]
            bundle_path = self._bundle_path(bundle_id)
            code = self._read_code(bundle_path, f"versions/{revision}.mmd")
            snapshot_payload = self._read_json(
                bundle_path, f"versions/{revision}.json", expected=dict
            )
            try:
                snapshot = _RevisionSnapshot.model_validate(snapshot_payload)
            except ValidationError as exc:
                raise ReviewValidationError(f"revision {revision} is invalid") from exc
            if snapshot.revision != revision or snapshot.code_digest != _digest(code):
                raise ReviewValidationError(f"revision {revision} failed its digest check")
            scene_ir = self._read_revision_ir(bundle_path, revision, snapshot.ir_digest)
            svg = self._read_revision_render(bundle_path, revision, "svg", snapshot.svg_digest)
            png = self._read_revision_render(bundle_path, revision, "png", snapshot.png_digest)
            layout_hints = self._read_revision_layout(
                bundle_path,
                snapshot.layout_digest,
            )
            current_provenance_digest = (
                _bytes_digest(_provenance_bytes(bundle.provenance))
                if bundle.provenance is not None
                else None
            )
            legacy_provenance_digest = bundle.state.legacy_provenance_digest
            if bundle.state.schema_version == LEGACY_REVIEW_SCHEMA_VERSION:
                legacy_provenance_digest = current_provenance_digest
            if snapshot.schema_version == LEGACY_REVIEW_SCHEMA_VERSION:
                target_provenance_digest = legacy_provenance_digest
                if bundle.state.schema_version == LEGACY_REVIEW_SCHEMA_VERSION:
                    provenance = bundle.provenance
                else:
                    provenance = self._read_revision_provenance(
                        bundle_path,
                        target_provenance_digest,
                    )
            else:
                target_provenance_digest = snapshot.provenance_digest
                provenance = self._read_revision_provenance(
                    bundle_path,
                    target_provenance_digest,
                )
            before = self._state_value(
                bundle.state,
                bundle.mermaid_code,
                bundle.scene_ir,
                bundle.provenance,
                bundle.layout_hints,
            )
            state = bundle.state.model_copy(
                update={
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "version": bundle.state.version + 1,
                    "cursor": next_cursor,
                    "current_revision": revision,
                    "code_digest": snapshot.code_digest,
                    "ir_digest": snapshot.ir_digest,
                    "svg_digest": snapshot.svg_digest,
                    "png_digest": snapshot.png_digest,
                    "provenance_digest": target_provenance_digest,
                    "layout_digest": snapshot.layout_digest,
                    "legacy_provenance_digest": legacy_provenance_digest,
                    "decision": snapshot.decision,
                    "decision_reason": snapshot.decision_reason,
                    "selected_candidate_id": snapshot.selected_candidate_id,
                    "updated_at": datetime.now(UTC),
                }
            )
            after = self._state_value(state, code, scene_ir, provenance, layout_hints)
            history = self._append_history(
                bundle.history,
                operation=operation,
                target=(target_revision if operation == "checkout_revision" else bundle_id),
                before=before,
                after=after,
                reason=reason,
            )
            files: dict[str, bytes | None] = {
                "final.mmd": code.encode("utf-8"),
                "scene-ir.json": _ir_bytes(scene_ir) if scene_ir is not None else None,
                "final.svg": svg,
                "final.png": png,
                "provenance.json": (
                    _provenance_bytes(provenance) if provenance is not None else None
                ),
                "layout-hints.json": (
                    _layout_bytes(layout_hints) if layout_hints is not None else None
                ),
                "review-history.json": _json_bytes(
                    [entry.model_dump(mode="json") for entry in history]
                ),
                "review-state.json": _json_bytes(state.model_dump(mode="json")),
            }
            if provenance is not None:
                if target_provenance_digest is None:
                    raise ReviewValidationError("revision provenance is missing its digest")
                files[f"versions/provenance/{target_provenance_digest}.json"] = _provenance_bytes(
                    provenance
                )
            if layout_hints is not None:
                if snapshot.layout_digest is None:
                    raise ReviewValidationError("revision layout is missing its digest")
                files[f"versions/layout/{snapshot.layout_digest}.json"] = _layout_bytes(
                    layout_hints
                )
            quality_status = (
                "automated_baseline" if revision == "r000000" else "unscored_user_revision"
            )
            files["manifest.json"] = self._updated_manifest_bytes(
                bundle.manifest,
                files,
                quality_status=quality_status,
            )
            self._atomic_replace_many(bundle_path, files)
        return self.load_bundle(bundle_id)

    def _commit_new_revision(
        self,
        bundle: ReviewBundle,
        *,
        code: str,
        scene_ir: dict[str, Any] | None,
        provenance: list[VisualEvidence] | None,
        layout_hints: ReviewLayoutHints | None,
        svg: str | None,
        png: bytes | None,
        decision: ReviewDecision,
        decision_reason: str | None,
        selected_candidate_id: str | None,
        operation: str,
        reason: str | None,
        before: dict[str, Any],
        audit_entry: ReviewHistoryEntry | None = None,
    ) -> ReviewBundle:
        bundle_path = self._bundle_path(bundle.bundle_id)
        next_version = bundle.state.version + 1
        revision = f"r{next_version:06d}"
        timeline = bundle.state.timeline[: bundle.state.cursor + 1] + [revision]
        current_provenance_digest = (
            _bytes_digest(_provenance_bytes(bundle.provenance))
            if bundle.provenance is not None
            else None
        )
        provenance_digest = (
            _bytes_digest(_provenance_bytes(provenance)) if provenance is not None else None
        )
        layout_digest = (
            _bytes_digest(_layout_bytes(layout_hints)) if layout_hints is not None else None
        )
        legacy_provenance_digest = bundle.state.legacy_provenance_digest
        if bundle.state.schema_version == LEGACY_REVIEW_SCHEMA_VERSION:
            legacy_provenance_digest = current_provenance_digest
        state = ReviewState(
            version=next_version,
            timeline=timeline,
            cursor=len(timeline) - 1,
            current_revision=revision,
            code_digest=_digest(code),
            ir_digest=_bytes_digest(_ir_bytes(scene_ir)) if scene_ir is not None else None,
            svg_digest=_digest(svg) if svg is not None else None,
            png_digest=_bytes_digest(png) if png is not None else None,
            provenance_digest=provenance_digest,
            layout_digest=layout_digest,
            legacy_provenance_digest=legacy_provenance_digest,
            decision=decision,
            decision_reason=decision_reason,
            selected_candidate_id=selected_candidate_id,
        )
        after = self._state_value(state, code, scene_ir, provenance, layout_hints)
        if audit_entry is not None:
            if audit_entry.source != "user":
                raise ReviewValidationError("review audit entries must have user source")
            if len(bundle.history) >= MAX_HISTORY_ENTRIES:
                raise ReviewValidationError("review history has reached the entry limit")
            history = [*bundle.history, audit_entry]
        else:
            history = self._append_history(
                bundle.history,
                operation=operation,
                target=bundle.bundle_id,
                before=before,
                after=after,
                reason=reason,
            )
        snapshot = _RevisionSnapshot(
            revision=revision,
            code_digest=state.code_digest,
            ir_digest=state.ir_digest,
            svg_digest=_digest(svg) if svg is not None else None,
            png_digest=_bytes_digest(png) if png is not None else None,
            provenance_digest=provenance_digest,
            layout_digest=layout_digest,
            decision=decision,
            decision_reason=decision_reason,
            selected_candidate_id=selected_candidate_id,
        )
        files: dict[str, bytes | None] = {}
        initial_mmd = bundle_path / "versions" / "r000000.mmd"
        if not initial_mmd.exists():
            if bundle.state.version != 0:
                raise ReviewValidationError("the initial review snapshot is missing")
            initial_snapshot = _RevisionSnapshot(
                revision="r000000",
                code_digest=_digest(bundle.mermaid_code),
                ir_digest=(
                    _bytes_digest(_ir_bytes(bundle.scene_ir))
                    if bundle.scene_ir is not None
                    else None
                ),
                svg_digest=_digest(bundle.svg) if bundle.svg is not None else None,
                png_digest=_bytes_digest(bundle.png) if bundle.png is not None else None,
                provenance_digest=current_provenance_digest,
                layout_digest=(
                    _bytes_digest(_layout_bytes(bundle.layout_hints))
                    if bundle.layout_hints is not None
                    else None
                ),
                decision=bundle.state.decision,
                decision_reason=bundle.state.decision_reason,
                selected_candidate_id=bundle.state.selected_candidate_id,
            )
            files["versions/r000000.mmd"] = bundle.mermaid_code.encode("utf-8")
            files["versions/r000000.json"] = _json_bytes(initial_snapshot.model_dump(mode="json"))
            if bundle.scene_ir is not None:
                files["versions/r000000.scene-ir.json"] = _ir_bytes(bundle.scene_ir)
            if bundle.svg is not None:
                files["versions/r000000.svg"] = bundle.svg.encode("utf-8")
            if bundle.png is not None:
                files["versions/r000000.png"] = bundle.png
            if bundle.provenance is not None:
                assert current_provenance_digest is not None
                files[f"versions/provenance/{current_provenance_digest}.json"] = _provenance_bytes(
                    bundle.provenance
                )
            if bundle.layout_hints is not None:
                current_layout_digest = _bytes_digest(_layout_bytes(bundle.layout_hints))
                files[f"versions/layout/{current_layout_digest}.json"] = _layout_bytes(
                    bundle.layout_hints
                )
        files.update(
            {
                f"versions/{revision}.mmd": code.encode("utf-8"),
                f"versions/{revision}.json": _json_bytes(snapshot.model_dump(mode="json")),
                "final.mmd": code.encode("utf-8"),
                "review-history.json": _json_bytes(
                    [entry.model_dump(mode="json") for entry in history]
                ),
                "review-state.json": _json_bytes(state.model_dump(mode="json")),
                "scene-ir.json": _ir_bytes(scene_ir) if scene_ir is not None else None,
                "final.svg": svg.encode("utf-8") if svg is not None else None,
                "final.png": png,
                "provenance.json": (
                    _provenance_bytes(provenance) if provenance is not None else None
                ),
                "layout-hints.json": (
                    _layout_bytes(layout_hints) if layout_hints is not None else None
                ),
            }
        )
        if (
            bundle.state.schema_version == LEGACY_REVIEW_SCHEMA_VERSION
            and legacy_provenance_digest is not None
            and bundle.provenance is not None
        ):
            files[f"versions/provenance/{legacy_provenance_digest}.json"] = _provenance_bytes(
                bundle.provenance
            )
        if provenance is not None:
            assert provenance_digest is not None
            files[f"versions/provenance/{provenance_digest}.json"] = _provenance_bytes(provenance)
        if layout_hints is not None:
            assert layout_digest is not None
            files[f"versions/layout/{layout_digest}.json"] = _layout_bytes(layout_hints)
        if scene_ir is not None:
            files[f"versions/{revision}.scene-ir.json"] = _ir_bytes(scene_ir)
        if svg is not None:
            files[f"versions/{revision}.svg"] = svg.encode("utf-8")
        if png is not None:
            files[f"versions/{revision}.png"] = png
        content_changed = (
            code != bundle.mermaid_code
            or scene_ir != bundle.scene_ir
            or provenance != bundle.provenance
            or layout_hints != bundle.layout_hints
        )
        files["manifest.json"] = self._updated_manifest_bytes(
            bundle.manifest,
            files,
            quality_status=("unscored_user_revision" if content_changed else None),
        )
        self._atomic_replace_many(bundle_path, files)
        return self.load_bundle(bundle.bundle_id)

    @staticmethod
    def _state_value(
        state: ReviewState,
        code: str,
        scene_ir: dict[str, Any] | None = None,
        provenance: list[VisualEvidence] | None = None,
        layout_hints: ReviewLayoutHints | None = None,
    ) -> dict[str, Any]:
        return {
            "revision": state.current_revision,
            "code_digest": _digest(code),
            "ir_digest": _bytes_digest(_ir_bytes(scene_ir)) if scene_ir is not None else None,
            "svg_digest": state.svg_digest,
            "png_digest": state.png_digest,
            "provenance_digest": (
                _bytes_digest(_provenance_bytes(provenance)) if provenance is not None else None
            ),
            "layout_digest": (
                _bytes_digest(_layout_bytes(layout_hints))
                if layout_hints is not None
                else None
            ),
            "decision": state.decision,
            "decision_reason": state.decision_reason,
            "selected_candidate_id": state.selected_candidate_id,
        }

    @staticmethod
    def _append_history(
        history: list[ReviewHistoryEntry],
        *,
        operation: str,
        target: str,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str | None,
    ) -> list[ReviewHistoryEntry]:
        if len(history) >= MAX_HISTORY_ENTRIES:
            raise ReviewValidationError("review history has reached the entry limit")
        return [
            *history,
            ReviewHistoryEntry(
                operation=operation,
                target=target,
                before=before,
                after=after,
                source="user",
                reason=reason,
            ),
        ]

    @staticmethod
    def _initial_state(
        code: str,
        scene_ir: dict[str, Any] | None,
        svg: str | None,
        png: bytes | None,
        provenance: list[VisualEvidence] | None,
        layout_hints: ReviewLayoutHints | None,
        *,
        selected_candidate_id: str | None = None,
    ) -> ReviewState:
        return ReviewState(
            version=0,
            timeline=["r000000"],
            cursor=0,
            current_revision="r000000",
            code_digest=_digest(code),
            ir_digest=_bytes_digest(_ir_bytes(scene_ir)) if scene_ir is not None else None,
            svg_digest=_digest(svg) if svg is not None else None,
            png_digest=_bytes_digest(png) if png is not None else None,
            provenance_digest=(
                _bytes_digest(_provenance_bytes(provenance)) if provenance is not None else None
            ),
            layout_digest=(
                _bytes_digest(_layout_bytes(layout_hints))
                if layout_hints is not None
                else None
            ),
            selected_candidate_id=selected_candidate_id,
        )

    @staticmethod
    def _check_expected(bundle: ReviewBundle, version: int, digest: str) -> None:
        if version != bundle.state.version or digest != bundle.state.code_digest:
            raise ReviewConflictError(
                "stale review state: reload the bundle before applying this operation"
            )

    @staticmethod
    def _validate_code(code: str) -> None:
        if not isinstance(code, str):
            raise ReviewValidationError("Mermaid code must be a string")
        if not code.strip():
            raise ReviewValidationError("Mermaid code cannot be empty")
        if "\x00" in code:
            raise ReviewValidationError("Mermaid code cannot contain NUL bytes")
        if len(code.encode("utf-8")) > MAX_MERMAID_BYTES:
            raise ReviewValidationError("Mermaid code exceeds the size limit")

    @staticmethod
    def _validate_reason(reason: str | None) -> None:
        if reason is not None and (not isinstance(reason, str) or len(reason) > MAX_REASON_LENGTH):
            raise ReviewValidationError("review reason is invalid or too long")

    @staticmethod
    def _validate_scene_ir(scene_ir: dict[str, Any] | None) -> None:
        if scene_ir is not None and not isinstance(scene_ir, dict):
            raise ReviewValidationError("Scene IR must be a JSON object")
        if scene_ir is not None:
            _ir_bytes(scene_ir)
            try:
                DiagramSceneIR.model_validate(scene_ir)
            except ValidationError as exc:
                raise ReviewValidationError("Scene IR violates the DiagramSceneIR schema") from exc

    @staticmethod
    def _validate_provenance(
        provenance: list[dict[str, Any] | VisualEvidence] | None,
    ) -> list[VisualEvidence] | None:
        if provenance is None:
            return None
        if not isinstance(provenance, list):
            raise ReviewValidationError("provenance must be a JSON array")
        if len(provenance) > MAX_OBSERVATION_EVIDENCE:
            raise ReviewValidationError("provenance exceeds the evidence count limit")
        try:
            normalized = [VisualEvidence.model_validate(item) for item in provenance]
        except ValidationError as exc:
            raise ReviewValidationError("provenance contains invalid evidence") from exc
        ids = [item.id for item in normalized]
        if len(ids) != len(set(ids)):
            raise ReviewValidationError("provenance evidence ids must be unique")
        _provenance_bytes(normalized)
        return normalized

    @staticmethod
    def _validate_layout_hints(payload: dict[str, Any] | None) -> ReviewLayoutHints | None:
        if payload is None:
            return None
        try:
            layout = ReviewLayoutHints.model_validate(payload)
        except ValidationError as exc:
            raise ReviewValidationError("layout hints violate the closed layout schema") from exc
        _layout_bytes(layout)
        return layout

    @staticmethod
    def _require_scene_node(scene_ir: dict[str, Any] | None, node_id: str) -> None:
        if scene_ir is None or not any(
            item.get("id") == node_id for item in scene_ir.get("elements", [])
        ):
            raise ReviewValidationError("layout node does not exist in the current Scene IR")

    @staticmethod
    def _reconcile_layout_hints(
        layout_hints: ReviewLayoutHints | None,
        scene_ir: dict[str, Any] | None,
    ) -> ReviewLayoutHints | None:
        if layout_hints is None or scene_ir is None:
            return None
        node_ids = {
            item.get("id")
            for item in scene_ir.get("elements", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        return layout_hints.retain_nodes(node_ids)

    @staticmethod
    def _validate_manifest(manifest: dict[str, Any]) -> None:
        for key in ("source_id", "status", "grade"):
            if key in manifest and not isinstance(manifest[key], str | int | float | bool | None):
                raise ReviewValidationError(f"manifest field {key!r} must be scalar")

    def _bundle_path(self, bundle_id: str) -> Path:
        if not isinstance(bundle_id, str) or not _BUNDLE_ID.fullmatch(bundle_id):
            raise UnsafeReviewPathError("invalid review bundle id")
        diagrams_root = self._safe_diagrams_root()
        candidate = diagrams_root / bundle_id
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ReviewStoreError(f"review bundle does not exist: {bundle_id}") from exc
        if candidate.is_symlink() or resolved.parent != diagrams_root:
            raise UnsafeReviewPathError("review bundle must be a direct, non-symlink directory")
        if not resolved.is_dir():
            raise ReviewStoreError(f"review bundle is not a directory: {bundle_id}")
        return resolved

    def _safe_diagrams_root(self) -> Path:
        try:
            resolved = self.diagrams_root.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ReviewStoreError(
                f"diagrams directory does not exist below {self.output_root}"
            ) from exc
        if self.diagrams_root.is_symlink() or resolved.parent != self.output_root:
            raise UnsafeReviewPathError("diagrams must be a direct, non-symlink directory")
        if not resolved.is_dir():
            raise ReviewStoreError("diagrams path is not a directory")
        return resolved

    def _artifact_path(self, bundle: Path, relative: str, *, must_exist: bool = True) -> Path:
        parts = Path(relative).parts
        if not parts or Path(relative).is_absolute() or ".." in parts:
            raise UnsafeReviewPathError("invalid review artifact path")
        candidate = bundle.joinpath(*parts)
        parent = candidate.parent.resolve(strict=must_exist)
        if parent != bundle and bundle not in parent.parents:
            raise UnsafeReviewPathError("review artifact path escapes its bundle")
        if any(path.is_symlink() for path in [candidate, *candidate.parents[:-1]] if path.exists()):
            raise UnsafeReviewPathError("review artifacts cannot be symlinks")
        if must_exist:
            try:
                resolved = candidate.resolve(strict=True)
            except FileNotFoundError as exc:
                raise ReviewValidationError(f"missing review artifact: {relative}") from exc
            outside_bundle = resolved.parent != bundle and bundle not in resolved.parents
            if resolved != candidate or outside_bundle:
                raise UnsafeReviewPathError("review artifact path escapes its bundle")
        return candidate

    def _read_code(self, bundle: Path, relative: str) -> str:
        path = self._artifact_path(bundle, relative)
        try:
            size = path.stat().st_size
            if size > MAX_MERMAID_BYTES:
                raise ReviewValidationError(f"{relative} exceeds the Mermaid size limit")
            code = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ReviewValidationError(f"{relative} is not UTF-8") from exc
        self._validate_code(code)
        return code

    def _read_json(self, bundle: Path, relative: str, *, expected: type) -> Any:
        path = self._artifact_path(bundle, relative)
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ReviewValidationError(f"{relative} exceeds the JSON size limit")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewValidationError(f"{relative} is not valid UTF-8 JSON") from exc
        if not isinstance(payload, expected):
            raise ReviewValidationError(f"{relative} must contain a JSON {expected.__name__}")
        return payload

    def _read_optional_json(self, bundle: Path, relative: str, *, expected: type) -> Any | None:
        path = self._artifact_path(bundle, relative, must_exist=False)
        if not path.exists():
            return None
        return self._read_json(bundle, relative, expected=expected)

    def _load_provenance(
        self,
        bundle: Path,
        manifest: dict[str, Any],
    ) -> list[VisualEvidence] | None:
        path = self._artifact_path(bundle, "provenance.json", must_exist=False)
        if path.exists() and path.stat().st_size > MAX_JSON_BYTES:
            raise ReviewValidationError("provenance.json exceeds the JSON size limit")
        raw_digest = _bytes_digest(path.read_bytes()) if path.exists() else None
        payload = self._read_optional_json(bundle, "provenance.json", expected=list)
        provenance = self._validate_provenance(payload)
        files = manifest.get("files")
        expected_digest = files.get("provenance.json") if isinstance(files, dict) else None
        if expected_digest is not None and expected_digest != raw_digest:
            raise ReviewConflictError("provenance.json failed its manifest digest check")
        return provenance

    def _load_layout_hints(
        self,
        bundle: Path,
        manifest: dict[str, Any],
    ) -> ReviewLayoutHints | None:
        path = self._artifact_path(bundle, "layout-hints.json", must_exist=False)
        if path.exists() and path.stat().st_size > MAX_JSON_BYTES:
            raise ReviewValidationError("layout-hints.json exceeds the JSON size limit")
        raw_digest = _bytes_digest(path.read_bytes()) if path.exists() else None
        payload = self._read_optional_json(bundle, "layout-hints.json", expected=dict)
        layout = self._validate_layout_hints(payload)
        files = manifest.get("files")
        expected_digest = files.get("layout-hints.json") if isinstance(files, dict) else None
        if raw_digest is not None and expected_digest is None:
            raise ReviewConflictError("layout-hints.json is not managed by the bundle manifest")
        if expected_digest is not None and expected_digest != raw_digest:
            raise ReviewConflictError("layout-hints.json failed its manifest digest check")
        return layout

    def _read_optional_bytes(self, bundle: Path, relative: str, limit: int) -> bytes | None:
        path = self._artifact_path(bundle, relative, must_exist=False)
        if not path.exists():
            return None
        path = self._artifact_path(bundle, relative)
        if path.stat().st_size > limit:
            raise ReviewValidationError(f"{relative} exceeds the artifact size limit")
        return path.read_bytes()

    def _read_revision_ir(
        self, bundle: Path, revision: str, expected_digest: str | None
    ) -> dict[str, Any] | None:
        if expected_digest is None:
            return None
        scene_ir = self._read_json(bundle, f"versions/{revision}.scene-ir.json", expected=dict)
        if _bytes_digest(_ir_bytes(scene_ir)) != expected_digest:
            raise ReviewValidationError(f"revision {revision} Scene IR failed its digest check")
        return scene_ir

    def _read_revision_provenance(
        self,
        bundle: Path,
        expected_digest: str | None,
    ) -> list[VisualEvidence] | None:
        if expected_digest is None:
            return None
        payload = self._read_json(
            bundle,
            f"versions/provenance/{expected_digest}.json",
            expected=list,
        )
        provenance = self._validate_provenance(payload)
        assert provenance is not None
        if _bytes_digest(_provenance_bytes(provenance)) != expected_digest:
            raise ReviewValidationError("revision provenance failed its digest check")
        return provenance

    def _read_revision_layout(
        self,
        bundle: Path,
        expected_digest: str | None,
    ) -> ReviewLayoutHints | None:
        if expected_digest is None:
            return None
        payload = self._read_json(
            bundle,
            f"versions/layout/{expected_digest}.json",
            expected=dict,
        )
        layout = self._validate_layout_hints(payload)
        assert layout is not None
        if _bytes_digest(_layout_bytes(layout)) != expected_digest:
            raise ReviewValidationError("revision layout failed its digest check")
        return layout

    def _read_revision_render(
        self,
        bundle: Path,
        revision: str,
        suffix: Literal["svg", "png"],
        expected_digest: str | None,
    ) -> bytes | None:
        if expected_digest is None:
            return None
        payload = self._read_optional_bytes(
            bundle, f"versions/{revision}.{suffix}", MAX_RENDER_BYTES
        )
        if payload is None or _bytes_digest(payload) != expected_digest:
            raise ReviewValidationError(
                f"revision {revision} {suffix.upper()} failed its digest check"
            )
        return payload

    @staticmethod
    def _updated_manifest_bytes(
        manifest: dict[str, Any],
        pending_files: dict[str, bytes | None],
        *,
        quality_status: str | None = None,
    ) -> bytes:
        updated = dict(manifest)
        if quality_status is not None:
            updated["review_quality_status"] = quality_status
        hashes = dict(updated.get("files", {}))
        for name in (
            "final.mmd",
            "final.svg",
            "final.png",
            "scene-ir.json",
            "provenance.json",
            "layout-hints.json",
        ):
            payload = pending_files.get(name)
            if name in pending_files and payload is None:
                hashes.pop(name, None)
            elif payload is not None:
                hashes[name] = _bytes_digest(payload)
        updated["files"] = hashes
        return _json_bytes(updated)

    @contextmanager
    def _locked_bundle(self, bundle_id: str) -> Iterator[Path]:
        bundle = self._bundle_path(bundle_id)
        lock_path = bundle / ".review.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise UnsafeReviewPathError("could not safely open the review lock") from exc
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield bundle
        finally:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _atomic_replace_many(self, bundle: Path, files: dict[str, bytes | None]) -> None:
        """Stage every payload, then replace targets; restore originals on an I/O error."""

        staged: dict[Path, Path | None] = {}
        originals: dict[Path, bytes | None] = {}
        try:
            for relative, payload in files.items():
                target = self._artifact_path(bundle, relative, must_exist=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                # Re-check after mkdir so a concurrently inserted symlink cannot redirect writes.
                target = self._artifact_path(bundle, relative, must_exist=False)
                originals[target] = target.read_bytes() if target.exists() else None
                if payload is None:
                    staged[target] = None
                    continue
                fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                temporary = Path(temporary_name)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged[target] = temporary
            for target, temporary in staged.items():
                if temporary is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(temporary, target)
            directory_fd = os.open(bundle, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            for target, original in originals.items():
                try:
                    if original is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_bytes(original)
                except OSError:
                    pass
            raise
        finally:
            for temporary in staged.values():
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
