from __future__ import annotations

import hashlib
import json
import os
import threading
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

import marker_mermaid.models as models
import marker_mermaid.review_store as review_store_module
from marker_mermaid.models import VisualEvidence
from marker_mermaid.review_store import (
    MAX_JSON_BYTES,
    MAX_RENDER_BYTES,
    ReviewConflictError,
    ReviewStore,
    ReviewValidationError,
    ReviewValidationResult,
    UnsafeReviewPathError,
)


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1, 1), "white").save(output, format="PNG")
    return output.getvalue()


def _valid_render(code: str) -> ReviewValidationResult:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return ReviewValidationResult(
        valid=True,
        svg=f"<svg viewBox='0 0 1 1'><title>{digest}</title></svg>",
    )


def make_bundle(tmp_path, bundle_id="diagram-a", code="flowchart LR\n  A --> B\n"):
    bundle = tmp_path / "diagrams" / bundle_id
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps({"source_id": "source-a", "status": "review_required", "grade": "C"}),
        encoding="utf-8",
    )
    (bundle / "final.mmd").write_text(code, encoding="utf-8")
    (bundle / "review-history.json").write_text("[]\n", encoding="utf-8")
    return bundle


def write_transaction_marker(bundle, entries):
    (bundle / review_store_module._REVIEW_TRANSACTION_FILE).write_text(
        json.dumps(
            {
                "schema_version": review_store_module.REVIEW_TRANSACTION_SCHEMA_VERSION,
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )


def test_review_store_rolls_forward_an_interrupted_transaction(tmp_path):
    bundle = make_bundle(tmp_path)
    replacement = b"flowchart LR\n  B --> A\n"
    temporary = ".final.mmd.recovery.tmp"
    (bundle / temporary).write_bytes(replacement)
    write_transaction_marker(
        bundle,
        [
            {
                "path": "final.mmd",
                "temporary": temporary,
                "digest": hashlib.sha256(replacement).hexdigest(),
            }
        ],
    )

    loaded = ReviewStore(tmp_path).load_bundle("diagram-a")

    assert loaded.mermaid_code == replacement.decode()
    assert not (bundle / temporary).exists()
    assert not (bundle / review_store_module._REVIEW_TRANSACTION_FILE).exists()


def test_review_store_finishes_a_transaction_whose_target_was_already_replaced(tmp_path):
    replacement = b"flowchart LR\n  B --> A\n"
    bundle = make_bundle(tmp_path, code=replacement.decode())
    temporary = ".final.mmd.already-replaced.tmp"
    write_transaction_marker(
        bundle,
        [
            {
                "path": "final.mmd",
                "temporary": temporary,
                "digest": hashlib.sha256(replacement).hexdigest(),
            }
        ],
    )

    loaded = ReviewStore(tmp_path).load_bundle("diagram-a")

    assert loaded.mermaid_code == replacement.decode()
    assert not (bundle / review_store_module._REVIEW_TRANSACTION_FILE).exists()


def test_list_bundles_surfaces_an_unrecoverable_transaction(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / review_store_module._REVIEW_TRANSACTION_FILE).write_text(
        "{}", encoding="utf-8"
    )

    [summary] = ReviewStore(tmp_path).list_bundles()

    assert summary.bundle_id == "diagram-a"
    assert summary.status == "error"
    assert summary.grade == "U"
    assert "invalid schema" in (summary.error or "")


def test_bundle_reads_wait_for_the_bundle_lock(tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    started = threading.Event()
    finished = threading.Event()

    def read_bundle():
        started.set()
        store.load_bundle("diagram-a")
        finished.set()

    with store._locked_bundle("diagram-a"):
        reader = threading.Thread(target=read_bundle)
        reader.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.1)
    reader.join(timeout=2)

    assert finished.is_set()


def test_review_store_fails_fast_on_unsupported_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(review_store_module.os, "name", "nt")

    with pytest.raises(RuntimeError, match="POSIX platforms only"):
        ReviewStore(tmp_path)


def write_v05_generation_manifest(bundle):
    svg = b"<svg viewBox='0 0 1 1'/>"
    png = _png_bytes()
    (bundle / "final.svg").write_bytes(svg)
    (bundle / "final.png").write_bytes(png)
    artifacts = {
        "final.mmd": (bundle / "final.mmd").read_bytes(),
        "final.svg": svg,
        "final.png": png,
    }
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in artifacts.items()}
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(
        {
            "schema_version": "mmx-sidecar-0.5",
            "files": hashes,
            "generation_artifact_presence": {name: True for name in artifacts},
            "emitted_diagram_type": "flowchart",
            "runtime_diagram_type": "flowchart",
            "generation_validation_receipt": {
                "schema_version": "1",
                "code_sha256": hashes["final.mmd"],
                "svg_sha256": hashes["final.svg"],
                "png_sha256": hashes["final.png"],
                "security_profile": "strict",
                "emitted_diagram_type": "flowchart",
                "runtime_diagram_type": "flowchart",
            },
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def evidence(evidence_id="ocr-1", text="Original"):
    return {
        "id": evidence_id,
        "kind": "ocr_token",
        "bbox": [0, 0, 10, 10],
        "text": text,
        "score": 0.9,
        "source_block_ids": ["block-1"],
    }


def evidence_with_blocks(block_ids, evidence_id="ocr-1", text="Original"):
    payload = evidence(evidence_id, text)
    payload["source_block_ids"] = block_ids
    return payload


def write_provenance(bundle, items, *, record_manifest_hash=True):
    payload = (
        json.dumps(items, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    (bundle / "provenance.json").write_bytes(payload)
    if record_manifest_hash:
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.setdefault("files", {})["provenance.json"] = hashlib.sha256(payload).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def write_scene(bundle, node_ids=("A", "B")):
    scene = {
        "elements": [
            {
                "id": node_id,
                "role": "node",
                "text": node_id,
                "bbox": [index * 20, 0, index * 20 + 10, 10],
            }
            for index, node_id in enumerate(node_ids)
        ],
        "relations": [],
        "groups": [],
    }
    (bundle / "scene-ir.json").write_text(json.dumps(scene), encoding="utf-8")
    return scene


def test_list_and_load_bundle_without_mutating_legacy_sidecar(tmp_path):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path)

    [summary] = store.list_bundles()
    bundle = store.load_bundle("diagram-a")

    assert summary.source_id == "source-a"
    assert summary.version == 0
    assert bundle.state.decision == "pending"
    assert bundle.mermaid_code.startswith("flowchart")
    assert not (bundle_path / "review-state.json").exists()


@pytest.mark.parametrize(
    ("artifact", "replacement"),
    [
        ("final.mmd", b"flowchart LR\n  X --> Y\n"),
        ("final.svg", b"<svg viewBox='0 0 2 2'/>"),
        ("final.png", _png_bytes() + b"tampered"),
    ],
)
def test_initial_load_rejects_manifest_artifact_tampering(tmp_path, artifact, replacement):
    bundle = make_bundle(tmp_path)
    write_v05_generation_manifest(bundle)
    (bundle / artifact).write_bytes(replacement)

    with pytest.raises(ReviewConflictError, match="manifest digest"):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_initial_load_rejects_generation_presence_mismatch(tmp_path):
    bundle = make_bundle(tmp_path)
    manifest = write_v05_generation_manifest(bundle)
    manifest["generation_artifact_presence"]["final.png"] = False
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReviewConflictError, match="artifact presence"):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_initial_load_rejects_generation_receipt_mismatch(tmp_path):
    bundle = make_bundle(tmp_path)
    manifest = write_v05_generation_manifest(bundle)
    manifest["generation_validation_receipt"]["svg_sha256"] = "0" * 64
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReviewConflictError, match="generation validation receipt"):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_reviewed_bundle_keeps_generation_receipt_bound_to_initial_revision(tmp_path):
    bundle = make_bundle(tmp_path)
    write_v05_generation_manifest(bundle)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  B --> A\n",
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["generation_validation_receipt"]["code_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReviewConflictError, match="generation validation receipt"):
        store.load_bundle("diagram-a")


def test_published_generation_requires_a_publication_receipt(tmp_path):
    bundle = make_bundle(tmp_path)
    manifest = write_v05_generation_manifest(bundle)
    manifest.update(
        {
            "status": "success",
            "grade": "A",
            "publish": True,
            "review_required": False,
            "generation_publication_receipt": None,
        }
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReviewConflictError, match="missing its publication receipt"):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_initial_load_rejects_publication_receipt_reference_mismatch(tmp_path):
    bundle = make_bundle(tmp_path)
    manifest = write_v05_generation_manifest(bundle)
    scores = {
        "aggregate_score": 0.8,
        "grade": "B",
        "metrics": {"ocr_recall": 0.8},
        "warnings": [],
    }
    scores_payload = (json.dumps(scores, indent=2, sort_keys=True) + "\n").encode()
    (bundle / "scores.json").write_bytes(scores_payload)
    manifest["files"]["scores.json"] = hashlib.sha256(scores_payload).hexdigest()
    manifest.update(
        {
            "source_id": "diagram-a",
            "selected_candidate_id": "candidate-1",
            "status": "success",
            "grade": "B",
            "publish": True,
            "review_required": False,
            "generation_publication_receipt": {
                "schema_version": "1",
                "source_id": "diagram-a",
                "selected_candidate_id": "candidate-1",
                "candidate_validation_sha256": "0" * 64,
                "candidate_quality_sha256": "0" * 64,
                "publish_policy": "best_effort_validated",
                "security_profile": "strict",
                "publish": True,
                "review_required": False,
                "status": "success",
                "grade": "B",
            },
        }
    )
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReviewConflictError, match="publication receipt"):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_legacy_bundle_keeps_uninspected_png_read_compatibility(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "final.png").write_bytes(b"legacy-uninspected-preview")

    loaded = ReviewStore(tmp_path).load_bundle("diagram-a")

    assert loaded.png == b"legacy-uninspected-preview"


def test_layout_hint_is_revisioned_without_changing_code_scene_or_provenance(tmp_path):
    bundle_path = make_bundle(tmp_path)
    source_scene = write_scene(bundle_path)
    write_provenance(bundle_path, [evidence()])
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")

    moved = store.apply_layout_hint(
        "diagram-a",
        node_id="A",
        x=0.25,
        y=0.75,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
        reason="place API left",
    )

    assert moved.mermaid_code == initial.mermaid_code
    assert moved.scene_ir == source_scene
    assert moved.provenance == initial.provenance
    assert moved.state.code_digest == initial.state.code_digest
    assert moved.state.version == 1
    assert moved.state.layout_digest
    assert moved.layout_hints.nodes[0].model_dump() == {
        "node_id": "A",
        "x": 0.25,
        "y": 0.75,
    }
    assert moved.manifest["review_quality_status"] == "unscored_user_revision"
    layout_blob = bundle_path / "versions/layout" / f"{moved.state.layout_digest}.json"
    assert layout_blob.is_file()
    assert moved.manifest["files"]["layout-hints.json"] == moved.state.layout_digest
    assert moved.history[-1].operation == "move_node"
    assert moved.history[-1].target == "A"
    assert moved.history[-1].before == {"layout_position": None}
    assert moved.history[-1].after == {"layout_position": [0.25, 0.75]}

    with pytest.raises(ReviewConflictError):
        store.apply_layout_hint(
            "diagram-a",
            node_id="B",
            x=0.5,
            y=0.5,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )
    with pytest.raises(ReviewValidationError, match="did not change"):
        store.apply_layout_hint(
            "diagram-a",
            node_id="A",
            x=0.25,
            y=0.75,
            expected_version=moved.state.version,
            expected_digest=moved.state.code_digest,
        )


def test_layout_hint_undo_redo_and_scene_reconciliation(tmp_path):
    bundle_path = make_bundle(tmp_path)
    write_scene(bundle_path)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    moved = store.apply_layout_hint(
        "diagram-a",
        node_id="A",
        x=0.2,
        y=0.3,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )

    undone = store.undo(
        "diagram-a",
        expected_version=moved.state.version,
        expected_digest=moved.state.code_digest,
    )
    assert undone.layout_hints is None
    assert not (bundle_path / "layout-hints.json").exists()
    assert "layout-hints.json" not in undone.manifest["files"]

    redone = store.redo(
        "diagram-a",
        expected_version=undone.state.version,
        expected_digest=undone.state.code_digest,
    )
    assert redone.layout_hints.nodes[0].node_id == "A"

    managed_scene_bytes = (bundle_path / "scene-ir.json").read_bytes()
    scene_without_a = write_scene(bundle_path, ("B",))
    # Restore the managed Scene file before applying the explicit editor transaction.
    (bundle_path / "scene-ir.json").write_bytes(managed_scene_bytes)
    pruned = store.apply_edit(
        "diagram-a",
        redone.mermaid_code,
        scene_ir=scene_without_a,
        expected_version=redone.state.version,
        expected_digest=redone.state.code_digest,
    )
    assert pruned.layout_hints is None


def test_layout_hint_rejects_unknown_node_and_tampering(tmp_path):
    bundle_path = make_bundle(tmp_path)
    write_scene(bundle_path)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    with pytest.raises(ReviewValidationError, match="does not exist"):
        store.apply_layout_hint(
            "diagram-a",
            node_id="missing",
            x=0.5,
            y=0.5,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )

    moved = store.apply_layout_hint(
        "diagram-a",
        node_id="A",
        x=0.5,
        y=0.5,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    (bundle_path / "layout-hints.json").write_text(
        json.dumps(
            {
                "schema_version": "mmx-review-layout-0.1",
                "coordinate_space": "normalized",
                "nodes": [{"node_id": "A", "x": 0.9, "y": 0.9}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReviewConflictError, match="manifest digest"):
        store.load_bundle("diagram-a")
    assert moved.state.layout_digest


def test_unmanaged_layout_artifact_is_not_adopted_by_legacy_bundle(tmp_path):
    bundle_path = make_bundle(tmp_path)
    write_scene(bundle_path)
    (bundle_path / "layout-hints.json").write_text(
        json.dumps(
            {
                "schema_version": "mmx-review-layout-0.1",
                "coordinate_space": "normalized",
                "nodes": [{"node_id": "A", "x": 0.5, "y": 0.5}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReviewConflictError, match="not managed"):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_list_summaries_is_bounded_and_skips_heavy_artifacts(monkeypatch, tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path)

    def reject_heavy_reads(*args, **kwargs):
        raise AssertionError("summary listing must not read render artifacts")

    monkeypatch.setattr(store, "_read_optional_bytes", reject_heavy_reads)

    [summary] = store.list_bundles(limit=1)
    assert summary.bundle_id == "diagram-a"
    with pytest.raises(ReviewValidationError, match="between 1 and"):
        store.list_bundles(limit=0)


def test_failed_bundle_bootstraps_review_from_alternative_and_can_be_repaired(tmp_path):
    bundle_path = make_bundle(tmp_path)
    (bundle_path / "final.mmd").unlink()
    alternatives = bundle_path / "alternatives"
    alternatives.mkdir()
    (alternatives / "candidate-a.json").write_text(
        json.dumps(
            {
                "candidate_id": "candidate-a",
                "mermaid_code": "flowchart LR\n  A -->\n",
                "scene_ir": None,
            }
        ),
        encoding="utf-8",
    )
    validator_calls = []

    def validator(code):
        validator_calls.append(code)
        return "-->\n" not in code

    store = ReviewStore(tmp_path, validator=validator)

    [summary] = store.list_bundles()
    loaded = store.load_bundle("diagram-a")

    assert summary.bundle_id == "diagram-a"
    assert loaded.state.selected_candidate_id == "candidate-a"
    assert not (bundle_path / "final.mmd").exists()

    with pytest.raises(ReviewValidationError, match="rejected approval"):
        store.approve(
            "diagram-a",
            expected_version=loaded.state.version,
            expected_digest=loaded.state.code_digest,
        )
    assert validator_calls == [loaded.mermaid_code]
    assert not (bundle_path / "final.mmd").exists()

    repaired = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> B\n",
        expected_version=loaded.state.version,
        expected_digest=loaded.state.code_digest,
    )

    assert repaired.mermaid_code.endswith("A --> B\n")
    assert (bundle_path / "final.mmd").is_file()
    assert (bundle_path / "versions/r000000.mmd").read_text().endswith("A -->\n")


def test_approval_requires_validator_and_commits_fresh_render(tmp_path):
    bundle_path = make_bundle(tmp_path)
    current = ReviewStore(tmp_path).load_bundle("diagram-a")
    with pytest.raises(ReviewValidationError, match="requires a configured"):
        ReviewStore(tmp_path).approve(
            "diagram-a",
            expected_version=current.state.version,
            expected_digest=current.state.code_digest,
        )

    store = ReviewStore(
        tmp_path,
        validator=lambda code: ReviewValidationResult(
            valid=True,
            svg="<svg viewBox='0 0 2 2'/>",
            png=_png_bytes(),
        ),
    )
    approved = store.approve(
        "diagram-a",
        expected_version=current.state.version,
        expected_digest=current.state.code_digest,
    )

    assert approved.state.decision == "approved"
    assert approved.svg == "<svg viewBox='0 0 2 2'/>"
    assert approved.png == _png_bytes()
    assert (bundle_path / "final.svg").read_text() == approved.svg


def test_review_validation_result_is_frozen():
    result = ReviewValidationResult(valid=True, svg="<svg viewBox='0 0 1 1'/>")

    with pytest.raises(ValidationError, match="frozen"):
        result.svg = "<svg viewBox='0 0 2 2'/>"


@pytest.mark.parametrize(
    "update",
    [
        {"png": b"not-a-png"},
        {"svg": "<svg viewBox='0 0 1 1'/>" + " " * MAX_RENDER_BYTES},
        {"svg": "<svg viewBox='0 0 1 1'><script/></svg>"},
    ],
)
def test_commit_revalidates_model_copy_bypassed_render_artifacts(tmp_path, update):
    bundle = make_bundle(tmp_path)
    trusted_shape = ReviewValidationResult(
        valid=True,
        svg="<svg viewBox='0 0 1 1'/>",
        png=_png_bytes(),
    )
    bypassed = trusted_shape.model_copy(update=update)
    store = ReviewStore(tmp_path, validator=lambda code: bypassed)
    current = store.load_bundle("diagram-a")
    before = {
        path.relative_to(bundle): path.read_bytes()
        for path in bundle.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }

    with pytest.raises(ReviewValidationError):
        store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\n  B --> A\n",
            expected_version=current.state.version,
            expected_digest=current.state.code_digest,
        )

    after = {
        path.relative_to(bundle): path.read_bytes()
        for path in bundle.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    assert after == before


def test_code_edit_without_fresh_render_removes_stale_artifacts(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "final.svg").write_text("<svg><title>old code</title></svg>")
    (bundle / "final.png").write_bytes(_png_bytes())
    store = ReviewStore(tmp_path, validator=lambda code: True)
    current = store.load_bundle("diagram-a")

    edited = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  X --> Y\n",
        expected_version=current.state.version,
        expected_digest=current.state.code_digest,
    )

    assert edited.svg is None
    assert edited.png is None
    assert edited.state.svg_digest is None
    assert edited.state.png_digest is None
    assert not (bundle / "final.svg").exists()
    assert not (bundle / "final.png").exists()
    assert (bundle / "versions/r000000.svg").exists()
    assert (bundle / "versions/r000000.png").exists()


def test_approval_rejects_boolean_validator_without_writing(tmp_path):
    bundle = make_bundle(tmp_path)
    store = ReviewStore(tmp_path, validator=lambda code: True)
    current = store.load_bundle("diagram-a")

    with pytest.raises(ReviewValidationError, match="fresh render artifacts"):
        store.approve(
            "diagram-a",
            expected_version=current.state.version,
            expected_digest=current.state.code_digest,
        )

    assert not (bundle / "review-state.json").exists()
    assert json.loads((bundle / "review-history.json").read_text()) == []


def test_bundle_and_artifact_traversal_are_rejected(tmp_path):
    make_bundle(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, tmp_path / "diagrams" / "linked")
    store = ReviewStore(tmp_path)

    for unsafe in ("../outside", "/tmp/x", "a/b", ".."):
        with pytest.raises(UnsafeReviewPathError):
            store.load_bundle(unsafe)
    with pytest.raises(UnsafeReviewPathError):
        store.load_bundle("linked")
    assert [item.bundle_id for item in store.list_bundles()] == ["diagram-a"]


def test_symlinked_required_artifact_is_rejected(tmp_path):
    bundle = make_bundle(tmp_path)
    external = tmp_path / "external.mmd"
    external.write_text("flowchart LR\nX --> Y\n", encoding="utf-8")
    (bundle / "final.mmd").unlink()
    os.symlink(external, bundle / "final.mmd")

    with pytest.raises(UnsafeReviewPathError):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_symlinked_diagrams_directory_is_never_followed(tmp_path):
    external_root = tmp_path / "external"
    make_bundle(external_root)
    os.symlink(external_root / "diagrams", tmp_path / "diagrams")

    store = ReviewStore(tmp_path)
    with pytest.raises(UnsafeReviewPathError):
        store.list_bundles()
    with pytest.raises(UnsafeReviewPathError):
        store.load_bundle("diagram-a")


def test_bundle_swap_during_staging_never_touches_external_tree(monkeypatch, tmp_path):
    bundle = make_bundle(tmp_path)
    outside = tmp_path / "outside"
    (outside / "versions").mkdir(parents=True)
    sentinel = outside / "versions/r000000.mmd"
    sentinel.write_text("DO NOT TOUCH", encoding="utf-8")
    moved = tmp_path / "moved-bundle"
    store = ReviewStore(tmp_path)
    current = store.load_bundle("diagram-a")
    real_open = review_store_module.os.open
    swapped = False

    def swap_before_first_staged_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if (
            not swapped
            and isinstance(path, str)
            and path.startswith(".r000000.mmd.")
            and path.endswith(".tmp")
            and dir_fd is not None
        ):
            bundle.rename(moved)
            bundle.symlink_to(outside, target_is_directory=True)
            swapped = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(review_store_module.os, "open", swap_before_first_staged_open)

    with pytest.raises(UnsafeReviewPathError):
        store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\n  B --> A\n",
            expected_version=current.state.version,
            expected_digest=current.state.code_digest,
        )

    assert swapped
    assert sentinel.read_text(encoding="utf-8") == "DO NOT TOUCH"
    assert sorted(
        path.relative_to(outside).as_posix() for path in outside.rglob("*") if path.is_file()
    ) == ["versions/r000000.mmd"]


def test_edit_uses_validator_and_is_atomic_on_rejection(tmp_path):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path, validator=lambda code: "BAD" not in code)
    before = {path.name: path.read_bytes() for path in bundle_path.iterdir()}
    loaded = store.load_bundle("diagram-a")

    with pytest.raises(ReviewValidationError):
        store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\nBAD --> B\n",
            expected_version=loaded.state.version,
            expected_digest=loaded.state.code_digest,
        )

    assert {path.name: path.read_bytes() for path in bundle_path.iterdir()} == before


@pytest.mark.parametrize(
    "scene_ir",
    [
        {"elements": [{"id": "A", "role": "node"}], "relations": [], "groups": []},
        {
            "elements": [
                {"id": "A", "role": "node", "bbox": [0, 0, 1, 1]},
                {"id": "A", "role": "node", "bbox": [2, 0, 3, 1]},
            ],
            "relations": [],
            "groups": [],
        },
        {
            "elements": [{"id": "A", "role": "node", "bbox": [0, 0, 1, 1]}],
            "relations": [
                {
                    "id": "E",
                    "source_id": "A",
                    "target_id": "missing",
                    "relation_type": "edge",
                }
            ],
            "groups": [],
        },
    ],
)
def test_edit_rejects_invalid_scene_ir_without_writing(tmp_path, scene_ir):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    current = store.load_bundle("diagram-a")
    before = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ReviewValidationError, match="DiagramSceneIR schema"):
        store.apply_edit(
            "diagram-a",
            current.mermaid_code,
            scene_ir=scene_ir,
            expected_version=current.state.version,
            expected_digest=current.state.code_digest,
        )

    after = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_io_failure_restores_code_history_and_state(monkeypatch, tmp_path):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    loaded = store.load_bundle("diagram-a")
    original_code = (bundle_path / "final.mmd").read_bytes()
    original_history = (bundle_path / "review-history.json").read_bytes()
    real_replace = review_store_module.os.replace
    calls = 0

    def fail_during_commit(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated commit failure")
        return real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(review_store_module.os, "replace", fail_during_commit)
    with pytest.raises(OSError, match="simulated"):
        store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\nA --> C\n",
            expected_version=loaded.state.version,
            expected_digest=loaded.state.code_digest,
        )

    assert (bundle_path / "final.mmd").read_bytes() == original_code
    assert (bundle_path / "review-history.json").read_bytes() == original_history
    assert not (bundle_path / "review-state.json").exists()
    assert not list((bundle_path / "versions").iterdir())


def test_staging_write_failure_removes_unpublished_temporary_file(monkeypatch, tmp_path):
    bundle = make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    current = store.load_bundle("diagram-a")
    real_fsync = review_store_module.os.fsync
    failed = False

    def fail_first_staging_fsync(descriptor):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("simulated staging fsync failure")
        return real_fsync(descriptor)

    monkeypatch.setattr(review_store_module.os, "fsync", fail_first_staging_fsync)

    with pytest.raises(OSError, match="staging fsync"):
        store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\n  B --> A\n",
            expected_version=current.state.version,
            expected_digest=current.state.code_digest,
        )

    assert failed
    assert not [path for path in bundle.rglob("*.tmp") if path.is_file()]
    assert not (bundle / "review-state.json").exists()


def test_transaction_rejects_oversized_existing_target_without_partial_write(tmp_path):
    bundle = make_bundle(tmp_path)
    versions = bundle / "versions"
    versions.mkdir()
    oversized = versions / "r000001.json"
    oversized.write_bytes(b"x" * (MAX_JSON_BYTES + 1))
    store = ReviewStore(tmp_path)
    current = store.load_bundle("diagram-a")
    original_code = (bundle / "final.mmd").read_bytes()

    with pytest.raises(ReviewValidationError, match="transaction limit"):
        store.apply_mermaid_edit(
            "diagram-a",
            "flowchart LR\n  B --> A\n",
            expected_version=current.state.version,
            expected_digest=current.state.code_digest,
        )

    assert (bundle / "final.mmd").read_bytes() == original_code
    assert oversized.stat().st_size == MAX_JSON_BYTES + 1
    assert not (bundle / "review-state.json").exists()


def test_stale_version_and_digest_are_rejected(tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    edited = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> C\n",
        expected_version=0,
        expected_digest=initial.state.code_digest,
    )

    with pytest.raises(ReviewConflictError):
        store.approve(
            "diagram-a",
            expected_version=0,
            expected_digest=initial.state.code_digest,
        )
    with pytest.raises(ReviewConflictError):
        store.approve(
            "diagram-a",
            expected_version=edited.state.version,
            expected_digest="0" * 64,
        )


def test_external_mermaid_change_after_state_creation_is_a_conflict(tmp_path):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path, validator=_valid_render)
    initial = store.load_bundle("diagram-a")
    store.approve(
        "diagram-a",
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    (bundle_path / "final.mmd").write_text("flowchart LR\nX --> Y\n", encoding="utf-8")

    with pytest.raises(ReviewConflictError, match="outside"):
        store.load_bundle("diagram-a")


def test_external_render_change_after_state_creation_is_a_conflict(tmp_path):
    bundle_path = make_bundle(tmp_path)
    (bundle_path / "final.svg").write_text("<svg><title>initial</title></svg>")
    store = ReviewStore(tmp_path, validator=_valid_render)
    initial = store.load_bundle("diagram-a")
    store.approve(
        "diagram-a",
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    (bundle_path / "final.svg").write_text("<svg><title>changed</title></svg>")

    with pytest.raises(ReviewConflictError, match="render"):
        store.load_bundle("diagram-a")


def test_edit_approve_and_reject_append_compatible_history(tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path, validator=_valid_render)
    state = store.load_bundle("diagram-a")
    state = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> C\n",
        expected_version=state.state.version,
        expected_digest=state.state.code_digest,
        reason="correct endpoint",
    )
    state = store.approve(
        "diagram-a",
        expected_version=state.state.version,
        expected_digest=state.state.code_digest,
        reason="verified against source",
    )
    state = store.reject(
        "diagram-a",
        expected_version=state.state.version,
        expected_digest=state.state.code_digest,
        reason="second reviewer disagreed",
    )

    assert state.state.version == 3
    assert state.state.decision == "rejected"
    assert [entry.operation for entry in state.history] == [
        "edit_mermaid",
        "approve",
        "reject",
    ]
    assert all(entry.source == "user" for entry in state.history)
    assert (tmp_path / "diagrams/diagram-a/versions/r000000.mmd").exists()
    assert (tmp_path / "diagrams/diagram-a/versions/r000003.json").exists()


def test_reject_requires_a_reason_without_writing(tmp_path):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    state = store.load_bundle("diagram-a")

    with pytest.raises(ReviewValidationError):
        store.reject(
            "diagram-a",
            expected_version=state.state.version,
            expected_digest=state.state.code_digest,
            reason="  ",
        )

    assert json.loads((bundle_path / "review-history.json").read_text()) == []


def test_undo_and_redo_restore_code_and_decision_without_deleting_history(tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path, validator=_valid_render)
    initial = store.load_bundle("diagram-a")
    edited = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> C\n",
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    approved = store.approve(
        "diagram-a",
        expected_version=edited.state.version,
        expected_digest=edited.state.code_digest,
    )

    undone = store.undo(
        "diagram-a",
        expected_version=approved.state.version,
        expected_digest=approved.state.code_digest,
    )
    assert undone.mermaid_code == edited.mermaid_code
    assert undone.state.decision == "pending"

    redone = store.redo(
        "diagram-a",
        expected_version=undone.state.version,
        expected_digest=undone.state.code_digest,
    )
    assert redone.state.decision == "approved"
    assert [entry.operation for entry in redone.history] == [
        "edit_mermaid",
        "approve",
        "undo",
        "redo",
    ]


def test_checkout_revision_jumps_within_active_timeline_and_is_audited(tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path, validator=_valid_render)
    baseline = store.load_bundle("diagram-a")
    edited = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> C\n",
        expected_version=baseline.state.version,
        expected_digest=baseline.state.code_digest,
    )
    approved = store.approve(
        "diagram-a",
        expected_version=edited.state.version,
        expected_digest=edited.state.code_digest,
    )

    restored = store.checkout_revision(
        "diagram-a",
        "r000000",
        expected_version=approved.state.version,
        expected_digest=approved.state.code_digest,
        reason="compare with baseline",
    )

    assert restored.state.version == 3
    assert restored.state.current_revision == "r000000"
    assert restored.state.cursor == 0
    assert restored.state.timeline == ["r000000", "r000001", "r000002"]
    assert restored.mermaid_code == baseline.mermaid_code
    assert restored.state.decision == "pending"
    assert restored.history[-1].operation == "checkout_revision"
    assert restored.history[-1].target == "r000000"
    assert restored.history[-1].reason == "compare with baseline"

    forward = store.checkout_revision(
        "diagram-a",
        "r000002",
        expected_version=restored.state.version,
        expected_digest=restored.state.code_digest,
    )
    assert forward.state.version == 4
    assert forward.state.cursor == 2
    assert forward.state.decision == "approved"

    with pytest.raises(ReviewConflictError, match="already current"):
        store.checkout_revision(
            "diagram-a",
            "r000002",
            expected_version=forward.state.version,
            expected_digest=forward.state.code_digest,
        )
    with pytest.raises(ReviewValidationError, match="active timeline"):
        store.checkout_revision(
            "diagram-a",
            "r999999",
            expected_version=forward.state.version,
            expected_digest=forward.state.code_digest,
        )
    with pytest.raises(ReviewValidationError, match="invalid ID"):
        store.checkout_revision(
            "diagram-a",
            "../../r000000",
            expected_version=forward.state.version,
            expected_digest=forward.state.code_digest,
        )
    with pytest.raises(ReviewConflictError, match="stale review state"):
        store.checkout_revision(
            "diagram-a",
            "../../r000000",
            expected_version=restored.state.version,
            expected_digest=restored.state.code_digest,
        )

    branch_point = store.checkout_revision(
        "diagram-a",
        "r000001",
        expected_version=forward.state.version,
        expected_digest=forward.state.code_digest,
    )
    branched = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> D\n",
        expected_version=branch_point.state.version,
        expected_digest=branch_point.state.code_digest,
    )
    assert branched.state.timeline == ["r000000", "r000001", "r000006"]
    assert (tmp_path / "diagrams/diagram-a/versions/r000002.json").exists()


