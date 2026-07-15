from __future__ import annotations

import copy
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_SCENE_ELEMENTS,
    MAX_SCENE_GROUPS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import (
    GANTT_TEXT_COMPATIBILITY_WARNING,
    SerializationError,
    enrich_gantt_accessibility_ir,
    plan_gantt_accessibility,
    plan_gantt_records,
    serialize_gantt,
    serialize_typed_ir_result,
    validated_gantt_metadata_ir,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

_ZERO_WIDTH_SPACE = "\u200b"


class _GanttRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="gantt",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
                "<text>Build ship</text></svg>"
            ),
        )

    def close(self) -> None:
        pass


class _GanttLabelRepair:
    name = "gantt_label_repair"

    def __init__(self, label: str) -> None:
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["sections"][0]["tasks"][0]["label"] = self.label
        serialized = serialize_typed_ir_result("gantt", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _GanttMetadataRepair:
    name = "gantt_metadata_repair"

    def __init__(
        self,
        field: str,
        value: object,
        *,
        serialize: bool,
        task_label: str | None = None,
    ) -> None:
        self.field = field
        self.value = value
        self.serialize = serialize
        self.task_label = task_label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir[self.field] = self.value
        if self.task_label is not None:
            typed_ir["sections"][0]["tasks"][0]["label"] = self.task_label
        code = (
            serialize_typed_ir_result("gantt", typed_ir, experimental=True).code
            if self.serialize
            else f"{candidate.mermaid_code.rstrip()}\n    todayMarker off\n"
        )
        return RepairProposal(code=code, operation=self.name, typed_ir=typed_ir)


class _ExplosiveText:
    def __bool__(self) -> bool:
        raise AssertionError("text truthiness hook must not run")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("text equality hook must not run")

    def __str__(self) -> str:
        raise AssertionError("text coercion hook must not run")


class _StringSubclass(str):
    pass


def _gantt_ir(
    *,
    task_label: object = "Build",
    section_title: object = "Delivery",
    title: object | None = None,
) -> dict:
    ir = {
        "date_format": "YYYY-MM-DD",
        "sections": [
            {
                "id": "delivery",
                "title": section_title,
                "tasks": [
                    {
                        "id": "build",
                        "label": task_label,
                        "status": "crit, done",
                        "start": "2026-07-01",
                        "end": "2026-07-02",
                        "bbox": [10, 10, 90, 40],
                        "evidence_ids": ["ocr-build"],
                    }
                ],
            }
        ],
    }
    if title is not None:
        ir["title"] = title
    return ir


def _gantt_observation(*, task_label: str, evidence_text: str) -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["gantt"], scores=[1]),
        typed_candidates=[
            TypedIRCandidate(diagram_type="gantt", ir=_gantt_ir(task_label=task_label))
        ],
        evidence=[
            VisualEvidence(
                id="ocr-build",
                kind="ocr_token",
                text=f"{evidence_text} 2026-07-01 2026-07-02",
                bbox=(10, 10, 90, 40),
            )
        ],
    )


def test_gantt_plan_freezes_terminal_text_without_mutating_semantic_ir() -> None:
    task_label = '  title fake\tBuild: "ship" \\ path &#35; %%{init: true}\r\nnext  '
    section_title = '  Section: "Delivery" \\ path <b>  '
    title = '  Plan: "Q3" \\ path <title>  '
    ir = _gantt_ir(task_label=task_label, section_title=section_title, title=title)
    original = copy.deepcopy(ir)

    plan = plan_gantt_records(ir)

    assert ir == original
    assert plan.title is not None
    assert plan.title.semantic == 'Plan: "Q3" \\ path <title>'
    assert plan.title.canvas == 'Plan: "Q3" \\ path ‹title>'
    assert plan.sections[0].semantic_label == 'Section: "Delivery" \\ path <b>'
    assert plan.sections[0].visible_label == 'Section: "Delivery" \\ path <b>'
    assert plan.sections[0].tasks[0].semantic_label == (
        'title fake Build: "ship" \\ path &#35; %%{init: true} next'
    )
    assert plan.sections[0].tasks[0].visible_label == (
        'title fake Build∶ "ship" \\ path &#35; ％％{init∶ true} next'
    )
    assert plan.compatibility_substitutions
    assert plan.sections[0].tasks[0].fields == (
        "crit",
        "done",
        "build",
        "2026-07-01",
        "2026-07-02",
    )


