from __future__ import annotations

import copy
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_SCENE_RELATIONS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import (
    SerializationError,
    enrich_timeline_accessibility_ir,
    plan_timeline_records,
    serialize_timeline,
    serialize_typed_ir_result,
    validated_timeline_accessibility_ir,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


def _timeline_ir(
    *,
    period: object = "Q1",
    label: object = "Launch",
) -> dict[str, object]:
    return {
        "title": "Roadmap",
        "events": [
            {
                "id": "launch",
                "time": period,
                "events": [label],
                "bbox": [10, 10, 90, 40],
                "evidence_ids": ["ocr-launch"],
            }
        ],
    }


class _TimelineRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="timeline",
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"/>',
        )

    def close(self) -> None:
        pass


class _TimelineLabelRepair:
    name = "timeline_label_repair"

    def __init__(self, label: str) -> None:
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["events"][0]["events"] = [self.label]
        serialized = serialize_typed_ir_result("timeline", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _TimelineMetadataRepair:
    name = "timeline_metadata_repair"

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["acc_title"] = " "
        return RepairProposal(
            code=f"{candidate.mermaid_code.rstrip()}\n    Q2 : Phantom\n",
            operation=self.name,
            typed_ir=typed_ir,
        )


def _timeline_observation(*, label: str, evidence_text: str) -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["timeline"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="timeline", ir=_timeline_ir(label=label))],
        evidence=[
            VisualEvidence(
                id="ocr-launch",
                kind="ocr_token",
                text=evidence_text,
                bbox=(10, 10, 90, 40),
            )
        ],
    )


def test_timeline_plan_freezes_exact_semantic_source_canvas_without_mutation() -> None:
    hostile = (
        '  title\tQ:1 # ; "quoted" \\ &amp; <tag> https://example.test '
        "callback(x) click style %%{init} iconify 한국어\r\nnext  "
    )
    ir = _timeline_ir(period=hostile, label=f"Event {hostile}")
    title_hostile = " ".join(hostile.split())
    ir["title"] = f"Roadmap {title_hostile}"
    original = copy.deepcopy(ir)

    plan = plan_timeline_records(ir)
    code = serialize_timeline(ir)

    semantic = " ".join(hostile.split())
    assert ir == original
    assert plan.title is not None
    assert plan.title.semantic == f"Roadmap {semantic}"
    assert plan.title.canvas == f"Roadmap {semantic}"
    assert plan.events[0].period.semantic == semantic
    assert plan.events[0].period.canvas == semantic
    assert plan.events[0].labels[0].semantic == f"Event {semantic}"
    assert plan.events[0].labels[0].canvas == f"Event {semantic}"
    assert plan.events[0].source_id == "launch"
    assert plan.events[0].scene_id == "timeline_event_1"
    assert "\u200b" in plan.events[0].period.source
    assert hostile not in code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_timeline_plan_resolves_aliases_and_unreadable_fallbacks() -> None:
    plan = plan_timeline_records(
        {
            "events": [
                {"id": "one", "period": "Q1", "label": "Launch"},
                {"id": "two", "time": "Q2", "events": []},
                {
                    "id": "three",
                    "time": " Q3 ",
                    "period": "Q3",
                    "label": "Beta",
                    "events": [" Beta "],
                },
            ]
        }
    )

    assert [event.source_id for event in plan.events] == ["one", "two", "three"]
    assert [event.scene_id for event in plan.events] == [
        "timeline_event_1",
        "timeline_event_2",
        "timeline_event_3",
    ]
    assert [event.period.semantic for event in plan.events] == ["Q1", "Q2", "Q3"]
    assert [[label.semantic for label in event.labels] for event in plan.events] == [
        ["Launch"],
        ["[unreadable]"],
        ["Beta"],
    ]


