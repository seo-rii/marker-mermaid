from __future__ import annotations

import copy
import time
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_TEXT_CHARS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import SerializationError, serialize_typed_ir_result
from marker_mermaid.serializers_uml import (
    STATE_TEXT_COMPATIBILITY_WARNING,
    plan_state_accessibility,
    plan_state_records,
    serialize_state,
    validated_state_accessibility_ir,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


class _StateRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="stateDiagram",
            svg=(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
                "<text>Active review</text></svg>"
            ),
        )

    def close(self) -> None:
        pass


class _StateLabelRepair:
    name = "state_label_repair"

    def __init__(self, label: str) -> None:
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["states"][0]["label"] = self.label
        serialized = serialize_typed_ir_result("state", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _StateMetadataRepair:
    name = "state_metadata_repair"

    def __init__(
        self,
        field: str,
        value: object,
        *,
        serialize: bool,
        node_label: str | None = None,
    ) -> None:
        self.field = field
        self.value = value
        self.serialize = serialize
        self.node_label = node_label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir[self.field] = self.value
        if self.node_label is not None:
            typed_ir["states"][0]["label"] = self.node_label
        code = (
            serialize_typed_ir_result("state", typed_ir, experimental=True).code
            if self.serialize
            else f"{candidate.mermaid_code.rstrip()}\n    direction LR\n"
        )
        return RepairProposal(
            code=code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _StringSubclass(str):
    pass


class _ExplosiveLabel:
    def __bool__(self) -> bool:
        raise AssertionError("label truthiness hook must not run")

    def __eq__(self, other: object) -> bool:
        raise AssertionError("label equality hook must not run")

    def __ne__(self, other: object) -> bool:
        raise AssertionError("label inequality hook must not run")

    def __str__(self) -> str:
        raise AssertionError("label string coercion hook must not run")


def _state_ir(*, node_label: object = "Active", transition_label: object = "Advance") -> dict:
    return {
        "states": [
            {"id": "active", "label": node_label, "evidence_ids": ["ocr-active"]},
            {"id": "done", "label": "Done", "evidence_ids": ["ocr-done"]},
        ],
        "transitions": [
            {
                "source": "active",
                "target": "done",
                "label": transition_label,
                "evidence_ids": ["arrow-advance"],
            }
        ],
    }


def _state_observation(
    *,
    node_label: str,
    evidence_text: str,
    metadata: dict[str, object] | None = None,
) -> EngineObservation:
    typed_ir = {
        **(metadata or {}),
        "states": [
            {
                "id": "active",
                "label": node_label,
                "bbox": [10, 10, 90, 40],
                "evidence_ids": ["ocr-active"],
            }
        ],
        "transitions": [],
    }
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["state"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="state", ir=typed_ir)],
        evidence=[
            VisualEvidence(
                id="ocr-active",
                kind="ocr_token",
                text=evidence_text,
                bbox=(10, 10, 90, 40),
            )
        ],
    )