def test_gantt_scene_and_ocr_projection_share_terminal_canvas_text() -> None:
    ir = _gantt_ir(
        task_label="Build: 50% &#35;",
        section_title="Delivery <team>",
        title="Plan <Q3>",
    )
    plan = plan_gantt_records(ir)
    scene = typed_ir_to_scene("gantt", ir)

    assert scene is not None
    assert scene.elements[0].text == plan.sections[0].tasks[0].visible_label
    assert scene.elements[0].evidence_ids == ["ocr-build"]
    assert scene.groups[0].label == plan.sections[0].visible_label
    assert list(typed_ir_semantic_texts("gantt", ir, scene)) == [
        plan.title.canvas,
        plan.sections[0].visible_label,
        plan.sections[0].tasks[0].visible_label,
    ]


def test_gantt_accessibility_uses_planned_labels_not_hidden_aliases_or_ids() -> None:
    ir = _gantt_ir(task_label="")
    ir["sections"][0]["id"] = "internal-section"
    ir["sections"][0]["title"] = "Visible section"
    ir["sections"][0]["tasks"][0]["id"] = "internal-task"
    ir["sections"][0]["tasks"][0]["text"] = "Hidden alias"

    plan = plan_gantt_records(ir)
    accessibility = plan_gantt_accessibility(ir, experimental=False, gantt_plan=plan)
    enriched = enrich_gantt_accessibility_ir(ir, experimental=False, gantt_plan=plan)

    assert "Visible section" in accessibility.description_semantic
    assert "Task 1" in accessibility.description_semantic
    assert "internal-section" not in accessibility.description_semantic
    assert "internal-task" not in accessibility.description_semantic
    assert "Hidden alias" not in accessibility.description_semantic
    assert enriched["acc_description"] == accessibility.description_semantic


@pytest.mark.parametrize(
    ("task_label", "title", "expects_warning"),
    [
        ("Build", "Plan", False),
        ('Build "ship" \\ path', "Plan", False),
        ("Build: 50%", "Plan", True),
        ("Build", "Plan <Q3>", True),
        ("Build &#35;", "Plan", False),
    ],
)
def test_gantt_result_warns_only_for_visible_compatibility_substitutions(
    task_label: str,
    title: str,
    expects_warning: bool,
) -> None:
    result = serialize_typed_ir_result("gantt", _gantt_ir(task_label=task_label, title=title))

    assert (GANTT_TEXT_COMPATIBILITY_WARNING in result.warnings) is expects_warning


def test_gantt_accessibility_only_substitution_adds_compatibility_warning() -> None:
    result = serialize_typed_ir_result(
        "gantt",
        {**_gantt_ir(), "acc_title": "Plan <Q3>"},
    )

    assert GANTT_TEXT_COMPATIBILITY_WARNING in result.warnings


@pytest.mark.parametrize(
    "label",
    [
        "title fake",
        "section fake",
        "dateFormat fake",
        "axisFormat %Y",
        "tickInterval 1day",
        "includes monday",
        "excludes weekends",
        "todayMarker off",
        "weekday monday",
        "weekend friday",
        "gantt",
        "click target",
        "style target fill:red",
        "foo; click target",
        "%%{init: true}",
        "https://example.invalid/x",
        "//example.invalid/x",
        "@import url(x)",
        "callback(x)",
        "iconify_api",
        "fa:server",
        "logos:github",
        "<script>alert(1)</script>",
        "2026-07-01 release",
    ],
)
def test_gantt_task_source_neutralization_matches_parser_and_strict_scanner(label: str) -> None:
    ir = _gantt_ir(task_label=label)
    plan = plan_gantt_records(ir)
    code = serialize_gantt(ir)

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
    assert plan.sections[0].tasks[0].code_label.replace(_ZERO_WIDTH_SPACE, "") == (
        plan.sections[0].tasks[0].visible_label
    )


@pytest.mark.parametrize("field", ["label", "title"])
@pytest.mark.parametrize(
    "value",
    ["   ", "bad\x00text", "bad\u200btext", "bad\ud800text", _StringSubclass("text")],
)
def test_gantt_visible_text_rejects_malformed_values(field: str, value: object) -> None:
    ir = _gantt_ir()
    if field == "label":
        ir["sections"][0]["tasks"][0][field] = value
    else:
        ir["sections"][0][field] = value

    with pytest.raises(SerializationError):
        serialize_gantt(ir)


