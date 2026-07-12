from __future__ import annotations

import hashlib
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


def evidence(evidence_id="ocr-1", text="Original"):
    return {
        "id": evidence_id,
        "kind": "ocr_token",
        "bbox": [0, 0, 10, 10],
        "text": text,
        "score": 0.9,
        "source_block_ids": ["block-1"],
    }


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

    scene_without_a = write_scene(bundle_path, ("B",))
    # Restore the managed Scene file before applying the explicit editor transaction.
    (bundle_path / "scene-ir.json").write_text(json.dumps(redone.scene_ir), encoding="utf-8")
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
            png=b"approved-png",
        ),
    )
    approved = store.approve(
        "diagram-a",
        expected_version=current.state.version,
        expected_digest=current.state.code_digest,
    )

    assert approved.state.decision == "approved"
    assert approved.svg == "<svg viewBox='0 0 2 2'/>"
    assert approved.png == b"approved-png"
    assert (bundle_path / "final.svg").read_text() == approved.svg


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
    store = ReviewStore(tmp_path, validator=lambda code: True)
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
    store = ReviewStore(tmp_path, validator=lambda code: True)
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
    store = ReviewStore(tmp_path, validator=lambda code: True)
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
    store = ReviewStore(tmp_path, validator=lambda code: True)
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
            svg="<svg><title>new</title></svg>",
            png=b"new-png",
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
    assert edited.svg == "<svg><title>new</title></svg>"
    assert edited.png == b"new-png"
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

    def fail_at_root_provenance(source, target):
        if os.fspath(target).endswith("/provenance.json") and "/versions/" not in os.fspath(target):
            raise OSError("simulated provenance commit failure")
        return real_replace(source, target)

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