def _reconstruct_state_with_repair(
    *,
    initial_label: str,
    repaired_label: str,
    evidence_text: str,
) -> object:
    observation = _state_observation(
        node_label=initial_label,
        evidence_text=evidence_text,
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    return ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_StateRuntime(), config.security_profile),
        repair_engine=_StateLabelRepair(repaired_label),
    ).reconstruct(
        "state-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )


def test_state_plan_freezes_mermaid_canvas_text_without_mutating_semantic_ir() -> None:
    ir = _state_ir(
        node_label='  Active\t"review" \\ path\r\nnext  ',
        transition_label='  go\t"now" \\ path\r\nnext  ',
    )
    original = copy.deepcopy(ir)

    plan = plan_state_records(ir)

    assert ir == original
    assert plan.nodes[0].code_label == "Active ″review″ \\ path next"
    assert plan.nodes[0].visible_label == "Active ″review″ \\ path next"
    assert plan.transitions[0].code_label == 'go "now" \\ path next'
    assert plan.transitions[0].visible_label == 'go "now" \\ path next'
    assert plan.compatibility_substitutions


def test_state_scene_and_ocr_projection_share_terminal_visible_text() -> None:
    ir = _state_ir(
        node_label='Active "review" \\ path',
        transition_label='go "now" \\ path',
    )

    scene = typed_ir_to_scene("state", ir)

    assert scene is not None
    assert [element.text for element in scene.elements] == [
        "Active ″review″ \\ path",
        "Done",
    ]
    assert [relation.label for relation in scene.relations] == ['go "now" \\ path']
    assert list(typed_ir_semantic_texts("state", ir, scene)) == [
        "Active ″review″ \\ path",
        "Done",
        'go "now" \\ path',
    ]


def test_state_result_warns_only_for_visible_node_compatibility_substitutions() -> None:
    plain = serialize_typed_ir_result("state", _state_ir())
    transition_only = serialize_typed_ir_result(
        "state",
        _state_ir(transition_label='go "now" \\ path'),
    )
    node_compatibility = serialize_typed_ir_result(
        "state",
        _state_ir(node_label='Active "review" \\ path'),
    )

    assert STATE_TEXT_COMPATIBILITY_WARNING not in plain.warnings
    assert STATE_TEXT_COMPATIBILITY_WARNING not in transition_only.warnings
    assert STATE_TEXT_COMPATIBILITY_WARNING in node_compatibility.warnings
    assert 'state "Active ″review″ \\ path" as active' in node_compatibility.code


@pytest.mark.parametrize(
    ("initial_label", "repaired_label", "evidence_text", "expects_warning"),
    [
        ("Actve", 'Active "review"', "Active review", True),
        ('Actve "wrong"', "Active", "Active", False),
    ],
    ids=["repair-adds-compatibility", "repair-removes-compatibility"],
)
def test_state_accepted_repair_reconciles_terminal_compatibility_warning(
    initial_label: str,
    repaired_label: str,
    evidence_text: str,
    expects_warning: bool,
) -> None:
    result = _reconstruct_state_with_repair(
        initial_label=initial_label,
        repaired_label=repaired_label,
        evidence_text=evidence_text,
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["states"][0]["label"] == repaired_label
    assert "containing Active" in result.selected.mermaid_code
    assert "containing Actve" not in result.selected.mermaid_code
    assert (STATE_TEXT_COMPATIBILITY_WARNING in result.selected.warnings) is expects_warning


def test_state_pipeline_rejects_raw_accessibility_metadata_before_runtime() -> None:
    observation = _state_observation(
        node_label="Active",
        evidence_text="Active",
    )
    observation.typed_candidates[0].ir["acc_title"] = " "
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _StateRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "state-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is None
    assert runtime.calls == []
    assert any(
        failure.stage == "serialization" and failure.error_type == "SerializationError"
        for failure in result.failures
    )


def test_state_repair_rejects_raw_accessibility_metadata_before_second_runtime() -> None:
    observation = _state_observation(node_label="Active", evidence_text="Active")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _StateRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_StateMetadataRepair("acc_title", " ", serialize=False),
    ).reconstruct(
        "state-source",
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


def test_state_accepted_repair_stores_exact_empty_metadata_as_omitted() -> None:
    observation = _state_observation(node_label="Actve", evidence_text="Active")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _StateRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_StateMetadataRepair(
            "acc_title",
            "",
            serialize=True,
            node_label="Active",
        ),
    ).reconstruct(
        "state-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert "acc_title" not in result.selected.typed_ir
    assert result.selected.typed_ir["states"][0]["label"] == "Active"


def test_state_accepted_repair_adds_accessibility_only_compatibility_warning() -> None:
    observation = _state_observation(node_label="Actve", evidence_text="Active")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _StateRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_StateMetadataRepair(
            "acc_title",
            "A &#35; <b>",
            serialize=True,
            node_label="Active",
        ),
    ).reconstruct(
        "state-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert STATE_TEXT_COMPATIBILITY_WARNING in result.selected.warnings


def test_state_source_only_neutralization_preserves_canvas_projection() -> None:
    label = "<b> http://example.test %% callback() iconify click config: ---"
    ir = _state_ir(node_label=label, transition_label="Advance")

    plan = plan_state_records(ir)
    code = serialize_state(ir)
    scene = typed_ir_to_scene("state", ir)

    assert "\u200b" in code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
    expected = " ".join(label.split())
    assert plan.nodes[0].visible_label == expected
    assert scene is not None and scene.elements[0].text == expected
    assert not plan.compatibility_substitutions


@pytest.mark.parametrize(
    "label",
    [
        "xhttp://example.test",
        "prejavascript:post",
        "myiconify",
        "MYICONIFY",
        "sofa: label",
        "catalogos: label",
        "%%%{ literal",
        "%%%%%   { literal",
    ],
)
def test_state_source_neutralization_matches_strict_scanner_substrings(label: str) -> None:
    ir = _state_ir(node_label=label, transition_label="Advance")

    plan = plan_state_records(ir)
    code = serialize_state(ir)
    scene = typed_ir_to_scene("state", ir)

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
    assert plan.nodes[0].visible_label == " ".join(label.split())
    assert scene is not None and scene.elements[0].text == " ".join(label.split())


@pytest.mark.parametrize("percent_count", range(2, 16))
@pytest.mark.parametrize("gap", ["", " ", "   "])
def test_state_source_neutralizes_every_overlapping_directive_open(
    percent_count: int,
    gap: str,
) -> None:
    label = f"{'%' * percent_count}{gap}{{ literal"
    ir = _state_ir(node_label=label, transition_label="Advance")

    plan = plan_state_records(ir)
    code = serialize_state(ir)

    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
    assert plan.nodes[0].visible_label == " ".join(label.split())


def test_state_plan_discloses_markdown_and_entity_canvas_compatibility() -> None:
    node_label = "direction LR **bold** _ital_ `code` ~~strike~~ [link](target) A &amp; B A &#35; B"
    transition_label = "direction LR **bold** _ital_ `code` ~~strike~~ [link](target)"
    ir = _state_ir(node_label=node_label, transition_label=transition_label)

    plan = plan_state_records(ir)
    scene = typed_ir_to_scene("state", ir)
    result = serialize_typed_ir_result("state", ir)

    expected_node = (
        "direction LR ∗∗bold∗∗ ＿ital＿ ｀code｀ ～～strike～～ ［link］(target) "
        "A ＆amp; B A ＆＃35; B"
    )
    expected_transition = "direction LR ∗∗bold∗∗ ＿ital＿ ｀code｀ ～～strike～～ ［link］(target)"
    assert "\u200b" in plan.nodes[0].code_label
    assert "\u200b" in plan.transitions[0].code_label
    assert plan.nodes[0].visible_label == expected_node
    assert plan.transitions[0].visible_label == expected_transition
    assert plan.compatibility_substitutions
    assert scene is not None
    assert scene.elements[0].text == expected_node
    assert scene.relations[0].label == expected_transition
    assert STATE_TEXT_COMPATIBILITY_WARNING in result.warnings


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("``code``", "｀｀code｀｀"),
        ("```code```", "｀｀｀code｀｀｀"),
        ("[](target)", "［］(target)"),
        ("[]()", "［］()"),
        ("![alt](target)", "!［alt］(target)"),
        ("[outer [inner]](target)", "［outer [inner]］(target)"),
        ("~strike~", "～strike～"),
        ("a~b~c", "a～b～c"),
        ("~mixed~~", "～mixed～～"),
        ("~~mixed~", "～～mixed～"),
        ("a~~b~~c", "a～～b～～c"),
        ("***both***", "∗∗∗both∗∗∗"),
        ("___both___", "＿＿＿both＿＿＿"),
        ("**a *b* c**", "∗∗a ∗b∗ c∗∗"),
    ],
)
def test_state_plan_neutralizes_active_nested_markdown_forms(
    label: str,
    expected: str,
) -> None:
    plan = plan_state_records(_state_ir(node_label=label, transition_label=label))

    assert plan.nodes[0].visible_label == expected
    assert plan.transitions[0].visible_label == expected
    assert plan.compatibility_substitutions


