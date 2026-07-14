from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

import marker_mermaid.models as models
from marker_mermaid.config import MermaidConfig, Mode, quality_grade
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    MermaidCandidate,
    MetricResult,
    PromptBudgetNotice,
    SceneElement,
    SceneGroup,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
    canonical_typed_ir_snapshot,
)


def test_mode_budgets_and_marker_prefixes():
    assert MermaidConfig().candidate_count == 3
    assert MermaidConfig(mode=Mode.MAXIMAL).max_repair_iterations == 10
    strict = MermaidConfig.from_marker_config(
        {"MermaidDiagramProcessor_mode": "strict", "candidate_count": 1}
    )
    assert strict.mode == Mode.STRICT
    assert strict.candidate_count == 1


def test_original_image_cannot_be_disabled():
    with pytest.raises(ValidationError):
        MermaidConfig(extract_images=False)
    with pytest.raises(ValidationError):
        MermaidConfig(include_original_image=False)


@pytest.mark.parametrize(
    "values",
    [
        {"tile_size": 63},
        {"tile_size": 128, "tile_overlap": -1},
        {"tile_size": 128, "tile_overlap": 128},
    ],
)
def test_tile_geometry_budget_is_validated(values):
    with pytest.raises(ValidationError, match="tile_"):
        MermaidConfig(**values)


def test_structured_vlm_prompt_budgets_are_bounded_and_marker_configurable():
    config = MermaidConfig.from_marker_config(
        {
            "MermaidDiagramProcessor_max_vlm_prompt_chars": 32_768,
            "MermaidDiagramProcessor_max_vlm_evidence_items": 32,
            "MermaidDiagramProcessor_max_vlm_ocr_items": 64,
        }
    )

    assert config.max_vlm_prompt_chars == 32_768
    assert config.max_vlm_evidence_items == 32
    assert config.max_vlm_ocr_items == 64
    for values in (
        {"max_vlm_prompt_chars": 32_767},
        {"max_vlm_prompt_chars": 1_000_001},
        {"max_views": 17},
        {"max_image_dimension": 4_097},
        {"tile_size": 4_097},
        {"max_vlm_evidence_items": 0},
        {"max_vlm_evidence_items": 4_097},
        {"max_vlm_ocr_items": -1},
        {"max_vlm_ocr_items": 4_097},
    ):
        with pytest.raises(ValidationError):
            MermaidConfig(**values)


def test_prompt_budget_notice_cross_checks_caps_counts_and_reasons():
    valid = {
        "engine": "marker_structured_vlm",
        "selection_profile": "structural-quota-v1",
        "prompt_chars": 10_000,
        "max_prompt_chars": 100_000,
        "schema_reserve_chars": 14_753,
        "max_evidence_items": 1,
        "max_ocr_items": 1,
        "evidence_total": 2,
        "evidence_considered": 2,
        "evidence_included": 1,
        "ocr_total": 2,
        "ocr_considered": 1,
        "ocr_included": 1,
        "omission_reasons": ["evidence_item_limit", "evidence_char_limit", "ocr_item_limit"],
        "selected_evidence_sha256": "0" * 64,
    }
    PromptBudgetNotice.model_validate(valid)

    for changes in (
        {"max_evidence_items": 2},
        {"max_ocr_items": 2},
        {"evidence_included": 2},
        {"ocr_considered": 2},
        {
            "omission_reasons": [
                "evidence_item_limit",
                "ocr_item_limit",
            ]
        },
    ):
        with pytest.raises(ValidationError):
            PromptBudgetNotice.model_validate({**valid, **changes})


@pytest.mark.parametrize(
    ("score", "grade"),
    [(0.85, "A"), (0.849, "B"), (0.70, "B"), (0.699, "C"), (0.50, "C"), (0.49, "D"), (None, "U")],
)
def test_grade_boundaries(score, grade):
    assert quality_grade(score) == grade


def test_scene_rejects_dangling_relations():
    with pytest.raises(ValidationError, match="missing elements"):
        DiagramSceneIR(
            elements=[SceneElement(id="A", role="node", bbox=(0, 0, 1, 1))],
            relations=[
                SceneRelation(
                    id="E",
                    source_id="A",
                    target_id="B",
                    relation_type="edge",
                )
            ],
        )


