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
    MINDMAP_TEXT_COMPATIBILITY_WARNING,
    SerializationError,
    enrich_mindmap_accessibility_ir,
    plan_mindmap_records,
    serialize_mindmap,
    serialize_typed_ir_result,
    validated_mindmap_accessibility_ir,
)
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


class _MindmapRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="mindmap",
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"/>',
        )

    def close(self) -> None:
        pass


class _MindmapLabelRepair:
    name = "mindmap_label_repair"

    def __init__(self, label: str) -> None:
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["root"]["children"][0]["label"] = self.label
        serialized = serialize_typed_ir_result("mindmap", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _MindmapMetadataRepair:
    name = "mindmap_metadata_repair"

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["acc_title"] = " "
        return RepairProposal(
            code=f'{candidate.mermaid_code.rstrip()}\n        phantom["Phantom"]\n',
            operation=self.name,
            typed_ir=typed_ir,
        )


def _mindmap_ir(*, child_label: object = "Child") -> dict[str, object]:
    return {
        "root": {
            "id": "logical-root",
            "label": "Root",
            "bbox": [0, 0, 100, 40],
            "evidence_ids": ["ocr-root"],
            "children": [
                {
                    "id": "logical-child",
                    "label": child_label,
                    "bbox": [120, 0, 220, 40],
                    "evidence_ids": ["ocr-child"],
                }
            ],
        }
    }


def _mindmap_observation(
    *,
    child_label: str = "Child",
    evidence_text: str = "Child",
) -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["mindmap"], scores=[1]),
        typed_candidates=[
            TypedIRCandidate(diagram_type="mindmap", ir=_mindmap_ir(child_label=child_label))
        ],
        evidence=[
            VisualEvidence(
                id="ocr-root",
                kind="ocr_token",
                text="Root",
                bbox=(0, 0, 100, 40),
            ),
            VisualEvidence(
                id="ocr-child",
                kind="ocr_token",
                text=evidence_text,
                bbox=(120, 0, 220, 40),
            ),
        ],
    )


def test_mindmap_plan_freezes_semantic_source_canvas_without_mutation() -> None:
    hostile = (
        '  Root\t"quoted" \\ path *bold* `code` ~~strike~~ _ital_ '
        "[link](target) &amp; &#35; #59; &#X41; #X42; &#foo; #xZZ; "
        "<tag> %%{init} click style iconify "
        "https://example.invalid callback(x) @import config:\r\n한국어  "
    )
    ir = {
        "root": {
            "id": "same",
            "label": hostile,
            "children": [{"id": "same", "text": f"Child {hostile}"}],
        }
    }
    original = copy.deepcopy(ir)

    plan = plan_mindmap_records(ir)
    code = serialize_mindmap(ir)

    semantic = " ".join(hostile.split())
    expected_canvas = (
        semantic.replace('"', "″")
        .replace("*", "＊")
        .replace("`", "ˋ")
        .replace("~", "～")
        .replace("&#35;", "＆＃35;")
        .replace("#59;", "＃59;")
        .replace("&#X41;", "＆＃X41;")
        .replace("#X42;", "＃X42;")
        .replace("&#foo;", "＆＃foo;")
        .replace("#xZZ;", "＃xZZ;")
    )
    assert ir == original
    assert [node.emitted_id for node in plan.nodes] == ["root", "node_2"]
    assert [node.source_id for node in plan.nodes] == ["same", "same"]
    assert plan.nodes[0].text.semantic == semantic
    assert plan.nodes[0].text.canvas == expected_canvas
    assert plan.nodes[1].text.semantic == f"Child {semantic}"
    assert plan.nodes[1].text.canvas == f"Child {expected_canvas}"
    assert plan.compatibility_substitutions
    assert all(node.text.source.startswith("\u200b") for node in plan.nodes)
    assert "&\u200bamp;" in plan.nodes[0].text.source
    assert "&lt;tag&gt;" in plan.nodes[0].text.source
    assert "\\\u200b" in plan.nodes[0].text.source
    assert hostile not in code
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(code).safe


def test_mindmap_uses_quoted_shapes_and_conditional_compatibility_warning() -> None:
    plain = serialize_typed_ir_result("mindmap", _mindmap_ir())
    compatible = serialize_typed_ir_result(
        "mindmap",
        _mindmap_ir(child_label='Child "quoted" *literal*'),
    )

    assert 'root(("\u200bRoot"))' in plain.code
    assert 'node_2["\u200bChild"]' in plain.code
    assert MINDMAP_TEXT_COMPATIBILITY_WARNING not in plain.warnings
    assert MINDMAP_TEXT_COMPATIBILITY_WARNING in compatible.warnings
    assert "″quoted″" in compatible.code
    assert "＊literal＊" in compatible.code
    assert all("cannot safely emit accTitle/accDescr" in warning for warning in plain.warnings)


