from __future__ import annotations

import pytest
from PIL import Image

from marker_mermaid.config import CompatibilityProfile, MermaidConfig, SecurityProfile
from marker_mermaid.engines import JsonFixtureEngine
from marker_mermaid.models import (
    DiagramSceneIR,
    DiagramTypePrediction,
    EngineObservation,
    SceneElement,
    SceneRelation,
    TypedIRCandidate,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.style_recovery import recover_flowchart_styles
from marker_mermaid.validation import CandidateValidator, NodeMermaidRuntime


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
                line_style="thick",
            )
        ],
        reading_direction="LR",
        diagram_type_candidates=["flowchart"],
    )


def test_style_only_profile_emits_allowlisted_node_and_link_styles():
    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert result.applied_element_ids == ("API", "DB")
    assert result.applied_link_indexes == (0,)
    assert "style API fill:#ffeeaa,stroke:#112233,stroke-width:3px" in result.code
    assert "style DB fill:blue,stroke-dasharray:5 5" in result.code
    assert "linkStyle 0 stroke-width:3px" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STYLE_ONLY).scan(result.code).safe


def test_strict_and_portable_basic_keep_style_only_in_scene_ir():
    code = 'flowchart LR\n    API["API"]\n'
    strict = recover_flowchart_styles(
        code,
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STRICT,
    )
    portable = recover_flowchart_styles(
        code,
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
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    assert not result.changed
    assert "not flowchart" in result.warnings[0]


def test_link_style_is_skipped_when_preceding_edge_order_is_not_fully_mappable():
    result = recover_flowchart_styles(
        'flowchart LR\n    A["A"] --> B["B"] --> C["C"]\n    API --> DB\n',
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


@pytest.mark.integration
def test_recovered_styles_parse_and_render_in_pinned_mermaid():
    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )
    runtime = NodeMermaidRuntime()
    try:
        outcome = CandidateValidator(runtime, SecurityProfile.STYLE_ONLY).validate(result.code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid
    assert outcome.runtime.render_valid, (outcome.runtime.error, outcome.warnings)
