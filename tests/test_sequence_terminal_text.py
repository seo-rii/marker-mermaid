from __future__ import annotations

import copy
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import (
    SEQUENCE_TEXT_COMPATIBILITY_WARNING,
    SerializationError,
    enrich_sequence_accessibility_ir,
    plan_sequence_accessibility,
    plan_sequence_records,
    serialize_sequence,
    serialize_typed_ir_result,
    validated_sequence_accessibility_ir,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


def _sequence_ir(
    *,
    client_id: str = "client",
    client_label: object = "Client",
    message_label: object = "Request",
    message_style: object = "solid",
) -> dict[str, object]:
    return {
        "participants": [
            {
                "id": client_id,
                "label": client_label,
                "bbox": [10, 10, 40, 40],
                "evidence_ids": ["ocr-client"],
            },
            {
                "id": "server",
                "label": "Server",
                "bbox": [60, 10, 90, 40],
                "evidence_ids": ["ocr-server"],
            },
        ],
        "messages": [
            {
                "id": "request-message",
                "source": client_id,
                "target": "server",
                "label": message_label,
                "style": message_style,
                "evidence_ids": ["arrow-request"],
            }
        ],
    }


class _SequenceRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="sequence",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
                "<text>Client Server Request</text></svg>"
            ),
        )

    def close(self) -> None:
        pass


class _SequenceLabelRepair:
    name = "sequence_label_repair"

    def __init__(self, label: str) -> None:
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["participants"][0]["label"] = self.label
        serialized = serialize_typed_ir_result("sequence", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _SequenceMetadataRepair:
    name = "sequence_metadata_repair"

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["acc_title"] = " "
        return RepairProposal(
            code=f"{candidate.mermaid_code.rstrip()}\n    autonumber\n",
            operation=self.name,
            typed_ir=typed_ir,
        )


def _sequence_observation(
    *,
    client_label: str,
    evidence_text: str,
    metadata: dict[str, object] | None = None,
) -> EngineObservation:
    ir = _sequence_ir(client_label=client_label)
    ir.update(metadata or {})
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["sequence"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="sequence", ir=ir)],
        evidence=[
            VisualEvidence(
                id="ocr-client",
                kind="ocr_token",
                text=evidence_text,
                bbox=(10, 10, 40, 40),
            ),
            VisualEvidence(
                id="ocr-server",
                kind="ocr_token",
                text="Server",
                bbox=(60, 10, 90, 40),
            ),
            VisualEvidence(id="arrow-request", kind="arrowhead", text="Request"),
        ],
    )


def test_sequence_plan_freezes_semantic_source_and_canvas_without_mutating_ir() -> None:
    ir = _sequence_ir(
        client_id="sequenceDiagram",
        client_label='  Client\t#1; "quoted" \\ path\r\nnext  ',
        message_label='  Request\t#2; "now" \\ path\r\nnext  ',
    )
    original = copy.deepcopy(ir)

    plan = plan_sequence_records(ir)

    assert ir == original
    assert plan.participants[0].source_id == "sequenceDiagram"
    assert plan.participants[0].emitted_id == "mmx_sequence_participant_1"
    assert plan.participants[0].semantic_label == 'Client #1; "quoted" \\ path next'
    assert plan.participants[0].source_label == ('Client #35;1#59; "quoted" \\ path next')
    assert plan.participants[0].canvas_label == 'Client #1; "quoted" \\ path next'
    assert plan.messages[0].semantic_label == 'Request #2; "now" \\ path next'
    assert plan.messages[0].source_label == 'Request #35;2#59; "now" \\ path next'
    assert plan.messages[0].canvas_label == 'Request #2; "now" \\ path next'
    assert not plan.compatibility_substitutions


def test_sequence_source_only_neutralization_is_strict_safe_and_canvas_exact() -> None:
    active_text = (
        "Client http://example.test callback(x) iconify click linkStyle config: --- "
        "@import %%{init: true}"
    )
    ir = _sequence_ir(client_label=active_text, message_label=f"Send {active_text}")

    plan = plan_sequence_records(ir)
    result = serialize_typed_ir_result("sequence", ir)
    scene = typed_ir_to_scene("sequence", ir)

    assert "\u200b" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe
    assert plan.participants[0].canvas_label == active_text
    assert plan.messages[0].canvas_label == f"Send {active_text}"
    assert scene is not None
    assert scene.elements[0].text == active_text
    assert scene.relations[0].label == f"Send {active_text}"
    assert not plan.compatibility_substitutions
    assert SEQUENCE_TEXT_COMPATIBILITY_WARNING not in result.warnings