def test_prediction_and_metric_invariants():
    with pytest.raises(ValidationError):
        DiagramTypePrediction(candidates=["flowchart"], scores=[])
    with pytest.raises(ValidationError):
        MetricResult(name="ocr_recall", value=None, available=True)
    with pytest.raises(ValidationError, match="descending"):
        DiagramTypePrediction(candidates=["flowchart", "architecture"], scores=[0.1, 0.9])


def test_engine_observation_and_typed_ir_are_resource_bounded():
    prediction = DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
    candidate = TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []})
    with pytest.raises(ValidationError, match="too_long"):
        EngineObservation(prediction=prediction, typed_candidates=[candidate] * 65)

    deeply_nested: dict = {}
    cursor = deeply_nested
    for _ in range(66):
        child: dict = {}
        cursor["child"] = child
        cursor = child
    with pytest.raises(ValidationError, match="nesting depth"):
        TypedIRCandidate(diagram_type="mindmap", ir=deeply_nested)


def test_typed_ir_snapshot_is_canonical_detached_and_normalizes_tuples() -> None:
    shared = {"label": "Node"}
    source = {
        "z": (shared, 1),
        "a": [shared, True, None],
    }

    snapshot = canonical_typed_ir_snapshot(source)

    assert snapshot == {
        "a": [{"label": "Node"}, True, None],
        "z": [{"label": "Node"}, 1],
    }
    assert snapshot is not source
    assert list(snapshot) == ["a", "z"]
    assert snapshot["a"] is not source["a"]
    assert snapshot["a"][0] is not snapshot["z"][0]
    shared["label"] = "Changed"
    source["a"].append("later")
    assert snapshot["a"] == [{"label": "Node"}, True, None]
    assert snapshot["z"] == [{"label": "Node"}, 1]


def test_typed_ir_snapshot_rejects_subclasses_without_running_hooks() -> None:
    calls: list[str] = []

    class HookedDict(dict):
        def __iter__(self):
            calls.append("dict-iter")
            return super().__iter__()

        def __deepcopy__(self, memo):
            calls.append("dict-deepcopy")
            return dict(self)

    class HookedList(list):
        def __iter__(self):
            calls.append("list-iter")
            return super().__iter__()

    class HookedString(str):
        def encode(self, *args, **kwargs):
            calls.append("string-encode")
            return super().encode(*args, **kwargs)

    with pytest.raises(ValueError, match="exact plain dictionary"):
        canonical_typed_ir_snapshot(HookedDict(nodes=[]))
    with pytest.raises(ValueError, match="exact JSON-compatible"):
        canonical_typed_ir_snapshot({"nodes": HookedList()})
    with pytest.raises(ValueError, match="exact JSON-compatible"):
        canonical_typed_ir_snapshot({"label": HookedString("Node")})

    assert calls == []


def test_typed_ir_snapshot_enforces_exact_depth_and_item_limits(monkeypatch) -> None:
    depth_payload = {"x": [1]}
    monkeypatch.setattr(models, "MAX_IR_DEPTH", 2)
    assert canonical_typed_ir_snapshot(depth_payload) == depth_payload
    monkeypatch.setattr(models, "MAX_IR_DEPTH", 1)
    with pytest.raises(ValueError, match="nesting depth"):
        canonical_typed_ir_snapshot(depth_payload)

    item_payload = {"x": [1, 2]}
    monkeypatch.setattr(models, "MAX_IR_DEPTH", 64)
    monkeypatch.setattr(models, "MAX_IR_ITEMS", 5)
    assert canonical_typed_ir_snapshot(item_payload) == item_payload
    monkeypatch.setattr(models, "MAX_IR_ITEMS", 4)
    with pytest.raises(ValueError, match="item budget"):
        canonical_typed_ir_snapshot(item_payload)


