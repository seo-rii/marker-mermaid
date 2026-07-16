from __future__ import annotations

import copy
from xml.etree import ElementTree as ET

import pytest
from PIL import Image

import marker_mermaid.serializers as serializers_module
from marker_mermaid.candidate_scene import typed_ir_semantic_texts, typed_ir_to_scene
from marker_mermaid.config import MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    MAX_SCENE_ELEMENTS,
    DiagramTypePrediction,
    EngineObservation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.protocols import RepairProposal, RuntimeResult
from marker_mermaid.scoring import ocr_recall
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.serializers import (
    ARCHITECTURE_TEXT_COMPATIBILITY_WARNING,
    SerializationError,
    enrich_architecture_accessibility_ir,
    plan_architecture_records,
    serialize_architecture,
    serialize_architecture_flowchart_fallback,
    serialize_runtime_fallback_result,
    serialize_typed_ir_result,
    validated_architecture_accessibility_ir,
)
from marker_mermaid.serializers_phase2 import serialize_phase2
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime

HOSTILE_TEXT = (
    'Quoted "value" \\ path *bold* `code` ~~strike~~ _ital_ [link](target) '
    "&amp; &#35; #59; &#foo; #xZZ; <tag> %%{init} click style iconify "
    "https://evil.invalid callback(x) @import config: 한국어"
)


class _ArchitectureRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="architecture",
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80"/>',
        )

    def close(self) -> None:
        pass


class _ArchitectureFallbackRuntime(_ArchitectureRuntime):
    def validate_and_render(self, code: str, timeout_seconds: float) -> RuntimeResult:
        del timeout_seconds
        self.calls.append(code)
        if code.startswith("architecture-beta"):
            return RuntimeResult(
                syntax_valid=True,
                render_valid=False,
                diagram_type="architecture",
                error="native parser rejected architecture-beta",
            )
        return RuntimeResult(
            syntax_valid=True,
            render_valid=True,
            diagram_type="flowchart-v2",
            svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 80"/>',
        )


class _ArchitectureLabelRepair:
    name = "architecture_label_repair"

    def __init__(self, label: str) -> None:
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["services"][0]["label"] = self.label
        serialized = serialize_typed_ir_result("architecture", typed_ir, experimental=True)
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


class _ArchitectureMetadataRepair:
    name = "architecture_metadata_repair"

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        typed_ir["acc_title"] = " "
        return RepairProposal(
            code=f'{candidate.mermaid_code.rstrip()}\n    service phantom(server)["Phantom"]\n',
            operation=self.name,
            typed_ir=typed_ir,
        )


class _ArchitectureFamilyLabelRepair:
    name = "architecture_family_label_repair"

    def __init__(self, diagram_type: str, label: str) -> None:
        self.diagram_type = diagram_type
        self.label = label

    def repair(self, context: object, candidate: object) -> RepairProposal:
        del context
        typed_ir = copy.deepcopy(candidate.typed_ir)
        root_field = {
            "c4": "elements",
            "deployment": "nodes",
            "component": "components",
        }[self.diagram_type]
        typed_ir[root_field][0]["label"] = self.label
        serialized = serialize_typed_ir_result(
            self.diagram_type,
            typed_ir,
            experimental=True,
        )
        if candidate.emitted_diagram_type == "flowchart":
            serialized = serialize_runtime_fallback_result(
                self.diagram_type,
                typed_ir,
                experimental=True,
            )
            assert serialized is not None
        return RepairProposal(
            code=serialized.code,
            operation=self.name,
            typed_ir=typed_ir,
        )


def _architecture_ir(*, label: object = HOSTILE_TEXT) -> dict[str, object]:
    return {
        "title": 'Title "quoted" \\ &amp; &#35; <tag> %%{init} click 한국어',
        "description": "Description *literal* callback(x) https://evil.invalid <end>",
        "groups": [
            {
                "id": "core zone",
                "label": f"Group {HOSTILE_TEXT}",
                "bbox": [0, 0, 240, 100],
            }
        ],
        "services": [
            {
                "id": "api-service",
                "label": label,
                "group": "core zone",
                "bbox": [10, 10, 100, 50],
                "evidence_ids": ["ocr-api"],
            },
            {
                "id": "database",
                "label": "Database",
                "group": "core zone",
                "bbox": [130, 10, 220, 50],
                "evidence_ids": ["ocr-db"],
            },
        ],
        "edges": [
            {
                "source": "api-service",
                "target": "database",
                "bidirectional": True,
                "evidence_ids": ["line-1"],
            }
        ],
    }


