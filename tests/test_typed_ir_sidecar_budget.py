from __future__ import annotations

import json

import pytest

import marker_mermaid.sidecars as sidecar_module
from marker_mermaid.models import (
    MAX_ID_CHARS,
    MAX_IR_TEXT_CHARS,
    MermaidCandidate,
    ReconstructionResult,
)
from marker_mermaid.sidecars import SidecarStore


def _typed_ir(label: str = "Start") -> dict[str, object]:
    return {
        "title": "Process",
        "nodes": [{"id": "A", "label": label}],
        "edges": [],
    }


def _candidate(candidate_id: str, label: str = "Start") -> MermaidCandidate:
    return MermaidCandidate(
        candidate_id=candidate_id,
        generation_method="typed_ir",
        diagram_type="flowchart",
        typed_ir=_typed_ir(label),
    )


def _result(
    source_id: str,
    *,
    selected: MermaidCandidate | None = None,
    alternatives: list[MermaidCandidate] | None = None,
) -> ReconstructionResult:
    return ReconstructionResult(
        source_id=source_id,
        source_image_name="source.png",
        selected=selected,
        alternatives=alternatives or [],
    )


def test_sidecar_rejects_post_construction_oversized_selected_typed_ir(tmp_path):
    selected = _candidate("selected")
    result = _result("oversized-selected", selected=selected)
    assert result.selected is not None
    result.selected.typed_ir = _typed_ir("x" * (MAX_IR_TEXT_CHARS + 1))

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams" / "oversized-selected").exists()


def test_sidecar_rejects_post_construction_oversized_alternative_typed_ir(tmp_path):
    alternative = _candidate("alternative")
    result = _result(
        "oversized-alternative",
        selected=_candidate("selected"),
        alternatives=[alternative],
    )
    result.alternatives[0].typed_ir = _typed_ir("x" * (MAX_IR_TEXT_CHARS + 1))

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path, write_alternatives=False).write(result)

    assert not (tmp_path / "diagrams" / "oversized-alternative").exists()


def test_sidecar_rejects_nested_typed_ir_container_without_running_hooks(tmp_path):
    calls = 0

    class HookedNode(dict):
        def __iter__(self):
            nonlocal calls
            calls += 1
            raise AssertionError("typed IR subclass iteration hook must not run")

        def __getitem__(self, key):
            nonlocal calls
            calls += 1
            raise AssertionError("typed IR subclass item hook must not run")

        def keys(self):
            nonlocal calls
            calls += 1
            raise AssertionError("typed IR subclass keys hook must not run")

        def values(self):
            nonlocal calls
            calls += 1
            raise AssertionError("typed IR subclass values hook must not run")

        def __deepcopy__(self, memo):
            nonlocal calls
            calls += 1
            raise AssertionError("typed IR subclass deepcopy hook must not run")

    result = _result("hooked-typed-ir", selected=_candidate("selected"))
    assert result.selected is not None
    result.selected.typed_ir = {
        "nodes": [HookedNode(id="A", label="Start")],
        "edges": [],
    }

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path).write(result)

    assert calls == 0
    assert not (tmp_path / "diagrams" / "hooked-typed-ir").exists()


def test_sidecar_revalidates_mutated_nested_typed_ir_contract(tmp_path):
    result = _result("invalid-contract", selected=_candidate("selected"))
    assert result.selected is not None
    result.selected.typed_ir = {
        "nodes": [{"id": ["not", "a", "string"], "label": "Start"}],
        "edges": [],
    }

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams" / "invalid-contract").exists()


def test_sidecar_rejects_non_plain_diagram_type_without_running_hooks(tmp_path):
    calls = 0

    class HookedDiagramType(str):
        def __str__(self):
            nonlocal calls
            calls += 1
            raise AssertionError("diagram type conversion hook must not run")

    result = _result("hooked-diagram-type", selected=_candidate("selected"))
    assert result.selected is not None
    result.selected.diagram_type = HookedDiagramType("flowchart")

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path).write(result)

    assert calls == 0
    assert not (tmp_path / "diagrams" / "hooked-diagram-type").exists()