def test_sequence_always_generates_participant_ids_and_resolves_exact_endpoints() -> None:
    ir = {
        "participants": [
            {"id": "sequenceDiagram", "label": "Grammar header"},
            {"id": "click", "label": "Control word"},
            {"id": "a-b", "label": "Punctuation"},
            {"id": "a b", "label": "Whitespace"},
        ],
        "messages": [
            {"id": "same", "source": "sequenceDiagram", "target": "click"},
            {"id": "same", "source": "a-b", "target": "a b", "style": "open"},
        ],
    }

    plan = plan_sequence_records(ir)
    code = serialize_sequence(ir)

    assert [participant.emitted_id for participant in plan.participants] == [
        "mmx_sequence_participant_1",
        "mmx_sequence_participant_2",
        "mmx_sequence_participant_3",
        "mmx_sequence_participant_4",
    ]
    assert [message.scene_id for message in plan.messages] == [
        "generated-relation-1",
        "generated-relation-2",
    ]
    assert [
        (message.source_emitted_id, message.target_emitted_id) for message in plan.messages
    ] == [
        ("mmx_sequence_participant_1", "mmx_sequence_participant_2"),
        ("mmx_sequence_participant_3", "mmx_sequence_participant_4"),
    ]
    assert "sequenceDiagram->>click" not in code
    assert "mmx_sequence_participant_1->>mmx_sequence_participant_2" in code
    assert "mmx_sequence_participant_3->mmx_sequence_participant_4" in code


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("source", "missing", "unknown participant"),
        ("source", None, "unknown participant"),
        ("target", "missing", "unknown participant"),
        ("target", None, "unknown participant"),
        ("style", "bold", "unsupported style"),
        ("style", 1, "unsupported style"),
    ],
)
def test_sequence_unknown_endpoints_and_styles_fail_closed(
    field: str,
    value: object,
    error: str,
) -> None:
    ir = _sequence_ir()
    ir["messages"][0][field] = value

    with pytest.raises(SerializationError, match=error):
        serialize_sequence(ir)


def test_sequence_scene_ocr_roles_direction_and_provenance_share_terminal_plan() -> None:
    ir = _sequence_ir(
        client_id="participant",
        client_label="Client #1;",
        message_label="Request #2;",
        message_style="dotted",
    )

    plan = plan_sequence_records(ir)
    scene = typed_ir_to_scene("sequence", ir)

    assert scene is not None
    assert scene.reading_direction == "LR"
    assert [element.id for element in scene.elements] == [
        plan.participants[0].emitted_id,
        plan.participants[1].emitted_id,
    ]
    assert [element.role for element in scene.elements] == ["participant", "participant"]
    assert [element.text for element in scene.elements] == ["Client #1;", "Server"]
    assert scene.elements[0].bbox == (10.0, 10.0, 40.0, 40.0)
    assert scene.elements[0].evidence_ids == ["ocr-client"]
    relation = scene.relations[0]
    assert relation.id == plan.messages[0].scene_id
    assert relation.source_id == plan.participants[0].emitted_id
    assert relation.target_id == plan.participants[1].emitted_id
    assert relation.relation_type == "message"
    assert relation.semantic_relation == "message"
    assert relation.label == "Request #2;"
    assert not relation.arrow_at_start
    assert relation.arrow_at_end
    assert relation.line_style == "dotted"
    assert relation.evidence_ids == ["arrow-request"]
    assert list(typed_ir_semantic_texts("sequence", ir, scene)) == [
        "Client #1;",
        "Server",
        "Request #2;",
    ]