def _architecture_observation(
    *,
    label: str = "API",
    evidence_text: str = "API",
) -> EngineObservation:
    ir = {
        "services": [
            {
                "id": "api",
                "label": label,
                "bbox": [0, 0, 100, 40],
                "evidence_ids": ["ocr-api"],
            },
            {
                "id": "db",
                "label": "Database",
                "bbox": [120, 0, 220, 40],
                "evidence_ids": ["ocr-db"],
            },
        ],
        "edges": [
            {
                "source": "api",
                "target": "db",
                "evidence_ids": ["line-api-db"],
            }
        ],
    }
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["architecture"], scores=[1]),
        typed_candidates=[TypedIRCandidate(diagram_type="architecture", ir=ir)],
        evidence=[
            VisualEvidence(
                id="ocr-api",
                kind="ocr_token",
                text=evidence_text,
                bbox=(0, 0, 100, 40),
            ),
            VisualEvidence(
                id="ocr-db",
                kind="ocr_token",
                text="Database",
                bbox=(120, 0, 220, 40),
            ),
            VisualEvidence(
                id="line-api-db",
                kind="line_segment",
                bbox=(90, 20, 130, 20),
            ),
        ],
    )


def _c4_observation(*, label: str, evidence_text: str) -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["c4"], scores=[1]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="c4",
                ir={
                    "elements": [
                        {
                            "id": "api",
                            "label": label,
                            "bbox": [0, 0, 100, 40],
                            "evidence_ids": ["ocr-api"],
                        }
                    ]
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="ocr-api",
                kind="ocr_token",
                text=evidence_text,
                bbox=(0, 0, 100, 40),
            )
        ],
    )


def _architecture_family_observation(
    diagram_type: str,
    *,
    label: str,
    evidence_text: str,
) -> EngineObservation:
    root_field = {
        "c4": "elements",
        "deployment": "nodes",
        "component": "components",
    }[diagram_type]
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=[diagram_type], scores=[1]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type=diagram_type,
                ir={
                    root_field: [
                        {
                            "id": "api",
                            "label": label,
                            "bbox": [0, 0, 100, 40],
                            "evidence_ids": ["ocr-api"],
                        }
                    ]
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="ocr-api",
                kind="ocr_token",
                text=evidence_text,
                bbox=(0, 0, 100, 40),
            )
        ],
    )


def _visible_svg_text(svg: str) -> str:
    root = ET.fromstring(svg)
    return " ".join("".join(root.itertext()).replace("\u200b", "").split())


def test_architecture_plan_freezes_terminal_text_for_native_and_fallback() -> None:
    ir = _architecture_ir()
    plan = plan_architecture_records(ir)
    native = serialize_architecture(ir)
    fallback = serialize_architecture_flowchart_fallback(ir)

    assert plan.compatibility_substitutions
    assert plan.services[0].text.semantic == HOSTILE_TEXT
    assert plan.services[0].text.canvas != HOSTILE_TEXT
    assert plan.services[0].text.source in native
    assert plan.services[0].text.source in fallback
    assert plan.groups[0].text.source in native
    assert plan.groups[0].text.source in fallback
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(native).safe
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(fallback).safe

    result = serialize_typed_ir_result("architecture", ir)
    assert ARCHITECTURE_TEXT_COMPATIBILITY_WARNING in result.warnings


def test_architecture_plain_source_optimization_keeps_emphasis_underscores_neutralized() -> None:
    plain = plan_architecture_records({"services": [{"id": "api", "label": "Core_service 1"}]})
    emphasis = plan_architecture_records({"services": [{"id": "api", "label": "Core _service_ 1"}]})

    assert plain.services[0].text.source == "Core_service 1"
    assert emphasis.services[0].text.source != emphasis.services[0].text.semantic
    assert "\u200b" in emphasis.services[0].text.source


