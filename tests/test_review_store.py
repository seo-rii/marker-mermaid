from __future__ import annotations

import json
import os

import pytest

import marker_mermaid.review_store as review_store_module
from marker_mermaid.review_store import (
    ReviewConflictError,
    ReviewStore,
    ReviewValidationError,
    ReviewValidationResult,
    UnsafeReviewPathError,
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


def test_io_failure_restores_code_history_and_state(monkeypatch, tmp_path):
    bundle_path = make_bundle(tmp_path)
    store = ReviewStore(tmp_path)
    loaded = store.load_bundle("diagram-a")
    original_code = (bundle_path / "final.mmd").read_bytes()
    original_history = (bundle_path / "review-history.json").read_bytes()
    real_replace = review_store_module.os.replace
    calls = 0

    def fail_during_commit(source, target):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated commit failure")
        return real_replace(source, target)

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
    store = ReviewStore(tmp_path)
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
    store = ReviewStore(tmp_path)
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
    store = ReviewStore(tmp_path)
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
    store = ReviewStore(tmp_path)
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
    original_ir = {"elements": [{"id": "A"}], "relations": [], "groups": []}
    (bundle_path / "scene-ir.json").write_text(json.dumps(original_ir), encoding="utf-8")
    (bundle_path / "final.svg").write_text("<svg><title>old</title></svg>", encoding="utf-8")
    (bundle_path / "final.png").write_bytes(b"old-png")
    store = ReviewStore(
        tmp_path,
        validator=lambda code: ReviewValidationResult(
            valid=True,
            svg="<svg><title>new</title></svg>",
            png=b"new-png",
        ),
    )
    initial = store.load_bundle("diagram-a")
    new_ir = {"elements": [{"id": "C"}], "relations": [], "groups": []}

    edited = store.apply_edit(
        "diagram-a",
        "flowchart LR\nC --> D\n",
        scene_ir=new_ir,
        expected_version=initial.state.version,
        expected_digest=initial.state.code_digest,
    )

    assert edited.scene_ir == new_ir
    assert edited.svg == "<svg><title>new</title></svg>"
    assert edited.png == b"new-png"
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
    hashes = json.loads((bundle_path / "manifest.json").read_text())["files"]
    assert set(hashes) >= {"final.mmd", "scene-ir.json", "final.svg", "final.png"}
