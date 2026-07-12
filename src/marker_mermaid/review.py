"""Interactive, local-first review workspace and same-origin JSON API."""

from __future__ import annotations

import hmac
import ipaddress
import json
import mimetypes
import secrets
import shutil
import urllib.parse
import webbrowser
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

from marker_mermaid.config import SecurityProfile
from marker_mermaid.review_commands import apply_review_command
from marker_mermaid.review_store import (
    MAX_JSON_BYTES,
    ReviewBundle,
    ReviewConflictError,
    ReviewStore,
    ReviewStoreError,
    ReviewValidationError,
    ReviewValidationResult,
    UnsafeReviewPathError,
)
from marker_mermaid.review_ui import ReviewWorkspaceAssets, build_review_workspace_assets
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

MAX_REQUEST_BYTES = min(MAX_JSON_BYTES, 1_000_000)
REVIEW_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


def _safe_source_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "images":
        return ""
    return "/" + "/".join(urllib.parse.quote(part, safe="") for part in path.parts)


class ReviewHandler(SimpleHTTPRequestHandler):
    """Serve workspace assets, bounded review APIs, and selected output artifacts."""

    server_version = "MarkerMermaidReview/0.3"

    def __init__(
        self,
        *args,
        directory: str,
        store: ReviewStore | None = None,
        csrf_token: str = "",
        assets: ReviewWorkspaceAssets | None = None,
        **kwargs,
    ):
        self.review_root = Path(directory).resolve()
        self.store = store or ReviewStore(self.review_root)
        self.csrf_token = csrf_token
        if assets is None:
            bootstrap = {
                "diagrams": [self._summary_payload(item) for item in self.store.list_bundles()],
                "csrf_token": csrf_token,
            }
            assets = build_review_workspace_assets(bootstrap)
        self.assets = assets
        super().__init__(*args, directory=str(self.review_root), **kwargs)

    def do_GET(self):  # noqa: N802 - stdlib handler API
        if not self._authorized_host():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path in {"", "/"}:
            self._send_bytes(self.assets.html.encode(), "text/html; charset=utf-8")
            return
        if path == "/assets/review.css":
            self._send_bytes(self.assets.css.encode(), "text/css; charset=utf-8")
            return
        if path == "/assets/review.js":
            self._send_bytes(self.assets.javascript.encode(), "text/javascript; charset=utf-8")
            return
        if path == "/api/diagrams":
            self._send_json(
                {"diagrams": [self._summary_payload(item) for item in self.store.list_bundles()]}
            )
            return
        route = self._diagram_route(path)
        if route is not None and route[1] == "":
            try:
                self._send_json({"diagram": self._bundle_payload(self.store.load_bundle(route[0]))})
            except ReviewStoreError as exc:
                self._send_store_error(exc)
            return
        static_path = self._static_artifact_path(path)
        if static_path is not None:
            self._send_static_file(static_path)
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self):  # noqa: N802 - stdlib handler API
        if not self._authorized_host():
            return
        path = urllib.parse.urlsplit(self.path).path
        route = self._diagram_route(path)
        if route is None or route[1] not in {
            "/edits",
            "/history",
            "/candidate",
            "/commands",
            "/decision",
        }:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if not self._authorized_mutation():
            return
        try:
            payload = self._read_json_body()
            expected_version, expected_digest = self._expected_state(payload)
            bundle_id, action = route
            if action == "/edits":
                bundle = self.store.apply_edit(
                    bundle_id,
                    payload.get("mermaid_code"),
                    scene_ir=payload.get("scene_ir"),
                    expected_version=expected_version,
                    expected_digest=expected_digest,
                    reason=payload.get("reason"),
                )
            elif action == "/history":
                operation = payload.get("action")
                if operation not in {"undo", "redo"}:
                    raise ReviewValidationError("history action must be undo or redo")
                bundle = getattr(self.store, operation)(
                    bundle_id,
                    expected_version=expected_version,
                    expected_digest=expected_digest,
                )
            elif action == "/candidate":
                bundle = self._select_candidate(
                    bundle_id,
                    payload.get("candidate_id"),
                    expected_version,
                    expected_digest,
                )
            elif action == "/commands":
                bundle = self._apply_command(
                    bundle_id,
                    payload.get("command"),
                    expected_version,
                    expected_digest,
                )
            else:
                decision = payload.get("decision")
                reason = payload.get("reason")
                if decision == "approve":
                    bundle = self.store.approve(
                        bundle_id,
                        expected_version=expected_version,
                        expected_digest=expected_digest,
                        reason=reason,
                    )
                elif decision == "reject":
                    bundle = self.store.reject(
                        bundle_id,
                        expected_version=expected_version,
                        expected_digest=expected_digest,
                        reason=reason or "",
                    )
                else:
                    raise ReviewValidationError("decision must be approve or reject")
            self._send_json({"diagram": self._bundle_payload(bundle)})
        except ReviewStoreError as exc:
            self._send_store_error(exc)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def end_headers(self):
        self.send_header("Content-Security-Policy", REVIEW_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _authorized_mutation(self) -> bool:
        supplied = self.headers.get("X-CSRF-Token", "")
        if not self.csrf_token or not hmac.compare_digest(supplied, self.csrf_token):
            self._send_json({"error": "invalid CSRF token"}, status=HTTPStatus.FORBIDDEN)
            return False
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc != host:
                self._send_json(
                    {"error": "cross-origin mutation blocked"}, status=HTTPStatus.FORBIDDEN
                )
                return False
        return True

    def _authorized_host(self) -> bool:
        """Reject DNS-rebinding Host values before exposing the CSRF bootstrap."""

        raw_host = self.headers.get("Host", "")
        try:
            parsed = urllib.parse.urlsplit(f"//{raw_host}")
            hostname = parsed.hostname
            request_port = parsed.port
        except ValueError:
            hostname = None
            request_port = None
        listener_host, listener_port = self.server.server_address[:2]
        allowed_hosts = {str(listener_host).casefold()}
        try:
            if ipaddress.ip_address(listener_host).is_loopback:
                allowed_hosts.update({"127.0.0.1", "::1", "localhost"})
        except ValueError:
            if str(listener_host).casefold() == "localhost":
                allowed_hosts.update({"127.0.0.1", "::1", "localhost"})
        if (
            hostname is None
            or hostname.casefold() not in allowed_hosts
            or (request_port is not None and request_port != listener_port)
        ):
            self._send_json(
                {"error": "unrecognized review Host"},
                status=HTTPStatus.MISDIRECTED_REQUEST,
            )
            return False
        return True

    def _read_json_body(self) -> dict[str, Any]:
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise ReviewValidationError("Content-Type must be application/json")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ReviewValidationError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ReviewValidationError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ReviewValidationError("request body exceeds the size limit")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ReviewValidationError("request body must be a JSON object")
        return value

    @staticmethod
    def _expected_state(payload: dict[str, Any]) -> tuple[int, str]:
        version = payload.get("expected_version")
        digest = payload.get("expected_digest")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ReviewValidationError("expected_version must be an integer")
        if not isinstance(digest, str):
            raise ReviewValidationError("expected_digest must be a string")
        return version, digest

    def _select_candidate(
        self, bundle_id: str, candidate_id: Any, version: int, digest: str
    ) -> ReviewBundle:
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ReviewValidationError("candidate_id must be a non-empty string")
        bundle = self.store.load_bundle(bundle_id)
        candidate = next(
            (
                item
                for item in self._alternatives(bundle_id)
                if item.get("candidate_id") == candidate_id
            ),
            None,
        )
        if candidate is None or not isinstance(candidate.get("mermaid_code"), str):
            raise ReviewValidationError("candidate is missing or has no Mermaid code")
        scene_ir = candidate.get("scene_ir")
        if scene_ir is not None and not isinstance(scene_ir, dict):
            raise ReviewValidationError("candidate Scene IR is invalid")
        return self.store.apply_edit(
            bundle_id,
            candidate["mermaid_code"],
            scene_ir=scene_ir if scene_ir is not None else bundle.scene_ir,
            expected_version=version,
            expected_digest=digest,
            reason=f"selected alternative {candidate_id}",
            operation="select_candidate",
            selected_candidate_id=candidate_id,
        )

    def _apply_command(
        self, bundle_id: str, command: Any, version: int, digest: str
    ) -> ReviewBundle:
        if not isinstance(command, str):
            raise ReviewValidationError("command must be a string")
        current = self.store.load_bundle(bundle_id)
        result = apply_review_command(
            command,
            ir=current.scene_ir,
            mermaid_code=current.mermaid_code,
            reason=command,
        )
        if not result.applied:
            raise ReviewValidationError(f"{result.error_code}: {result.message}")
        if result.regeneration_required:
            raise ReviewValidationError(
                "diagram type changes require an explicit matching Mermaid edit or candidate"
            )
        assert result.mermaid_code is not None
        assert result.history_entry is not None
        return self.store.apply_edit(
            bundle_id,
            result.mermaid_code,
            scene_ir=result.ir,
            expected_version=version,
            expected_digest=digest,
            reason=command,
            operation="natural_language_patch",
            audit_entry=result.history_entry,
        )

    def _bundle_payload(self, bundle: ReviewBundle) -> dict[str, Any]:
        manifest = bundle.manifest
        scores = self._optional_json(bundle.bundle_id, "scores.json", {})
        provenance = self._optional_json(bundle.bundle_id, "provenance.json", [])
        warnings = scores.get("warnings", []) if isinstance(scores, dict) else []
        failures = manifest.get("failures", [])
        issues = [*warnings, *failures] if isinstance(warnings, list) else list(failures)
        if manifest.get("review_quality_status") == "unscored_user_revision":
            issues.insert(0, "User-edited revision has not been rescored against the source.")
        return {
            "id": bundle.bundle_id,
            "source_id": str(manifest.get("source_id", bundle.bundle_id)),
            "label": str(manifest.get("source_id", bundle.bundle_id)),
            "source_url": _safe_source_url(manifest.get("source_image")),
            "rendered_url": (
                f"/diagrams/{urllib.parse.quote(bundle.bundle_id, safe='')}/final.svg"
                f"?v={bundle.state.version}"
            ),
            "status": str(manifest.get("status", "unknown")),
            "grade": str(manifest.get("grade", "U")),
            "decision": bundle.state.decision,
            "decision_reason": bundle.state.decision_reason,
            "selected_candidate_id": bundle.state.selected_candidate_id,
            "mermaid_code": bundle.mermaid_code,
            "scene_ir": bundle.scene_ir or {},
            "provenance": provenance,
            "issues": issues,
            "alternatives": self._alternatives(bundle.bundle_id),
            "history": [entry.model_dump(mode="json") for entry in bundle.history],
            "version": bundle.state.version,
            "digest": bundle.state.code_digest,
            "can_undo": bundle.state.cursor > 0,
            "can_redo": bundle.state.cursor + 1 < len(bundle.state.timeline),
        }

    @staticmethod
    def _summary_payload(summary) -> dict[str, Any]:
        return {
            "id": summary.bundle_id,
            "source_id": summary.source_id,
            "label": summary.source_id,
            "status": summary.status,
            "grade": summary.grade,
            "decision": summary.decision,
            "version": summary.version,
            "digest": summary.code_digest,
        }

    def _alternatives(self, bundle_id: str) -> list[dict[str, Any]]:
        bundle_path = self.store._bundle_path(bundle_id)  # noqa: SLF001 - shared safety boundary
        directory = bundle_path / "alternatives"
        if not directory.is_dir() or directory.is_symlink():
            return []
        alternatives: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            if path.is_symlink() or path.parent != directory:
                continue
            try:
                value = self.store._read_json(  # noqa: SLF001 - fixed sidecar artifact
                    bundle_path, f"alternatives/{path.name}", expected=dict
                )
            except ReviewStoreError:
                continue
            alternatives.append(value)
        return alternatives

    def _optional_json(self, bundle_id: str, name: str, default: Any) -> Any:
        bundle_path = self.store._bundle_path(bundle_id)  # noqa: SLF001
        try:
            value = self.store._read_optional_json(  # noqa: SLF001
                bundle_path, name, expected=dict if isinstance(default, dict) else list
            )
        except ReviewStoreError:
            return default
        return default if value is None else value

    @staticmethod
    def _diagram_route(path: str) -> tuple[str, str] | None:
        prefix = "/api/diagrams/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix) :]
        encoded_id, separator, suffix = remainder.partition("/")
        if not encoded_id:
            return None
        bundle_id = urllib.parse.unquote(encoded_id)
        return bundle_id, f"/{suffix}" if separator else ""

    @staticmethod
    def _allowed_static_path(path: str) -> bool:
        decoded = urllib.parse.unquote(path)
        parts = PurePosixPath(decoded).parts
        if ".." in parts:
            return False
        if len(parts) == 3 and parts[1] == "images":
            return not parts[2].startswith(".")
        return len(parts) == 4 and parts[1] == "diagrams" and parts[3] in {"final.svg", "final.png"}

    def _static_artifact_path(self, path: str) -> Path | None:
        """Resolve an allowlisted artifact without following any symlink component."""

        if not self._allowed_static_path(path):
            return None
        decoded = urllib.parse.unquote(path)
        relative_parts = PurePosixPath(decoded).parts[1:]
        candidate = self.review_root.joinpath(*relative_parts)
        current = self.review_root
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                return None
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError):
            return None
        if self.review_root not in resolved.parents or not resolved.is_file():
            return None
        return resolved

    def _send_static_file(self, path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError:
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        with handle:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            shutil.copyfileobj(handle, self.wfile)

    def _send_store_error(self, exc: ReviewStoreError) -> None:
        if isinstance(exc, ReviewConflictError):
            status = HTTPStatus.CONFLICT
        elif isinstance(exc, ReviewValidationError):
            status = HTTPStatus.UNPROCESSABLE_ENTITY
        elif isinstance(exc, UnsafeReviewPathError):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.NOT_FOUND
        self._send_json({"error": str(exc)}, status=status)

    def _send_json(self, value: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
            "application/json; charset=utf-8",
            status=status,
        )

    def _send_bytes(
        self,
        payload: bytes,
        content_type: str,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve_review(output_dir: str | Path, *, host: str, port: int, open_browser: bool) -> None:
    """Serve the interactive workspace until interrupted, closing Chromium cleanly."""

    root = Path(output_dir).resolve()
    if not (root / "diagrams").is_dir():
        raise FileNotFoundError(f"no diagrams directory below {root}")
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)

    def validate_edit(code: str) -> ReviewValidationResult:
        outcome = validator.validate(code, 15.0)
        rendered = outcome.runtime
        return ReviewValidationResult(
            valid=rendered.syntax_valid and rendered.render_valid,
            svg=rendered.svg,
            png=rendered.png,
            diagram_type=rendered.diagram_type,
            warnings=outcome.warnings,
            error=rendered.error,
        )

    store = ReviewStore(root, validator=validate_edit)
    csrf_token = secrets.token_urlsafe(32)
    assets = build_review_workspace_assets(
        {
            "diagrams": [ReviewHandler._summary_payload(item) for item in store.list_bundles()],
            "csrf_token": csrf_token,
        }
    )
    handler = partial(
        ReviewHandler,
        directory=str(root),
        store=store,
        csrf_token=csrf_token,
        assets=assets,
    )
    server = ThreadingHTTPServer((host, port), handler)
    url_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{url_host}:{server.server_port}/"
    print(f"Review workspace: {url}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            "Warning: review has CSRF protection but no user authentication; use a trusted network."
        )
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.close()