def test_new_edit_after_undo_retains_old_snapshots_and_audit_history(tmp_path):
    make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    state = store.load_bundle("diagram-a")
    state = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\nA --> C\n",
        expected_version=state.state.version,
        expected_digest=state.state.code_digest,
    )
    state = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\nA --> D\n",
        expected_version=state.state.version,
        expected_digest=state.state.code_digest,
    )
    state = store.undo(
        "diagram-a",
        expected_version=state.state.version,
        expected_digest=state.state.code_digest,
    )
    state = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\nA --> E\n",
        expected_version=state.state.version,
        expected_digest=state.state.code_digest,
    )

    versions = tmp_path / "diagrams/diagram-a/versions"
    assert (versions / "r000002.mmd").read_text(encoding="utf-8").endswith("A --> D\n")
    assert state.state.timeline == ["r000000", "r000001", "r000004"]
    assert [entry.operation for entry in state.history] == [
        "edit_mermaid",
        "edit_mermaid",
        "undo",
        "edit_mermaid",
    ]
    with pytest.raises(ReviewConflictError):
        store.redo(
            "diagram-a",
            expected_version=state.state.version,
            expected_digest=state.state.code_digest,
        )


def test_invalid_json_is_rejected(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "review-history.json").write_text('{"not": "a list"}', encoding="utf-8")

    with pytest.raises(ReviewValidationError):
        ReviewStore(tmp_path).load_bundle("diagram-a")