@pytest.mark.parametrize(
    ("style", "token", "arrow_at_end", "line_style"),
    [
        ("solid", "->>", True, "solid"),
        ("dotted", "-->>", True, "dotted"),
        ("open", "->", False, "solid"),
        ("dotted_open", "-->", False, "dotted"),
        ("cross", "-x", True, "solid"),
    ],
)
def test_sequence_style_plan_matches_source_and_scene_arrow_semantics(
    style: str,
    token: str,
    arrow_at_end: bool,
    line_style: str,
) -> None:
    ir = _sequence_ir(message_style=style)

    plan = plan_sequence_records(ir)
    scene = typed_ir_to_scene("sequence", ir)
    code = serialize_sequence(ir)

    assert plan.messages[0].arrow_token == token
    assert plan.messages[0].arrow_at_end is arrow_at_end
    assert plan.messages[0].line_style == line_style
    assert scene is not None
    assert scene.relations[0].arrow_at_end is arrow_at_end
    assert scene.relations[0].line_style == line_style
    assert (
        f"{plan.messages[0].source_emitted_id}{token}{plan.messages[0].target_emitted_id}: Request"
    ) in code


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_sequence_exact_empty_accessibility_metadata_is_omitted_without_mutation(
    field: str,
) -> None:
    ir = {**_sequence_ir(), field: ""}
    original = copy.deepcopy(ir)

    validated = validated_sequence_accessibility_ir(ir)
    enriched = enrich_sequence_accessibility_ir(ir, experimental=False)
    result = serialize_typed_ir_result("sequence", ir)

    assert ir == original
    assert field not in validated
    if field in {"title", "description"}:
        assert field not in enriched
    else:
        assert enriched[field]
    assert enriched["acc_title"]
    assert enriched["acc_description"]
    assert "accTitle:" in result.code
    assert "accDescr:" in result.code


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
def test_sequence_raw_accessibility_rejects_nontext_whitespace_and_control(
    field: str,
    value: object,
) -> None:
    ir = {**_sequence_ir(), field: value}

    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        validated_sequence_accessibility_ir(ir)
    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_typed_ir_result("sequence", ir)


def test_sequence_accessibility_angle_substitution_is_the_only_visible_warning() -> None:
    plain = serialize_typed_ir_result("sequence", _sequence_ir())
    record_delimiters = serialize_typed_ir_result(
        "sequence",
        _sequence_ir(client_label="Client #1;", message_label="Request #2;"),
    )
    ir = {
        **_sequence_ir(),
        "acc_title": "Sequence <map>",
        "acc_description": "Client > Server",
    }
    record_plan = plan_sequence_records(ir)
    accessibility = plan_sequence_accessibility(
        ir,
        experimental=False,
        sequence_plan=record_plan,
    )
    accessibility_result = serialize_typed_ir_result("sequence", ir)

    assert SEQUENCE_TEXT_COMPATIBILITY_WARNING not in plain.warnings
    assert SEQUENCE_TEXT_COMPATIBILITY_WARNING not in record_delimiters.warnings
    assert accessibility.title_semantic == "Sequence <map>"
    assert accessibility.title_canvas == "Sequence 〈map〉"
    assert accessibility.description_canvas == "Client 〉 Server"
    assert accessibility.compatibility_substitutions
    assert SEQUENCE_TEXT_COMPATIBILITY_WARNING in accessibility_result.warnings


