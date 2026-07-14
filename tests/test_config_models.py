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


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        ("requirement", {"requirements": ["not-an-object"]}, "requirements[0]"),
        (
            "requirement",
            {"requirements": [{"text": ["wrong"]}]},
            "requirements[0].text",
        ),
        (
            "requirement",
            {"requirements": [{"bbox": [0, 0, 10]}]},
            "requirements[0].bbox",
        ),
        (
            "requirement",
            {"requirements": [], "elements": {"id": "API"}},
            "elements",
        ),
        (
            "requirement",
            {"requirements": [], "relations": [{"source": ["API"]}]},
            "relations[0].source",
        ),
        (
            "requirement",
            {"requirements": [{"type": "business"}]},
            "requirements[0].type",
        ),
        (
            "requirement",
            {"requirements": [{"risk": "urgent"}]},
            "requirements[0].risk",
        ),
        (
            "requirement",
            {"requirements": [{"verify_method": "review"}]},
            "requirements[0].verify_method",
        ),
        (
            "requirement",
            {"requirements": [{"verifymethod": "review"}]},
            "requirements[0].verifymethod",
        ),
        (
            "requirement",
            {"requirements": [], "relations": [{"type": "depends"}]},
            "relations[0].type",
        ),
        ("block", {"blocks": ["not-an-object"]}, "blocks[0]"),
        ("block", {"blocks": [{"label": {"wrong": True}}]}, "blocks[0].label"),
        ("block", {"blocks": [{"shape": "cloud"}]}, "blocks[0].shape"),
        ("block", {"blocks": [], "edges": {"source": "A"}}, "edges"),
        (
            "block",
            {"blocks": [], "edges": [{"source": ["A"]}]},
            "edges[0].source",
        ),
        (
            "block",
            {"blocks": [], "edges": [{"bidirectional": 1}]},
            "edges[0].bidirectional",
        ),
        (
            "block",
            {"blocks": [], "edges": [{"evidence_ids": [1]}]},
            "edges[0].evidence_ids[0]",
        ),
        ("block", {"blocks": [], "columns": 2.5}, "columns"),
        ("block", {"blocks": [], "columns": True}, "columns"),
        ("block", {"blocks": [], "columns": []}, "columns"),
    ],
)
def test_phase_two_native_nested_contracts_reject_wrong_shapes_and_closed_tokens(
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
            "requirement",
            {
                "requirements": [
                    {
                        "type": "FUNCTIONAL_REQUIREMENT",
                        "risk": "HIGH",
                        "verifymethod": "TEST",
                        "future_metadata": {"kept": True},
                    }
                ],
                "elements": [{"plugin_style": "external"}],
                "relations": [
                    {
                        "source": "unknown",
                        "target": "also-unknown",
                        "type": "SATISFIES",
                        "future_metadata": {"kept": True},
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "block",
            {
                "blocks": [
                    {
                        "shape": "CYLINDER",
                        "future_metadata": {"kept": True},
                    }
                ],
                "edges": [
                    {
                        "source": "unknown",
                        "target": "also-unknown",
                        "style": "future-style",
                        "future_metadata": {"kept": True},
                    }
                ],
                "columns": "2",
                "future_root_metadata": {"kept": True},
            },
        ),
    ],
)
def test_phase_two_native_nested_contracts_preserve_partial_alias_and_forward_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize(
    ("ir", "location"),
    [
        ({"level": ["context"], "elements": []}, "level"),
        ({"level": "landscape", "elements": []}, "level"),
        ({"elements": ["not-an-object"]}, "elements[0]"),
        ({"elements": [{"id": 1}]}, "elements[0].id"),
        ({"elements": [{"label": ["API"]}]}, "elements[0].label"),
        ({"elements": [{"name": {"text": "API"}}]}, "elements[0].name"),
        ({"elements": [{"kind": "actor"}]}, "elements[0].kind"),
        ({"elements": [{"type": "service"}]}, "elements[0].type"),
        ({"elements": [{"boundary": 1}]}, "elements[0].boundary"),
        ({"elements": [{"description": 1}]}, "elements[0].description"),
        ({"elements": [{"technology": ["Python"]}]}, "elements[0].technology"),
        ({"elements": [{"bbox": [0, 0, 10]}]}, "elements[0].bbox"),
        ({"elements": [{"evidence_ids": [1]}]}, "elements[0].evidence_ids[0]"),
        ({"elements": [], "boundaries": {"id": "system"}}, "boundaries"),
        ({"elements": [], "boundaries": ["not-an-object"]}, "boundaries[0]"),
        ({"elements": [], "boundaries": [{"id": 1}]}, "boundaries[0].id"),
        ({"elements": [], "boundaries": [{"label": ["System"]}]}, "boundaries[0].label"),
        ({"elements": [], "boundaries": [{"type": ["system"]}]}, "boundaries[0].type"),
        ({"elements": [], "boundaries": [{"bbox": [0, False, 10, 10]}]}, "boundaries[0].bbox"),
        (
            {"elements": [], "boundaries": [{"evidence_ids": [1]}]},
            "boundaries[0].evidence_ids[0]",
        ),
        ({"elements": [], "relations": {"source": "A"}}, "relations"),
        ({"elements": [], "relations": ["not-an-object"]}, "relations[0]"),
        ({"elements": [], "relations": [{"id": 1}]}, "relations[0].id"),
        ({"elements": [], "relations": [{"source": ["A"]}]}, "relations[0].source"),
        ({"elements": [], "relations": [{"target": 1}]}, "relations[0].target"),
        ({"elements": [], "relations": [{"label": {"text": "uses"}}]}, "relations[0].label"),
        ({"elements": [], "relations": [{"technology": ["HTTPS"]}]}, "relations[0].technology"),
        ({"elements": [], "relations": [{"bidirectional": 1}]}, "relations[0].bidirectional"),
        ({"elements": [], "relations": [{"source_side": "r"}]}, "relations[0].source_side"),
        ({"elements": [], "relations": [{"target_side": "X"}]}, "relations[0].target_side"),
        ({"elements": [], "relations": [{"bbox": [0, 0, "10", 10]}]}, "relations[0].bbox"),
        (
            {"elements": [], "relations": [{"evidence_ids": [1]}]},
            "relations[0].evidence_ids[0]",
        ),
    ],
)
def test_c4_nested_contract_rejects_wrong_shapes_scalars_and_closed_tokens(
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TypedIRCandidate(diagram_type="c4", ir=ir)

    message = str(exc_info.value)
    assert "violates its nested contract" in message
    assert location in message


def test_c4_nested_contract_preserves_partial_alias_diagnostic_and_forward_ir() -> None:
    ir = {
        "level": "COMPONENT",
        "elements": [
            {
                "type": "EXTERNAL_DATABASE",
                "description": "Diagnostic description",
                "technology": "Postgres",
                "bbox": [0, 0, 10, 10],
                "evidence_ids": ["vector-db"],
                "future_metadata": {"kept": True},
            }
        ],
        "boundaries": [
            {
                "type": "vendor_specific_boundary",
                "bbox": [0, 0, 20, 20],
                "evidence_ids": ["contour-boundary"],
                "future_metadata": {"kept": True},
            }
        ],
        "relations": [
            {
                "source": "unknown",
                "target": "also-unknown",
                "label": "Uses",
                "technology": "HTTPS",
                "bidirectional": False,
                "source_side": "T",
                "target_side": "B",
                "bbox": [1, 2, 3, 4],
                "evidence_ids": ["arrow-1"],
                "future_metadata": {"kept": True},
            }
        ],
        "future_root_metadata": {"kept": True},
    }

    candidate = TypedIRCandidate(diagram_type="c4", ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize(
    "bbox",
    [[0, 0, 10], [0, 0, True, 10], ["0", 0, 10, 10], [0, 0, math.inf, 10]],
)
def test_c4_nested_contract_requires_four_strict_finite_bbox_numbers(bbox) -> None:
    with pytest.raises(ValidationError, match=r"elements\[0\]\.bbox|finite"):
        TypedIRCandidate(
            diagram_type="c4",
            ir={"elements": [{"id": "api", "bbox": bbox}]},
        )


def test_canonical_key_revalidates_mutated_c4_nested_contract() -> None:
    candidate = TypedIRCandidate(
        diagram_type="c4",
        ir={"elements": [{"kind": "system"}]},
    )
    candidate.ir["elements"][0]["kind"] = "actor"

    with pytest.raises(ValidationError, match=r"elements\[0\]\.kind"):
        candidate.canonical_key()


def test_generic_candidate_envelopes_apply_c4_nested_contract() -> None:
    prediction = DiagramTypePrediction(candidates=["c4"], scores=[1.0])

    with pytest.raises(ValidationError, match=r"elements\[0\]\.kind"):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[
                {
                    "diagram_type": "c4",
                    "ir": {"elements": [{"kind": "actor"}]},
                }
            ],
        )

    with pytest.raises(ValidationError, match=r"relations\[0\]\.source_side"):
        MermaidCandidate(
            candidate_id="candidate-c4",
            generation_method="typed_ir",
            diagram_type="c4",
            typed_ir={
                "elements": [{"id": "api"}],
                "relations": [{"source": "api", "target": "api", "source_side": "r"}],
            },
        )


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        ("deployment", {"nodes": ["not-an-object"]}, "nodes[0]"),
        ("deployment", {"nodes": [], "artifacts": {"id": "image"}}, "artifacts"),
        ("deployment", {"nodes": [], "groups": ["not-an-object"]}, "groups[0]"),
        ("deployment", {"nodes": [], "links": {"source": "app"}}, "links"),
        ("deployment", {"nodes": [], "edges": {"source": "app"}}, "edges"),
        ("deployment", {"nodes": [{"id": 1}]}, "nodes[0].id"),
        ("deployment", {"nodes": [{"name": {"text": "App"}}]}, "nodes[0].name"),
        ("deployment", {"nodes": [{"icon": ["server"]}]}, "nodes[0].icon"),
        ("deployment", {"nodes": [{"group": 1}]}, "nodes[0].group"),
        ("deployment", {"nodes": [{"bbox": [0, 0, 10]}]}, "nodes[0].bbox"),
        ("deployment", {"nodes": [{"evidence_ids": [1]}]}, "nodes[0].evidence_ids[0]"),
        ("deployment", {"nodes": [], "artifacts": [{"label": ["Image"]}]}, "artifacts[0].label"),
        ("deployment", {"nodes": [], "groups": [{"id": 1}]}, "groups[0].id"),
        ("deployment", {"nodes": [], "groups": [{"label": ["Zone"]}]}, "groups[0].label"),
        ("deployment", {"nodes": [], "groups": [{"icon": {"name": "cloud"}}]}, "groups[0].icon"),
        ("deployment", {"nodes": [], "groups": [{"bbox": [0, False, 10, 10]}]}, "groups[0].bbox"),
        (
            "deployment",
            {"nodes": [], "groups": [{"evidence_ids": [1]}]},
            "groups[0].evidence_ids[0]",
        ),
        ("deployment", {"nodes": [], "links": ["not-an-object"]}, "links[0]"),
        ("deployment", {"nodes": [], "links": [{"id": 1}]}, "links[0].id"),
        ("deployment", {"nodes": [], "links": [{"source": ["app"]}]}, "links[0].source"),
        ("deployment", {"nodes": [], "links": [{"target": 1}]}, "links[0].target"),
        ("deployment", {"nodes": [], "links": [{"label": {"text": "uses"}}]}, "links[0].label"),
        ("deployment", {"nodes": [], "links": [{"bidirectional": 1}]}, "links[0].bidirectional"),
        ("deployment", {"nodes": [], "links": [{"source_side": "r"}]}, "links[0].source_side"),
        ("deployment", {"nodes": [], "links": [{"target_side": "X"}]}, "links[0].target_side"),
        ("deployment", {"nodes": [], "links": [{"bbox": [0, 0, "10", 10]}]}, "links[0].bbox"),
        ("deployment", {"nodes": [], "links": [{"evidence_ids": [1]}]}, "links[0].evidence_ids[0]"),
        (
            "deployment",
            {"nodes": [], "links": [], "edges": [{"label": ["ignored but invalid"]}]},
            "edges[0].label",
        ),
        ("component", {"components": ["not-an-object"]}, "components[0]"),
        ("component", {"components": [], "interfaces": {"id": "port"}}, "interfaces"),
        ("component", {"components": [], "groups": {"id": "zone"}}, "groups"),
        ("component", {"components": [], "dependencies": {"source": "web"}}, "dependencies"),
        ("component", {"components": [], "edges": ["not-an-object"]}, "edges[0]"),
        ("component", {"components": [{"label": ["Web"]}]}, "components[0].label"),
        (
            "component",
            {"components": [], "interfaces": [{"evidence_ids": [1]}]},
            "interfaces[0].evidence_ids[0]",
        ),
        (
            "component",
            {"components": [], "dependencies": [{"bidirectional": 1}]},
            "dependencies[0].bidirectional",
        ),
        (
            "component",
            {"components": [], "dependencies": [{"source_side": "L "}]},
            "dependencies[0].source_side",
        ),
        (
            "component",
            {"components": [], "dependencies": [{"target_side": "b"}]},
            "dependencies[0].target_side",
        ),
        (
            "component",
            {"components": [], "dependencies": [], "edges": [{"source": ["ignored"]}]},
            "edges[0].source",
        ),
    ],
)
def test_architecture_fallback_nested_contracts_reject_wrong_shapes(
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
            "deployment",
            {
                "nodes": [
                    {
                        "name": "App",
                        "icon": "VENDOR_RUNTIME",
                        "group": "runtime",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-app"],
                        "stereotype": "executionEnvironment",
                    }
                ],
                "artifacts": [{"label": "Image", "future_metadata": {"kept": True}}],
                "groups": [
                    {
                        "id": "runtime",
                        "label": "Runtime",
                        "icon": "PRIVATE_CLOUD",
                        "bbox": [0, 0, 20, 20],
                        "evidence_ids": ["contour-runtime"],
                    }
                ],
                "links": [
                    {
                        "source": "unknown",
                        "target": "also-unknown",
                        "label": "JDBC",
                        "source_side": "T",
                        "target_side": "B",
                        "bidirectional": False,
                        "evidence_ids": ["line-jdbc"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "edges": [{"source": "legacy", "target": "ignored", "label": "Legacy"}],
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "component",
            {
                "components": [
                    {
                        "label": "Web",
                        "icon": "SERVER",
                        "group": "application",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-web"],
                        "stereotype": "component",
                    }
                ],
                "interfaces": [{"name": "Auth port", "provided": True}],
                "groups": [{"id": "application", "icon": "cloud"}],
                "dependencies": [
                    {
                        "source": "unknown",
                        "target": "also-unknown",
                        "label": "OAuth",
                        "source_side": "L",
                        "target_side": "R",
                        "bidirectional": True,
                        "evidence_ids": ["line-oauth"],
                    }
                ],
                "edges": [{"source": "legacy", "target": "ignored", "label": "Legacy"}],
                "future_root_metadata": {"kept": True},
            },
        ),
    ],
)
def test_architecture_fallback_nested_contracts_preserve_partial_and_extra_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize(
    ("diagram_type", "ir", "mutation", "location"),
    [
        (
            "deployment",
            {"nodes": [{"icon": "server"}]},
            lambda ir: ir["nodes"][0].__setitem__("icon", ["server"]),
            r"nodes\[0\]\.icon",
        ),
        (
            "component",
            {"components": [{"id": "web"}], "dependencies": [{"source_side": "L"}]},
            lambda ir: ir["dependencies"][0].__setitem__("source_side", "l"),
            r"dependencies\[0\]\.source_side",
        ),
    ],
)
def test_canonical_key_revalidates_mutated_architecture_fallback_contracts(
    diagram_type: str,
    ir: dict[str, object],
    mutation,
    location: str,
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)
    mutation(candidate.ir)

    with pytest.raises(ValidationError, match=location):
        candidate.canonical_key()


def test_generic_candidate_envelopes_apply_architecture_fallback_nested_contracts() -> None:
    with pytest.raises(ValidationError, match=r"links\[0\]\.bidirectional"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["deployment"], scores=[1.0]),
            typed_candidates=[
                {
                    "diagram_type": "deployment",
                    "ir": {"nodes": [], "links": [{"bidirectional": 1}]},
                }
            ],
        )

    with pytest.raises(ValidationError, match=r"dependencies\[0\]\.target_side"):
        MermaidCandidate(
            candidate_id="candidate-component",
            generation_method="typed_ir",
            diagram_type="component",
            typed_ir={
                "components": [],
                "dependencies": [{"target_side": "b"}],
            },
        )


@pytest.mark.parametrize(
    ("ir", "location"),
    [
        ({"actors": ["not-an-object"], "use_cases": []}, "actors[0]"),
        ({"actors": [], "use_cases": ["not-an-object"]}, "use_cases[0]"),
        ({"actors": [], "use_cases": [], "relations": {"source": "actor"}}, "relations"),
        ({"actors": [], "use_cases": [], "relations": None}, "relations"),
        ({"actors": [], "use_cases": [], "relations": ["not-an-object"]}, "relations[0]"),
        ({"actors": [{"id": 1}], "use_cases": []}, "actors[0].id"),
        ({"actors": [{"label": ["Shopper"]}], "use_cases": []}, "actors[0].label"),
        ({"actors": [{"name": {"text": "Shopper"}}], "use_cases": []}, "actors[0].name"),
        ({"actors": [{"bbox": [0, 0, 10]}], "use_cases": []}, "actors[0].bbox"),
        (
            {"actors": [{"evidence_ids": [1]}], "use_cases": []},
            "actors[0].evidence_ids[0]",
        ),
        ({"actors": [], "use_cases": [{"id": 1}]}, "use_cases[0].id"),
        ({"actors": [], "use_cases": [{"label": ["Checkout"]}]}, "use_cases[0].label"),
        ({"actors": [], "use_cases": [{"name": {"text": "Checkout"}}]}, "use_cases[0].name"),
        ({"actors": [], "use_cases": [{"bbox": [0, False, 10, 10]}]}, "use_cases[0].bbox"),
        (
            {"actors": [], "use_cases": [{"evidence_ids": [1]}]},
            "use_cases[0].evidence_ids[0]",
        ),
        ({"actors": [], "use_cases": [], "relations": [{"id": 1}]}, "relations[0].id"),
        (
            {"actors": [], "use_cases": [], "relations": [{"source": ["actor"]}]},
            "relations[0].source",
        ),
        (
            {"actors": [], "use_cases": [], "relations": [{"target": 1}]},
            "relations[0].target",
        ),
        (
            {"actors": [], "use_cases": [], "relations": [{"type": ["association"]}]},
            "relations[0].type",
        ),
        (
            {"actors": [], "use_cases": [], "relations": [{"label": {"text": "uses"}}]},
            "relations[0].label",
        ),
        (
            {"actors": [], "use_cases": [], "relations": [{"bbox": [0, 0, "10", 10]}]},
            "relations[0].bbox",
        ),
        (
            {"actors": [], "use_cases": [], "relations": [{"evidence_ids": [1]}]},
            "relations[0].evidence_ids[0]",
        ),
    ],
)
def test_usecase_nested_contract_rejects_wrong_shapes_with_exact_locations(
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TypedIRCandidate(diagram_type="usecase", ir=ir)

    message = str(exc_info.value)
    assert "violates its nested contract" in message
    assert location in message


def test_usecase_nested_contract_preserves_partial_open_relation_and_extra_ir() -> None:
    ir = {
        "direction": "sideways",
        "actors": [
            {
                "name": "Shopper",
                "bbox": [0, 0, 10, 10],
                "evidence_ids": ["ocr-shopper"],
                "stereotype": "primary actor",
            }
        ],
        "use_cases": [
            {
                "label": "Checkout",
                "bbox": [20, 0, 30, 10],
                "evidence_ids": ["ocr-checkout"],
                "system_boundary": "checkout-system",
            }
        ],
        "relations": [
            {
                "source": "unknown",
                "target": "also-unknown",
                "type": "CUSTOM_INCLUDE",
                "label": "Preserved alias",
                "bbox": [10, 0, 20, 10],
                "evidence_ids": ["arrow-include"],
                "future_metadata": {"kept": True},
            }
        ],
        "groups": [{"id": "hidden-system", "member_ids": ["Checkout"]}],
        "future_root_metadata": {"kept": True},
    }

    candidate = TypedIRCandidate(diagram_type="usecase", ir=ir)

    assert candidate.ir == ir


def test_canonical_key_revalidates_mutated_usecase_nested_contract() -> None:
    candidate = TypedIRCandidate(
        diagram_type="usecase",
        ir={
            "actors": [{"id": "actor"}],
            "use_cases": [{"id": "case"}],
            "relations": [{"type": "association"}],
        },
    )
    candidate.ir["relations"][0]["type"] = ["association"]

    with pytest.raises(ValidationError, match=r"relations\[0\]\.type"):
        candidate.canonical_key()


def test_generic_candidate_envelopes_apply_usecase_nested_contract() -> None:
    with pytest.raises(ValidationError, match=r"actors\[0\]\.name"):
        EngineObservation(
            prediction=DiagramTypePrediction(candidates=["usecase"], scores=[1.0]),
            typed_candidates=[
                {
                    "diagram_type": "usecase",
                    "ir": {"actors": [{"name": ["Shopper"]}], "use_cases": []},
                }
            ],
        )

    with pytest.raises(ValidationError, match=r"relations\[0\]\.label"):
        MermaidCandidate(
            candidate_id="candidate-usecase",
            generation_method="typed_ir",
            diagram_type="usecase",
            typed_ir={
                "actors": [],
                "use_cases": [],
                "relations": [{"label": {"text": "uses"}}],
            },
        )


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


@pytest.mark.parametrize(
    ("diagram_type", "ir", "mutation", "location"),
    [
        (
            "requirement",
            {"requirements": [{"type": "functional"}]},
            lambda ir: ir["requirements"][0].__setitem__("type", "business"),
            r"requirements\[0\]\.type",
        ),
        (
            "block",
            {"blocks": [{"shape": "rectangle"}]},
            lambda ir: ir["blocks"][0].__setitem__("shape", "cloud"),
            r"blocks\[0\]\.shape",
        ),
    ],
)
def test_canonical_key_revalidates_mutated_phase_two_native_nested_contract(
    diagram_type: str,
    ir: dict[str, object],
    mutation,
    location: str,
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)
    mutation(candidate.ir)

    with pytest.raises(ValidationError, match=location):
        candidate.canonical_key()


def test_generic_candidate_envelopes_apply_phase_two_native_nested_contracts() -> None:
    prediction = DiagramTypePrediction(candidates=["requirement"], scores=[1.0])

    with pytest.raises(ValidationError, match=r"requirements\[0\]\.risk"):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[
                {
                    "diagram_type": "requirement",
                    "ir": {"requirements": [{"risk": "urgent"}]},
                }
            ],
        )

    with pytest.raises(ValidationError, match=r"blocks\[0\]\.shape"):
        MermaidCandidate(
            candidate_id="candidate-block",
            generation_method="typed_ir",
            diagram_type="block",
            typed_ir={"blocks": [{"shape": "cloud"}]},
        )


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        ("pie", {"slices": {"label": "A", "value": 1}}, "slices"),
        ("pie", {"slices": ["not-an-object"]}, "slices[0]"),
        ("pie", {"slices": [{"label": 1}]}, "slices[0].label"),
        ("pie", {"slices": [{"value": "1"}]}, "slices[0].value"),
        ("pie", {"slices": [{"value": True}]}, "slices[0].value"),
        ("pie", {"slices": [{"bbox": [0, 0, 10]}]}, "slices[0].bbox"),
        ("pie", {"slices": [{"evidence_ids": [1]}]}, "slices[0].evidence_ids[0]"),
        ("pie", {"slices": [], "show_data": 1}, "show_data"),
        (
            "xychart",
            {"x_axis": [], "y_axis": {}, "series": []},
            "x_axis",
        ),
        (
            "xychart",
            {"x_axis": {"categories": "Q1"}, "y_axis": {}, "series": []},
            "x_axis.categories",
        ),
        (
            "xychart",
            {"x_axis": {"categories": [1]}, "y_axis": {}, "series": []},
            "x_axis.categories[0]",
        ),
        (
            "xychart",
            {"x_axis": {"label": 1}, "y_axis": {}, "series": []},
            "x_axis.label",
        ),
        (
            "xychart",
            {"x_axis": {"min": "0"}, "y_axis": {}, "series": []},
            "x_axis.min",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {"max": True}, "series": []},
            "y_axis.max",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": "not-a-list"},
            "series",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": ["not-an-object"]},
            "series[0]",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"kind": ["line"]}]},
            "series[0].kind",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"kind": "area"}]},
            "series[0].kind",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"values": "1,2"}]},
            "series[0].values",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"values": [True]}]},
            "series[0].values[0]",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"points": ["0,1"]}]},
            "series[0].points[0]",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"points": [{"x": "0"}]}]},
            "series[0].points[0].x",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"points": [{"y": True}]}]},
            "series[0].points[0].y",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"bbox": [0, False, 10, 10]}]},
            "series[0].bbox",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"evidence_ids": [1]}]},
            "series[0].evidence_ids[0]",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": "not-a-list"},
            "points",
        ),
        (
            "quadrant",
            {"x_axis": {"low": 0}, "y_axis": {}, "points": []},
            "x_axis.low",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {"high": ["High"]}, "points": []},
            "y_axis.high",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": ["not-an-object"]},
            "points[0]",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"label": 1}]},
            "points[0].label",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"x": "0.5"}]},
            "points[0].x",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"y": True}]},
            "points[0].y",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"bbox": [0, 0, "10", 10]}]},
            "points[0].bbox",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"evidence_ids": [1]}]},
            "points[0].evidence_ids[0]",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [], "quadrants": "Q1"},
            "quadrants",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [], "quadrants": [1]},
            "quadrants",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [], "quadrants": {"1": 1}},
            "quadrants",
        ),
    ],
)
def test_phase_three_core_chart_nested_contracts_reject_strict_wrong_shapes(
    diagram_type: str,
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert location in str(exc_info.value)


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        ("pie", {"slices": [{"value": math.inf}]}),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"points": [{"y": math.nan}]}]},
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"x": -math.inf}]},
        ),
    ],
)
def test_phase_three_core_chart_contracts_reject_non_finite_numbers_at_canonical_boundary(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="finite and bounded"):
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "pie",
            {
                "show_data": True,
                "slices": [
                    {
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-a"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "xychart",
            {
                "x_axis": {
                    "label": "Quarter",
                    "categories": [],
                    "future_metadata": {"kept": True},
                },
                "y_axis": {"min": 0, "future_metadata": {"kept": True}},
                "series": [
                    {
                        "kind": "LINE",
                        "values": [],
                        "points": [],
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["series-1"],
                        "name": "forward-compatible diagnostic",
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "quadrant",
            {
                "x_axis": {"low": "Low", "future_metadata": {"kept": True}},
                "y_axis": {},
                "points": [
                    {
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["point-1"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "quadrants": {"QUADRANT-1": "Expand", "2": "Promote"},
                "future_root_metadata": {"kept": True},
            },
        ),
    ],
)
def test_phase_three_core_chart_nested_contracts_preserve_partial_and_extra_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize(
    ("diagram_type", "ir", "mutation", "location"),
    [
        (
            "pie",
            {"slices": [{"label": "A", "value": 1}]},
            lambda ir: ir["slices"][0].__setitem__("label", 1),
            r"slices\[0\]\.label",
        ),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"kind": "LINE"}]},
            lambda ir: ir["series"][0].__setitem__("kind", "area"),
            r"series\[0\]\.kind",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"x": 0.5}]},
            lambda ir: ir["points"][0].__setitem__("x", "0.5"),
            r"points\[0\]\.x",
        ),
    ],
)
def test_canonical_key_revalidates_mutated_phase_three_core_chart_contracts(
    diagram_type: str,
    ir: dict[str, object],
    mutation,
    location: str,
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)
    mutation(candidate.ir)

    with pytest.raises(ValidationError, match=location):
        candidate.canonical_key()