def test_ir_and_render_artifacts_are_revisioned_and_restored_by_undo(tmp_path):
    bundle_path = make_bundle(tmp_path)
    original_ir = {
        "elements": [{"id": "A", "role": "node", "bbox": [0, 0, 10, 10]}],
        "relations": [],
        "groups": [],
    }
    (bundle_path / "scene-ir.json").write_text(json.dumps(original_ir), encoding="utf-8")
    (bundle_path / "final.svg").write_text("<svg><title>old</title></svg>", encoding="utf-8")
    (bundle_path / "final.png").write_bytes(b"old-png")
    store = ReviewStore(
        tmp_path,
        validator=lambda code: ReviewValidationResult(
            valid=True,
            svg="<svg viewBox='0 0 1 1'><title>new</title></svg>",
            png=_png_bytes(),
        ),
    )
    initial = store.load_bundle("diagram-a")
    new_ir = {
        "elements": [{"id": "C", "role": "node", "bbox": [0, 0, 10, 10]}],
        "relations": [],
        "groups": [],
    }

    edited = store.apply_edit(
        "diagram-a",
        "flowchart LR\nC --> D\n",
        scene_ir=new_ir,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )

    assert edited.scene_ir == new_ir
    assert edited.svg == "<svg viewBox='0 0 1 1'><title>new</title></svg>"
    assert edited.png == _png_bytes()
    assert edited.manifest["review_quality_status"] == "unscored_user_revision"
    assert (bundle_path / "versions/r000000.scene-ir.json").exists()
    assert (bundle_path / "versions/r000001.svg").exists()

    restored = store.undo(
        "diagram-a",
        expected_version=edited.state.version,
        expected_digest=edited.state.code_digest,
    )

    assert restored.scene_ir == original_ir
    assert restored.svg == "<svg><title>old</title></svg>"
    assert restored.png == b"old-png"
    assert restored.manifest["review_quality_status"] == "automated_baseline"
    hashes = json.loads((bundle_path / "manifest.json").read_text())["files"]
    assert set(hashes) >= {"final.mmd", "scene-ir.json", "final.svg", "final.png"}