def test_mindmap_aliases_and_unreadable_fallbacks_are_exact() -> None:
    plan = plan_mindmap_records(
        {
            "root": {
                "label": " Root ",
                "text": "Root",
                "children": [
                    {"label": "", "text": "Child"},
                    {"label": "", "text": ""},
                    {},
                ],
            }
        }
    )

    assert [node.text.semantic for node in plan.nodes] == [
        "Root",
        "Child",
        "[unreadable]",
        "[unreadable]",
    ]


@pytest.mark.parametrize(
    ("root", "error"),
    [
        ({"label": 1}, "label must be text"),
        ({"text": False}, "text must be text"),
        ({"label": "One", "text": "Two"}, "conflicting label/text"),
        ({"label": " "}, "bounded non-empty"),
        ({"label": "A\x00B"}, "unsupported text"),
        ({"label": "A\u200bB"}, "unsupported text"),
        ({"label": "A\ud800B"}, "unsupported text|UTF-8"),
        ({"id": 1, "label": "Root"}, "id must be a string"),
        ({"id": " ", "label": "Root"}, "id must be a bounded non-empty"),
        ({"label": "Root", "children": "Child"}, "children must be a list"),
        ({"label": "Root", "children": [None]}, "children must be objects"),
    ],
)
def test_mindmap_malformed_recursive_records_fail_closed(
    root: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(SerializationError, match=error):
        plan_mindmap_records({"root": root})
    with pytest.raises(SerializationError, match=error):
        serialize_mindmap({"root": root})
    assert typed_ir_to_scene("mindmap", {"root": root}) is None


def test_mindmap_object_reuse_and_cycles_fail_closed() -> None:
    shared = {"label": "Shared"}
    reused = {"root": {"label": "Root", "children": [shared, shared]}}
    cyclic_root: dict[str, object] = {"label": "Root"}
    cyclic_root["children"] = [cyclic_root]

    for ir in (reused, {"root": cyclic_root}):
        with pytest.raises(SerializationError, match="objects must not be reused"):
            plan_mindmap_records(ir)
        assert typed_ir_to_scene("mindmap", ir) is None


def test_mindmap_scene_ocr_topology_and_provenance_share_terminal_plan() -> None:
    ir = {
        "direction": "RL",
        "root": {
            "id": "spoofed-root",
            "label": 'Root "quoted"',
            "role": "admin",
            "shape": "diamond",
            "bbox": [0, 0, 100, 40],
            "evidence_ids": ["ocr-root"],
            "children": [
                {
                    "id": "spoofed-child",
                    "label": "Child *literal*",
                    "role": "admin",
                    "shape": "circle",
                    "bbox": [120, 0, 220, 40],
                    "evidence_ids": ["ocr-child"],
                }
            ],
        },
    }
    plan = plan_mindmap_records(ir)
    scene = typed_ir_to_scene("mindmap", ir)

    assert scene is not None
    assert scene.reading_direction == "radial"
    assert [(item.id, item.role, item.shape, item.text) for item in scene.elements] == [
        ("root", "root", "circle", "Root ″quoted″"),
        ("node_2", "node", "rectangle", "Child ＊literal＊"),
    ]
    assert scene.elements[0].bbox == (0.0, 0.0, 100.0, 40.0)
    assert scene.elements[0].evidence_ids == ["ocr-root"]
    assert len(scene.relations) == 1
    relation = scene.relations[0]
    assert relation.id == "generated-relation-1"
    assert (relation.source_id, relation.target_id) == ("root", "node_2")
    assert relation.relation_type == "containment"
    assert relation.semantic_relation == "containment"
    assert not relation.arrow_at_start and not relation.arrow_at_end
    assert relation.evidence_ids == ["ocr-child"]
    assert list(typed_ir_semantic_texts("mindmap", ir, scene)) == [
        node.text.canvas for node in plan.nodes
    ]


@pytest.mark.parametrize("field", ["title", "description", "acc_title", "acc_description"])
def test_mindmap_exact_empty_metadata_is_omitted_without_mutation(field: str) -> None:
    ir = {**_mindmap_ir(), field: ""}
    original = copy.deepcopy(ir)

    validated = validated_mindmap_accessibility_ir(ir)
    enriched = enrich_mindmap_accessibility_ir(ir, experimental=False)
    result = serialize_typed_ir_result("mindmap", ir)

    assert ir == original
    assert field not in validated
    if field in {"title", "description"}:
        assert field not in enriched
    assert enriched["acc_title"]
    assert enriched["acc_description"]
    assert "Root" in enriched["acc_description"]
    assert "Child" in enriched["acc_description"]
    assert "accTitle:" not in result.code
    assert "accDescr:" not in result.code


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
def test_mindmap_raw_metadata_rejects_invalid_text(field: str, value: object) -> None:
    ir = {**_mindmap_ir(), field: value}

    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        validated_mindmap_accessibility_ir(ir)
    with pytest.raises(SerializationError, match="must be text|bounded|unsupported|UTF-8"):
        serialize_typed_ir_result("mindmap", ir)


def test_mindmap_pipeline_stores_raw_snapshot_not_generated_accessibility() -> None:
    observation = _mindmap_observation()
    source_ir = observation.typed_candidates[0].ir
    source_snapshot = copy.deepcopy(source_ir)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_MindmapRuntime(), config.security_profile),
    ).reconstruct(
        "mindmap-source",
        "source.png",
        Image.new("RGB", (240, 50), "white"),
    )

    assert source_ir == source_snapshot
    assert result.selected is not None
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir


def test_mindmap_pipeline_rejects_raw_metadata_before_runtime() -> None:
    observation = _mindmap_observation()
    observation.typed_candidates[0].ir["acc_title"] = " "
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _MindmapRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "mindmap-source",
        "source.png",
        Image.new("RGB", (240, 50), "white"),
    )

    assert result.selected is None
    assert runtime.calls == []
    assert any(failure.stage == "serialization" for failure in result.failures)


def test_mindmap_accepted_repair_keeps_raw_snapshot_and_reconciles_warning() -> None:
    observation = _mindmap_observation(child_label='Chld "quoted"')
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_MindmapRuntime(), config.security_profile),
        repair_engine=_MindmapLabelRepair("Child"),
    ).reconstruct(
        "mindmap-source",
        "source.png",
        Image.new("RGB", (240, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["root"]["children"][0]["label"] == "Child"
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    assert MINDMAP_TEXT_COMPATIBILITY_WARNING not in result.selected.warnings
    repaired_accessibility = enrich_mindmap_accessibility_ir(
        result.selected.typed_ir,
        experimental=True,
    )
    assert "Child" in repaired_accessibility["acc_description"]
    assert "Chld" not in repaired_accessibility["acc_description"]


def test_mindmap_accepted_repair_adds_current_compatibility_warning() -> None:
    observation = _mindmap_observation(
        child_label="Chld",
        evidence_text='Child "quoted"',
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_MindmapRuntime(), config.security_profile),
        repair_engine=_MindmapLabelRepair('Child "quoted"'),
    ).reconstruct(
        "mindmap-source",
        "source.png",
        Image.new("RGB", (240, 50), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert MINDMAP_TEXT_COMPATIBILITY_WARNING in result.selected.warnings


def test_mindmap_repair_rejects_invalid_raw_metadata_before_second_runtime() -> None:
    observation = _mindmap_observation(child_label="Chld")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _MindmapRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_MindmapMetadataRepair(),
    ).reconstruct(
        "mindmap-source",
        "source.png",
        Image.new("RGB", (240, 50), "white"),
    )

    assert result.selected is not None
    assert len(runtime.calls) == 1
    assert not result.selected.repair_history[-1].accepted
    assert any(
        warning == "semantic repair IR could not be serialized: SerializationError"
        for warning in result.selected.warnings
    )


def test_mindmap_shared_plan_enforces_source_line_and_utf16_budgets() -> None:
    line_heavy_ir = {
        "root": {
            "label": "Root",
            "children": [{"label": f"Node {index}"} for index in range(4_998)],
        }
    }
    character_heavy_ir = {"root": {"label": "A" * 49_990}}
    utf16_heavy_ir = {"root": {"label": "😀" * 24_990}}

    with pytest.raises(SerializationError, match="source-line limit"):
        plan_mindmap_records(line_heavy_ir)
    with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
        plan_mindmap_records(character_heavy_ir)
    with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
        plan_mindmap_records(utf16_heavy_ir)
    assert typed_ir_to_scene("mindmap", line_heavy_ir) is None
    assert typed_ir_to_scene("mindmap", character_heavy_ir) is None
    assert typed_ir_to_scene("mindmap", utf16_heavy_ir) is None


@pytest.mark.integration
def test_mindmap_mermaid_11_16_svg_matches_terminal_canvas() -> None:
    hostile = (
        'Root "quoted" \\ path *bold* `code` ~~strike~~ _ital_ [link](target) '
        "&amp; &#35; #59; &#foo; #xZZ; <tag> %%{init} click style iconify "
        "https://evil.invalid callback(x) @import config: 한국어"
    )
    ir = {
        "root": {
            "label": hostile,
            "children": [
                {"label": f"Child {hostile}"},
                {"label": "] ) (( // --- participant sequenceDiagram accTitle"},
            ],
        }
    }
    plan = plan_mindmap_records(ir)
    result = serialize_typed_ir_result("mindmap", ir)
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
    visible_text = " ".join("".join(root.itertext()).replace("\u200b", "").split())
    for node in plan.nodes:
        assert node.text.canvas in visible_text
    assert not any(element.tag.rsplit("}", 1)[-1] in {"title", "desc"} for element in list(root))
    assert MINDMAP_TEXT_COMPATIBILITY_WARNING in result.warnings