def test_sequence_pipeline_stores_raw_snapshot_not_generated_accessibility() -> None:
    observation = _sequence_observation(client_label="Client", evidence_text="Client")
    source_ir = observation.typed_candidates[0].ir
    source_snapshot = copy.deepcopy(source_ir)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_SequenceRuntime(), config.security_profile),
    ).reconstruct(
        "sequence-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert source_ir == source_snapshot
    assert result.selected is not None
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    assert "containing Client, Server" in result.selected.mermaid_code


def test_sequence_pipeline_rejects_raw_accessibility_before_runtime() -> None:
    observation = _sequence_observation(
        client_label="Client",
        evidence_text="Client",
        metadata={"acc_title": " "},
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _SequenceRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "sequence-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is None
    assert runtime.calls == []
    assert any(failure.stage == "serialization" for failure in result.failures)


@pytest.mark.parametrize(
    ("initial_label", "repaired_label", "evidence_text", "expects_warning"),
    [
        ("Clent", "Client <web>", "Client web", True),
        ("Clent <wrong>", "Client", "Client", False),
    ],
    ids=["repair-adds-accessibility-substitution", "repair-removes-substitution"],
)
def test_sequence_accepted_repair_regenerates_accessibility_and_warning(
    initial_label: str,
    repaired_label: str,
    evidence_text: str,
    expects_warning: bool,
) -> None:
    observation = _sequence_observation(
        client_label=initial_label,
        evidence_text=evidence_text,
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_SequenceRuntime(), config.security_profile),
        repair_engine=_SequenceLabelRepair(repaired_label),
    ).reconstruct(
        "sequence-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["participants"][0]["label"] == repaired_label
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    accessibility_label = repaired_label.replace("<", "〈").replace(">", "〉")
    assert f"containing {accessibility_label}, Server" in result.selected.mermaid_code
    assert (SEQUENCE_TEXT_COMPATIBILITY_WARNING in result.selected.warnings) is expects_warning


def test_sequence_repair_rejects_invalid_raw_accessibility_before_second_runtime() -> None:
    observation = _sequence_observation(client_label="Clent", evidence_text="Client")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _SequenceRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_SequenceMetadataRepair(),
    ).reconstruct(
        "sequence-source",
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


def test_sequence_shared_plan_enforces_source_line_and_character_budgets() -> None:
    line_heavy_ir = {
        "participants": [f"P{index}" for index in range(4_997)],
        "messages": [],
    }
    character_heavy_ir = {
        "participants": [
            {"id": "client", "label": "#" * 12_500},
            {"id": "server", "label": "Server"},
        ],
        "messages": [],
    }
    utf16_heavy_ir = {
        "participants": [
            {"id": "client", "label": "😀" * 24_990},
            {"id": "server", "label": "Server"},
        ],
        "messages": [],
    }

    with pytest.raises(SerializationError, match="source-line limit"):
        plan_sequence_records(line_heavy_ir)
    with pytest.raises(SerializationError, match="source-character limit"):
        plan_sequence_records(character_heavy_ir)
    with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
        plan_sequence_records(utf16_heavy_ir)
    assert typed_ir_to_scene("sequence", line_heavy_ir) is None
    assert typed_ir_to_scene("sequence", character_heavy_ir) is None
    assert typed_ir_to_scene("sequence", utf16_heavy_ir) is None


@pytest.mark.integration
def test_sequence_mermaid_11_16_svg_matches_terminal_accessibility_and_styles() -> None:
    styles = ["solid", "dotted", "open", "dotted_open", "cross"]
    ir = {
        "participants": [
            {
                "id": "sequenceDiagram",
                "label": 'Client; participant X as Injected #1; "quoted" \\ path',
            },
            {"id": "click", "label": "Server"},
        ],
        "messages": [
            {
                "source": "sequenceDiagram" if index % 2 == 0 else "click",
                "target": "click" if index % 2 == 0 else "sequenceDiagram",
                "label": f"{style}; X->>Y: Injected #2;",
                "style": style,
            }
            for index, style in enumerate(styles)
        ],
        "acc_title": "Sequence <map>",
        "acc_description": "Client > Server",
    }
    plan = plan_sequence_records(ir)
    accessibility = plan_sequence_accessibility(
        ir,
        experimental=False,
        sequence_plan=plan,
    )
    result = serialize_typed_ir_result("sequence", ir)
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
    title = next(element for element in root if element.tag.rsplit("}", 1)[-1] == "title")
    description = next(element for element in root if element.tag.rsplit("}", 1)[-1] == "desc")
    assert (title.text or "").replace("\u200b", "") == accessibility.title_canvas
    assert (description.text or "").replace("\u200b", "") == accessibility.description_canvas
    canvas_text = " ".join(" ".join(root.itertext()).split()).replace("\u200b", "")
    for participant in plan.participants:
        assert participant.canvas_label in canvas_text
    for message in plan.messages:
        assert message.canvas_label in canvas_text
    actor_tops = [
        element
        for element in root.iter()
        if "actor-top" in (element.attrib.get("class") or "").split()
    ]
    message_texts = [
        element
        for element in root.iter()
        if "messageText" in (element.attrib.get("class") or "").split()
    ]
    assert len(actor_tops) == 2
    assert len(message_texts) == len(plan.messages)
    marker_ends = [
        value for element in root.iter() if (value := element.attrib.get("marker-end")) is not None
    ]
    assert sum("arrowhead" in value for value in marker_ends) >= 2
    assert any("crosshead" in value for value in marker_ends)
    assert "stroke-dasharray" in outcome.runtime.svg
    assert SEQUENCE_TEXT_COMPATIBILITY_WARNING in result.warnings