@pytest.mark.parametrize(
    "label",
    [
        "***",
        "___",
        "~~",
        "~~~value~~~",
        "~~~~value~~~~",
        "~~~~~value~~~~~",
        "[x](",
        "[x](target",
        "order_id a__b a~~b [x] # Heading 2*3",
        "x_y",
        "A * B",
        "~1",
        "C\\path",
        "<x@y>",
        "<user@example.com>",
        "user@example.com",
        "u@foo_bar.example.com",
        "www.example.com",
        "www.example",
        "xwww.example.com",
        "WWW.EXAMPLE",
        "2 < 3",
    ],
)
def test_state_plan_preserves_markdown_inactive_punctuation_without_warning(
    label: str,
) -> None:
    ir = {
        **_state_ir(node_label=label, transition_label=label),
        "acc_title": "State fixture",
        "acc_description": "State fixture description",
    }

    plan = plan_state_records(ir)
    result = serialize_typed_ir_result("state", ir)

    assert plan.nodes[0].code_label.replace("\u200b", "") == label
    assert plan.nodes[0].visible_label == " ".join(label.split())
    assert plan.transitions[0].code_label.replace("\u200b", "") == label
    assert plan.transitions[0].visible_label == label
    assert not plan.compatibility_substitutions
    assert STATE_TEXT_COMPATIBILITY_WARNING not in result.warnings


