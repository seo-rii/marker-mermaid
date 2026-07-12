from __future__ import annotations

import pytest
from PIL import Image
from pydantic import ValidationError

from marker_mermaid.config import CompatibilityProfile, MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.style_recovery import recover_flowchart_styles
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime
from marker_mermaid.vector import (
    VectorObservation,
    VectorPrimitive,
    VectorPrimitiveEngine,
    VectorText,
)


def scene():
    return DiagramSceneIR(
        elements=[
            SceneElement(
                id="API",
                role="service",
                text="API",
                bbox=(0, 0, 10, 10),
                fill_color="#ffeeaa",
                border_color="#112233",
                border_style="thick",
            ),
            SceneElement(
                id="DB",
                role="database",
                text="DB",
                bbox=(20, 0, 30, 10),
                fill_color="blue",
                border_style="dashed",
            ),
        ],
        relations=[
            SceneRelation(
                id="E1",
                source_id="API",
                target_id="DB",
                relation_type="flow",
                line_color="#445566",
                line_style="thick",
            )
        ],
        reading_direction="LR",
        diagram_type_candidates=["flowchart"],
    )


def _bold_vector_engine() -> VectorPrimitiveEngine:
    return VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(
            canvas_size=size,
            texts=(VectorText("API", (2, 2, 18, 10), font_weight="bold"),),
            primitives=(
                VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 15), closed=True),
            ),
        )
    )


def _bold_semantic_observation(label: str = "API") -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "nodes": [
                        {
                            "id": "API",
                            "label": label,
                            "evidence_ids": ["vector-text-001"],
                        }
                    ],
                    "edges": [],
                },
            )
        ],
    )


def _bold_evidence_registry() -> dict[str, VisualEvidence]:
    return {
        "vector-text-001": VisualEvidence(
            id="vector-text-001",
            kind="vector_text",
            text="API",
            bbox=(0, 0, 10, 10),
            font_weight="bold",
        )
    }


def test_font_weight_models_reject_arbitrary_css_values():
    with pytest.raises(ValidationError):
        SceneElement(
            id="A",
            role="node",
            bbox=(0, 0, 1, 1),
            font_weight="900;fill:url(https://example.invalid)",
        )
    with pytest.raises(ValidationError):
        VisualEvidence(id="v", kind="vector_text", font_weight="bolder")


def test_style_only_profile_emits_allowlisted_node_and_link_styles():
    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        scene(),
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert result.applied_element_ids == ("API", "DB")
    assert result.applied_link_indexes == (0,)
    assert "style API fill:#ffeeaa,stroke:#112233,stroke-width:3px" in result.code
    assert "style DB fill:blue,stroke-dasharray:5 5" in result.code
    assert "linkStyle 0 stroke:#445566,stroke-width:3px" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STYLE_ONLY).scan(result.code).safe


def test_strict_and_portable_basic_keep_style_only_in_scene_ir():
    code = 'flowchart LR\n    API["API"]\n'
    strict = recover_flowchart_styles(
        code,
        scene(),
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STRICT,
    )
    portable = recover_flowchart_styles(
        code,
        scene(),
        scene(),
        compatibility_profile=CompatibilityProfile.PORTABLE_BASIC,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert strict.code == portable.code == code
    assert not strict.changed and not portable.changed
    assert "do not permit" in strict.warnings[0]


def test_unsupported_colors_and_identifier_collisions_are_not_emitted():
    evidence = scene()
    evidence.elements[0].fill_color = "url(https://attacker.example/x)"
    evidence.elements.append(
        SceneElement(
            id="A-B",
            role="node",
            text="one",
            bbox=(0, 20, 10, 30),
            fill_color="red",
        )
    )
    evidence.elements.append(
        SceneElement(
            id="A B",
            role="node",
            text="two",
            bbox=(20, 20, 30, 30),
            fill_color="green",
        )
    )
    code = 'flowchart LR\n    API["API"]\n    A_B["ambiguous"]\n'

    result = recover_flowchart_styles(
        code,
        evidence,
        evidence,
        compatibility_profile=CompatibilityProfile.PORTABLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert "url(" not in result.code
    assert "style A_B" not in result.code
    assert any("unsupported fill color" in warning for warning in result.warnings)
    assert any("ambiguous normalized" in warning for warning in result.warnings)


def test_non_flowchart_output_is_not_modified():
    result = recover_flowchart_styles(
        "sequenceDiagram\n    API->>DB: call\n",
        scene(),
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert not result.changed
    assert "not flowchart" in result.warnings[0]


def test_color_only_edge_uses_the_existing_allowlist_and_exact_mapping():
    evidence = scene()
    evidence.relations[0].line_style = None

    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        evidence,
        evidence,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert "linkStyle 0 stroke:#445566" in result.code
    assert result.applied_link_indexes == (0,)


def test_unsupported_edge_color_is_disclosed_but_never_emitted():
    evidence = scene()
    evidence.relations[0].line_color = "url(https://attacker.example/edge)"
    evidence.relations[0].line_style = None

    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        evidence,
        evidence,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert "url(" not in result.code
    assert "linkStyle" not in result.code
    assert any("unsupported line color" in warning for warning in result.warnings)


def test_hostile_color_warning_is_single_line_and_bounded():
    evidence = scene()
    evidence.relations[0].line_color = "bad\n" + "x" * 10_000
    evidence.relations[0].line_style = None

    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        evidence,
        evidence,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    warning = next(item for item in result.warnings if "unsupported line color" in item)
    assert "\n" not in warning
    assert len(warning) < 256


def test_link_style_is_skipped_when_preceding_edge_order_is_not_fully_mappable():
    result = recover_flowchart_styles(
        'flowchart LR\n    A["A"] --> B["B"] --> C["C"]\n    API --> DB\n',
        scene(),
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert "linkStyle" not in result.code
    assert any("edge ordering" in warning for warning in result.warnings)


def test_pipeline_applies_style_recovery_before_the_hard_render_gate(fake_runtime):
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=scene(),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "nodes": [{"id": "API", "label": "API"}, {"id": "DB", "label": "DB"}],
                    "edges": [{"source": "API", "target": "DB"}],
                },
            )
        ],
    )
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "style API fill:#ffeeaa" in result.selected.mermaid_code
    assert fake_runtime.calls == [result.selected.mermaid_code]
    assert result.selected.repair_history[0].operation == "recover_style"
    assert result.selected.repair_history[0].accepted