@pytest.mark.parametrize("field", ["label", "title"])
def test_gantt_visible_text_does_not_invoke_user_coercion_hooks(field: str) -> None:
    ir = _gantt_ir()
    if field == "label":
        ir["sections"][0]["tasks"][0][field] = _ExplosiveText()
    else:
        ir["sections"][0][field] = _ExplosiveText()

    with pytest.raises(SerializationError):
        serialize_gantt(ir)


def test_gantt_exact_empty_labels_keep_existing_fallbacks() -> None:
    ir = _gantt_ir(task_label="", section_title="")

    plan = plan_gantt_records(ir)
    code = serialize_gantt(ir)

    assert plan.sections[0].semantic_label == "Tasks"
    assert plan.sections[0].tasks[0].semantic_label == "Task 1"
    assert "section Tasks" in code
    assert "Task 1 :crit, done, build, 2026-07-01, 2026-07-02" in code


def test_gantt_skips_empty_sections_but_requires_one_renderable_task() -> None:
    ir = _gantt_ir()
    ir["sections"].insert(0, {"id": "empty", "title": "Invisible", "tasks": []})

    plan = plan_gantt_records(ir)
    code = serialize_gantt(ir)

    assert [section.semantic_label for section in plan.sections] == ["Delivery"]
    assert "Invisible" not in code
    with pytest.raises(SerializationError, match="renderable task"):
        serialize_gantt({"sections": [{"title": "Only empty", "tasks": []}]})


def test_gantt_rejects_scene_budget_overflow_before_record_planning() -> None:
    with pytest.raises(SerializationError, match="section count"):
        plan_gantt_records(
            {"sections": [{"title": "Empty", "tasks": []} for _ in range(MAX_SCENE_GROUPS + 1)]}
        )

    task = {
        "id": "task",
        "label": "Task",
        "start": "2026-07-01",
        "end": "2026-07-02",
    }
    with pytest.raises(SerializationError, match="task count"):
        plan_gantt_records(
            {
                "sections": [
                    {
                        "title": "Overflow",
                        "tasks": [task] * (MAX_SCENE_ELEMENTS + 1),
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    "status",
    ["unknown", "done, unknown", "done,,crit", "done; click target", "active, done"],
)
def test_gantt_rejects_unsupported_or_ambiguous_status_tokens(status: str) -> None:
    ir = _gantt_ir()
    ir["sections"][0]["tasks"][0]["status"] = status

    with pytest.raises(SerializationError, match="status"):
        serialize_gantt(ir)


@pytest.mark.parametrize("field", ["id", "start", "end", "duration"])
@pytest.mark.parametrize("value", ["bad,value", "bad#value", "bad;value", "   "])
def test_gantt_rejects_ambiguous_schedule_fields(field: str, value: str) -> None:
    ir = _gantt_ir()
    task = ir["sections"][0]["tasks"][0]
    if field == "duration":
        task.pop("end")
    task[field] = value

    with pytest.raises(SerializationError):
        serialize_gantt(ir)


def test_gantt_requires_exactly_one_end_or_duration() -> None:
    both = _gantt_ir()
    both["sections"][0]["tasks"][0]["duration"] = "1d"
    neither = _gantt_ir()
    neither["sections"][0]["tasks"][0].pop("end")

    with pytest.raises(SerializationError, match="exactly one of end or duration"):
        serialize_gantt(both)
    with pytest.raises(SerializationError, match="exactly one of end or duration"):
        serialize_gantt(neither)


@pytest.mark.parametrize(
    "task_id",
    ["active", "done", "crit", "milestone", "vert", "__proto__", "ICONIFY_task"],
)
def test_gantt_rejects_task_ids_consumed_by_runtime_or_security(task_id: str) -> None:
    ir = _gantt_ir()
    ir["sections"][0]["tasks"][0]["id"] = task_id

    with pytest.raises(SerializationError, match="task id"):
        serialize_gantt(ir)


def test_gantt_rejects_duplicate_terminal_task_ids() -> None:
    ir = _gantt_ir()
    ir["sections"][0]["tasks"].append(
        {
            "id": "build",
            "label": "Duplicate",
            "start": "2026-07-02",
            "end": "2026-07-03",
        }
    )

    with pytest.raises(SerializationError, match="duplicate gantt task id"):
        serialize_gantt(ir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("duration", "garbage"),
        ("duration", "-1d"),
        ("duration", "2026-07-02"),
        ("duration", "0.1d"),
        ("duration", "0.1M"),
        ("duration", "0.5y"),
        ("duration", "0.1ms"),
        ("duration", "0.0001s"),
        ("duration", "100000000d"),
        ("end", "garbage"),
        ("end", "1d"),
        ("end", "2026-02-30"),
        ("end", "2026-7-02"),
    ],
)
def test_gantt_rejects_runtime_silent_zero_width_schedule_values(
    field: str,
    value: str,
) -> None:
    ir = _gantt_ir()
    task = ir["sections"][0]["tasks"][0]
    if field == "duration":
        task.pop("end")
    task[field] = value

    with pytest.raises(SerializationError, match="duration|date"):
        serialize_gantt(ir)


@pytest.mark.parametrize("end", ["2026-06-30", "2026-07-01"])
def test_gantt_rejects_non_milestone_end_not_after_start(end: str) -> None:
    ir = _gantt_ir()
    ir["sections"][0]["tasks"][0]["end"] = end

    with pytest.raises(SerializationError, match="end date must follow"):
        serialize_gantt(ir)


def test_gantt_allows_zero_duration_only_for_an_explicit_milestone() -> None:
    ordinary = _gantt_ir()
    ordinary_task = ordinary["sections"][0]["tasks"][0]
    ordinary_task.pop("end")
    ordinary_task["duration"] = "0d"
    milestone = copy.deepcopy(ordinary)
    milestone["sections"][0]["tasks"][0]["status"] = "milestone"

    with pytest.raises(SerializationError, match="duration must be positive"):
        serialize_gantt(ordinary)
    assert "Build :milestone, build, 2026-07-01, 0d" in serialize_gantt(milestone)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start", "after missing"),
        ("end", "until missing"),
        ("start", "after build trailing?"),
        ("end", "until build trailing?"),
    ],
)
def test_gantt_rejects_missing_or_malformed_dependency_targets(
    field: str,
    value: str,
) -> None:
    ir = _gantt_ir()
    ir["sections"][0]["tasks"][0][field] = value

    with pytest.raises(SerializationError, match="dependency|date"):
        serialize_gantt(ir)