def test_typed_ir_snapshot_enforces_key_value_and_aggregate_text_limits(monkeypatch) -> None:
    monkeypatch.setattr(models, "MAX_IR_TEXT_CHARS", 3)
    assert canonical_typed_ir_snapshot({"key": "val"}) == {"key": "val"}
    with pytest.raises(ValueError, match="field size"):
        canonical_typed_ir_snapshot({"long": "x"})
    with pytest.raises(ValueError, match="field size"):
        canonical_typed_ir_snapshot({"key": "long"})

    multibyte = {"é": "한"}
    monkeypatch.setattr(models, "MAX_IR_TEXT_CHARS", 50_000)
    monkeypatch.setattr(models, "MAX_IR_UTF8_TEXT_BYTES", 5)
    assert canonical_typed_ir_snapshot(multibyte) == multibyte
    monkeypatch.setattr(models, "MAX_IR_UTF8_TEXT_BYTES", 4)
    with pytest.raises(ValueError, match="UTF-8 text byte budget"):
        canonical_typed_ir_snapshot(multibyte)


def test_typed_ir_snapshot_counts_repeated_alias_text_and_escaped_json_bytes(
    monkeypatch,
) -> None:
    shared = ["é"]
    repeated = {"a": shared, "b": shared}
    monkeypatch.setattr(models, "MAX_IR_UTF8_TEXT_BYTES", 6)
    assert canonical_typed_ir_snapshot(repeated) == {"a": ["é"], "b": ["é"]}
    monkeypatch.setattr(models, "MAX_IR_UTF8_TEXT_BYTES", 5)
    with pytest.raises(ValueError, match="UTF-8 text byte budget"):
        canonical_typed_ir_snapshot(repeated)

    escaped = {"x": "\\" * 8}
    compact_size = len(
        json.dumps(
            escaped,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    monkeypatch.setattr(models, "MAX_IR_UTF8_TEXT_BYTES", 1_000_000)
    monkeypatch.setattr(models, "MAX_IR_JSON_BYTES", compact_size)
    assert canonical_typed_ir_snapshot(escaped) == escaped
    monkeypatch.setattr(models, "MAX_IR_JSON_BYTES", compact_size - 1)
    with pytest.raises(ValueError, match="escaped JSON byte budget"):
        canonical_typed_ir_snapshot(escaped)


def test_typed_ir_snapshot_rejects_cycles_and_unbounded_numbers() -> None:
    recursive: dict[str, object] = {}
    recursive["self"] = recursive
    with pytest.raises(ValueError, match="reference cycles"):
        canonical_typed_ir_snapshot(recursive)

    assert canonical_typed_ir_snapshot(
        {
            "minimum": -models.MAX_IR_ABS_NUMBER,
            "maximum": models.MAX_IR_ABS_NUMBER,
            "float": float(models.MAX_IR_ABS_NUMBER),
        }
    )
    for value in (
        models.MAX_IR_ABS_NUMBER + 1,
        -(models.MAX_IR_ABS_NUMBER + 1),
        float(models.MAX_IR_ABS_NUMBER * 2),
        math.inf,
        -math.inf,
        math.nan,
    ):
        with pytest.raises(ValueError, match="(numeric budget|finite and bounded)"):
            canonical_typed_ir_snapshot({"value": value})


def test_typed_candidate_owns_a_canonical_snapshot_of_input_ir() -> None:
    source = {"nodes": [{"id": "A"}], "future": ("kept",)}

    candidate = TypedIRCandidate(diagram_type="flowchart", ir=source)

    assert candidate.ir == {"future": ["kept"], "nodes": [{"id": "A"}]}
    assert candidate.ir is not source
    assert candidate.ir["nodes"] is not source["nodes"]
    source["nodes"][0]["id"] = "changed"
    assert candidate.ir["nodes"][0]["id"] == "A"


def test_engine_observation_enforces_aggregate_typed_ir_budget(monkeypatch) -> None:
    prediction = DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
    candidate = TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []})
    one_ir_size = len(
        json.dumps(
            candidate.ir,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    monkeypatch.setattr(models, "MAX_OBSERVATION_TYPED_IR_JSON_BYTES", one_ir_size * 2)

    observation = EngineObservation(
        prediction=prediction,
        typed_candidates=[candidate, candidate],
    )

    assert observation.typed_candidates[0] is not candidate
    assert observation.typed_candidates[0].ir is not candidate.ir
    assert observation.typed_candidates[0].ir is not observation.typed_candidates[1].ir

    monkeypatch.setattr(models, "MAX_OBSERVATION_TYPED_IR_JSON_BYTES", one_ir_size * 2 - 1)
    with pytest.raises(ValidationError, match="aggregate JSON byte budget"):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[candidate, candidate],
        )


def test_engine_observation_rejects_candidate_count_before_inspecting_items() -> None:
    calls: list[str] = []

    class HookedCandidate:
        def __getattribute__(self, name):
            calls.append(name)
            return super().__getattribute__(name)

    with pytest.raises(ValidationError, match="too_long"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
            typed_candidates=[HookedCandidate()] * 65,
        )

    assert calls == []


def test_engine_observation_validates_raw_typed_candidate_metadata_without_hooks() -> None:
    calls: list[str] = []

    class HookedString(str):
        def encode(self, *args, **kwargs):
            calls.append("encode")
            return super().encode(*args, **kwargs)

    class HookedNumber:
        def __float__(self):
            calls.append("float")
            return 0.5

    prediction = DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
    observation = EngineObservation(
        prediction=prediction,
        typed_candidates=[
            {
                "diagram_type": "flowchart",
                "ir": {"nodes": []},
                "confidence": 1,
            }
        ],
    )
    assert observation.typed_candidates[0].confidence == 1

    with pytest.raises(ValidationError, match="plain string"):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[{"diagram_type": HookedString("flowchart"), "ir": {"nodes": []}}],
        )
    with pytest.raises(ValidationError, match="exact number"):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[
                {
                    "diagram_type": "flowchart",
                    "ir": {"nodes": []},
                    "confidence": HookedNumber(),
                }
            ],
        )
    with pytest.raises(ValidationError, match="label must be a string"):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[
                {
                    "diagram_type": "flowchart",
                    "ir": {"nodes": [{"label": {"invalid": True}}]},
                }
            ],
        )

    assert calls == []