def test_bold_style_is_constant_and_attributed_across_vector_to_typed_ids():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="vector-node-001",
                role="unknown",
                text="API",
                bbox=(0, 0, 10, 10),
                font_weight="bold",
                evidence_ids=["vector-text-001"],
            )
        ]
    )
    generated = DiagramSceneIR(
        elements=[SceneElement(id="API", role="service", text="API", bbox=(0, 0, 0, 0))]
    )

    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n',
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_evidence_ids={"vector-text-001"},
        known_bold_evidence=_bold_evidence_registry(),
    )

    assert "style API font-weight:bold" in result.code
    assert result.attributions[0].source_element_id == "vector-node-001"
    assert result.attributions[0].emitted_element_id == "API"
    assert result.attributions[0].match_method == "unique_label"
    assert result.attributions[0].evidence_ids == ("vector-text-001",)


def test_bold_style_is_omitted_for_ambiguous_or_unavailable_candidate_mapping():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="vector-node-001",
                role="unknown",
                text="API",
                bbox=(0, 0, 10, 10),
                font_weight="bold",
                evidence_ids=["vector-text-001"],
            )
        ]
    )
    ambiguous = DiagramSceneIR(
        elements=[
            SceneElement(id="API1", role="node", text="API", bbox=(0, 0, 0, 0)),
            SceneElement(id="API2", role="node", text="API", bbox=(0, 0, 0, 0)),
        ]
    )
    code = 'flowchart LR\n    API1["API"]\n    API2["API"]\n'

    result = recover_flowchart_styles(
        code,
        source,
        ambiguous,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_evidence_ids={"vector-text-001"},
        known_bold_evidence=_bold_evidence_registry(),
    )
    unavailable = recover_flowchart_styles(
        code,
        source,
        None,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_evidence_ids={"vector-text-001"},
        known_bold_evidence=_bold_evidence_registry(),
    )

    assert "font-weight" not in result.code
    assert any("ambiguous by label" in warning for warning in result.warnings)
    assert unavailable.code == code
    assert "candidate Scene is unavailable" in unavailable.warnings[0]


def test_style_mapping_rejects_exact_id_with_inconsistent_content():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="API",
                role="node",
                text="User",
                bbox=(0, 0, 10, 10),
                fill_color="red",
            )
        ]
    )
    generated = DiagramSceneIR(
        elements=[
            SceneElement(id="API", role="node", text="Database", bbox=(0, 0, 0, 0))
        ]
    )

    result = recover_flowchart_styles(
        'flowchart LR\n    API["Database"]\n',
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert "style API" not in result.code
    assert any("content mismatch" in warning for warning in result.warnings)


def test_style_label_mapping_preserves_semantic_punctuation():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="source",
                role="node",
                text="A+B",
                bbox=(0, 0, 10, 10),
                fill_color="red",
                evidence_ids=["vector-text-001"],
            )
        ]
    )
    generated = DiagramSceneIR(
        elements=[SceneElement(id="dst", role="node", text="A-B", bbox=(0, 0, 0, 0))]
    )

    result = recover_flowchart_styles(
        'flowchart LR\n    dst["A-B"]\n',
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_evidence_ids={"vector-text-001"},
    )

    assert "style dst" not in result.code
    assert any("mapping was unavailable" in warning for warning in result.warnings)