def test_gantt_accepts_valid_dates_durations_and_existing_after_dependencies() -> None:
    ir = {
        "date_format": "DD/MM/YYYY HH:mm",
        "sections": [
            {
                "title": "Delivery",
                "tasks": [
                    {
                        "id": "build",
                        "label": "Build",
                        "start": "01/07/2026 09:30",
                        "end": "02/07/2026 09:30",
                    },
                    {
                        "id": "ship",
                        "label": "Ship",
                        "start": "after build",
                        "duration": "2d",
                    },
                    {
                        "id": "notify",
                        "label": "Notify",
                        "start": "after build ship",
                        "duration": "1h",
                    },
                ],
            }
        ],
    }

    code = serialize_gantt(ir)

    assert "Build :build, 01/07/2026 09:30, 02/07/2026 09:30" in code
    assert "Ship :ship, after build, 2d" in code
    assert "Notify :notify, after build ship, 1h" in code


def test_gantt_rejects_mermaid_11_16_inconsistent_seconds_timestamp_format() -> None:
    ir = _gantt_ir()
    ir["date_format"] = "X"
    ir["sections"][0]["tasks"][0]["start"] = "1704067200"
    ir["sections"][0]["tasks"][0]["end"] = "1704153600"

    with pytest.raises(SerializationError, match="different units"):
        serialize_gantt(ir)

    ir["date_format"] = "x"
    ir["sections"][0]["tasks"][0]["start"] = "1704067200000"
    ir["sections"][0]["tasks"][0]["end"] = "1704153600000"
    assert "dateFormat x" in serialize_gantt(ir)

    for invalid_epoch in ("01704067200000", "8640000000000001"):
        ir["sections"][0]["tasks"][0]["end"] = invalid_epoch
        with pytest.raises(SerializationError, match="end date is invalid"):
            serialize_gantt(ir)