def test_provenance_load_enforces_aggregate_reference_boundary_without_writing(
    monkeypatch,
    tmp_path,
):
    bundle_path = make_bundle(tmp_path)
    monkeypatch.setattr(models, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 2)
    write_provenance(
        bundle_path,
        [evidence_with_blocks(["shared", "shared"])],
    )
    store = ReviewStore(tmp_path)

    loaded = store.load_bundle("diagram-a")

    assert loaded.provenance is not None
    assert loaded.provenance[0].source_block_ids == ["shared", "shared"]

    write_provenance(
        bundle_path,
        [evidence_with_blocks(["shared", "shared", "shared"])],
    )
    before = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }

    with pytest.raises(ReviewValidationError, match="source-block references"):
        store.load_bundle("diagram-a")

    after = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    assert after == before
    assert not (bundle_path / "review-state.json").exists()
    assert not (bundle_path / "versions").exists()


def test_provenance_load_digests_and_parses_one_bounded_byte_snapshot(
    monkeypatch,
    tmp_path,
):
    bundle_path = make_bundle(tmp_path)
    write_provenance(bundle_path, [evidence("original", "Original")])
    provenance_path = bundle_path / "provenance.json"
    replacement_path = bundle_path / "replacement.json"
    replacement_path.write_text(
        json.dumps([evidence("replacement", "Replacement")]),
        encoding="utf-8",
    )
    original_open = Path.open
    swapped = False

    class SwappingReader:
        def __init__(self, artifact):
            self.artifact = artifact

        def __enter__(self):
            self.artifact.__enter__()
            return self

        def __exit__(self, *args):
            return self.artifact.__exit__(*args)

        def read(self, *args, **kwargs):
            nonlocal swapped
            payload = self.artifact.read(*args, **kwargs)
            os.replace(replacement_path, provenance_path)
            swapped = True
            return payload

    def swapping_open(self, mode="r", *args, **kwargs):
        artifact = original_open(self, mode, *args, **kwargs)
        if self == provenance_path and mode == "rb" and not swapped:
            return SwappingReader(artifact)
        return artifact

    monkeypatch.setattr(Path, "open", swapping_open)

    loaded = ReviewStore(tmp_path).load_bundle("diagram-a")

    assert swapped
    assert loaded.provenance is not None
    assert [item.id for item in loaded.provenance] == ["original"]