@pytest.mark.parametrize(
    ("event", "error"),
    [
        ({"time": "Q1", "period": "Q2", "label": "Launch"}, "conflicting"),
        (
            {"time": "Q1", "label": "Launch", "events": ["Beta"]},
            "conflicting",
        ),
        ({"time": 1, "label": "Launch"}, "must be text"),
        ({"time": "Q1", "events": "Launch"}, "must be a list"),
        ({"time": "Q1", "events": [None]}, "must be text"),
        ({"time": " ", "label": "Launch"}, "bounded non-empty"),
    ],
)
def test_timeline_alias_and_record_errors_fail_closed(
    event: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(SerializationError, match=error):
        plan_timeline_records({"events": [event]})


def test_timeline_duplicate_source_ids_fail_closed() -> None:
    with pytest.raises(SerializationError, match="ids must be unique"):
        plan_timeline_records(
            {
                "events": [
                    {"id": "same", "time": "Q1", "label": "One"},
                    {"id": "same", "time": "Q2", "label": "Two"},
                ]
            }
        )


def test_timeline_scene_ocr_direction_identity_and_provenance_share_plan() -> None:
    ir = {
        "title": "Roadmap #1;",
        "direction": "RL",
        "events": [
            {
                "id": "title",
                "time": "Q: 1 #1;",
                "events": ["Launch #2;", "Beta: ready"],
                "role": "admin",
                "shape": "circle",
                "text": "Hidden",
                "bbox": [10, 10, 90, 40],
                "evidence_ids": ["ocr-event"],
            }
        ],
    }

    plan = plan_timeline_records(ir)
    scene = typed_ir_to_scene("timeline", ir)

    assert scene is not None
    assert scene.reading_direction == "timeline"
    assert len(scene.elements) == 1
    element = scene.elements[0]
    assert element.id == plan.events[0].scene_id == "timeline_event_1"
    assert element.role == "event"
    assert element.text == "Q: 1 #1;"
    assert element.shape is None
    assert element.bbox == (10.0, 10.0, 90.0, 40.0)
    assert element.evidence_ids == ["ocr-event"]
    assert list(typed_ir_semantic_texts("timeline", ir, scene)) == [
        "Roadmap #1;",
        "Q: 1 #1;",
        "Launch #2;",
        "Beta: ready",
    ]


def test_timeline_scene_rejects_the_same_malformed_plan_as_serializer() -> None:
    malformed = {"events": [{"time": "title Foo", "events": "Launch"}]}

    with pytest.raises(SerializationError):
        serialize_timeline(malformed)
    assert typed_ir_to_scene("timeline", malformed) is None


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_timeline_exact_empty_metadata_is_omitted_without_mutation(field: str) -> None:
    ir = {**_timeline_ir(), field: ""}
    original = copy.deepcopy(ir)

    validated = validated_timeline_accessibility_ir(ir)
    enriched = enrich_timeline_accessibility_ir(ir, experimental=False)
    result = serialize_typed_ir_result("timeline", ir)

    assert ir == original
    assert field not in validated
    if field in {"title", "description"}:
        assert field not in enriched
    else:
        assert enriched[field]
    assert enriched["acc_title"]
    assert enriched["acc_description"]
    assert "Q1: Launch" in enriched["acc_description"]
    assert "accTitle:" not in result.code
    assert "accDescr:" not in result.code
    assert any("cannot safely emit accTitle/accDescr" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", False),
        ("description", []),
        ("acc_title", " "),
        ("acc_description", "A\nB"),
        ("title", "A\x00B"),
        ("description", "A\u200bB"),
        ("acc_title", "A\ud800B"),
    ],
)
def test_timeline_raw_metadata_rejects_nontext_whitespace_and_control(
    field: str,
    value: object,
) -> None:
    ir = {**_timeline_ir(), field: value}

    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        validated_timeline_accessibility_ir(ir)
    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_typed_ir_result("timeline", ir)


