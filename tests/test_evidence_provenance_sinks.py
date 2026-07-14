from __future__ import annotations

import json

import pytest
from PIL import Image

import marker_mermaid.models as model_module
import marker_mermaid.sidecars as sidecar_module
from marker_mermaid.models import MermaidCandidate, ReconstructionResult, VisualEvidence
from marker_mermaid.output import save_document_output
from marker_mermaid.sidecars import SidecarStore


def _mutated_result(source_id: str, axis: str) -> ReconstructionResult:
    block_ids = (("a", "b"), ("c", "d")) if axis == "references" else (("가나",), ("다라",))
    evidence = [
        VisualEvidence(
            id=f"evidence-{index}",
            kind="contour",
            source_block_ids=list(ids),
        )
        for index, ids in enumerate(block_ids, start=1)
    ]
    result = ReconstructionResult(
        source_id=source_id,
        source_image_name="source.png",
        status="failed",
    )
    result.evidence = evidence
    return result


def _set_evidence_limits(monkeypatch, axis: str, limit: int) -> None:
    monkeypatch.setattr(
        model_module,
        "MAX_EVIDENCE_SOURCE_BLOCK_REFS",
        limit if axis == "references" else 4,
    )
    monkeypatch.setattr(
        model_module,
        "MAX_EVIDENCE_SOURCE_BLOCK_CHARS",
        limit if axis == "characters" else 4,
    )
    monkeypatch.setattr(model_module, "MAX_EVIDENCE_INPUT_CHARS", 10_000)


@pytest.mark.parametrize("axis", ["references", "characters"])
def test_sidecar_accepts_exact_aggregate_evidence_provenance_budget(
    tmp_path,
    monkeypatch,
    axis: str,
) -> None:
    _set_evidence_limits(monkeypatch, axis, 4)
    result = _mutated_result(f"sidecar-exact-{axis}", axis)
    live_evidence = result.evidence
    live_source_ids = [item.source_block_ids for item in live_evidence]
    expected_source_ids = [ids[:] for ids in live_source_ids]

    relative = SidecarStore(tmp_path).write(result)

    provenance = json.loads((tmp_path / relative / "provenance.json").read_text())
    assert [item["source_block_ids"] for item in provenance] == expected_source_ids
    assert result.evidence is live_evidence
    assert all(
        item.source_block_ids is source_ids
        for item, source_ids in zip(result.evidence, live_source_ids, strict=True)
    )
    assert [item.source_block_ids for item in result.evidence] == expected_source_ids


@pytest.mark.parametrize(
    ("axis", "message"),
    [
        ("references", "source-block references exceed the aggregate limit"),
        ("characters", "source-block characters exceed the aggregate limit"),
    ],
)
def test_sidecar_rejects_aggregate_evidence_provenance_overflow_before_sink_work(
    tmp_path,
    monkeypatch,
    axis: str,
    message: str,
) -> None:
    _set_evidence_limits(monkeypatch, axis, 3)
    result = _mutated_result(f"sidecar-overflow-{axis}", axis)
    result.selected = MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type="flowchart",
    )
    json_calls = 0
    candidate_json_calls = 0
    dump_calls = 0
    copy_calls = 0

    def forbidden_json(*_args, **_kwargs):
        nonlocal json_calls
        json_calls += 1
        raise AssertionError("sidecar JSON serialization must follow evidence preflight")

    def forbidden_dump(*_args, **_kwargs):
        nonlocal dump_calls
        dump_calls += 1
        raise AssertionError("evidence model_dump must follow evidence preflight")

    def forbidden_candidate_json(*_args, **_kwargs):
        nonlocal candidate_json_calls
        candidate_json_calls += 1
        raise AssertionError("candidate JSON must follow evidence preflight")

    def forbidden_copy(*_args, **_kwargs):
        nonlocal copy_calls
        copy_calls += 1
        raise AssertionError("result copying must follow evidence preflight")

    monkeypatch.setattr(sidecar_module, "_json_bytes", forbidden_json)
    monkeypatch.setattr(sidecar_module, "_candidate_json", forbidden_candidate_json)
    monkeypatch.setattr(VisualEvidence, "model_dump", forbidden_dump)
    monkeypatch.setattr(ReconstructionResult, "model_copy", forbidden_copy)

    with pytest.raises(ValueError, match=message):
        SidecarStore(tmp_path).write(result)

    assert (json_calls, candidate_json_calls, dump_calls, copy_calls) == (0, 0, 0, 0)
    assert not (tmp_path / "diagrams").exists()