def test_gantt_rejects_epoch_duration_that_resolves_outside_ecmascript_date_range() -> None:
    direct = _gantt_ir()
    direct["date_format"] = "x"
    direct_task = direct["sections"][0]["tasks"][0]
    direct_task["start"] = "8640000000000000"
    direct_task.pop("end")
    direct_task["duration"] = "1ms"

    chained = {
        "date_format": "x",
        "sections": [
            {
                "tasks": [
                    {
                        "id": "boundary",
                        "label": "Boundary",
                        "start": "8639999999999999",
                        "duration": "1ms",
                    },
                    {
                        "id": "overflow",
                        "label": "Overflow",
                        "start": "after boundary",
                        "duration": "1ms",
                    },
                ]
            }
        ],
    }

    with pytest.raises(SerializationError, match="ECMAScript Date range"):
        serialize_gantt(direct)
    with pytest.raises(SerializationError, match="ECMAScript Date range"):
        serialize_gantt(chained)


@pytest.mark.parametrize(
    "date_format",
    [
        "YYYY-MM-DDTHH:mm:ssZ",
        "YYYY-MM-DDTHH:mm:ssZZ",
        "YYYY-MM-DD HH:mm:ss.S",
        "YYYY-MM-DD HH:mm:ss.SS",
    ],
)
def test_gantt_rejects_date_tokens_with_zero_width_mermaid_11_16_end_parity(
    date_format: str,
) -> None:
    ir = _gantt_ir()
    ir["date_format"] = date_format

    with pytest.raises(SerializationError, match="unsupported date token"):
        serialize_gantt(ir)


@pytest.mark.parametrize(
    "date_format",
    ["YYYY-MM-DD h:mm", "YYYY-MM-DD HH:mm A"],
)
def test_gantt_rejects_unpaired_12_hour_and_meridiem_tokens(date_format: str) -> None:
    ir = _gantt_ir()
    ir["date_format"] = date_format

    with pytest.raises(SerializationError, match="pair h/hh with A/a"):
        serialize_gantt(ir)


def test_gantt_accepts_paired_12_hour_and_meridiem_tokens() -> None:
    ir = _gantt_ir()
    ir["date_format"] = "YYYY-MM-DD h:mm A"
    ir["sections"][0]["tasks"][0]["start"] = "2026-07-01 12:00 PM"
    ir["sections"][0]["tasks"][0]["end"] = "2026-07-01 1:00 PM"

    assert "dateFormat YYYY-MM-DD h:mm A" in serialize_gantt(ir)


def test_gantt_rejects_cycles_and_forward_dependencies() -> None:
    cycle = {
        "sections": [
            {
                "tasks": [
                    {"id": "a", "label": "A", "start": "after b", "duration": "1d"},
                    {"id": "b", "label": "B", "start": "after a", "duration": "1d"},
                ]
            }
        ]
    }
    reverse_chain = {
        "sections": [
            {
                "tasks": [
                    *[
                        {
                            "id": f"task_{index}",
                            "label": f"Task {index}",
                            "start": f"after task_{index - 1}",
                            "duration": "1d",
                        }
                        for index in range(12, 1, -1)
                    ],
                    {
                        "id": "task_1",
                        "label": "Task 1",
                        "start": "2026-07-01",
                        "end": "2026-07-02",
                    },
                ]
            }
        ]
    }

    with pytest.raises(SerializationError, match="prior task"):
        serialize_gantt(cycle)
    with pytest.raises(SerializationError, match="prior task"):
        serialize_gantt(reverse_chain)


@pytest.mark.parametrize(
    "field",
    ["title", "description", "acc_title", "acc_description", "date_format"],
)
def test_gantt_exact_empty_metadata_is_omitted(field: str) -> None:
    ir = {**_gantt_ir(), field: ""}
    original = copy.deepcopy(ir)

    validated = validated_gantt_metadata_ir(ir)

    assert ir == original
    assert field not in validated


@pytest.mark.parametrize(
    "field",
    ["title", "description", "acc_title", "acc_description", "date_format"],
)
@pytest.mark.parametrize(
    "value",
    ["   ", "bad\ntext", "bad\u200btext", "bad\ud800text", _StringSubclass("text")],
)
def test_gantt_metadata_rejects_malformed_raw_text(field: str, value: object) -> None:
    ir = {**_gantt_ir(), field: value}

    with pytest.raises(SerializationError):
        validated_gantt_metadata_ir(ir)
    with pytest.raises(SerializationError):
        serialize_typed_ir_result("gantt", ir)