@pytest.mark.parametrize(
    ("diagram_type", "invalid_ir", "location"),
    [
        ("pie", {"slices": [], "show_data": 1}, r"show_data"),
        (
            "xychart",
            {"x_axis": {}, "y_axis": {}, "series": [{"kind": "area"}]},
            r"series\[0\]\.kind",
        ),
        (
            "quadrant",
            {"x_axis": {}, "y_axis": {}, "points": [{"x": "0.5"}]},
            r"points\[0\]\.x",
        ),
    ],
)
def test_generic_candidate_envelopes_apply_phase_three_core_chart_contracts(
    diagram_type: str,
    invalid_ir: dict[str, object],
    location: str,
) -> None:
    prediction = DiagramTypePrediction(candidates=[diagram_type], scores=[1.0])

    with pytest.raises(ValidationError, match=location):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[{"diagram_type": diagram_type, "ir": invalid_ir}],
        )

    with pytest.raises(ValidationError, match=location):
        MermaidCandidate(
            candidate_id=f"candidate-{diagram_type}",
            generation_method="typed_ir",
            diagram_type=diagram_type,
            typed_ir=invalid_ir,
        )


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        ("sankey", {"nodes": ["node"], "flows": []}, "nodes[0]"),
        (
            "sankey",
            {"nodes": [], "flows": [{"source": [], "target": "B", "value": 1}]},
            "flows[0].source",
        ),
        (
            "sankey",
            {"nodes": [], "flows": [{"source": "A", "target": "B", "value": "1"}]},
            "flows[0].value",
        ),
        (
            "sankey",
            {"nodes": [], "flows": [{"source": "A", "target": "B", "value": True}]},
            "flows[0].value",
        ),
        (
            "radar",
            {"dimensions": [{"id": 1}], "series": []},
            "dimensions[0].id",
        ),
        (
            "radar",
            {"dimensions": [], "series": [{"values": ["1"]}]},
            "series[0].values[0]",
        ),
        (
            "radar",
            {"dimensions": [], "series": [{"values": [True]}]},
            "series[0].values[0]",
        ),
        (
            "radar",
            {"dimensions": [], "series": [], "ticks": True},
            "ticks",
        ),
        (
            "radar",
            {"dimensions": [], "series": [], "show_legend": 1},
            "show_legend",
        ),
        (
            "radar",
            {"dimensions": [], "series": [], "graticule": "CIRCLE"},
            "graticule",
        ),
        ("treemap", {"root": {"children": "child"}}, "root.children"),
        ("treemap", {"root": {"label": 1}}, "root.label"),
        ("treemap", {"root": {"value": "1"}}, "root.value"),
        ("treemap", {"root": {"value": True}}, "root.value"),
        (
            "treemap",
            {"root": {"children": [{"evidence_ids": [1]}]}},
            "root.children[0].evidence_ids[0]",
        ),
        ("venn", {"sets": [{"id": 1}], "intersections": []}, "sets[0].id"),
        (
            "venn",
            {"sets": [{"value": "1"}], "intersections": []},
            "sets[0].value",
        ),
        (
            "venn",
            {"sets": [], "intersections": [{"sets": "A"}]},
            "intersections[0].sets",
        ),
        (
            "venn",
            {"sets": [], "intersections": [{"sets": ["A", 1]}]},
            "intersections[0].sets[1]",
        ),
        (
            "venn",
            {"sets": [], "intersections": [{"value": True}]},
            "intersections[0].value",
        ),
    ],
)
def test_phase_three_extended_chart_contracts_reject_strict_wrong_shapes(
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
            "sankey",
            {"nodes": [], "flows": [{"source": "A", "target": "B", "value": math.inf}]},
        ),
        (
            "radar",
            {"dimensions": [], "series": [{"values": [math.nan]}]},
        ),
        ("treemap", {"root": {"children": [{"value": -math.inf}]}}),
        (
            "venn",
            {"sets": [], "intersections": [{"sets": [], "value": math.inf}]},
        ),
    ],
)
def test_phase_three_extended_chart_contracts_reject_non_finite_numbers(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="finite and bounded"):
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        (
            "sankey",
            {"nodes": [{"bbox": [0, 0, 10]}], "flows": []},
            "nodes[0].bbox",
        ),
        (
            "radar",
            {"dimensions": [], "series": [{"evidence_ids": [1]}]},
            "series[0].evidence_ids[0]",
        ),
        (
            "treemap",
            {"root": {"children": [{"bbox": [0, 0, "10", 10]}]}},
            "root.children[0].bbox",
        ),
        (
            "venn",
            {"sets": [], "intersections": [{"bbox": [0, 0, 10, True]}]},
            "intersections[0].bbox",
        ),
    ],
)
def test_phase_three_extended_chart_records_require_strict_bbox_and_evidence(
    diagram_type: str,
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError, match=location.replace("[", r"\[").replace("]", r"\]")):
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "sankey",
            {
                "nodes": [
                    {
                        "id": "source",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-source"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "flows": [],
                "links": [
                    {
                        "id": "compat-link",
                        "source": "source",
                        "target": "sink",
                        "value": 1,
                        "future_metadata": {"kept": True},
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "radar",
            {
                "dimensions": [
                    {
                        "id": "speed",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-speed"],
                    }
                ],
                "axes": [{"id": "compat-axis", "future_metadata": {"kept": True}}],
                "series": [
                    {
                        "id": "v1",
                        "values": [],
                        "bbox": [10, 0, 20, 10],
                        "evidence_ids": ["line-v1"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "min": 0,
                "max": 1,
                "ticks": 4,
                "show_legend": False,
                "graticule": "polygon",
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "treemap",
            {
                "root": {
                    "id": "root",
                    "name": "Compatibility name",
                    "bbox": [0, 0, 20, 20],
                    "evidence_ids": ["contour-root"],
                    "children": [
                        {
                            "id": "leaf",
                            "value": 1,
                            "bbox": [1, 1, 5, 5],
                            "evidence_ids": ["ocr-leaf"],
                            "future_metadata": {"kept": True},
                        }
                    ],
                    "future_metadata": {"kept": True},
                },
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "venn",
            {
                "sets": [
                    {
                        "id": "A",
                        "name": "Compatibility name",
                        "value": 1,
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-a"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "intersections": [
                    {
                        "id": "AB",
                        "sets": ["A", "B"],
                        "name": "Compatibility intersection",
                        "value": 0,
                        "bbox": [5, 0, 15, 10],
                        "evidence_ids": ["ocr-ab"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
    ],
)
def test_phase_three_extended_chart_contracts_preserve_alias_extra_and_original_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir
    assert candidate.ir is not ir


@pytest.mark.parametrize(
    ("diagram_type", "ir", "root_field"),
    [
        ("sankey", {"nodes": [], "links": []}, "flows"),
        ("radar", {"axes": [], "series": []}, "dimensions"),
    ],
)
def test_phase_three_extended_chart_aliases_do_not_replace_canonical_roots(
    diagram_type: str,
    ir: dict[str, object],
    root_field: str,
) -> None:
    with pytest.raises(ValidationError, match=rf"requires root field '{root_field}'"):
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)


@pytest.mark.parametrize(
    ("diagram_type", "ir", "mutation", "location"),
    [
        (
            "sankey",
            {"nodes": [], "flows": [{"value": 1}]},
            lambda ir: ir["flows"][0].__setitem__("value", "1"),
            r"flows\[0\]\.value",
        ),
        (
            "radar",
            {"dimensions": [], "series": [{"values": [1]}]},
            lambda ir: ir["series"][0]["values"].__setitem__(0, True),
            r"series\[0\]\.values\[0\]",
        ),
        (
            "treemap",
            {"root": {"children": [{"value": 1}]}},
            lambda ir: ir["root"]["children"][0].__setitem__("value", "1"),
            r"root\.children\[0\]\.value",
        ),
        (
            "venn",
            {"sets": [], "intersections": [{"sets": ["A"]}]},
            lambda ir: ir["intersections"][0].__setitem__("sets", "A"),
            r"intersections\[0\]\.sets",
        ),
    ],
)
def test_canonical_key_revalidates_mutated_phase_three_extended_chart_contracts(
    diagram_type: str,
    ir: dict[str, object],
    mutation,
    location: str,
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)
    mutation(candidate.ir)

    with pytest.raises(ValidationError, match=location):
        candidate.canonical_key()


@pytest.mark.parametrize(
    ("diagram_type", "invalid_ir", "location"),
    [
        (
            "sankey",
            {"nodes": [], "flows": [{"value": "1"}]},
            r"flows\[0\]\.value",
        ),
        (
            "radar",
            {"dimensions": [], "series": [], "graticule": "square"},
            "graticule",
        ),
        ("treemap", {"root": {"children": ["leaf"]}}, r"root\.children\[0\]"),
        (
            "venn",
            {"sets": [], "intersections": [{"sets": [1]}]},
            r"intersections\[0\]\.sets\[0\]",
        ),
    ],
)
def test_generic_candidate_envelopes_apply_phase_three_extended_chart_contracts(
    diagram_type: str,
    invalid_ir: dict[str, object],
    location: str,
) -> None:
    prediction = DiagramTypePrediction(candidates=[diagram_type], scores=[1.0])

    with pytest.raises(ValidationError, match=location):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[{"diagram_type": diagram_type, "ir": invalid_ir}],
        )

    with pytest.raises(ValidationError, match=location):
        MermaidCandidate(
            candidate_id=f"candidate-{diagram_type}",
            generation_method="typed_ir",
            diagram_type=diagram_type,
            typed_ir=invalid_ir,
        )


@pytest.mark.parametrize(
    ("diagram_type", "ir", "location"),
    [
        ("journey", {"sections": ["section"]}, "sections[0]"),
        ("journey", {"sections": [{"tasks": ["task"]}]}, "sections[0].tasks[0]"),
        ("journey", {"sections": [{"title": 1}]}, "sections[0].title"),
        ("journey", {"sections": [{"tasks": [{"text": 1}]}]}, "tasks[0].text"),
        ("journey", {"sections": [{"tasks": [{"score": "5"}]}]}, "tasks[0].score"),
        ("journey", {"sections": [{"tasks": [{"actors": "User"}]}]}, "tasks[0].actors"),
        ("journey", {"sections": [{"tasks": [{"actors": [1]}]}]}, "actors[0]"),
        ("journey", {"sections": [{"bbox": [0, 0, 10]}]}, "sections[0].bbox"),
        ("journey", {"sections": [{"evidence_ids": [1]}]}, "evidence_ids[0]"),
        ("kanban", {"columns": ["column"], "cards": []}, "columns[0]"),
        ("kanban", {"columns": [], "cards": ["card"]}, "cards[0]"),
        ("kanban", {"columns": [{"id": 1}], "cards": []}, "columns[0].id"),
        ("kanban", {"columns": [{"title": []}], "cards": []}, "columns[0].title"),
        ("kanban", {"columns": [], "cards": [{"text": 1}]}, "cards[0].text"),
        ("kanban", {"columns": [], "cards": [{"column_id": 1}]}, "cards[0].column_id"),
        (
            "kanban",
            {"columns": [], "cards": [{"bbox": [0, 0, 10, True]}]},
            "cards[0].bbox",
        ),
        (
            "kanban",
            {"columns": [{"evidence_ids": [1]}], "cards": []},
            "evidence_ids[0]",
        ),
        ("gitgraph", {"initial_branch": "main", "operations": ["commit"]}, "operations[0]"),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"type": 1}]},
            "operations[0].type",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"id": 1}]},
            "operations[0].id",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"branch": 1}]},
            "operations[0].branch",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"tag": 1}]},
            "operations[0].tag",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"commit_type": 1}]},
            "operations[0].commit_type",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"style": 1}]},
            "operations[0].style",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"name": 1}]},
            "operations[0].name",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"from": 1}]},
            "operations[0].from",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"source": 1}]},
            "operations[0].source",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"target": 1}]},
            "operations[0].target",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"order": "1"}]},
            "operations[0].order",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"order": True}]},
            "operations[0].order",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"bbox": [0, 0, "10", 10]}]},
            "operations[0].bbox",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"evidence_ids": [1]}]},
            "evidence_ids[0]",
        ),
    ],
)
def test_planning_nested_contracts_reject_non_objects_and_strict_known_types(
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
    ("ir", "message"),
    [
        ({"operations": []}, "requires root field 'initial_branch'"),
        (
            {"initial_branch": 1, "operations": []},
            "field 'initial_branch' must be a string",
        ),
    ],
)
def test_gitgraph_contract_requires_initial_branch_string_root(
    ir: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        TypedIRCandidate(diagram_type="gitgraph", ir=ir)


@pytest.mark.parametrize(
    ("ir", "location"),
    [
        ({"initial_branch": "main", "direction": "RL", "operations": []}, "direction"),
        (
            {"initial_branch": "main", "operations": [{"type": "checkout"}]},
            "operations[0].type",
        ),
        (
            {"initial_branch": "main", "operations": [{"commit_type": "FAST"}]},
            "operations[0].commit_type",
        ),
        (
            {"initial_branch": "main", "operations": [{"style": "RED"}]},
            "operations[0].style",
        ),
    ],
)
def test_gitgraph_nested_contract_rejects_unsupported_closed_tokens(
    ir: dict[str, object],
    location: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        TypedIRCandidate(diagram_type="gitgraph", ir=ir)

    message = str(exc_info.value)
    assert "unsupported token" in message
    assert location in message


def test_gitgraph_nested_contract_accepts_lowercase_closed_tokens_without_rewriting() -> None:
    ir = {
        "initial_branch": "main",
        "direction": "lr",
        "operations": [
            {"type": "commit", "commit_type": "normal"},
            {"type": "branch", "commit_type": "reverse"},
            {"type": "merge", "style": "highlight"},
        ],
    }

    candidate = TypedIRCandidate(diagram_type="gitgraph", ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "journey",
            {
                "sections": [
                    {
                        "label": "Discover",
                        "tasks": [
                            {
                                "id": "find",
                                "text": "Find product",
                                "score": 3,
                                "actors": ["Customer"],
                                "bbox": [0, 0, 10, 10],
                                "evidence_ids": ["ocr-find"],
                                "future_metadata": {"kept": True},
                            }
                        ],
                        "future_metadata": {"kept": True},
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "kanban",
            {
                "columns": [
                    {
                        "id": "todo",
                        "title": "To do",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-todo"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "cards": [
                    {
                        "id": "card-1",
                        "text": "Write tests",
                        "column_id": "todo",
                        "future_metadata": {"kept": True},
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
        (
            "gitgraph",
            {
                "initial_branch": "main",
                "operations": [
                    {
                        "type": "branch",
                        "id": "feature",
                        "from": "main",
                        "style": "highlight",
                        "bbox": [0, 0, 10, 10],
                        "evidence_ids": ["ocr-feature"],
                        "future_metadata": {"kept": True},
                    }
                ],
                "future_root_metadata": {"kept": True},
            },
        ),
    ],
)
def test_planning_contracts_preserve_compatibility_aliases_extra_and_original_ir(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir
    assert candidate.ir is not ir


@pytest.mark.parametrize(
    ("diagram_type", "ir", "root_field"),
    [
        ("journey", {"tasks": []}, "sections"),
        ("kanban", {"lanes": [], "cards": []}, "columns"),
        ("kanban", {"columns": [], "items": []}, "cards"),
        ("gitgraph", {"branch": "main", "operations": []}, "initial_branch"),
        ("gitgraph", {"initial_branch": "main", "commits": []}, "operations"),
    ],
)
def test_planning_aliases_do_not_replace_canonical_roots(
    diagram_type: str,
    ir: dict[str, object],
    root_field: str,
) -> None:
    with pytest.raises(ValidationError, match=rf"requires root field '{root_field}'"):
        TypedIRCandidate(diagram_type=diagram_type, ir=ir)


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "journey",
            {"sections": [{"title": "", "tasks": [{"label": "", "score": 0, "actors": []}]}]},
        ),
        (
            "kanban",
            {
                "columns": [{"id": "todo", "label": "To do"}],
                "cards": [{"id": "todo", "label": "Card", "column_id": "missing"}],
            },
        ),
        (
            "gitgraph",
            {
                "initial_branch": "release",
                "operations": [
                    {"type": "branch", "name": "feature", "from": "missing", "order": -1}
                ],
            },
        ),
    ],
)
def test_planning_contracts_leave_semantic_requiredness_to_serializer(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)

    assert candidate.ir == ir


@pytest.mark.parametrize(
    ("diagram_type", "ir", "mutation", "location"),
    [
        (
            "journey",
            {"sections": [{"tasks": [{"score": 3}]}]},
            lambda ir: ir["sections"][0]["tasks"][0].__setitem__("score", True),
            r"sections\[0\]\.tasks\[0\]\.score",
        ),
        (
            "kanban",
            {"columns": [{"id": "todo"}], "cards": []},
            lambda ir: ir["columns"][0].__setitem__("id", 1),
            r"columns\[0\]\.id",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "operations": [{"type": "branch", "order": 1}]},
            lambda ir: ir["operations"][0].__setitem__("order", True),
            r"operations\[0\]\.order",
        ),
    ],
)
def test_canonical_key_revalidates_mutated_planning_contracts(
    diagram_type: str,
    ir: dict[str, object],
    mutation,
    location: str,
) -> None:
    candidate = TypedIRCandidate(diagram_type=diagram_type, ir=ir)
    mutation(candidate.ir)

    with pytest.raises(ValidationError, match=location):
        candidate.canonical_key()


@pytest.mark.parametrize(
    ("diagram_type", "invalid_ir", "location"),
    [
        (
            "journey",
            {"sections": [{"tasks": [{"score": True}]}]},
            r"sections\[0\]\.tasks\[0\]\.score",
        ),
        (
            "kanban",
            {"columns": [], "cards": [{"text": {"invalid": True}}]},
            r"cards\[0\]\.text",
        ),
        (
            "gitgraph",
            {"initial_branch": "main", "direction": "RL", "operations": []},
            "direction",
        ),
    ],
)
def test_generic_candidate_envelopes_apply_planning_nested_contracts(
    diagram_type: str,
    invalid_ir: dict[str, object],
    location: str,
) -> None:
    prediction = DiagramTypePrediction(candidates=[diagram_type], scores=[1.0])

    with pytest.raises(ValidationError, match=location):
        EngineObservation(
            prediction=prediction,
            typed_candidates=[{"diagram_type": diagram_type, "ir": invalid_ir}],
        )

    with pytest.raises(ValidationError, match=location):
        MermaidCandidate(
            candidate_id=f"candidate-{diagram_type}",
            generation_method="typed_ir",
            diagram_type=diagram_type,
            typed_ir=invalid_ir,
        )


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