@pytest.mark.parametrize(
    "label",
    [
        "[a" * (MAX_TEXT_CHARS // 2),
        ("[x](" * (MAX_TEXT_CHARS // 4))[:MAX_TEXT_CHARS],
        (" **a" * (MAX_TEXT_CHARS // 5))[:MAX_TEXT_CHARS],
        (" __a" * (MAX_TEXT_CHARS // 5))[:MAX_TEXT_CHARS],
        (" ~~a" * (MAX_TEXT_CHARS // 5))[:MAX_TEXT_CHARS],
        "`" * (MAX_TEXT_CHARS // 2) + "x" * (MAX_TEXT_CHARS // 2),
    ],
    ids=[
        "unmatched-brackets",
        "unmatched-link-targets",
        "unmatched-stars",
        "unmatched-underscores",
        "unmatched-strikes",
        "unmatched-backticks",
    ],
)
def test_state_markdown_planning_is_bounded_for_adversarial_terminal_text(label: str) -> None:
    started = time.perf_counter()

    plan = plan_state_records(_state_ir(node_label=label, transition_label="Advance"))

    assert time.perf_counter() - started < 5
    assert plan.nodes[0].visible_label == " ".join(label.split())
    assert not plan.compatibility_substitutions


@pytest.mark.parametrize(
    ("label", "expected", "substituted"),
    [
        ("\\", "∖", True),
        ("A\\", "A∖", True),
        ("\\A", "∖A", True),
        ("A\\\\B", "A∖∖B", True),
        ("A\\`B", "A∖`B", True),
        ("A\\[B", "A∖[B", True),
        ("A\\*B", "A∖*B", True),
        ("A\\B", "A\\B", False),
        ("A\\ B", "A\\ B", False),
    ],
)
def test_state_plan_replaces_only_markdown_active_backslashes(
    label: str,
    expected: str,
    substituted: bool,
) -> None:
    plan = plan_state_records(_state_ir(node_label=label, transition_label=label))

    assert plan.nodes[0].visible_label == expected
    assert plan.transitions[0].visible_label == expected
    assert plan.compatibility_substitutions is substituted


@pytest.mark.parametrize("field", ["node", "transition"])
@pytest.mark.parametrize("value", ["   ", "\t\r\n", "\u00a0"])
def test_state_explicit_whitespace_only_labels_fail_before_runtime(
    field: str,
    value: str,
) -> None:
    ir = _state_ir(
        node_label=value if field == "node" else "Active",
        transition_label=value if field == "transition" else "Advance",
    )

    with pytest.raises(SerializationError, match="bounded non-empty text"):
        serialize_typed_ir_result("state", ir)


@pytest.mark.parametrize(
    "value",
    [
        False,
        0,
        0.0,
        [],
        {},
        b"",
        ["not", "text"],
        _StringSubclass(""),
        "A\x00B",
        "A\u200bB",
        "A\ud800B",
        "x" * (MAX_TEXT_CHARS + 1),
    ],
)
@pytest.mark.parametrize("field", ["node", "transition"])
def test_state_visible_labels_reject_malformed_text(field: str, value: object) -> None:
    ir = _state_ir(
        node_label=value if field == "node" else "Active",
        transition_label=value if field == "transition" else "Advance",
    )

    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_state(ir)
    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_typed_ir_result("state", ir)


@pytest.mark.parametrize("field", ["node", "transition"])
def test_state_label_validation_does_not_invoke_user_coercion_hooks(field: str) -> None:
    value = _ExplosiveLabel()
    ir = _state_ir(
        node_label=value if field == "node" else "Active",
        transition_label=value if field == "transition" else "Advance",
    )

    with pytest.raises(SerializationError, match="must be text"):
        serialize_state(ir)
    with pytest.raises(SerializationError, match="must be text"):
        serialize_typed_ir_result("state", ir)


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_state_exact_empty_accessibility_metadata_is_omitted_without_mutation(
    field: str,
) -> None:
    ir = {**_state_ir(), field: ""}
    original = copy.deepcopy(ir)

    validated = validated_state_accessibility_ir(ir)
    direct = serialize_state(ir)
    public = serialize_typed_ir_result("state", ir)

    assert ir == original
    assert field not in validated
    assert "accTitle: S\u200btate reconstruction" in direct
    assert "accTitle: S\u200btate reconstruction" in public.code


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
@pytest.mark.parametrize(
    "value",
    [
        False,
        0,
        0.0,
        [],
        {},
        b"",
        _StringSubclass("text"),
        " ",
        "\t",
        "A\nB",
        "A\x00B",
        "A\u200bB",
        "A\u2028B",
        "A\u2029B",
        "A\ud800B",
        "x" * (MAX_TEXT_CHARS + 1),
    ],
)
def test_state_accessibility_metadata_rejects_malformed_raw_text_before_enrichment(
    field: str,
    value: object,
) -> None:
    ir = {**_state_ir(), field: value}

    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_typed_ir_result("state", ir)


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_state_metadata_validation_does_not_invoke_user_coercion_hooks(field: str) -> None:
    ir = {**_state_ir(), field: _ExplosiveLabel()}

    with pytest.raises(SerializationError, match="must be text"):
        serialize_typed_ir_result("state", ir)


def test_state_accessibility_plan_preserves_safe_text_and_discloses_terminal_substitutions() -> (
    None
):
    ir = {
        **_state_ir(),
        "title": 'A &#35; <b> &amp; **bold** "title" \\ path 3 > 2',
        "description": "<!--note--> `code` [link](target)",
    }

    plan = plan_state_accessibility(ir, experimental=False)
    result = serialize_typed_ir_result("state", ir)

    assert plan.title_canvas == 'A ＆＃35; ‹b> &amp; **bold** "title" \\ path 3 > 2'
    assert plan.description_canvas == "‹!--note--> `code` [link](target)"
    assert plan.compatibility_substitutions
    assert STATE_TEXT_COMPATIBILITY_WARNING in result.warnings


def test_state_accessibility_planning_is_bounded_for_adversarial_less_than_text() -> None:
    title = "<a" * (MAX_TEXT_CHARS // 2)
    started = time.perf_counter()

    plan = plan_state_accessibility({**_state_ir(), "title": title}, experimental=False)

    assert time.perf_counter() - started < 5
    assert plan.title_canvas == "‹a" * (MAX_TEXT_CHARS // 2)
    assert plan.compatibility_substitutions


def test_state_exact_empty_defaults_and_hidden_pseudostate_labels_remain_compatible() -> None:
    ir = {
        "states": [
            {"id": "active", "label": "", "evidence_ids": ["ocr-active"]},
            {
                "id": "decision",
                "kind": "choice",
                "label": "   ",
                "evidence_ids": ["shape-decision"],
            },
        ],
        "transitions": [
            {
                "source": "active",
                "target": "decision",
                "label": "",
                "evidence_ids": ["arrow-decision"],
            }
        ],
    }

    plan = plan_state_records(ir)
    code = serialize_state(ir)

    assert plan.nodes[0].visible_label == "active"
    assert plan.nodes[1].kind == "choice"
    assert plan.transitions[0].visible_label is None
    assert not plan.compatibility_substitutions
    assert 'state "active" as active' in code
    assert "state decision <<choice>>" in code
    assert "active --> decision\n" in code


@pytest.mark.parametrize(
    "reserved_id",
    [
        "accDescr",
        "accTitle",
        "as",
        "class",
        "classDef",
        "click",
        "default",
        "direction",
        "href",
        "iconify",
        "linkStyle",
        "my_iconify_node",
        "note",
        "scale",
        "state",
        "stateDiagram",
        "stateDiagram-v2",
        "style",
    ],
)
def test_state_plan_maps_mermaid_reserved_ids_without_losing_source_identity(
    reserved_id: str,
) -> None:
    ir = {
        "states": [
            {"id": reserved_id, "label": "Reserved", "evidence_ids": ["ocr-reserved"]},
            {"id": "target", "label": "Target", "evidence_ids": ["ocr-target"]},
        ],
        "transitions": [
            {
                "source": reserved_id,
                "target": "target",
                "label": "Advance",
                "evidence_ids": ["arrow-advance"],
            }
        ],
    }

    plan = plan_state_records(ir)
    code = serialize_state(ir)
    scene = typed_ir_to_scene("state", ir)

    assert plan.nodes[0].source_id == reserved_id
    assert plan.nodes[0].source_record is ir["states"][0]
    assert plan.nodes[0].emitted_id.startswith("mmx_")
    assert plan.transitions[0].source_id == plan.nodes[0].emitted_id
    assert f"as {plan.nodes[0].emitted_id}" in code
    assert f"{plan.nodes[0].emitted_id} --> target : Advance" in code
    assert scene is not None
    assert scene.elements[0].id == plan.nodes[0].emitted_id
    assert scene.elements[0].evidence_ids == ["ocr-reserved"]
    assert scene.relations[0].source_id == plan.nodes[0].emitted_id


def test_state_reserved_id_mapping_does_not_steal_existing_normalized_ids() -> None:
    ir = {
        "states": [
            {"id": "state", "label": "Reserved", "evidence_ids": ["ocr-reserved"]},
            {
                "id": "mmx_state_id_1",
                "label": "Literal",
                "evidence_ids": ["ocr-literal"],
            },
            {"id": "target", "label": "Target", "evidence_ids": ["ocr-target"]},
        ],
        "transitions": [
            {
                "source": "state",
                "target": "mmx_state_id_1",
                "evidence_ids": ["arrow-first"],
            },
            {
                "source": "mmx_state_id_1",
                "target": "target",
                "evidence_ids": ["arrow-second"],
            },
        ],
    }

    plan = plan_state_records(ir)

    assert [(node.source_id, node.emitted_id) for node in plan.nodes] == [
        ("state", "mmx_state_id_1_2"),
        ("mmx_state_id_1", "mmx_state_id_1"),
        ("target", "target"),
    ]
    assert [(edge.source_id, edge.target_id) for edge in plan.transitions] == [
        ("mmx_state_id_1_2", "mmx_state_id_1"),
        ("mmx_state_id_1", "target"),
    ]


def test_state_hidden_pseudostate_labels_cannot_reenter_accessibility_derivation() -> None:
    ir = {
        "states": [
            {"id": "active", "label": "Active", "evidence_ids": ["ocr-active"]},
            {
                "id": "decision",
                "kind": "choice",
                "label": "hidden secret",
                "evidence_ids": ["shape-decision"],
            },
        ],
        "transitions": [],
    }

    direct = serialize_state(ir)
    public = serialize_typed_ir_result("state", ir)

    assert "containing Active, decision" in direct
    assert "hidden secret" not in direct
    assert "hidden secret" not in public.code

    ir["states"][1]["label"] = _ExplosiveLabel()
    assert "containing Active, decision" in serialize_state(ir)
    assert "containing Active, decision" in serialize_typed_ir_result("state", ir).code


def test_state_pipeline_accepts_strict_safe_pseudostate_declaration() -> None:
    observation = _state_observation(node_label="Active", evidence_text="Active")
    observation.typed_candidates[0].ir["states"].append(
        {
            "id": "decision",
            "kind": "choice",
            "label": "hidden secret",
            "bbox": [40, 10, 60, 30],
            "evidence_ids": ["shape-decision"],
        }
    )
    observation.evidence.append(
        VisualEvidence(
            id="shape-decision",
            kind="contour",
            bbox=(40, 10, 60, 30),
        )
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _StateRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "state-source",
        "source.png",
        Image.new("RGB", (100, 50), "white"),
    )

    assert result.selected is not None
    assert len(runtime.calls) == 1
    assert "state decision <<choice>>" in result.selected.mermaid_code
    assert "hidden secret" not in result.selected.mermaid_code
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir


@pytest.mark.integration
def test_state_mermaid_11_16_accepts_strict_safe_pseudostate_declarations() -> None:
    ir = {
        "states": [
            {"id": "decision", "kind": "choice", "evidence_ids": ["choice"]},
            {"id": "parallel_start", "kind": "fork", "evidence_ids": ["fork"]},
            {"id": "parallel_end", "kind": "join", "evidence_ids": ["join"]},
        ],
        "transitions": [],
    }
    code = serialize_state(ir)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error


@pytest.mark.integration
def test_state_mermaid_11_16_renders_every_reserved_id_transition() -> None:
    reserved_ids = [
        "accDescr",
        "accTitle",
        "as",
        "class",
        "classDef",
        "click",
        "default",
        "direction",
        "href",
        "iconify",
        "linkStyle",
        "my_iconify_node",
        "note",
        "scale",
        "state",
        "stateDiagram",
        "stateDiagram-v2",
        "style",
    ]
    ir = {
        "states": [
            {
                "id": source_id,
                "label": f"Node {index}",
                "evidence_ids": [f"node-{index}"],
            }
            for index, source_id in enumerate(reserved_ids, start=1)
        ],
        "transitions": [
            {
                "source": reserved_ids[index - 1],
                "target": reserved_ids[index],
                "label": f"edge {index}",
                "evidence_ids": [f"edge-{index}"],
            }
            for index in range(1, len(reserved_ids))
        ],
    }
    plan = plan_state_records(ir)
    code = serialize_state(ir)

    assert all(node.emitted_id.startswith("mmx_") for node in plan.nodes)
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    root = ET.fromstring(outcome.runtime.svg)
    transition_paths = [
        element
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "path"
        and "transition" in element.attrib.get("class", "").split()
    ]
    assert len(transition_paths) == len(plan.transitions)
    canvas_text = " ".join(" ".join(root.itertext()).split())
    assert all(f"edge {index}" in canvas_text for index in range(1, len(reserved_ids)))


@pytest.mark.integration
def test_state_mermaid_11_16_svg_matches_frozen_node_transition_and_accessibility_text() -> None:
    ir = {
        "title": 'direction LR State "title" \\ path A &#35; <b> &amp; **bold**',
        "description": 'direction LR State "description" \\ path <!--note-->',
        **_state_ir(
            node_label=(
                'direction LR Active "review" \\ path **bold** _ital_ `code` '
                "~~strike~~ [link](target) A &amp; B A &#35; B"
            ),
            transition_label=(
                'direction LR go "now" \\ path **bold** _ital_ `code` ~~strike~~ [link](target)'
            ),
        ),
    }
    ir["states"][1]["label"] = "order_id a__b a~~b [x] # Heading 2*3"
    code = serialize_state(ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    root = ET.fromstring(outcome.runtime.svg)
    title = next(element for element in root if element.tag.rsplit("}", 1)[-1] == "title")
    description = next(element for element in root if element.tag.rsplit("}", 1)[-1] == "desc")
    assert (title.text or "").replace("\u200b", "") == (
        'direction LR State "title" \\ path A ＆＃35; ‹b> &amp; **bold**'
    )
    assert (description.text or "").replace("\u200b", "") == (
        'direction LR State "description" \\ path ‹!--note-->'
    )

    canvas_fragments = [
        " ".join((element.text or "").split())
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] not in {"style", "title", "desc"}
        and (element.text or "").strip()
    ]
    canvas_text = " ".join(canvas_fragments).replace("\u200b", "")
    assert (
        "direction LR Active ″review″ \\ path ∗∗bold∗∗ ＿ital＿ ｀code｀ ～～strike～～ "
        "［link］(target) A ＆amp; B A ＆＃35; B"
    ) in canvas_text
    assert (
        'direction LR go "now" \\ path ∗∗bold∗∗ ＿ital＿ ｀code｀ ～～strike～～ ［link］(target)'
    ) in canvas_text
    assert "order_id a__b a~~b [x] # Heading 2*3" in canvas_text
    assert "&quot;" not in canvas_text


@pytest.mark.integration
def test_state_mermaid_11_16_preserves_extended_markdown_and_active_backslash_plan() -> None:
    node_label = (
        "``code`` []() [outer [inner]](target) ***both*** a~b~c ~~~literal~~~ "
        "<user@example.com> u@foo_bar.example.com xwww.example.com"
    )
    transition_label = (
        "\\ A\\ \\A A\\\\B A\\`B A\\[B A\\*B A\\B A\\ B user@example.com "
        "u@foo_bar.example.com www.example.com xwww.example.com"
    )
    ir = _state_ir(node_label=node_label, transition_label=transition_label)
    plan = plan_state_records(ir)
    code = serialize_state(ir)
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    root = ET.fromstring(outcome.runtime.svg)
    canvas_fragments: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "text":
            continue
        rows = [
            descendant
            for descendant in element.iter()
            if descendant is not element
            and descendant.tag.rsplit("}", 1)[-1] == "tspan"
            and "row" in descendant.attrib.get("class", "").split()
        ]
        if rows:
            canvas_fragments.extend(" ".join("".join(row.itertext()).split()) for row in rows)
        elif "".join(element.itertext()).strip():
            canvas_fragments.append(" ".join("".join(element.itertext()).split()))
    canvas_text = " ".join(canvas_fragments).replace("\u200b", "")
    assert plan.nodes[0].visible_label in canvas_text
    assert plan.transitions[0].visible_label in canvas_text