def test_architecture_scene_and_ocr_use_exact_terminal_canvas() -> None:
    ir = _architecture_ir()
    plan = plan_architecture_records(ir)
    scene = typed_ir_to_scene("architecture", ir, emitted_diagram_type="architecture")

    assert scene is not None
    assert [(element.id, element.text, element.evidence_ids) for element in scene.elements] == [
        (
            plan.services[0].emitted_id,
            plan.services[0].text.canvas,
            ["ocr-api"],
        ),
        (plan.services[1].emitted_id, "Database", ["ocr-db"]),
    ]
    assert [(group.id, group.label) for group in scene.groups] == [
        (plan.groups[0].emitted_id, plan.groups[0].text.canvas)
    ]
    assert [
        (relation.id, relation.source_id, relation.target_id) for relation in scene.relations
    ] == [("generated-relation-1", plan.services[0].emitted_id, plan.services[1].emitted_id)]
    assert scene.relations[0].evidence_ids == ["line-1"]
    assert (scene.relations[0].arrow_at_start, scene.relations[0].arrow_at_end) == (
        True,
        True,
    )

    texts = list(typed_ir_semantic_texts("architecture", ir, scene))
    assert texts == [
        plan.services[0].text.canvas,
        plan.services[1].text.canvas,
        plan.groups[0].text.canvas,
    ]
    assert ocr_recall([HOSTILE_TEXT], "", generated_texts=texts) < 1
    assert ocr_recall([plan.services[0].text.canvas], "", generated_texts=texts) == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda ir: ir["services"][0].update(label=17), "service 1 label must be text"),
        (lambda ir: ir["services"][0].update(label=" \t\n "), "bounded non-empty text"),
        (lambda ir: ir["services"][0].update(id=17), "service id must be a string"),
        (lambda ir: ir["groups"][0].update(id="bad\u200bid"), "unsupported text"),
        (lambda ir: ir["groups"][0].update(label="bad\u200btext"), "unsupported text"),
        (lambda ir: ir["edges"][0].pop("source"), "requires source and target"),
        (
            lambda ir: ir["edges"][0].update(source_side="left"),
            "unsupported source_side",
        ),
    ],
)
def test_architecture_plan_rejects_malformed_terminal_records(mutate, message: str) -> None:
    ir = _architecture_ir(label="API")
    mutate(ir)

    with pytest.raises(SerializationError, match=message):
        plan_architecture_records(ir)
    assert typed_ir_to_scene("architecture", ir) is None


def test_architecture_missing_endpoint_does_not_alias_literal_none_service() -> None:
    ir = {
        "services": [{"id": "None", "label": "Named None"}, {"id": "db", "label": "DB"}],
        "edges": [{"target": "db"}],
    }

    with pytest.raises(SerializationError, match="requires source and target"):
        serialize_architecture(ir)
    assert typed_ir_to_scene("architecture", ir) is None


def test_architecture_scene_respects_selected_flowchart_empty_group_failure() -> None:
    ir = {
        "groups": [{"id": "empty", "label": "Empty"}],
        "services": [{"id": "api", "label": "API"}],
    }

    assert typed_ir_to_scene("architecture", ir, emitted_diagram_type="architecture") is not None
    assert typed_ir_to_scene("architecture", ir, emitted_diagram_type="flowchart-v2") is None