def test_engine_observation_detaches_mutated_typed_candidate_for_sibling_isolation() -> None:
    candidate = TypedIRCandidate(
        diagram_type="flowchart",
        ir={"nodes": [{"id": "A", "label": "Start"}]},
    )
    candidate.ir["nodes"][0]["label"] = {"invalid": "nested label"}

    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
        typed_candidates=[candidate],
    )

    assert observation.typed_candidates[0] is not candidate
    candidate.ir["nodes"][0]["label"] = "mutated again"
    assert observation.typed_candidates[0].ir["nodes"][0]["label"] == {"invalid": "nested label"}
    with pytest.raises(ValidationError, match="label must be a string"):
        observation.typed_candidates[0].canonical_key()


def test_typed_candidate_field_count_fails_before_copy_or_ir_scan(monkeypatch) -> None:
    candidate = TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []})
    candidate.__dict__["extra"] = "rejected"
    monkeypatch.setattr(
        models,
        "canonical_typed_ir_snapshot",
        lambda _value: pytest.fail("over-field candidate must fail before IR scan"),
    )

    with pytest.raises(ValueError, match="field-count"):
        candidate.canonical_key()
    with pytest.raises(ValidationError, match="field-count"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
            typed_candidates=[candidate],
        )
    with pytest.raises(ValidationError, match="field-count"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
            typed_candidates=[
                {
                    "diagram_type": "flowchart",
                    "ir": {"nodes": []},
                    "confidence": 0.5,
                    "extra": "rejected",
                }
            ],
        )
    with pytest.raises(ValidationError, match="unknown public field"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
            typed_candidates=[
                {
                    "diagram_type": "flowchart",
                    "ir": {"nodes": []},
                    "extra": "rejected",
                }
            ],
        )


def test_candidate_envelopes_reject_hostile_keys_without_equality_hooks() -> None:
    calls: list[str] = []

    class HostileKey(str):
        __hash__ = str.__hash__

        def __eq__(self, other):
            calls.append(str(other))
            raise AssertionError("candidate key equality hook must not run")

    typed = TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []})
    typed.__dict__.pop("diagram_type")
    typed.__dict__[HostileKey("diagram_type")] = "flowchart"

    with pytest.raises(ValueError, match="unknown public field"):
        typed.canonical_key()
    with pytest.raises(ValidationError, match="unknown public field"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1.0]),
            typed_candidates=[typed],
        )
    with pytest.raises(ValidationError, match="field names must be plain strings"):
        MermaidCandidate.model_validate(
            {
                "candidate_id": "candidate-1",
                "generation_method": "typed_ir",
                HostileKey("diagram_type"): "flowchart",
                "typed_ir": {"nodes": []},
            }
        )

    assert calls == []