def test_provenance_load_counts_source_block_python_characters(monkeypatch, tmp_path):
    bundle_path = make_bundle(tmp_path)
    monkeypatch.setattr(models, "MAX_EVIDENCE_SOURCE_BLOCK_CHARS", 2)
    write_provenance(bundle_path, [evidence_with_blocks(["가나"])])
    store = ReviewStore(tmp_path)

    loaded = store.load_bundle("diagram-a")

    assert loaded.provenance is not None
    assert loaded.provenance[0].source_block_ids == ["가나"]

    write_provenance(bundle_path, [evidence_with_blocks(["가나다"])])
    with pytest.raises(ReviewValidationError, match="source-block characters"):
        store.load_bundle("diagram-a")


def test_provenance_replacement_exact_boundary_and_plus_one_are_atomic(
    monkeypatch,
    tmp_path,
):
    bundle_path = make_bundle(tmp_path)
    write_provenance(bundle_path, [evidence_with_blocks(["source-a"])])
    monkeypatch.setattr(models, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 2)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")

    exact = store.apply_edit(
        "diagram-a",
        initial.mermaid_code,
        scene_ir=initial.scene_ir,
        provenance=[evidence_with_blocks(["same", "same"], "exact")],
        replace_provenance=True,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )

    assert exact.state.schema_version == "mmx-review-0.4.1"
    assert exact.provenance is not None
    assert exact.provenance[0].source_block_ids == ["same", "same"]
    before = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    validation_calls: list[str] = []

    def forbidden_validator(code):
        validation_calls.append(code)
        raise AssertionError("over-budget provenance must fail before render validation")

    replacement = [evidence_with_blocks(["same", "same", "same"], "overflow")]
    with pytest.raises(ReviewValidationError, match="source-block references"):
        store.apply_edit(
            "diagram-a",
            exact.mermaid_code,
            scene_ir=exact.scene_ir,
            provenance=replacement,
            replace_provenance=True,
            expected_version=exact.state.version,
            expected_digest=exact.state.code_digest,
            validator=forbidden_validator,
        )

    after = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    assert validation_calls == []
    assert replacement[0]["source_block_ids"] == ["same", "same", "same"]
    assert after == before