def test_architecture_family_accessibility_keeps_requested_semantic_type() -> None:
    ir = {"elements": [{"id": "api", "label": "API"}]}

    native, native_type, _native_reason = serialize_phase2("c4", ir)
    fallback, fallback_type, _fallback_reason = serialize_phase2(
        "c4",
        ir,
        native_runtime_valid=False,
    )

    assert native_type == "architecture"
    assert fallback_type == "flowchart"
    assert "accTitle: C4 model reconstruction" in native
    assert "accTitle: C4 model reconstruction" in fallback


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        ("c4", {"elements": [{"id": "api"}]}),
        ("deployment", {"nodes": [{"id": "api"}]}),
        ("component", {"components": [{"id": "api"}]}),
    ],
)
def test_architecture_family_validates_raw_accessibility_before_generic_enrichment(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    ir["acc_title"] = 17

    with pytest.raises(SerializationError, match="architecture acc_title must be text"):
        serialize_typed_ir_result(diagram_type, ir)


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "c4",
            {"elements": [{"id": "api", "label": 'Quoted "C4" *service*'}]},
        ),
        (
            "deployment",
            {"nodes": [{"id": "api", "label": 'Quoted "deployment" *node*'}]},
        ),
        (
            "component",
            {"components": [{"id": "api", "label": 'Quoted "component" *service*'}]},
        ),
    ],
)
def test_architecture_family_visible_substitutions_have_compatibility_warning(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    result = serialize_typed_ir_result(diagram_type, ir)

    assert result.emitted_type == "architecture"
    assert ARCHITECTURE_TEXT_COMPATIBILITY_WARNING in result.warnings


@pytest.mark.parametrize(
    ("diagram_type", "ir"),
    [
        (
            "architecture",
            {
                "services": [{"id": "api"}, {"id": "db"}],
                "edges": [{"source": "api", "target": "db", "bidirectional": "false"}],
            },
        ),
        (
            "c4",
            {
                "elements": [{"id": "api"}, {"id": "db"}],
                "relations": [{"source": "api", "target": "db", "bidirectional": "false"}],
            },
        ),
        (
            "deployment",
            {
                "nodes": [{"id": "api"}, {"id": "db"}],
                "links": [{"source": "api", "target": "db", "bidirectional": "false"}],
            },
        ),
        (
            "component",
            {
                "components": [{"id": "api"}, {"id": "db"}],
                "dependencies": [{"source": "api", "target": "db", "bidirectional": "false"}],
            },
        ),
    ],
)
def test_architecture_family_rejects_non_boolean_bidirectional(
    diagram_type: str,
    ir: dict[str, object],
) -> None:
    with pytest.raises(SerializationError, match="bidirectional must be a boolean"):
        serialize_typed_ir_result(diagram_type, ir)
    assert typed_ir_to_scene(diagram_type, ir) is None


@pytest.mark.parametrize(
    ("diagram_type", "root_field", "relation_field"),
    [
        ("c4", "elements", "relations"),
        ("deployment", "nodes", "links"),
        ("component", "components", "dependencies"),
    ],
)
@pytest.mark.parametrize("invalid_source", [pytest.param(None, id="missing"), 1])
def test_architecture_family_missing_endpoint_does_not_alias_stringified_node_id(
    diagram_type: str,
    root_field: str,
    relation_field: str,
    invalid_source: object,
) -> None:
    relation: dict[str, object] = {"target": "db"}
    if invalid_source is not None:
        relation["source"] = invalid_source
    ir = {
        root_field: [
            {"id": "None", "label": "Named None"},
            {"id": "1", "label": "Named One"},
            {"id": "db", "label": "DB"},
        ],
        relation_field: [relation],
    }

    with pytest.raises(SerializationError, match="requires source and target"):
        serialize_typed_ir_result(diagram_type, ir)
    assert typed_ir_to_scene(diagram_type, ir) is None


@pytest.mark.parametrize(
    ("diagram_type", "root_field"),
    [
        ("c4", "elements"),
        ("deployment", "nodes"),
        ("component", "components"),
    ],
)
def test_architecture_family_does_not_launder_falsey_non_text_label(
    diagram_type: str,
    root_field: str,
) -> None:
    ir = {root_field: [{"id": "api", "label": 0}]}

    with pytest.raises(SerializationError, match="service 1 label must be text"):
        serialize_typed_ir_result(diagram_type, ir)
    assert typed_ir_to_scene(diagram_type, ir) is None


def test_architecture_scene_omits_malformed_record_local_evidence() -> None:
    ir = _architecture_ir(label="API")
    ir["services"][0]["evidence_ids"] = "ocr-api"
    ir["edges"][0]["evidence_ids"] = "line-1"

    plan = plan_architecture_records(ir)
    scene = typed_ir_to_scene("architecture", ir)

    assert plan.services[0].evidence_ids == ()
    assert plan.services[1].evidence_ids == ("ocr-db",)
    assert plan.relations[0].evidence_ids == ()
    assert scene is not None
    assert scene.elements[0].evidence_ids == []
    assert scene.elements[1].evidence_ids == ["ocr-db"]
    assert scene.relations[0].evidence_ids == []


def test_architecture_raw_accessibility_is_validated_before_enrichment() -> None:
    ir = _architecture_ir(label="API")
    ir["acc_title"] = ""
    ir["acc_description"] = ""
    validated = validated_architecture_accessibility_ir(ir)
    enriched = enrich_architecture_accessibility_ir(validated, experimental=False)
    precomputed = plan_architecture_records(validated, experimental=False)
    enriched_experimental = enrich_architecture_accessibility_ir(
        validated,
        experimental=True,
        architecture_plan=precomputed,
    )

    assert "acc_title" not in validated
    assert "acc_description" not in validated
    assert enriched["acc_title"] == ir["title"]
    assert enriched["acc_description"] == ir["description"]
    assert "experimental" in enriched_experimental["acc_description"]

    for field, value in (
        ("title", " \t\n "),
        ("description", "bad\u200btext"),
        ("acc_title", 17),
    ):
        malformed = _architecture_ir(label="API")
        malformed[field] = value
        with pytest.raises(SerializationError, match=f"architecture {field}"):
            validated_architecture_accessibility_ir(malformed)


def test_architecture_accessibility_codec_does_not_create_visible_compatibility_warning() -> None:
    ir = {
        "title": HOSTILE_TEXT,
        "description": f"Description {HOSTILE_TEXT}",
        "services": [{"id": "api", "label": "API"}],
    }

    plan = plan_architecture_records(ir)
    result = serialize_typed_ir_result("architecture", ir)

    assert plan.accessibility.title_canvas == HOSTILE_TEXT
    assert plan.accessibility.description_canvas == f"Description {HOSTILE_TEXT}"
    assert ARCHITECTURE_TEXT_COMPATIBILITY_WARNING not in result.warnings
    assert MermaidSecurityScanner(SecurityProfile.STRICT).scan(result.code).safe


def test_architecture_pipeline_stores_raw_snapshot_not_derived_accessibility() -> None:
    observation = _architecture_observation()
    source_ir = observation.typed_candidates[0].ir
    source_snapshot = copy.deepcopy(source_ir)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_ArchitectureRuntime(), config.security_profile),
    ).reconstruct(
        "architecture-source",
        "source.png",
        Image.new("RGB", (240, 80), "white"),
    )

    assert source_ir == source_snapshot
    assert result.selected is not None
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir


def test_architecture_pipeline_rejects_invalid_raw_metadata_before_runtime() -> None:
    observation = _architecture_observation()
    observation.typed_candidates[0].ir["acc_title"] = " "
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _ArchitectureRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
    ).reconstruct(
        "architecture-source",
        "source.png",
        Image.new("RGB", (240, 80), "white"),
    )

    assert result.selected is None
    assert runtime.calls == []
    assert any(failure.stage == "serialization" for failure in result.failures)


def test_architecture_accepted_repair_keeps_raw_snapshot_and_reconciles_warning() -> None:
    observation = _architecture_observation(label='APX "quoted"', evidence_text="API")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_ArchitectureRuntime(), config.security_profile),
        repair_engine=_ArchitectureLabelRepair("API"),
    ).reconstruct(
        "architecture-source",
        "source.png",
        Image.new("RGB", (240, 80), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert result.selected.typed_ir["services"][0]["label"] == "API"
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    assert ARCHITECTURE_TEXT_COMPATIBILITY_WARNING not in result.selected.warnings
    repaired_accessibility = enrich_architecture_accessibility_ir(
        result.selected.typed_ir,
        experimental=True,
    )
    assert "API" in repaired_accessibility["acc_description"]
    assert "APX" not in repaired_accessibility["acc_description"]


def test_architecture_accepted_repair_adds_current_compatibility_warning() -> None:
    observation = _architecture_observation(label="APX", evidence_text='API "quoted"')
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_ArchitectureRuntime(), config.security_profile),
        repair_engine=_ArchitectureLabelRepair('API "quoted"'),
    ).reconstruct(
        "architecture-source",
        "source.png",
        Image.new("RGB", (240, 80), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert ARCHITECTURE_TEXT_COMPATIBILITY_WARNING in result.selected.warnings


@pytest.mark.parametrize(
    ("initial_label", "repaired_label", "warning_expected"),
    [
        ('APX "quoted"', "API", False),
        ("APX", 'API "quoted"', True),
    ],
)
def test_c4_accepted_repair_reconciles_architecture_compatibility_warning(
    initial_label: str,
    repaired_label: str,
    warning_expected: bool,
) -> None:
    observation = _c4_observation(label=initial_label, evidence_text=repaired_label)
    config = MermaidConfig(candidate_count=1, publish_min_score=0)

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(_ArchitectureRuntime(), config.security_profile),
        repair_engine=_ArchitectureFamilyLabelRepair("c4", repaired_label),
    ).reconstruct(
        "c4-source",
        "source.png",
        Image.new("RGB", (120, 60), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert (ARCHITECTURE_TEXT_COMPATIBILITY_WARNING in result.selected.warnings) is warning_expected


@pytest.mark.parametrize("diagram_type", ["c4", "deployment", "component"])
@pytest.mark.parametrize("runtime_fallback", [False, True])
def test_architecture_family_accepted_repair_regenerates_accessibility_from_raw_snapshot(
    diagram_type: str,
    runtime_fallback: bool,
) -> None:
    observation = _architecture_family_observation(
        diagram_type,
        label="APX",
        evidence_text="API",
    )
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _ArchitectureFallbackRuntime() if runtime_fallback else _ArchitectureRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_ArchitectureFamilyLabelRepair(diagram_type, "API"),
    ).reconstruct(
        f"{diagram_type}-source",
        "source.png",
        Image.new("RGB", (120, 60), "white"),
    )

    assert result.selected is not None
    assert result.selected.repair_history[-1].accepted
    assert "acc_title" not in result.selected.typed_ir
    assert "acc_description" not in result.selected.typed_ir
    assert "API" in result.selected.mermaid_code
    assert "APX" not in result.selected.mermaid_code
    assert result.selected.mermaid_code.startswith(
        "flowchart" if runtime_fallback else "architecture-beta"
    )


def test_architecture_repair_rejects_invalid_raw_metadata_before_second_runtime() -> None:
    observation = _architecture_observation(label="APX", evidence_text="API")
    config = MermaidConfig(candidate_count=1, publish_min_score=0)
    runtime = _ArchitectureRuntime()

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(runtime, config.security_profile),
        repair_engine=_ArchitectureMetadataRepair(),
    ).reconstruct(
        "architecture-source",
        "source.png",
        Image.new("RGB", (240, 80), "white"),
    )

    assert result.selected is not None
    assert len(runtime.calls) == 1
    assert not result.selected.repair_history[-1].accepted
    assert any(
        warning == "semantic repair IR could not be serialized: SerializationError"
        for warning in result.selected.warnings
    )


def test_architecture_native_and_fallback_output_budgets_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line_heavy_ir = {
        "services": [{"id": f"service-{index}"} for index in range(MAX_SCENE_ELEMENTS)]
    }
    streaming_character_heavy_ir = {
        "services": [{"id": f"service-{index}", "label": "A" * 40} for index in range(1_000)]
    }
    character_heavy_ir = {"services": [{"id": "api", "label": "A" * 49_990}]}
    utf16_heavy_ir = {"services": [{"id": "api", "label": "😀" * 24_990}]}

    measured_chunks: list[int] = []
    original_utf16_units = serializers_module._mermaid_utf16_units

    def measured_utf16_units(text: str) -> int:
        measured_chunks.append(len(text))
        return original_utf16_units(text)

    monkeypatch.setattr(serializers_module, "_mermaid_utf16_units", measured_utf16_units)

    for serializer in (serialize_architecture, serialize_architecture_flowchart_fallback):
        measured_chunks.clear()
        with pytest.raises(SerializationError, match="source-line limit"):
            serializer(line_heavy_ir)
        assert measured_chunks == []
        with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
            serializer(streaming_character_heavy_ir)
        assert measured_chunks
        assert max(measured_chunks) < 1_000
        with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
            serializer(character_heavy_ir)
        with pytest.raises(SerializationError, match="UTF-16 source-character limit"):
            serializer(utf16_heavy_ir)
    assert typed_ir_to_scene("architecture", line_heavy_ir) is None
    assert typed_ir_to_scene("architecture", character_heavy_ir) is None
    assert typed_ir_to_scene("architecture", utf16_heavy_ir) is None


@pytest.mark.integration
@pytest.mark.parametrize("terminal", ["native", "fallback"])
def test_architecture_mermaid_11_16_svg_matches_terminal_plan(terminal: str) -> None:
    ir = _architecture_ir()
    plan = plan_architecture_records(ir, experimental=False)
    code = (
        serialize_architecture(ir)
        if terminal == "native"
        else serialize_architecture_flowchart_fallback(ir)
    )
    runtime = NodeMermaidRuntime()
    validator = CandidateValidator(runtime, SecurityProfile.STRICT)
    try:
        outcome = validator.validate(code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid, outcome.runtime.error
    assert outcome.runtime.render_valid, outcome.runtime.error
    assert outcome.runtime.svg is not None
    visible = _visible_svg_text(outcome.runtime.svg)
    for service in plan.services:
        assert service.text.canvas in visible
    for group in plan.groups:
        assert group.text.canvas in visible

    root = ET.fromstring(outcome.runtime.svg)
    title = next(element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "title")
    description = next(
        element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "desc"
    )
    assert "".join(title.itertext()).replace("\u200b", "") == plan.accessibility.title_canvas
    assert (
        "".join(description.itertext()).replace("\u200b", "")
        == plan.accessibility.description_canvas
    )
