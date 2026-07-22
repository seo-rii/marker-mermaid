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
    SceneGroup,
    SceneRelation,
    TypedIRCandidate,
    VisualEvidence,
)
from marker_mermaid.pipeline import ReconstructionPipeline
from marker_mermaid.security import MermaidSecurityScanner
from marker_mermaid.style_recovery import TrustedEdgeStyleEvidence, recover_flowchart_styles
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
                evidence_ids=["vector-shape-001"],
            ),
            SceneElement(
                id="DB",
                role="database",
                text="DB",
                bbox=(20, 0, 30, 10),
                fill_color="blue",
                border_style="dashed",
                evidence_ids=["vector-shape-002"],
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
                evidence_ids=["vector-line-001"],
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
            primitives=(VectorPrimitive(kind="rectangle", bbox=(0, 0, 20, 15), closed=True),),
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


def _group_vector_engine() -> VectorPrimitiveEngine:
    return VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(
            canvas_size=size,
            primitives=(
                VectorPrimitive(
                    kind="rectangle",
                    bbox=(0, 0, 60, 30),
                    fill_color="#eef4ff",
                    stroke_color="#225588",
                    line_style="thick",
                    closed=True,
                ),
                VectorPrimitive(kind="rectangle", bbox=(5, 5, 20, 20), closed=True),
                VectorPrimitive(kind="rectangle", bbox=(40, 5, 55, 20), closed=True),
            ),
        )
    )


def _styled_vector_engine() -> VectorPrimitiveEngine:
    return VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(
            canvas_size=size,
            texts=(
                VectorText("API", (1, 1, 9, 5)),
                VectorText("DB", (21, 1, 29, 5)),
            ),
            primitives=(
                VectorPrimitive(
                    kind="rectangle",
                    bbox=(0, 0, 10, 10),
                    fill_color="#ffeeaa",
                    stroke_color="#112233",
                    line_style="thick",
                    closed=True,
                ),
                VectorPrimitive(
                    kind="rectangle",
                    bbox=(20, 0, 30, 10),
                    fill_color="blue",
                    line_style="dashed",
                    closed=True,
                ),
                VectorPrimitive(
                    kind="line",
                    bbox=(10, 5, 20, 5),
                    points=((10, 5), (20, 5)),
                    stroke_color="#445566",
                    line_style="thick",
                    arrow_at_end=True,
                ),
            ),
        )
    )


def _edge_only_style_vector_engine() -> VectorPrimitiveEngine:
    return VectorPrimitiveEngine(
        extractor=lambda _source, size: VectorObservation(
            canvas_size=size,
            texts=(
                VectorText("API", (1, 1, 9, 5)),
                VectorText("DB", (21, 1, 29, 5)),
            ),
            primitives=(
                VectorPrimitive(kind="rectangle", bbox=(0, 0, 10, 10), closed=True),
                VectorPrimitive(kind="rectangle", bbox=(20, 0, 30, 10), closed=True),
                VectorPrimitive(
                    kind="line",
                    bbox=(10, 5, 20, 5),
                    points=((10, 5), (20, 5)),
                    stroke_color="#445566",
                    line_style="thick",
                    arrow_at_end=True,
                ),
            ),
        )
    )


def _styled_semantic_observation() -> EngineObservation:
    semantic_scene = scene()
    semantic_scene.canvas_size = (40, 20)
    for element in semantic_scene.elements:
        element.fill_color = None
        element.border_color = None
        element.border_style = None
        element.evidence_ids = []
    for relation in semantic_scene.relations:
        relation.line_color = None
        relation.line_style = None
        relation.evidence_ids = []
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=semantic_scene,
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


