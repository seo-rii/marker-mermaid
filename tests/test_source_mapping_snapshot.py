from __future__ import annotations

import json
import math

import pytest

import marker_mermaid.models as models
import marker_mermaid.sidecars as sidecars
from marker_mermaid.models import ReconstructionResult, canonical_source_mapping_snapshot
from marker_mermaid.sidecars import SidecarStore


def test_source_mapping_snapshot_is_canonical_and_detached() -> None:
    source = {
        "z": (1, {"label": "Node"}),
        "a": [True, None, 1.5],
    }

    snapshot = canonical_source_mapping_snapshot(source)

    assert snapshot == {
        "a": [True, None, 1.5],
        "z": [1, {"label": "Node"}],
    }
    assert snapshot is not source
    assert list(snapshot) == ["a", "z"]
    assert snapshot["a"] is not source["a"]
    source["a"][0] = False
    source["z"][1]["label"] = "Changed"
    assert snapshot["a"][0] is True
    assert snapshot["z"][1]["label"] == "Node"


def test_source_mapping_snapshot_rejects_container_subclasses_without_hooks() -> None:
    calls: list[str] = []

    class HookedMapping(dict):
        def __iter__(self):
            calls.append("iter")
            return super().__iter__()

        def __deepcopy__(self, memo):
            calls.append("deepcopy")
            return dict(self)

    class HookedList(list):
        def __iter__(self):
            calls.append("list-iter")
            return super().__iter__()

        def __deepcopy__(self, memo):
            calls.append("list-deepcopy")
            return list(self)

    with pytest.raises(ValueError, match="exact plain dictionary"):
        canonical_source_mapping_snapshot(HookedMapping(source={"id": "source"}))
    with pytest.raises(ValueError, match="exact JSON-compatible"):
        canonical_source_mapping_snapshot({"values": HookedList([1, 2])})

    assert calls == []


def test_source_mapping_snapshot_rejects_custom_scalars_without_hooks() -> None:
    calls: list[str] = []

    class HookedValue:
        def __iter__(self):
            calls.append("iter")
            return iter(())

        def __deepcopy__(self, memo):
            calls.append("deepcopy")
            return self

    with pytest.raises(ValueError, match="exact JSON-compatible"):
        canonical_source_mapping_snapshot({"value": HookedValue()})

    assert calls == []


def test_source_mapping_snapshot_enforces_structural_limits(monkeypatch) -> None:
    monkeypatch.setattr(models, "MAX_SOURCE_MAPPING_DEPTH", 2)
    with pytest.raises(ValueError, match="nesting depth"):
        canonical_source_mapping_snapshot({"a": [[[1]]]})

    monkeypatch.setattr(models, "MAX_SOURCE_MAPPING_ITEMS", 4)
    with pytest.raises(ValueError, match="item budget"):
        canonical_source_mapping_snapshot({"a": [1, 2, 3]})

    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    with pytest.raises(ValueError, match="reference cycles"):
        canonical_source_mapping_snapshot(recursive)


def test_source_mapping_snapshot_enforces_text_and_escaped_byte_limits(monkeypatch) -> None:
    monkeypatch.setattr(models, "MAX_SOURCE_MAPPING_STRING_CHARS", 8)
    with pytest.raises(ValueError, match="field size"):
        canonical_source_mapping_snapshot({"value": "x" * 9})

    monkeypatch.setattr(models, "MAX_SOURCE_MAPPING_STRING_CHARS", 100)
    monkeypatch.setattr(models, "MAX_SOURCE_MAPPING_JSON_BYTES", 23)
    compact = json.dumps(
        {"x": "\\" * 8},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(compact) == 24
    with pytest.raises(ValueError, match="escaped JSON byte budget"):
        canonical_source_mapping_snapshot({"x": "\\" * 8})


@pytest.mark.parametrize(
    "value",
    [
        math.inf,
        -math.inf,
        math.nan,
        models.MAX_SOURCE_MAPPING_ABS_NUMBER + 1,
        float(models.MAX_SOURCE_MAPPING_ABS_NUMBER * 2),
    ],
)
def test_source_mapping_snapshot_rejects_unbounded_numbers(value: object) -> None:
    with pytest.raises(ValueError, match="(finite and bounded|numeric budget)"):
        canonical_source_mapping_snapshot({"value": value})


def test_sidecar_serializes_only_the_canonical_source_mapping_snapshot(tmp_path) -> None:
    result = ReconstructionResult(
        source_id="mapping-source",
        source_image_name="source.png",
        source_mapping={"z": (1, {"label": "Node"}), "a": [True, None]},
    )

    relative = SidecarStore(tmp_path).write(result)
    payload = json.loads((tmp_path / relative / "source-map.json").read_text())

    assert payload == {
        "a": [True, None],
        "z": [1, {"label": "Node"}],
    }


def test_sidecar_rejects_mapping_subclass_without_running_copy_hooks(tmp_path) -> None:
    calls: list[str] = []

    class HookedMapping(dict):
        def __iter__(self):
            calls.append("iter")
            return super().__iter__()

        def __deepcopy__(self, memo):
            calls.append("deepcopy")
            return dict(self)

    result = ReconstructionResult(
        source_id="mapping-hook",
        source_image_name="source.png",
    )
    result.source_mapping = HookedMapping(source={"source_id": result.source_id})

    with pytest.raises(ValueError, match="invalid source mapping"):
        SidecarStore(tmp_path).write(result)

    assert calls == []
    assert not (tmp_path / "diagrams" / "mapping-hook").exists()


def test_sidecar_rejects_oversized_mapping_before_creating_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(models, "MAX_SOURCE_MAPPING_JSON_BYTES", 23)
    json_calls = 0

    def unexpected_json_bytes(_value):
        nonlocal json_calls
        json_calls += 1
        pytest.fail("oversized source mappings must fail before sidecar JSON serialization")

    monkeypatch.setattr(sidecars, "_json_bytes", unexpected_json_bytes)
    result = ReconstructionResult(
        source_id="mapping-oversized",
        source_image_name="source.png",
        source_mapping={"x": "\\" * 8},
    )

    with pytest.raises(ValueError, match="invalid source mapping"):
        SidecarStore(tmp_path).write(result)

    assert json_calls == 0
    assert not (tmp_path / "diagrams" / "mapping-oversized").exists()


def test_sidecar_rejects_live_mapping_change_during_safe_result_copy(
    tmp_path,
    monkeypatch,
) -> None:
    result = ReconstructionResult(
        source_id="mapping-race",
        source_image_name="source.png",
        source_mapping={"source": {"source_id": "mapping-race"}},
    )
    original_model_copy = ReconstructionResult.model_copy

    def mutating_model_copy(self, *, update=None, deep=False):
        copied = original_model_copy(self, update=update, deep=deep)
        if deep:
            assert result.source_mapping is not None
            result.source_mapping["source"]["source_id"] = "changed"
        return copied

    monkeypatch.setattr(ReconstructionResult, "model_copy", mutating_model_copy)

    with pytest.raises(ValueError, match="changed while its snapshot was captured"):
        SidecarStore(tmp_path).write(result)

    assert not (tmp_path / "diagrams" / "mapping-race").exists()