def test_mutated_diagram_type_fails_before_utf8_or_ir_allocation(monkeypatch) -> None:
    candidate = TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []})
    candidate.diagram_type = "x" * (models.MAX_ID_CHARS + 1)
    prediction = DiagramTypePrediction(candidates=["flowchart"], scores=[1.0])
    monkeypatch.setattr(
        models,
        "_require_utf8_text",
        lambda *_args, **_kwargs: pytest.fail(
            "oversized diagram type must fail before UTF-8 encoding"
        ),
    )
    monkeypatch.setattr(
        models,
        "canonical_typed_ir_snapshot",
        lambda _value: pytest.fail("oversized diagram type must fail before IR scan"),
    )

    with pytest.raises(ValueError, match="text size"):
        candidate.canonical_key()
    with pytest.raises(ValidationError, match="text size"):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[candidate],
        )


def test_typed_candidate_rejects_another_diagram_familys_root_shape():
    with pytest.raises(ValidationError, match="requires root field 'participants'"):
        TypedIRCandidate(
            diagram_type="sequence",
            ir={"nodes": [{"id": "A"}], "edges": []},
        )
    with pytest.raises(ValidationError, match="must be a list"):
        TypedIRCandidate(
            diagram_type="flowchart",
            ir={"nodes": {"A": {"label": "wrong container"}}},
        )


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        (
            "flowchart",
            {"nodes": [{"id": "A", "evidence_ids": [1]}]},
            "nodes[0].evidence_ids[0]",
        ),
        (
            "generic_network",
            {"nodes": [{"id": "A"}], "groups": [{"member_ids": "A"}]},
            "groups[0].member_ids",
        ),
        (
            "swimlane",
            {"lanes": [{"id": "lane", "nodes": ["not-an-object"]}]},
            "lanes[0].nodes[0]",
        ),
        (
            "bpmn",
            {"lanes": [{"nodes": [{"id": "task", "label": ["wrong"]}]}]},
            "lanes[0].nodes[0].label",
        ),
        (
            "sequence",
            {"participants": ["A"], "messages": [{"source": [], "target": "A"}]},
            "messages[0].source",
        ),
        (
            "mindmap",
            {"root": {"label": "Root", "children": ["not-an-object"]}},
            "root.children[0]",
        ),
        (
            "timeline",
            {"events": [{"time": "Q1", "events": ["Launch", {}]}]},
            "events[0].events[1]",
        ),
        (
            "gantt",
            {"sections": [{"tasks": [{"start": 2026, "duration": "1d"}]}]},
            "sections[0].tasks[0].start",
        ),
        (
            "architecture",
            {"services": [{"id": "api", "group": ["cloud"]}]},
            "services[0].group",
        ),
        (
            "architecture",
            {
                "services": [{"id": "api"}, {"id": "db"}],
                "edges": [{"source": "api", "target": "db", "source_side": "X"}],
            },
            "edges[0].source_side",
        ),
    ],
)
def test_phase_one_nested_contracts_reject_wrong_record_shapes(
    diagram_type: str,
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    message = str(exc_info.value)
    assert "violates its nested contract" in message
    assert location in message


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        ("flowchart", {"nodes": []}),
        ("generic_network", {"nodes": [{"label": "[unreadable]"}]}),
        ("swimlane", {"lanes": [{"id": "lane"}]}),
        ("bpmn", {"lanes": [{"nodes": []}]}),
        ("sequence", {"participants": ["Client"], "messages": []}),
        ("mindmap", {"root": {"children": [{"text": "Child"}]}}),
        ("timeline", {"events": [{"period": "Q1", "events": ["Launch"]}]}),
        (
            "gantt",
            {
                "date_format": "YYYY-MM-DD",
                "sections": [{"title": "Build", "tasks": []}],
            },
        ),
        (
            "architecture",
            {
                "services": [
                    {
                        "id": "api",
                        "name": "API",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["vector-api"],
                        "future_metadata": {"kept": True},
                    }
                ]
            },
        ),
    ],
)
def test_phase_one_nested_contracts_preserve_partial_and_forward_compatible_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        (
            "state",
            {
                "states": [{"id": "pending", "kind": "decision"}],
                "transitions": [],
            },
            "states[0].kind",
        ),
        (
            "state",
            {
                "states": [{"id": "pending"}],
                "transitions": [{"source": ["pending"], "target": "[*]"}],
            },
            "transitions[0].source",
        ),
        (
            "class",
            {"classes": [{"members": [{"name": "authorize", "parameters": "amount"}]}]},
            "classes[0].members[0].parameters",
        ),
        (
            "class",
            {
                "classes": [{"id": "Service"}],
                "relations": [{"source": "Service", "target": "Service", "type": "guessed"}],
            },
            "relations[0].type",
        ),
        (
            "er",
            {"entities": [{"attributes": [{"type": "uuid", "keys": ["PK", "INDEX"]}]}]},
            "entities[0].attributes[0].keys[1]",
        ),
        (
            "er",
            {
                "entities": [{"id": "A"}, {"id": "B"}],
                "relationships": [{"source": "A", "target": "B", "identifying": 1}],
            },
            "relationships[0].identifying",
        ),
    ],
)
def test_core_uml_nested_contracts_reject_wrong_shapes_and_closed_tokens(
    diagram_type: str,
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    message = str(exc_info.value)
    assert "violates its nested contract" in message
    assert location in message


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "state",
            {
                "states": [{"kind": "choice", "future_metadata": {"kept": True}}],
                "transitions": [{"source": "[*]", "target": "choice"}],
            },
        ),
        (
            "class",
            {
                "classes": [
                    {
                        "members": [
                            {
                                "kind": "method",
                                "visibility": "+",
                                "parameters": ["amount"],
                                "classifier": "abstract",
                                "future_metadata": {"kept": True},
                            }
                        ],
                        "plugin_style": "stereotype",
                    }
                ],
                "relations": [{"type": "realization"}],
            },
        ),
        (
            "er",
            {
                "entities": [
                    {
                        "attributes": [
                            {
                                "keys": ["PK", "FK"],
                                "future_metadata": {"kept": True},
                            }
                        ],
                        "plugin_style": "weak-entity",
                    }
                ],
                "relationships": [
                    {
                        "source_cardinality": "only_one",
                        "target_cardinality": "zero_or_more",
                        "identifying": False,
                    }
                ],
            },
        ),
    ],
)
def test_core_uml_nested_contracts_preserve_partial_and_forward_compatible_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize("bbox", [[0, 0, 10], [0, 0, True, 10], ["0", 0, 10, 10]])
def test_phase_one_nested_contracts_require_four_strict_finite_bbox_numbers(bbox) -> None:
    with pytest.raises(ValidationError, match="bbox"):
        TypedIRCandidate(
            diagram_type="architecture",
            ir={"services": [{"id": "api", "bbox": bbox}]},
        )