def test_provenance_digest_snapshot_enforces_budget_without_running_list_hooks(
    monkeypatch,
):
    monkeypatch.setattr(models, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 2)
    source = VisualEvidence.model_validate(evidence_with_blocks(["same", "same"], "exact"))

    payload = review_store_module._provenance_bytes([source])

    assert json.loads(payload)[0]["source_block_ids"] == ["same", "same"]
    source.source_block_ids.append("same")
    with pytest.raises(ReviewValidationError, match="source-block references"):
        review_store_module._provenance_bytes([source])

    hook_calls: list[str] = []

    class HookedList(list):
        def __iter__(self):
            hook_calls.append("iter")
            return super().__iter__()

    with pytest.raises(ReviewValidationError, match="exact plain list"):
        review_store_module._provenance_bytes(HookedList([source]))
    assert hook_calls == []


@pytest.mark.parametrize("overflow", ["current", "target"])
def test_commit_revalidates_provenance_before_path_or_serialization(
    monkeypatch,
    tmp_path,
    overflow,
):
    bundle_path = make_bundle(tmp_path)
    write_provenance(bundle_path, [evidence_with_blocks(["source-a"])])
    monkeypatch.setattr(models, "MAX_EVIDENCE_SOURCE_BLOCK_REFS", 2)
    store = ReviewStore(tmp_path)
    bundle = store.load_bundle("diagram-a")
    assert bundle.provenance is not None
    target = [VisualEvidence.model_validate(evidence_with_blocks(["target"]))]
    if overflow == "current":
        bundle.provenance[0].source_block_ids[:] = ["same", "same", "same"]
    else:
        target[0].source_block_ids[:] = ["same", "same", "same"]
    path_calls: list[str] = []

    def forbidden_bundle_path(bundle_id):
        path_calls.append(bundle_id)
        raise AssertionError("provenance preflight must run before bundle path access")

    monkeypatch.setattr(store, "_bundle_path", forbidden_bundle_path)
    with pytest.raises(ReviewValidationError, match="source-block references"):
        store._commit_new_revision(
            bundle,
            code=bundle.mermaid_code,
            scene_ir=bundle.scene_ir,
            provenance=target,
            layout_hints=bundle.layout_hints,
            svg=bundle.svg,
            png=bundle.png,
            decision="pending",
            decision_reason=None,
            selected_candidate_id=bundle.state.selected_candidate_id,
            operation="test_provenance_preflight",
            reason=None,
            before={},
        )

    assert path_calls == []