def test_gantt_pipeline_stores_raw_snapshot_instead_of_generated_accessibility() -> None:
    observation = _gantt_observation(task_label="Build", evidence_text="Build")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _GanttRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "gantt-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    assert "containing Delivery, Build" in result.selected.mermaid_code


def test_gantt_pipeline_preserves_explicit_raw_accessibility_without_mutating_input() -> None:
    observation = _gantt_observation(task_label="Build", evidence_text="Build")
    source_ir = observation.typed_candidates[0].ir
    source_ir["acc_title"] = "Schedule overview"
    source_ir["acc_description"] = "Delivery work"
    source_snapshot = copy.deepcopy(source_ir)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_GanttRuntime(), config.security_profile),
    ).reconstruct(
        "gantt-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert source_ir == source_snapshot
    assert result.selected is not None
    assert result.selected.typed_ir["acc_title"] == "Schedule overview"
    assert result.selected.typed_ir["acc_description"] == "Delivery work"
    assert "accTitle: Schedule overview" in result.selected.mermaid_code
    assert "accDescr: Delivery work" in result.selected.mermaid_code


def test_gantt_pipeline_rejects_raw_metadata_before_runtime() -> None:
    observation = _gantt_observation(task_label="Build", evidence_text="Build")
    observation.typed_candidates[0].ir["acc_title"] = " "
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _GanttRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "gantt-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is None
    assert runtime.calls == []


def test_gantt_accepted_repair_regenerates_accessibility_from_current_label() -> None:
    observation = _gantt_observation(task_label="Buld", evidence_text="Build")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _GanttRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_GanttLabelRepair("Build"),
    ).reconstruct(
        "gantt-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["sections"][0]["tasks"][0]["label"] == "Build"
    assert "acc_description" not in result.selected.typed_ir
    assert "containing Delivery, Build" in result.selected.mermaid_code
    assert "Buld" not in result.selected.mermaid_code


@pytest.mark.parametrize(
    ("initial_label", "repaired_label", "evidence_text", "expects_warning"),
    [
        ("Build: 50%", "Build", "Build", False),
        ("Buld", "Build: 50%", "Build: 50%", True),
    ],
)
def test_gantt_accepted_repair_reconciles_terminal_compatibility_warning(
    initial_label: str,
    repaired_label: str,
    evidence_text: str,
    expects_warning: bool,
) -> None:
    observation = _gantt_observation(
        task_label=initial_label,
        evidence_text=evidence_text,
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_GanttRuntime(), config.security_profile),
        repair_engine=_GanttLabelRepair(repaired_label),
    ).reconstruct(
        "gantt-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert (GANTT_TEXT_COMPATIBILITY_WARNING in result.selected.warnings) is expects_warning


def test_gantt_accepted_repair_omits_exact_empty_raw_metadata_snapshot() -> None:
    observation = _gantt_observation(task_label="Buld", evidence_text="Build")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_GanttRuntime(), config.security_profile),
        repair_engine=_GanttMetadataRepair(
            "acc_title",
            "",
            serialize=True,
            task_label="Build",
        ),
    ).reconstruct(
        "gantt-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    assert "containing Delivery, Build" in result.selected.mermaid_code


def test_gantt_repair_rejects_invalid_raw_metadata_before_second_runtime() -> None:
    observation = _gantt_observation(task_label="Buld", evidence_text="Build")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _GanttRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_GanttMetadataRepair("acc_title", " ", serialize=False),
    ).reconstruct(
        "gantt-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert len(runtime.calls) == 1
    assert not result.selected.repair_history[-1].accepted


@pytest.mark.integration
def test_gantt_mermaid_11_16_svg_matches_terminal_text_and_accessibility_plan() -> None:
    task_label = (
        'title fake Build: "ship" \\ path &#35; <b>tag</b> %%{init: true} '
        "https://example.invalid/x iconify @import callback(x) user@example.com www.example.com"
    )
    ir = {
        **_gantt_ir(
            task_label=task_label,
            section_title='Section: "Delivery" \\ path <team>',
            title='Plan: "Q3" \\ path <title>',
        ),
        "acc_title": 'Accessible "plan" \\ path <title>',
        "acc_description": "Accessible %%{note} &#35; <description>",
    }
    ir["sections"][0]["tasks"].append(
        {
            "id": "ship",
            "label": "Ship",
            "start": "after build",
            "duration": "1d",
        }
    )
    ir["sections"][0]["tasks"].append(
        {
            "id": "notify",
            "label": "Notify",
            "start": "after build ship",
            "duration": "1h",
        }
    )
    plan = plan_gantt_records(ir)
    accessibility = plan_gantt_accessibility(ir, experimental=False, gantt_plan=plan)
    result = serialize_typed_ir_result("gantt", ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(result.code, 20)
    finally:
        runtime.close()

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, (outcome.runtime.error, outcome.warnings)
    assert outcome.runtime.svg is not None
    root = ET.fromstring(outcome.runtime.svg)
    assert (root.find("{http://www.w3.org/2000/svg}title").text or "").replace(
        _ZERO_WIDTH_SPACE, ""
    ) == accessibility.title_canvas
    assert (root.find("{http://www.w3.org/2000/svg}desc").text or "").replace(
        _ZERO_WIDTH_SPACE, ""
    ) == accessibility.description_canvas
    visible = " ".join(
        " ".join("".join(element.itertext()).split())
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "text" and "".join(element.itertext()).strip()
    ).replace(_ZERO_WIDTH_SPACE, "")
    assert plan.title is not None
    assert plan.title.canvas in visible
    assert plan.sections[0].visible_label in visible
    assert plan.sections[0].tasks[0].visible_label in visible
    assert plan.sections[0].tasks[1].visible_label in visible
    assert plan.sections[0].tasks[2].visible_label in visible
    task_rects = {
        task_id: next(
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "rect"
            and (element.get("id") or "").endswith(f"-{task_id}")
        )
        for task_id in ("build", "ship", "notify")
    }
    assert float(task_rects["notify"].get("x")) >= (
        float(task_rects["ship"].get("x")) + float(task_rects["ship"].get("width"))
    )
    assert GANTT_TEXT_COMPATIBILITY_WARNING in result.warnings


@pytest.mark.integration
def test_gantt_supported_schedule_subset_has_nonzero_mermaid_11_16_geometry() -> None:
    epoch = _gantt_ir()
    epoch["date_format"] = "x"
    epoch["sections"][0]["tasks"][0]["start"] = "1704067200000"
    epoch["sections"][0]["tasks"][0]["end"] = "1704153600000"

    meridiem = _gantt_ir()
    meridiem["date_format"] = "YYYY-MM-DD h:mm A"
    meridiem["sections"][0]["tasks"][0]["start"] = "2026-07-01 12:00 PM"
    meridiem["sections"][0]["tasks"][0]["end"] = "2026-07-01 1:00 PM"

    milliseconds = _gantt_ir()
    milliseconds["date_format"] = "YYYY-MM-DD HH:mm:ss.SSS"
    milliseconds["sections"][0]["tasks"][0]["start"] = "2026-07-01 09:30:00.000"
    milliseconds["sections"][0]["tasks"][0]["end"] = "2026-07-01 09:30:00.001"

    fractional_duration = _gantt_ir()
    fractional_task = fractional_duration["sections"][0]["tasks"][0]
    fractional_task.pop("end")
    fractional_task["duration"] = "1.5h"

    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcomes = [
            validator.validate(serialize_gantt(ir), 20)
            for ir in (epoch, meridiem, milliseconds, fractional_duration)
        ]
    finally:
        runtime.close()

    for outcome in outcomes:
        assert outcome.runtime.syntax_valid, outcome.runtime.error
        assert outcome.runtime.render_valid, outcome.runtime.error
        assert outcome.runtime.svg is not None
        root = ET.fromstring(outcome.runtime.svg)
        task_rect = next(
            element
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1] == "rect"
            and (element.get("id") or "").endswith("-build")
        )
        assert float(task_rect.get("width")) > 0


@pytest.mark.integration
def test_gantt_mermaid_11_16_rejects_mixed_scale_zero_width_task() -> None:
    ir = {
        "date_format": "x",
        "sections": [
            {
                "tasks": [
                    {
                        "id": "long",
                        "label": "Long",
                        "start": "1704067200000",
                        "duration": "1d",
                    },
                    {
                        "id": "tiny",
                        "label": "Tiny",
                        "start": "after long",
                        "duration": "1ms",
                    },
                ]
            }
        ],
    }
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(serialize_gantt(ir), 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid
    assert not outcome.runtime.render_valid
    assert "rendered Gantt contains a non-visible task rectangle" in outcome.warnings
