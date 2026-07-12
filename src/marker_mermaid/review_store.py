"""Traversal-safe persistence for the local review workspace.

The review server is intentionally kept separate from this module.  ``ReviewStore``
only operates on sidecar bundles below ``<output>/diagrams`` and exposes an
optimistic-concurrency API that an HTTP, CLI, or desktop frontend can share.
"""

from __future__ import annotations

import hashlib
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

from marker_mermaid.models import DiagramSceneIR, ReviewHistoryEntry

REVIEW_SCHEMA_VERSION = "mmx-review-0.3"
MAX_MERMAID_BYTES = 1_000_000
MAX_JSON_BYTES = 4_000_000
MAX_RENDER_BYTES = 16_000_000
MAX_HISTORY_ENTRIES = 10_000
MAX_REASON_LENGTH = 4_096
_BUNDLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")


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
    schema_version: Literal[REVIEW_SCHEMA_VERSION] = REVIEW_SCHEMA_VERSION
    version: int = Field(ge=0)
    timeline: list[str]
    cursor: int = Field(ge=0)
    current_revision: str
    code_digest: str
    ir_digest: str | None = None
    svg_digest: str | None = None
    png_digest: str | None = None
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

    @field_validator("code_digest", "ir_digest", "svg_digest", "png_digest")
    @classmethod
    def digest_is_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("artifact digest must be a lowercase SHA-256 digest")
        return value

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
    history: list[ReviewHistoryEntry]
    state: ReviewState


class _RevisionSnapshot(BaseModel):
    schema_version: Literal[REVIEW_SCHEMA_VERSION] = REVIEW_SCHEMA_VERSION
    revision: str
    code_digest: str
    ir_digest: str | None = None
    svg_digest: str | None = None
    png_digest: str | None = None
    decision: ReviewDecision
    decision_reason: str | None = None
    selected_candidate_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _bytes_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _ir_bytes(value: dict[str, Any]) -> bytes:
    return _json_bytes(value)


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

    def list_bundles(self) -> list[ReviewBundleSummary]:
        if not self.diagrams_root.exists():
            return []
        diagrams_root = self._safe_diagrams_root()
        summaries: list[ReviewBundleSummary] = []
        with os.scandir(diagrams_root) as entries:
            bundle_ids = sorted(
                entry.name
                for entry in entries
                if entry.is_dir(follow_symlinks=False) and _BUNDLE_ID.fullmatch(entry.name)
            )
        for bundle_id in bundle_ids:
            try:
                bundle = self.load_bundle(bundle_id)
            except ReviewStoreError:
                continue
            summaries.append(
                ReviewBundleSummary(
                    bundle_id=bundle_id,
                    source_id=str(bundle.manifest.get("source_id", bundle_id)),
                    status=str(bundle.manifest.get("status", "unknown")),
                    grade=str(bundle.manifest.get("grade", "U")),
                    version=bundle.state.version,
                    decision=bundle.state.decision,
                    code_digest=bundle.state.code_digest,
                )
            )
        return summaries

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
        else:
            state = self._initial_state(
                code,
                scene_ir,
                svg,
                png,
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
    ) -> ReviewBundle:
        """Atomically persist code, IR, render artifacts, state, and audit history."""

        self._validate_code(code)
        self._validate_scene_ir(scene_ir)
        self._validate_reason(reason)
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
            before = self._state_value(bundle.state, bundle.mermaid_code, bundle.scene_ir)
            return self._commit_new_revision(
                bundle,
                code=code,
                scene_ir=scene_ir,
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
            -1,
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
            1,
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
            before = self._state_value(bundle.state, bundle.mermaid_code, bundle.scene_ir)
            return self._commit_new_revision(
                bundle,
                code=bundle.mermaid_code,
                scene_ir=bundle.scene_ir,
                svg=bundle.svg,
                png=bundle.png,
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
        delta: int,
        *,
        expected_version: int,
        expected_digest: str,
        reason: str | None,
    ) -> ReviewBundle:
        self._validate_reason(reason)
        with self._locked_bundle(bundle_id):
            bundle = self.load_bundle(bundle_id)
            self._check_expected(bundle, expected_version, expected_digest)
            next_cursor = bundle.state.cursor + delta
            if not 0 <= next_cursor < len(bundle.state.timeline):
                action = "undo" if delta < 0 else "redo"
                raise ReviewConflictError(f"nothing to {action}")
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
            before = self._state_value(bundle.state, bundle.mermaid_code, bundle.scene_ir)
            state = bundle.state.model_copy(
                update={
                    "version": bundle.state.version + 1,
                    "cursor": next_cursor,
                    "current_revision": revision,
                    "code_digest": snapshot.code_digest,
                    "ir_digest": snapshot.ir_digest,
                    "svg_digest": snapshot.svg_digest,
                    "png_digest": snapshot.png_digest,
                    "decision": snapshot.decision,
                    "decision_reason": snapshot.decision_reason,
                    "selected_candidate_id": snapshot.selected_candidate_id,
                    "updated_at": datetime.now(UTC),
                }
            )
            after = self._state_value(state, code, scene_ir)
            history = self._append_history(
                bundle.history,
                operation="undo" if delta < 0 else "redo",
                target=bundle_id,
                before=before,
                after=after,
                reason=reason,
            )
            files: dict[str, bytes | None] = {
                "final.mmd": code.encode("utf-8"),
                "scene-ir.json": _ir_bytes(scene_ir) if scene_ir is not None else None,
                "final.svg": svg,
                "final.png": png,
                "review-history.json": _json_bytes(
                    [entry.model_dump(mode="json") for entry in history]
                ),
                "review-state.json": _json_bytes(state.model_dump(mode="json")),
            }
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
        state = ReviewState(
            version=next_version,
            timeline=timeline,
            cursor=len(timeline) - 1,
            current_revision=revision,
            code_digest=_digest(code),
            ir_digest=_bytes_digest(_ir_bytes(scene_ir)) if scene_ir is not None else None,
            svg_digest=_digest(svg) if svg is not None else None,
            png_digest=_bytes_digest(png) if png is not None else None,
            decision=decision,
            decision_reason=decision_reason,
            selected_candidate_id=selected_candidate_id,
        )
        after = self._state_value(state, code, scene_ir)
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
            }
        )
        if scene_ir is not None:
            files[f"versions/{revision}.scene-ir.json"] = _ir_bytes(scene_ir)
        if svg is not None:
            files[f"versions/{revision}.svg"] = svg.encode("utf-8")
        if png is not None:
            files[f"versions/{revision}.png"] = png
        content_changed = code != bundle.mermaid_code or scene_ir != bundle.scene_ir
        files["manifest.json"] = self._updated_manifest_bytes(
            bundle.manifest,
            files,
            quality_status=("unscored_user_revision" if content_changed else None),
        )
        self._atomic_replace_many(bundle_path, files)
        return self.load_bundle(bundle.bundle_id)

    @staticmethod
    def _state_value(
        state: ReviewState, code: str, scene_ir: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "revision": state.current_revision,
            "code_digest": _digest(code),
            "ir_digest": _bytes_digest(_ir_bytes(scene_ir)) if scene_ir is not None else None,
            "svg_digest": state.svg_digest,
            "png_digest": state.png_digest,
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
        for name in ("final.mmd", "final.svg", "final.png", "scene-ir.json"):
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