def _group_semantic_observation() -> EngineObservation:
    return EngineObservation(
        prediction=DiagramTypePrediction(candidates=["flowchart"], scores=[0.9]),
        scene_ir=_group_scenes(),
        typed_candidates=[
            TypedIRCandidate(
                diagram_type="flowchart",
                ir={
                    "nodes": [{"id": "A", "label": "API"}, {"id": "B", "label": "DB"}],
                    "edges": [],
                    "groups": [
                        {
                            "id": "backend",
                            "label": "Backend",
                            "member_ids": ["A", "B"],
                        }
                    ],
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


def _node_style_registry(source: DiagramSceneIR | None = None) -> dict[str, SceneElement]:
    styled = source or scene()
    result: dict[str, SceneElement] = {}
    for element in styled.elements:
        for evidence_id in element.evidence_ids:
            if evidence_id.startswith("vector-shape-"):
                result[evidence_id] = element.model_copy(deep=True)
    return result


def _edge_style_registry(
    source: DiagramSceneIR | None = None,
) -> dict[str, TrustedEdgeStyleEvidence]:
    styled = source or scene()
    elements = {element.id: element for element in styled.elements}
    result: dict[str, TrustedEdgeStyleEvidence] = {}
    for relation in styled.relations:
        if relation.source_id not in elements or relation.target_id not in elements:
            continue
        for evidence_id in relation.evidence_ids:
            if evidence_id.startswith("vector-line-"):
                result[evidence_id] = TrustedEdgeStyleEvidence(
                    relation=relation.model_copy(deep=True),
                    source_bbox=elements[relation.source_id].bbox,
                    target_bbox=elements[relation.target_id].bbox,
                )
    return result


def _group_scenes():
    elements = [
        SceneElement(id="A", role="node", text="API", bbox=(5, 5, 20, 20)),
        SceneElement(id="B", role="node", text="DB", bbox=(40, 5, 55, 20)),
    ]
    group = SceneGroup(
        id="backend",
        role="subgraph",
        label="Backend",
        bbox=(0, 0, 60, 30),
        member_ids=["A", "B"],
    )
    return DiagramSceneIR(elements=elements, groups=[group], coordinate_space="pixels")


def _trusted_group_style_registry():
    return {
        "vector-shape-001": SceneElement(
            id="vector-node-001",
            role="unknown",
            bbox=(0, 0, 60, 30),
            fill_color="#eef4ff",
            border_color="#225588",
            border_style="thick",
            evidence_ids=["vector-shape-001"],
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
        known_node_style_evidence=_node_style_registry(),
        known_edge_style_evidence=_edge_style_registry(),
    )

    assert result.applied_element_ids == ("API", "DB")
    assert result.applied_link_indexes == (0,)
    assert "style API fill:#ffeeaa,stroke:#112233,stroke-width:3px" in result.code
    assert "style DB fill:blue,stroke-dasharray:5 5" in result.code
    assert "linkStyle 0 stroke:#445566,stroke-width:3px" in result.code
    assert MermaidSecurityScanner(SecurityProfile.STYLE_ONLY).scan(result.code).safe


def test_group_style_requires_exact_members_and_trusted_vector_bbox():
    source = _group_scenes()
    generated = _group_scenes()
    result = recover_flowchart_styles(
        'flowchart LR\n    subgraph backend["Backend"]\n'
        '        A["API"]\n        B["DB"]\n    end\n',
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=_trusted_group_style_registry(),
    )

    assert result.applied_group_ids == ("backend",)
    assert "style backend fill:#eef4ff,stroke:#225588,stroke-width:3px" in result.code
    assert result.group_attributions[0].evidence_ids == ("vector-shape-001",)
    assert result.group_attributions[0].match_method == "exact_members_and_vector_bbox"
    assert MermaidSecurityScanner(SecurityProfile.STYLE_ONLY).scan(result.code).safe


def test_group_style_refuses_untrusted_ambiguous_or_extra_member_geometry():
    source = _group_scenes()
    generated = _group_scenes()
    code = (
        'flowchart LR\n    subgraph backend["Backend"]\n'
        '        A["API"]\n        B["DB"]\n    end\n'
    )

    untrusted = recover_flowchart_styles(
        code,
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )
    assert not untrusted.changed

    ambiguous_registry = _trusted_group_style_registry()
    ambiguous_registry["vector-shape-002"] = next(iter(ambiguous_registry.values())).model_copy(
        deep=True
    )
    ambiguous_registry["vector-shape-002"].evidence_ids = ["vector-shape-002"]
    ambiguous = recover_flowchart_styles(
        code,
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=ambiguous_registry,
    )
    assert not ambiguous.changed

    source.elements.append(
        SceneElement(id="outside", role="node", text="Other", bbox=(25, 10, 30, 15))
    )
    extra = recover_flowchart_styles(
        code,
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=_trusted_group_style_registry(),
    )
    assert not extra.changed


def test_group_style_refuses_duplicate_source_membership_or_subgraph_declaration():
    source = _group_scenes()
    generated = _group_scenes()
    source.groups.append(
        SceneGroup(
            id="duplicate",
            role="subgraph",
            label="Duplicate",
            bbox=(0, 0, 60, 30),
            member_ids=["A", "B"],
        )
    )
    code = (
        'flowchart LR\n    subgraph backend["Backend"]\n'
        '        A["API"]\n        B["DB"]\n    end\n'
    )
    duplicate_source = recover_flowchart_styles(
        code,
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=_trusted_group_style_registry(),
    )
    assert not duplicate_source.changed

    duplicate_declaration = recover_flowchart_styles(
        code + '    subgraph backend["Again"]\n    end\n',
        _group_scenes(),
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=_trusted_group_style_registry(),
    )
    assert not duplicate_declaration.changed

    node_collision = recover_flowchart_styles(
        code.replace("flowchart LR\n", 'flowchart LR\n    backend["Node"]\n'),
        _group_scenes(),
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=_trusted_group_style_registry(),
    )
    assert not node_collision.changed


def test_group_style_refuses_normalized_member_id_collision():
    source = DiagramSceneIR(
        elements=[
            SceneElement(id="A-B", role="node", text="First", bbox=(5, 5, 15, 15)),
            SceneElement(id="A_B", role="node", text="Second", bbox=(20, 5, 30, 15)),
            SceneElement(id="C", role="node", text="Third", bbox=(35, 5, 45, 15)),
        ],
        groups=[
            SceneGroup(
                id="backend",
                role="subgraph",
                label="Backend",
                bbox=(0, 0, 50, 20),
                member_ids=["A_B", "C"],
            )
        ],
        coordinate_space="pixels",
    )
    generated = DiagramSceneIR(
        elements=[
            SceneElement(id="A_B", role="node", text="Unknown 1", bbox=(0, 0, 0, 0)),
            SceneElement(id="A_B_2", role="node", text="Unknown 2", bbox=(0, 0, 0, 0)),
            SceneElement(id="C", role="node", text="Third", bbox=(0, 0, 0, 0)),
        ],
        groups=[
            SceneGroup(
                id="backend",
                role="subgraph",
                label="Backend",
                bbox=(0, 0, 0, 0),
                member_ids=["A_B", "C"],
            )
        ],
    )
    registry = {
        "vector-container": SceneElement(
            id="vector-container",
            role="unknown",
            bbox=(0, 0, 50, 20),
            fill_color="blue",
            evidence_ids=["vector-container"],
        )
    }

    result = recover_flowchart_styles(
        'flowchart LR\n    subgraph backend["Backend"]\n'
        '        A_B["Unknown 1"]\n        C["Third"]\n    end\n',
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=registry,
    )

    assert not result.changed
    assert any("ambiguous normalized" in warning for warning in result.warnings)


def test_group_style_refuses_member_contour_as_single_member_container():
    member = SceneElement(
        id="A",
        role="node",
        text="API",
        bbox=(0, 0, 20, 20),
        evidence_ids=["member-contour"],
    )
    source = DiagramSceneIR(
        elements=[member],
        groups=[
            SceneGroup(
                id="backend",
                role="subgraph",
                label="Backend",
                bbox=(0, 0, 20, 20),
                member_ids=["A"],
            )
        ],
        coordinate_space="pixels",
    )
    registry = {
        "member-contour": member.model_copy(
            update={"fill_color": "red", "border_color": "blue"}, deep=True
        )
    }

    result = recover_flowchart_styles(
        'flowchart LR\n    subgraph backend["Backend"]\n        A["API"]\n    end\n',
        source,
        source,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=registry,
    )

    assert not result.changed


def test_group_style_work_budget_accounts_for_member_comparisons(monkeypatch):
    elements = [
        SceneElement(
            id=f"N{index}",
            role="node",
            text=str(index),
            bbox=(float(index), 0, float(index + 1), 1),
        )
        for index in range(1_500)
    ]
    source = DiagramSceneIR(
        elements=elements,
        groups=[
            SceneGroup(
                id="large",
                role="subgraph",
                label="Large",
                bbox=(0, 0, 1_500, 1),
                member_ids=[element.id for element in elements],
            )
        ],
        coordinate_space="pixels",
    )
    registry = {
        "vector-container": SceneElement(
            id="vector-container",
            role="unknown",
            bbox=(0, 0, 1_500, 1),
            fill_color="blue",
            evidence_ids=["vector-container"],
        )
    }

    real_sorted = sorted

    def reject_large_sort(iterable, *args, **kwargs):
        values = list(iterable)
        if len(values) > 100:
            raise AssertionError("group membership was sorted after the work budget failed")
        return real_sorted(values, *args, **kwargs)

    with monkeypatch.context() as patch_context:
        patch_context.setattr("builtins.sorted", reject_large_sort)
        result = recover_flowchart_styles(
            'flowchart LR\n    subgraph large["Large"]\n    end\n',
            source,
            source,
            compatibility_profile=CompatibilityProfile.STYLE_RICH,
            security_profile=SecurityProfile.STYLE_ONLY,
            known_group_style_evidence=registry,
        )

    assert not result.changed
    assert any("work budget" in warning for warning in result.warnings)


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
        known_node_style_evidence=_node_style_registry(evidence),
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
        known_edge_style_evidence=_edge_style_registry(evidence),
    )

    assert "linkStyle 0 stroke:#445566" in result.code
    assert result.applied_link_indexes == (0,)


def test_node_and_edge_styles_reject_wrong_bbox_or_reused_line_evidence():
    evidence = scene()
    wrong_node_registry = _node_style_registry(evidence)
    wrong_node_registry["vector-shape-001"].bbox = (100, 100, 110, 110)
    duplicate = evidence.relations[0].model_copy(deep=True)
    duplicate.id = "E2"
    evidence.relations.append(duplicate)

    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n    API --> DB\n',
        evidence,
        evidence,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_node_style_evidence=wrong_node_registry,
        known_edge_style_evidence=_edge_style_registry(evidence),
    )

    assert "style API" not in result.code
    assert "linkStyle" not in result.code
    assert any("registered vector contour" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    ("source_arrows", "generated_arrows", "edge_line", "expected"),
    [
        ((False, False), (False, False), "API --- DB", True),
        ((False, False), (False, False), "API --> DB", False),
        ((True, True), (True, True), "API <--> DB", True),
        ((False, True), (False, True), "DB --> API", False),
        ((False, True), (False, False), "API --> DB", False),
    ],
)
def test_edge_style_requires_source_generated_and_code_arrow_agreement(
    source_arrows, generated_arrows, edge_line, expected
):
    source = scene()
    source.relations[0].arrow_at_start, source.relations[0].arrow_at_end = source_arrows
    generated = source.model_copy(deep=True)
    (
        generated.relations[0].arrow_at_start,
        generated.relations[0].arrow_at_end,
    ) = generated_arrows

    result = recover_flowchart_styles(
        f'flowchart LR\n    API["API"]\n    DB[("DB")]\n    {edge_line}\n',
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_edge_style_evidence=_edge_style_registry(source),
    )

    assert ("linkStyle 0" in result.code) is expected


def test_many_unique_edge_styles_use_deterministic_pair_index():
    elements = [
        SceneElement(id=f"N{index}", role="node", bbox=(index * 2, 0, index * 2 + 1, 1))
        for index in range(251)
    ]
    relations = [
        SceneRelation(
            id=f"E{index}",
            source_id=f"N{index}",
            target_id=f"N{index + 1}",
            relation_type="connector",
            line_color="blue",
            evidence_ids=[f"vector-line-{index}"],
        )
        for index in range(250)
    ]
    source = DiagramSceneIR(elements=elements, relations=relations)
    registry = {
        f"vector-line-{index}": TrustedEdgeStyleEvidence(
            relation=relation.model_copy(deep=True),
            source_bbox=elements[index].bbox,
            target_bbox=elements[index + 1].bbox,
        )
        for index, relation in enumerate(relations)
    }
    declarations = "\n".join(f'    N{index}["N{index}"]' for index in range(251))
    edges = "\n".join(f"    N{index} --> N{index + 1}" for index in range(250))

    result = recover_flowchart_styles(
        f"flowchart LR\n{declarations}\n{edges}\n",
        source,
        source,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_edge_style_evidence=registry,
    )

    assert result.applied_link_indexes == tuple(range(250))
    assert result.code.count("linkStyle ") == 250


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
        known_edge_style_evidence=_edge_style_registry(evidence),
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
        known_edge_style_evidence=_edge_style_registry(evidence),
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
        known_edge_style_evidence=_edge_style_registry(),
    )

    assert "linkStyle" not in result.code
    assert any("edge ordering" in warning for warning in result.warnings)


@pytest.mark.parametrize("unmapped_edge", ["A --o B", "A --x B", "A -.- B", "A === B", "A ~~~ B"])
def test_link_style_is_skipped_when_preceded_by_any_unmapped_mermaid_edge(
    unmapped_edge: str,
) -> None:
    result = recover_flowchart_styles(
        f'flowchart LR\n    {unmapped_edge}\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        scene(),
        scene(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_edge_style_evidence=_edge_style_registry(),
    )

    assert "linkStyle" not in result.code
    assert any("edge ordering" in warning for warning in result.warnings)


def test_pipeline_rejects_self_declared_node_and_edge_styles(fake_runtime):
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
    assert "style API" not in result.selected.mermaid_code
    assert "linkStyle" not in result.selected.mermaid_code
    assert fake_runtime.calls == [result.selected.mermaid_code]
    assert all(item.operation != "recover_style" for item in result.selected.repair_history)


def test_pipeline_maps_trusted_vector_node_and_edge_styles_to_typed_ids(fake_runtime):
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [_styled_vector_engine(), JsonFixtureEngine(_styled_semantic_observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "style API fill:#ffeeaa,stroke:#112233,stroke-width:3px" in (
        result.selected.mermaid_code
    )
    assert "style DB fill:blue,stroke-dasharray:5 5" in result.selected.mermaid_code
    assert "linkStyle 0 stroke:#445566,stroke-width:3px" in result.selected.mermaid_code
    history = next(
        item for item in result.selected.repair_history if item.operation == "recover_style"
    )
    assert {item["evidence_ids"][0] for item in history.details["attributions"]} == {
        "vector-shape-001",
        "vector-shape-002",
    }
    assert history.details["edge_attributions"] == [
        {
            "source_relation_id": "vector-relation-001",
            "link_index": 0,
            "evidence_ids": ["vector-line-001"],
            "match_method": "vector_evidence_and_endpoint_bbox",
        }
    ]


def test_pipeline_maps_vector_edge_style_through_trusted_normal_text_labels(fake_runtime):
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [_edge_only_style_vector_engine(), JsonFixtureEngine(_styled_semantic_observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "style API" not in result.selected.mermaid_code
    assert "style DB" not in result.selected.mermaid_code
    assert "linkStyle 0 stroke:#445566,stroke-width:3px" in result.selected.mermaid_code


def test_pipeline_revokes_node_and_edge_style_trust_on_evidence_collision(fake_runtime):
    spoof = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["unknown"], scores=[1.0]),
        evidence=[
            VisualEvidence(
                id="vector-shape-001",
                kind="contour",
                bbox=(0, 0, 10, 10),
                source_block_ids=["source"],
            ),
            VisualEvidence(
                id="vector-line-001",
                kind="line_segment",
                bbox=(10, 5, 20, 5),
                source_block_ids=["source"],
            ),
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
            _styled_vector_engine(),
            JsonFixtureEngine(_styled_semantic_observation()),
        ],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (40, 20), "white"))

    assert result.selected is not None
    assert "style API" not in result.selected.mermaid_code
    assert "style DB fill:blue" in result.selected.mermaid_code
    assert "linkStyle" not in result.selected.mermaid_code


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
        elements=[SceneElement(id="API", role="node", text="Database", bbox=(0, 0, 0, 0))]
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


def test_shared_trusted_evidence_bucket_is_fail_closed_without_candidate_expansion():
    source = DiagramSceneIR(
        elements=[
            SceneElement(
                id=f"S{index}",
                role="node",
                text=f"Node {index}",
                bbox=(index, 0, index + 1, 1),
                evidence_ids=["shared-contour"],
            )
            for index in range(500)
        ]
    )
    generated = DiagramSceneIR(
        elements=[
            SceneElement(
                id=f"G{index}",
                role="node",
                text=f"Node {index}",
                bbox=(0, 0, 0, 0),
                evidence_ids=["shared-contour"],
            )
            for index in range(500)
        ]
    )
    registry = {
        "shared-contour": SceneElement(
            id="vector-node",
            role="unknown",
            bbox=(0, 0, 1, 1),
            fill_color="red",
            evidence_ids=["shared-contour"],
        )
    }

    result = recover_flowchart_styles(
        'flowchart LR\n    G0["Node 0"]\n',
        source,
        generated,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_node_style_evidence=registry,
    )

    assert not result.changed
    assert any("ambiguous by evidence" in warning for warning in result.warnings)


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
                evidence_ids=["vector-line-ambiguous"],
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
        known_edge_style_evidence={
            "vector-line-ambiguous": TrustedEdgeStyleEvidence(
                relation=source.relations[0].model_copy(deep=True),
                source_bbox=(0, 20, 10, 30),
                target_bbox=(20, 0, 30, 10),
            )
        },
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


def test_pipeline_maps_trusted_vector_container_style_to_typed_group(fake_runtime):
    config = MermaidConfig(
        candidate_count=1,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
    )

    result = ReconstructionPipeline(
        config,
        [_group_vector_engine(), JsonFixtureEngine(_group_semantic_observation())],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (60, 30), "white"))

    assert result.selected is not None
    assert (
        "style backend fill:#eef4ff,stroke:#225588,stroke-width:3px" in result.selected.mermaid_code
    )
    details = result.selected.repair_history[0].details
    assert details["group_ids"] == ["backend"]
    assert details["group_attributions"] == [
        {
            "source_group_id": "backend",
            "emitted_group_id": "backend",
            "evidence_ids": ["vector-shape-001"],
            "match_method": "exact_members_and_vector_bbox",
        }
    ]


def test_pipeline_revokes_group_style_trust_on_evidence_id_collision(fake_runtime):
    spoof = EngineObservation(
        prediction=DiagramTypePrediction(candidates=["unknown"], scores=[1.0]),
        evidence=[
            VisualEvidence(
                id="vector-shape-001",
                kind="contour",
                bbox=(0, 0, 60, 30),
                source_block_ids=["source"],
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
            _group_vector_engine(),
            JsonFixtureEngine(_group_semantic_observation()),
        ],
        CandidateValidator(fake_runtime, config.security_profile),
    ).reconstruct("source", "source.png", Image.new("RGB", (60, 30), "white"))

    assert result.selected is not None
    assert "style backend" not in result.selected.mermaid_code


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
    styled_scene.elements[0].evidence_ids = ["vector-shape-001", "vector-text-001"]
    result = recover_flowchart_styles(
        'flowchart LR\n    API["API"]\n    DB[("DB")]\n    API --> DB\n',
        styled_scene,
        styled_scene,
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_evidence_ids={"vector-text-001"},
        known_bold_evidence=_bold_evidence_registry(),
        known_node_style_evidence=_node_style_registry(styled_scene),
        known_edge_style_evidence=_edge_style_registry(styled_scene),
    )
    runtime = NodeMermaidRuntime()
    try:
        outcome = CandidateValidator(runtime, SecurityProfile.STYLE_ONLY).validate(result.code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid
    assert outcome.runtime.render_valid, (outcome.runtime.error, outcome.warnings)
    assert "font-weight:bold" in result.code


@pytest.mark.integration
def test_recovered_group_style_parses_and_renders_in_pinned_mermaid():
    result = recover_flowchart_styles(
        'flowchart LR\n    subgraph backend["Backend"]\n'
        '        A["API"]\n        B["DB"]\n    end\n',
        _group_scenes(),
        _group_scenes(),
        compatibility_profile=CompatibilityProfile.STYLE_RICH,
        security_profile=SecurityProfile.STYLE_ONLY,
        known_group_style_evidence=_trusted_group_style_registry(),
    )
    runtime = NodeMermaidRuntime()
    try:
        outcome = CandidateValidator(runtime, SecurityProfile.STYLE_ONLY).validate(result.code, 20)
    finally:
        runtime.close()

    assert outcome.runtime.syntax_valid
    assert outcome.runtime.render_valid, (outcome.runtime.error, outcome.warnings)
    assert "style backend fill:#eef4ff" in result.code