def test_canonical_key_revalidates_mutated_nested_contracts() -> None:
    candidate = TypedIRCandidate(
        diagram_type="timeline",
        ir={"events": [{"time": "Q1", "events": ["Launch"]}]},
    )
    candidate.ir["events"][0]["events"] = "Launch"

    with pytest.raises(ValidationError, match=r"events\[0\]\.events"):
        candidate.canonical_key()


def test_canonical_key_revalidates_mutated_core_uml_nested_contract() -> None:
    candidate = TypedIRCandidate(
        diagram_type="class",
        ir={"classes": [{"members": [{"name": "authorize", "parameters": ["amount"]}]}]},
    )
    candidate.ir["classes"][0]["members"][0]["parameters"] = "amount"

    with pytest.raises(ValidationError, match=r"classes\[0\]\.members\[0\]\.parameters"):
        candidate.canonical_key()


def test_canonical_key_uses_a_bounded_digest_without_model_dump(
    monkeypatch,
) -> None:
    payload = "private-label-" * 100
    candidate = TypedIRCandidate(
        diagram_type="flowchart",
        ir={"nodes": [{"id": "A", "label": payload}]},
    )

    def unexpected_model_dump(*args, **kwargs):
        pytest.fail("canonical_key must not model_dump a live typed candidate")

    monkeypatch.setattr(TypedIRCandidate, "model_dump", unexpected_model_dump)
    before = candidate.canonical_key()
    candidate.ir["nodes"][0]["label"] = "changed"
    after = candidate.canonical_key()

    assert before != after
    assert payload not in before
    assert len(before) <= 330
    assert before.startswith("flowchart\0sha256:")