def test_timeline_pipeline_stores_raw_snapshot_not_generated_accessibility() -> None:
    observation = _timeline_observation(label="Launch", evidence_text="Q1 Launch")
    source_ir = observation.typed_candidates[0].ir
    source_snapshot = copy.deepcopy(source_ir)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_TimelineRuntime(), config.security_profile),
    ).reconstruct(
        "timeline-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert source_ir == source_snapshot
    assert result.selected is not None
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir


def test_timeline_pipeline_rejects_raw_metadata_before_runtime() -> None:
    observation = _timeline_observation(label="Launch", evidence_text="Q1 Launch")
    observation.typed_candidates[0].ir["acc_title"] = " "
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _TimelineRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "timeline-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is None
    assert runtime.calls == []
    assert any(failure.stage == "serialization" for failure in result.failures)


def test_timeline_accepted_repair_regenerates_accessibility_from_raw_snapshot() -> None:
    observation = _timeline_observation(label="Lanch", evidence_text="Q1 Launch")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_TimelineRuntime(), config.security_profile),
        repair_engine=_TimelineLabelRepair("Launch"),
    ).reconstruct(
        "timeline-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["events"][0]["events"] == ["Launch"]
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    repaired_accessibility = enrich_timeline_accessibility_ir(
        result.selected.typed_ir,
        experimental=True,
    )
    assert "Q1: Launch" in repaired_accessibility["acc_description"]
    assert "Lanch" not in repaired_accessibility["acc_description"]


def test_timeline_repair_rejects_invalid_raw_metadata_before_second_runtime() -> None:
    observation = _timeline_observation(label="Lanch", evidence_text="Q1 Launch")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _TimelineRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_TimelineMetadataRepair(),
    ).reconstruct(
        "timeline-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert len(runtime.calls) == 1
    assert not result.selected.repair_history[-1].accepted
    assert any(
        warning == "semantic repair IR could not be serialized: SerializationError"
        for warning in result.selected.warnings
    )


def test_timeline_shared_plan_enforces_source_line_and_utf16_budgets() -> None:
    line_heavy_ir = {
        "events": [
            {"id": f"event-{index}", "time": f"P{index}", "label": "E"} for index in range(4_999)
        ]
    }
    character_heavy_ir = {"events": [{"time": "Q1", "label": "A" * 13_000}]}
    utf16_heavy_ir = {"events": [{"time": "Q1", "label": "😀" * 24_990}]}
    label_heavy_ir = {
        "events": [
            {
                "time": "Q1",
                "events": ["E"] * (MAX_SCENE_RELATIONS + 1),
            }
        ]
    }

    with pytest.raises(SerializationError, match="source-line limit"):
        plan_timeline_records(line_heavy_ir)
    with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
        plan_timeline_records(character_heavy_ir)
    with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
        plan_timeline_records(utf16_heavy_ir)
    with pytest.raises(SerializationError, match="visible event label count"):
        plan_timeline_records(label_heavy_ir)
    assert typed_ir_to_scene("timeline", line_heavy_ir) is None
    assert typed_ir_to_scene("timeline", character_heavy_ir) is None
    assert typed_ir_to_scene("timeline", utf16_heavy_ir) is None
    assert typed_ir_to_scene("timeline", label_heavy_ir) is None


@pytest.mark.integration
def test_timeline_mermaid_11_16_svg_matches_exact_terminal_canvas() -> None:
    hostile = (
        'title Q:1 # ; "quoted" \\ &amp; &#35; #59; <tag> % %%{init} '
        "https://evil.invalid/x callback(x) click style iconify 한국어"
    )
    ir = {
        "title": f"Roadmap {hostile}",
        "events": [
            {
                "id": "hostile",
                "time": hostile,
                "events": [f"Launch {hostile}", f"Beta {hostile}"],
            }
        ],
    }
    plan = plan_timeline_records(ir)
    result = serialize_typed_ir_result("timeline", ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    root = ET.fromstring(outcome.runtime.svg)
    visible_text = " ".join(" ".join(root.itertext()).replace("\u200b", "").split())
    assert plan.title is not None and plan.title.canvas in visible_text
    assert plan.events[0].period.canvas in visible_text
    for label in plan.events[0].labels:
        assert label.canvas in visible_text
    assert not any(element.tag.rsplit("}", 1)[-1] in {"title", "desc"} for element in list(root))