def test_provenance_is_digest_checked_revisioned_and_restored_by_undo_redo(tmp_path):
    bundle_path = make_bundle(tmp_path)
    original = [evidence()]
    replacement = [
        {
            "id": "user-edit-1",
            "kind": "user_edit",
            "bbox": [20, 0, 30, 10],
            "text": "Confirmed",
            "score": 1.0,
            "source_block_ids": ["block-1"],
        }
    ]
    write_provenance(bundle_path, original)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")

    edited = store.apply_edit(
        "diagram-a",
        initial.mermaid_code,
        scene_ir=initial.scene_ir,
        provenance=replacement,
        replace_provenance=True,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )

    assert edited.state.schema_version == "mmx-review-0.4.1"
    assert [item.id for item in edited.provenance] == ["user-edit-1"]
    assert edited.state.provenance_digest
    assert (bundle_path / "versions/provenance" / f"{edited.state.provenance_digest}.json").exists()
    baseline = json.loads((bundle_path / "versions/r000000.json").read_text())
    assert baseline["provenance_digest"]
    assert json.loads((bundle_path / "provenance.json").read_text())[0]["id"] == "user-edit-1"
    manifest = json.loads((bundle_path / "manifest.json").read_text())
    assert manifest["files"]["provenance.json"] == edited.state.provenance_digest

    undone = store.undo(
        "diagram-a",
        expected_version=edited.state.version,
        expected_digest=edited.state.code_digest,
    )
    assert [item.id for item in undone.provenance] == ["ocr-1"]
    assert json.loads((bundle_path / "provenance.json").read_text())[0]["id"] == "ocr-1"

    redone = store.redo(
        "diagram-a",
        expected_version=undone.state.version,
        expected_digest=undone.state.code_digest,
    )
    assert [item.id for item in redone.provenance] == ["user-edit-1"]