def test_edge_style_rejects_ambiguous_normalized_endpoint_ids():
    source = DiagramSceneIR(
        elements=[
            SceneElement(id="A-B", role="node", text="first", bbox=(0, 0, 10, 10)),
            SceneElement(id="A B", role="node", text="second", bbox=(0, 20, 10, 30)),
            SceneElement(id="X", role="node", text="target", bbox=(20, 0, 30, 10)),
        ],
        relations=[
            SceneRelation(
                id="E",
                source_id="A B",
                target_id="X",
                relation_type="flow",
                line_color="red",
            )
        ],
    )
    code = (
        'flowchart LR\n    A_B["first"]\n    A_B_2["second"]\n'
        '    X["target"]\n    A_B --> X\n    A_B_2 --> X\n'
    )

    result = recover_flowchart_styles(
        code,
        source,
        source,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert "linkStyle" not in result.code
    assert any("ambiguous normalized endpoint" in warning for warning in result.warnings)


def test_bold_style_requires_registered_vector_evidence_even_for_an_exact_id():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id="API",
                role="service",
                text="API",
                bbox=(0, 0, 10, 10),
                font_weight="bold",
            )
        ]
    )

    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n',
        source,
        source,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert "font-weight" not in result.code
    assert any("without registered vector evidence" in item for item in result.warnings)


def test_pipeline_rejects_self_declared_vector_bold_from_fixture_engine(fake_runtime):
    observation = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=DiagramSceneIR(
            elements=[
                SceneElement(
                    id="vector-node-001",
                    role="unknown",
                    text="API",
                    bbox=(0, 0, 10, 10),
                    font_weight="bold",
                    evidence_ids=["vector-text-001"],
                )
            ],
            diagram_type_candidates=["flowchart"],
        ),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "nodes": [
                        {
                            "id": "API",
                            "label": "API",
                            "evidence_ids": ["vector-text-001"],
                        }
                    ],
                    "edges": [],
                },
            )
        ],
        evidence=[
            VisualEvidence(
                id="vector-text-001",
                kind="vector_text",
                text="API",
                bbox=(0, 0, 10, 10),
                font_weight="bold",
            )
        ],
    )
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [JsonFixtureEngine(observation)],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "font-weight" not in result.selected.mermaid_code


def test_pipeline_maps_trusted_vector_bold_to_semantic_typed_node(fake_runtime):
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [_bold_vector_engine(), JsonFixtureEngine(_bold_semantic_observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "style API font-weight:bold" in result.selected.mermaid_code
    attribution = result.selected.repair_history[0].details["attributions"][0]
    assert attribution["source_element_id"] == "vector-node-001"
    assert attribution["emitted_element_id"] == "API"
    assert attribution["match_method"] == "evidence_overlap"


def test_pipeline_rejects_bold_evidence_id_collision(fake_runtime):
    spoof = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["unknown"], scores=[1.0]),
        evidence=[
            VisualEvidence(
                id="vector-text-001",
                kind="vector_text",
                text="API",
                bbox=(2, 2, 18, 10),
                font_weight="bold",
            )
        ],
    )
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [
            JsonFixtureEngine(spoof),
            _bold_vector_engine(),
            JsonFixtureEngine(_bold_semantic_observation()),
        ],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "font-weight" not in result.selected.mermaid_code


def test_pipeline_rejects_bold_when_typed_label_disagrees_with_vector_span(fake_runtime):
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [_bold_vector_engine(), JsonFixtureEngine(_bold_semantic_observation("Admin"))],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "font-weight" not in result.selected.mermaid_code


@pytest.mark.integration
def test_recovered_styles_parse_and_render_in_pinned_mermaid():
    styled_scene = scene()
    styled_scene.elements[0].font_weight = "bold"
    styled_scene.elements[0].evidence_ids = ["vector-text-001"]
    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        styled_scene,
        styled_scene,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_evidence_ids={"vector-text-001"},
        known_bold_evidence=_bold_evidence_registry(),
    )
    runtime = NodeMermaidRuntime()
    try:
        outcome = CandidateValidator(runtime, SecurityProfile.STYLE_ONLY).validate(result.code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid
    assert outcome.runtime.render_valid, (outcome.runtime.error, outcome.warnings)
    assert "font-weight:bold" in result.code