def test_canonical_key_rejects_mutated_confidence_without_numeric_hooks() -> None:
    calls: list[str] = []

    class HookedNumber:
        def __float__(self):
            calls.append("float")
            return 0.5

    candidate = TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []})
    candidate.confidence = HookedNumber()

    with pytest.raises(ValueError, match="exact number"):
        candidate.canonical_key()

    assert calls == []


def test_mermaid_candidate_canonicalizes_and_contract_validates_typed_ir() -> None:
    source = {"nodes": [{"id": "A"}], "future": ("kept",)}

    candidate = MermaidCandidate(
        candidate_id="candidate-1",
        generation_method="typed_ir",
        diagram_type="flowchart",
        typed_ir=source,
    )

    assert candidate.typed_ir == {"future": ["kept"], "nodes": [{"id": "A"}]}
    assert candidate.typed_ir is not source
    source["nodes"][0]["id"] = "changed"
    assert candidate.typed_ir["nodes"][0]["id"] == "A"

    with pytest.raises(ValidationError, match="requires root field 'nodes'"):
        MermaidCandidate(
            candidate_id="candidate-2",
            generation_method="typed_ir",
            diagram_type="flowchart",
            typed_ir={"participants": []},
        )


def test_mermaid_candidate_rejects_typed_ir_subclass_without_hooks() -> None:
    calls: list[str] = []

    class HookedIR(dict):
        def __iter__(self):
            calls.append("iter")
            return super().__iter__()

        def __deepcopy__(self, memo):
            calls.append("deepcopy")
            return dict(self)

    with pytest.raises(ValidationError, match="exact plain dictionary"):
        MermaidCandidate(
            candidate_id="candidate-1",
            generation_method="typed_ir",
            diagram_type="flowchart",
            typed_ir=HookedIR(nodes=[]),
        )

    assert calls == []


@pytest.mark.parametrize("diagram_type", ["x" * (models.MAX_ID_CHARS + 1), "\ud800"])
def test_mermaid_candidate_rejects_invalid_diagram_type_before_typed_ir_scan(
    monkeypatch,
    diagram_type,
) -> None:
    monkeypatch.setattr(
        models,
        "canonical_typed_ir_snapshot",
        lambda _value: pytest.fail("invalid diagram type must fail before typed IR scan"),
    )

    with pytest.raises(ValidationError, match="candidate diagram type"):
        MermaidCandidate(
            candidate_id="candidate-1",
            generation_method="typed_ir",
            diagram_type=diagram_type,
            typed_ir={"nodes": []},
        )


def test_candidate_confidence_is_a_probability():
    with pytest.raises(ValidationError):
        TypedIRCandidate(diagram_type="flowchart", ir={"nodes": []}, confidence=1.1)


def test_observation_text_and_scene_coordinates_are_json_bounded():
    with pytest.raises(ValidationError, match="text size limit"):
        VisualEvidence(id="e", kind="ocr_token", text="x" * 50_001)
    with pytest.raises(ValidationError, match="finite"):
        SceneElement(id="A", role="node", bbox=(0, 0, math.nan, 1))
    with pytest.raises(ValidationError, match="finite"):
        SceneRelation(
            id="E",
            source_id=None,
            target_id=None,
            relation_type="edge",
            polyline=[(0, 0), (math.inf, 1)],
        )
    with pytest.raises(ValidationError, match="endpoint"):
        SceneRelation(id="E", source_id="x" * 257, relation_type="edge")
    with pytest.raises(ValidationError, match="warning"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[1]),
            warnings=["x" * 4_097],
        )


def test_scene_group_budget_is_independent_from_evidence_reference_budget():
    members = [f"N{index}" for index in range(257)]
    group = SceneGroup(id="G", role="group", bbox=(0, 0, 1, 1), member_ids=members)

    assert len(group.member_ids) == 257