def test_sidecar_rejects_hostile_candidate_keys_without_equality_hooks(tmp_path):
    calls: list[str] = []

    class HostileKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append(str(other))
            raise AssertionError("candidate key equality hook must not run")

    result = _result("hostile-candidate-key", selected=_candidate("selected"))
    assert result.selected is not None
    result.selected.__dict__.pop("diagram_type")
    result.selected.__dict__[HostileKey("diagram_type")] = "flowchart"

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path).write(result)

    assert calls == []
    assert not (tmp_path / "diagrams" / "hostile-candidate-key").exists()


@pytest.mark.parametrize("diagram_type", ["", "x" * (MAX_ID_CHARS + 1), "\ud800"])
def test_sidecar_rejects_invalid_diagram_type_before_typed_ir_scan(
    tmp_path,
    monkeypatch,
    diagram_type,
):
    result = _result("invalid-diagram-type", selected=_candidate("selected"))
    assert result.selected is not None
    result.selected.diagram_type = diagram_type
    monkeypatch.setattr(
        sidecar_module,
        "canonical_typed_ir_snapshot",
        lambda _value: pytest.fail("invalid diagram type must fail before typed IR scan"),
    )

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams" / "invalid-diagram-type").exists()


def test_sidecar_rejects_oversized_alternative_collection_before_copy(
    tmp_path,
    monkeypatch,
):
    result = _result(
        "too-many-alternatives",
        selected=_candidate("selected"),
        alternatives=[_candidate("alternative-a"), _candidate("alternative-b")],
    )
    monkeypatch.setattr(sidecar_module, "MAX_OBSERVATION_CANDIDATES", 1)

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams" / "too-many-alternatives").exists()


def test_sidecar_rejects_aggregate_typed_ir_budget_before_candidate_serialization(
    tmp_path,
    monkeypatch,
):
    result = _result(
        "aggregate-typed-ir",
        selected=_candidate("selected"),
        alternatives=[_candidate("alternative")],
    )
    one_ir_bytes = len(
        json.dumps(
            _typed_ir(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    monkeypatch.setattr(
        sidecar_module,
        "MAX_OBSERVATION_TYPED_IR_JSON_BYTES",
        one_ir_bytes,
    )
    monkeypatch.setattr(
        sidecar_module,
        "_candidate_json",
        lambda candidate: pytest.fail("candidate serialization must not run"),
    )

    with pytest.raises(ValueError, match="invalid typed IR"):
        SidecarStore(tmp_path, write_alternatives=False).write(result)

    assert not (tmp_path / "diagrams" / "aggregate-typed-ir").exists()


def test_sidecar_detects_live_typed_ir_race_during_result_copy(tmp_path):
    result = _result("typed-ir-race", selected=_candidate("selected"))
    assert result.selected is not None
    selected = result.selected
    hook_calls = 0

    class MutatingAst(dict):
        def __deepcopy__(self, memo):
            nonlocal hook_calls
            hook_calls += 1
            assert selected.typed_ir is not None
            selected.typed_ir["nodes"][0]["label"] = "Changed"
            return dict(self)

    selected.ast = MutatingAst(version=1)

    with pytest.raises(ValueError, match="changed while its snapshot was captured"):
        SidecarStore(tmp_path).write(result)

    assert hook_calls == 1
    assert not (tmp_path / "diagrams" / "typed-ir-race").exists()


def test_sidecar_preserves_valid_selected_and_alternative_typed_ir(tmp_path):
    selected = _candidate("selected", "Start")
    alternative = _candidate("alternative", "Finish")
    result = _result(
        "valid-typed-ir",
        selected=selected,
        alternatives=[alternative],
    )

    relative = SidecarStore(tmp_path).write(result)
    bundle = tmp_path / relative

    assert json.loads((bundle / "typed-ir.json").read_text()) == selected.typed_ir
    alternative_payload = json.loads((bundle / "alternatives" / "alternative.json").read_text())
    assert alternative_payload["typed_ir"] == alternative.typed_ir