def test_provenance_absence_and_creation_are_restored_exactly(tmp_path):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    created = store.apply_edit(
        "diagram-a",
        initial.mermaid_code,
        scene_ir=initial.scene_ir,
        provenance=[evidence()],
        replace_provenance=True,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    assert (bundle_path / "provenance.json").exists()

    undone = store.undo(
        "diagram-a",
        expected_version=created.state.version,
        expected_digest=created.state.code_digest,
    )
    assert undone.provenance is None
    assert not (bundle_path / "provenance.json").exists()
    assert "provenance.json" not in undone.manifest["files"]


def test_legacy_review_state_lazily_migrates_static_provenance_on_undo(tmp_path):
    bundle_path = make_bundle(tmp_path)
    write_provenance(bundle_path, [evidence()])
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> C\n",
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    state_path = bundle_path / "review-state.json"
    state = json.loads(state_path.read_text())
    state["schema_version"] = "mmx-review-0.3"
    state.pop("provenance_digest", None)
    state.pop("legacy_provenance_digest", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for snapshot_path in (bundle_path / "versions").glob("r*.json"):
        snapshot = json.loads(snapshot_path.read_text())
        snapshot["schema_version"] = "mmx-review-0.3"
        snapshot.pop("provenance_digest", None)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    for blob in (bundle_path / "versions/provenance").glob("*.json"):
        blob.unlink()

    legacy = store.load_bundle("diagram-a")
    migrated = store.undo(
        "diagram-a",
        expected_version=legacy.state.version,
        expected_digest=legacy.state.code_digest,
    )

    assert migrated.state.schema_version == "mmx-review-0.4.1"
    assert migrated.state.legacy_provenance_digest
    assert [item.id for item in migrated.provenance] == ["ocr-1"]
    assert (
        bundle_path / "versions/provenance" / f"{migrated.state.legacy_provenance_digest}.json"
    ).exists()

    replaced = store.apply_edit(
        "diagram-a",
        migrated.mermaid_code,
        scene_ir=migrated.scene_ir,
        provenance=[evidence("user-2", "Replacement")],
        replace_provenance=True,
        expected_version=migrated.state.version,
        expected_digest=migrated.state.code_digest,
    )
    restored = store.undo(
        "diagram-a",
        expected_version=replaced.state.version,
        expected_digest=replaced.state.code_digest,
    )
    assert [item.id for item in restored.provenance] == ["ocr-1"]

    removed = store.apply_edit(
        "diagram-a",
        restored.mermaid_code,
        scene_ir=restored.scene_ir,
        provenance=None,
        replace_provenance=True,
        expected_version=restored.state.version,
        expected_digest=restored.state.code_digest,
    )
    assert removed.provenance is None
    after_removal = store.apply_edit(
        "diagram-a",
        removed.mermaid_code,
        scene_ir=removed.scene_ir,
        provenance=[evidence("user-3", "After removal")],
        replace_provenance=True,
        expected_version=removed.state.version,
        expected_digest=removed.state.code_digest,
    )
    restored_removal = store.undo(
        "diagram-a",
        expected_version=after_removal.state.version,
        expected_digest=after_removal.state.code_digest,
    )
    assert restored_removal.provenance is None
    assert not (bundle_path / "provenance.json").exists()


def test_provenance_only_0_4_state_migrates_without_layout(tmp_path):
    bundle_path = make_bundle(tmp_path)
    write_scene(bundle_path)
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    edited = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> C\n",
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )
    state_path = bundle_path / "review-state.json"
    state = json.loads(state_path.read_text())
    state["schema_version"] = "mmx-review-0.4"
    state.pop("layout_digest", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    for snapshot_path in (bundle_path / "versions").glob("r*.json"):
        snapshot = json.loads(snapshot_path.read_text())
        snapshot["schema_version"] = "mmx-review-0.4"
        snapshot.pop("layout_digest", None)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    old_state = store.load_bundle("diagram-a")
    assert old_state.state.schema_version == "mmx-review-0.4"
    migrated = store.apply_mermaid_edit(
        "diagram-a",
        "flowchart LR\n  A --> D\n",
        expected_version=edited.state.version,
        expected_digest=edited.state.code_digest,
    )
    assert migrated.state.schema_version == "mmx-review-0.4.1"
    assert migrated.layout_hints is None


def test_provenance_tamper_and_invalid_replacement_fail_without_writing(tmp_path):
    bundle_path = make_bundle(tmp_path)
    write_provenance(bundle_path, [evidence()])
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    before = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    with pytest.raises(ReviewValidationError, match="explicit replace_provenance"):
        store.apply_edit(
            "diagram-a",
            initial.mermaid_code,
            scene_ir=initial.scene_ir,
            provenance=[evidence("other")],
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )
    with pytest.raises(ReviewValidationError, match="unique"):
        store.apply_edit(
            "diagram-a",
            initial.mermaid_code,
            scene_ir=initial.scene_ir,
            provenance=[evidence(), evidence()],
            replace_provenance=True,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )
    dangling_scene = {
        "elements": [
            {
                "id": "A",
                "role": "node",
                "bbox": [0, 0, 10, 10],
                "evidence_ids": ["missing-evidence"],
            }
        ],
        "relations": [],
        "groups": [],
    }
    with pytest.raises(ReviewValidationError, match="absent from provenance"):
        store.apply_edit(
            "diagram-a",
            initial.mermaid_code,
            scene_ir=dangling_scene,
            provenance=[evidence("other")],
            replace_provenance=True,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )
    after = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    assert after == before

    (bundle_path / "provenance.json").write_text(json.dumps([evidence(text="Tampered")]))
    with pytest.raises(ReviewConflictError, match="manifest digest"):
        store.load_bundle("diagram-a")


def test_provenance_commit_io_failure_restores_every_bundle_file(monkeypatch, tmp_path):
    bundle_path = make_bundle(tmp_path)
    write_provenance(bundle_path, [evidence()])
    store = ReviewStore(tmp_path)
    initial = store.load_bundle("diagram-a")
    before = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    real_replace = review_store_module.os.replace

    def fail_at_root_provenance(source, target, *, src_dir_fd=None, dst_dir_fd=None):
        if os.fspath(target) == "provenance.json":
            raise OSError("simulated provenance commit failure")
        return real_replace(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(review_store_module.os, "replace", fail_at_root_provenance)
    with pytest.raises(OSError, match="provenance commit"):
        store.apply_edit(
            "diagram-a",
            initial.mermaid_code,
            scene_ir=initial.scene_ir,
            provenance=[evidence("user-1", "Edited")],
            replace_provenance=True,
            expected_version=initial.state.version,
            expected_digest=initial.state.code_digest,
        )

    after = {
        path.relative_to(bundle_path): path.read_bytes()
        for path in bundle_path.rglob("*")
        if path.is_file() and path.name != ".review.lock"
    }
    assert after == before