def test_sidecar_rejects_live_evidence_change_during_detached_result_copy(
    tmp_path,
    monkeypatch,
) -> None:
    _set_evidence_limits(monkeypatch, "references", 4)
    result = _mutated_result("sidecar-evidence-race", "references")
    original_model_copy = ReconstructionResult.model_copy

    def mutating_model_copy(self, *, update=None, deep=False):
        copied = original_model_copy(self, update=update, deep=deep)
        if deep:
            result.evidence[0].source_block_ids[0] = "changed"
        return copied

    monkeypatch.setattr(ReconstructionResult, "model_copy", mutating_model_copy)

    with pytest.raises(ValueError, match="changed while its snapshot was captured"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams").exists()


@pytest.mark.parametrize("axis", ["references", "characters"])
def test_document_output_accepts_exact_aggregate_evidence_provenance_budget(
    tmp_path,
    monkeypatch,
    axis: str,
) -> None:
    _set_evidence_limits(monkeypatch, axis, 4)
    result = _mutated_result(f"output-exact-{axis}", axis)
    live_evidence = result.evidence
    live_source_ids = [item.source_block_ids for item in live_evidence]
    expected_source_ids = [ids[:] for ids in live_source_ids]
    root = tmp_path / f"output-{axis}"

    document = save_document_output(
        output_dir=root,
        filename="document",
        markdown="",
        images={"source.png": Image.new("RGB", (2, 2), "white")},
        metadata={"mermaid": [{"source_id": result.source_id}]},
        reconstructions=[result],
    )

    assert document.is_file()
    assert (root / "images" / "source.png").is_file()
    assert (root / "diagrams" / f"output-exact-{axis}" / "provenance.json").is_file()
    assert result.evidence is live_evidence
    assert all(
        item.source_block_ids is source_ids
        for item, source_ids in zip(result.evidence, live_source_ids, strict=True)
    )
    assert [item.source_block_ids for item in result.evidence] == expected_source_ids


def test_document_output_reuses_preflight_evidence_snapshot_after_image_write_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    _set_evidence_limits(monkeypatch, "references", 4)
    result = _mutated_result("output-evidence-snapshot", "references")
    expected_source_ids = [item.source_block_ids[:] for item in result.evidence]
    original_save = Image.Image.save
    mutated = False

    def mutating_save(self, fp, *args, **kwargs):
        nonlocal mutated
        if not mutated:
            mutated = True
            result.evidence[0].source_block_ids.append("overflow")
        return original_save(self, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", mutating_save)
    root = tmp_path / "output-snapshot"

    save_document_output(
        output_dir=root,
        filename="document",
        markdown="",
        images={"source.png": Image.new("RGB", (2, 2), "white")},
        metadata={"mermaid": [{"source_id": result.source_id}]},
        reconstructions=[result],
    )

    provenance = json.loads(
        (root / "diagrams" / "output-evidence-snapshot" / "provenance.json").read_text()
    )
    assert [item["source_block_ids"] for item in provenance] == expected_source_ids
    assert len(result.evidence[0].source_block_ids) == 3
    assert result.sidecar_dir == "diagrams/output-evidence-snapshot"


def test_document_output_reuses_preflight_result_pairs_after_input_list_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    first = _mutated_result("output-first", "references")
    second = _mutated_result("output-second", "references")
    reconstructions = [first, second]
    original_save = Image.Image.save

    def mutating_save(self, fp, *args, **kwargs):
        reconstructions.clear()
        return original_save(self, fp, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "save", mutating_save)
    root = tmp_path / "output-pairs"

    save_document_output(
        output_dir=root,
        filename="document",
        markdown="",
        images={"source.png": Image.new("RGB", (2, 2), "white")},
        metadata={
            "mermaid": [
                {"source_id": first.source_id},
                {"source_id": second.source_id},
            ]
        },
        reconstructions=reconstructions,
    )

    assert (root / "diagrams" / "output-first" / "provenance.json").is_file()
    assert (root / "diagrams" / "output-second" / "provenance.json").is_file()
    assert first.sidecar_dir == "diagrams/output-first"
    assert second.sidecar_dir == "diagrams/output-second"


@pytest.mark.parametrize(
    ("axis", "message"),
    [
        ("references", "source-block references exceed the aggregate limit"),
        ("characters", "source-block characters exceed the aggregate limit"),
    ],
)
def test_document_output_rejects_aggregate_evidence_provenance_before_any_write(
    tmp_path,
    monkeypatch,
    axis: str,
    message: str,
) -> None:
    _set_evidence_limits(monkeypatch, axis, 3)
    result = _mutated_result(f"output-overflow-{axis}", axis)
    root = tmp_path / f"output-{axis}"

    def forbidden_write(*_args, **_kwargs):
        raise AssertionError("output writes must follow evidence preflight")

    monkeypatch.setattr(Image.Image, "save", forbidden_write)
    monkeypatch.setattr(SidecarStore, "write", forbidden_write)

    with pytest.raises(ValueError, match=message):
        save_document_output(
            output_dir=root,
            filename="document",
            markdown="",
            images={"source.png": Image.new("RGB", (2, 2), "white")},
            metadata={"mermaid": [{"source_id": result.source_id}]},
            reconstructions=[result],
        )

    assert not root.exists()
